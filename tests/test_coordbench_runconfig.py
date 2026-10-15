"""runconfig.py writes the run-level files CooperBench's runner writes,
config.json and summary.json, so these pin its keys and formulas to
runner/core.py's at 63b9d44, and pin the provenance that tells the
paper's dataset from the code's. Git runs on repositories made here;
docker is a stub; nothing reaches a container."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py shares its name with the standard library's tracer.
if getattr(sys.modules.get("trace"), "__file__", None) != str(COORDBENCH / "trace.py"):
    sys.modules.pop("trace", None)
import drivers  # noqa: E402
import runconfig  # noqa: E402

HAS_GIT = shutil.which("git") is not None


def git(directory, *arguments):
    return subprocess.run(["git", "-C", str(directory), *arguments], check=True, capture_output=True,
                          text=True).stdout.strip()


def report(status, evaluation=None, cost=None, eval_skipped=False, task="r/1/1,2"):
    return {"entry": {"task": task, "status": status, "cost": cost, "eval": evaluation},
            "eval_skipped": eval_skipped, "run_dir": None}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-runconfig-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        runconfig.PROVENANCE.clear()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class Provenance(Case):
    def checkout(self):
        repo = self.tmp / "cooperbench"
        self.write(repo / "dataset" / "r_task" / "task1" / "feature1" / "feature.md", "Do 1.\n")
        self.write(repo / "src" / "x.py", "x = 1\n")
        git(repo.parent, "init", "-q", str(repo))
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "dataset")
        return repo

    @unittest.skipUnless(HAS_GIT, "needs git")
    def test_a_dataset_in_a_checkout_is_named_by_its_git_tree_and_its_last_commit(self):
        repo = self.checkout()
        facts = runconfig.dataset_facts(repo / "dataset")
        self.assertEqual(facts["git_tree"], git(repo, "rev-parse", "HEAD:dataset"))
        self.assertEqual(facts["last_commit"], git(repo, "rev-parse", "HEAD"))
        self.assertEqual((facts["modified"], facts["files"]), (False, 1))
        self.assertRegex(facts["sha256"], "^[0-9a-f]{64}$")
        (repo / "dataset" / "r_task" / "task1" / "feature1" / "feature.md").write_text("Do 1 better.\n")
        changed = runconfig.dataset_facts(repo / "dataset")
        self.assertEqual((changed["git_tree"], changed["modified"]), (facts["git_tree"], True))
        self.assertNotEqual(changed["sha256"], facts["sha256"])
        checkout = runconfig.checkout_facts(repo)
        self.assertEqual((checkout["commit"], checkout["modified"], checkout["modified_count"]),
                         (git(repo, "rev-parse", "HEAD"), ["dataset/r_task/task1/feature1/feature.md"], 1))

    def test_a_dataset_outside_git_has_its_content_hash_alone(self):
        dataset = self.tmp / "dataset"
        self.write(dataset / "a" / "f.md", "one\n")
        self.write(dataset / "b.md", "two\n")
        facts = runconfig.dataset_facts(dataset)
        self.assertEqual((facts["git_tree"], facts["last_commit"], facts["modified"], facts["files"]),
                         (None, None, None, 2))
        # The hash is of paths and contents, never of times.
        again = self.tmp / "copy"
        shutil.copytree(dataset, again)
        self.assertEqual(runconfig.content_digest(again)[0], facts["sha256"])
        self.assertEqual(runconfig.checkout_facts(dataset), {"dir": str(dataset), "commit": None, "modified": None,
                                                             "modified_count": None})
        self.assertEqual(runconfig.dataset_facts(self.tmp / "missing")["sha256"], None)

    def test_provenance_is_worked_out_once_per_pair_of_directories(self):
        dataset = self.tmp / "dataset"
        self.write(dataset / "f.md", "x\n")
        with mock.patch.object(runconfig, "dataset_facts", wraps=runconfig.dataset_facts) as facts:
            first = runconfig.provenance(self.tmp, dataset)
            self.assertIs(runconfig.provenance(self.tmp, dataset), first)
        self.assertEqual(facts.call_count, 1)
        self.assertEqual(set(first), {"cooperbench", "dataset", "coordbench"})

    def test_an_image_is_recorded_as_docker_holds_it_with_the_overlay_label(self):
        def docker(*args, **_):
            self.assertEqual(args[:3], ("image", "inspect", "-f"))
            known = args[-1] == "akhatua/x:task1"
            out = 'sha256:abc|["mirror.gcr.io/akhatua/x@sha256:def"]|cloud-session\n' if known else ""
            return subprocess.CompletedProcess(args, 0 if known else 1, out, "")

        with mock.patch.object(drivers, "docker", docker):
            self.assertEqual(runconfig.image_facts("akhatua/x:task1"),
                             {"image": "akhatua/x:task1", "id": "sha256:abc",
                              "repo_digests": ["mirror.gcr.io/akhatua/x@sha256:def"], "overlay": "cloud-session"})
            self.assertEqual(runconfig.image_facts("akhatua/y:task2"),
                             {"image": "akhatua/y:task2", "id": None, "repo_digests": None, "overlay": None})

        def rebuilt(*args, **_):
            return subprocess.CompletedProcess(args, 0, "sha256:abc|[]|<no value>\n", "")

        with mock.patch.object(drivers, "docker", rebuilt):
            facts = runconfig.image_facts("akhatua/x:task1")
        self.assertEqual((facts["repo_digests"], facts["overlay"]), ([], None))


class TheConfig(Case):
    class Driver:
        framework = "claude_code"
        host_workspace = False
        differences_from_cooperbench = ("one", "two")

    def settings(self, **overrides):
        return {"run_name": "r", "driver": "claude-cli", "model": "claude-x", "options": {"k": 1},
                "max_turns": None, "timeout_s": 7200.0, "cooperbench_dir": self.tmp, "dataset_dir": self.tmp,
                **overrides}

    def test_cooperbench_s_keys_come_first_in_its_order_then_coordbench_s(self):
        record = runconfig.config_record(self.settings(concurrency=4), self.Driver(), "coop", "coop",
                                         [("r", 1, [1, 2]), ("r", 1, [3, 4])], {"img": {}})
        self.assertEqual(list(record)[:7], ["run_name", "agent_framework", "model", "setting", "concurrency",
                                            "total_tasks", "started_at"])
        self.assertEqual((record["concurrency"], record["total_tasks"], record["isolation"]), (4, 2, "container"))
        self.assertEqual(record["differences_from_cooperbench"], ["one", "two"])
        self.assertEqual(record["cooperbench_sandbox_timeout_s"], 600)
        self.assertEqual(record["pairs"][1], {"repo": "r", "task_id": 1, "features": [3, 4]})

    def test_a_driver_working_on_host_files_is_advisory_unless_it_says_otherwise(self):
        class Host:
            host_workspace = True

        class Says(Host):
            isolation = "container"

        self.assertEqual(runconfig.driver_isolation(Host()), "advisory")
        self.assertEqual(runconfig.driver_isolation(Says()), "container")
        self.assertEqual(runconfig.driver_isolation(object()), "container")
        self.assertEqual(runconfig.driver_isolation(drivers.SessionDriver()), "advisory")

    def test_prepare_keeps_the_config_the_first_prepare_wrote(self):
        runconfig.write_config(self.tmp, {"n": 1})
        runconfig.write_config(self.tmp, {"n": 2}, replace=False)
        self.assertEqual(json.loads((self.tmp / "config.json").read_text()), {"n": 1})
        runconfig.write_config(self.tmp, {"n": 3})
        self.assertEqual(json.loads((self.tmp / "config.json").read_text()), {"n": 3})


class TheSummary(Case):
    def test_the_formulas_are_save_summary_s(self):
        reports = [report("done", "pass", 1.0), report("done", "fail", 2.0), report("done", "error"),
                   report("skip", "pass", eval_skipped=True), report("error", None)]
        summary = runconfig.summary_record("r", reports, 12.5, {"wall_time": 1}, {"arm": "coop"})
        self.assertEqual(list(summary)[:10], ["run_name", "completed_at", "pass_rate", "total_tasks", "completed",
                                              "skipped", "failed", "total_cost", "total_time_seconds", "results"])
        # pass_rate leaves eval errors out of the denominator, as _save_summary does.
        self.assertEqual(summary["pass_rate"], 2 / 3)
        self.assertEqual(summary["eval"], {"total_evaluated": 4, "passed": 2, "failed": 1, "errors": 1,
                                           "skipped": 1, "pass_rate": 2 / 3})
        # The console's rate divides by everything evaluated.
        self.assertEqual(summary["pass_rate_over_evaluated"], 2 / 4)
        self.assertEqual((summary["total_tasks"], summary["completed"], summary["skipped"], summary["failed"]),
                         (5, 3, 1, 1))
        self.assertEqual((summary["total_cost"], summary["total_time_seconds"]), (3.0, 12.5))
        self.assertEqual((summary["run_totals"], summary["arm"]), ({"wall_time": 1}, "coop"))
        self.assertEqual(summary["results"][0], {"task": "r/1/1,2", "status": "done", "cost": 1.0, "eval": "pass"})

    def test_a_cost_nobody_measured_stays_unknown_and_an_empty_run_rates_zero(self):
        self.assertIsNone(runconfig.summary_record("r", [report("done", "pass")], 1, {}, {})["total_cost"])
        self.assertEqual(runconfig.summary_record("r", [], 0, {}, {})["pass_rate"], 0.0)

    def test_a_pair_s_report_is_read_from_what_it_left(self):
        pair = self.tmp / "p"
        self.write(pair / "result.json", json.dumps({"total_cost": 0.5}))
        self.write(pair / "eval.json", json.dumps({"both_passed": False, "error": None}))
        self.assertEqual(runconfig.pair_report(pair, "t", "done")["entry"],
                         {"task": "t", "status": "done", "cost": 0.5, "eval": "fail"})
        self.assertTrue(runconfig.pair_report(pair, "t", "skip", eval_skipped=True)["eval_skipped"])
        # An errored evaluation already on disk counts as an error and not
        # as skipped, as _process_eval_result returns (0, 0, 1, 0) for it.
        errored = self.tmp / "e"
        self.write(errored / "result.json", json.dumps({"total_cost": 1.0}))
        self.write(errored / "eval.json", json.dumps({"error": "sandbox died", "both_passed": False}))
        skipped = runconfig.pair_report(errored, "t", "skip", eval_skipped=True)
        self.assertEqual((skipped["entry"]["eval"], skipped["eval_skipped"]), ("error", False))
        self.assertEqual(runconfig.eval_tally([skipped]), {"passed": 0, "failed": 0, "errors": 1, "skipped": 0})
        failed = self.tmp / "q"
        self.write(failed / "result.json", json.dumps({"total_cost": None, "error": "DockerError: no"}))
        self.assertEqual(runconfig.pair_report(failed, "t", "done")["entry"],
                         {"task": "t", "status": "error", "cost": None, "error": "DockerError: no", "eval": None})
        self.assertEqual(runconfig.eval_status({"error": "x", "both_passed": True}), "error")
        self.assertIsNone(runconfig.eval_status(None))
        # A run the session route's audit found measured the harness is an
        # error, whatever its evaluation scored, as summary.py and the
        # report count it.
        audited = self.tmp / "a"
        self.write(audited / "result.json", json.dumps({"total_cost": 2.0}))
        self.write(audited / "eval.json", json.dumps({"both_passed": True, "error": None}))
        self.write(audited / "audit.json", json.dumps({"harness_error": "the run met a full disk"}))
        report = runconfig.pair_report(audited, "t", "done")
        self.assertEqual(report["entry"], {"task": "t", "status": "error", "cost": 2.0,
                                           "error": "harness error: the run met a full disk",
                                           "harness_error": "the run met a full disk", "eval": None})
        self.assertEqual(runconfig.eval_tally([report]), {"passed": 0, "failed": 0, "errors": 0, "skipped": 0})

    def test_run_totals_are_get_run_totals_over_every_result_of_the_setting(self):
        setting = self.tmp / "coop"
        self.write(setting / "r" / "1" / "f1_f2" / "result.json", json.dumps({
            "total_cost": 1.5, "duration_seconds": 100, "started_at": "2026-10-09T10:00:00",
            "ended_at": "2026-10-09T10:01:40"}))
        self.write(setting / "r" / "1" / "f3_f4" / "result.json", json.dumps({
            "total_cost": None, "duration_seconds": 50, "started_at": "2026-10-09T10:01:00",
            "ended_at": "2026-10-09T10:05:00"}))
        self.assertEqual(runconfig.run_totals(setting), {"wall_time": 300.0, "run_time": 150.0, "total_cost": 1.5,
                                                         "task_count": 2})
        self.assertEqual(runconfig.run_totals(self.tmp / "none"),
                         {"wall_time": 0.0, "run_time": 0.0, "total_cost": 0.0, "task_count": 0})
        reports = runconfig.reports_from_disk(setting)
        self.assertEqual([r["entry"]["status"] for r in reports], ["done", "done"])

    def test_the_claude_code_digest_gathers_what_every_agent_ran_with(self):
        first, second = self.tmp / "a", self.tmp / "b"
        self.write(first / "result.json", json.dumps({"agents": {
            "agent1": {"claude_code_version": "2.1.0", "model": "claude-x", "tools": ["Bash", "Read"],
                       "mcp_servers": [{"name": "coordbench", "status": "connected"}], "cost_source": "claude code",
                       "model_differs": False},
            "agent2": {"claude_code_version": "2.1.1", "model": "claude-y", "tools": ["Bash"],
                       "mcp_servers": [{"name": "coordbench", "status": "failed"}], "model_differs": True}}}))
        self.write(second / "result.json", json.dumps({"agent": {"model": "claude-x", "tools": None}}))
        self.assertEqual(runconfig.claude_code_digest([first, second, self.tmp / "missing"]), {
            "claude_code_versions": ["2.1.0", "2.1.1"], "models": ["claude-x", "claude-y"], "tools": ["Bash", "Read"],
            "mcp_servers": ["coordbench (connected)", "coordbench (failed)"], "cost_sources": ["claude code"],
            "model_mismatches": 1})

    def test_a_task_is_labelled_as_cooperbench_labels_it(self):
        self.assertEqual(runconfig.task_label("pallets_click_task", 2068, [6, 1]), "pallets_click_task/2068/6,1")


if __name__ == "__main__":
    unittest.main()
