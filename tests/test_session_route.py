"""The session route's lane, bundle and subset, the parts a run's
accidents reach: what the finish does about a returned agent's marker,
which files a results directory takes, and which runs a sample's logs
take."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "cooperbench" / "session"))

import bundle  # noqa: E402
import lane  # noqa: E402
import subset  # noqa: E402
import summary  # noqa: E402


def plan_with(workspaces, deadline):
    return {"agents": [{"id": agent, "handle": {"workspace": str(workspace), "deadline": deadline}}
                       for agent, workspace in workspaces.items()]}


class PlaceMissingMarkers(unittest.TestCase):
    """A subagent that returned without its marker gets one, so the run
    ends as if it had; one whose workspace the teardown has taken already
    gets nothing and raises nothing, since a finish run again after one
    that scored the pair and died (a full disk) finds no workspace."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"
        self.run_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write_plan(self, workspaces, deadline):
        lane.write_plan(self.run_dir, plan_with(workspaces, deadline))

    def test_a_returned_agent_without_a_marker_gets_one_before_its_deadline(self):
        workspace = self.run_dir / "agent1"
        workspace.mkdir()
        self.write_plan({"agent1": workspace}, time.time() + 600)
        notes = lane.place_missing_markers(self.run_dir, {"agent1"}, set())
        self.assertTrue((workspace / lane.MARKER).exists())
        self.assertEqual(notes["placed_markers"], ["agent1"])
        self.assertEqual(notes["gone_workspaces"], [])
        self.assertEqual(json.loads((self.run_dir / "harness.json").read_text())["placed_markers"], ["agent1"])

    def test_a_workspace_the_teardown_took_is_noted_and_not_touched(self):
        gone = self.run_dir / "workspaces" / "agent1"
        self.write_plan({"agent1": gone}, time.time() + 600)
        notes = lane.place_missing_markers(self.run_dir, {"agent1"}, set())
        self.assertFalse(gone.exists())
        self.assertEqual(notes["gone_workspaces"], ["agent1"])
        self.assertEqual(notes["placed_markers"], [])

    def test_an_agent_past_its_deadline_is_left_to_expire(self):
        workspace = self.run_dir / "agent1"
        workspace.mkdir()
        self.write_plan({"agent1": workspace}, time.time() - 1)
        notes = lane.place_missing_markers(self.run_dir, {"agent1"}, set())
        self.assertFalse((workspace / lane.MARKER).exists())
        self.assertEqual(notes["overran"], ["agent1"])

    def test_a_second_finish_keeps_the_first_finishs_notes(self):
        workspace = self.run_dir / "agent1"
        workspace.mkdir()
        self.write_plan({"agent1": workspace}, time.time() + 600)
        lane.place_missing_markers(self.run_dir, {"agent1"}, set())
        lane.place_missing_markers(self.run_dir, {"agent1"}, set())
        record = json.loads((self.run_dir / "harness.json").read_text())
        self.assertEqual(record["placed_markers"], [])
        self.assertEqual(record["previous"]["placed_markers"], ["agent1"])

    def test_a_dead_agent_has_its_deadline_brought_forward(self):
        workspace = self.run_dir / "agent2"
        workspace.mkdir()
        self.write_plan({"agent2": workspace}, time.time() + 600)
        notes = lane.place_missing_markers(self.run_dir, set(), {"agent2"})
        self.assertEqual(notes["expired"], ["agent2"])
        deadline = lane.read_plan(self.run_dir)["agents"][0]["handle"]["deadline"]
        self.assertLess(deadline, time.time())


class FinishAfterTeardown(unittest.TestCase):
    """A finish run again once an earlier one has torn the run down
    leaves that earlier finish's patches and score alone: run.py finish
    would find no marker and no patch, call the agents failed and write
    empty patches over the only copy."""

    def test_a_gone_workspace_stops_the_finish_before_run_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            lane.write_plan(run_dir, plan_with({"agent1": run_dir / "gone1", "agent2": run_dir / "gone2"},
                                               time.time() + 600))
            (run_dir / "agent1.patch").write_text("diff --git a/x b/x\n")
            (run_dir / "result.json").write_text("{}\n")
            args = SimpleNamespace(run_dir=str(run_dir), returned=["agent1", "agent2"], dead=None)
            with mock.patch.object(lane.subprocess, "run", side_effect=AssertionError("run.py was called")):
                verdict = lane.finish_pair(args)
            self.assertEqual(verdict["verdict"], "error")
            self.assertEqual(verdict["harness"]["gone_workspaces"], ["agent1", "agent2"])
            self.assertEqual((run_dir / "agent1.patch").read_text(), "diff --git a/x b/x\n")

    def test_an_agent_not_returned_with_a_gone_workspace_stops_it_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "here").mkdir()
            lane.write_plan(run_dir, plan_with({"agent1": run_dir / "here", "agent2": run_dir / "gone"},
                                               time.time() + 600))
            (run_dir / "result.json").write_text("{}\n")
            args = SimpleNamespace(run_dir=str(run_dir), returned=["agent1"], dead=None)
            with mock.patch.object(lane.subprocess, "run", side_effect=AssertionError("run.py was called")):
                verdict = lane.finish_pair(args)
            self.assertEqual(verdict["harness"]["gone_workspaces"], ["agent2"])

    def test_a_gone_workspace_with_no_outputs_yet_still_reaches_run_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "here").mkdir()
            lane.write_plan(run_dir, plan_with({"agent1": run_dir / "here", "agent2": run_dir / "gone"},
                                               time.time() + 600))
            args = SimpleNamespace(run_dir=str(run_dir), returned=["agent1"], dead=["agent2"])
            done = SimpleNamespace(stdout='{"verdict": "fail"}\n', stderr="", returncode=0)
            with mock.patch.object(lane.subprocess, "run", return_value=done) as called:
                verdict = lane.finish_pair(args)
            self.assertEqual(verdict["verdict"], "fail")
            self.assertIn("finish", called.call_args_list[0][0][0])


class BundleWanted(unittest.TestCase):
    """The results directory takes a run's records and leaves out what is
    large, holds a token or is an earlier attempt's archive."""

    def test_records_are_wanted_and_archives_transcripts_and_wrappers_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "f1_f2"
            (run / "bin" / "agent1").mkdir(parents=True)
            names = ["result.json", "eval.json", "harness.json", "agent1.patch", "agent1_sent.jsonl",
                     "previous-20261015T124001.tar.gz", "agent1_session.jsonl", "plan.json", "bin/agent1/token"]
            for name in names:
                (run / name).write_text("x\n")
            kept = sorted(path.relative_to(run).as_posix() for path in run.rglob("*") if bundle.wanted(path))
        self.assertEqual(kept, ["agent1.patch", "agent1_sent.jsonl", "eval.json", "harness.json", "result.json"])


class SubsetCopiesTheSample(unittest.TestCase):
    """A sample's logs hold the finished runs of the items that carry a
    sample record, transcripts included and archives not, none of the
    tag's other runs, and the sample's lines of the queue; what is left
    out is named by its reason."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.logs = self.base / "logs"
        self.queue = self.base / "t-queue.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def run_dir(self, task, files=True):
        run = self.logs / "t-solo" / "solo" / "repo" / str(task) / "f1_f2"
        if files:
            run.mkdir(parents=True)
            (run / "result.json").write_text("{}\n")
            (run / "agent1_session.jsonl").write_text("{}\n")
            (run / "previous-20261015T120000.tar.gz").write_bytes(b"\x1f\x8b")
        return run

    def write_queue(self, items):
        self.queue.write_text("".join(json.dumps(item) + "\n" for item in items))

    def copied(self, out):
        return sorted(path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file())

    def test_finished_sampled_runs_are_copied_and_the_rest_named(self):
        outside = self.base / "elsewhere" / "run"
        outside.mkdir(parents=True)
        self.write_queue([
            {"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(self.run_dir(1))},
            {"id": "b", "status": "done", "run_dir": str(self.run_dir(2))},
            {"id": "c", "sample": {"sample": 8}, "status": "pending"},
            {"id": "d", "sample": {"sample": 8}, "status": "done", "run_dir": str(self.run_dir(3, files=False))},
            {"id": "e", "sample": {"sample": 8}, "status": "running", "run_dir": str(self.run_dir(4))},
            {"id": "f", "sample": {"sample": 8}, "status": "failed", "run_dir": str(outside)}])
        out = self.base / "out"
        left = subset.copy_runs(subset.sampled(self.queue), self.logs, out)
        self.assertEqual(left, {"unfinished": ["c", "e"], "no_run_dir": ["d"], "outside_logs": ["f"]})
        self.assertEqual(self.copied(out), ["t-solo/solo/repo/1/f1_f2/agent1_session.jsonl",
                                            "t-solo/solo/repo/1/f1_f2/result.json"])

    def test_logs_spelled_relative_to_the_working_directory(self):
        self.write_queue([{"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(self.run_dir(1))}])
        here = os.getcwd()
        os.chdir(self.base)
        try:
            left = subset.copy_runs(subset.sampled(self.queue), Path("logs"), Path("out"))
        finally:
            os.chdir(here)
        self.assertEqual(sum(len(ids) for ids in left.values()), 0)
        self.assertTrue((self.base / "out" / "t-solo" / "solo" / "repo" / "1" / "f1_f2" / "result.json").exists())

    def test_out_that_is_logs_or_inside_it_is_refused_and_nothing_moves(self):
        run = self.run_dir(1)
        self.write_queue([{"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(run)}])
        for out in (self.logs, self.logs / "sample"):
            with mock.patch("sys.stderr"):
                self.assertEqual(subset.main([str(self.queue), str(self.logs), str(out)]), 2)
        self.assertTrue((run / "result.json").exists())
        self.assertFalse((self.logs / "sample").exists())

    def test_nothing_finished_ends_quietly_with_the_queue_written(self):
        self.write_queue([{"id": "c", "sample": {"sample": 8}, "status": "pending"},
                          {"id": "x", "status": "done", "run_dir": str(self.run_dir(2))}])
        out = self.base / "out"
        with mock.patch("builtins.print") as printed:
            self.assertEqual(subset.main([str(self.queue), str(self.logs), str(out)]), 1)
        self.assertIn("not finished: c", printed.call_args[0][0])
        self.assertEqual([json.loads(line)["id"] for line in (out / self.queue.name).read_text().splitlines()], ["c"])

    def test_main_rebuilds_each_arm_from_the_copied_pairs(self):
        run = self.run_dir(1)
        (run / "plan.json").write_text(json.dumps({"run_dir": str(run), "arm_name": "solo"}) + "\n")
        (self.logs / "t-solo" / "config.json").write_text("{}\n")
        self.write_queue([{"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(run)}])
        out = self.base / "out"
        runner = mock.Mock()
        with mock.patch.object(subset, "load_runner", return_value=runner), mock.patch("builtins.print"):
            self.assertEqual(subset.main([str(self.queue), str(self.logs), str(out)]), 0)
        self.assertTrue((out / "t-solo" / "config.json").exists())
        plan = runner.refresh_summary.call_args[0][0]
        self.assertEqual(Path(plan["run_dir"]), out / "t-solo" / "solo" / "repo" / "1" / "f1_f2")

    def test_an_out_that_holds_anything_is_refused(self):
        run = self.run_dir(1)
        self.write_queue([{"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(run)}])
        stale = self.base / "out" / "t-solo" / "solo" / "repo" / "9" / "f1_f2"
        stale.mkdir(parents=True)
        for out in (self.base / "out", self.base):
            with mock.patch("sys.stderr"):
                self.assertEqual(subset.main([str(self.queue), str(self.logs), str(out)]), 2)
        self.assertEqual(len(self.queue.read_text().splitlines()), 1)
        self.assertTrue(stale.exists())

    def test_an_arm_linked_in_from_another_disk_is_copied(self):
        elsewhere = self.base / "disk2" / "t-solo" / "solo" / "repo" / "1" / "f1_f2"
        elsewhere.mkdir(parents=True)
        (elsewhere / "result.json").write_text("{}\n")
        self.logs.mkdir()
        (self.logs / "t-solo").symlink_to(self.base / "disk2" / "t-solo")
        run = self.logs / "t-solo" / "solo" / "repo" / "1" / "f1_f2"
        self.write_queue([{"id": "a", "sample": {"sample": 8}, "status": "done", "run_dir": str(run)}])
        left = subset.copy_runs(subset.sampled(self.queue), self.logs, self.base / "out")
        self.assertEqual(left["outside_logs"], [])
        self.assertTrue((self.base / "out" / "t-solo" / "solo" / "repo" / "1" / "f1_f2" / "result.json").exists())

    def test_the_runner_loads_with_the_standard_librarys_queue(self):
        probe = ("import sys; sys.path.insert(0, sys.argv[1]); import subset; subset.load_runner(); "
                 "import queue; print(queue.__file__)")
        out = subprocess.run([sys.executable, "-c", probe, str(ROOT / "bench" / "cooperbench" / "session")],
                             capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("session", out.stdout)



def measured_agent(cache_read, output=1000):
    return {"input_tokens": 10, "cache_read_tokens": cache_read, "cache_write_tokens": 50000,
            "output_tokens": None, "output_tokens_lower_bound": output, "model": "claude-sonnet-5-5"}


UNMEASURED = dict.fromkeys(("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens",
                            "output_tokens_lower_bound", "model"))


def write_pair(logs, arm, task, agents, both_passed=True, merge=None):
    folder = Path(logs) / f"curse-1-{arm}" / "coop" / "repo_task" / str(task) / "f1_f2"
    folder.mkdir(parents=True)
    result = {"repo": "repo_task", "task_id": task, "features": [1, 2], "model": "claude-sonnet-5-5",
              "duration_seconds": 300.0, "agents": agents}
    (folder / "result.json").write_text(json.dumps(result))
    evaluation = {"both_passed": both_passed, **({"merge": merge} if merge else {})}
    (folder / "eval.json").write_text(json.dumps(evaluation))
    return folder


class SummaryCountsWhatWasMeasured(unittest.TestCase):
    """summary.py's figures: an agent with no counts at all leaves its
    pair out of the token and cost figures, a merge conflict is read from
    the status CooperBench's eval.json gives the merge, and a paired ratio
    is taken from the medians before rounding."""

    def setUp(self):
        self.logs = tempfile.mkdtemp(prefix="coordbench-summary-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.logs], check=False))

    def test_an_agent_without_counts_leaves_its_pair_unmeasured_not_halved(self):
        half = summary.pair_row("curse-1", "coop", write_pair(self.logs, "coop", 1, {
            "agent1": dict(UNMEASURED), "agent2": measured_agent(1_000_000)}))
        self.assertEqual((half["unmeasured_agents"], half["total_tokens"], half["cost_usd"]), (1, None, None))
        whole = summary.pair_row("curse-1", "coop", write_pair(self.logs, "coop", 2, {
            "agent1": measured_agent(1_000_000), "agent2": measured_agent(1_000_000)}))
        self.assertEqual(whole["unmeasured_agents"], 0)
        self.assertEqual(whole["total_tokens"], 2 * (10 + 1_000_000 + 50000 + 1000))
        arm = summary.summarize_arm([half, whole])
        self.assertEqual(arm["unmeasured_token_pairs"], 1)
        self.assertEqual(arm["tokens_per_pair_mean"]["total"], whole["total_tokens"], "the half-counted pair is left out")
        self.assertEqual(arm["priced_pairs"], 1)

    def test_a_measured_zero_is_a_count(self):
        zero = summary.tokens_of({"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                                  "output_tokens": 0})
        self.assertEqual(zero["input"] + zero["output"], 0)
        self.assertIsNone(summary.tokens_of(dict(UNMEASURED)))

    def test_a_merge_conflict_is_read_from_the_merge_status_cooperbench_writes(self):
        agents = {"agent1": measured_agent(1), "agent2": measured_agent(1)}
        rows = [summary.pair_row("curse-1", "team", write_pair(self.logs, "team", task, agents, merge=merge))
                for task, merge in ((1, {"status": "conflicts", "strategy": "solo-agent1"}),
                                    (2, {"status": "conflicts", "strategy": "naive"}),
                                    (3, {"status": "clean", "strategy": "naive"}),
                                    (4, {"status": "identical", "strategy": "skip-merge-identical"}),
                                    (5, None))]
        self.assertEqual([row["merge_conflict"] for row in rows], [True, True, False, False, None])
        self.assertEqual(summary.summarize_arm(rows)["merge_conflict_pairs"], 2)

    def test_ratios_come_from_the_medians_before_rounding(self):
        def row(task, cost, tokens=None):
            return {"repo": "r", "task": task, "features": "f1_f2", "agent_errors": 0, "passed": True,
                    "wall_seconds": 100.0, "agent_seconds_sum": 100.0, "total_tokens": tokens, "cost_usd": cost}
        solo = [row(1, 0.64, 100), row(2, 0.64, 300)]
        coop = [row(1, 1.16, 200), row(2, 1.16, None)]
        result = summary.paired(solo, coop)
        self.assertEqual(result["cost_ratio_b_over_a"], round(1.16 / 0.64, 3), "not 1.2 / 0.6 = 2.0")
        self.assertEqual(result["total_tokens_ratio_b_over_a"], 2.0, "over the shared pairs both arms measured")



class AnArmGivenByItsDirectory(unittest.TestCase):
    """run.py's --arm takes a directory, so an arm outside coordbench's
    tree (bench/cooperbench/arms/xblossom) is queued by its path: the
    item keeps the path for prepare, and its id, run name and logs go by
    the directory's name, as a built-in arm's go by its own."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-arm-path-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        dataset = self.tmp / "dataset" / "subsets"
        dataset.mkdir(parents=True)
        (dataset / "flash.json").write_text(json.dumps(
            {"tasks": [{"repo": "pallets_click_task", "task_id": 2068, "pairs": [[5, 11]]}]}))

    def test_the_queue_names_the_item_by_the_directory_and_keeps_the_path(self):
        arm = str(ROOT / "bench" / "cooperbench" / "arms" / "xblossom")
        queue_file = self.tmp / "q.jsonl"
        out = subprocess.run([sys.executable, str(ROOT / "bench" / "cooperbench" / "session" / "queue.py"),
                              "add", str(queue_file), "--tag", "xb1", "--arms", f"team,{arm}",
                              "--pairs", "pallets_click_task/2068/f5_f11",
                              "--dataset-dir", str(self.tmp / "dataset")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        items = [json.loads(line) for line in queue_file.read_text().splitlines() if line.strip()]
        by_arm = {item["arm"]: item for item in items}
        self.assertEqual(by_arm[arm]["run_name"], "xb1-xblossom")
        self.assertEqual(by_arm[arm]["id"], "xb1__xblossom__pallets_click_task__2068__f5_f11")
        self.assertEqual(by_arm["team"]["run_name"], "xb1-team")

    def test_the_lane_shows_the_agents_the_arm_by_its_name(self):
        item = {"id": "xb1__xblossom__r__1__f1_f2", "arm": "/somewhere/arms/xblossom", "repo": "r",
                "task": 1, "features": "1,2", "timeout_min": 45}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            lane.announce({"run_dir": "/x", "agents": []}, item)
        self.assertEqual(json.loads(printed.getvalue())["arm"], "xblossom")

    def test_bundle_takes_the_arms_it_is_given(self):
        logs = self.tmp / "logs"
        result = logs / "xb1-xblossom" / "team" / "r" / "1" / "f1_f2" / "result.json"
        result.parent.mkdir(parents=True)
        result.write_text("{}\n")
        out = self.tmp / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            bundle.main(["--logs", str(logs), "--tag", "xb1", "--out", str(out), "--arms", "team,xblossom"])
        self.assertTrue((out / "raw" / "xb1-xblossom" / "team" / "r" / "1" / "f1_f2" / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
