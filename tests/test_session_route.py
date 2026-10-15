"""The session route's lane, bundle and subset, the parts a run's
accidents reach: what the finish does about a returned agent's marker,
which files a results directory takes, and which runs a sample's logs
take."""
import json
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
            args = SimpleNamespace(run_dir=str(run_dir), returned=["agent1"], dead=None)
            with mock.patch.object(lane.subprocess, "run", side_effect=AssertionError("run.py was called")):
                verdict = lane.finish_pair(args)
            self.assertEqual(verdict["harness"]["gone_workspaces"], ["agent2"])


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
    """A sample's logs hold the runs of the items that carry a sample
    record, without the transcripts and the archives, and none of the
    tag's other runs."""

    def test_sampled_runs_are_copied_without_transcripts_and_others_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            sampled = logs / "t-solo" / "solo" / "repo" / "1" / "f1_f2"
            other = logs / "t-solo" / "solo" / "repo" / "2" / "f1_f2"
            for run in (sampled, other):
                run.mkdir(parents=True)
                (run / "result.json").write_text("{}\n")
                (run / "agent1_session.jsonl").write_text("{}\n")
                (run / "previous-20261015T120000.tar.gz").write_bytes(b"\x1f\x8b")
            queue = Path(tmp) / "q.jsonl"
            queue.write_text("".join(json.dumps(item) + "\n" for item in [
                {"id": "a", "sample": {"sample": 8}, "run_dir": str(sampled)},
                {"id": "b", "run_dir": str(other)},
                {"id": "c", "sample": {"sample": 8}},
                {"id": "d", "sample": {"sample": 8}, "run_dir": str(logs / "t-solo" / "solo" / "repo" / "3" / "f1_f2")}]))
            out = Path(tmp) / "out"
            items = subset.sampled(queue)
            missing = subset.copy_runs(items, logs, out)
            copied = sorted(path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file())
        self.assertEqual([item["id"] for item in items], ["a", "d"])
        self.assertEqual(missing, ["d"])
        self.assertEqual(copied, ["t-solo/solo/repo/1/f1_f2/result.json"])


if __name__ == "__main__":
    unittest.main()
