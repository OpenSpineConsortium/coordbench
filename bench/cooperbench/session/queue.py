"""The work queue of a session-driven run: one line per (pair, arm) item.

A run that uses coordbench's session driver is driven by an orchestrating
Claude Code session, which spawns one subagent per bench agent, so the
run cannot be one process: prepare runs, the session spawns, finish runs.
Several lanes (workflows of the session) take items from one queue, so
the work balances itself and an item can be added while the run is on.

    python3 queue.py add QUEUE --tag TAG --arms solo,coop,sdlc [--pairs @pairs.txt|flash]
        [--sample N --stratify language,conflict,published --allocation equal --seed 0 --published coop/index.json]
        [--timeout-min 45] [--model claude-sonnet-5-5] [--skip REPO/TASK ...]
        [--order-seed N] [--option KEY=VALUE ...]
    python3 queue.py next QUEUE --worker NAME      pop the first pending item (JSON on stdout)
    python3 queue.py set QUEUE ITEM_ID KEY=VALUE ...
    python3 queue.py show QUEUE [--counts]
    python3 queue.py requeue QUEUE [--status failed | --harness-only] [--ids ...]
                                                   put items back as pending
    python3 queue.py pause QUEUE                   pending items wait (status paused) until resume
    python3 queue.py resume QUEUE [--pairs ...]    paused items are pending again
    python3 queue.py shuffle QUEUE --tag TAG --seed N
                                                   put each pair's arms not yet taken in a seeded order

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

The arms of a pair go in the order --arms gives unless --order-seed, or
shuffle on a queue already filled, puts them in an order drawn for each
pair from the seed and the pair: with one fixed order the same arm would
always meet a lane's first minutes, the load of the arm before it and a
usage window's end, and its times and failures would carry them. The
draw depends on nothing else, so it is the same on every machine and in
every repetition of an add.

requeue --harness-only takes back only the finished items, done or
failed, whose run measured the harness rather than the arm (an agent the
harness lost, a full disk, a transcript not found or cut short, an agent
spawned twice), by the run's audit.json, which lane.py finish writes and
audit.py run by hand writes again, or, for a run stopped before prepare,
by the mark lane.py put on the item; an agent that ran out of time is
the arm's result and stays failed.
"""

import argparse
import fcntl
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strata  # noqa: E402 - a sibling script, found by its directory

ARMS_DEFAULT = ("solo", "coop", "sdlc", "coop-git", "team")
# What a prepare or a finish wrote on an item, cleared when the item is
# taken again, so a requeued item is not read as prepared already.
RUN_KEYS = ("worker", "taken_at", "run_dir", "prepared_at", "finishing_at", "finished_at", "verdict",
            "error", "note", "image_waits", "disk_waits", "harness_error")


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
    return f"{tag}__{arm_label(arm)}__{repo}__{task}__f{pair[0]}_f{pair[1]}"


def arm_label(arm):
    """An arm named by its directory (run.py's --arm takes a path) goes by
    the directory's name in the item's id and run name, so its logs land
    under <tag>-<name> as a built-in arm's do; the item keeps the path,
    which prepare passes to run.py."""
    return Path(arm).name if "/" in arm else arm


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


def pair_key(repo, task, features):
    return f"{repo}/{task}/{features}"


def arm_order(arms, seed, key):
    """The arms in the order the seed draws for this pair, from the arms
    sorted by label, so the draw does not depend on the order they were
    given in: a generator seeded by text, which Python seeds the same way
    on every run."""
    order = sorted(arms, key=arm_label)
    random.Random(f"{seed}:{key}").shuffle(order)
    return order


def command_add(args):
    arms = args.arms.split(",")
    skip = set(args.skip or [])
    options = dict(args.option or [])
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
            features = f"{pair[0]},{pair[1]}"
            order = arms if args.order_seed is None else arm_order(arms, args.order_seed,
                                                                   pair_key(repo, task, features))
            for arm in order:
                ident = item_id(args.tag, arm, repo, task, pair)
                if ident in known:
                    continue
                item = {"id": ident, "tag": args.tag, "arm": arm, "repo": repo, "task": task,
                        "features": features, "run_name": f"{args.tag}-{arm_label(arm)}",
                        "timeout_min": args.timeout_min, "model": args.model, "status": "pending",
                        "added_at": time.time()}
                if record:
                    item["sample"] = record
                if args.order_seed is not None:
                    item["order_seed"] = args.order_seed
                if options:
                    item["options"] = options
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
            key = (arm_label(item["arm"]), item["status"])
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


def command_shuffle(args):
    """Put the arms of each pair of the tag that no lane has taken yet
    (pending or paused) in the order the seed draws for the pair, in the
    places those items hold, so the pairs' order and every other item
    stay where they are."""
    open_states = ("pending", "paused")
    with locked(args.queue):
        items = read_items(args.queue)
        places = {}
        for index, item in enumerate(items):
            if item["tag"] == args.tag and item["status"] in open_states:
                places.setdefault(pair_key(item["repo"], item["task"], item["features"]), []).append(index)
        moved = 0
        for key, indexes in places.items():
            held = {arm_label(items[i]["arm"]): items[i] for i in indexes}
            order = arm_order(held, args.seed, key)
            for index, label in zip(indexes, order):
                if items[index] is not held[label]:
                    moved += 1
                items[index] = dict(held[label], order_seed=args.seed)
        write_items(args.queue, items)
    print(f"ordered {sum(len(i) for i in places.values())} items of {len(places)} pairs; {moved} moved")


def command_requeue(args):
    with locked(args.queue):
        items = read_items(args.queue)
        n = 0
        for item in items:
            if args.harness_only:
                if item["status"] not in FINISHED or not harness_error_of(item):
                    continue
            elif item["status"] != args.status:
                continue
            if not args.ids or item["id"] in args.ids:
                for key in RUN_KEYS:
                    item.pop(key, None)
                item["status"] = "pending"
                item["attempts"] = item.get("attempts", 0) + 1
                n += 1
        write_items(args.queue, items)
    print(f"requeued {n}")


# The statuses of a run that ended: a harness error is done when audit.py
# marked it by hand after a finish that scored it, failed when lane.py's
# finish did.
FINISHED = ("done", "failed")


def harness_error_of(item):
    """Why the item's run measured the harness rather than the arm, or
    None: audit.py's verdict in the run's audit.json wherever the run has
    one, since an audit run again by hand may have changed it, as the
    report and summary.py read it; else the mark lane.py put on the item,
    which a run stopped before prepare, with no run directory, has alone."""
    audit = audit_of(item)
    if audit is not None:
        return audit.get("harness_error")
    return item.get("harness_error")


def audit_of(item):
    """The item's run's audit.json, or None when it has none to read."""
    if not item.get("run_dir"):
        return None
    try:
        audit = json.loads((Path(item["run_dir"]) / "audit.json").read_text())
    except (OSError, ValueError):
        return None
    return audit if isinstance(audit, dict) else None


def parse_option(text):
    """KEY=VALUE as run.py's --option reads it: the value as JSON when it
    is JSON, else as the text."""
    key, equals, value = text.partition("=")
    if not equals or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value


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
    add.add_argument("--order-seed", type=int, help="put each pair's arms in an order drawn from this seed")
    add.add_argument("--option", action="append", type=parse_option, metavar="KEY=VALUE",
                     help="a run option every item passes to run.py prepare (--option); repeat for more")
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
    req.add_argument("--harness-only", action="store_true",
                     help="the finished items, done or failed, whose run measured the harness rather than "
                          "the arm, whatever --status says")
    req.set_defaults(func=command_requeue)
    shuffle = sub.add_parser("shuffle")
    shuffle.add_argument("queue")
    shuffle.add_argument("--tag", required=True)
    shuffle.add_argument("--seed", type=int, required=True)
    shuffle.set_defaults(func=command_shuffle)
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
