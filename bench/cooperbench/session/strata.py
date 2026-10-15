"""The natural strata of CooperBench's pairs, and a nested stratified
sample of them, so a run can start small and grow without redrawing.

    python3 strata.py [--pairs @file|flash] [--published coop/index.json] [--by language,conflict,published] table [--md]
    python3 strata.py [the same options] sample N [--allocation equal|proportional] [--seed 0]

Both take --skip REPO/TASK,REPO/TASK: the tasks queue.py add --skip leaves
out before its draw, there given as separate words, so the same tasks
reproduce the queue's order.

Each pair has, from the dataset alone: its repository and the language
of the code the features change (by repository), whether the two gold
patches conflict when merged (gold_conflict_report.json, the paper's
Table 4), and the size of the two gold patches together (lines added,
in terciles over the pair list: small, medium, large). With the website's
coop/index.json (cooperbench/website, public/static/data/coop/index.json),
it also has the published difficulty: the share of the paper's coop
runs that passed it, eight runs for most pairs and three for some (none:
no run; few: under half; many: half or more), and whether the paper's
Claude run passed it.

A sample is nested: the order sample prints is fixed by the seed, and
the first N of it is the sample of size N, so a run of 10 pairs grown to
20 keeps its first 10 and adds 10. With equal allocation the order
takes one pair from each stratum in turn, the largest stratum first, so
a small sample covers the strata that hold most pairs and a sample of
as many pairs as there are strata holds one of each; with proportional
allocation the strata are represented as they are in the list. A
stratum is the combination of the keys named by --by, in the order
given.
"""

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

LANGUAGE = {
    "pallets_click_task": "python", "pallets_jinja_task": "python", "dottxt_ai_outlines_task": "python",
    "dspy_task": "python", "huggingface_datasets_task": "python", "samuelcolvin_dirty_equals_task": "python",
    "llama_index_task": "python", "pillow_task": "python+c", "typst_task": "rust", "go_chi_task": "go",
    "openai_tiktoken_task": "python+rust",
}
KEYS = ("language", "conflict", "size", "published")


def dataset_dir(args):
    return Path(args.dataset_dir)


def flash_pairs(dataset, pairs_arg):
    """(repo, task, [a, b]) for the argument: flash, @file of REPO/TASK
    lines, or REPO/TASK[/fA_fB] entries separated by commas."""
    subset = json.load(open(dataset / "subsets" / "flash.json"))
    by_task = {(t["repo"], int(t["task_id"])): t["pairs"] for t in subset["tasks"]}
    wanted = []
    if pairs_arg == "flash":
        wanted = [(repo, task, None) for repo, task in by_task]
    elif pairs_arg.startswith("@"):
        for line in Path(pairs_arg[1:]).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                parts = line.split("/")
                features = [int(p) for p in parts[2].replace("f", "").split("_")] if len(parts) == 3 else None
                wanted.append((parts[0], int(parts[1]), features))
    else:
        for entry in pairs_arg.split(","):
            parts = entry.strip().split("/")
            features = [int(p) for p in parts[2].replace("f", "").split("_")] if len(parts) == 3 else None
            wanted.append((parts[0], int(parts[1]), features))
    out = []
    for repo, task, features in wanted:
        for pair in by_task[(repo, task)]:
            if features is None or sorted(features) == sorted(pair):
                out.append((repo, task, list(pair)))
    return out


def added_lines(path):
    text = Path(path).read_text(errors="replace")
    return sum(1 for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))


def gold_conflicts(dataset):
    report = json.load(open(dataset / "gold_conflict_report.json"))
    return {(r["repo"], int(r["task_id"]), tuple(sorted((r["f1"], r["f2"])))): r["has_conflict"]
            for r in report["all_results"]}


def published_results(path):
    """{(repo, task, (a, b)): {model: passed}} from the website's index."""
    if not path:
        return {}
    index = json.load(open(path))
    out = {}
    for row in index["tasks"]:
        a, b = (int(f.replace("feature", "")) for f in row["features"].split("_"))
        task = int(row["taskId"].replace("task", ""))
        out[(row["repo"], task, tuple(sorted((a, b))))] = {m: r.get("passed") for m, r in row["results"].items()}
    return out


def tercile_bounds(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]


def describe(dataset, pairs, published):
    """One record per pair with its strata."""
    conflicts = gold_conflicts(dataset)
    rows = []
    for repo, task, (a, b) in pairs:
        task_dir = dataset / repo / f"task{task}"
        lines = added_lines(task_dir / f"feature{a}" / "feature.patch") + added_lines(task_dir / f"feature{b}" / "feature.patch")
        key = (repo, task, tuple(sorted((a, b))))
        results = published.get(key)
        rows.append({"repo": repo, "task": task, "pair": f"f{a}_f{b}", "language": LANGUAGE.get(repo, "other"),
                     "conflict": "conflict" if conflicts.get(key) else "clean", "gold_lines": lines,
                     "published_passed": sum(1 for v in results.values() if v) if results else None,
                     "published_runs": len(results) if results else None,
                     "published_claude": results.get("claude") if results else None})
    if rows:
        low, high = tercile_bounds([r["gold_lines"] for r in rows])
        for r in rows:
            r["size"] = "small" if r["gold_lines"] < low else "large" if r["gold_lines"] >= high else "medium"
            n, runs = r["published_passed"], r["published_runs"]
            share = n / runs if runs else None
            r["published"] = "unknown" if share is None else "none" if n == 0 else "few" if share < 0.5 else "many"
    return rows


def stratum_of(row, keys):
    return tuple(row[k] for k in keys)


def nested_order(rows, keys, allocation, seed):
    """The rows in sample order: a seeded shuffle within each stratum,
    then the strata interleaved one at a time (equal) or each row placed
    at its evenly spaced position in its stratum (proportional)."""
    rng = random.Random(seed)
    cells = OrderedDict()
    for row in sorted(rows, key=lambda r: (r["repo"], r["task"], r["pair"])):
        cells.setdefault(stratum_of(row, keys), []).append(row)
    for members in cells.values():
        rng.shuffle(members)
    if allocation == "equal":
        # The largest strata first, so the first pairs of the order come
        # from where most of the list is; ties in the strata's own order.
        by_size = sorted(cells.items(), key=lambda c: (-len(c[1]), tuple(str(x) for x in c[0])))
        order, queues = [], [list(m) for _, m in by_size]
        while any(queues):
            for queue in queues:
                if queue:
                    order.append(queue.pop(0))
        return order
    placed = []
    for members in cells.values():
        offset = rng.random()
        for rank, row in enumerate(members):
            placed.append(((rank + offset) / len(members), row))
    return [row for _, row in sorted(placed, key=lambda p: p[0])]


def pair_name(row):
    return f"{row['repo']}/{row['task']}/{row['pair']}"


def command_table(args, rows):
    keys = args.by.split(",")
    counts = Counter(stratum_of(r, keys) for r in rows)
    if args.md:
        print("| " + " | ".join(keys) + " | pairs |")
        print("|" + "---|" * (len(keys) + 1))
        for cell, n in sorted(counts.items(), key=lambda c: tuple(str(x) for x in c[0])):
            print("| " + " | ".join(str(x) for x in cell) + f" | {n} |")
        print()
        print("| pair | language | gold merge | gold lines | size | published coop passes / runs | paper's Claude |")
        print("|---|---|---|---|---|---|---|")
        for r in sorted(rows, key=lambda r: (r["repo"], r["task"], r["pair"])):
            claude = "-" if r["published_claude"] is None else "pass" if r["published_claude"] else "fail"
            passed = "-" if r["published_passed"] is None else f"{r['published_passed']}/{r['published_runs']}"
            print(f"| {pair_name(r)} | {r['language']} | {r['conflict']} | {r['gold_lines']} | {r['size']} | {passed} | {claude} |")
    else:
        for cell, n in sorted(counts.items(), key=lambda c: tuple(str(x) for x in c[0])):
            print(f"{n:3d}  " + "  ".join(f"{k}={v}" for k, v in zip(keys, cell)))
        print(f"{len(rows)} pairs")


def command_sample(args, rows):
    keys = args.by.split(",")
    order = nested_order(rows, keys, args.allocation, args.seed)
    chosen = order[:args.n]
    for row in chosen:
        note = "  ".join(f"{k}={row[k]}" for k in keys)
        print(f"{pair_name(row)}  # {note}")
    taken = Counter(stratum_of(r, keys) for r in chosen)
    whole = Counter(stratum_of(r, keys) for r in rows)
    for cell in sorted(whole, key=lambda c: tuple(str(x) for x in c)):
        print(f"# {taken.get(cell, 0)}/{whole[cell]}  " + "  ".join(f"{k}={v}" for k, v in zip(keys, cell)), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", default=str(Path.home() / "cooperbench" / "dataset"))
    parser.add_argument("--pairs", default="flash")
    parser.add_argument("--published", help="the website's coop/index.json, for the published difficulty")
    parser.add_argument("--by", default="language,conflict", help="the keys of a stratum, from " + ", ".join(KEYS))
    parser.add_argument("--skip", default="",
                        help="REPO/TASK entries, comma separated, left out before the strata are read, as queue.py add "
                             "--skip leaves them out before its draw, so the same skip here reproduces the queue's order")
    sub = parser.add_subparsers(dest="command", required=True)
    table = sub.add_parser("table")
    table.add_argument("--md", action="store_true")
    sample = sub.add_parser("sample")
    sample.add_argument("n", type=int)
    sample.add_argument("--allocation", choices=("equal", "proportional"), default="equal")
    sample.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    dataset = dataset_dir(args)
    skip = {entry.strip() for entry in args.skip.split(",") if entry.strip()}
    pairs = [p for p in flash_pairs(dataset, args.pairs) if f"{p[0]}/{p[1]}" not in skip]
    rows = describe(dataset, pairs, published_results(args.published))
    try:
        if args.command == "table":
            command_table(args, rows)
        else:
            command_sample(args, rows)
    except BrokenPipeError:
        # The table is read through head often enough; a closed pipe is
        # not an error worth a traceback.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


if __name__ == "__main__":
    sys.exit(main())
