"""How much of each Claude account's limits is used, and which account a
run should take: the pool of slots the token wizard stores
(scripts/tokens.py, docs/setup/claude-tokens.md).

A subscription has a five-hour window and a seven-day window, and some
plans a weekly limit for one model, such as Fable. GitHub never gives a
secret back, so nothing can read a slot's usage by looking at its token;
three kinds of reading are kept instead, one file per slot and kind on the
branch `pool`:

  run    a Claude run in Actions (the workflows record it once step 2 of
         docs/research/claude-token-pool.md wires them). Claude Code reads the
         anthropic-ratelimit-unified-* headers of each response and, in
         its stream-json output, emits a rate_limit_event whose
         unifiedWindows carry the five-hour and seven-day windows'
         utilization and reset; `observe` takes the last one from the
         run's execution file, at no cost.
  probe  `probe` asks Claude for one word with a slot's token and reads
         the same event: a fresh reading for a small part of the window,
         run by .github/workflows/pool.yml when a person asks for one.
  cswap  claude-swap on the owner's machine reads each of its accounts'
         usage page, which a token from claude setup-token cannot (it is
         inference-only), and that page has the per-model weekly limits;
         `python3 scripts/tokens.py usage` maps cswap's accounts to slots
         as the wizard remembered them and sends the readings here.

No reading holds a token, an e-mail or a word of a conversation: a slot's
number, when, the windows' fractions and resets, and per-model limits by
the model's name. `pick` is the auto mode: of the slots in the pool, the
one with the most room in its five-hour window, then its seven-day
window, among those no window holds at or past the threshold; when every
slot is held, the one whose hold ends first, which the caller is told.
A window whose reset has passed counts as empty, since it is.

  python3 tools/pool.py observe --execution-file F --slot N --out DIR [--source probe]
  python3 tools/pool.py probe --slot N --out FILE
  python3 tools/pool.py publish FILE...        onto the branch pool (Actions)
  python3 tools/pool.py show DIR               the slots as the panel shows them
  python3 tools/pool.py pick DIR --slots 1,2,3 [--threshold 0.9]
  python3 tools/pool.py choose --worker NAME --pool 1,2,3   the slot a worker's run takes

Which account a worker in Actions runs on is the file
.github/symphony/accounts.json, {"default": "main", "workers": {"NAME":
"auto"}}: main is CLAUDE_CODE_OAUTH_TOKEN, a number from 1 to 8 is that
slot's SYMPHONY_CLAUDE_<slot>, and auto is pick over the pool, the slots
of SYMPHONY_CLAUDE_ACCOUNTS. With no file, or no line for the worker and
no default, the worker runs on main, as every worker did before.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = 1
BRANCH = "pool"
SOURCES = ("run", "probe", "cswap")
SLOTS = tuple(str(slot) for slot in range(1, 9)) + ("main",)
WINDOWS = ("five_hour", "seven_day")
THRESHOLD = 0.9
UNKNOWN_HOLD = timedelta(hours=5)
FILE_NAME = re.compile(r"^slot-(main|[1-8])\.(run|probe|cswap)\.json$")
NAME_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
PROBE = ("claude", "-p", "Reply with the one word ok.", "--model", "haiku", "--max-turns", "1",
         "--output-format", "stream-json", "--verbose")
PROBE_TIMEOUT = 120
PUSH_ATTEMPTS = 5
ASSIGNMENTS = ".github/symphony/accounts.json"
CHOICES = SLOTS + ("auto",)


class PoolError(Exception):
    """What could not be read or written; never holds a token."""


# Times.

def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value):
    """An ISO time or unix seconds, as Claude Code and cswap write them, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and len(value) <= 40:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return None


def fraction(value):
    """A window's utilization as a fraction, 0 and up, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return max(0.0, min(float(value), 10.0))


def window(utilization, resets):
    used = fraction(utilization)
    if used is None:
        return None
    found = {"utilization": round(used, 4)}
    moment = parse_time(resets)
    if moment is not None:
        found["resetsAt"] = iso(moment)
    return found


# Readings.

def reading(slot, source, windows, scoped=(), status=None, now=None):
    record = {"version": VERSION, "slot": slot, "source": source, "at": iso(now or datetime.now(timezone.utc)),
              "windows": {name: found for name, found in windows.items() if found}}
    if scoped:
        record["scoped"] = list(scoped)
    if status in ("allowed", "allowed_warning", "rejected"):
        record["status"] = status
    return record


def messages_in(text):
    """The messages of an execution file: one JSON array, or a JSON object
    a line, as stream-json writes them."""
    text = text.strip()
    if text.startswith("["):
        try:
            found = json.loads(text)
        except json.JSONDecodeError:
            found = None
        if isinstance(found, list):
            return [item for item in found if isinstance(item, dict)]
    found = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            found.append(item)
    return found


# The per-model weekly windows a rate_limit_event can name, by the model.
MODEL_WINDOWS = {"seven_day_opus": "Opus", "seven_day_sonnet": "Sonnet"}


def windows_from_event(info):
    """The windows a rate_limit_event's rate_limit_info carries, and its
    per-model ones: every window under unifiedWindows, and the top-level
    one where it names one. A refusal names the window that refused and
    may give no utilization: that window is full until its reset."""
    windows, scoped = {}, []
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict):
        for name in WINDOWS:
            each = unified.get(name)
            if isinstance(each, dict):
                windows[name] = window(each.get("utilization"), each.get("resetsAt"))
    kind = info.get("rateLimitType")
    used = info.get("utilization")
    if info.get("status") == "rejected":
        used = 1.0 if fraction(used) is None else max(fraction(used), 1.0)
    if kind in WINDOWS and (info.get("status") == "rejected" or not windows.get(kind)):
        windows[kind] = window(used, info.get("resetsAt"))
    elif kind in MODEL_WINDOWS:
        found = window(used, info.get("resetsAt"))
        if found:
            found["name"] = MODEL_WINDOWS[kind]
            scoped.append(found)
    return windows, scoped


def observed(messages, slot, source, now=None):
    """The reading of the last rate_limit_event that names a window, or None."""
    for message in reversed(messages):
        info = message.get("rate_limit_info") if message.get("type") == "rate_limit_event" else None
        if not isinstance(info, dict):
            continue
        windows, scoped = windows_from_event(info)
        if any(windows.values()) or scoped:
            return reading(slot, source, windows, scoped, status=info.get("status"), now=now)
    return None


def from_cswap(row, slot, now=None):
    """A reading from one account row of `cswap list --json`: its usage, or
    the last good one when the newest fetch failed, with when it was read."""
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else row.get("lastGoodUsage")
    if not isinstance(usage, dict):
        return None
    windows = {}
    for name, key in (("five_hour", "fiveHour"), ("seven_day", "sevenDay")):
        each = usage.get(key)
        if isinstance(each, dict) and fraction(each.get("pct")) is not None:
            windows[name] = window(each["pct"] / 100, each.get("resetsAt"))
    scoped = []
    for each in usage.get("scoped") or []:
        if isinstance(each, dict) and isinstance(each.get("name"), str) and NAME_SHAPE.fullmatch(each["name"]) \
                and fraction(each.get("pct")) is not None:
            found = window(each["pct"] / 100, each.get("resetsAt"))
            found["name"] = each["name"]
            scoped.append(found)
    if not windows and not scoped:
        return None
    when = parse_time(row.get("usageFetchedAt") or row.get("lastGoodFetchedAt")) or now
    return reading(slot, "cswap", windows, scoped, now=when)


def file_name(record):
    return f"slot-{record['slot']}.{record['source']}.json"


def write(path, record):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="ascii")


# The slots as they stand.

def load(folder):
    """Every reading in a checkout of the branch pool, by slot."""
    found = {}
    for path in sorted(Path(folder).glob("slot-*.json")):
        if not FILE_NAME.match(path.name):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if well_formed(record, FILE_NAME.match(path.name)):
            found.setdefault(record["slot"], []).append(record)
    return found


def well_formed(record, name):
    """A reading as this file writes it, of the slot and kind its file's
    name says; anyone who may write the branch could put anything there,
    and one stray file must not stop the panel or the picker."""
    if not isinstance(record, dict) or record.get("version") != VERSION or not parse_time(record.get("at")):
        return False
    if (record.get("slot"), record.get("source")) != name.groups():
        return False
    windows, scoped = record.get("windows", {}), record.get("scoped", [])
    if not isinstance(windows, dict) or not isinstance(scoped, list):
        return False
    return all(isinstance(each, dict) for each in list(windows.values()) + scoped)


def current(records, now):
    """A slot's windows from its newest reading of each, a window past its
    reset counting as empty, and its per-model limits from the newest
    reading that has them."""
    windows, scoped = {}, []
    for record in sorted(records, key=lambda each: parse_time(each["at"])):
        for name, each in (record.get("windows") or {}).items():
            if name in WINDOWS and isinstance(each, dict) and fraction(each.get("utilization")) is not None:
                windows[name] = dict(each, at=record["at"], source=record["source"])
        if record.get("scoped"):
            scoped = [dict(each, at=record["at"]) for each in record["scoped"] if isinstance(each, dict)]
    for each in list(windows.values()) + scoped:
        resets = parse_time(each.get("resetsAt"))
        if resets is not None and resets <= now:
            each["utilization"] = 0.0
            each["reset"] = True
    return windows, scoped


def held_until(windows, scoped, threshold, now):
    """When the slot is free again: the latest reset of a window at or past
    the threshold, or None when nothing holds it. A full window with no
    reset known holds it for the length of a five-hour window from its
    reading, so a slot is tried again rather than held for good on a
    reading nobody may renew."""
    until = None
    for each in list(windows.values()) + scoped:
        used = fraction(each.get("utilization"))
        if used is not None and used >= threshold:
            resets = parse_time(each.get("resetsAt"))
            if resets is None:
                read = parse_time(each.get("at")) or now
                resets = read + UNKNOWN_HOLD
            if resets > now and (until is None or resets > until):
                until = resets
    return until


def pick(found, slots, threshold=THRESHOLD, now=None, spread=0):
    """(slot, None) for the slot with the most room, or (slot, until) for
    the one whose hold ends first when every slot is held. Workers of one
    run all read the pool before any of them writes it, so spread, each
    worker's place in the run, deals them the free slots in turn, the
    one with the most room to the first."""
    now = now or datetime.now(timezone.utc)
    if not slots:
        raise PoolError("the pool names no slot")
    free, held = [], []
    for order, slot in enumerate(slots):
        windows, scoped = current(found.get(slot, []), now)
        until = held_until(windows, scoped, threshold, now)
        room = tuple(fraction((windows.get(name) or {}).get("utilization")) or 0.0 for name in WINDOWS)
        # A slot no reading names comes after every slot one does: its
        # secret may be unset or revoked, which no reading would show.
        (held if until else free).append((until, (not windows, room), order, slot))
    if free:
        free.sort(key=lambda each: (each[1], each[2]))
        return free[spread % len(free)][3], None
    until, _, _, slot = min(held, key=lambda each: (each[0], each[2]))
    return slot, until


def parse_slots(text):
    """A list of slots as SYMPHONY_CLAUDE_ACCOUNTS holds it, in its order, each once."""
    slots = []
    for word in re.split(r"[,\s]+", text or ""):
        if word and word not in SLOTS:
            raise PoolError(f"a slot is a number from 1 to 8 or main, not {word[:20]!r}")
        if word and word not in slots:
            slots.append(word)
    return slots


def summary(found, threshold=THRESHOLD, now=None):
    """The slots as the control panel shows them, as JSON."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for slot in SLOTS:
        if slot not in found:
            continue
        windows, scoped = current(found[slot], now)
        until = held_until(windows, scoped, threshold, now)
        rows.append({"slot": slot, "windows": windows, "scoped": scoped,
                     "heldUntil": iso(until) if until else None,
                     "readAt": max(record["at"] for record in found[slot])})
    return {"version": VERSION, "at": iso(now), "threshold": threshold, "slots": rows}


# Which account a worker takes.

def assignments(path):
    """The file's assignments, checked: {worker: choice} and the default."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, "main"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise PoolError(f"{path} is not JSON ({error.msg}, line {error.lineno})") from None
    if not isinstance(data, dict) or set(data) - {"default", "workers"}:
        raise PoolError(f"{path} holds an object with default and workers, and nothing else")
    default = data.get("default", "main")
    workers = data.get("workers", {})
    if default not in CHOICES:
        raise PoolError(f"{path}: the default is main, auto or a slot from 1 to 8")
    if not isinstance(workers, dict) or not all(isinstance(name, str) and choice in CHOICES for name, choice in workers.items()):
        raise PoolError(f"{path}: each worker takes main, auto or a slot from 1 to 8")
    return workers, default


def branch_readings(folder, cwd=None):
    """The readings on origin's branch pool, read into the folder given,
    which stays empty where the branch is not there yet."""
    folder = Path(folder)
    fetched = subprocess.run(["git", "fetch", "--quiet", "--no-tags", "origin",
                              f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"], cwd=cwd, capture_output=True)
    if fetched.returncode != 0:
        return folder
    for name in git("ls-tree", "--name-only", f"refs/remotes/origin/{BRANCH}", cwd=cwd).splitlines():
        if FILE_NAME.match(name):
            shown = subprocess.run(["git", "show", f"refs/remotes/origin/{BRANCH}:{name}"], cwd=cwd, capture_output=True)
            if shown.returncode == 0:
                (folder / name).write_bytes(shown.stdout)
    return folder


def choose(worker, pool_text, path=ASSIGNMENTS, folder=None, now=None, spread=0):
    """(slot, how, until): the slot a worker's run takes, how it was
    chosen, and, for auto with every slot held, when the first is free."""
    workers, default = assignments(path)
    choice = workers.get(worker, default)
    how = "assigned" if worker in workers else "default"
    if choice != "auto":
        return choice, how, None
    slots = [slot for slot in parse_slots(pool_text) if slot != "main"]
    if not slots:
        return "main", "auto, with no slot in SYMPHONY_CLAUDE_ACCOUNTS", None
    if folder is None:
        with tempfile.TemporaryDirectory(prefix="pool-") as scratch:
            found = load(branch_readings(scratch))
    else:
        found = load(folder)
    slot, until = pick(found, slots, now=now, spread=spread)
    return slot, "auto", until


# Probing and publishing, in Actions.

def probe(slot, run=subprocess.run):
    """One word from Claude on the token in CLAUDE_CODE_OAUTH_TOKEN, for the
    rate-limit event its answer brings; the token stays in the environment
    claude reads it from."""
    try:
        done = run(list(PROBE), capture_output=True, text=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PoolError(f"claude could not be run for slot {slot} ({type(error).__name__})") from None
    record = observed(messages_in(done.stdout or ""), slot, "probe")
    if record is None:
        raise PoolError(f"claude answered for slot {slot} with no rate-limit reading (exit {done.returncode})")
    return record


def git(*args, cwd=None, stdin=None):
    done = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True)
    if done.returncode != 0:
        raise PoolError(f"git {args[0]}: {(done.stderr or '').strip().splitlines()[-1:] or 'failed'}")
    return done.stdout.strip()


def newest_by_name(paths):
    """One path per reading's name, the newest by its own time: jobs of
    one run that took the same slot each leave a reading of that name,
    and the last to finish says most."""
    chosen = {}
    for path in paths:
        name = Path(path).name
        if not FILE_NAME.match(name):
            raise PoolError(f"{name} is not a reading's name")
        try:
            at = parse_time(json.loads(Path(path).read_text(encoding="utf-8")).get("at"))
        except (OSError, ValueError, AttributeError):
            at = None
        at = at or datetime.min.replace(tzinfo=timezone.utc)
        if name not in chosen or at > chosen[name][0]:
            chosen[name] = (at, path)
    return {name: path for name, (_, path) in chosen.items()}


def publish(paths, cwd=None):
    """The readings onto the branch pool, beside the ones there: one commit
    with no parent, as the branches board and metrics are kept, pushed
    with a lease on the commit read, and tried again when another run
    pushed first."""
    entries = {}
    for name, path in newest_by_name(paths).items():
        entries[name] = git("hash-object", "-w", str(path), cwd=cwd)
    for _ in range(PUSH_ATTEMPTS):
        previous = ""
        if subprocess.run(["git", "fetch", "--quiet", "--no-tags", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"],
                          cwd=cwd, capture_output=True).returncode == 0:
            previous = git("rev-parse", f"refs/remotes/origin/{BRANCH}", cwd=cwd)
        tree = {}
        if previous:
            for line in git("ls-tree", previous, cwd=cwd).splitlines():
                meta, name = line.split("\t", 1)
                if FILE_NAME.match(name):
                    tree[name] = meta.split()[2]
        tree.update(entries)
        listing = "".join(f"100644 blob {blob}\t{name}\n" for name, blob in sorted(tree.items()))
        made = git("mktree", cwd=cwd, stdin=listing)
        commit = git("commit-tree", made, "-m", "Usage of the pool's slots", cwd=cwd)
        pushed = subprocess.run(["git", "push", "--quiet", f"--force-with-lease=refs/heads/{BRANCH}:{previous}",
                                 "origin", f"{commit}:refs/heads/{BRANCH}"], cwd=cwd, capture_output=True)
        if pushed.returncode == 0:
            return commit
        refused = (pushed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        if not any("stale info" in line or "fetch first" in line or "non-fast-forward" in line for line in refused):
            raise PoolError(f"the push to {BRANCH} was refused: {refused[-1] if refused else 'no reason given'}")
    raise PoolError(f"the branch {BRANCH} moved under every one of {PUSH_ATTEMPTS} pushes")


# The command line.

def out_path(out, record):
    """--out names a file when it ends in .json, else the folder the
    reading goes into under its own name, made if need be."""
    out = Path(out)
    return out if out.suffix == ".json" else out / file_name(record)


def build_parser():
    parser = argparse.ArgumentParser(prog="python3 tools/pool.py", description="The usage of the pool's Claude slots.")
    commands = parser.add_subparsers(dest="command", required=True)
    observe = commands.add_parser("observe", help="the last rate-limit reading of a run's execution file")
    observe.add_argument("--execution-file", required=True)
    observe.add_argument("--slot", required=True, choices=SLOTS)
    observe.add_argument("--source", default="run", choices=("run", "probe"), help="probe for a run that pool.yml started to read the slot")
    observe.add_argument("--out", required=True)
    probing = commands.add_parser("probe", help="one word from Claude, for a fresh reading")
    probing.add_argument("--slot", required=True, choices=SLOTS)
    probing.add_argument("--out", required=True)
    publishing = commands.add_parser("publish", help="readings onto the branch pool")
    publishing.add_argument("paths", nargs="+")
    showing = commands.add_parser("show", help="the slots as they stand, as JSON")
    showing.add_argument("folder")
    showing.add_argument("--threshold", type=float, default=THRESHOLD)
    picking = commands.add_parser("pick", help="the slot a run should take")
    picking.add_argument("folder")
    picking.add_argument("--slots", required=True)
    picking.add_argument("--threshold", type=float, default=THRESHOLD)
    choosing = commands.add_parser("choose", help="the slot a worker's run takes, as step outputs")
    choosing.add_argument("--worker", required=True)
    choosing.add_argument("--pool", default="")
    choosing.add_argument("--assignments", default=ASSIGNMENTS)
    choosing.add_argument("--spread", type=int, default=0, help="the worker's place in its run, from 0")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "observe":
            try:
                text = Path(args.execution_file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                print(f"pool: no execution file at {args.execution_file}; nothing observed")
                return 0
            record = observed(messages_in(text), args.slot, args.source)
            if record is None:
                print("pool: the run carried no rate-limit reading; nothing observed")
                return 0
            write(out_path(args.out, record), record)
        elif args.command == "probe":
            record = probe(args.slot)
            write(out_path(args.out, record), record)
        elif args.command == "publish":
            print(f"pool: written as {publish(args.paths)}")
        elif args.command == "show":
            print(json.dumps(summary(load(args.folder), args.threshold), indent=2, sort_keys=True))
        elif args.command == "pick":
            slot, until = pick(load(args.folder), parse_slots(args.slots), args.threshold)
            print(slot)
            if until:
                print(f"pool: every slot is held; slot {slot} is free first, at "
                      f"{iso(until)}", file=sys.stderr)
        elif args.command == "choose":
            slot, how, until = choose(args.worker, args.pool, args.assignments, spread=max(args.spread, 0))
            print(f"slot={slot}")
            if until:
                print("wait=true")
                print(f"until={iso(until)}")
            print(f"pool: {args.worker} runs on {'slot ' + slot if slot != 'main' else 'the main token'} ({how})"
                  + (", though every slot of the pool is held" if until else ""), file=sys.stderr)
    except PoolError as error:
        print(f"pool: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
