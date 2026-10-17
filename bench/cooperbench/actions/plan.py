"""The matrix of a benchmark dispatch: which (pair, arm) runs on which
account, in what order, for .github/workflows/bench.yml.

A run is one job per pair and arm, so a pair that stops on a usage limit
costs that one job and the resumed dispatch runs it again. Every arm of a
pair goes to one account, so the arms of a pair draw on one usage window
and no account sees a pair's work twice; accounts take pairs in turn; and
the arm order rotates by pair index, so no arm is always first on an
account's fresh window or always last on its tired one, as far as GitHub
keeps the order (its queue is first in, first out by the time a job
started waiting, which it does not guarantee). GitHub runs at most 256
jobs from one matrix, and queues at most 100 pending jobs in one
concurrency group and cancels the rest, so a dispatch holds at most 256
items, an account at most 100 of them in a real run, and the plan says
how many it left out. The items an earlier dispatch of the tag finished
(--finished names them, from the marker artifacts the run jobs leave,
artifacts.py list-finished) are left out before those caps, so a dispatch
of the same tag takes up where the last one stopped and the caps never
hold the same items back twice; each pair keeps its account across
dispatches, since the account is chosen by the pair's place in the list.

The accounts input "auto" takes the slots of SYMPHONY_CLAUDE_ACCOUNTS (all
eight when it is empty) whose secret is set, leaving out each one the
branch pool says is at its limit until it resets: a five-hour, seven-day
or per-model window at or past 90 percent, as symphony-template's
tools/pool.py reads it (pool.py here is that file's copy). The slots kept
stay in the variable's order, so a pair keeps its account across
dispatches for as long as the same slots are free.

A fake run's tag is fake-<tag> (a pull request's smoke-<...> already
says so), and a real run refuses such a tag: a fake pair's artifact looks
finished, and a real dispatch under the same tag would skip the pair and
report the fake one as the benchmark.

The pairs come from CooperBench's flash subset (dataset/subsets/flash.json),
narrowed by the dispatch's "pairs" input: "flash" (or nothing) for every pair, a list
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
# GitHub queues at most this many pending jobs in one concurrency group
# (queue: max), and a real run's jobs queue per account.
QUEUE_LIMIT = 100
# GitHub runs at most this many jobs from one matrix, in any mode.
MATRIX_LIMIT = 256
# An account is a slot, 1 to 8, whose token is the secret
# SYMPHONY_CLAUDE_<slot>; the workflow names the eight secrets one by one,
# since a secret looked up by a computed name hands the runner them all.
SLOTS = tuple(str(n) for n in range(1, 9))
# A tag that says its artifacts hold no real pair.
FAKE_PREFIXES = ("fake-", "smoke-")
# Minutes an agent may take: the default and the most a mode allows, so a
# job ends inside its timeout-minutes with the scoring (up to three
# hours) and the upload still to run.
TIMEOUT_DEFAULT = {"real": 120, "fake": 20, "survey": 20}
TIMEOUT_MOST = {"real": 120, "fake": 30, "survey": 30}
ARMS_DIR = Path(__file__).resolve().parents[3] / "coordbench" / "arms"
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
    if not entries or entries == ["flash"]:
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


def plan_items(pairs, arms, accounts, tag, queue_limit=QUEUE_LIMIT, finished=()):
    """One item per pair and arm, pair-major, each pair's arms on one
    account, accounts in turn, the arm order rotated by pair index; the
    items whose artifact an earlier dispatch finished are left out first,
    then the matrix limit and the per-account queue cap where one applies
    hold the rest back. Returns the items, how many were finished and how
    many the caps dropped; a later dispatch of the same tag takes those."""
    items, done, dropped = [], 0, 0
    counts = {account: 0 for account in accounts}
    for index, pair in enumerate(pairs):
        account = accounts[index % len(accounts)]
        rotated = arms[index % len(arms):] + arms[:index % len(arms)]
        for arm in rotated:
            if artifact_name(tag, arm, pair) in finished:
                done += 1
                continue
            if len(items) >= MATRIX_LIMIT or (queue_limit and counts[account] >= queue_limit):
                dropped += 1
                continue
            counts[account] += 1
            repo, task, features = pair
            items.append({"repo": repo, "task": task, "features": feature_word(features),
                          "arm": arm, "account": account, "name": run_name(tag, arm, pair),
                          "artifact": artifact_name(tag, arm, pair), "order": len(items)})
    return items, done, dropped


def survey_tasks(pairs):
    """Each task once, for the gradeability survey."""
    tasks = []
    for repo, task, _ in pairs:
        if (repo, task) not in [(t["repo"], t["task"]) for t in tasks]:
            tasks.append({"repo": repo, "task": task})
    return tasks


def mode_tag(mode, tag):
    """The tag the artifacts carry: a fake run's says so, a real run's may
    not, a survey's is as given."""
    if mode == "fake" and not tag.startswith(FAKE_PREFIXES):
        return "fake-" + tag
    if mode == "real" and tag.startswith(FAKE_PREFIXES):
        raise ValueError(f"a real run takes no tag starting with {' or '.join(FAKE_PREFIXES)}: its artifacts would "
                         f"be mistaken for finished pairs and reported as the benchmark ({tag!r})")
    return tag


def auto_accounts(pool_text, present, found, now=None):
    """The slots auto takes, in the pool's order, and a note on each slot
    left out: one whose secret is unset, and one a window holds, with
    when it is free again. Refuses when no slot is left."""
    import pool
    slots = [slot for slot in parse_list(pool_text) if slot in SLOTS] or list(SLOTS)
    now = now or pool.datetime.now(pool.timezone.utc)
    kept, notes, frees = [], [], []
    for slot in slots:
        if present is not None and slot not in present:
            continue
        windows, scoped = pool.current(found.get(slot, []), now)
        until = pool.held_until(windows, scoped, pool.THRESHOLD, now)
        if until:
            notes.append(f"auto left out slot {slot}, at its limit until {pool.iso(until)}")
            frees.append(until)
        else:
            kept.append(slot)
    if not kept:
        when = f"; the first is free at {pool.iso(min(frees))}" if frees else ""
        raise ValueError("auto found no slot to run on: every slot of SYMPHONY_CLAUDE_ACCOUNTS is unset or at its "
                         f"limit{when}")
    return kept, notes


def chosen_accounts(mode, accounts_text, present):
    """The slots a real run uses, each one whose secret the workflow saw
    set when it says which it saw; the mode's name otherwise."""
    if mode != "real":
        return [mode]
    accounts = parse_list(accounts_text)
    if not accounts:
        raise ValueError("a real run names at least one account (the accounts input, or the variable "
                         "SYMPHONY_CLAUDE_ACCOUNTS)")
    for account in accounts:
        if account not in SLOTS:
            raise ValueError(f"an account is a slot from 1 to 8, the number in its secret's name SYMPHONY_CLAUDE_<slot>: "
                             f"{account!r}")
        if present is not None and account not in present:
            raise ValueError(f"the organization secret SYMPHONY_CLAUDE_{account} is unset or not shared with this "
                             f"repository, so slot {account} has no token; set it, or leave the slot out")
    return accounts


def chosen_arms(arms_text, arms_dir=ARMS_DIR):
    """The arms, each one the runner has (coordbench/arms/<name>/arm.json),
    so a misspelt arm fails the plan and not every job of the arm."""
    arms = parse_list(arms_text, ARMS)
    known = sorted(p.parent.name for p in Path(arms_dir).glob("*/arm.json"))
    for arm in arms:
        if arm not in known:
            raise ValueError(f"no arm named {arm!r} under {arms_dir}; the arms are {', '.join(known)}")
    return arms


def chosen_timeout(mode, text):
    """Minutes an agent may take: the mode's default when the input is
    empty, else the whole minutes given, at most what the mode allows."""
    if not (text or "").strip():
        return TIMEOUT_DEFAULT[mode]
    if not re.fullmatch(r"\d{1,4}", text.strip()):
        raise ValueError(f"timeout_min is whole minutes: {text!r}")
    minutes = int(text.strip())
    if not 1 <= minutes <= TIMEOUT_MOST[mode]:
        raise ValueError(f"timeout_min is from 1 to {TIMEOUT_MOST[mode]} in {mode} mode, so the job ends inside "
                         f"its own limit with the scoring and the upload still to run: {minutes}")
    return minutes


REGISTERED_PAIRS = "@bench/cooperbench/pairs.txt"


def registration_notes(mode, pairs_text, arms):
    """What a real run changed against PLAN.md's registered list and arms,
    said in the plan's notes, so the job summary shows it before the
    reviewer's click."""
    notes = []
    if mode != "real":
        return notes
    if pairs_text.strip() != REGISTERED_PAIRS:
        notes.append(f"the pairs are not the registered list {REGISTERED_PAIRS} (PLAN.md): {pairs_text.strip()!r}")
    if arms != list(ARMS):
        notes.append(f"the arms are not PLAN.md's {','.join(ARMS)}: {','.join(arms)}")
    return notes


def plan(dataset_dir, mode, pairs_text, arms_text, accounts_text, tag, finished=(), present=None,
         timeout_text="", arms_dir=ARMS_DIR, pool_text="", readings=None, now=None):
    pairs = chosen_pairs(dataset_dir, pairs_text)
    tag = mode_tag(mode, tag)
    arms = chosen_arms(arms_text, arms_dir)
    auto_notes = []
    if mode == "real" and accounts_text.strip().lower() == "auto":
        accounts, auto_notes = auto_accounts(pool_text, present, readings or {}, now)
    else:
        accounts = chosen_accounts(mode, accounts_text, present)
    timeout = chosen_timeout(mode, timeout_text)
    items, done, dropped = [], 0, 0
    if mode != "survey":
        items, done, dropped = plan_items(pairs, arms, accounts, tag, QUEUE_LIMIT if mode == "real" else 0,
                                          set(finished))
    notes = registration_notes(mode, pairs_text, arms) + auto_notes
    if done:
        notes.append(f"{done} items finished by an earlier dispatch of the tag and left out")
    if dropped:
        notes.append(f"{dropped} items left out: GitHub runs at most {MATRIX_LIMIT} jobs from one matrix"
                     + (f" and queues at most {QUEUE_LIMIT} per account" if mode == "real" else "")
                     + "; dispatch the same tag again once these finish")
    survey = survey_tasks(pairs)
    return {"mode": mode, "tag": tag, "pairs": len(pairs), "arms": arms, "accounts": accounts,
            "timeout_min": timeout, "items": items, "survey": survey, "finished": done, "dropped": dropped,
            "count": len(survey) if mode == "survey" else len(items), "notes": notes}


def write_outputs(result, path):
    with open(path, "a", encoding="utf-8") as out:
        out.write(f"matrix={json.dumps({'include': result['items']})}\n")
        out.write(f"survey={json.dumps({'include': result['survey']})}\n")
        out.write(f"tag={result['tag']}\n")
        out.write(f"timeout_min={result['timeout_min']}\n")
        out.write(f"count={result['count']}\n")
        out.write(f"notes={' '.join(result['notes'])}\n")


def read_names(path):
    """The artifact names in a file, one per line, blank lines left out."""
    if not path:
        return set()
    return {line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()}


def listed_finished(mode, tag):
    """The pair artifacts of the tag whose marker is on GitHub, read with
    the runner's token (artifacts.py list-finished); when the listing
    fails every item is planned and the jobs skip the finished ones, which
    costs runner starts, not usage, and the plan's notes say so."""
    import artifacts
    if mode == "survey":
        return set(), None
    try:
        found, cut = artifacts.list_artifacts(artifacts.settings())
    except (KeyError, *artifacts.READ_PROBLEMS) as problem:
        return set(), f"could not list the finished items ({problem}); every item is planned and the jobs skip " \
                      f"the finished ones"
    note = None
    if cut:
        note = f"the artifact listing was cut at {artifacts.PAGE * artifacts.MOST_PAGES}; a finished item older " \
               f"than that is planned again and its job skips it"
    names = artifacts.by_name(found, f"{mode_tag(mode, tag)}__")
    return {name[:-len(artifacts.DONE_SUFFIX)] for name in names if name.endswith(artifacts.DONE_SUFFIX)}, note


def pool_readings(mode, accounts_text, folder):
    """The pool's readings by slot, for auto alone: from the folder given,
    else from origin's branch pool, which a repository with no reading
    yet does not have, so every slot then counts as free."""
    if mode != "real" or accounts_text.strip().lower() != "auto":
        return None
    import tempfile
    import pool
    if folder:
        return pool.load(folder)
    with tempfile.TemporaryDirectory(prefix="pool-") as scratch:
        return pool.load(pool.branch_readings(scratch))


def secrets_present(environ=None):
    """The slots whose secret the workflow saw set, from HAS_SECRET_<slot>
    ('true' or 'false', each set by the workflow from the secret's name),
    or None when the workflow did not say, as a run outside it does not."""
    environ = os.environ if environ is None else environ
    if not any(f"HAS_SECRET_{slot}" in environ for slot in SLOTS):
        return None
    return {slot for slot in SLOTS if environ.get(f"HAS_SECRET_{slot}") == "true"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset-dir", required=True, help="CooperBench's dataset/ directory")
    parser.add_argument("--mode", choices=("survey", "fake", "real"), required=True)
    parser.add_argument("--pairs", default="flash")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--accounts", default="", help="the slots, or auto for the free ones of --pool")
    parser.add_argument("--pool", default="", help="auto's slots, the variable SYMPHONY_CLAUDE_ACCOUNTS")
    parser.add_argument("--readings", help="a folder of the pool's readings; auto reads origin's branch pool without it")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--timeout-min", default="", help="minutes an agent may take; the mode's default when empty")
    parser.add_argument("--finished", help="a file naming the artifacts an earlier dispatch finished, one per line")
    parser.add_argument("--list-finished", action="store_true",
                        help="read the finished items' markers from GitHub (GITHUB_TOKEN, GITHUB_REPOSITORY)")
    parser.add_argument("--arms-dir", default=ARMS_DIR, help="where the arms are (coordbench/arms)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,40}", args.tag):
        parser.error("the tag is letters, digits, dots and dashes, up to 41 characters")
    finished, note = read_names(args.finished), None
    try:
        mode_tag(args.mode, args.tag)
        if args.list_finished:
            finished, note = listed_finished(args.mode, args.tag)
        result = plan(args.dataset_dir, args.mode, args.pairs, args.arms, args.accounts, args.tag,
                      finished, secrets_present(), args.timeout_min, args.arms_dir, args.pool,
                      pool_readings(args.mode, args.accounts, args.readings))
    except (ValueError, OSError) as problem:
        parser.error(str(problem))
    if note:
        result["notes"].append(note)
    print(json.dumps(result, indent=2))
    if os.environ.get("GITHUB_OUTPUT"):
        write_outputs(result, os.environ["GITHUB_OUTPUT"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
