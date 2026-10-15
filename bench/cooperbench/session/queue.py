"""The work queue of a session-driven run: one line per (pair, arm) item.

A run that uses coordbench's session driver is driven by an orchestrating
Claude Code session, which spawns one subagent per bench agent, so the
run cannot be one process: prepare runs, the session spawns, finish runs.
Several lanes (workflows of the session) take items from one queue, so
the work balances itself and an item can be added while the run is on.

    python3 queue.py add QUEUE --tag TAG --arms solo,coop,sdlc [--pairs @pairs.txt|flash]
        [--sample N --stratify language,conflict,published --allocation equal --seed 0 --published coop/index.json]
        [--timeout-min 45] [--model claude-sonnet-5-5] [--skip REPO/TASK ...]
    python3 queue.py next QUEUE --worker NAME      pop the first pending item (JSON on stdout)
    python3 queue.py set QUEUE ITEM_ID KEY=VALUE ...
    python3 queue.py show QUEUE [--counts]
    python3 queue.py requeue QUEUE --status failed  put failed items back as pending
    python3 queue.py pause QUEUE                   pending items wait (status paused) until resume
    python3 queue.py resume QUEUE [--pairs ...]    paused items are pending again

A run grows by adding again: --sample N takes the first N pairs of the
nested stratified order strata.py fixes by the seed (the same seed and
keys, a larger N), and an item already in the queue is not added twice,
so a run of 10 pairs grown to 20 keeps its 10 and queues 10 more. The
queue keeps the draw's arguments on each item and refuses a draw with
other arguments, which would not nest. requeue clears what a run left
on an item, so it is prepared afresh.

The file is JSON lines, rewritten whole under an fcntl lock, so two lanes
never pop the same item. Items are ordered task by task, then pair by
pair, then arm by arm, so every arm advances together and a run cut
short still holds the same pairs under every arm, which the paired tests
need.
"""

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strata  # noqa: E402 - a sibling script, found by its directory

ARMS_DEFAULT = ("solo", "coop", "sdlc", "coop-git", "team")
# What a prepare or a finish wrote on an item, cleared when the item is
# taken again, so a requeued item is not read as prepared already.
RUN_KEYS = ("worker", "taken_at", "run_dir", "prepared_at", "finishing_at", "finished_at", "verdict",
            "error", "note", "image_waits")


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


def item_id(tag, arm, repo, task, pair):
    return f"{tag}__{arm}__{repo}__{task}__f{pair[0]}_f{pair[1]}"


def sampling_record(args):
    """What the nested order depends on; a later add with a different
    record would draw a different order, so the queue keeps it and
    refuses the difference."""
    digest = None
    if args.published:
        digest = hashlib.sha256(Path(args.published).read_bytes()).hexdigest()[:16]
    return {"stratify": args.stratify, "allocation": args.allocation, "seed": args.seed,
            "pairs": args.pairs, "skip": sorted(args.skip or []), "published_sha256": digest}


def sampled_pairs(args):
    """The first --sample pairs of strata.py's nested order over the pair
    list, the skipped tasks left out before the draw so a skip never
    shortens the sample."""
    dataset = Path(args.dataset_dir)
    skip = set(args.skip or [])
    pairs = [p for p in strata.flash_pairs(dataset, args.pairs) if f"{p[0]}/{p[1]}" not in skip]
    rows = strata.describe(dataset, pairs, strata.published_results(args.published))
    order = strata.nested_order(rows, args.stratify.split(","), args.allocation, args.seed)
    return [(r["repo"], r["task"], [int(f) for f in r["pair"].replace("f", "").split("_")]) for r in order[:args.sample]]


def command_add(args):
    arms = args.arms.split(",")
    skip = set(args.skip or [])
    record = sampling_record(args) if args.sample else None
    pairs = sampled_pairs(args) if args.sample else strata.flash_pairs(Path(args.dataset_dir), args.pairs)
    # The order is the pair list's, or the nested stratified order under
    # --sample, and every arm of a pair sits together in it.
    with locked(args.queue):
        items = read_items(args.queue)
        known = {item["id"] for item in items}
        earlier = [item["sample"] for item in items if item.get("sample") and item["tag"] == args.tag]
        if record and earlier and earlier[0] != record and not args.force:
            sys.exit(f"this queue's sample was drawn with {json.dumps(earlier[0])}; a different draw "
                     f"would not nest. Repeat those arguments, or pass --force to add anyway.")
        added = 0
        for repo, task, pair in pairs:
            if f"{repo}/{task}" in skip:
                continue
            for arm in arms:
                ident = item_id(args.tag, arm, repo, task, pair)
                if ident in known:
                    continue
                item = {"id": ident, "tag": args.tag, "arm": arm, "repo": repo, "task": task,
                        "features": f"{pair[0]},{pair[1]}", "run_name": f"{args.tag}-{arm}",
                        "timeout_min": args.timeout_min, "model": args.model, "status": "pending",
                        "added_at": time.time()}
                if record:
                    item["sample"] = record
                items.append(item)
                added += 1
        write_items(args.queue, items)
    print(f"added {added} items; {len(items)} in the queue")


def command_next(args):
    """The worker's own running item that is not being finished first (a
    lane that came back after waiting for an image, or one that lost the
    plan it was handed and takes the item up again), else the first
    pending item, with what an earlier run left on it cleared."""
    with locked(args.queue):
        items = read_items(args.queue)
        for item in items:
            if item["status"] == "running" and item.get("worker") == args.worker and not item.get("finishing_at"):
                print(json.dumps(item))
                return 0
        for item in items:
            if item["status"] == "pending":
                for key in RUN_KEYS:
                    item.pop(key, None)
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
    try:
        return show_items(args)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


def show_items(args):
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


def command_pause(args):
    with locked(args.queue):
        items = read_items(args.queue)
        changed = 0
        for item in items:
            if item["status"] == "pending":
                item["status"] = "paused"
                changed += 1
        write_items(args.queue, items)
    print(f"paused {changed} items")


def command_resume(args):
    """Paused items are pending again: all of them, or those of the
    pairs named (REPO/TASK or REPO/TASK/fA_fB)."""
    wanted = set(args.pairs.split(",")) if args.pairs else None
    with locked(args.queue):
        items = read_items(args.queue)
        changed = 0
        for item in items:
            names = {f"{item['repo']}/{item['task']}",
                     f"{item['repo']}/{item['task']}/f{item['features'].replace(',', '_f')}"}
            if item["status"] == "paused" and (wanted is None or names & wanted):
                item["status"] = "pending"
                changed += 1
        write_items(args.queue, items)
    print(f"resumed {changed} items")


def command_requeue(args):
    with locked(args.queue):
        items = read_items(args.queue)
        n = 0
        for item in items:
            if item["status"] == args.status and (not args.ids or item["id"] in args.ids):
                for key in RUN_KEYS:
                    item.pop(key, None)
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
    add.add_argument("--sample", type=int, help="take the first N pairs of the nested stratified order")
    add.add_argument("--stratify", default="language,conflict", help="the strata keys, as strata.py --by")
    add.add_argument("--allocation", choices=("equal", "proportional"), default="equal")
    add.add_argument("--seed", type=int, default=0)
    add.add_argument("--published", help="the website's coop/index.json, for the published stratum")
    add.add_argument("--force", action="store_true", help="add a sample drawn with other arguments than the queue's")
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
    pause = sub.add_parser("pause")
    pause.add_argument("queue")
    pause.set_defaults(func=command_pause)
    resume = sub.add_parser("resume")
    resume.add_argument("queue")
    resume.add_argument("--pairs", help="REPO/TASK or REPO/TASK/fA_fB entries, comma separated; all when absent")
    resume.set_defaults(func=command_resume)
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
