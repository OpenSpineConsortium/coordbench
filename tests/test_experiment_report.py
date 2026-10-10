"""The experiment's report counts a pair an agent left in Error apart from
the failures, and compares arms only on the pairs both scored, so these
pin both against a small tree shaped like CooperBench's logs. They also
pin the two shapes of result.json the report reads (CooperBench's solo
runner writes one "agent", everything else "agents") and the difference
between a null token count, which is unknown, and a measured zero."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "cooperbench"))

import report  # noqa: E402

MEASURED = {"input_tokens": 100, "output_tokens": 10}
UNKNOWN = {k: None for k in report.TOKEN_FIELDS}
ZEROS = {k: 0 for k in report.TOKEN_FIELDS}


def write_pair(logs, run, setting, pair, passed=None, error=False, usage=MEASURED):
    pair_dir = logs / run / setting / "pallets_click_task" / "2068" / pair
    pair_dir.mkdir(parents=True)
    status = "Error" if error else "Submitted"
    agents = {"agent1": {"status": status, **usage}}
    (pair_dir / "result.json").write_text(json.dumps({"agents": agents, "duration_seconds": 120}))
    if passed is not None:
        (pair_dir / "eval.json").write_text(json.dumps({"both_passed": passed}))
    return pair_dir


def write_cooperbench_solo_pair(logs, run, pair, status, passed):
    """A pair as CooperBench's own solo runner leaves it (runner/solo.py at
    63b9d44): one "agent" block with these keys, never an "agents" dict,
    and an eval.json even when the agent errored, since core.py evaluates
    every pair that ran."""
    pair_dir = logs / run / "solo" / "pallets_click_task" / "2068" / pair
    pair_dir.mkdir(parents=True)
    agent = {"status": status, "cost": 0.5, "steps": 7, "input_tokens": 300, "output_tokens": 40,
             "cache_read_tokens": 20, "cache_write_tokens": 5, "patch_lines": 0,
             "error": "boom" if status == "Error" else None}
    record = {"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "solo",
              "run_id": "abc12345", "run_name": run, "agent_framework": "claude_code", "model": "m",
              "started_at": "2026-10-09T10:00:00", "ended_at": "2026-10-09T10:02:00",
              "duration_seconds": 120.0, "agent": agent, "total_cost": 0.5, "total_steps": 7,
              "log_dir": str(pair_dir)}
    (pair_dir / "result.json").write_text(json.dumps(record))
    (pair_dir / "eval.json").write_text(json.dumps({"both_passed": passed}))
    return pair_dir


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)
        write_pair(self.logs, "flash-solo-t", "solo", "f1_f6", passed=True)
        write_pair(self.logs, "flash-solo-t", "solo", "f2_f10", passed=False)
        write_pair(self.logs, "flash-team-t", "team", "f1_f6", passed=False)
        write_pair(self.logs, "flash-team-t", "team", "f2_f10", error=True)

    def tearDown(self):
        self.tmp.cleanup()

    def arms(self):
        return {arm: report.arm_outcomes(self.logs, f"flash-{arm}-t") for arm in report.ARMS}

    def test_an_errored_pair_is_counted_apart_from_failures(self):
        text = report.render(self.arms())
        self.assertIn("| solo | 1/2 | 50% |", text)
        self.assertIn("| team | 0/1 | 0% |", text)
        self.assertIn("| 1 | 110 | 2.0 | 31/50 |", text)

    def test_arms_are_compared_on_the_pairs_both_scored(self):
        text = report.render(self.arms())
        self.assertIn("| solo vs team | 1 | 1 | 0 | 1.000 |", text)
        self.assertIn("| pallets_click_task/2068/f2_f10 | fail | - | - | error |", text)

    def test_the_statistics(self):
        low, high = report.wilson(31, 50)
        self.assertEqual((round(low), round(high)), (48, 74))
        self.assertAlmostEqual(report.mcnemar_exact(10, 2), 158 / 4096)
        self.assertEqual(report.mcnemar_exact(0, 0), 1.0)


class CooperBenchSoloShapeTest(unittest.TestCase):
    """CooperBench's solo runner writes "agent", not "agents"; the report
    must read that block, or the authors' solo baseline shows 0 tokens and
    its errored pairs are scored as failures while coordbench's are set
    apart."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_one_agent_block_is_read_for_tokens(self):
        pair_dir = write_cooperbench_solo_pair(self.logs, "flash-solo-cc", "f1_f6", "Submitted", True)
        outcome, usage = report.pair_outcome(pair_dir)
        self.assertEqual(outcome, "pass")
        self.assertEqual(usage["tokens"], 365)

    def test_an_errored_solo_agent_is_set_apart_despite_its_eval(self):
        pair_dir = write_cooperbench_solo_pair(self.logs, "flash-solo-cc", "f2_f10", "Error", False)
        self.assertEqual(report.pair_outcome(pair_dir)[0], "error")
        text = report.render({"solo": report.arm_outcomes(self.logs, "flash-solo-cc")})
        self.assertIn("| solo | 0/0 | 0% | 0-0% | 1 | 365 |", text)

    def test_a_result_without_either_block_has_no_agents(self):
        self.assertEqual(report.pair_agents({"agent": "solo"}), {})
        self.assertEqual(report.pair_agents({}), {})


class UnknownUsageTest(unittest.TestCase):
    """A driver that cannot measure usage writes null for the token counts,
    where a measured zero is 0. The report keeps the two apart: an unknown
    pair is left out of the mean and the cell is '-' when nothing is known."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_pair_whose_every_count_is_null_has_unknown_tokens(self):
        pair_dir = write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=UNKNOWN)
        self.assertIsNone(report.pair_outcome(pair_dir)[1]["tokens"])

    def test_measured_zeros_stay_a_zero(self):
        pair_dir = write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=ZEROS)
        self.assertEqual(report.pair_outcome(pair_dir)[1]["tokens"], 0)
        text = report.render({"team": report.arm_outcomes(self.logs, "r")})
        self.assertIn("| team | 1/1 | 100% | 21-100% | 0 | 0 | 2.0 |", text)

    def test_the_summary_shows_a_dash_when_no_pair_was_measured(self):
        write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=UNKNOWN)
        write_pair(self.logs, "r", "team", "f2_f10", passed=False, usage=UNKNOWN)
        text = report.render({"team": report.arm_outcomes(self.logs, "r")})
        self.assertIn("| team | 1/2 | 50% | 9-91% | 0 | - | 2.0 |", text)

    def test_the_summary_averages_over_the_measured_pairs_only(self):
        write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=UNKNOWN)
        write_pair(self.logs, "r", "team", "f2_f10", passed=True, usage={"input_tokens": 300, "output_tokens": 0})
        write_pair(self.logs, "r", "team", "f3_f4", passed=True, usage=MEASURED)
        text = report.render({"team": report.arm_outcomes(self.logs, "r")})
        self.assertIn("| team | 3/3 | 100% | 44-100% | 0 | 205 | 2.0 |", text)

    def test_one_measured_agent_makes_the_pair_known(self):
        pair_dir = self.logs / "r" / "team" / "pallets_click_task" / "2068" / "f1_f6"
        pair_dir.mkdir(parents=True)
        agents = {"agent1": {"status": "Submitted", **UNKNOWN}, "agent2": {"status": "Submitted", **MEASURED}}
        (pair_dir / "result.json").write_text(json.dumps({"agents": agents, "duration_seconds": 60}))
        self.assertEqual(report.pair_outcome(pair_dir)[1]["tokens"], 110)


if __name__ == "__main__":
    unittest.main()
