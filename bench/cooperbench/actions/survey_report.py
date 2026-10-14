"""The survey's table from the gradeability reports the survey jobs left:
one row per task, whether every feature graded on the runner (its tests
fail on the base commit and pass with the gold patch), the image's size,
the time the check took and the disk left, so PLAN.md's pair list can be
written from it. Reads every <logs>/<tag>-survey-<repo>-<task>/*.summary.json
and its .json beside it, and, given the plan's survey matrix (a JSON file
holding {"include": [{"repo": ..., "task": ...}, ...]}), a row for every
planned task whose job left no summary, since a job the runner cut short
before the check wrote one would otherwise vanish from the table the pair
list is fixed from; prints Markdown.
Usage: survey_report.py <logs> [tag] [plan.json]."""

import json
import sys
from pathlib import Path

NO_SUMMARY = "no artifact (the job ended before the check wrote its summary)"


def gradeable_features(report):
    """What CooperBench's check_gradeable.py writes: a JSON list, one row
    per feature, each with the keys task, feature, verdict and note, where
    the verdict is "OK" for a feature whose tests fail on the base commit
    and pass with the gold patch, and the failure's name otherwise."""
    if not isinstance(report, list):
        return None
    verdicts = [row.get("verdict") == "OK" for row in report if isinstance(row, dict)]
    return verdicts or None


def gigabytes(value):
    return f"{value / 1e9:.2f}" if value else "-"


def planned_tasks(path):
    """The (repo, task) pairs of the plan's survey matrix, in its order."""
    if not path:
        return []
    matrix = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(t["repo"], t["task"]) for t in matrix.get("include", []) if isinstance(t, dict)]


def rows(logs, tag=None, planned=()):
    """One row per survey directory, <tag>-survey-<repo>-<task>; with no
    tag, every directory with -survey- in its name; then one per planned
    task with no summary."""
    found = []
    pattern = f"{tag}-survey-*/*.summary.json" if tag else "*-survey-*/*.summary.json"
    for summary_path in sorted(Path(logs).glob(pattern)):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        report_path = summary_path.with_name(summary_path.name.replace(".summary.json", ".json"))
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        verdicts = gradeable_features(report)
        # check_gradeable.py exits 0 whatever it found, so the verdicts
        # decide; a missing report means the check stopped before writing.
        if verdicts is None:
            graded = "no report (the check failed before writing one)"
        elif all(verdicts):
            graded = "all"
        else:
            graded = "none" if not any(verdicts) else "not all"
        found.append({"repo": summary["repo"], "task": summary["task"], "graded": graded,
                      "features": f"{sum(verdicts)}/{len(verdicts)}" if verdicts else "-",
                      "image": gigabytes(summary.get("image_bytes")), "seconds": summary.get("seconds"),
                      "free": gigabytes(summary.get("disk_free_bytes"))})
    seen = {(str(row["repo"]), str(row["task"])) for row in found}
    for repo, task in planned:
        if (str(repo), str(task)) not in seen:
            found.append({"repo": repo, "task": task, "graded": NO_SUMMARY, "features": "-", "image": "-",
                          "seconds": "-", "free": "-"})
    return found


def render(found):
    lines = ["# Gradeability survey on GitHub's runners", "",
             "| Task | Graded here | Features | Image on disk (GB) | Check (s) | Disk free after (GB) |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in found:
        lines.append(f"| {row['repo']} {row['task']} | {row['graded']} | {row['features']} | {row['image']} | "
                     f"{row['seconds']} | {row['free']} |")
    graded = sum(1 for row in found if row["graded"] == "all")
    lines += ["", f"{graded} of {len(found)} tasks graded whole on the runner."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    planned = planned_tasks(argv[2]) if len(argv) > 2 else []
    print(render(rows(argv[0] if argv else ".", argv[1] if len(argv) > 1 else None, planned)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
