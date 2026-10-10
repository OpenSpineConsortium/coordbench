"""The experiment's report reads CooperBench's runs and coordbench's alike
and sets each measure beside CooperBench's own, so these pin it against
small trees shaped like CooperBench's logs: the paper's rate (eval.json
over every attempted pair, an errored agent's pair scored like any
other) beside coordbench's rate on finished pairs; the two shapes of
result.json (CooperBench's solo runner writes one "agent", everything
else "agents"); a null token count, which is unknown, against a measured
zero; and, on one experiment of two models with Solo, coop, coop-nocomm
and coop-git runs, the coordination gap, the pass route, the conflict
rate, the cascade, the communication measures, the judge's labels, the
difficulty curves, the dataset split and the statistics."""

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


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def write_lines(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def write_pair(logs, run, setting, pair, passed=None, error=False, usage=MEASURED, task="2068"):
    pair_dir = logs / run / setting / "pallets_click_task" / task / pair
    status = "Error" if error else "Submitted"
    write_json(pair_dir / "result.json", {"agents": {"agent1": {"status": status, **usage}}, "duration_seconds": 120})
    if passed is not None:
        write_json(pair_dir / "eval.json", {"both_passed": passed})
    return pair_dir


def write_cooperbench_solo_pair(logs, run, pair, status, passed):
    """A pair as CooperBench's own solo runner leaves it (runner/solo.py at
    63b9d44): one "agent" block with these keys, never an "agents" dict,
    and an eval.json even when the agent errored, since core.py evaluates
    every pair that ran."""
    pair_dir = logs / run / "solo" / "pallets_click_task" / "2068" / pair
    agent = {"status": status, "cost": 0.5, "steps": 7, "input_tokens": 300, "output_tokens": 40,
             "cache_read_tokens": 20, "cache_write_tokens": 5, "patch_lines": 0,
             "error": "boom" if status == "Error" else None}
    record = {"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "solo",
              "run_id": "abc12345", "run_name": run, "agent_framework": "claude_code", "model": "m",
              "started_at": "2026-10-09T10:00:00", "ended_at": "2026-10-09T10:02:00",
              "duration_seconds": 120.0, "agent": agent, "total_cost": 0.5, "total_steps": 7,
              "log_dir": str(pair_dir)}
    write_json(pair_dir / "result.json", record)
    write_json(pair_dir / "eval.json", {"both_passed": passed})
    return pair_dir


def render(logs, runs, **options):
    arms = {}
    for label, run_name in runs:
        arms.setdefault(label, []).extend(report.read_run(logs, run_name, label))
    return report.render(arms, options=options)


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
        return {arm: report.arm_outcomes(self.logs, f"flash-{arm}-t", arm) for arm in report.ARMS}

    def test_the_papers_rate_counts_every_attempted_pair(self):
        text = report.render(self.arms())
        self.assertIn("| solo | - | 2 | 1 | 50.0% |", text)
        self.assertIn("| team | - | 2 | 0 | 0.0% | 0.0-65.8% | one task set |", text)

    def test_an_errored_pair_is_set_apart_on_finished_pairs(self):
        text = report.render(self.arms())
        self.assertIn("| 0/1 | 0.0% | 0.0-79.3% | 1 | 31/50 |", text)

    def test_arms_are_compared_on_the_pairs_both_scored(self):
        text = report.render(self.arms())
        self.assertIn("| solo vs team | 1 | 1 | 0 | 1.000 |", text)
        self.assertIn("| pallets_click_task/2068/f2_f10 | fail | error |", text)

    def test_the_statistics(self):
        low, high = report.wilson(31, 50)
        self.assertEqual((round(low), round(high)), (48, 74))
        self.assertAlmostEqual(report.mcnemar_exact(10, 2), 158 / 4096)
        self.assertEqual(report.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(report.fisher_exact(3, 1, 1, 3), 0.4857142857)
        self.assertAlmostEqual(report.fisher_exact(1, 9, 11, 3), 0.0027594562)
        self.assertEqual(report.mann_whitney([1, 2, 3], [4, 5, 6]), (0.0, 0.1))
        self.assertIsNone(report.mann_whitney([], [1]))
        self.assertEqual(report.midranks([3, 1, 3, 2]), [3.5, 1.0, 3.5, 2.0])

    def test_the_normal_approximation_is_close_to_the_exact_test(self):
        first, second = list(range(0, 36, 2)), list(range(5, 41, 2))
        exact = report.mann_whitney(first, second)[1]
        u = report.mann_whitney(first, second)[0]
        normal = report.normal_mann_whitney_p(u, 18, 18, report.midranks(first + second))
        self.assertAlmostEqual(exact, normal, delta=0.02)

    def test_the_bootstrap_resamples_task_sets(self):
        self.assertIsNone(report.bootstrap_rate([(1, 2)]))
        self.assertEqual(report.bootstrap_rate([(1, 2), (1, 2)]), (50.0, 50.0))
        low, high = report.bootstrap_rate([(0, 2), (2, 2), (1, 2)])
        self.assertLessEqual(low, 50.0)
        self.assertGreaterEqual(high, 50.0)
        self.assertEqual(report.bootstrap_rate([(0, 2), (2, 2), (1, 2)], seed=3),
                         report.bootstrap_rate([(0, 2), (2, 2), (1, 2)], seed=3))

    def test_cooperbenchs_three_rates(self):
        pair_dir = write_pair(self.logs, "r", "coop", "f3_f4", passed=False)
        write_json(pair_dir / "eval.json", {"both_passed": False, "error": "sandbox"})
        write_pair(self.logs, "r", "coop", "f5_f6", passed=True)
        write_pair(self.logs, "r", "coop", "f7_f8", passed=False)
        write_pair(self.logs, "r", "coop", "f8_f9")
        text = render(self.logs, [("coop", "r")])
        self.assertIn("| coop | - | 50.0% | 33.3% | 25.0% |", text)


class CooperBenchSoloShapeTest(unittest.TestCase):
    """CooperBench's solo runner writes "agent", not "agents"; the report
    must read that block, or the authors' solo baseline shows 0 tokens and
    never sees its agent in Error."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_one_agent_block_is_read_for_tokens(self):
        pair = report.read_pair(write_cooperbench_solo_pair(self.logs, "flash-solo-cc", "f1_f6", "Submitted", True),
                                "solo", "flash-solo-cc")
        self.assertTrue(pair.passed)
        self.assertEqual(pair.tokens, 365)

    def test_an_errored_solo_agent_is_scored_by_its_eval_and_set_apart_when_finished(self):
        pair = report.read_pair(write_cooperbench_solo_pair(self.logs, "flash-solo-cc", "f2_f10", "Error", True),
                                "solo", "flash-solo-cc")
        self.assertTrue(pair.passed)
        self.assertEqual(pair.finished_outcome, "error")
        text = render(self.logs, [("solo", "flash-solo-cc")])
        self.assertIn("| solo | m | 1 | 1 | 100.0% |", text)
        self.assertIn("| 0/0 | - | - | 1 | 24/50 |", text)
        self.assertIn("| solo | m | 1 | 365 | 0.50 |", text)
        self.assertIn("pass (agent Error)", text)

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

    def pair(self, *arguments, **keywords):
        return report.read_pair(write_pair(self.logs, *arguments, **keywords), "team", "r")

    def test_a_pair_whose_every_count_is_null_has_unknown_tokens(self):
        self.assertIsNone(self.pair("r", "team", "f1_f6", passed=True, usage=UNKNOWN).tokens)

    def test_measured_zeros_stay_a_zero(self):
        self.assertEqual(self.pair("r", "team", "f1_f6", passed=True, usage=ZEROS).tokens, 0)
        self.assertIn("| team | - | 1 | 0 | - | - | 2.0 | 2.0 |", render(self.logs, [("team", "r")]))

    def test_the_summary_shows_a_dash_when_no_pair_was_measured(self):
        write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=UNKNOWN)
        write_pair(self.logs, "r", "team", "f2_f10", passed=False, usage=UNKNOWN)
        self.assertIn("| team | - | 2 | - | - | - | 2.0 | 2.0 |", render(self.logs, [("team", "r")]))

    def test_the_summary_averages_over_the_measured_pairs_only(self):
        write_pair(self.logs, "r", "team", "f1_f6", passed=True, usage=UNKNOWN)
        write_pair(self.logs, "r", "team", "f2_f10", passed=True, usage={"input_tokens": 300, "output_tokens": 0})
        write_pair(self.logs, "r", "team", "f3_f4", passed=True, usage=MEASURED)
        self.assertIn("| team | - | 3 | 205 |", render(self.logs, [("team", "r")]))

    def test_one_measured_agent_makes_the_pair_known(self):
        pair_dir = self.logs / "r" / "team" / "pallets_click_task" / "2068" / "f1_f6"
        agents = {"agent1": {"status": "Submitted", **UNKNOWN}, "agent2": {"status": "Submitted", **MEASURED}}
        write_json(pair_dir / "result.json", {"agents": agents, "duration_seconds": 60})
        self.assertEqual(report.read_pair(pair_dir, "team", "r").tokens, 110)


# One experiment: two models, five pairs over two task sets, and the runs
# CooperBench's paper compares.
KEYS = ("pallets_click_task/2068/f1_f2", "pallets_click_task/2068/f1_f3", "pallets_click_task/2068/f2_f3",
        "pallets_jinja_task/1465/f1_f2", "pallets_jinja_task/1465/f1_f3")
SOLO = {"m1": (True, True, False, True, False), "m2": (True, False, False, False, False)}
CLEAN, CONFLICT = {"status": "clean", "strategy": "naive"}, {"status": "conflicts", "strategy": "naive"}
LEAD = {"status": "conflicts", "strategy": "solo-agent1"}
# (merge, both_passed) per key; None for a pair that was never scored.
COOP = {"m1": ((CLEAN, True), (LEAD, True), (CONFLICT, False), (CLEAN, False), None),
        "m2": ((CLEAN, True), (CONFLICT, False), (CONFLICT, False), (CLEAN, False), (CLEAN, False))}
NOCOMM = {"m1": ((CONFLICT, False), (CONFLICT, False), (CONFLICT, False), (CLEAN, True), (CONFLICT, False))}
GIT = {"m1": ((CLEAN, True), (CLEAN, True), (CLEAN, True), (CLEAN, False), (CONFLICT, False))}

STORE = {"messages": [
    {"id": 1, "sender_id": "agent_1", "recipient_id": "agent_2", "from": "agent1", "to": "agent2",
     "recipients": ["agent2"], "message_ids": [5], "send_id": 4,
     "content": "I will edit src/click/termui.py lines 10-20 and L30."},
    {"id": 2, "sender_id": "agent_2", "recipient_id": "broadcast", "from": "agent2", "to": "broadcast",
     "recipients": ["agent1"], "message_ids": [8], "send_id": 7, "content": "Which file? See core.py:12?"},
    {"id": 3, "sender_id": "agent_1", "recipient_id": "agent_2", "from": "agent1", "to": "agent2",
     "recipients": ["agent2"], "message_ids": [11], "send_id": 10, "content": "Done, updated termui.py"}],
    "export_timestamp": None, "total_messages": 3}
EVENTS = [{"seq": 0, "t": 0.0, "kind": "run_started", "agent": "hub"},
          {"seq": 4, "t": 1.0, "kind": "tool", "agent": "agent1", "tool": "send_message", "via": "mcp"},
          {"seq": 5, "t": 1.0, "kind": "message", "agent": "agent1", "to": "agent2", "content": "x",
           "message_id": 5, "send_id": 4},
          {"seq": 7, "t": 2.0, "kind": "tool", "agent": "agent2", "tool": "send_message", "via": "shell"},
          {"seq": 8, "t": 2.0, "kind": "message", "agent": "agent2", "to": "agent1", "content": "y",
           "message_id": 8, "send_id": 7},
          {"seq": 9, "t": 2.5, "kind": "tool", "agent": "agent2", "tool": "receive_messages", "via": "shell"}]
STREAM = [{"type": "system", "subtype": "init"},
          {"type": "assistant", "message": {"id": "a", "content": [
              {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
          {"type": "assistant", "message": {"id": "b", "content": [
              {"type": "tool_use", "id": "t2", "name": "mcp__coordbench__send_message", "input": {}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2"}]}},
          {"type": "assistant", "parent_tool_use_id": "t9", "message": {"id": "c", "content": [
              {"type": "tool_use", "id": "t3", "name": "Read", "input": {}}]}}]
CHAINED = {"command": "coop-send agent1 'y'; coop-recv"}
SESSION = [{"type": "assistant", "isSidechain": False, "message": {"id": "d", "content": [
               {"type": "tool_use", "id": "u1", "name": "Bash", "input": CHAINED}]}},
           {"type": "user", "isSidechain": False, "message": {"content": [
               {"type": "tool_result", "tool_use_id": "u1"}]}},
           {"type": "assistant", "isSidechain": True, "message": {"id": "e", "content": [
               {"type": "tool_use", "id": "u2", "name": "Read", "input": {}}]}}]


def cascade(merge, passed):
    """A cascade block as cascade.py writes it, for one 63b9d44 outcome:
    a conflict that union resolves into a pass, else the naive result."""
    conflict = merge["status"] == "conflicts"
    union = {"ran": conflict, "clean": True, "f1": conflict, "f2": conflict} if conflict else {"ran": False}
    return {"naive": {"conflict": conflict, "f1": not conflict and passed, "f2": not conflict and passed,
                      "conflict_sections": 1 if conflict else 0},
            "union": union, "llm": {"ran": False, "reason": "no endpoint configured"},
            "agent_alone": {"agent1": {"f1": True, "f2": merge["strategy"] == "solo-agent1"},
                            "agent2": {"f1": True, "f2": False}},
            "route": "union" if conflict else ("naive" if passed else "none"),
            "verdict_63b9d44": {"both_passed": passed, "test_filter": "63b9d44"},
            "verdict_paper_era": {"both_passed": conflict or passed, "test_filter": "59bbf65"}}


def write_experiment(logs):
    for model, outcomes in SOLO.items():
        for key, passed in zip(KEYS, outcomes):
            pair_dir = logs / f"solo-{model}" / "solo" / key
            write_json(pair_dir / "result.json", {"model": model, "setting": "solo", "arm": "solo",
                                                  "agent": {"status": "Submitted"}, "duration_seconds": 60})
            write_json(pair_dir / "eval.json", {"both_passed": passed, "merge": None})
    for run, table, arm in (("coop", COOP, "coop"), ("nocomm", NOCOMM, "coop-nocomm"), ("git", GIT, "coop-git")):
        for model, outcomes in table.items():
            for key, outcome in zip(KEYS, outcomes):
                write_multi_pair(logs / f"{run}-{model}" / "coop" / key, model, arm, outcome)
    first = logs / "coop-m1" / "coop" / KEYS[0]
    write_json(first / "conversation_store.json", STORE)
    write_lines(first / "events.jsonl", EVENTS)
    write_lines(first / "agent1_stream.jsonl", STREAM)
    write_lines(first / "agent2_session.jsonl", SESSION)
    write_json(first / "judge.json", {
        "backend": "fake", "model": "judge-1", "prompt_variant": "appF-7",
        "commerrors": {"issue": {"code": "C4a", "group": "Repetition", "description": "d"},
                       "evidence": ["a", "b"], "verified": [True, True]},
        "speech_acts": {"5": "Plan", "8": "Question", "11": "Update"},
        "heuristics": {"near_duplicates": [[1, 3, 0.95, True]], "unanswered_questions": []},
        "emergent": {"negotiation": {"present": True, "evidence": []}}})
    failed = logs / "coop-m1" / "coop" / KEYS[2]
    write_json(failed / "conversation_store.json", {"messages": [
        {"from": "agent2", "recipients": ["agent1"], "message_ids": [3], "content": "Can you take parse()?"}]})
    write_json(failed / "judge.json", {
        "backend": "fake", "model": "judge-1", "prompt_variant": "appF-7",
        "commerrors": {"issue": None, "evidence": [], "verified": []},
        "speech_acts": {"3": "Question"},
        "symptoms": {"level": "coordination", "primary": "Work overlap", "secondary": ["Repetition"],
                     "family": "Structural", "evidence": []}})
    write_json(logs / "coop-m1" / "coop" / KEYS[3] / "judge.json", {
        "backend": "fake", "model": "judge-1", "prompt_variant": "symptoms-r1",
        "calls": {"symptoms": {"backend": "fake", "model": "judge-1", "prompt_variant": "symptoms-r1"},
                  "commerrors": {"backend": "fake", "model": "judge-0", "prompt_variant": "appF-8"}},
        "symptoms": {"level": "implementation", "primary": None, "secondary": [], "evidence": []}})


def write_multi_pair(pair_dir, model, arm, outcome):
    agents = {"agent1": {"status": "Submitted", "cost": 0.25, "cost_source": "claude code"},
              "agent2": {"status": "Submitted", "cost": 0.25, "cost_source": "claude code"}}
    write_json(pair_dir / "result.json", {"model": model, "setting": "coop", "arm": arm, "agents": agents,
                                          "total_cost": 0.5, "duration_seconds": 300})
    if outcome is None:
        return
    merge, passed = outcome
    evaluation = {"both_passed": passed, "merge": merge, "feature1": {"passed": passed}, "feature2": {"passed": passed}}
    if arm == "coop":
        evaluation["cascade"] = cascade(merge, passed)
    write_json(pair_dir / "eval.json", evaluation)


class HeuristicsAlone(unittest.TestCase):
    """judge.py heuristics calls no model and writes calls {}: the report
    shows its cross-checks and names no judge."""

    def test_a_judge_json_of_heuristics_alone_names_no_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            pair = Path(tmp) / "coop-h" / "coop" / KEYS[0]
            write_multi_pair(pair, "m1", "coop", ({"status": "clean", "strategy": "naive"}, True))
            write_json(pair / "judge.json", {"format": "coordbench-judge/1", "calls": {},
                                             "heuristics": {"overlapping_hunks": [{"file": "a.py"}]}})
            text = render(Path(tmp), [("coop", "coop-h")])
        failures = text.split("## Failure modes")[1].split("\n## ")[0]
        self.assertIn("none called, the free heuristics only", failures)
        self.assertNotIn("- / - / -", failures)
        self.assertIn("overlapping_hunks", failures)


class ExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.logs = Path(cls.tmp.name)
        write_experiment(cls.logs)
        runs = [("solo", "solo-m1"), ("solo", "solo-m2"), ("coop", "coop-m1"), ("coop", "coop-m2"),
                ("coop-nocomm", "nocomm-m1"), ("coop-git", "git-m1")]
        cls.text = render(cls.logs, runs)
        cls.sections = {}
        for chunk in cls.text.split("\n## ")[1:]:
            title, _, body = chunk.partition("\n")
            cls.sections[title] = body

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rates_are_per_arm_and_model_with_a_pooled_row(self):
        rates = self.sections["Pass rates"]
        self.assertIn("| coop | m1 | 5 | 2 | 40.0% |", rates)
        self.assertIn("| coop | m2 | 5 | 1 | 20.0% |", rates)
        self.assertIn("| coop | pooled | 10 | 3 | 30.0% |", rates)
        self.assertIn("| solo | pooled | 10 | 4 | 40.0% |", rates)
        self.assertIn("| 2/4 | 50.0% |", rates)

    def test_the_coordination_gap_is_taken_on_the_pairs_both_scored(self):
        gap = self.sections["Coordination gap"]
        self.assertIn("| coop | m1 | solo | 4 | 75.0% | 50.0% | 25.0 | 33% | 1 | 0 | 1.000 |", gap)
        self.assertIn("| coop | pooled | solo | 9 |", gap)

    def test_the_git_ablation(self):
        git = self.sections["Git ablation"]
        self.assertIn("| m1 | 40.0% | 60.0% | 60.0% | +0.0 | -20.0 | +20.0 | 4 | 1.000 |", git)

    def test_the_pass_route(self):
        route = self.sections["Pass route"]
        self.assertIn("| coop | m1 | 2 | 1 | 0 | 1 | 0 | naive 1, none 1, union 2 | 1/2, 1/2 |", route)

    def test_the_conflict_rate_and_the_semantic_failures(self):
        conflicts = self.sections["Naive-merge conflicts"]
        self.assertIn("| coop | m1 | 4 | 2 | 50.0% |", conflicts)
        self.assertIn("| 2 | 1 | 50.0% |", conflicts)
        self.assertIn("| coop vs coop-nocomm | m1 | 4 | 0 | 1 | 1.000 |", conflicts)

    def test_the_cascade_is_cumulative(self):
        cascade = self.sections["Merge cascade (Table 6)"]
        self.assertIn("| coop | m1 | 4 | 25.00 | 75.00 (+50.0) | not run | 75.00 | 50.00 |", cascade)
        self.assertIn("The learned resolver did not run: no endpoint configured.", cascade)
        self.assertIn("verdict_paper_era: 59bbf65", cascade)
        self.assertIn("| coop | m1 | 4 | 1 | 0 | 4 |", cascade)

    def test_turns_and_the_overhead(self):
        communication = self.sections["Communication"]
        self.assertIn("| coop | m1 | 2 | 2 | 2.0 | 2.0 | 66.7% | 100.0% | 66.7% | 1 |", communication)

    def test_speech_acts_and_first_turn_planning(self):
        acts = self.sections["Speech acts"]
        self.assertIn("| coop | m1 | judge | 2 | 1 | 1 (33.3%) | 2 (33.3%) | 0 (0.0%) | 1 (33.3%) | 0 (0.0%) |", acts)
        self.assertIn("| coop | m1 | judge | 1 | 1.00 | 1.00 | 1 | 0.00 | 0.00 |", acts)
        self.assertIn("| coop | m1 | judge | 2 | 0/1 (0.0%) | 1/1 (100.0%) | 1.000 |", acts)
        self.assertIn("| judge | 0.05 | 0/1 (0.0%) | 0/0 (-) | 1.000 |", acts)

    def test_concrete_references(self):
        references = self.sections["Concrete references"]
        self.assertIn("| coop | m1 | passed / failed | 1 / 1 | 3.0 / 0.0 | 1.000 | 3.0 / 0.0 | 1.000 |", references)

    def test_the_judges_labels(self):
        failures = self.sections["Failure modes"]
        self.assertIn("fake / judge-1 / appF-7", failures)
        # judge.py's calls name each section's judge, which the top level does not.
        self.assertIn("commerrors: fake / judge-0 / appF-8;", failures)
        self.assertIn("symptoms: fake / judge-1 / symptoms-r1.", failures)
        self.assertIn("| coop | m1 | 2 | 1 (50.0%) | 0 (0.0%) | 0 (0.0%) | 1 (50.0%, 9.5-90.5%) | 1/2 (50.0%) |",
                      failures)
        self.assertIn("| coop | m1 | 2 | 1 | 1 | 50.0% |", failures)
        self.assertIn("| Work overlap | 1 (100.0%; 50.0%) |", failures)
        self.assertIn("| Structural | 1 (100.0%; 50.0%) |", failures)
        self.assertIn("| coop | m1 | 1 | 1 | 0 |", failures)
        self.assertIn("| coop | m1 | 1 | 1 |", failures)

    def test_the_difficulty_curves(self):
        found = self.sections["Difficulty (App C)"]
        self.assertIn("2 Solo model(s): m1, m2", found)
        self.assertIn("| coop | m1 | 4 | 3 | 0.05-0.95 |", found)
        self.assertIn("| coop | pooled |", found)
        self.assertNotIn("| coop-git | pooled |", found)

    def test_every_pair_carries_the_route_letter(self):
        pairs = self.sections["Every pair"]
        self.assertIn("| pallets_click_task/2068/f1_f3 | pass | fail | pass L | fail | fail | pass N |", pairs)
        self.assertIn("| pallets_jinja_task/1465/f1_f3 | fail | fail | error | fail | fail | fail |", pairs)

    def test_a_missing_dataset_says_not_recorded(self):
        self.assertIn("not recorded: no CooperBench dataset found", self.sections["Dataset"])


class RunsTableTest(unittest.TestCase):
    """The Runs table reads config.json and summary.json, which a run from
    before them lacks, and says "not recorded" for what is missing."""

    def test_the_run_level_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            pair_dir = write_pair(logs, "r", "coop", "f1_f2", passed=True)
            result = json.loads((pair_dir / "result.json").read_text())
            result["agents"]["agent1"]["model_differs"] = True
            result.update(model="m", arm="coop", driver="session")
            write_json(pair_dir / "result.json", result)
            write_json(logs / "r" / "config.json", {
                "isolation": "advisory", "provenance": {"cooperbench": {"commit": "63b9d44aaaaaaaaaa"},
                                                        "dataset": {"git_tree": "0123456789abcdef"}}})
            write_json(logs / "r" / "summary.json", {"pass_rate": 1.0, "total_time_seconds": 600})
            write_pair(logs, "old", "coop", "f1_f2", passed=False)
            runs = [("coop", "r", report.read_run(logs, "r", "coop"), logs),
                    ("old", "old", report.read_run(logs, "old", "old"), logs)]
            text = report.render({label: pairs for label, _, pairs, _ in runs}, runs)
        self.assertIn("| coop | r | m | coop | session | coop | 1 | - | 63b9d44aaa | 0123456789 | advisory | 1 "
                      "| 100.0% | 10.0 |", text)
        self.assertIn("| old | old | - | not recorded | not recorded | coop | 1 | - | not recorded | not recorded "
                      "| not recorded | not recorded | - | - |", text)


class ConversationSourceTest(unittest.TestCase):
    """The messages come from conversation_store.json, else events.jsonl
    (by send_id, or by adjacency in a record from before it), else
    CooperBench's conversation.json; with none, nothing is recorded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "r" / "coop" / "repo" / "1" / "f1_f2"
        write_json(self.dir / "result.json", {"setting": "coop"})

    def tearDown(self):
        self.tmp.cleanup()

    def sends(self):
        return report.pair_sends(report.read_pair(self.dir, "coop", "r"))

    def test_nothing_recorded(self):
        self.assertIsNone(self.sends())

    def test_a_broadcast_is_one_send_by_its_send_id(self):
        write_lines(self.dir / "events.jsonl", [
            {"kind": "tool", "agent": "agent1", "tool": "broadcast"},
            {"kind": "message", "agent": "agent1", "to": "agent2", "content": "hi", "message_id": 2, "send_id": 1},
            {"kind": "message", "agent": "agent1", "to": "agent3", "content": "hi", "message_id": 3, "send_id": 1}])
        sends, text = self.sends()
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0].recipients, ["agent2", "agent3"])
        self.assertEqual(sends[0].message_ids, [2, 3])
        self.assertTrue(text)

    def test_an_old_record_groups_adjacent_deliveries_and_has_no_text(self):
        write_lines(self.dir / "events.jsonl", [
            {"kind": "tool", "agent": "agent1", "tool": "broadcast"},
            {"kind": "message", "agent": "agent1", "to": "agent2", "chars": 2},
            {"kind": "message", "agent": "agent1", "to": "agent3", "chars": 2},
            {"kind": "tool", "agent": "agent1", "tool": "send_message"},
            {"kind": "message", "agent": "agent1", "to": "agent2", "chars": 3}])
        sends, text = self.sends()
        self.assertEqual([len(s.recipients) for s in sends], [2, 1])
        self.assertFalse(text)

    def test_cooperbenchs_conversation_json(self):
        write_json(self.dir / "conversation.json", [
            {"from": "agent1", "to": "agent2", "message": "hi", "timestamp": 1.0},
            {"from": "agent1", "to": "agent3", "message": "hi", "timestamp": 1.0},
            {"from": "agent2", "to": "agent1", "message": "ok", "timestamp": 2.0}])
        sends, text = self.sends()
        self.assertEqual([s.sender for s in sends], ["agent1", "agent2"])
        self.assertEqual(report.speaker_runs(sends), 2)

    def test_a_one_line_stream_falls_back_to_the_session_transcript(self):
        write_lines(self.dir / "agent1_stream.jsonl", [{"type": "coordbench_session", "agent": "agent1"}])
        write_lines(self.dir / "agent1_session.jsonl", SESSION)
        write_lines(self.dir / "agent2_stream.jsonl", STREAM)
        overhead = report.pair_overhead(report.read_pair(self.dir, "coop", "r"))
        self.assertEqual(overhead, {"calls": 3, "results": 3, "sends": 2, "receives": 1})

    def test_no_transcript_leaves_the_overhead_unknown(self):
        write_lines(self.dir / "agent1_stream.jsonl", [{"type": "coordbench_session", "agent": "agent1"}])
        self.assertIsNone(report.pair_overhead(report.read_pair(self.dir, "coop", "r")))

    def test_reference_counts(self):
        self.assertEqual(report.references("see lines 3-4, L7 and a/b.py:9 in a/b.py, c.json, e.g. x"), (3, 3))


class PublishedTest(unittest.TestCase):
    """The website's index is read over the rows with results, with typst
    mapped to typst_task, so coordbench's pairs meet the paper models'."""

    def test_published_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            write_json(path, {"tasks": [
                {"repo": "typst", "taskId": "task6554", "f1": "feature1", "f2": "feature2",
                 "results": {"claude": {"passed": True, "hasConflict": False},
                             "gpt5": {"passed": True, "hasConflict": None, "missing": True}}}]})
            found = report.published_outcomes(path)
        self.assertEqual(found, {"claude": {"typst_task/6554/f1_f2": (True, False)}})


if __name__ == "__main__":
    unittest.main()
