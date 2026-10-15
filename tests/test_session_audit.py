"""What the session route's lane does so that a run measures the arm and
not the harness, and what it records when it cannot: the audit of a
finished run (API calls, the fixed prompt, efforts, refusals, a full disk,
an agent spawned twice), the lane's refusal to hand out again a run whose
agents ran, its wait for disk room, its refusal of an arm whose plugins
the route would drop, the queue's seeded arm order and its requeue of
harness errors alone, and the summary leaving those runs out."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "cooperbench" / "session"))
sys.path.insert(0, str(ROOT / "coordbench"))

import audit  # noqa: E402
import drivers  # noqa: E402
import lane  # noqa: E402
import summary  # noqa: E402

QUEUE_PY = ROOT / "bench" / "cooperbench" / "session" / "queue.py"
TOKEN = "coordbench-run-token team-1-f1_f2-abc123 agent1"


def assistant(message_id, text="", usage=None, effort="xhigh"):
    return {"type": "assistant", "effort": effort,
            "message": {"id": message_id, "role": "assistant", "content": [{"type": "text", "text": text}],
                        "usage": usage or {"input_tokens": 2, "cache_read_input_tokens": 40000,
                                           "cache_creation_input_tokens": 38000, "output_tokens": 9}}}


def tool_result(text, is_error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": text, "is_error": is_error}]}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write_lines(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    return path


class TheAuditReadsATranscript(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-audit-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))

    def test_calls_the_fixed_prompt_efforts_refusals_and_a_full_disk(self):
        path = write_lines(self.tmp / "agent1_session.jsonl", [
            user("Your task is the file /x/agent1.session-prompt.md"),
            assistant("m1", TOKEN), assistant("m1", ""),
            tool_result("<tool_use_error>Blocked: sleep 30 followed by: coop-recv</tool_use_error>", True),
            {"type": "user", "permissionDecision": {"decision": "reject", "reasonType": "classifier"}},
            {"type": "user", "permissionDecision": {"decision": "accept", "source": "config"}},
            tool_result("Exit code 1\nwrite /tmp/go-build/vet.cfg: no space left on device", True),
            tool_result("Exit code 1\nFAILED tests/test_x.py", True),
            assistant("m2", "done", effort="high"),
        ])
        facts = audit.transcript_facts(path)
        self.assertEqual((facts["calls"], facts["first_context"]), (2, 78002))
        self.assertEqual(facts["efforts"], ["xhigh", "high"])
        self.assertEqual(facts["refusals"], 2, "the guard's block and the rejected permission, not a failed test")
        self.assertEqual(facts["disk_full"], 1)
        self.assertIsNone(audit.transcript_facts(self.tmp / "missing.jsonl"))

    def test_attempts_count_only_the_spawns_of_the_agent(self):
        roots = [self.tmp / "projects"]
        prompt_file = "/runs/pair/agent1.session-prompt.md"
        spawn = [user(f"Your complete task is the file {prompt_file}. Read it."), assistant("a", TOKEN)]
        write_lines(roots[0] / "p" / "s" / "subagents" / "agent-first.jsonl", spawn)
        handle = {"run_token": TOKEN, "prompt_file": prompt_file, "started": time.time() - 30}
        self.assertEqual(audit.attempts_of(handle, roots), 1)
        # A lane's relay may echo the token in a reply; it was not spawned
        # as the agent, so it is no attempt.
        write_lines(roots[0] / "p" / "s" / "subagents" / "agent-relay.jsonl",
                    [user("Run lane.py prepare and return its JSON."), assistant("r", f"token: {TOKEN}")])
        self.assertEqual(audit.attempts_of(handle, roots), 1)
        write_lines(roots[0] / "p" / "s" / "subagents" / "agent-second.jsonl", spawn)
        self.assertEqual(audit.attempts_of(handle, roots), 2)
        self.assertIsNone(audit.attempts_of({"prompt_file": prompt_file}, roots))

    def test_why_a_run_measured_the_harness(self):
        clean = {"agent1": {"attempts": 1, "disk_full": 0}}
        self.assertIsNone(audit.harness_error(clean, 0, {"expired": []}))
        self.assertEqual(audit.harness_error(clean, 0, {"expired": ["agent1"]}), "the harness lost agent1")
        self.assertEqual(audit.harness_error(clean, 2, None), "the run met a full disk")
        self.assertEqual(audit.harness_error({"agent1": {"attempts": 2}}, 0, None), "agent1 ran more than once")

    def test_a_run_is_audited_from_its_plan_and_keeps_no_transcript_path(self):
        run_dir = self.tmp / "run"
        write_lines(run_dir / "agent1_session.jsonl", [assistant("m1", TOKEN)])
        lane.write_plan(run_dir, {"agents": [{"id": "agent1", "handle": {
            "workspace": str(run_dir / "ws"), "log_path": str(run_dir / "agent1_stream.jsonl")}}]})
        (run_dir / "finish.out").write_text("OSError: [Errno 28] No space left on device\n")
        record = audit.write_audit(run_dir, roots=[])
        self.assertEqual(record["agents"]["agent1"]["calls"], 1)
        self.assertEqual(record["disk_full_log_lines"], 1)
        self.assertEqual(record["harness_error"], "the run met a full disk")
        self.assertNotIn(str(self.tmp / "projects"), (run_dir / "audit.json").read_text())


class TheLaneHandsARunOutOnce(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-lane-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.workspaces = {name: self.tmp / name for name in ("agent1", "agent2")}
        for workspace in self.workspaces.values():
            workspace.mkdir()
        lane.write_plan(self.run_dir, {"agents": [{"id": name, "handle": {"workspace": str(path)}}
                                                  for name, path in self.workspaces.items()]})
        self.item = {"id": "t__team__r__1__f1_f2", "run_dir": str(self.run_dir), "prepared_at": 1.0}

    def test_a_run_no_agent_touched_is_handed_out_again(self):
        self.assertFalse(lane.ran_before(self.item))

    def test_a_marker_or_a_patch_means_the_agents_ran(self):
        (self.workspaces["agent2"] / "patch.txt").write_text("diff\n")
        self.assertTrue(lane.ran_before(self.item))

    def test_a_run_that_ran_is_finished_with_its_unmarked_agents_lost(self):
        (self.workspaces["agent1"] / lane.MARKER).touch()
        sets, detached = [], []
        with mock.patch.object(lane, "queue", side_effect=lambda *a: sets.append(a) or ""), \
                mock.patch.object(lane, "detach", side_effect=lambda a: detached.append(a) or 0), \
                contextlib.redirect_stderr(io.StringIO()):
            lane.finish_ran_run(SimpleNamespace(queue="q.jsonl"), self.item)
        self.assertEqual((detached[0].returned, detached[0].dead), (["agent1"], ["agent2"]))
        self.assertTrue(any(field.startswith("finishing_at=") for field in sets[0]),
                        "set before the finish starts, so the next prepare takes another item")

    def test_the_prepare_finishes_a_run_that_ran_and_takes_the_next_item(self):
        (self.workspaces["agent1"] / lane.MARKER).touch()
        answers = iter([json.dumps(self.item), json.dumps({"done": True})])
        with mock.patch.object(lane, "queue", side_effect=lambda *a: next(answers) if a[0] == "next" else ""), \
                mock.patch.object(lane, "finish_ran_run") as finished, \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            lane.command_prepare(SimpleNamespace(queue="q.jsonl", worker="w", log_dir=None,
                                                 wait_image_min=0, min_free_gb=0))
        self.assertTrue(finished.called)
        self.assertEqual(json.loads(printed.getvalue().splitlines()[-1]), {"done": True})


class TheLaneChecksTheDiskAndTheArm(unittest.TestCase):

    def test_a_place_short_of_room_is_named(self):
        with mock.patch.object(lane, "disk_places", return_value={"docker": "/d", "logs": "/l"}), \
                mock.patch.object(lane, "free_gb", side_effect=lambda path: {"/d": 3.0, "/l": 50.0}[path]):
            self.assertEqual(lane.short_of_disk("/l", 8), ["docker: 3.0 GB free at /d"])
            self.assertEqual(lane.short_of_disk("/l", 2), [])

    def test_an_arm_with_a_plugin_or_mcp_server_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "arm.json").write_text(json.dumps({"plugins": ["./plug"], "mcp_servers": {"x": {}}}))
            refused = lane.session_cannot_run(tmp)
            self.assertIn("plugins ['./plug']", refused)
            self.assertIn("MCP servers ['x']", refused)
            plan, error = lane.prepare_item({"arm": tmp}, None)
            self.assertIsNone(plan)
            self.assertEqual(error, refused)
        for arm in ("solo", "coop", "sdlc", "coop-git", "team", str(ROOT / "bench" / "cooperbench" / "arms" / "xblossom")):
            self.assertIsNone(lane.session_cannot_run(arm), arm)

    def test_the_items_options_reach_run_py(self):
        item = {"arm": "solo", "repo": "r", "task": 1, "features": "1,2", "model": "m", "timeout_min": 45,
                "run_name": "t-solo", "options": {"max_messages": 3, "tag": "x"}}
        failed = SimpleNamespace(stdout="", stderr="no", returncode=1)
        with mock.patch.object(lane.subprocess, "run", return_value=failed) as called:
            lane.prepare_item(item, None)
        command = called.call_args[0][0]
        self.assertIn("max_messages=3", command)
        self.assertIn("tag=x", command)

    def test_a_lost_agent_is_named_lost_and_not_timed_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            handle = {"agent": "agent2", "workspace": tmp, "log_path": str(Path(tmp) / "agent2_stream.jsonl"),
                      "timeout_s": 2700, "deadline": time.time() - 1, "lost": True}
            outcome = drivers.SessionDriver(poll_seconds=0, transcript_roots=[]).wait(handle)
        self.assertEqual(outcome.status, "Error")
        self.assertIn("lost", outcome.error)
        self.assertNotIn("minutes", outcome.error)


class TheQueueOrdersArmsBySeed(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-queue-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        dataset = self.tmp / "dataset" / "subsets"
        dataset.mkdir(parents=True)
        tasks = [{"repo": "r_task", "task_id": n, "pairs": [[1, 2]]} for n in range(1, 9)]
        (dataset / "flash.json").write_text(json.dumps({"tasks": tasks}))

    def queue(self, *args):
        out = subprocess.run([sys.executable, str(QUEUE_PY), *args], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def items(self, path):
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def add(self, path, arms, *extra):
        self.queue("add", str(path), "--tag", "t", "--arms", arms, "--dataset-dir", str(self.tmp / "dataset"), *extra)
        return self.items(path)

    def test_the_seeded_order_varies_by_pair_and_not_by_the_order_given(self):
        one = self.add(self.tmp / "a.jsonl", "solo,coop,team", "--order-seed", "0")
        two = self.add(self.tmp / "b.jsonl", "team,solo,coop", "--order-seed", "0")
        self.assertEqual([i["id"] for i in one], [i["id"] for i in two])
        firsts = {i["arm"] for i in one[::3]}
        self.assertGreater(len(firsts), 1, "the first arm of a pair is not always the same")
        plain = self.add(self.tmp / "c.jsonl", "solo,coop,team")
        self.assertEqual({i["arm"] for i in plain[::3]}, {"solo"})

    def test_shuffle_moves_only_the_arms_no_lane_has_taken(self):
        path = self.tmp / "q.jsonl"
        items = self.add(path, "solo,coop,team")
        items[0]["status"] = "done"
        path.write_text("".join(json.dumps(i) + "\n" for i in items))
        self.queue("shuffle", str(path), "--tag", "t", "--seed", "0")
        after = self.items(path)
        self.assertEqual(after[0]["id"], items[0]["id"], "a done item keeps its place")
        self.assertEqual([i["task"] for i in after], [i["task"] for i in items], "the pairs keep their order")
        self.assertEqual(sorted(i["id"] for i in after), sorted(i["id"] for i in items))
        self.assertGreater(len({i["arm"] for i in after[3::3]}), 1)

    def test_options_ride_on_every_item(self):
        items = self.add(self.tmp / "o.jsonl", "solo", "--option", "max_messages=3", "--option", "tag=x")
        self.assertEqual(items[0]["options"], {"max_messages": 3, "tag": "x"})

    def test_requeue_can_take_back_the_harness_errors_alone(self):
        path = self.tmp / "h.jsonl"
        items = self.add(path, "solo")
        items[0].update(status="failed", harness_error="the run met a full disk")
        items[1].update(status="failed", error="no marker within 45 minutes")
        path.write_text("".join(json.dumps(i) + "\n" for i in items))
        self.queue("requeue", str(path), "--status", "failed", "--harness-only")
        after = self.items(path)
        self.assertEqual((after[0]["status"], after[1]["status"]), ("pending", "failed"))
        self.assertNotIn("harness_error", after[0])


class TheSummaryLeavesHarnessErrorsOut(unittest.TestCase):

    def row(self, task, passed, harness_error=None, total=100, prefix=60):
        return {"repo": "r", "task": task, "features": "f1_f2", "agent_errors": 0, "passed": passed,
                "wall_seconds": 10.0, "agents_wall_seconds": 10.0, "agent_seconds_sum": 10.0, "cost_usd": 1.0,
                "input_tokens": 1, "cache_read_tokens": 1, "cache_write_tokens": 1, "output_tokens": 1,
                "total_tokens": total, "output_is_lower_bound": False, "tool_calls": 1, "messages_sent": 0,
                "message_words": 0, "merge_conflict": False, "claims": 0, "prs_opened": 0, "merges": 0,
                "protocol_merge_conflicts": 0, "time_to_first_merge_seconds": None,
                **summary.route_fields({"harness_error": harness_error, "agents": {"agent1": {
                    "calls": 2, "first_context": prefix // 2, "refusals": 1 if task == 1 else 0,
                    "efforts": ["xhigh"]}}}, total)}

    def test_a_harness_error_is_neither_a_pass_nor_a_failure(self):
        arm = summary.summarize_arm([self.row(1, True), self.row(2, False),
                                     self.row(3, True, harness_error="the run met a full disk")])
        self.assertEqual((arm["attempts"], arm["passed"], arm["harness_errors"]), (2, 1, 1))
        self.assertEqual(arm["harness_error_runs"], ["r/3/f1_f2: the run met a full disk"])
        self.assertEqual(arm["prefix_share_of_tokens"], 0.6)
        self.assertEqual(arm["net_tokens_per_pair_median"], 40)
        self.assertEqual((arm["refusal_pairs"], arm["efforts"]), (1, ["xhigh"]))

    def test_a_run_without_an_audit_has_no_route_figures(self):
        fields = summary.route_fields(None, 100)
        self.assertEqual((fields["audited"], fields["net_tokens"], fields["harness_error"]), (False, None, None))


if __name__ == "__main__":
    unittest.main()
