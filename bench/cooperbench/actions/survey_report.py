"""The survey's table from the gradeability reports the survey jobs left:
one row per task, whether every feature graded on the runner (its tests
fail on the base commit and pass with the gold patch), the image's size,
the time the check took and the disk left, so PLAN.md's pair list can be
written from it. Reads every <logs>/<tag>-survey-<repo>-<task>/*.summary.json
and its .json beside it; prints Markdown."""

import json
import sys
from pathlib import Path


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


def rows(logs):
    found = []
    for summary_path in sorted(Path(logs).glob("*-survey-*/*.summary.json")):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        report_path = summary_path.with_name(summary_path.name.replace(".summary.json", ".json"))
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        verdicts = gradeable_features(report)
        graded = "all" if summary["status"] == 0 else ("none" if verdicts and not any(verdicts) else "not all")
        if verdicts is None and summary["status"] != 0:
            graded = "no report (the check failed before writing one)"
        found.append({"repo": summary["repo"], "task": summary["task"], "graded": graded,
                      "features": f"{sum(verdicts)}/{len(verdicts)}" if verdicts else "-",
                      "image": gigabytes(summary.get("image_bytes")), "seconds": summary.get("seconds"),
                      "free": gigabytes(summary.get("disk_free_bytes"))})
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
    print(render(rows(argv[0] if argv else ".")), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
