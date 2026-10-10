"""The cascade puts the paper's verdict beside CooperBench 63b9d44's, so
these pin each piece to the code it reproduces: 59bbf65's line scan, pass
regex, filter, resolver prompt and splice, and 63b9d44's helpers as the
cascade drives them. Most tests run the cascade over a fake sandbox that
answers as CooperBench's helpers do; the channel to CooperBench's
virtualenv is tested against a stub package; and when CooperBench's
checkout is here (COOPERBENCH_DIR or ~/cooperbench, with its .venv) its
real helpers and its real test_merged run on a local git repository
through a backend that maps the container's paths to a directory.

Only Docker can test the rest: CooperBench's DockerBackend making and
removing the container, the task images' own runner.sh and suites, the
600 s command limit, and the removal of a container left behind when the
channel is killed. Nothing here calls a model."""

import base64
import contextlib
import http.server
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py shares its name with the standard library's tracer;
# a process that happened to load that one first gets ours here instead.
if getattr(sys.modules.get("trace"), "__file__", None) != str(COORDBENCH / "trace.py"):
    sys.modules.pop("trace", None)
import cascade  # noqa: E402

BASE = "b" * 40
HEAD = "a" * 40
FEATURES = [1, 6]
PASS = {"passed": True, "output": "===== 3 passed in 0.10s =====", "exit_code": 0, "tests_passed": 3,
        "tests_failed": 0}
FAIL = {"passed": False, "output": "===== 1 failed, 2 passed in 0.10s =====", "exit_code": 1, "tests_passed": 2,
        "tests_failed": 1}
CARGO_PASS = {"passed": True, "output": "test result: ok. 5 passed; 0 failed; 0 ignored", "exit_code": 0,
              "tests_passed": 5, "tests_failed": 0}
PATCH1 = ("diff --git a/src/one.py b/src/one.py\n--- a/src/one.py\n+++ b/src/one.py\n@@ -1 +1 @@\n-a\n+A\n"
          "diff --git a/tests/test_one.py b/tests/test_one.py\n--- a/tests/test_one.py\n+++ b/tests/test_one.py\n"
          "@@ -1 +1 @@\n-t\n+T\n")
PATCH2 = "diff --git a/src/two.py b/src/two.py\n--- a/src/two.py\n+++ b/src/two.py\n@@ -1 +1 @@\n-b\n+B\n"
INPUTS = {"features": FEATURES, "patches": [PATCH1, PATCH2], "tests": ["tests one\n", "tests two\n"]}
APPLIED = {"agent1": "applied", "agent2": "applied"}
CONFLICTED_DIFF = textwrap.dedent("""\
    diff --cc src/lib.py
    index 1111111,2222222..0000000
    --- a/src/lib.py
    +++ b/src/lib.py
    @@@ -1,3 -1,3 +1,9 @@@
      def base():
    ++<<<<<<< HEAD
     +    return 2
     +    # two
    ++=======
    +     return 1
    ++>>>>>>> agent1
    ++<<<<<<< HEAD
     +X = 2
    ++=======
    + X = 1
    ++>>>>>>> agent1
    """)


def merged_record(status, first=None, second=None, both=None, agent1="applied"):
    """An eval.json record as run.py writes it from test_merged's answer."""
    first, second = first or placeholder(), second or placeholder()
    if both is None:
        both = first["passed"] and second["passed"]
    return {"repo": "r", "task_id": 1, "features": FEATURES, "setting": "coop",
            "apply_status": {"agent1": agent1, "agent2": "applied"},
            "merge": {"status": status, "strategy": "naive", "diff": ""},
            "feature1": first, "feature2": second, "both_passed": both, "error": None}


def placeholder():
    return {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "test_output": ""}


def clean_feature(run):
    """A feature's record on test_merged's clean path, which renames output."""
    return {"feature_id": 1, "passed": run["passed"], "exit_code": run["exit_code"],
            "tests_passed": run["tests_passed"], "tests_failed": run["tests_failed"], "test_output": run["output"]}


class FakeSandbox:
    """CooperBench's helpers as a script: the test sets each answer, every
    call is recorded, and exec reads and writes a dict of files."""

    def __init__(self, naive=None, union=None, tests=None, files=None, apply_status=None, broken=None):
        self.naive = naive or {"conflict": False, "diff": "clean diff\n", "output": ""}
        self.union = union or {"diff": "union diff\n", "output": "", "error": None}
        self.tests = tests or {}
        self.files = dict(files or {})
        self.apply_status = apply_status or dict(APPLIED)
        self.broken = broken
        self.calls, self.patches, self.commands, self.staging = [], {}, [], ""

    def check(self, name):
        self.calls.append(name)
        if name == self.broken:
            raise cascade.SandboxError(f"{name}: it broke")

    def filter_patch(self, content):
        self.check("filter_patch")
        sections = content.split("diff --git")
        return "".join("diff --git" + s for s in sections[1:] if not s.startswith(" a/tests/"))

    def write_patch(self, name, content):
        self.check("write_patch")
        self.patches[name] = content

    def setup_branches(self):
        self.check("setup_branches")
        return {"base_sha": BASE, "apply_status": dict(self.apply_status), "error": None, "output": ""}

    def merge_naive(self, base_sha):
        self.check("merge_naive")
        return dict(self.naive)

    def merge_union(self, base_sha):
        self.check("merge_union")
        return dict(self.union)

    def run_tests(self, tests_patch, feature_patch, base_sha):
        self.check("run_tests")
        self.calls.append(("run_tests", tests_patch, feature_patch))
        return dict(self.tests.get((tests_patch, feature_patch), FAIL))

    def exec(self, *args):
        self.check("exec")
        self.commands.append(args)
        if args[0] == "cat":
            if args[1] in self.files:
                return {"returncode": 0, "output": self.files[args[1]]}
            return {"returncode": 1, "output": f"cat: {args[1]}: No such file or directory"}
        return self.shell(args[-1])

    def shell(self, command):
        if "git rev-parse agent2" in command:
            return {"returncode": 0, "output": HEAD + "\n"}
        if command.startswith(": > "):
            self.staging = ""
        elif command.startswith("printf %s "):
            self.staging += command.split()[2]
        elif command.startswith("base64 -d "):
            self.files[shlex.split(command)[-1]] = base64.b64decode(self.staging).decode("utf-8")
        return {"returncode": 0, "output": ""}

    def ran(self, feature_patch):
        return [call[1] for call in self.calls if isinstance(call, tuple) and call[2] == feature_patch]

    def shell_commands(self):
        return [args[-1] for args in self.commands if args[0] == "bash"]


def alone_runs(agent1=(PASS, FAIL), agent2=(FAIL, PASS)):
    return {("tests1.patch", "patch1.patch"): agent1[0], ("tests2.patch", "patch1.patch"): agent1[1],
            ("tests1.patch", "patch2.patch"): agent2[0], ("tests2.patch", "patch2.patch"): agent2[1]}


def cascade_over(sandbox, evaluation, resolver=None):
    plan = {"cooperbench_dir": "/nonexistent", "features": FEATURES}
    with mock.patch.object(cascade, "cooperbench_commit", return_value="63b9d44" + "0" * 33):
        block = cascade.new_block(plan, evaluation)
    artifacts = {}
    cascade.fill(block, artifacts, sandbox, INPUTS, evaluation, resolver)
    return block, artifacts


class TheLegacyLineScan(unittest.TestCase):
    """59bbf65's _analyze_diff_content counts sections and the lines inside
    them, skipping the ======= separators, over a combined diff's prefixes."""

    def test_counts_sections_and_lines_as_59bbf65_did(self):
        details = cascade.conflict_details(CONFLICTED_DIFF)
        self.assertEqual(details, {"conflict_sections": 2, "conflict_lines": 5, "avg_lines_per_conflict": 2.5})

    def test_an_empty_diff_has_nothing(self):
        self.assertEqual(cascade.conflict_details(""),
                         {"conflict_sections": 0, "conflict_lines": 0, "avg_lines_per_conflict": 0})

    def test_score_is_twenty_a_section_and_two_a_line(self):
        self.assertEqual(cascade.conflict_score({"conflict_sections": 2, "conflict_lines": 5}), 50)

    def test_severity_thresholds_are_strict(self):
        expected = {0: "NONE", 1: "LOW", 20: "LOW", 21: "MEDIUM", 50: "MEDIUM", 51: "HIGH", 100: "HIGH",
                    101: "CRITICAL"}
        for score, level in expected.items():
            self.assertEqual(cascade.severity(score), level, score)


class ThePassRules(unittest.TestCase):
    def test_the_regex_reads_pytest_and_go_as_59bbf65_did(self):
        self.assertTrue(cascade.legacy_passed("===== 68 passed in 1.20s ====="))
        self.assertTrue(cascade.legacy_passed("ok  \tgithub.com/go-chi/chi\t0.1s"))
        self.assertFalse(cascade.legacy_passed("1 failed, 67 passed"))
        self.assertFalse(cascade.legacy_passed(""))

    def test_the_regex_fails_every_cargo_run_on_its_zero_failed(self):
        self.assertFalse(cascade.legacy_passed(CARGO_PASS["output"]))

    def test_a_record_takes_either_name_of_the_output(self):
        from_run = cascade.test_record(PASS)
        from_clean_path = cascade.test_record(clean_feature(PASS))
        self.assertEqual(from_run, from_clean_path)
        self.assertEqual(from_run["test_output"], PASS["output"])
        self.assertTrue(from_run["passed_regex"])


class TheLegacyFilter(unittest.TestCase):
    """59bbf65 applied only a patch's code files; 63b9d44 drops test files."""

    def patch(self, *paths):
        return "".join(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-x\n+y\n" for p in paths)

    def test_drops_tests_docs_and_dependency_files(self):
        patch = self.patch("src/app.py", "tests/test_app.py", "README.md", "pyproject.toml", "docs/usage.rst",
                           "pkg/metrics_test.go")
        self.assertEqual(cascade.legacy_dropped(patch),
                         ["README.md", "docs/usage.rst", "pkg/metrics_test.go", "pyproject.toml",
                          "tests/test_app.py"])

    def test_its_java_patterns_never_match_since_paths_are_lowered(self):
        self.assertEqual(cascade.legacy_category("src/FooTest.java"), "code")

    def test_a_new_file_is_sorted_by_its_header(self):
        new_file = "diff --git a/NOTES.txt b/NOTES.txt\nnew file mode 100644\n--- /dev/null\n+++ b/NOTES.txt\n"
        self.assertEqual(cascade.legacy_dropped(new_file), ["NOTES.txt"])

    def test_the_comparison_lists_what_each_filter_dropped(self):
        raw = self.patch("src/app.py", "tests/test_app.py", "CHANGELOG.md")
        kept = self.patch("src/app.py", "CHANGELOG.md")
        compared = cascade.filter_comparison([raw, ""], [kept, ""])
        self.assertEqual(compared["agent1"], {"63b9d44": ["tests/test_app.py"],
                                              "59bbf65": ["CHANGELOG.md", "tests/test_app.py"]})
        self.assertEqual(compared["agent2"], {"63b9d44": [], "59bbf65": []})


CONFLICTED_FILE = "".join(f"line {n}\n" for n in range(1, 9)) + (
    "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> agent1\n") + "".join(f"tail {n}\n" for n in range(1, 9))


class TheResolverSplice(unittest.TestCase):
    """59bbf65's llm_merge.py: five lines of context, the splice from the
    last region back, and the missing newline its splice leaves."""

    def test_strips_a_markdown_fence_and_blank_edges(self):
        self.assertEqual(cascade.strip_markdown_blocks("\n```python\n    x = 1\n```\n\n"), "    x = 1")
        self.assertEqual(cascade.strip_markdown_blocks("a\n\n"), "a")

    def test_a_region_carries_five_lines_either_side(self):
        (region,) = cascade.conflict_regions(CONFLICTED_FILE)
        self.assertEqual((region["conflict_start"], region["conflict_end"]), (8, 12))
        self.assertEqual((region["context_start"], region["context_end"]), (3, 18))
        self.assertTrue(region["context_string"].startswith("line 4\n"))
        self.assertTrue(region["context_string"].endswith("tail 5\n"))

    def test_neighbours_cut_each_others_context_even_into_the_region(self):
        """Two regions two lines apart: each window stops where the other's
        five lines begin, which in 59bbf65 cuts into the regions themselves."""
        text = "a\n<<<<<<< HEAD\n1\n=======\n2\n>>>>>>> x\nb\nc\n<<<<<<< HEAD\n3\n=======\n4\n>>>>>>> x\nd\n"
        first, second = cascade.conflict_regions(text)
        self.assertEqual((first["context_start"], first["context_end"]), (0, 3))
        self.assertEqual((second["context_start"], second["context_end"]), (11, 14))
        self.assertEqual(first["context_string"], "a\n<<<<<<< HEAD\n1\n")

    def test_the_last_resolved_line_joins_the_next_as_in_59bbf65(self):
        text, total, resolved, problems = cascade.apply_resolutions(
            CONFLICTED_FILE, lambda context: context.replace("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> agent1\n",
                                                             "both\n").rstrip("\n"))
        self.assertEqual((total, resolved, problems), (1, 1, []))
        self.assertIn("both\n", text)
        self.assertIn("tail 5tail 6\n", text)
        self.assertNotIn("<<<<<<<", text)

    def test_a_region_that_fails_stays_as_it_was(self):
        def broken(context):
            raise ConnectionError("no server")
        text, total, resolved, problems = cascade.apply_resolutions(CONFLICTED_FILE, broken)
        self.assertEqual((text, total, resolved), (CONFLICTED_FILE, 1, 0))
        self.assertEqual(problems, ["ConnectionError: no server"])

    def test_the_file_is_read_in_text_mode(self):
        text, total, _, _ = cascade.apply_resolutions(CONFLICTED_FILE.replace("\n", "\r\n"), lambda c: "x")
        self.assertEqual(total, 1)
        self.assertNotIn("\r", text)

    def test_a_file_without_markers_is_left_alone(self):
        self.assertEqual(cascade.apply_resolutions("a\nb\n", lambda c: "x"), ("a\nb\n", 0, 0, []))


class TheCascadeOverAFakeSandbox(unittest.TestCase):
    def test_a_clean_merge_takes_test_merged_results_and_routes_naive(self):
        evaluation = merged_record("clean", clean_feature(PASS), clean_feature(PASS))
        sandbox = FakeSandbox(tests=alone_runs())
        block, artifacts = cascade_over(sandbox, evaluation)
        self.assertEqual(block["naive"]["tests_from"], "test_merged")
        self.assertEqual(sandbox.ran("naive_diff.patch"), [])
        self.assertEqual((block["naive"]["f1"], block["naive"]["f2"]), (True, True))
        self.assertEqual((block["route"], block["verdict_paper_era"], block["verdict_63b9d44"]),
                         ("naive", True, True))
        self.assertTrue(block["verdict_63b9d44_rerun"])
        self.assertFalse(block["union"]["ran"])
        self.assertEqual(block["llm"], {"ran": False, "needed": False, "reason": "not needed: the naive merge was clean"})
        self.assertFalse(block["lower_bound"])
        self.assertIs(block["has_naive_merge_conflict"], False)
        self.assertEqual((block["naive"]["conflict_sections"], block["naive"]["severity"]), (0, "NONE"))
        self.assertEqual(artifacts, {cascade.NAIVE_DIFF: "clean diff\n"})
        self.assertEqual(block["deciding_artifact"],
                         {"verdict_63b9d44": cascade.NAIVE_DIFF, "verdict_paper_era": cascade.NAIVE_DIFF})

    def test_each_agent_alone_runs_against_both_suites(self):
        sandbox = FakeSandbox(tests=alone_runs())
        block, _ = cascade_over(sandbox, merged_record("clean", clean_feature(PASS), clean_feature(PASS)))
        alone = block["agent_alone"]
        self.assertEqual((alone["agent1"]["f1"], alone["agent1"]["f2"]), (True, False))
        self.assertEqual((alone["agent2"]["f1"], alone["agent2"]["f2"]), (False, True))
        self.assertEqual((alone["agent1"]["feature"], alone["agent2"]["patch_file"]), (1, "agent6.patch"))
        self.assertEqual(sandbox.ran("patch1.patch") + sandbox.ran("patch2.patch"),
                         ["tests1.patch", "tests2.patch", "tests1.patch", "tests2.patch"])

    def test_the_patches_are_filtered_and_written_as_test_merged_writes_them(self):
        sandbox = FakeSandbox(tests=alone_runs())
        block, _ = cascade_over(sandbox, merged_record("clean", clean_feature(PASS), clean_feature(PASS)))
        self.assertEqual(sorted(sandbox.patches), ["patch1.patch", "patch2.patch", "tests1.patch", "tests2.patch"])
        self.assertNotIn("tests/test_one.py", sandbox.patches["patch1.patch"])
        self.assertEqual(sandbox.patches["tests2.patch"], "tests two\n")
        self.assertEqual(block["dropped_by_filter"]["agent1"]["63b9d44"], ["tests/test_one.py"])
        self.assertTrue(block["filters_agree"])

    def conflicted(self, union=None, tests=None, files=None):
        runs = {**alone_runs(), **(tests or {})}
        files = {"/patches/naive_conflict.diff": CONFLICTED_DIFF, **(files or {})}
        return FakeSandbox(naive={"conflict": True, "diff": "", "output": ""}, union=union, tests=runs, files=files)

    def test_union_passes_where_the_lead_alone_fails(self):
        sandbox = self.conflicted(tests={("tests1.patch", "union_diff.patch"): PASS,
                                         ("tests2.patch", "union_diff.patch"): PASS})
        block, artifacts = cascade_over(sandbox, merged_record("conflicts"))
        self.assertEqual((block["route"], block["verdict_paper_era"], block["verdict_63b9d44"]),
                         ("union", True, False))
        self.assertIs(block["verdict_63b9d44_rerun"], False)
        self.assertEqual((block["naive"]["f1"], block["naive"]["tests_from"]), (None, None))
        self.assertEqual((block["naive"]["conflict_sections"], block["naive"]["conflict_lines"]), (2, 5))
        self.assertEqual((block["naive"]["conflict_score"], block["naive"]["severity"]), (50, "MEDIUM"))
        self.assertEqual(artifacts[cascade.NAIVE_DIFF], CONFLICTED_DIFF)
        self.assertEqual(artifacts[cascade.UNION_DIFF], "union diff\n")
        self.assertFalse(block["llm"]["needed"])
        self.assertEqual(block["deciding_artifact"],
                         {"verdict_63b9d44": "agent1.patch", "verdict_paper_era": cascade.UNION_DIFF})

    def test_the_conflicted_diff_comes_from_agent2s_head_before_any_merge(self):
        sandbox = self.conflicted()
        cascade_over(sandbox, merged_record("conflicts"))
        capture = next(c for c in sandbox.shell_commands() if "naive_conflict.diff" in c)
        self.assertIn(f"git reset --hard {HEAD}", capture)
        self.assertIn("git merge agent1 --no-commit --no-ff", capture)
        self.assertIn("git merge --abort", capture)

    def test_without_a_resolver_the_llm_step_says_why_and_the_verdict_is_a_lower_bound(self):
        sandbox = self.conflicted(tests={("tests1.patch", "union_diff.patch"): PASS})
        block, artifacts = cascade_over(sandbox, merged_record("conflicts"))
        self.assertEqual(block["llm"], {"ran": False, "needed": True, "reason": cascade.NO_RESOLVER})
        self.assertTrue(block["lower_bound"])
        self.assertEqual((block["route"], block["verdict_paper_era"]), ("none", False))
        self.assertEqual(block["deciding_artifact"]["verdict_paper_era"], cascade.UNION_DIFF)
        self.assertNotIn(cascade.LLM_DIFF, artifacts)

    def test_a_union_merge_that_still_conflicts_leaves_the_conflicted_diff_deciding(self):
        sandbox = self.conflicted(union={"error": "Union merge still has conflicts", "diff": "", "output": ""})
        block, artifacts = cascade_over(sandbox, merged_record("conflicts"))
        self.assertEqual((block["union"]["ran"], block["union"]["clean"], block["union"]["f1"]), (True, False, None))
        self.assertNotIn(cascade.UNION_DIFF, artifacts)
        self.assertEqual(sandbox.ran("union_diff.patch"), [])
        self.assertEqual(block["deciding_artifact"]["verdict_paper_era"], cascade.NAIVE_DIFF)

    def test_a_resolver_resolves_each_region_and_its_merge_is_tested(self):
        conflicted = "def f():\n<<<<<<< HEAD\n    return 2\n=======\n    return 1\n>>>>>>> agent1\n"
        sandbox = self.conflicted(
            tests={("tests1.patch", "union_diff.patch"): PASS, ("tests1.patch", "llm_diff.patch"): PASS,
                   ("tests2.patch", "llm_diff.patch"): PASS},
            files={"/patches/llm_unmerged.txt": "src/lib.py\nsrc/gone.py\n", "/workspace/repo/src/lib.py": conflicted,
                   "/patches/llm_diff.patch": "llm diff\n"})
        prompts = []

        def answer(config, prompt):
            prompts.append((config, prompt))
            return "```\ndef f():\n    return 3\n```"
        resolver = {"url": "http://user:secret@127.0.0.1:8457/v1", "model": "resolver", "key": None}
        with mock.patch.object(cascade, "ask_endpoint", answer):
            block, artifacts = cascade_over(sandbox, merged_record("conflicts"), resolver)
        llm = block["llm"]
        self.assertEqual((llm["ran"], llm["total_conflicts"], llm["resolved"], llm["errors"]), (True, 1, 1, []))
        self.assertEqual(llm["endpoint"], "http://127.0.0.1:8457/v1")
        self.assertEqual(sandbox.files["/workspace/repo/src/lib.py"], "def f():\n    return 3")
        self.assertEqual(prompts[0][1], cascade.RESOLVER_PROMPT + "\n\n" + conflicted)
        self.assertEqual((block["route"], block["verdict_paper_era"], block["lower_bound"]), ("llm", True, False))
        self.assertEqual(artifacts[cascade.LLM_DIFF], "llm diff\n")
        self.assertEqual(block["deciding_artifact"]["verdict_paper_era"], cascade.LLM_DIFF)
        remerge = next(c for c in sandbox.shell_commands() if "llm_unmerged.txt" in c)
        self.assertIn(f"git reset --hard {HEAD}", remerge)
        commit = next(c for c in sandbox.shell_commands() if "llm_diff.patch" in c)
        self.assertIn("'LLM merge resolution for features 1 and 6'", commit)
        self.assertIn(f"git diff {BASE} HEAD", commit)

    def test_a_failed_apply_has_no_naive_conflict_flag_and_runs_the_merge_itself(self):
        sandbox = FakeSandbox(apply_status={"agent1": "applied", "agent2": "failed"},
                              tests={**alone_runs(agent1=(PASS, PASS)), ("tests1.patch", "naive_diff.patch"): PASS})
        evaluation = merged_record("missing_input", clean_feature(PASS), clean_feature(PASS))
        block, _ = cascade_over(sandbox, evaluation)
        self.assertIsNone(block["has_naive_merge_conflict"])
        self.assertIs(block["naive"]["conflict"], False)
        self.assertEqual(block["naive"]["tests_from"], "cascade")
        self.assertEqual(block["agent_alone"]["agent2"]["reason"], "the patch did not apply")
        self.assertIsNone(block["agent_alone"]["agent2"]["f1"])
        self.assertIs(block["verdict_63b9d44_rerun"], True)
        self.assertEqual(block["route"], "none")

    def test_an_empty_patch_is_not_run_alone(self):
        sandbox = FakeSandbox(apply_status={"agent1": "skipped", "agent2": "applied"}, tests=alone_runs())
        block, _ = cascade_over(sandbox, merged_record("clean", clean_feature(FAIL), clean_feature(PASS),
                                                       agent1="skipped"))
        self.assertEqual(block["agent_alone"]["agent1"]["reason"], "the patch was empty")
        self.assertEqual(sandbox.ran("patch1.patch"), [])

    def test_the_two_pass_rules_can_disagree(self):
        evaluation = merged_record("clean", clean_feature(CARGO_PASS), clean_feature(CARGO_PASS))
        block, _ = cascade_over(FakeSandbox(tests=alone_runs()), evaluation)
        self.assertEqual((block["naive"]["f1"], block["naive"]["f1_regex"]), (True, False))
        self.assertEqual((block["route"], block["route_regex"]), ("naive", "none"))
        self.assertEqual((block["verdict_paper_era"], block["verdict_paper_era_regex"]), (True, False))

    def test_identical_patches_reuse_test_merged_and_point_at_agent1s_patch(self):
        evaluation = merged_record("identical", dict(PASS), dict(PASS))
        block, _ = cascade_over(FakeSandbox(tests=alone_runs()), evaluation)
        self.assertEqual(block["naive"]["tests_from"], "test_merged")
        self.assertTrue(block["naive"]["f1_regex"])
        self.assertEqual(block["deciding_artifact"]["verdict_63b9d44"], "agent1.patch")

    def test_the_union_step_waits_for_a_conflict(self):
        evaluation = merged_record("clean", clean_feature(FAIL), clean_feature(PASS))
        sandbox = FakeSandbox(tests=alone_runs())
        block, _ = cascade_over(sandbox, evaluation)
        self.assertNotIn("merge_union", sandbox.calls)
        self.assertEqual((block["route"], block["verdict_paper_era"], block["verdict_63b9d44"]), ("none", False, False))
        self.assertEqual(block["union"]["reason"], "the naive merge was clean")


class TheEntryPoint(unittest.TestCase):
    """run() reads the pair from its directory and the dataset, writes the
    merged diffs beside eval.json, and never raises."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="coordbench-cascade-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.run_dir = self.scratch / "logs" / "pair"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "agent1.patch").write_text(PATCH1, encoding="utf-8")
        (self.run_dir / "agent6.patch").write_text(PATCH2, encoding="utf-8")
        for feature in FEATURES:
            tests = self.scratch / "cb" / "dataset" / "r_task" / "task7" / f"feature{feature}" / "tests.patch"
            tests.parent.mkdir(parents=True)
            tests.write_text(f"tests for {feature}\n", encoding="utf-8")
        self.plan = {"repo": "r_task", "task_id": 7, "features": FEATURES, "setting": "coop", "error": None,
                     "run_dir": str(self.run_dir), "cooperbench_dir": str(self.scratch / "cb")}

    def run_with(self, sandbox, evaluation, settings=None):
        @contextlib.contextmanager
        def opened(plan):
            yield sandbox
        with mock.patch.object(cascade, "opened_sandbox", opened):
            return cascade.run(self.plan, evaluation, settings)

    def test_it_writes_the_merged_diffs_and_removes_stale_ones(self):
        (self.run_dir / cascade.UNION_DIFF).write_text("from an earlier scoring\n", encoding="utf-8")
        sandbox = FakeSandbox(tests=alone_runs())
        block = self.run_with(sandbox, merged_record("clean", clean_feature(PASS), clean_feature(PASS)))
        self.assertIsNone(block["error"])
        self.assertEqual((self.run_dir / cascade.NAIVE_DIFF).read_text(encoding="utf-8"), "clean diff\n")
        self.assertFalse((self.run_dir / cascade.UNION_DIFF).exists())
        self.assertEqual(sandbox.patches["tests1.patch"], "tests for 1\n")
        self.assertEqual(sandbox.patches["patch2.patch"], PATCH2)
        json.dumps(block)

    def test_a_failure_part_way_is_the_blocks_error_and_keeps_what_came_before(self):
        sandbox = FakeSandbox(naive={"conflict": True, "diff": "", "output": ""}, broken="merge_union",
                              files={"/patches/naive_conflict.diff": CONFLICTED_DIFF})
        block = self.run_with(sandbox, merged_record("conflicts"))
        self.assertEqual(block["error"], "SandboxError: merge_union: it broke")
        self.assertTrue(block["naive"]["conflict"])
        self.assertIsNone(block["union"])
        self.assertIsNone(block["verdict_paper_era"])
        self.assertIs(block["verdict_63b9d44"], False)
        self.assertEqual((self.run_dir / cascade.NAIVE_DIFF).read_text(encoding="utf-8"), CONFLICTED_DIFF)

    def test_it_skips_what_has_nothing_to_merge(self):
        cases = [({**self.plan, "setting": "solo"}, {}, {}, "the solo setting"),
                 ({**self.plan, "features": [1, 2, 3]}, {}, {}, "pairs of features only"),
                 ({**self.plan, "error": "agent1 crashed"}, {}, {}, "agent1 crashed"),
                 (self.plan, {"error": "Tests patch not found"}, {}, "Tests patch not found"),
                 (self.plan, {}, {"cascade": False}, "turned off")]
        for plan, evaluation, settings, reason in cases:
            block = cascade.run(plan, evaluation, settings)
            self.assertEqual((block["ran"], block["cascade_version"]), (False, cascade.CASCADE_VERSION))
            self.assertIn(reason, block["reason"])
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()), ["agent1.patch", "agent6.patch"])

    def test_it_reads_the_tests_from_the_dataset_the_scorer_was_given(self):
        """run.py's --dataset-dir hands test_merged another dataset (a
        59bbf65 worktree); the cascade judges by the same tests."""
        other = self.scratch / "dataset-59bbf65"
        for feature in FEATURES:
            tests = other / "r_task" / "task7" / f"feature{feature}" / "tests.patch"
            tests.parent.mkdir(parents=True)
            tests.write_text(f"paper-era tests for {feature}\n", encoding="utf-8")
        for settings, plan in (({"dataset_dir": str(other)}, self.plan),
                               ({}, {**self.plan, "dataset_dir": str(other)})):
            sandbox = FakeSandbox(tests=alone_runs())
            with mock.patch.object(cascade, "opened_sandbox", contextlib.contextmanager(lambda p: (yield sandbox))):
                cascade.run(plan, merged_record("clean", clean_feature(PASS), clean_feature(PASS)), settings)
            self.assertEqual(sandbox.patches["tests1.patch"], "paper-era tests for 1\n")

    def test_it_names_the_basis_by_cooperbench_s_commit(self):
        with mock.patch.object(cascade, "cooperbench_commit", return_value="63b9d44" + "f" * 33):
            block = self.run_with(FakeSandbox(tests=alone_runs()),
                                  merged_record("clean", clean_feature(PASS), clean_feature(PASS)))
        self.assertEqual(block["basis"], "the paper's cascade on the CooperBench 63b9d44 dataset")
        self.assertEqual(set(block["test_file_filter"]), {"verdict_63b9d44", "verdict_paper_era"})


class Resolver(http.server.BaseHTTPRequestHandler):
    """An OpenAI-compatible server for one test: it fails the first
    `failures` requests, then answers with `content`."""

    requests, failures, content = [], 0, ""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if len(type(self).requests) <= type(self).failures:
            self.send_error(500)
            return
        answer = json.dumps({"choices": [{"message": {"content": type(self).content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(self, *args):
        pass


class ValueResolver(Resolver):
    """Answers each prompt with its context, fenced, the conflict region
    replaced by one line that satisfies both features of the LLM test."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        context = body["messages"][0]["content"].split("Resolve this conflict:\n\n", 1)[1].split("\n")
        start = next(i for i, line in enumerate(context) if line.startswith("<<<<<<<"))
        end = next(i for i, line in enumerate(context) if line.startswith(">>>>>>>"))
        content = "```python\n" + "\n".join(context[:start] + ["VALUE = 3"] + context[end + 1:]) + "\n```"
        answer = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)


@contextlib.contextmanager
def resolver_server(content="", failures=0, handler=Resolver):
    Resolver.requests, Resolver.failures, Resolver.content = [], failures, content
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


class TheEndpoint(unittest.TestCase):
    def test_one_chat_completion_as_litellm_sent_it(self):
        with resolver_server("```python\nresolved\n```\n") as url:
            with mock.patch.dict(os.environ, {cascade.RESOLVER_KEY: "coordbench-fake-resolver-key"}):
                config = cascade.resolver_config({"resolver_url": url + "/"})
            resolved = cascade.endpoint_resolver(config)("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
        self.assertEqual(resolved, "resolved")
        (request,) = Resolver.requests
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["body"]["model"], cascade.DEFAULT_RESOLVER_MODEL)
        self.assertEqual(request["body"]["temperature"], 0)
        self.assertEqual(request["body"]["messages"],
                         [{"role": "user",
                           "content": cascade.RESOLVER_PROMPT + "\n\n<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n"}])
        self.assertEqual(request["auth"], "Bearer coordbench-fake-resolver-key")

    def test_three_retries_then_the_error(self):
        with resolver_server("fine", failures=3) as url:
            self.assertEqual(cascade.ask_endpoint({"url": url, "model": "m"}, "p"), "fine")
            self.assertEqual(len(Resolver.requests), 4)
        with resolver_server("never", failures=9) as url:
            with self.assertRaises(OSError):
                cascade.ask_endpoint({"url": url, "model": "m"}, "p")
            self.assertEqual(len(Resolver.requests), 4)

    def test_the_endpoint_comes_from_settings_or_the_environment_the_key_from_the_environment(self):
        with mock.patch.dict(os.environ, {cascade.RESOLVER_URL: "", cascade.RESOLVER_MODEL: ""}):
            self.assertIsNone(cascade.resolver_config({}))
        environment = {cascade.RESOLVER_URL: "http://127.0.0.1:8457/v1", cascade.RESOLVER_MODEL: "qwen"}
        with mock.patch.dict(os.environ, environment):
            self.assertEqual(cascade.resolver_config({}),
                             {"url": "http://127.0.0.1:8457/v1", "model": "qwen",
                              "key": os.environ.get(cascade.RESOLVER_KEY)})
            config = cascade.resolver_config({"resolver_url": "http://h/v1", "resolver_model": "m",
                                              "resolver_key": "ignored"})
            self.assertEqual((config["url"], config["model"]), ("http://h/v1", "m"))
            self.assertNotEqual(config["key"], "ignored")

    def test_the_block_never_shows_credentials_in_the_endpoint(self):
        self.assertEqual(cascade.shown_url("https://me:pw@example.com:8443/v1"), "https://example.com:8443/v1")
        self.assertEqual(cascade.shown_url("http://[::1]:8457/v1"), "http://[::1]:8457/v1")


STUB_SANDBOX = '''
import os, time
print("the stub talks on stdout")


class Result:
    def __init__(self, returncode, output):
        self.returncode, self.output = returncode, output

    def stdout_read(self):
        return self.output

    def stderr_read(self):
        return ""


class StubSandbox:
    def __init__(self, image, timeout):
        self.image, self.timeout = image, timeout

    def exec(self, *args):
        if args[0] == "getenv":
            return Result(0, os.environ.get(args[1], ""))
        return Result(0, "ran " + " ".join(args))

    def terminate(self):
        with open(os.environ["STUB_MARKER"], "w") as marker:
            marker.write(self.image)


class StubBackend:
    def create_sandbox(self, image, timeout):
        return StubSandbox(image, timeout)


def get_backend(name):
    assert name == "docker"
    return StubBackend()


def _load_patch(content):
    return content or None


def _filter_test_files(content):
    return content.upper()


def _write_patch(sb, name, content):
    print("writing", name)


def _setup_branches(sb):
    return {"base_sha": "abc", "apply_status": {"agent1": "applied", "agent2": "applied"}, "error": None}


def _merge_naive(sb, base_sha):
    print("chatter between the answers")
    return {"conflict": True, "diff": "", "output": base_sha}


def _merge_union(sb, base_sha):
    raise RuntimeError("union broke")


def _run_tests(sb, tests_patch, feature_patch, base_sha):
    if feature_patch == "slow.patch":
        time.sleep(60)
    return {"passed": True, "output": "1 passed", "exit_code": 0, "tests_passed": 1, "tests_failed": 0}
'''


class TheChannel(unittest.TestCase):
    """RemoteSandbox against a stub of CooperBench's package, run by this
    Python since the stub has no virtualenv."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="coordbench-channel-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        package = self.scratch / "src" / "cooperbench"
        (package / "eval").mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "eval" / "__init__.py").write_text("", encoding="utf-8")
        (package / "eval" / "sandbox.py").write_text(STUB_SANDBOX, encoding="utf-8")
        (package / "utils.py").write_text(
            "def get_image_name(repo, task_id):\n    return 'img-' + repo + '-' + str(task_id)\n", encoding="utf-8")
        self.marker = self.scratch / "terminated"
        environment = {"STUB_MARKER": str(self.marker), "CLAUDE_CODE_OAUTH_TOKEN": "coordbench-fake-oauth",
                       cascade.RESOLVER_KEY: "coordbench-fake-resolver-key", "STUB_HARMLESS": "kept"}
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sandbox = cascade.RemoteSandbox.start(self.scratch, budget=120)
        self.addCleanup(self.sandbox.close)

    def test_each_helper_answers_with_its_own_value_whatever_it_prints(self):
        self.assertEqual(self.sandbox.open("repo_task", 3), {"image": "img-repo_task-3", "container": None})
        self.assertEqual(self.sandbox.filter_patch("diff"), "DIFF")
        self.assertIsNone(self.sandbox.write_patch("patch1.patch", "x"))
        self.assertEqual(self.sandbox.setup_branches()["base_sha"], "abc")
        self.assertEqual(self.sandbox.merge_naive("abc"), {"conflict": True, "diff": "", "output": "abc"})
        self.assertEqual(self.sandbox.exec("echo", "hi"), {"returncode": 0, "output": "ran echo hi"})
        self.assertTrue(self.sandbox.run_tests("tests1.patch", "patch1.patch", "abc")["passed"])

    def test_a_helper_that_raises_is_an_error_here_and_the_channel_goes_on(self):
        self.sandbox.open("repo_task", 3)
        with self.assertRaisesRegex(cascade.SandboxError, "merge_union: RuntimeError: union broke"):
            self.sandbox.merge_union("abc")
        self.assertEqual(self.sandbox.filter_patch("a"), "A")

    def test_closing_ends_the_sandbox_and_the_process(self):
        self.sandbox.open("repo_task", 3)
        self.sandbox.close()
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "img-repo_task-3")
        self.assertEqual(self.sandbox.process.returncode, 0)

    def test_the_sandbox_process_gets_no_credentials(self):
        self.sandbox.open("repo_task", 3)
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", cascade.RESOLVER_KEY):
            self.assertEqual(self.sandbox.exec("getenv", name)["output"], "", name)
        self.assertEqual(self.sandbox.exec("getenv", "STUB_HARMLESS")["output"], "kept")

    def test_a_call_past_its_time_breaks_the_channel_and_closing_kills_it(self):
        self.sandbox.open("repo_task", 3)
        started = time.monotonic()
        with self.assertRaisesRegex(cascade.SandboxError, "no answer within"):
            self.sandbox.call("run_tests", wait=0.5, tests_patch="t", feature_patch="slow.patch", base_sha="b")
        with self.assertRaisesRegex(cascade.SandboxError, "out of step"):
            self.sandbox.filter_patch("a")
        with mock.patch.object(cascade.drivers, "docker") as docker:
            self.sandbox.close()
        docker.assert_not_called()
        self.assertIsNotNone(self.sandbox.process.returncode)
        self.assertLess(time.monotonic() - started, 30)

    def test_a_kill_removes_the_container_it_knows(self):
        self.sandbox.container = "c0ffee"
        self.sandbox.broken = True
        with mock.patch.object(cascade.drivers, "docker") as docker:
            self.sandbox.close()
        docker.assert_called_once_with("rm", "-f", "c0ffee", timeout=60)


def cooperbench_checkout():
    """CooperBench's checkout with its virtualenv, from COOPERBENCH_DIR or
    ~/cooperbench, or None."""
    for root in filter(None, [os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")]):
        root = Path(root)
        if (root / ".venv" / "bin" / "python").exists() and (root / "src" / "cooperbench" / "eval" / "sandbox.py").exists():
            return root
    return None


COOPERBENCH = cooperbench_checkout()
# A backend for CooperBench's own helpers that runs each command here: a
# fresh copy of the template repository per sandbox, the container's
# paths mapped to a directory. Put before SANDBOX_SCRIPT (or a script
# calling test_merged), it replaces the get_backend the helpers call.
LOCAL_BACKEND = r'''
import os, shutil, subprocess, sys
sys.path.insert(0, sys.argv[1])
from cooperbench.eval import sandbox as _cooperbench_sandbox
_ROOT = os.environ["COORDBENCH_TEST_SANDBOX"]
_PLACES = (("/workspace/repo", _ROOT + "/repo"), ("/patches", _ROOT + "/patches"),
           ("/usr/local/bin/runner.sh", _ROOT + "/runner.sh"))


class _LocalResult:
    def __init__(self, done):
        self.returncode, self._output = done.returncode, done.stdout

    def stdout_read(self):
        return self._output

    def stderr_read(self):
        return ""


class _LocalSandbox:
    def exec(self, *args):
        moved = []
        for arg in args:
            for old, new in _PLACES:
                arg = arg.replace(old, new)
            moved.append(arg)
        return _LocalResult(subprocess.run(moved, cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, errors="replace"))

    def terminate(self):
        with open(_ROOT + "/terminated", "a") as marker:
            marker.write("terminated\n")


class _LocalBackend:
    def create_sandbox(self, image, timeout=600, workdir="/workspace"):
        with open(_ROOT + "/image", "w") as named:
            named.write(image)
        for place in ("repo", "patches"):
            shutil.rmtree(_ROOT + "/" + place, ignore_errors=True)
        shutil.copytree(_ROOT + "/template", _ROOT + "/repo", symlinks=True)
        os.makedirs(_ROOT + "/patches")
        return _LocalSandbox()


_cooperbench_sandbox.get_backend = lambda name: _LocalBackend()
'''
TEST_MERGED_SCRIPT = r'''
import json
request = json.loads(sys.argv[2])
print(json.dumps(_cooperbench_sandbox.test_merged(**request)))
'''
RUNNER = """#!/bin/bash
set -e
cd "{root}/repo"
git reset --hard HEAD >/dev/null 2>&1
git clean -fd >/dev/null 2>&1
if [[ -n "$2" ]]; then
    git apply --ignore-whitespace "{root}/patches/$2" || git apply --3way "{root}/patches/$2"
fi
git apply --ignore-whitespace "{root}/patches/$1"
status=0
for check in checks/*.py; do python3 "$check" || status=1; done
exit $status
"""
LIBRARY = ('"""A library for the cascade\'s tests."""\n\n\ndef base():\n    return 0\n\n\n'
           'def middle():\n    return "a"\n\n\ndef more():\n    return "b"\n\n\nVALUE = 0\n')
CHECK = """import sys
sys.path.insert(0, ".")
import lib
ok = {condition}
print("1 passed" if ok else "1 failed")
sys.exit(0 if ok else 1)
"""


def git(directory, *args):
    return subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True, text=True).stdout


def diff_of(template, change):
    """The patch an agent would hand in: `change` applied to a copy of the
    template repository, diffed against its base."""
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "copy"
        shutil.copytree(template, copy)
        change(copy)
        git(copy, "add", "-A")
        return git(copy, "diff", "--cached")


@unittest.skipIf(COOPERBENCH is None or shutil.which("git") is None,
                 "CooperBench's checkout with its .venv is not here; set COOPERBENCH_DIR to it")
class AgainstCooperBench(unittest.TestCase):
    """CooperBench's real helpers and its real test_merged, on a git
    repository here: the cascade's commands must work with what 63b9d44's
    helpers leave behind (branches, commits, aborted merges)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="coordbench-local-sandbox-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        environment = {"COORDBENCH_TEST_SANDBOX": str(self.root), "GIT_CONFIG_GLOBAL": os.devnull,
                       "GIT_CONFIG_NOSYSTEM": "1"}
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.template = self.root / "template"
        self.template.mkdir()
        git(self.template, "init", "-q")
        git(self.template, "config", "user.email", "base@example.com")
        git(self.template, "config", "user.name", "Base")
        (self.template / "lib.py").write_text(LIBRARY, encoding="utf-8")
        git(self.template, "add", "-A")
        git(self.template, "commit", "-q", "-m", "base")
        (self.root / "runner.sh").write_text(RUNNER.format(root=self.root), encoding="utf-8")
        self.checkout = self.fake_checkout()

    def fake_checkout(self):
        """CooperBench's code and virtualenv beside a dataset of one task,
        the way run.py's plan names a checkout."""
        checkout = self.root / "cooperbench"
        checkout.mkdir()
        (checkout / "src").symlink_to(COOPERBENCH / "src")
        (checkout / ".venv").symlink_to(COOPERBENCH / ".venv")
        return checkout

    def feature(self, number, condition):
        """A feature's hidden tests: a tests.patch adding checks/f<n>.py."""
        def add_check(copy):
            (copy / "checks").mkdir(exist_ok=True)
            (copy / "checks" / f"f{number}.py").write_text(CHECK.format(condition=condition), encoding="utf-8")
        tests = self.checkout / "dataset" / "fake_repo_task" / "task1" / f"feature{number}" / "tests.patch"
        tests.parent.mkdir(parents=True)
        tests.write_text(diff_of(self.template, add_check), encoding="utf-8")

    def agent(self, run_dir, number, change):
        (run_dir / f"agent{number}.patch").write_text(diff_of(self.template, change), encoding="utf-8")

    def pair(self, change1, change2, check1, check2, settings=None):
        """test_merged and then the cascade on one pair; returns both and the run directory."""
        run_dir = self.root / "run"
        run_dir.mkdir()
        self.feature(1, check1)
        self.feature(2, check2)
        self.agent(run_dir, 1, change1)
        self.agent(run_dir, 2, change2)
        plan = {"repo": "fake_repo_task", "task_id": 1, "features": [1, 2], "setting": "coop", "error": None,
                "run_dir": str(run_dir), "cooperbench_dir": str(self.checkout)}
        evaluation = self.score_with_test_merged(plan)
        with mock.patch.object(cascade, "SANDBOX_SCRIPT", LOCAL_BACKEND + cascade.SANDBOX_SCRIPT):
            block = cascade.run(plan, evaluation, settings)
        return evaluation, block, run_dir

    def score_with_test_merged(self, plan):
        """CooperBench's own verdict on the pair, from its real test_merged."""
        request = {"repo_name": plan["repo"], "task_id": 1, "feature1_id": 1, "feature2_id": 2,
                   "patch1": (Path(plan["run_dir"]) / "agent1.patch").read_text(encoding="utf-8"),
                   "patch2": (Path(plan["run_dir"]) / "agent2.patch").read_text(encoding="utf-8"),
                   "backend": "docker", "dataset_dir": str(self.checkout / "dataset")}
        done = subprocess.run([str(COOPERBENCH / ".venv" / "bin" / "python"), "-I", "-c",
                               LOCAL_BACKEND + TEST_MERGED_SCRIPT, str(COOPERBENCH / "src"), json.dumps(request)],
                              cwd=self.checkout, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def append(self, text):
        def change(copy):
            with open(copy / "lib.py", "a", encoding="utf-8") as library:
                library.write(text)
        return change

    def replace(self, old, new):
        def change(copy):
            path = copy / "lib.py"
            path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        return change

    def test_union_passes_a_conflict_the_lead_alone_cannot(self):
        evaluation, block, run_dir = self.pair(
            self.append("\n\ndef one():\n    return 1\n"), self.append("\n\ndef two():\n    return 2\n"),
            "getattr(lib, 'one', None) is not None and lib.one() == 1",
            "getattr(lib, 'two', None) is not None and lib.two() == 2")
        self.assertIsNone(block["error"])
        self.assertEqual((evaluation["merge"]["status"], evaluation["both_passed"]), ("conflicts", False))
        self.assertEqual((block["verdict_63b9d44"], block["verdict_63b9d44_rerun"]), (False, False))
        self.assertTrue(block["has_naive_merge_conflict"])
        self.assertEqual(block["naive"]["conflict_sections"], 1)
        self.assertIn("<<<<<<<", (run_dir / cascade.NAIVE_DIFF).read_text(encoding="utf-8"))
        self.assertEqual((block["union"]["clean"], block["union"]["f1"], block["union"]["f2"]), (True, True, True))
        union = (run_dir / cascade.UNION_DIFF).read_text(encoding="utf-8")
        self.assertIn("+def one():", union)
        self.assertIn("+def two():", union)
        self.assertEqual((block["route"], block["verdict_paper_era"]), ("union", True))
        alone = block["agent_alone"]
        self.assertEqual([alone[a][f] for a in ("agent1", "agent2") for f in ("f1", "f2")],
                         [True, False, False, True])
        self.assertEqual((self.root / "image").read_text(encoding="utf-8"), "akhatua/cooperbench-fake-repo:task1")
        self.assertEqual((self.root / "terminated").read_text(encoding="utf-8").count("terminated"), 2)

    def test_a_clean_merge_reuses_test_merged_and_agrees_with_it(self):
        evaluation, block, run_dir = self.pair(
            self.replace("def base():\n    return 0\n", "def base():\n    return 0\n\n\ndef one():\n    return 1\n"),
            self.replace("VALUE = 0\n", "VALUE = 2\n"),
            "getattr(lib, 'one', None) is not None and lib.one() == 1", "lib.VALUE == 2")
        self.assertIsNone(block["error"])
        self.assertEqual((evaluation["merge"]["status"], evaluation["both_passed"]), ("clean", True))
        self.assertEqual((block["naive"]["tests_from"], block["naive"]["f1"], block["naive"]["f2"]),
                         ("test_merged", True, True))
        self.assertEqual((block["route"], block["verdict_63b9d44"], block["verdict_63b9d44_rerun"]),
                         ("naive", True, True))
        self.assertEqual((run_dir / cascade.NAIVE_DIFF).read_text(encoding="utf-8")[:5000],
                         evaluation["merge"]["diff"])
        self.assertFalse((run_dir / cascade.UNION_DIFF).exists())

    def test_the_resolver_merges_what_union_cannot(self):
        with resolver_server(handler=ValueResolver) as url:
            evaluation, block, run_dir = self.pair(
                self.replace("VALUE = 0\n", "VALUE = 1\n"), self.replace("VALUE = 0\n", "VALUE = 2\n"),
                "lib.VALUE % 2 == 1", "lib.VALUE > 1", settings={"resolver_url": url})
        self.assertIsNone(block["error"])
        self.assertEqual(evaluation["both_passed"], False)
        self.assertEqual((block["union"]["clean"], block["union"]["f1"], block["union"]["f2"]), (True, True, False))
        llm = block["llm"]
        self.assertEqual((llm["ran"], llm["total_conflicts"], llm["resolved"]), (True, 1, 1))
        self.assertEqual((llm["f1"], llm["f2"]), (True, True))
        self.assertIn("+VALUE = 3", (run_dir / cascade.LLM_DIFF).read_text(encoding="utf-8"))
        self.assertEqual((block["route"], block["verdict_paper_era"], block["lower_bound"]), ("llm", True, False))
        self.assertEqual(len(Resolver.requests), 1)


if __name__ == "__main__":
    unittest.main()
