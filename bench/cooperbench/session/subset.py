"""Copy one sample's runs out of a tag's logs into logs of their own, so
report.py, summary.py and bundle.py read the sample alone: a tag that grew
from a pilot holds pairs outside the stratified order (the pilot's), and
the three tools read every pair under a tag. The sample is the queue's
items that carry a sample record (queue.py add --sample) and have a run
directory; each is copied as it is but for the session transcripts and
the archives of earlier attempts, which are large, and each arm gets its
config.json and a summary.json rebuilt from the copied pairs alone, by
the runner's own refresh_summary, so the report's summary column counts
the sample and not the tag.
    python3 subset.py QUEUE LOGS OUT
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
LEFT_BEHIND = ("previous-*.tar.gz", "*_session.jsonl")


def sampled(queue):
    """The queue's items that belong to the sample and ran."""
    items = [json.loads(line) for line in Path(queue).read_text().splitlines() if line.strip()]
    return [item for item in items if item.get("sample") and item.get("run_dir")]


def copy_runs(items, logs, out):
    """Each item's run directory under out, at its path under logs; the
    ids whose directory is not there are returned, not raised, since a
    pending item has none yet."""
    missing = []
    for item in items:
        source = Path(item["run_dir"])
        if not source.is_dir():
            missing.append(item["id"])
            continue
        target = Path(out) / source.relative_to(logs)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*LEFT_BEHIND))
    return missing


def refresh_arm(arm_dir, source_arm, runner):
    """config.json as the tag has it, summary.json from the copied pairs."""
    if (source_arm / "config.json").is_file():
        shutil.copy2(source_arm / "config.json", arm_dir / "config.json")
    plans = sorted(arm_dir.rglob("plan.json"))
    if plans and runner is not None:
        plan = json.loads(plans[0].read_text())
        plan["run_dir"] = str(plans[0].parent)
        runner.refresh_summary(plan)


def load_runner():
    sys.path.insert(0, str(ROOT / "coordbench"))
    import run  # noqa: E402  (the runner, imported where it lives)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("queue")
    parser.add_argument("logs", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    items = sampled(args.queue)
    missing = copy_runs(items, args.logs, args.out)
    runner = load_runner()
    for arm_dir in sorted(path for path in args.out.iterdir() if path.is_dir()):
        refresh_arm(arm_dir, args.logs / arm_dir.name, runner)
    print(f"copied {len(items) - len(missing)} of the sample's {len(items)} runs to {args.out}"
          + (f"; no run directory yet: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
