"""Copy one sample's runs out of a tag's logs into logs of their own, so
report.py, summary.py and bundle.py read the sample alone: a tag that grew
from a pilot holds pairs outside the stratified order (the pilot's), and
the three tools read every pair under a tag. The sample is the queue's
items that carry a sample record (queue.py add --sample); those that have
finished (done or failed) are copied as they are, session transcripts
included, since the report counts tool calls and messages from them, but
for the archives of earlier attempts, which are large. The others are
named and left out, so a sample still running reads as incomplete and
not as smaller, and so are those whose run was a harness error (the
queue's harness_error, or the run's audit.json), which measured the
session route rather than the arm. Each arm gets its config.json and a summary.json rebuilt
from the copied pairs alone, by the runner's own refresh_summary, so the
report's summary column counts the sample and not the tag, and OUT gets
the sample's lines of the queue under the queue's own name, where
bundle.py looks for it. OUT must be new or empty, so nothing an earlier
pass copied is read as part of this one.
    python3 subset.py QUEUE LOGS OUT
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
LEFT_BEHIND = ("previous-*.tar.gz",)
FINISHED = ("done", "failed")


def sampled(queue):
    """The queue's items that belong to the sample."""
    items = [json.loads(line) for line in Path(queue).read_text().splitlines() if line.strip()]
    return [item for item in items if item.get("sample")]


def copy_runs(items, logs, out):
    """Each finished item's run directory under out, at its path under
    logs; what was left out is returned by reason, not raised."""
    logs = plain_path(logs)
    left = {"unfinished": [], "harness_error": [], "no_run_dir": [], "outside_logs": []}
    for item in items:
        if item.get("status") not in FINISHED:
            left["unfinished"].append(item["id"])
            continue
        if harness_error(item):
            left["harness_error"].append(item["id"])
            continue
        source = plain_path(item.get("run_dir") or "")
        if not item.get("run_dir") or not source.is_dir():
            left["no_run_dir"].append(item["id"])
            continue
        if logs not in source.parents:
            left["outside_logs"].append(item["id"])
            continue
        target = Path(out) / source.relative_to(logs)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*LEFT_BEHIND))
    return left


def harness_error(item):
    """Whether the item's run measured the harness rather than the arm, by
    the queue (lane.py finish) or by its audit.json (audit.py run by hand):
    such a run is no result of its arm, so the sample counts it as not
    finished, to be run again."""
    if item.get("harness_error"):
        return True
    try:
        audit = json.loads((Path(item.get("run_dir") or "") / "audit.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(audit, dict) and bool(audit.get("harness_error"))


def plain_path(path):
    """An absolute path with . and .. taken out but symlinks kept, so an
    arm directory linked in from another disk still lies under LOGS."""
    return Path(os.path.abspath(path))


def write_queue(items, queue, out):
    """The sample's lines of the queue, under the queue's own name."""
    lines = "".join(json.dumps(item) + "\n" for item in items)
    (Path(out) / Path(queue).name).write_text(lines)


def refresh_arm(arm_dir, source_arm, runner):
    """config.json as the tag has it, summary.json from the copied pairs."""
    if (source_arm / "config.json").is_file():
        shutil.copy2(source_arm / "config.json", arm_dir / "config.json")
    plans = sorted(arm_dir.rglob("plan.json"))
    if plans:
        plan = json.loads(plans[0].read_text())
        plan["run_dir"] = str(plans[0].parent)
        runner.refresh_summary(plan)


def load_runner():
    """The runner, imported where it lives; this directory's queue.py
    would otherwise stand in for the standard library's queue."""
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != HERE]
    sys.path.insert(0, str(ROOT / "coordbench"))
    import run  # noqa: E402
    return run


def refuse(out, logs):
    """Why OUT may not be used, or None. OUT that is LOGS or lies in it
    would have the copy delete or mix with the runs it copies; OUT that
    holds anything already would keep an earlier pass's copy of a run
    since requeued, or hold LOGS or the queue itself, so it must be new
    or empty."""
    out, logs = Path(out).resolve(), Path(logs).resolve()
    if out == logs or logs in out.parents:
        return f"OUT {out} is LOGS or inside it; give a directory beside LOGS"
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        return f"OUT {out} is not a new or empty directory; remove it or name another"
    return None


def report(copied, total, left, out):
    words = {"unfinished": "not finished", "harness_error": "a harness error, to be run again",
             "no_run_dir": "no run directory", "outside_logs": "run directory outside LOGS"}
    parts = [f"copied {copied} of the sample's {total} runs to {out}"]
    parts += [f"{words[reason]}: {', '.join(ids)}" for reason, ids in left.items() if ids]
    return "; ".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("queue")
    parser.add_argument("logs", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    problem = refuse(args.out, args.logs)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    items = sampled(args.queue)
    args.out.mkdir(parents=True, exist_ok=True)
    left = copy_runs(items, args.logs, args.out)
    copied = len(items) - sum(len(ids) for ids in left.values())
    write_queue(items, args.queue, args.out)
    print(report(copied, len(items), left, args.out))
    if not copied:
        return 1
    runner = load_runner()
    for arm_dir in sorted(path for path in args.out.iterdir() if path.is_dir()):
        refresh_arm(arm_dir, args.logs / arm_dir.name, runner)
    return 0


if __name__ == "__main__":
    sys.exit(main())
