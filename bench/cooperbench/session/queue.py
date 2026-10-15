"""The work queue of a session-driven run: one line per (pair, arm) item.

A run that uses coordbench's session driver is driven by an orchestrating
Claude Code session, which spawns one subagent per bench agent, so the
run cannot be one process: prepare runs, the session spawns, finish runs.
Several lanes (workflows of the session) take items from one queue, so
the work balances itself and an item can be added while the run is on.

    python3 queue.py add QUEUE --tag TAG --arms solo,coop,sdlc [--pairs @pairs.txt|flash]
        [--timeout-min 45] [--model claude-sonnet-5-5] [--skip REPO/TASK ...]
    python3 queue.py next QUEUE --worker NAME      pop the first pending item (JSON on stdout)
    python3 queue.py set QUEUE ITEM_ID KEY=VALUE ...
    python3 queue.py show QUEUE [--counts]
    python3 queue.py requeue QUEUE --status failed  put failed items back as pending

The file is JSON lines, rewritten whole under an fcntl lock, so two lanes
never pop the same item. Items are ordered task by task, then pair by
pair, then arm by arm, so every arm advances together and a run cut
short still holds the same pairs under every arm, which the paired tests
need.
"""

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

ARMS_DEFAULT = ("solo", "coop", "sdlc", "coop-git", "team")


def locked(path):
    """A context manager holding an exclusive lock on <path>.lock."""
    class Lock:
        def __enter__(self):
            self.handle = open(f"{path}.lock", "w")
            fcntl.flock(self.handle, fcntl.LOCK_EX)
            return self

        def __exit__(self, *_):
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
    return Lock()


def read_items(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_items(path, items):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as out:
        for item in items:
            out.write(json.dumps(item) + "\n")
    os.replace(tmp, path)


def flash_pairs(dataset_dir, pairs_arg):
    """(repo, task, [a, b]) for every pair the argument names: flash, a
    @file of REPO/TASK lines (every flash pair of each), or entries
    REPO/TASK[/fA_fB] separated by commas."""
    subset = json.load(open(Path(dataset_dir) / "subsets" / "flash.json"))
    by_task = {(t["repo"], int(t["task_id"])): t["pairs"] for t in subset["tasks"]}
    wanted = []
    if pairs_arg == "flash":
        wanted = [(repo, task, None) for repo, task in by_task]
    elif pairs_arg.startswith("@"):
        for line in Path(pairs_arg[1:]).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                repo, task = line.split("/")[:2]
                wanted.append((repo, int(task), None))
    else:
        for entry in pairs_arg.split(","):
            parts = entry.strip().split("/")
            features = None
            if len(parts) == 3:
                features = [int(p) for p in parts[2].replace("f", "").split("_")]
            wanted.append((parts[0], int(parts[1]), features))
    out = []
    for repo, task, features in wanted:
        pairs = by_task[(repo, task)]
        for pair in pairs:
            if features is None or sorted(features) == sorted(pair):
                out.append((repo, task, list(pair)))
    return out


def item_id(tag, arm, repo, task, pair):
    return f"{tag}__{arm}__{repo}__{task}__f{pair[0]}_f{pair[1]}"


def command_add(args):
    arms = args.arms.split(",")
    skip = set(args.skip or [])
    pairs = flash_pairs(args.dataset_dir, args.pairs)
    # The order of the task list is kept as given (the pair list's order),
    # so the small images run first and the large ones last.
    with locked(args.queue):
        items = read_items(args.queue)
        known = {item["id"] for item in items}
        added = 0
        for repo, task, pair in pairs:
            if f"{repo}/{task}" in skip:
                continue
            for arm in arms:
                ident = item_id(args.tag, arm, repo, task, pair)
                if ident in known:
                    continue
                items.append({"id": ident, "tag": args.tag, "arm": arm, "repo": repo, "task": task,
                              "features": f"{pair[0]},{pair[1]}", "run_name": f"{args.tag}-{arm}",
                              "timeout_min": args.timeout_min, "model": args.model, "status": "pending",
                              "added_at": time.time()})
                added += 1
        write_items(args.queue, items)
    print(f"added {added} items; {len(items)} in the queue")


def command_next(args):
    """The worker's own running item that was never prepared first (a
    lane that came back after waiting for an image), else the first
    pending item."""
    with locked(args.queue):
        items = read_items(args.queue)
        for item in items:
            if item["status"] == "running" and item.get("worker") == args.worker and not item.get("prepared_at"):
                print(json.dumps(item))
                return 0
        for item in items:
            if item["status"] == "pending":
                item.update(status="running", worker=args.worker, taken_at=time.time())
                write_items(args.queue, items)
                print(json.dumps(item))
                return 0
    print(json.dumps({"done": True}))
    return 0


def command_set(args):
    with locked(args.queue):
        items = read_items(args.queue)
        for item in items:
            if item["id"] == args.item:
                for pair in args.fields:
                    key, _, value = pair.partition("=")
                    try:
                        item[key] = json.loads(value)
                    except json.JSONDecodeError:
                        item[key] = value
                write_items(args.queue, items)
                print(json.dumps(item))
                return 0
    print(f"no item {args.item}", file=sys.stderr)
    return 1


def command_show(args):
    items = read_items(args.queue)
    if args.counts:
        counts = {}
        for item in items:
            key = (item["arm"], item["status"])
            counts[key] = counts.get(key, 0) + 1
        for (arm, status), n in sorted(counts.items()):
            print(f"{arm:10} {status:10} {n}")
        return 0
    for item in items:
        print(item["status"], item.get("worker", "-"), item["id"], item.get("verdict", ""))
    return 0


def command_requeue(args):
    with locked(args.queue):
        items = read_items(args.queue)
        n = 0
        for item in items:
            if item["status"] == args.status and (not args.ids or item["id"] in args.ids):
                item["status"] = "pending"
                item["attempts"] = item.get("attempts", 0) + 1
                n += 1
        write_items(args.queue, items)
    print(f"requeued {n}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("queue")
    add.add_argument("--tag", required=True)
    add.add_argument("--arms", default=",".join(ARMS_DEFAULT))
    add.add_argument("--pairs", default="flash")
    add.add_argument("--dataset-dir", default=str(Path.home() / "cooperbench" / "dataset"))
    add.add_argument("--timeout-min", type=int, default=45)
    add.add_argument("--model", default="claude-sonnet-5-5")
    add.add_argument("--skip", nargs="*")
    add.set_defaults(func=command_add)
    nxt = sub.add_parser("next")
    nxt.add_argument("queue")
    nxt.add_argument("--worker", required=True)
    nxt.set_defaults(func=command_next)
    setter = sub.add_parser("set")
    setter.add_argument("queue")
    setter.add_argument("item")
    setter.add_argument("fields", nargs="+")
    setter.set_defaults(func=command_set)
    show = sub.add_parser("show")
    show.add_argument("queue")
    show.add_argument("--counts", action="store_true")
    show.set_defaults(func=command_show)
    req = sub.add_parser("requeue")
    req.add_argument("queue")
    req.add_argument("--status", default="failed")
    req.add_argument("--ids", nargs="*")
    req.set_defaults(func=command_requeue)
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
