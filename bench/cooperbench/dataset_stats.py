"""What CooperBench's dataset is made of, and how far it moved since the paper.

The paper's numbers about the dataset (Table 3's repositories, features
and pairs; Table 4's feature complexity; "77.3% of tasks have conflicting
ground-truth solutions", Sec 2.1) were taken on the dataset of CooperBench
commit 59bbf65, the paper era, while coordbench runs whatever dataset the
CooperBench checkout holds (63b9d44 by default). This module reads that
dataset with the standard library only and sets every count beside the
paper's, so a reader sees which side a difference comes from:

- composition: task sets, features and pairs per repository, from the
  task directories and subsets/all.json, beside Table 3 (whose #PRs
  column is inconsistent inside the paper: it sums to 32, prints 34, and
  App A.2 says 52 task sets; 63b9d44 has 30);
- feature complexity: per gold feature.patch, lines added and removed,
  files, hunks and an approximation of the functions touched, beside
  Table 4. CooperBench's own count (generation/prompt.py,
  _extract_patch_info) uses unidiff, a dependency coordbench refuses, so
  the same counts are taken from the diff text here. Table 4's "Avg.
  Functions" equals the mean hunk count of this dataset to one decimal
  in most repositories, so it is set beside the hunks, with the distinct
  enclosing-function lines of the hunk headers given apart as an
  approximation of functions proper;
- gold conflicts: CooperBench's own scripts/check_gold_conflicts.py
  writes dataset/gold_conflict_report.json (76.5% at 63b9d44, 499 of
  652), which is read as it is, beside the paper's 77.3% (504 of 652).
  That script applies with --ignore-whitespace and an older test-file
  filter, so it is not exactly what the scorer's merge sees;
- the dataset version: which features and tasks changed between 59bbf65
  and the checkout, from git (a feature is amended when its feature.md,
  feature.patch or tests.patch changed in content; a pure rename, as
  typst became typst_task, is not an amendment; runner.sh, Dockerfile,
  setup.sh, run_tests.sh and combined.patch are the task's own files);
- the spec audit (dataset/.spec_audit_verdicts.json: OK, SPEC when
  feature.md was amended, TEST when tests.patch was) and the
  gradeability checks (dataset/gradeable_report_docker.json and the
  reports bench/cooperbench/survey.sh keeps per task).

report.py imports these to split its rates by dataset version and audit
class; run alone it prints the dataset's own tables:

    python3 bench/cooperbench/dataset_stats.py [--cooperbench-dir ~/cooperbench]
        [--survey-dir ~/cooperbench-survey] [--json out.json]
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

PAPER_COMMIT = "59bbf65"
PAPER_GOLD_CONFLICT_RATE = 77.3
PAPER_TOTALS = {"repositories": 12, "task_sets": 34, "features": 199, "pairs": 652}

# Table 3 (#PRs, features, task pairs) and Table 4 (average lines,
# functions and files per feature), by CooperBench's repository directory.
TABLE_3 = {
    "dspy_task": (4, 23, 55), "llama_index_task": (3, 16, 39), "pillow_task": (3, 15, 30),
    "pallets_click_task": (3, 27, 115), "pallets_jinja_task": (3, 30, 135),
    "huggingface_datasets_task": (3, 13, 26), "dottxt_ai_outlines_task": (3, 22, 79),
    "openai_tiktoken_task": (1, 10, 45), "samuelcolvin_dirty_equals_task": (1, 9, 36),
    "react_hook_form_task": (2, 11, 25), "go_chi_task": (3, 13, 22), "typst_task": (3, 10, 45),
}
TABLE_4 = {
    "dspy_task": (70.9, 5.6, 1.3), "llama_index_task": (16.8, 1.8, 1.0), "pillow_task": (38.1, 2.7, 1.0),
    "pallets_click_task": (53.9, 5.4, 1.6), "pallets_jinja_task": (67.7, 6.2, 1.0),
    "huggingface_datasets_task": (15.3, 2.3, 1.0), "dottxt_ai_outlines_task": (44.7, 4.1, 1.1),
    "openai_tiktoken_task": (46.4, 4.6, 1.0), "samuelcolvin_dirty_equals_task": (71.0, 4.0, 2.0),
    "react_hook_form_task": (49.8, 4.6, 2.3), "go_chi_task": (80.2, 5.7, 2.8), "typst_task": (58.4, 1.7, 1.1),
}
TABLE_4_OVERALL = (52.3, 4.4, 1.4)

FEATURE_FILES = ("feature.md", "feature.patch", "tests.patch")
TASK_FILES = ("runner.sh", "Dockerfile", "setup.sh", "run_tests.sh", "combined.patch")
# The paper-era name of one repository directory, renamed after 59bbf65.
REPO_ALIASES = {"typst": "typst_task"}
GIT_TIMEOUT_SECONDS = 60
FEATURE_DIR = re.compile(r"^feature(\d+)$")
TASK_DIR = re.compile(r"^task(\d+)$")
PAIR_DIR = re.compile(r"^f\d+(?:_f\d+)+$")
FUNCTION_NAME = re.compile(r"\b(?:def|func|fn|function|class|struct|impl|interface|type)\s+([A-Za-z_][\w.]*)")


# Keys. A feature is "<repo>/task<id>|feature<n>", as the spec audit names
# it; a task is "<repo>/task<id>"; a pair is report.py's
# "<repo>/<id>/f<a>_f<b>", the last three parts of its log directory.

def feature_key(repo, task_id, feature):
    return f"{canonical_repo(repo)}/task{task_id}|feature{feature}"


def task_key(repo, task_id):
    return f"{canonical_repo(repo)}/task{task_id}"


def canonical_repo(repo):
    return REPO_ALIASES.get(repo, repo)


def parse_pair_key(key):
    """(repo, task id, features) of a report pair key, or None when the key
    is not of that shape."""
    parts = key.split("/")
    if len(parts) != 3 or not PAIR_DIR.match(parts[2]):
        return None
    task_id = parts[1][4:] if parts[1].startswith("task") else parts[1]
    features = [int(f) for f in parts[2][1:].split("_f")]
    return canonical_repo(parts[0]), task_id, features


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# Composition (Table 3).

def task_dirs(dataset_dir):
    """Every task directory as (repo, task id, path), sorted."""
    found = []
    for repo_dir in sorted(p for p in Path(dataset_dir).iterdir() if p.is_dir()):
        for task_dir in sorted(p for p in repo_dir.iterdir() if p.is_dir() and TASK_DIR.match(p.name)):
            found.append((repo_dir.name, TASK_DIR.match(task_dir.name).group(1), task_dir))
    return found


def feature_numbers(task_dir):
    return sorted(int(FEATURE_DIR.match(p.name).group(1)) for p in task_dir.iterdir()
                  if p.is_dir() and FEATURE_DIR.match(p.name))


def all_pairs(dataset_dir):
    """The benchmark's pairs as report keys: subsets/all.json when it is
    there, every two features of each task otherwise."""
    listed = read_json(Path(dataset_dir) / "subsets" / "all.json")
    keys = set()
    if isinstance(listed, dict):
        for task in listed.get("tasks") or []:
            for pair in task.get("pairs") or []:
                keys.add(pair_key(task["repo"], task["task_id"], pair))
        return keys
    for repo, task_id, path in task_dirs(dataset_dir):
        numbers = feature_numbers(path)
        keys.update(pair_key(repo, task_id, (a, b)) for i, a in enumerate(numbers) for b in numbers[i + 1:])
    return keys


def pair_key(repo, task_id, features):
    return f"{canonical_repo(repo)}/{task_id}/" + "_".join(f"f{f}" for f in features)


def composition(dataset_dir):
    """Per repository: task sets, features and pairs, and the totals."""
    pairs = all_pairs(dataset_dir)
    rows = {}
    for repo, task_id, path in task_dirs(dataset_dir):
        row = rows.setdefault(repo, {"task_sets": 0, "features": 0, "pairs": 0})
        row["task_sets"] += 1
        row["features"] += len(feature_numbers(path))
    for key in pairs:
        repo = key.split("/")[0]
        rows.setdefault(repo, {"task_sets": 0, "features": 0, "pairs": 0})["pairs"] += 1
    totals = {name: sum(row[name] for row in rows.values()) for name in ("task_sets", "features", "pairs")}
    return {"repositories": rows, "totals": {"repositories": len(rows), **totals}}


# Feature complexity (Table 4).

def patch_counts(text):
    """Lines added and removed, files, hunks and the distinct enclosing
    functions of one unified diff."""
    added = removed = hunks = 0
    files, functions, current, in_hunk = set(), set(), None, False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            current, in_hunk = line.split(" b/", 1)[-1], False
            files.add(current)
        elif line.startswith("@@"):
            hunks, in_hunk = hunks + 1, True
            context = line.split("@@", 2)[-1].strip()
            if context:
                functions.add((current, function_name(context)))
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed, "lines": added + removed, "files": len(files),
            "hunks": hunks, "functions": len(functions)}


def function_name(context):
    """The name a hunk header's context line defines, or the line itself."""
    match = FUNCTION_NAME.search(context)
    return match.group(1) if match else context


def feature_complexity(dataset_dir):
    """Per feature its gold patch's counts, keyed by feature_key."""
    found = {}
    for repo, task_id, path in task_dirs(dataset_dir):
        for number in feature_numbers(path):
            patch = path / f"feature{number}" / "feature.patch"
            if patch.is_file():
                found[feature_key(repo, task_id, number)] = patch_counts(patch.read_text(errors="replace"))
    return found


def complexity_table(counts):
    """Averages per repository and overall, over features."""
    by_repo = {}
    for key, row in counts.items():
        by_repo.setdefault(key.split("/")[0], []).append(row)
    table = {repo: averages(rows) for repo, rows in sorted(by_repo.items())}
    return {"repositories": table, "overall": averages(list(counts.values()))}


def averages(rows):
    if not rows:
        return {"features": 0}
    names = ("lines", "added", "removed", "files", "hunks", "functions")
    return {"features": len(rows), **{name: sum(r[name] for r in rows) / len(rows) for name in names}}


# Gold conflicts.

def gold_conflicts(dataset_dir):
    """CooperBench's gold_conflict_report.json: its summary, the rate per
    repository and every pair's verdict by report key; None when absent."""
    found = read_json(Path(dataset_dir) / "gold_conflict_report.json")
    if not isinstance(found, dict):
        return None
    verdicts, by_repo = {}, {}
    for row in found.get("all_results") or []:
        key = pair_key(row["repo"], row["task_id"], (row["f1"], row["f2"]))
        verdicts[key] = None if row.get("error") else bool(row.get("has_conflict"))
        tally = by_repo.setdefault(canonical_repo(row["repo"]), [0, 0])
        if verdicts[key] is not None:
            tally[0] += verdicts[key]
            tally[1] += 1
    return {"summary": found.get("summary") or {}, "pairs": verdicts,
            "repositories": {repo: {"conflicts": k, "total": n} for repo, (k, n) in sorted(by_repo.items())}}


# The dataset version: what changed since the paper's commit.

def git_lines(directory, *arguments):
    try:
        done = subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.splitlines() if done.returncode == 0 else None


def dataset_changes(cooperbench_dir, old=PAPER_COMMIT, new="HEAD"):
    """The features and tasks whose files changed between two commits of
    the CooperBench checkout, from `git diff --name-status -M`; None when
    the directory is no checkout or lacks the old commit."""
    lines = git_lines(cooperbench_dir, "diff", "--name-status", "-M", old, new, "--", "dataset")
    if lines is None:
        return None
    changes = {"old": old, "new": new, "features": {}, "tasks": {}, "renamed_only": 0}
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status, path = fields[0], fields[-1]
        if status == "R100":
            changes["renamed_only"] += 1
            continue
        record_change(changes, path.split("/"), status)
    return changes


def record_change(changes, parts, status):
    """File one changed dataset path under its feature or its task."""
    if len(parts) == 5 and TASK_DIR.match(parts[2]) and FEATURE_DIR.match(parts[3]) and parts[4] in FEATURE_FILES:
        key = feature_key(parts[1], TASK_DIR.match(parts[2]).group(1), FEATURE_DIR.match(parts[3]).group(1))
        changes["features"].setdefault(key, []).append(f"{parts[4]} {status}")
    elif len(parts) == 4 and TASK_DIR.match(parts[2]) and parts[3] in TASK_FILES:
        key = task_key(parts[1], TASK_DIR.match(parts[2]).group(1))
        changes["tasks"].setdefault(key, []).append(f"{parts[3]} {status}")


# The spec audit and the gradeability checks.

def spec_audit(dataset_dir):
    """Each feature's audit verdict (OK, SPEC or TEST); None when absent."""
    found = read_json(Path(dataset_dir) / ".spec_audit_verdicts.json")
    if not isinstance(found, dict):
        return None
    return {key: value[0] if isinstance(value, list) and value else value for key, value in found.items()}


def gradeability(dataset_dir, survey_dir=None):
    """Each checked feature's verdict (OK when its tests fail on the base
    and pass with the gold patch), from the dataset's last report and every
    report survey.sh kept; None when no report was found."""
    reports = [Path(dataset_dir) / "gradeable_report_docker.json"]
    if survey_dir and Path(survey_dir).is_dir():
        reports += sorted(Path(survey_dir).glob("*.json"))
    verdicts = {}
    for path in reports:
        for row in read_json(path) or []:
            if isinstance(row, dict) and row.get("task") and row.get("feature"):
                task = row["task"].split("/")
                if len(task) == 2 and TASK_DIR.match(task[1]):
                    key = feature_key(task[0], TASK_DIR.match(task[1]).group(1), row["feature"][7:])
                    verdicts[key] = row.get("verdict")
    return verdicts or None


# What each pair of a run is, by these facts.

def pair_facts(key, changes=None, audit=None, gradeable=None, gold=None):
    """The dataset facts of one report pair key: amended (a feature changed
    since the paper; None when unknown), task_changed, audit class (both
    OK, any SPEC, any TEST), gradeable (False when a feature failed its
    check), and whether its gold patches conflict."""
    parsed = parse_pair_key(key)
    if parsed is None:
        return {"amended": None, "task_changed": None, "audit": None, "gradeable": None, "gold_conflict": None}
    repo, task_id, features = parsed
    keys = [feature_key(repo, task_id, f) for f in features]
    return {"amended": None if changes is None else any(k in changes["features"] for k in keys),
            "task_changed": None if changes is None else task_key(repo, task_id) in changes["tasks"],
            "audit": audit_class(keys, audit), "gradeable": gradeable_of(keys, gradeable),
            "gold_conflict": None if gold is None else gold["pairs"].get(pair_key(repo, task_id, features))}


def audit_class(keys, audit):
    if audit is None or any(k not in audit for k in keys):
        return None
    verdicts = {audit[k] for k in keys}
    if "TEST" in verdicts:
        return "any TEST"
    return "any SPEC" if "SPEC" in verdicts else "both OK"


def gradeable_of(keys, gradeable):
    if gradeable is None:
        return None
    seen = [gradeable[k] for k in keys if k in gradeable]
    if any(v != "OK" for v in seen):
        return False
    return True if len(seen) == len(keys) else None


class Dataset:
    """Every dataset fact a report needs, read once; each part is None
    when its file or the git history is not there."""

    def __init__(self, cooperbench_dir, survey_dir=None):
        self.cooperbench_dir = Path(cooperbench_dir)
        self.dir = self.cooperbench_dir / "dataset"
        present = self.dir.is_dir()
        self.pairs = all_pairs(self.dir) if present else set()
        self.gold = gold_conflicts(self.dir) if present else None
        self.audit = spec_audit(self.dir) if present else None
        self.gradeable = gradeability(self.dir, survey_dir) if present else None
        self.changes = dataset_changes(self.cooperbench_dir) if present else None

    def facts(self, key):
        return pair_facts(key, self.changes, self.audit, self.gradeable, self.gold)


# The dataset's own tables.

def render(cooperbench_dir, survey_dir=None):
    dataset_dir = Path(cooperbench_dir) / "dataset"
    if not dataset_dir.is_dir():
        return f"# CooperBench's dataset\n\nNo dataset under {dataset_dir}.\n"
    lines = ["# CooperBench's dataset", ""]
    lines += composition_lines(composition(dataset_dir))
    lines += complexity_lines(complexity_table(feature_complexity(dataset_dir)))
    lines += gold_lines(gold_conflicts(dataset_dir))
    lines += change_lines(dataset_changes(cooperbench_dir), spec_audit(dataset_dir))
    lines += gradeability_lines(gradeability(dataset_dir, survey_dir))
    return "\n".join(lines) + "\n"


def composition_lines(found):
    lines = ["## Composition (Table 3)", "",
             "| Repository | Task sets | Paper #PRs | Features | Paper | Pairs | Paper |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for repo, row in sorted(found["repositories"].items()):
        prs, features, pairs = TABLE_3.get(repo, ("-", "-", "-"))
        lines.append(f"| {repo} | {row['task_sets']} | {prs} | {row['features']} | {features} "
                     f"| {row['pairs']} | {pairs} |")
    totals = found["totals"]
    lines += [f"| Total ({totals['repositories']} repositories) | {totals['task_sets']} | 34 "
              f"| {totals['features']} | 199 | {totals['pairs']} | 652 |", "",
              "The paper's #PRs column sums to 32 against its printed total of 34, and App A.2 says "
              "52 task sets; the task sets here are the dataset's task directories.", ""]
    return lines


def complexity_lines(table):
    lines = ["## Feature complexity (Table 4)", "",
             "Counted from each gold feature.patch: lines are added plus removed, hunks are the @@ "
             "headers, and enclosing functions are the distinct function lines git writes after the "
             "hunk headers, per file. The paper's \"Avg. Functions\" column equals the mean hunk count "
             "here to one decimal in most repositories and overall, so the paper's functions are "
             "probably hunks; the enclosing functions are given beside them as an approximation of "
             "functions proper (it misses a hunk that adds a function at top level).", "",
             "| Repository | Features | Lines | Paper | Hunks | Paper functions | Enclosing functions "
             "| Files | Paper |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for repo, row in table["repositories"].items():
        lines.append(complexity_row(repo, row, TABLE_4.get(repo)))
    lines += [complexity_row("Overall", table["overall"], TABLE_4_OVERALL), ""]
    return lines


def complexity_row(name, row, paper):
    paper = paper or ("-", "-", "-")
    if not row.get("features"):
        return f"| {name} | 0 | - | {paper[0]} | - | {paper[1]} | - | - | {paper[2]} |"
    return (f"| {name} | {row['features']} | {row['lines']:.1f} | {paper[0]} | {row['hunks']:.1f} | {paper[1]} "
            f"| {row['functions']:.1f} | {row['files']:.1f} | {paper[2]} |")


def gold_lines(gold):
    lines = ["## Conflicting gold solutions", ""]
    if gold is None:
        return lines + ["dataset/gold_conflict_report.json is not there.", ""]
    summary = gold["summary"]
    lines += [f"CooperBench's scripts/check_gold_conflicts.py: {summary.get('conflicts')} of "
              f"{summary.get('total_pairs')} pairs ({summary.get('conflict_rate')}) on this dataset; the paper "
              f"says {PAPER_GOLD_CONFLICT_RATE}% (Sec 2.1), on the paper-era dataset. The script applies with "
              "--ignore-whitespace and an older test-file filter, so it is not exactly the scorer's merge.", "",
              "| Repository | Conflicting | Pairs | Share |", "| --- | --- | --- | --- |"]
    for repo, row in gold["repositories"].items():
        share = 100 * row["conflicts"] / row["total"] if row["total"] else 0.0
        lines.append(f"| {repo} | {row['conflicts']} | {row['total']} | {share:.1f}% |")
    return lines + [""]


def change_lines(changes, audit):
    lines = ["## The dataset since the paper", ""]
    if changes is None:
        lines.append(f"No git history with commit {PAPER_COMMIT} here, so what changed since the paper is unknown.")
    else:
        lines.append(f"Between {changes['old']} and {changes['new']}: {len(changes['features'])} features amended "
                     f"(feature.md, feature.patch or tests.patch changed), {len(changes['tasks'])} tasks with "
                     f"changed task files (runner.sh, Dockerfile, setup.sh, run_tests.sh, combined.patch), "
                     f"{changes['renamed_only']} files renamed without a change (typst to typst_task).")
    if audit is not None:
        counts = {verdict: list(audit.values()).count(verdict) for verdict in sorted(set(audit.values()))}
        lines += ["", "Spec audit (dataset/.spec_audit_verdicts.json): "
                  + ", ".join(f"{verdict} {count}" for verdict, count in counts.items()) + "."]
    return lines + [""]


def gradeability_lines(verdicts):
    lines = ["## Gradeability", ""]
    if verdicts is None:
        return lines + ["No gradeability report found.", ""]
    failing = sorted(key for key, verdict in verdicts.items() if verdict != "OK")
    lines.append(f"{len(verdicts)} features checked, {len(failing)} not gradeable here"
                 + (": " + ", ".join(failing) if failing else "."))
    return lines + [""]


def summary(cooperbench_dir, survey_dir=None):
    """Everything above as one JSON value, for --json."""
    dataset_dir = Path(cooperbench_dir) / "dataset"
    if not dataset_dir.is_dir():
        return {"dataset_dir": str(dataset_dir), "present": False}
    return {"dataset_dir": str(dataset_dir), "present": True, "composition": composition(dataset_dir),
            "complexity": complexity_table(feature_complexity(dataset_dir)),
            "gold_conflicts": summary_of_gold(gold_conflicts(dataset_dir)),
            "changes": dataset_changes(cooperbench_dir), "spec_audit": spec_audit(dataset_dir),
            "gradeability": gradeability(dataset_dir, survey_dir)}


def summary_of_gold(gold):
    return None if gold is None else {"summary": gold["summary"], "repositories": gold["repositories"]}


def default_cooperbench_dir():
    return Path(os.environ.get("COOPERBENCH_DIR") or Path.home() / "cooperbench")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cooperbench-dir", type=Path, default=default_cooperbench_dir())
    parser.add_argument("--survey-dir", type=Path, default=Path.home() / "cooperbench-survey")
    parser.add_argument("--json", type=Path, help="also write every count as JSON to this file")
    args = parser.parse_args(argv)
    print(render(args.cooperbench_dir, args.survey_dir), end="")
    if args.json:
        args.json.write_text(json.dumps(summary(args.cooperbench_dir, args.survey_dir), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
