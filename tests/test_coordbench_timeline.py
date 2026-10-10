"""timeline.py answers where one run's time went, the harness or the
agents, from result.json, events.jsonl and eval.json alone, so these pin
its rows against a run directory written in the shape the runner and the
hub produce (timings for every phase, tool and tool_done events, Claude
Code's duration fields) and its graceful degradation against a run in the
pilots' older shape (no timings, no tool_done, no duration fields), with
no events.jsonl, with no eval.json and with a cut line in the events."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

import timeline  # noqa: E402

USAGE_NULLS = {"cost": None, "steps": None, "input_tokens": None, "output_tokens": None,
               "cache_read_tokens": None, "cache_write_tokens": None}


def timing(phase, seconds, agent=None):
    return {"phase": phase, "agent": agent, "started": "2026-10-09T21:05:00",
            "ended": "2026-10-09T21:05:01", "seconds": seconds}


def tool(agent, name, t):
    return {"kind": "tool", "agent": agent, "tool": name, "t": t}


def done(agent, name, t, duration_ms, blocked_ms=0, is_error=False, cancelled=False):
    return {"kind": "tool_done", "agent": agent, "tool": name, "t": t, "duration_ms": duration_ms,
            "blocked_ms": blocked_ms, "is_error": is_error, "cancelled": cancelled}


def agent_record(feature, role, status="Submitted", duration_ms=None, api_duration_ms=None):
    return {"feature_id": feature, "role": role, "status": status, **USAGE_NULLS, "patch_lines": 200,
            "error": None, "duration_ms": duration_ms, "api_duration_ms": api_duration_ms}


def contract_result():
    """result.json as run.py writes it after this round: timings for every
    phase and Claude Code's two durations per agent."""
    return {
        "repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "coop",
        "run_id": "team-2068-f1_f6-abc123", "run_name": "fixture", "agent_framework": "claude_code_subagent",
        "model": "claude-sonnet-5-5", "arm": "team", "driver": "claude-cli",
        "started_at": "2026-10-09T21:05:00", "ended_at": "2026-10-09T21:26:40", "duration_seconds": 1300.0,
        "agents": {"agent1": agent_record(1, "lead", duration_ms=598000, api_duration_ms=300000),
                   "agent2": agent_record(6, "member", duration_ms=499000, api_duration_ms=100000)},
        "total_cost": None, "total_steps": None, "metrics": {"tool_calls": {}},
        "timings": [timing("hub", 0.5), timing("run_create", 0.02), timing("git_remote", 1.0),
                    timing("shared_volume", 2.0), timing("provision", 30.0, "agent1"),
                    timing("provision", 31.0, "agent2"), timing("agent", 600.0, "agent1"),
                    timing("agent", 500.0, "agent2"), timing("close", 0.3), timing("score", 90.0),
                    timing("teardown", 5.0)],
    }


def contract_events():
    """Two agents' calls in the hub's shape, with a runner event and a
    message among them that the breakdown must leave alone."""
    return [
        {"kind": "task_create", "agent": "bench-runner", "task_id": "abc", "t": 0.0},
        tool("agent1", "list_agents", 10.0), done("agent1", "list_agents", 10.5, 500),
        tool("agent2", "task_list", 12.0), done("agent2", "task_list", 12.5, 500),
        tool("agent1", "wait_for_message", 20.0),
        done("agent1", "wait_for_message", 50.0, 30000, blocked_ms=29500),
        tool("agent2", "wait_for_message", 100.0),
        done("agent2", "wait_for_message", 160.0, 60000, blocked_ms=60000, cancelled=True),
        tool("agent2", "send_message", 200.0),
        {"kind": "message", "agent": "agent2", "to": "agent1", "chars": 12, "t": 200.0},
        done("agent2", "send_message", 200.5, 500),
        tool("agent1", "send_message", 400.0), done("agent1", "send_message", 400.5, 500, is_error=True),
        tool("agent1", "peek_messages", 410.0), done("agent1", "peek_messages", 410.5, 500),
    ]


def pilot_result():
    """result.json in the pilots' shape: no timings, no duration fields."""
    result = contract_result()
    del result["timings"]
    result.update(arm="coop", driver="session", duration_seconds=1057.73538)
    for record in result["agents"].values():
        del record["duration_ms"], record["api_duration_ms"]
    return result


def pilot_events():
    """The pilots' events: tool events without tool_done, and the receive
    events the hub emits beside them."""
    return [tool("agent1", "list_agents", 68.0), tool("agent1", "broadcast", 68.1),
            tool("agent1", "receive_messages", 114.5),
            {"kind": "receive", "agent": "agent1", "count": 1, "t": 114.5},
            tool("agent1", "send_message", 391.5), tool("agent2", "broadcast", 85.6),
            tool("agent2", "receive_messages", 398.4)]


def write_run(directory, result, events=None, evaluation=None):
    directory = Path(directory)
    (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
    if events is not None:
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    if evaluation is not None:
        (directory / "eval.json").write_text(json.dumps(evaluation), encoding="utf-8")
    return directory


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = timeline.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TimelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-timeline-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_dir(self, result, events=None, evaluation=None):
        directory = self.tmp / "run"
        directory.mkdir()
        return write_run(directory, result, events, evaluation)

    def lines(self, text):
        return text.splitlines()


class ContractShapeTest(TimelineCase):
    """A run written in the shape the runner and the hub produce now."""

    def setUp(self):
        super().setUp()
        self.dir = self.run_dir(contract_result(), contract_events(), {"both_passed": True, "error": None})
        self.code, self.out, self.err = run_main(str(self.dir))
        self.rows = self.lines(self.out)

    def test_prints_and_says_nothing_on_stderr(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.err, "")

    def test_header_names_the_run_its_verdict_and_its_wall_time(self):
        self.assertEqual(self.rows[0], "# pallets_click_task task 2068, features 1, 6: arm team, "
                                       "driver claude-cli, verdict pass, wall 1300.0 s")

    def test_infrastructure_lists_every_phase_but_the_agents_in_phase_order(self):
        self.assertIn("| phase | agent | seconds |", self.rows)
        start = self.rows.index("| phase | agent | seconds |") + 2
        self.assertEqual(self.rows[start:start + 10], [
            "| hub | - | 500 ms |",
            "| run_create | - | 20 ms |",
            "| git_remote | - | 1.0 s |",
            "| shared_volume | - | 2.0 s |",
            "| provision | agent1 | 30.0 s |",
            "| provision | agent2 | 31.0 s |",
            "| close | - | 300 ms |",
            "| score | - | 90.0 s |",
            "| teardown | - | 5.0 s |",
            "| sum of phases | - | 159.8 s |",
        ])
        self.assertNotIn("| agent | agent1 |", self.out)

    def test_hub_handling_time_is_duration_less_blocked_over_every_call(self):
        # agent1: 500 + (30000 - 29500) + 500 + 500 = 2000 ms; agent2: 500 + 0 + 500 = 1000 ms.
        self.assertIn("Hub handling time over every tool call (duration less blocked): 3.0 s.", self.rows)

    def test_agents_table_splits_own_time_into_model_and_local_when_api_time_is_known(self):
        header = ("| agent | wall | blocked on peers | in hub calls | tool calls | errors | cancelled "
                  "| model (api) | local (shell, edits) |")
        self.assertIn(header, self.rows)
        start = self.rows.index(header) + 2
        # agent1: 600 - 29.5 - 2.0 - 300 = 268.5; agent2: 500 - 60 - 1.0 - 100 = 339.0.
        self.assertEqual(self.rows[start:start + 2], [
            "| agent1 | 600.0 s | 29.5 s | 2.0 s | 4 | 1 | 0 | 300.0 s | 268.5 s |",
            "| agent2 | 500.0 s | 60.0 s | 1.0 s | 3 | 0 | 1 | 100.0 s | 339.0 s |",
        ])

    def test_idle_stretches_run_from_a_tool_done_to_the_next_tool_longest_first(self):
        header = "| agent | gap | from t | to t | tool before | tool after |"
        start = self.rows.index(header) + 2
        self.assertEqual(self.rows[start:start + 5], [
            "| agent1 | 350.0 s | 50.0 | 400.0 | wait_for_message | send_message |",
            "| agent1 | 9.5 s | 10.5 | 20.0 | list_agents | wait_for_message |",
            "| agent1 | 9.5 s | 400.5 | 410.0 | send_message | peek_messages |",
            "| agent2 | 87.5 s | 12.5 | 100.0 | task_list | wait_for_message |",
            "| agent2 | 40.0 s | 160.0 | 200.0 | wait_for_message | send_message |",
        ])
        self.assertIn("from one call's end (tool_done)", self.out)

    def test_json_carries_the_same_numbers(self):
        code, out, _ = run_main(str(self.dir), "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(report["run"]["verdict"], "pass")
        self.assertEqual(report["infrastructure"]["total_seconds"], 159.82)
        self.assertEqual(report["infrastructure"]["hub_seconds"], 3.0)
        agent1, agent2 = report["agents"]
        self.assertEqual((agent1["wall_seconds"], agent1["wall_source"], agent1["blocked_seconds"],
                          agent1["hub_seconds"], agent1["tool_calls"], agent1["errors"], agent1["cancelled"],
                          agent1["api_seconds"], agent1["own_seconds"]),
                         (600.0, "phase", 29.5, 2.0, 4, 1, 0, 300.0, 268.5))
        self.assertEqual(agent2["cancelled"], 1)
        self.assertEqual(report["idle"]["basis"], "tool_done")
        self.assertEqual(report["idle"]["agents"]["agent1"][0],
                         {"seconds": 350.0, "start": 50.0, "end": 400.0,
                          "before": "wait_for_message", "after": "send_message"})



class FallbacksTest(TimelineCase):
    def test_only_the_three_longest_gaps_of_an_agent_are_kept(self):
        """Five calls make four gaps of 5, 40, 20 and 10 seconds; the table
        holds the longest three, longest first, and leaves the 5 s one out."""
        events, t = [], 0.0
        for name, gap in (("a", 0.0), ("b", 5.0), ("c", 40.0), ("d", 20.0), ("e", 10.0)):
            t += gap
            events += [tool("agent1", name, t), done("agent1", name, t + 1.0, 1000)]
            t += 1.0
        result = contract_result()
        del result["agents"]["agent2"]
        result["timings"] = [r for r in result["timings"] if r["agent"] != "agent2"]
        _, out, _ = run_main(str(self.run_dir(result, events, {"both_passed": True})))
        rows = out.splitlines()
        start = rows.index("| agent | gap | from t | to t | tool before | tool after |") + 2
        self.assertEqual(rows[start:], [
            "| agent1 | 40.0 s | 7.0 | 47.0 | b | c |",
            "| agent1 | 20.0 s | 48.0 | 68.0 | c | d |",
            "| agent1 | 10.0 s | 69.0 | 79.0 | d | e |",
        ])

    def test_wall_falls_back_to_claude_codes_duration_when_the_agent_phase_is_missing(self):
        result = contract_result()
        result["timings"] = [t for t in result["timings"] if not (t["phase"] == "agent" and t["agent"] == "agent2")]
        code, out, _ = run_main(str(self.run_dir(result, contract_events(), {"both_passed": True})))
        self.assertEqual(code, 0)
        # 499000 ms -> 499.0 s; local = 499 - 60 - 1.0 - 100.
        self.assertIn("| agent2 | 499.0 s (from Claude Code) | 60.0 s | 1.0 s | 3 | 0 | 1 | 100.0 s | 338.0 s |", out)

    def test_an_agent_without_api_time_in_a_run_that_has_it_gets_no_local_time(self):
        result = contract_result()
        result["agents"]["agent2"]["api_duration_ms"] = None
        _, out, _ = run_main(str(self.run_dir(result, contract_events(), {"both_passed": True})))
        self.assertIn("| agent2 | 500.0 s | 60.0 s | 1.0 s | 3 | 0 | 1 | not recorded | not recorded |", out)

    def test_own_time_is_one_column_holding_the_model_time_when_no_api_time_is_known(self):
        result = contract_result()
        for record in result["agents"].values():
            record["api_duration_ms"] = None
        _, out, _ = run_main(str(self.run_dir(result, contract_events(), {"both_passed": True})))
        self.assertIn("| agent | wall | blocked on peers | in hub calls | tool calls | errors | cancelled "
                      "| own (tools, edits, thinking) |", out)
        self.assertIn("| agent1 | 600.0 s | 29.5 s | 2.0 s | 4 | 1 | 0 | 568.5 s |", out)

    def test_an_agent_in_error_makes_the_verdict_error(self):
        result = contract_result()
        result["agents"]["agent2"]["status"] = "Error"
        _, out, _ = run_main(str(self.run_dir(result, contract_events(), {"both_passed": False})))
        self.assertIn("verdict error,", out.splitlines()[0])

    def test_an_agent_only_the_events_name_gets_a_row(self):
        result = contract_result()
        del result["agents"]["agent2"]
        result["timings"] = [t for t in result["timings"] if t["agent"] != "agent2"]
        _, out, _ = run_main(str(self.run_dir(result, contract_events(), {"both_passed": True})))
        self.assertIn("| agent2 | not recorded | 60.0 s | 1.0 s | 3 | 0 | 1 | not recorded | not recorded |", out)


class PilotShapeTest(TimelineCase):
    """A run from before this round: result.json without timings or
    duration fields, events without tool_done."""

    def setUp(self):
        super().setUp()
        self.dir = self.run_dir(pilot_result(), pilot_events(), {"both_passed": False, "error": None})
        self.code, self.out, self.err = run_main(str(self.dir))
        self.rows = self.lines(self.out)

    def test_prints_without_a_crash_and_scores_the_verdict(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.err, "")
        self.assertEqual(self.rows[0], "# pallets_click_task task 2068, features 1, 6: arm coop, "
                                       "driver session, verdict fail, wall 1057.7 s")

    def test_infrastructure_and_hub_time_read_not_recorded(self):
        self.assertIn("Phase timings: not recorded (result.json has no timings).", self.rows)
        self.assertIn("Hub handling time over every tool call (duration less blocked): "
                      "not recorded (no tool_done events).", self.rows)
        self.assertNotIn("| phase | agent | seconds |", self.rows)

    def test_agents_keep_their_call_counts_and_nothing_else(self):
        self.assertIn("| agent | wall | blocked on peers | in hub calls | tool calls | errors | cancelled "
                      "| own (tools, edits, thinking) |", self.rows)
        self.assertIn("| agent1 | not recorded | not recorded | not recorded | 4 | not recorded | not recorded "
                      "| not recorded |", self.rows)
        self.assertIn("| agent2 | not recorded | not recorded | not recorded | 2 | not recorded | not recorded "
                      "| not recorded |", self.rows)

    def test_idle_stretches_fall_back_to_call_start_to_call_start_and_say_so(self):
        self.assertIn("No tool_done events: each gap runs from one call's start to the next call's start, "
                      "so it holds the earlier call's own duration too.", self.rows)
        header = "| agent | gap | from t | to t | tool before | tool after |"
        start = self.rows.index(header) + 2
        self.assertEqual(self.rows[start:start + 4], [
            "| agent1 | 277.0 s | 114.5 | 391.5 | receive_messages | send_message |",
            "| agent1 | 46.4 s | 68.1 | 114.5 | broadcast | receive_messages |",
            "| agent1 | 100 ms | 68.0 | 68.1 | list_agents | broadcast |",
            "| agent2 | 312.8 s | 85.6 | 398.4 | broadcast | receive_messages |",
        ])

    def test_json_marks_the_absent_fields_null(self):
        _, out, _ = run_main(str(self.dir), "--json")
        report = json.loads(out)
        self.assertIsNone(report["infrastructure"]["phases"])
        self.assertIsNone(report["infrastructure"]["hub_seconds"])
        self.assertEqual(report["idle"]["basis"], "tool")
        agent1 = report["agents"][0]
        self.assertEqual(agent1["tool_calls"], 4)
        for field in ("wall_seconds", "blocked_seconds", "hub_seconds", "errors", "cancelled",
                      "api_seconds", "own_seconds"):
            self.assertIsNone(agent1[field], field)


class MissingFilesTest(TimelineCase):
    def test_a_run_with_no_events_still_prints_what_result_json_holds(self):
        code, out, err = run_main(str(self.run_dir(contract_result(), None, {"both_passed": True})))
        self.assertEqual((code, err), (0, ""))
        rows = out.splitlines()
        self.assertEqual(rows[0], "# pallets_click_task task 2068, features 1, 6: arm team, "
                                  "driver claude-cli, verdict pass, wall 1300.0 s")
        self.assertIn("| sum of phases | - | 159.8 s |", rows)
        self.assertIn("Hub handling time over every tool call (duration less blocked): "
                      "not recorded (no tool_done events).", rows)
        self.assertIn("| agent1 | 600.0 s | not recorded | not recorded | not recorded | not recorded "
                      "| not recorded | 300.0 s | not recorded |", rows)
        self.assertIn("not recorded (no events.jsonl).", rows)

    def test_a_run_never_scored_has_no_verdict(self):
        _, out, _ = run_main(str(self.run_dir(contract_result(), contract_events())))
        self.assertIn("verdict not recorded,", out.splitlines()[0])

    def test_an_agent_with_one_call_has_no_gap_row_but_a_placeholder(self):
        events = [tool("agent1", "list_agents", 10.0), done("agent1", "list_agents", 10.5, 500)]
        result = contract_result()
        del result["agents"]["agent2"]
        result["timings"] = [t for t in result["timings"] if t["agent"] != "agent2"]
        _, out, _ = run_main(str(self.run_dir(result, events, {"both_passed": True})))
        self.assertIn("| agent1 | - | - | - | - | - |", out)

    def test_a_cut_events_line_is_skipped_and_counted_not_fatal(self):
        directory = self.run_dir(pilot_result(), pilot_events(), {"both_passed": True})
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 99, "t": 500.0, "kind": "to')
        code, out, err = run_main(str(directory))
        self.assertEqual(code, 0)
        self.assertIn("skipped 1 line(s) that are not events", err)
        self.assertIn("| agent1 | not recorded | not recorded | not recorded | 4 |", out)

    def test_an_unreadable_eval_json_leaves_the_verdict_unrecorded_and_says_so(self):
        directory = self.run_dir(contract_result(), contract_events())
        (directory / "eval.json").write_text("{not json", encoding="utf-8")
        code, out, err = run_main(str(directory))
        self.assertEqual(code, 0)
        self.assertIn("eval.json: cannot read", err)
        self.assertIn("verdict not recorded,", out.splitlines()[0])

    def test_a_directory_without_result_json_exits_2_and_says_which_file(self):
        code, out, err = run_main(str(self.tmp))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("result.json is not there to read", err)


class StandsAloneTest(TimelineCase):
    def test_a_copy_of_the_module_runs_alone_as_a_script(self):
        """The module reads JSON only, so a copy beside a run, with nothing
        of coordbench near it, prints the same breakdown."""
        alone = self.tmp / "alone"
        alone.mkdir()
        shutil.copy(COORDBENCH / "timeline.py", alone / "timeline.py")
        run_dir = self.run_dir(contract_result(), contract_events(), {"both_passed": True})
        completed = subprocess.run([sys.executable, "-I", str(alone / "timeline.py"), str(run_dir)],
                                   capture_output=True, text=True, cwd=str(alone))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("| agent1 | 600.0 s | 29.5 s | 2.0 s | 4 | 1 | 0 | 300.0 s | 268.5 s |", completed.stdout)


if __name__ == "__main__":
    unittest.main()
