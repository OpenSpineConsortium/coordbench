"""Copy the bundled runs of the curse-1 experiment back into a logs
directory, so one report can span the runs an earlier container made and
the runs made since. A bundle keeps every file the pass rates, the
latencies, the tokens and the audit are read from, but no transcript, so
the figures drawn from transcripts (tool calls, the communication
overhead) cover only the runs whose container is still the one at hand.

Three rules keep the copy true to the queue:

- A run whose bundled audit.json names a harness error is skipped: it
  measured the harness, the queue put it back, and its path in the logs
  belongs to the run made again. A run without an audit.json that reads
  is skipped too, since nothing says it was clean.
- Where two bundles hold a run at the same path, only the one that
  started last counts, and it is skipped in turn if it was a harness
  error. The logs keep one directory per path, so a later attempt
  replaced an earlier one there, and the queue's item is the later
  attempt: the pilot's click 2068 f5_f11 under coop, sdlc, coop-git and
  team was cut by a restart and run again in the step of 8, and both
  bundles hold a copy. Every bundled run recorded its start in UTC, so
  the start times sort as written.
- A run directory the logs already hold is never replaced, since it may
  be a run made on the container at hand. So restore every bundle in one
  call: restored one bundle at a time, a path keeps whichever attempt
  came first. An arm's summary.json is never copied, since the runner
  rebuilds it from its runs, and an arm directory without a config.json
  takes the first bundle's.

Each run is copied into LOGS/.restore-partial first and then renamed
into place, so a copy cut short (a full disk) leaves its files outside
every arm's directory, where report.py and the run totals never look;
the next restore clears it.

    python3 restore-bundles.py RESULTS LOGS [--bundles NAMES] [--dry-run]
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

BUNDLES = "curse-1-pilot,curse-1-step-8,curse-1-checkpoint"
STAGING = ".restore-partial"


def read_json(path):
    """The JSON object a file holds, or {} when the file is missing, does
    not parse or holds something other than an object."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def started_at(result):
    started = read_json(result).get("started_at")
    return started if isinstance(started, str) else ""


def why_left_out(run):
    """Why a bundled run is not copied, or None when it is clean."""
    audit = read_json(run / "audit.json")
    if not audit:
        return "no audit.json that reads"
    return audit.get("harness_error") or None


def bundled_runs(results, bundles):
    """Each bundled run's directory by its path under raw/, the latest
    start at each path standing for it; with the paths whose latest
    start was a harness error or is unaudited apart, each with why."""
    latest = {}
    for bundle in bundles:
        raw = results / bundle / "raw"
        for result in sorted(raw.glob("*/*/*/*/*/result.json")):
            relative = result.parent.relative_to(raw)
            started = started_at(result)
            if relative not in latest or started > latest[relative][0]:
                latest[relative] = (started, bundle, result.parent)
    chosen, spoiled = {}, []
    for relative, (_, bundle, run) in latest.items():
        why = why_left_out(run)
        if why:
            spoiled.append((f"{bundle}/{relative}", why))
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


def copy_run(run, target, staging):
    """Copy into the staging directory, then rename into place."""
    partial = staging / "run"
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(run, partial)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial.rename(target)


def restore(results, logs, bundles, dry_run):
    runs, spoiled = bundled_runs(results, bundles)
    staging = logs / STAGING
    if not dry_run:
        shutil.rmtree(staging, ignore_errors=True)
    copied, kept = [], []
    for relative, run in sorted(runs.items()):
        target = logs / relative
        if target.exists():
            kept.append(str(relative))
            continue
        copied.append(str(relative))
        if not dry_run:
            copy_run(run, target, staging)
    if not dry_run:
        shutil.rmtree(staging, ignore_errors=True)
    configs = []
    for arm, config in arm_configs(results, bundles).items():
        target = logs / arm / "config.json"
        if not target.exists():
            configs.append(arm)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(config, target)
    return {"copied": copied, "kept": kept, "spoiled": spoiled, "configs": configs}


def bundle_names(text, results):
    """The bundles named, and those of them RESULTS holds no raw/ for."""
    names = [name.strip() for name in text.split(",") if name.strip()]
    return names, [name for name in names if not (results / name / "raw").is_dir()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", type=Path, help="bench/cooperbench/results")
    parser.add_argument("logs", type=Path, help="the logs directory, ~/cooperbench/logs")
    parser.add_argument("--bundles", default=BUNDLES,
                        help=f"bundle directories under RESULTS, comma separated (default: {BUNDLES})")
    parser.add_argument("--dry-run", action="store_true", help="say what would be copied and copy nothing")
    args = parser.parse_args(argv)
    bundles, missing = bundle_names(args.bundles, args.results)
    if missing or not bundles:
        parser.error(f"no raw/ under {args.results} for: {', '.join(missing) or 'any bundle'}")
    done = restore(args.results, args.logs, bundles, args.dry_run)
    verb = "would copy" if args.dry_run else "copied"
    print(f"bundles: {', '.join(bundles)}")
    print(f"{verb} {len(done['copied'])} runs; left {len(done['kept'])} the logs already hold; "
          f"left out {len(done['spoiled'])} harness errors or unaudited runs; "
          f"config.json for {len(done['configs'])} arms")
    for name in done["kept"]:
        print(f"  already in the logs: {name}")
    for name, why in done["spoiled"]:
        print(f"  not copied ({why}): {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
