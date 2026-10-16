"""Copy the bundled runs of the curse-1 experiment back into a logs
directory, so one report can span the runs an earlier container made and
the runs made since. A bundle keeps every file the pass rates, the
latencies, the tokens and the audit are read from, but no transcript, so
the figures drawn from transcripts (tool calls, the communication
overhead) cover only the runs whose container is still the one at hand.

Two rules keep the copy true to the queue:

- A run whose bundled audit.json names a harness error is skipped: it
  measured the harness, the queue put it back, and its path in the logs
  belongs to the run made again.
- Where two bundles hold a run at the same path, only the one that
  started last counts, and it is skipped in turn if it was a harness
  error. The logs keep one directory per path, so a later attempt
  replaced an earlier one there, and the queue's item is the later
  attempt: the pilot's click 2068 f5_f11 under coop, sdlc, coop-git and
  team was cut by a restart and run again in the step of 8, and both
  bundles hold a copy.

A run directory the logs already hold is left as it is, since the logs'
own runs are never older than a bundle's; so is an arm's summary.json,
which the runner rebuilds from its runs. An arm directory the logs lack
takes the bundle's config.json.

    python3 restore-bundles.py RESULTS LOGS [--bundles curse-1-pilot,curse-1-step-8] [--dry-run]
"""

import argparse
import json
import shutil
from pathlib import Path

BUNDLES = "curse-1-pilot,curse-1-step-8"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def bundled_runs(results, bundles):
    """Each bundled run's directory by its path under raw/, the latest
    start at each path standing for it; with the paths whose latest
    start was a harness error apart."""
    latest = {}
    for bundle in bundles:
        raw = results / bundle / "raw"
        for result in sorted(raw.glob("*/*/*/*/*/result.json")):
            relative = result.parent.relative_to(raw)
            started = read_json(result).get("started_at") or ""
            if relative not in latest or started > latest[relative][0]:
                latest[relative] = (started, bundle, result.parent)
    chosen, spoiled = {}, []
    for relative, (_, bundle, run) in latest.items():
        if read_json(run / "audit.json").get("harness_error"):
            spoiled.append(f"{bundle}/{relative}")
        else:
            chosen[relative] = run
    return chosen, sorted(spoiled)


def arm_configs(results, bundles):
    """The first bundle's config.json for each arm directory."""
    configs = {}
    for bundle in bundles:
        for config in sorted((results / bundle / "raw").glob("*/config.json")):
            configs.setdefault(config.parent.name, config)
    return configs


def restore(results, logs, bundles, dry_run):
    runs, spoiled = bundled_runs(results, bundles)
    copied, kept = [], []
    for relative, run in sorted(runs.items()):
        target = logs / relative
        if target.exists():
            kept.append(str(relative))
            continue
        copied.append(str(relative))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(run, target)
    configs = []
    for arm, config in arm_configs(results, bundles).items():
        target = logs / arm / "config.json"
        if not target.exists():
            configs.append(arm)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(config, target)
    return {"copied": copied, "kept": kept, "spoiled": spoiled, "configs": configs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", type=Path, help="bench/cooperbench/results")
    parser.add_argument("logs", type=Path, help="the logs directory, ~/cooperbench/logs")
    parser.add_argument("--bundles", default=BUNDLES, help="bundle directories under RESULTS, comma separated")
    parser.add_argument("--dry-run", action="store_true", help="say what would be copied and copy nothing")
    args = parser.parse_args(argv)
    done = restore(args.results, args.logs, args.bundles.split(","), args.dry_run)
    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {len(done['copied'])} runs; left {len(done['kept'])} the logs already hold; "
          f"skipped {len(done['spoiled'])} harness errors; config.json for {len(done['configs'])} arms")
    for name in done["kept"]:
        print(f"  already in the logs: {name}")
    for name in done["spoiled"]:
        print(f"  harness error, not copied: {name}")


if __name__ == "__main__":
    main()
