"""export.py turns a pair directory into the trajectory CooperBench's own
viewer reads and coordbench's comparison viewer reads, so these pin it
against tests/fixtures/export/coop_pair, a coop pair under the claude-cli
driver in the shapes the runner, the hub and Claude Code write: a session
transcript per agent (with a subagent's sidechain inside agent1's), the
hub's events with run_started, message ids and receipts, eval.json with
the cascade block and a judge.json. They check the step mapping block by
block, the hub join and its warnings, the fallbacks for a run with only a
stream or only the hub's record, the website's index merge, the legacy
aggregate and the cause files, and that the vendored parser is still
CooperBench's."""

import ast
import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coordbench"))

import export  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "export" / "coop_pair"
EPOCH = 1791580000.0
NO_DATASET = "/nonexistent-cooperbench"
EXPECTED_STEPS = [
    ("agent_1", "think", "think", "think"),
    ("agent_1", "execute_bash", "bash", "run"),
    ("agent_1", "openhands_comm_send", "communication", "SendMessageAction"),
    ("agent_2", "openhands_comm_send", "communication", "SendMessageAction"),
    ("agent_1", "str_replace_editor", "view", "read"),
    ("agent_2", "openhands_comm_send", "communication", "SendMessageAction"),
    ("agent_1", "str_replace_editor", "edit", "edit"),
    ("agent_1", "str_replace_editor", "create", "edit"),
    ("agent_1", "execute_bash", "find_grep", "run"),
    ("agent_1", "execute_bash", "find_grep", "run"),
    ("agent_1", "task_tracker", "task_tracking", "task_tracking"),
    ("agent_1", "openhands_comm_get", "communication", "ReceiveMessageAction"),
    ("agent_1", "openhands_comm_get", "communication", "ReceiveMessageAction"),
    ("agent_2", "openhands_comm_get", "communication", "ReceiveMessageAction"),
    ("agent_1", "openhands_comm_send", "communication", "SendMessageAction"),
    ("agent_1", "execute_bash", "bash", "run"),
    ("agent_1", "task", "other", "task"),
    ("agent_1", "execute_bash", "other", "run"),
    ("agent_1", "openhands_comm_get", "communication", "ReceiveMessageAction"),
    ("agent_1", "openhands_comm_send", "communication", "SendMessageAction"),
    ("agent_1", "coop_peek", "other", "coop_peek"),
    ("agent_1", "finish", "submit", "FinishAction"),
    ("agent_2", "finish", "submit", "FinishAction"),
]


def build(path=FIXTURE, **options):
    return export.build_trajectory(path, NO_DATASET, **options)


def steps_named(trajectory, name):
    return [s for s in trajectory["steps"] if s["toolName"] == name]


def at(offset):
    return export.utc_iso(export.parse_time(EPOCH + offset))


class PairCopy:
    """A writable copy of the fixture, for the variants a test makes."""

    def __enter__(self):
        self.root = Path(tempfile.mkdtemp())
        self.path = self.root / "pair"
        shutil.copytree(FIXTURE, self.path)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.root)


class StepMapping(unittest.TestCase):
    """The fixture's transcripts map to the expected steps."""

    @classmethod
    def setUpClass(cls):
        cls.trajectory = build()
        cls.steps = cls.trajectory["steps"]

    def test_the_steps_are_the_expected_ones_in_time_order(self):
        got = [(s["agentId"], s["toolName"], s["toolType"], s["action"]) for s in self.steps]
        self.assertEqual(got, EXPECTED_STEPS)
        self.assertEqual([s["id"] for s in self.steps], list(range(len(self.steps))))

    def test_every_step_has_the_fields_both_viewers_read(self):
        for step in self.steps:
            for key in ("id", "timestamp", "agentId", "toolType", "source", "action", "args", "message", "observation"):
                self.assertIn(key, step)
            self.assertTrue(step["tool_call_metadata"]["function_name"])
            self.assertIn(step["toolType"], export.TOOL_TYPES)
            self.assertTrue(step["timestamp"].endswith("+00:00"))

    def test_times_are_the_transcript_times_in_utc(self):
        bash = self.steps[1]
        self.assertEqual(bash["timestamp"], at(2.2))
        self.assertEqual(bash["ended_at"], at(2.6))
        self.assertAlmostEqual(bash["duration_ms"], 400.0, places=3)

    def test_a_tool_step_carries_the_text_written_before_it(self):
        self.assertEqual(self.steps[1]["thought"], "Let me look around.")
        self.assertEqual(self.steps[0]["args"], {"thought": "I should look at the repo first."})

    def test_bash_shows_its_command_and_its_output(self):
        bash = self.steps[1]
        self.assertEqual(bash["args"]["command"], "ls src")
        self.assertEqual(bash["observation"], "core.py\nutil.py")
        tests = self.steps[15]
        self.assertIn("pytest", tests["args"]["command"])

    def test_read_is_typed_view_with_its_range_and_the_file_text(self):
        read = self.steps[4]
        self.assertEqual(read["args"], {"command": "view", "path": "/workspace/repo/src/core.py", "view_range": [10, 40]})
        self.assertEqual(read["observation"], "def f():\n    pass\n")

    def test_edit_and_write_show_a_diff_body(self):
        edit, write = self.steps[6], self.steps[7]
        self.assertEqual(edit["args"]["old_str"], "    pass")
        self.assertEqual(edit["args"]["new_str"], "    return 1")
        self.assertEqual(write["args"]["command"], "create")
        self.assertEqual(write["args"]["new_str"], "x = 1\n")
        self.assertEqual(write["toolType"], "create")

    def test_grep_and_glob_read_as_marked_search_lines(self):
        self.assertEqual(self.steps[8]["args"]["command"], "[Grep] def f in src glob=*.py")
        self.assertEqual(self.steps[9]["args"]["command"], "[Glob] find **/*.py in .")

    def test_todo_write_becomes_a_task_list(self):
        tasks = self.steps[10]["args"]["task_list"]
        self.assertEqual([t["status"] for t in tasks], ["done", "in_progress", "todo"])
        self.assertEqual(tasks[0]["title"], "Edit core")

    def test_a_receive_is_one_step_per_message_with_the_hubs_ids(self):
        first, second = self.steps[11], self.steps[12]
        self.assertEqual(first["coordbench"]["message_ids"], [5])
        self.assertEqual(second["coordbench"]["message_ids"], [8])
        self.assertEqual(first["args"]["from"], "agent2")
        self.assertIn("Which lines", first["args"]["content"])

    def test_an_empty_wait_reads_no_new_messages_with_its_blocked_time(self):
        wait = self.steps[18]
        self.assertEqual(wait["args"]["content"], export.NO_NEW_MESSAGES)
        self.assertEqual(wait["coordbench"]["hub"]["blocked_ms"], 1000.0)

    def test_a_shell_send_reads_as_a_send_with_the_delivered_text(self):
        send = self.steps[14]
        self.assertEqual(send["args"], {"content": "done with core", "to": "agent2"})
        self.assertEqual(send["coordbench"]["message_ids"], [17])
        self.assertEqual(send["coordbench"]["tool"], "Bash")

    def test_a_broadcast_is_one_step_addressed_to_broadcast(self):
        self.assertEqual(self.steps[5]["args"]["to"], "broadcast")
        self.assertEqual(self.steps[5]["coordbench"]["message_ids"], [8])

    def test_a_chained_command_joins_every_hub_call_it_made(self):
        chain = steps_named(self.trajectory, "coop_peek")[0]
        self.assertEqual([c["seq"] for c in chain["coordbench"]["hub_calls"]], [21, 23])

    def test_subagent_steps_are_marked_and_typed_other(self):
        sub = self.steps[17]
        self.assertTrue(sub["coordbench"]["subagent"])
        self.assertEqual(sub["args"]["command"], "flake8 src")
        self.assertNotIn("subagent", self.steps[16]["coordbench"])

    def test_the_finish_step_says_what_the_agent_said_last(self):
        finish1, finish2 = self.steps[-2], self.steps[-1]
        self.assertEqual(finish1["args"]["message"], "All done. Patch written.")
        self.assertEqual(finish2["args"]["message"], "Finished feature 6.")
        self.assertEqual(finish2["timestamp"], at(22.0))

    def test_the_model_is_on_each_agents_first_step(self):
        firsts = {}
        for step in self.steps:
            firsts.setdefault(step["agentId"], step)
        for step in firsts.values():
            self.assertEqual(step["tool_call_metadata"]["model_response"]["model"], "claude-sonnet-5-5-20260101")

    def test_llm_metrics_sit_on_each_agents_last_step(self):
        last = self.steps[-2]
        metrics = last["llm_metrics"]
        self.assertEqual(metrics["accumulated_cost"], 0.1234)
        usage = metrics["accumulated_token_usage"]
        tokens = self.trajectory["metadata"]["coordbench"]["agents"][0]["tokens"]
        self.assertEqual(usage["prompt_tokens"], tokens["input"] + tokens["cache_read"] + tokens["cache_write"])
        self.assertEqual(usage["completion_tokens"], tokens["output"])

    def test_usage_counts_each_api_message_once_with_its_largest_count(self):
        tokens = self.trajectory["metadata"]["coordbench"]["agents"][0]["tokens"]
        # 18 API messages (16 of agent1's, 2 of its subagent's), each saying
        # 4 input tokens on every line it spans.
        self.assertEqual(tokens["input"], 18 * 4)
        self.assertEqual(tokens["cache_write"], 18 * 1000)

    def test_the_stock_viewers_port_agrees_with_the_precomputed_type(self):
        """getToolType is right everywhere coordbench's types are, but for
        subagent steps (typed other on purpose) and a Write that updated a
        file, which the stock rule calls create from args.command."""
        for step in self.steps:
            if step["coordbench"].get("subagent"):
                continue
            self.assertEqual(export.get_tool_type(step), step["toolType"], step["toolName"])


class HubJoin(unittest.TestCase):

    def test_unmatched_calls_on_either_side_are_warnings(self):
        warnings = build()["metadata"]["coordbench"]["join_warnings"]
        self.assertEqual(len(warnings), 2)
        self.assertIn("send_message call at " + at(20.2) + " has no hub event", warnings[0])
        self.assertEqual(warnings[1], "agent1: 1 hub list_agents call(s) match no transcript step")

    def test_hub_timing_is_copied_onto_the_step(self):
        send = build()["steps"][2]
        self.assertEqual(send["coordbench"]["hub"]["seq"], 1)
        self.assertEqual(send["coordbench"]["hub"]["abs_ts"], EPOCH + 4.0)
        self.assertEqual(send["coordbench"]["hub"]["duration_ms"], 60)

    def test_messages_carry_their_read_times(self):
        messages = build()["metadata"]["coordbench"]["messages"]
        self.assertEqual([m["id"] for m in messages], [2, 5, 8, 17])
        self.assertEqual(messages[0]["read_at"], [at(15.07)])
        self.assertEqual(messages[3]["read_at"], [])

    def test_the_send_steps_messages_equal_conversation_json(self):
        """conversation.json, as CooperBench's runner writes it from the
        same deliveries, holds the messages the send steps made."""
        trajectory = build()
        by_id = {m["id"]: m for m in trajectory["metadata"]["coordbench"]["messages"]}
        sent = [by_id[i] for s in trajectory["steps"] if s["toolName"] == "openhands_comm_send"
                for i in s["coordbench"].get("message_ids", [])]
        conversation = conversation_json(read_events(FIXTURE))
        self.assertEqual([(m["from"], m["to"], m["content"]) for m in sent],
                         [(c["from"], c["to"], c["message"]) for c in conversation])

    def test_receive_steps_carry_the_text_the_hub_delivered(self):
        trajectory = build()
        by_id = {m["id"]: m for m in trajectory["metadata"]["coordbench"]["messages"]}
        for step in steps_named(trajectory, "openhands_comm_get"):
            for message_id in step["coordbench"].get("message_ids", []):
                self.assertEqual(step["args"]["content"], by_id[message_id]["content"])

    def test_the_hubs_events_are_on_the_wall_clock(self):
        events = build()["metadata"]["coordbench"]["hub_events"]
        self.assertEqual(events[0]["kind"], "run_started")
        self.assertEqual(events[1]["abs_ts"], EPOCH + 4.0)


def read_events(pair):
    return [json.loads(line) for line in (pair / "events.jsonl").read_text().splitlines() if line.strip()]


def conversation_json(events):
    """conversation.json in coop.py's shape, one entry per delivery sorted
    by timestamp, the way conversation.py derives it."""
    features = {a["id"]: a["features"][0] for a in events[0]["agents"]}
    entries = [{"from": e["agent"], "to": e["to"], "message": e["content"], "timestamp": e["timestamp"],
                "feature_id": features[e["agent"]]} for e in events if e["kind"] == "message"]
    return sorted(entries, key=lambda e: e["timestamp"])


class Metadata(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.metadata = build()["metadata"]
        cls.block = cls.metadata["coordbench"]

    def test_the_stock_viewers_keys(self):
        m = self.metadata
        self.assertEqual((m["repo"], m["task_id"], m["features"]), ("pallets_click_task", "task2068", "feature1_feature6"))
        self.assertEqual(m["agent_1"]["steps"], 17)
        self.assertEqual(m["agent_2"]["cost"], 0.05)
        self.assertAlmostEqual(m["total_cost"], 0.1734)
        self.assertEqual(m["duration_seconds"], 42.0)

    def test_the_outcome_holds_both_verdicts_and_the_route(self):
        outcome = self.block["outcome"]
        self.assertEqual(outcome["both_passed"], False)
        self.assertEqual(outcome["paper_both_passed"], True)
        self.assertEqual(outcome["cascade_route"], "union")
        self.assertEqual(outcome["merge_strategy"], "solo-agent1")
        self.assertTrue(outcome["has_naive_merge_conflict"])

    def test_the_agents_are_ordinal_lanes_in_feature_order(self):
        agents = self.block["agents"]
        self.assertEqual([(a["lane"], a["id"], a["feature_id"]) for a in agents],
                         [("agent_1", "agent1", 1), ("agent_2", "agent2", 6)])
        self.assertEqual(agents[0]["started_at"], at(0.0))
        self.assertEqual(agents[0]["model_usage"]["claude-sonnet-5-5-20260101"]["costUSD"], 0.1234)

    def test_what_was_read_and_where_the_times_came_from(self):
        self.assertEqual(self.block["format"], export.FORMAT)
        self.assertEqual(self.block["timestamps"], "session-jsonl")
        self.assertEqual(self.block["anchor"], "run_started")
        self.assertIn("agent1_session.jsonl", self.block["sources"])
        self.assertIn("judge.json", self.block["sources"])
        self.assertEqual(self.block["prompt_files"], ["agent1_prompt.md", "agent2_prompt.md"])
        self.assertEqual(self.block["failure_modes"]["commerrors"]["issue"]["code"], "C1a")

    def test_prompts_are_never_steps_so_the_stock_filter_drops_nothing(self):
        self.assertEqual(self.block["filtered_by_stock_viewer"], [])

    def test_two_agents_need_no_stock_viewer_note(self):
        self.assertNotIn("stock_viewer_note", self.block)

    def test_the_whole_object_is_json(self):
        json.dumps(build())


class StockViewer(unittest.TestCase):

    def test_the_filter_reads_the_first_non_empty_field_as_the_viewer_does(self):
        self.assertTrue(export.stock_viewer_drops({"message": "You are agent_1 working on"}))
        self.assertTrue(export.stock_viewer_drops({"content": "<ROLE> x", "message": "fine"}))
        self.assertFalse(export.stock_viewer_drops({"content": "fine", "message": "You are agent_1"}))
        self.assertTrue(export.stock_viewer_drops({"content": "", "message": "Added workspace context"}))
        self.assertFalse(export.stock_viewer_drops({"message": ["You are agent_"]}))

    def test_get_tool_type_ports_the_viewers_rule(self):
        def step(name, **args):
            return {"tool_call_metadata": {"function_name": name}, "args": args}
        self.assertEqual(export.get_tool_type(step("execute_bash", command="find . -name x")), "find_grep")
        self.assertEqual(export.get_tool_type(step("execute_bash", command="locate x")), "find_grep")
        self.assertEqual(export.get_tool_type(step("execute_bash", command="ls")), "bash")
        self.assertEqual(export.get_tool_type(step("str_replace_editor")), "edit")
        self.assertEqual(export.get_tool_type(step("openhands_comm_get")), "communication")
        self.assertEqual(export.get_tool_type({"toolName": "finish"}), "submit")

    def test_more_than_two_agents_get_the_note_and_the_total(self):
        agents = [{"id": f"agent{i}"} for i in range(3)]
        self.assertIn("3 agents", export.stock_viewer_note(agents))
        self.assertIsNone(export.stock_viewer_note(agents[:2]))


class Commands(unittest.TestCase):

    def test_docker_exec_is_unwrapped_to_the_inner_command(self):
        wrapped = "docker exec -w /workspace/repo cb-agent1 bash -lc 'git diff > patch.txt'"
        self.assertEqual(export.unwrap_docker(wrapped), "git diff > patch.txt")
        self.assertEqual(export.unwrap_docker("docker exec -i box sh -c 'ls -la'"), "ls -la")
        self.assertEqual(export.unwrap_docker("docker ps"), "docker ps")
        self.assertEqual(export.unwrap_docker("echo 'unclosed"), "echo 'unclosed")

    def test_an_alias_counts_only_where_a_command_starts(self):
        self.assertEqual(export.first_alias('/run/bin/agent1/coop-send agent2 "hi there"'),
                         ("send_message", ["agent2", "hi there"]))
        self.assertEqual(export.first_alias('echo "use coop-send to talk"'), (None, None))
        self.assertEqual(export.aliases_in("coop-peek && coord-list_agents | cat"), ["peek_messages", "list_agents"])
        self.assertEqual(export.aliases_in("FOO=1 coop-recv"), ["receive_messages"])

    def test_alias_words_read_back_as_the_tools_arguments(self):
        self.assertEqual(export.alias_input("task_update", ["t1", "done", "-n", "all set"]),
                         {"task_id": "t1", "status": "done", "note": "all set"})
        self.assertEqual(export.alias_input("task_create", ["--assign", "agent2", "Implement", "feature", "6"]),
                         {"assign": "agent2", "title": "Implement feature 6"})
        self.assertEqual(export.alias_input("request", ["agent1", "plan_approval", "ok?", "--wait", "30"]),
                         {"wait": "30", "recipient": "agent1", "kind": "plan_approval", "body": "ok?"})

    def test_a_coordination_mcp_tool_is_known_by_its_server(self):
        self.assertEqual(export.coordination_tool("mcp__plugin_coop_coordbench__send_message"), "send_message")
        self.assertEqual(export.coordination_tool("mcp__cooperbench-team__wait_for_message"), "wait_for_message")
        self.assertIsNone(export.coordination_tool("mcp__slack__send_message"))
        self.assertIsNone(export.coordination_tool("Bash"))


class Fallbacks(unittest.TestCase):

    def test_a_stream_alone_gives_interpolated_times_over_the_agent_phase(self):
        with PairCopy() as pair:
            (pair / "agent1_session.jsonl").unlink()
            lines = [line for line in (FIXTURE / "agent1_session.jsonl").read_text().splitlines()
                     if not json.loads(line).get("isSidechain")]
            stream = [json.dumps({k: v for k, v in json.loads(line).items() if k != "timestamp"}) for line in lines]
            original = (FIXTURE / "agent1_stream.jsonl").read_text().splitlines()
            (pair / "agent1_stream.jsonl").write_text("\n".join([original[0]] + stream + [original[-1]]) + "\n")
            trajectory = build(pair)
        block = trajectory["metadata"]["coordbench"]
        self.assertEqual(block["timestamps"], "interpolated")
        times = [export.parse_time(s["timestamp"]).timestamp() for s in trajectory["steps"] if s["agentId"] == "agent_1"]
        self.assertTrue(all(EPOCH <= t <= EPOCH + 21.5 + 1e-6 for t in times))
        self.assertEqual(steps_named(trajectory, "finish")[0]["args"]["message"], "All done. Patch written.")
        self.assertEqual(block["agents"][0]["tokens"]["input"], 64)

    def test_the_hubs_record_alone_gives_the_coordination_calls(self):
        """A session-driver pilot: no transcript, a one-line outcome under
        the stream's name, events without run_started, ids or text."""
        with PairCopy() as pair:
            for name in ("agent1_session.jsonl", "agent2_session.jsonl", "agent1_stream.jsonl"):
                (pair / name).unlink()
            (pair / "agent1_stream.jsonl").write_text(json.dumps({"type": "coordbench_session", "agent": "agent1"}) + "\n")
            events = [pilot_event(e) for e in read_events(FIXTURE)[1:]]
            (pair / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
            result = json.loads((pair / "result.json").read_text())
            result["driver"] = "session"
            (pair / "result.json").write_text(json.dumps(result))
            trajectory = build(pair)
        block = trajectory["metadata"]["coordbench"]
        self.assertEqual(block["timestamps"], "hub-events")
        self.assertEqual(block["anchor"], "run_create phase (approximate)")
        self.assertEqual(block["join_warnings"], [])
        self.assertFalse(block["messages_recorded"])
        sends = steps_named(trajectory, "openhands_comm_send")
        self.assertEqual(len(sends), 4)
        self.assertTrue(all(s["coordbench"]["from_hub"] for s in sends))
        receives = [s for s in steps_named(trajectory, "openhands_comm_get") if s["agentId"] == "agent_1"]
        self.assertEqual(sum(1 for s in receives if s["coordbench"].get("text_recorded") is False), 2)
        self.assertEqual(steps_named(trajectory, "finish")[1]["timestamp"], at(22.5))

    def test_a_cooperbench_run_takes_its_messages_from_conversation_json(self):
        with PairCopy() as pair:
            conversation = conversation_json(read_events(FIXTURE))
            (pair / "events.jsonl").unlink()
            (pair / "conversation.json").write_text(json.dumps(conversation))
            trajectory = build(pair)
        block = trajectory["metadata"]["coordbench"]
        self.assertEqual([m["content"] for m in block["messages"]], [c["message"] for c in conversation])
        sends = [s["args"]["content"] for s in steps_named(trajectory, "openhands_comm_send") if not s["is_error"]]
        self.assertEqual(sends, [c["message"] for c in conversation])
        receive = steps_named(trajectory, "openhands_comm_get")[0]
        self.assertIn("Which lines", receive["args"]["content"])
        self.assertEqual(block["anchor"], None)


def pilot_event(event):
    """An event as the pilots' hub recorded it: no message text, no ids."""
    event = {k: v for k, v in event.items() if k not in ("content", "message_id", "send_id", "message_ids", "call_id",
                                                       "via", "timestamp", "timestamp_iso")}
    return event


class Site(unittest.TestCase):

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out)

    def export_fixture(self, **options):
        trajectory = build(**options)
        evaluation = json.loads((FIXTURE / "eval.json").read_text())
        return export.write_site(self.out, [(FIXTURE, trajectory, evaluation)]), trajectory

    def test_the_trajectory_lands_where_the_stock_viewer_looks(self):
        urls, trajectory = self.export_fixture()
        key = "claude_sonnet_5_5_coop_claude_cli"
        path = self.out / "static/data/coop/trajectories" / key / "pallets_click_task_task2068_feature1_feature6.json.gz"
        self.assertEqual(json.loads(gzip.decompress(path.read_bytes())), json.loads(json.dumps(trajectory)))
        self.assertEqual(urls, [f"/viewer?model={key}&repo=pallets_click_task&task=task2068&features=feature1_feature6"])

    def test_a_git_arm_goes_under_coop_git_with_its_setting(self):
        trajectory = build()
        trajectory["metadata"]["coordbench"]["arm"] = "coop-git"
        urls = export.write_site(self.out, [(FIXTURE, trajectory, None)], "mine")
        self.assertTrue((self.out / "static/data/coop_git/index.json").is_file())
        self.assertTrue(urls[0].endswith("&setting=coopGit"))

    def test_the_index_row_and_stats(self):
        self.export_fixture()
        index = json.loads((self.out / "static/data/coop/index.json").read_text())
        key = "claude_sonnet_5_5_coop_claude_cli"
        self.assertEqual(index["models"], [key])
        self.assertEqual(index["modelDisplayNames"][key], "claude-sonnet-5-5 (coordbench coop, claude-cli)")
        row = index["tasks"][0]
        self.assertEqual((row["repo"], row["taskId"], row["features"], row["f1"], row["f2"]),
                         ("pallets_click_task", "task2068", "feature1_feature6", "feature1", "feature6"))
        self.assertEqual(row["results"][key], {"passed": False, "hasConflict": True})
        self.assertEqual(index["stats"][key], {"total": 1, "passed": 0, "rate": 0})

    def test_the_index_merges_idempotently(self):
        trajectory = build()
        row = export.index_row(trajectory, "k")
        once = export.merge_index({}, [row], "k", "K")
        twice = export.merge_index(once, [row], "k", "K")
        self.assertEqual(once, twice)

    def test_merging_keeps_other_models_and_meets_their_typst_rows(self):
        existing = {"models": ["gpt5"], "modelDisplayNames": {"gpt5": "GPT-5"},
                    "stats": {"gpt5": {"total": 652, "passed": 182, "rate": 27.95}},
                    "tasks": [{"repo": "typst", "taskId": "task6554", "features": "feature1_feature2", "f1": "feature1",
                               "f2": "feature2", "results": {"gpt5": {"passed": True, "hasConflict": False}},
                               "hasTrajectory": {"gpt5": True}}]}
        row = {"repo": "typst_task", "taskId": "task6554", "features": "feature1_feature2", "f1": "feature1",
               "f2": "feature2", "results": {"k": {"passed": True, "hasConflict": False}}, "hasTrajectory": {"k": True}}
        merged = export.merge_index(existing, [row], "k", "K")
        self.assertEqual(len(merged["tasks"]), 1)
        self.assertEqual(set(merged["tasks"][0]["results"]), {"gpt5", "k"})
        self.assertEqual(merged["stats"]["gpt5"], existing["stats"]["gpt5"])
        self.assertEqual(export.row_repo(merged, row), "typst")

    def test_stats_count_every_attempted_pair_and_round_as_write_index(self):
        rows = [{"repo": "r", "taskId": "task1", "features": f"feature1_feature{n}",
                 "results": {"k": {"passed": n % 3 == 0, "hasConflict": None}}, "hasTrajectory": {"k": True}}
                for n in range(2, 9)]
        merged = export.merge_index({}, rows, "k", "K")
        self.assertEqual(merged["stats"]["k"], {"total": 7, "passed": 2, "rate": 28.6})

    def test_a_pair_never_scored_counts_as_attempted_with_no_conflict_known(self):
        trajectory = build()
        trajectory["metadata"]["coordbench"]["outcome"].update(both_passed=None, merge_status=None)
        self.assertEqual(export.index_row(trajectory, "k")["results"]["k"],
                         {"passed": False, "hasConflict": None, "error": True})

    def test_has_conflict_is_none_when_no_merge_happened(self):
        self.assertIsNone(export.has_conflict({"status": "missing_input"}))
        self.assertIsNone(export.has_conflict({"status": "error"}))
        self.assertIsNone(export.has_conflict(None))
        self.assertFalse(export.has_conflict({"status": "clean"}))

    def test_the_legacy_aggregate_reads_as_generate_coop_index_reads_it(self):
        self.export_fixture()
        path = (self.out / "legacy/coop/pallets_click_task/task2068"
                / "aggregated_results_claude_sonnet_5_5_coop_claude_cli_k1.json")
        document = json.loads(path.read_text())
        detail = document["detailed_results"][0]
        self.assertEqual((detail["feature1_id"], detail["feature2_id"]), (1, 6))
        self.assertTrue(detail["has_naive_merge_conflict"])
        self.assertTrue(detail["feature1_union_merge_test_passed"] and detail["feature2_union_merge_test_passed"])
        self.assertEqual((document["naive_merge_success"], document["union_merge_success"]), (0, 1))
        self.assertEqual(generate_coop_index_passed(detail), True)

    def test_cause_labels_write_the_examples_the_stock_viewer_offers(self):
        labels = self.out / "labels.json"
        labels.write_text(json.dumps([{"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6],
                                       "cause": "Communication", "note": "the question went unanswered"}]))
        cause = export.cause_for(export.load_causes(labels), FIXTURE, json.loads((FIXTURE / "result.json").read_text()))
        self.assertEqual(cause, {"cause": "communication", "note": "the question went unanswered"})
        self.export_fixture(cause=cause)
        index = json.loads((self.out / "static/data/causes/index.json").read_text())
        entry = index["communication"][0]
        self.assertEqual(set(index), set(export.CAUSES))
        steps = json.loads((self.out / entry["trajectory_url"].replace("static/data/", "static/data/", 1)).read_text())
        self.assertEqual(entry["step_count"], len(steps))
        self.assertEqual(list(steps[0]), list(export.SEVEN_KEYS))
        comm = next(s for s in steps if s["toolType"] == "communication")
        self.assertIsNone(comm["args"])
        self.assertTrue(comm["details"])


    def test_annotate_s_labels_file_and_its_applied_label_both_reach_the_metadata(self):
        """annotate.py writes labels.json as {format, pairs: [...]} and
        tally --apply writes the label into judge.json as its cause; the
        exporter reads either, so a labelled pair shows its cause."""
        labels = self.out / "labels.json"
        labels.write_text(json.dumps({"format": "coordbench-cause-labels/1", "pairs": [
            {"key": "k", "pair_dir": str(FIXTURE), "packet": 1, "cause": "Expectation", "note": "n"}]}))
        result = json.loads((FIXTURE / "result.json").read_text())
        self.assertEqual(export.cause_for(export.load_causes(labels), FIXTURE, result),
                         {"cause": "expectation", "note": "n"})
        with PairCopy() as pair:
            judged = json.loads((pair / "judge.json").read_text())
            judged["cause"] = {"label": "Commitment", "note": "said it would, did not", "source": "human"}
            (pair / "judge.json").write_text(json.dumps(judged))
            block = export.build_trajectory(pair, NO_DATASET)["metadata"]["coordbench"]
        self.assertEqual(block["cause"], {"cause": "commitment", "note": "said it would, did not"})


class DriverNames(unittest.TestCase):

    def test_a_driver_of_ones_own_is_named_by_its_class(self):
        self.assertEqual(export.driver_of({"driver": "/home/me/drivers/fake.py:FakeDriver"}), "FakeDriver")
        self.assertEqual(export.driver_of({"driver": "claude-cli"}), "claude-cli")
        self.assertEqual(export.driver_of({"agent_framework": "claude_code"}), "claude_code")


def generate_coop_index_passed(item):
    """The cascade generate_coop_index.py's parse_aggregated_results runs
    over a detailed_results entry (website scripts, lines 38-65)."""
    passed = False
    if not item.get("has_naive_merge_conflict", False):
        passed = item.get("feature1_naive_merge_test_passed") is True and item.get("feature2_naive_merge_test_passed") is True
    if not passed:
        passed = item.get("feature1_union_merge_test_passed") is True and item.get("feature2_union_merge_test_passed") is True
    if not passed:
        passed = item.get("feature1_llm_merge_test_passed") is True and item.get("feature2_llm_merge_test_passed") is True
    return passed


class PairFiles(unittest.TestCase):

    def test_write_pair_writes_the_trajectory_and_the_runner_files(self):
        with PairCopy() as pair:
            export.write_pair(pair, NO_DATASET)
            trajectory = json.loads(gzip.decompress((pair / "trajectory.json.gz").read_bytes()))
            runner = json.loads((pair / "agent1_traj.json").read_text())
            agent6 = json.loads((pair / "agent6_traj.json").read_text())
        self.assertEqual(trajectory["metadata"]["coordbench"]["format"], export.FORMAT)
        self.assertEqual((runner["feature_id"], runner["agent_id"], runner["steps"]), (1, "agent1", 17))
        self.assertEqual(runner["messages"][0], {"role": "user", "content": json.loads(
            (FIXTURE / "agent1_session.jsonl").read_text().splitlines()[0])["message"]["content"]})
        self.assertEqual(agent6["agent_id"], "agent2")

    def test_an_existing_runner_file_is_left_alone(self):
        with PairCopy() as pair:
            (pair / "agent1_traj.json").write_text("{}")
            export.write_pair(pair, NO_DATASET)
            self.assertEqual((pair / "agent1_traj.json").read_text(), "{}")

    def test_export_finds_pairs_below_a_run_directory(self):
        with PairCopy() as pair:
            exported, urls = export.export([pair.parent], pair_files=False)
        self.assertEqual(len(exported), 1)
        self.assertEqual(urls, [])

    def test_features_info_reads_titles_and_the_spec_audit(self):
        root = Path(tempfile.mkdtemp())
        try:
            spec = root / "dataset/pallets_click_task/task2068/feature1"
            spec.mkdir(parents=True)
            (spec / "feature.md").write_text("**Title**: Edit many files\n\nBody\n")
            (root / "dataset/.spec_audit_verdicts.json").write_text(json.dumps(
                {"pallets_click_task/task2068|feature1": ["OK", "named"]}))
            info = export.build_trajectory(FIXTURE, root)["metadata"]["coordbench"]["features_info"]
        finally:
            shutil.rmtree(root)
        self.assertEqual(info[0], {"feature": 1, "title": "Edit many files", "spec_audit": {"verdict": "OK", "note": "named"}})
        self.assertEqual(info[1], {"feature": 6, "title": None, "spec_audit": None})


COOPERBENCH = Path(os.environ.get("COOPERBENCH_DIR") or Path.home() / "cooperbench")
PARSERS = COOPERBENCH / "src/cooperbench/agents/claude_code/parsers.py"


@unittest.skipUnless(PARSERS.is_file(), "no CooperBench checkout at COOPERBENCH_DIR or ~/cooperbench")
class Vendored(unittest.TestCase):
    """The vendored parser is CooperBench's own, function for function."""

    def test_the_functions_match_cooperbenchs(self):
        theirs = functions(PARSERS.read_text())
        ours = functions((ROOT / "coordbench/export.py").read_text())
        for name in ("_iter_json_lines", "_content_blocks_to_text", "parse_session_jsonl"):
            self.assertEqual(ours[name], theirs[name], name)


def functions(source):
    return {node.name: ast.dump(node) for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}


if __name__ == "__main__":
    unittest.main()
