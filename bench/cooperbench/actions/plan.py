"""The matrix of a benchmark dispatch: which (pair, arm) runs on which
account, in what order, for .github/workflows/bench.yml.

A run is one job per pair and arm, so a pair that stops on a usage limit
costs that one job and the resumed dispatch runs it again. Every arm of a
pair goes to one account, so the arms of a pair draw on one usage window
and no account sees a pair's work twice; accounts take pairs in turn; and
the arm order rotates by pair index, so no arm is always first on an
account's fresh window or always last on its tired one. GitHub queues at
most 100 pending jobs in one concurrency group and cancels the rest, so an
account gets at most that many items in one dispatch and the plan says
how many it left out; a dispatch of the same tag later takes them, since
a finished item is skipped by its artifact.

The pairs come from CooperBench's flash subset (dataset/subsets/flash.json),
narrowed by the dispatch's "pairs" input: "flash" for every pair, a list
such as "pallets_click_task/2068, pallets_jinja_task/1559/f5_f8" (a task
for all its flash pairs, a task and a feature pair for that one), or
"@path" for a file with one such entry per line, which is how the
pre-registered list of bench/cooperbench/PLAN.md is named.

Run with --dataset-dir and the inputs; it prints one JSON object and, when
GITHUB_OUTPUT is set, appends the workflow's outputs to it.
"""

import argparse
import json
import os
import re
from pathlib import Path

ARMS = ("solo", "coop", "coop-git", "team", "sdlc")
QUEUE_LIMIT = 100
ENTRY = re.compile(r"^(?P<repo>[A-Za-z0-9_]+)/(?P<task>\d{1,9})(?:/f?(?P<first>\d{1,4})_f?(?P<second>\d{1,4}))?$")
SEPARATORS = re.compile(r"[,\s]+")


def flash_pairs(dataset_dir):
    """Every (repo, task, features) of the flash subset, in its order."""
    subset = json.loads((Path(dataset_dir) / "subsets" / "flash.json").read_text(encoding="utf-8"))
    pairs = []
    for task in subset["tasks"]:
        for features in task.get("pairs") or task.get("groups") or []:
            pairs.append((task["repo"], int(task["task_id"]), tuple(sorted(int(f) for f in features))))
    return pairs


def parse_entries(text):
    """The entries of a pairs input: words split on commas and blanks, a
    word starting with @ read as a file of entries, one per line, with
    blank lines and # comments left out."""
    entries = []
    for word in SEPARATORS.split(text.strip()):
        if not word:
            continue
        if word.startswith("@"):
            for line in Path(word[1:]).read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    entries.extend(parse_entries(line))
        else:
            entries.append(word)
    return entries


def chosen_pairs(dataset_dir, text):
    """The pairs the input names, in the subset's order, each once."""
    everything = flash_pairs(dataset_dir)
    entries = parse_entries(text)
    if entries == ["flash"]:
        return everything
    wanted = []
    for entry in entries:
        match = ENTRY.match(entry)
        if not match:
            raise ValueError(f"not a pair entry: {entry!r} (expected repo/task or repo/task/f1_f6, or flash)")
        repo, task = match.group("repo"), int(match.group("task"))
        features = None
        if match.group("first"):
            features = tuple(sorted((int(match.group("first")), int(match.group("second")))))
        found = [pair for pair in everything if pair[0] == repo and pair[1] == task
                 and (features is None or pair[2] == features)]
        if not found:
            raise ValueError(f"the flash subset has no pair for {entry}")
        wanted.extend(found)
    return [pair for index, pair in enumerate(everything) if pair in wanted]


def parse_list(text, default=()):
    words = [word for word in SEPARATORS.split((text or "").strip()) if word]
    return list(words) if words else list(default)


def feature_word(features):
    return "f" + "_f".join(str(f) for f in features)


def run_name(tag, arm, pair):
    repo, task, features = pair
    return f"{tag}-{arm}-{repo}-{task}-{feature_word(features)}"


def artifact_name(tag, arm, pair):
    """The artifact's name: the run name's parts joined by two underscores,
    which no part contains, so a report job can read them back; GitHub
    refuses a few characters in an artifact's name, none of them here."""
    repo, task, features = pair
    return "__".join([tag, arm, repo, str(task), feature_word(features)])


def plan_items(pairs, arms, accounts, tag):
    """One item per pair and arm, pair-major, each pair's arms on one
    account, accounts in turn, the arm order rotated by pair index; then
    the per-account queue cap."""
    items, dropped = [], 0
    counts = {account: 0 for account in accounts}
    for index, pair in enumerate(pairs):
        account = accounts[index % len(accounts)]
        rotated = arms[index % len(arms):] + arms[:index % len(arms)]
        for arm in rotated:
            if counts[account] >= QUEUE_LIMIT:
                dropped += 1
                continue
            counts[account] += 1
            repo, task, features = pair
            items.append({"repo": repo, "task": task, "features": feature_word(features),
                          "arm": arm, "account": account, "name": run_name(tag, arm, pair),
                          "artifact": artifact_name(tag, arm, pair), "order": len(items)})
    return items, dropped


def survey_tasks(pairs):
    """Each task once, for the gradeability survey."""
    tasks = []
    for repo, task, _ in pairs:
        if (repo, task) not in [(t["repo"], t["task"]) for t in tasks]:
            tasks.append({"repo": repo, "task": task})
    return tasks


def plan(dataset_dir, mode, pairs_text, arms_text, accounts_text, tag):
    pairs = chosen_pairs(dataset_dir, pairs_text)
    arms = parse_list(arms_text, ARMS)
    accounts = parse_list(accounts_text) if mode == "real" else [mode]
    if mode == "real" and not accounts:
        raise ValueError("a real run names at least one account (the accounts input, or the variable "
                         "SYMPHONY_CLAUDE_ACCOUNTS)")
    items, dropped = plan_items(pairs, arms, accounts, tag)
    notes = []
    if dropped:
        notes.append(f"{dropped} items left out: GitHub queues at most {QUEUE_LIMIT} jobs per account; "
                     f"dispatch the same tag again once these finish")
    return {"mode": mode, "tag": tag, "pairs": len(pairs), "arms": arms, "accounts": accounts,
            "items": items, "survey": survey_tasks(pairs), "dropped": dropped, "notes": notes}


def write_outputs(result, path):
    with open(path, "a", encoding="utf-8") as out:
        out.write(f"matrix={json.dumps({'include': result['items']})}\n")
        out.write(f"survey={json.dumps({'include': result['survey']})}\n")
        out.write(f"tag={result['tag']}\n")
        out.write(f"count={len(result['items'])}\n")
        out.write(f"notes={' '.join(result['notes'])}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-dir", required=True, help="CooperBench's dataset/ directory")
    parser.add_argument("--mode", choices=("survey", "fake", "real"), required=True)
    parser.add_argument("--pairs", default="flash")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--accounts", default="")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,40}", args.tag):
        parser.error("the tag is letters, digits, dots and dashes, up to 41 characters")
    try:
        result = plan(args.dataset_dir, args.mode, args.pairs, args.arms, args.accounts, args.tag)
    except (ValueError, OSError) as problem:
        parser.error(str(problem))
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        write_outputs(result, os.environ["GITHUB_OUTPUT"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
