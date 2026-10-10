"""dataset_stats.py reads CooperBench's dataset with the standard library
and sets it beside the paper's tables, and report.py splits its rates by
what it finds, so these pin the counts on a small dataset shaped like
CooperBench's: a gold patch's lines, hunks, files and functions; the
composition with and without subsets/all.json; the gold-conflict report;
what changed between two commits of a git checkout (a pure rename, as
typst became typst_task, is no amendment); and each pair's audit class
and gradeability."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "cooperbench"))

import dataset_stats  # noqa: E402

PATCH = """diff --git a/src/click/termui.py b/src/click/termui.py
index 1..2 100644
--- a/src/click/termui.py
+++ b/src/click/termui.py
@@ -10,7 +10,8 @@ def edit(text):
     keep
-    old
+    new
+    newer
@@ -40,3 +41,3 @@ class Editor:
-    a
+    b
diff --git a/src/click/core.py b/src/click/core.py
--- a/src/click/core.py
+++ b/src/click/core.py
@@ -1,2 +1,3 @@
+import os
 x = 1
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_dataset(root, with_subset=True):
    """Two tasks of one repository and one of another, each feature with
    a gold patch; the audit, the gold conflicts and a gradeability report."""
    dataset = root / "dataset"
    for repo, task, features in (("pallets_click_task", "2068", 3), ("pallets_click_task", "2800", 2),
                                 ("typst_task", "6554", 2)):
        for feature in range(1, features + 1):
            base = dataset / repo / f"task{task}" / f"feature{feature}"
            write(base / "feature.md", f"feature {feature}\n")
            write(base / "feature.patch", PATCH)
            write(base / "tests.patch", "tests\n")
        write(dataset / repo / f"task{task}" / "runner.sh", "run\n")
    if with_subset:
        subset = {"tasks": [{"repo": "pallets_click_task", "task_id": "2068", "pairs": [[1, 2], [1, 3], [2, 3]]},
                            {"repo": "pallets_click_task", "task_id": "2800", "pairs": [[1, 2]]},
                            {"repo": "typst_task", "task_id": "6554", "pairs": [[1, 2]]}]}
        write(dataset / "subsets" / "all.json", json.dumps(subset))
    audit = {"pallets_click_task/task2068|feature1": ["OK", ""], "pallets_click_task/task2068|feature2": ["SPEC", ""],
             "pallets_click_task/task2068|feature3": ["TEST", ""], "pallets_click_task/task2800|feature1": ["OK", ""],
             "pallets_click_task/task2800|feature2": ["OK", ""]}
    write(dataset / ".spec_audit_verdicts.json", json.dumps(audit))
    gold = {"summary": {"total_pairs": 5, "conflicts": 3, "clean_merges": 2, "errors": 0, "conflict_rate": "60.0%"},
            "all_results": [
                {"repo": "pallets_click_task", "task_id": 2068, "f1": 1, "f2": 2, "has_conflict": True, "error": None},
                {"repo": "pallets_click_task", "task_id": 2068, "f1": 1, "f2": 3, "has_conflict": False, "error": None},
                {"repo": "pallets_click_task", "task_id": 2068, "f1": 2, "f2": 3, "has_conflict": True, "error": None},
                {"repo": "pallets_click_task", "task_id": 2800, "f1": 1, "f2": 2, "has_conflict": False, "error": None},
                {"repo": "typst", "task_id": 6554, "f1": 1, "f2": 2, "has_conflict": True, "error": None}]}
    write(dataset / "gold_conflict_report.json", json.dumps(gold))
    gradeable = [{"task": "pallets_click_task/task2068", "feature": "feature1", "verdict": "OK"},
                 {"task": "pallets_click_task/task2068", "feature": "feature2", "verdict": "OK"},
                 {"task": "pallets_click_task/task2068", "feature": "feature3", "verdict": "BASE_PASSES"}]
    write(dataset / "gradeable_report_docker.json", json.dumps(gradeable))
    return dataset


def git(directory, *arguments):
    subprocess.run(["git", "-C", str(directory), *arguments], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(directory)})


class PatchCountTest(unittest.TestCase):
    def test_lines_files_hunks_and_functions(self):
        counts = dataset_stats.patch_counts(PATCH)
        self.assertEqual(counts["added"], 4)
        self.assertEqual(counts["removed"], 2)
        self.assertEqual(counts["lines"], 6)
        self.assertEqual(counts["files"], 2)
        self.assertEqual(counts["hunks"], 3)
        # Two hunk headers name a function or class; the top-level hunk does not.
        self.assertEqual(counts["functions"], 2)

    def test_the_function_name_of_a_hunk_context(self):
        self.assertEqual(dataset_stats.function_name("def edit(text):"), "edit")
        go_method = "func (mx *Mux) Use(m ...func())"
        self.assertEqual(dataset_stats.function_name(go_method), go_method)
        self.assertEqual(dataset_stats.function_name("pub fn layout(&self)"), "layout")


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_composition_reads_the_subset_or_counts_every_two_features(self):
        dataset = make_dataset(self.root)
        found = dataset_stats.composition(dataset)
        self.assertEqual(found["totals"], {"repositories": 2, "task_sets": 3, "features": 7, "pairs": 5})
        self.assertEqual(found["repositories"]["pallets_click_task"]["pairs"], 4)
        (dataset / "subsets" / "all.json").unlink()
        self.assertEqual(dataset_stats.composition(dataset)["totals"]["pairs"], 5)

    def test_complexity_averages_over_features(self):
        dataset = make_dataset(self.root)
        table = dataset_stats.complexity_table(dataset_stats.feature_complexity(dataset))
        self.assertEqual(table["overall"]["features"], 7)
        self.assertEqual(table["overall"]["lines"], 6)
        self.assertEqual(table["overall"]["hunks"], 3)

    def test_the_gold_report_is_read_with_the_typst_alias(self):
        gold = dataset_stats.gold_conflicts(make_dataset(self.root))
        self.assertEqual(gold["summary"]["conflict_rate"], "60.0%")
        self.assertTrue(gold["pairs"]["typst_task/6554/f1_f2"])
        self.assertEqual(gold["repositories"]["pallets_click_task"], {"conflicts": 2, "total": 4})

    def test_pair_facts_name_the_audit_class_and_gradeability(self):
        dataset = make_dataset(self.root)
        audit, gradeable = dataset_stats.spec_audit(dataset), dataset_stats.gradeability(dataset)
        facts = lambda key: dataset_stats.pair_facts(key, None, audit, gradeable)
        self.assertEqual(facts("pallets_click_task/2068/f1_f2")["audit"], "any SPEC")
        self.assertEqual(facts("pallets_click_task/2068/f2_f3")["audit"], "any TEST")
        self.assertEqual(facts("pallets_click_task/2800/f1_f2")["audit"], "both OK")
        self.assertIsNone(facts("typst_task/6554/f1_f2")["audit"])
        self.assertTrue(facts("pallets_click_task/2068/f1_f2")["gradeable"])
        self.assertFalse(facts("pallets_click_task/2068/f1_f3")["gradeable"])
        self.assertIsNone(facts("pallets_click_task/2800/f1_f2")["gradeable"])
        self.assertIsNone(facts("not/a pair")["audit"])

    def test_the_survey_reports_add_to_the_gradeability(self):
        dataset = make_dataset(self.root)
        survey = self.root / "survey"
        write(survey / "pallets_click_task_2800.json",
              json.dumps([{"task": "pallets_click_task/task2800", "feature": "feature2", "verdict": "GOLD_FAILS"}]))
        verdicts = dataset_stats.gradeability(dataset, survey)
        self.assertEqual(verdicts["pallets_click_task/task2800|feature2"], "GOLD_FAILS")

    def test_changes_since_an_old_commit(self):
        make_dataset(self.root)
        old_typst = self.root / "dataset" / "typst"
        (self.root / "dataset" / "typst_task").rename(old_typst)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "old")
        old = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
        old_typst.rename(self.root / "dataset" / "typst_task")
        write(self.root / "dataset" / "pallets_click_task" / "task2068" / "feature2" / "feature.md", "amended\n")
        write(self.root / "dataset" / "pallets_click_task" / "task2800" / "runner.sh", "changed\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "new")
        changes = dataset_stats.dataset_changes(self.root, old=old)
        self.assertEqual(list(changes["features"]), ["pallets_click_task/task2068|feature2"])
        self.assertEqual(list(changes["tasks"]), ["pallets_click_task/task2800"])
        self.assertGreater(changes["renamed_only"], 0)
        facts = dataset_stats.pair_facts("pallets_click_task/2068/f1_f2", changes)
        self.assertTrue(facts["amended"])
        self.assertFalse(dataset_stats.pair_facts("typst_task/6554/f1_f2", changes)["amended"])
        self.assertTrue(dataset_stats.pair_facts("pallets_click_task/2800/f1_f2", changes)["task_changed"])

    def test_no_git_history_leaves_the_changes_unknown(self):
        make_dataset(self.root)
        self.assertIsNone(dataset_stats.dataset_changes(self.root))
        self.assertIsNone(dataset_stats.pair_facts("pallets_click_task/2068/f1_f2")["amended"])

    def test_render_sets_the_counts_beside_the_paper(self):
        make_dataset(self.root)
        text = dataset_stats.render(self.root)
        self.assertIn("| pallets_click_task | 2 | 3 | 5 | 27 | 4 | 115 |", text)
        self.assertIn("3 of 5 pairs (60.0%)", text)
        self.assertIn("77.3%", text)
        self.assertIn("No git history", text)
        self.assertIn("1 not gradeable here: pallets_click_task/task2068|feature3", text)

    def test_a_missing_dataset_is_said_so(self):
        self.assertIn("No dataset under", dataset_stats.render(self.root))
        self.assertEqual(dataset_stats.summary(self.root)["present"], False)

    def test_the_command_writes_json(self):
        make_dataset(self.root)
        out = self.root / "stats.json"
        with open(self.root / "stdout.txt", "w") as sink:
            stdout, sys.stdout = sys.stdout, sink
            try:
                dataset_stats.main(["--cooperbench-dir", str(self.root), "--json", str(out)])
            finally:
                sys.stdout = stdout
        self.assertEqual(json.loads(out.read_text())["composition"]["totals"]["pairs"], 5)

    def test_parse_pair_key(self):
        self.assertEqual(dataset_stats.parse_pair_key("typst/6554/f1_f10"), ("typst_task", "6554", [1, 10]))
        self.assertEqual(dataset_stats.parse_pair_key("r/task7/f1_f2_f3"), ("r", "7", [1, 2, 3]))
        self.assertIsNone(dataset_stats.parse_pair_key("r/7/solo"))


if __name__ == "__main__":
    unittest.main()
