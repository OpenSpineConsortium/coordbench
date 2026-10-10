"""Write tests/fixtures/export/coop_pair, the pair test_coordbench_export.py
and test_coordbench_compare.py read: a coordbench coop pair under the
claude-cli driver, in the shapes the runner, the hub and the cascade
write (result.json with timings, events.jsonl with run_started, message
ids and receipts, eval.json with its cascade block, a judge.json), with a
Claude Code session transcript per agent and a subagent's sidechain in
agent1's. The output is committed; run this only to change it:

    python3 tests/fixtures/export/make_coop_pair.py tests/fixtures/export/coop_pair
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(sys.argv[1])
EPOCH = 1791580000.0  # run_started
SESSION1 = "11111111-1111-4111-8111-111111111111"
SESSION2 = "22222222-2222-4222-8222-222222222222"
MODEL = "claude-sonnet-5-5-20260101"


def iso(t):
    return datetime.fromtimestamp(EPOCH + t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def naive(t):
    return datetime.fromtimestamp(EPOCH + t, timezone.utc).replace(tzinfo=None).isoformat()


class Transcript:
    def __init__(self, session, cwd="/workspace/repo"):
        self.session, self.cwd, self.lines, self.parent, self.count = session, cwd, [], None, 0

    def line(self, t, kind, message, sidechain=False, agent_id=None, **extra):
        self.count += 1
        uuid = f"{self.session[:8]}-{self.count:04d}"
        record = {"parentUuid": self.parent, "isSidechain": sidechain, "userType": "external", "cwd": self.cwd,
                  "sessionId": self.session, "version": "2.1.300", "gitBranch": "", "type": kind,
                  "message": message, "uuid": uuid, "timestamp": iso(t)}
        if agent_id:
            record["agentId"] = agent_id
        record.update(extra)
        self.lines.append(record)
        self.parent = uuid

    def prompt(self, t, text):
        self.line(t, "user", {"role": "user", "content": text})

    def assistant(self, t, message_id, blocks, output, stop=None, **side):
        usage = {"input_tokens": 4, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 2000,
                 "output_tokens": output, "service_tier": "standard"}
        self.line(t, "assistant", {"id": message_id, "type": "message", "role": "assistant", "model": MODEL,
                                   "content": blocks, "stop_reason": stop, "stop_sequence": None, "usage": usage},
                  requestId=f"req_{message_id}", **side)

    def result(self, t, tool_id, content, extra=None, is_error=False, **side):
        block = {"tool_use_id": tool_id, "type": "tool_result", "content": content}
        if is_error:
            block["is_error"] = True
        fields = {"toolUseResult": extra} if extra is not None else {}
        self.line(t, "user", {"role": "user", "content": [block]}, **side, **fields)

    def write(self, path):
        path.write_text("".join(json.dumps(line) + "\n" for line in self.lines), encoding="ascii")


def use(tool_id, name, tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


SEND = "mcp__plugin_coop_coordbench__send_message"
BROADCAST = "mcp__plugin_coop_coordbench__broadcast"
RECV = "mcp__plugin_coop_coordbench__receive_messages"
WAIT = "mcp__plugin_coop_coordbench__wait_for_message"

PROMPT1 = "You are agent1, one of two agents implementing feature 1 of pallets_click_task task 2068."
PROMPT2 = "You are agent2, one of two agents implementing feature 6 of pallets_click_task task 2068."
MSG_PLAN1 = "I take src/core.py lines 10-40 for feature 1."
MSG_PLAN2 = "I take src/util.py for feature 6. Which lines of core.py do you need?"
MSG_STATUS2 = "Status update: util.py is done."
MSG_DONE1 = "done with core"


def agent1():
    t = Transcript(SESSION1)
    t.prompt(0.5, PROMPT1)
    t.assistant(2.0, "msg_a01", [{"type": "thinking", "thinking": "I should look at the repo first.", "signature": "x"}], 3)
    t.assistant(2.1, "msg_a01", [{"type": "text", "text": "Let me look around."}], 20)
    t.assistant(2.2, "msg_a01", [use("toolu_01", "Bash", {"command": "ls src", "description": "List src"})], 60, "tool_use")
    t.result(2.6, "toolu_01", "core.py\nutil.py", {"stdout": "core.py\nutil.py", "stderr": "", "interrupted": False,
                                                   "isImage": False})
    t.assistant(4.0, "msg_a02", [use("toolu_02", SEND, {"to": "agent2", "text": MSG_PLAN1})], 40, "tool_use")
    t.result(4.3, "toolu_02", [{"type": "text", "text": "sent to agent2"}], [{"type": "text", "text": "sent to agent2"}])
    t.assistant(6.0, "msg_a03", [use("toolu_03", "Read", {"file_path": "/workspace/repo/src/core.py", "offset": 10,
                                                          "limit": 30})], 30, "tool_use")
    t.result(6.2, "toolu_03", "    10\tdef f():\n    11\t    pass\n",
             {"type": "text", "file": {"filePath": "/workspace/repo/src/core.py", "content": "def f():\n    pass\n",
                                       "numLines": 2, "startLine": 10, "totalLines": 50}})
    t.assistant(8.0, "msg_a04", [use("toolu_04", "Edit", {"file_path": "/workspace/repo/src/core.py",
                                                          "old_string": "    pass", "new_string": "    return 1",
                                                          "replace_all": False})], 50, "tool_use")
    t.result(8.3, "toolu_04", "The file /workspace/repo/src/core.py has been updated.",
             {"filePath": "/workspace/repo/src/core.py", "oldString": "    pass", "newString": "    return 1",
              "structuredPatch": [], "userModified": False, "replaceAll": False})
    t.assistant(9.0, "msg_a05", [use("toolu_05", "Write", {"file_path": "/workspace/repo/src/new.py",
                                                           "content": "x = 1\n"})], 20, "tool_use")
    t.result(9.2, "toolu_05", "File created successfully at: /workspace/repo/src/new.py",
             {"type": "create", "filePath": "/workspace/repo/src/new.py", "content": "x = 1\n", "structuredPatch": []})
    t.assistant(10.0, "msg_a06", [use("toolu_06", "Grep", {"pattern": "def f", "path": "src", "glob": "*.py"})], 20,
                "tool_use")
    t.result(10.1, "toolu_06", "src/core.py", {"mode": "files_with_matches", "filenames": ["src/core.py"], "numFiles": 1})
    t.assistant(11.0, "msg_a07", [use("toolu_07", "Glob", {"pattern": "**/*.py"})], 20, "tool_use")
    t.result(11.1, "toolu_07", "src/core.py\nsrc/util.py\nsrc/new.py", {"filenames": ["src/core.py"], "numFiles": 3})
    todos = [{"content": "Edit core", "status": "completed", "activeForm": "Editing core"},
             {"content": "Run the tests", "status": "in_progress", "activeForm": "Running the tests"},
             {"content": "Write the patch", "status": "pending", "activeForm": "Writing the patch"}]
    t.assistant(12.0, "msg_a08", [use("toolu_08", "TodoWrite", {"todos": todos})], 30, "tool_use")
    t.result(12.1, "toolu_08", "Todos have been modified successfully.", {"oldTodos": [], "newTodos": todos})
    t.assistant(14.0, "msg_a09", [{"type": "text", "text": "Checking my inbox."}, use("toolu_09", RECV, {})], 20,
                "tool_use")
    inbox = [{"from": "agent2", "to": "agent1", "content": MSG_PLAN2, "timestamp": EPOCH + 5.05,
              "timestamp_iso": naive(5.05)},
             {"from": "agent2", "to": "agent1", "content": MSG_STATUS2, "timestamp": EPOCH + 7.05,
              "timestamp_iso": naive(7.05)}]
    t.result(14.3, "toolu_09", [{"type": "text", "text": json.dumps(inbox, indent=2)}])
    t.assistant(16.0, "msg_a10", [use("toolu_10", "Bash", {"command": f'coop-send agent2 "{MSG_DONE1}"',
                                                           "description": "Tell agent2"})], 20, "tool_use")
    t.result(16.4, "toolu_10", "sent to agent2", {"stdout": "", "stderr": "sent to agent2", "interrupted": False,
                                                  "isImage": False})
    t.assistant(17.0, "msg_a11", [use("toolu_11", "Bash", {"command": "cd /workspace/repo && python -m pytest tests -q",
                                                           "description": "Run the tests"})], 20, "tool_use")
    t.result(17.8, "toolu_11", "3 passed in 0.10s", {"stdout": "3 passed in 0.10s", "stderr": "",
                                                     "interrupted": False, "isImage": False})
    t.assistant(18.0, "msg_a12", [use("toolu_12", "Task", {"description": "Check style", "prompt": "Run flake8 on src",
                                                           "subagent_type": "general-purpose"})], 20, "tool_use")
    side = {"sidechain": True, "agent_id": "a1sub"}
    t.line(18.2, "user", {"role": "user", "content": "Run flake8 on src"}, **side)
    t.assistant(18.4, "msg_s01", [use("toolu_s1", "Bash", {"command": "flake8 src", "description": "Lint"})], 10,
                "tool_use", **side)
    t.result(18.6, "toolu_s1", "", {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}, **side)
    t.assistant(18.7, "msg_s02", [{"type": "text", "text": "Style is clean."}], 5, "end_turn", **side)
    t.result(18.9, "toolu_12", [{"type": "text", "text": "Style is clean."}])
    t.assistant(19.0, "msg_a13", [use("toolu_13", WAIT, {"timeout_ms": 1000})], 20, "tool_use")
    t.result(20.1, "toolu_13", [{"type": "text", "text": "no message arrived before the timeout"}])
    t.assistant(20.2, "msg_a14", [use("toolu_14", SEND, {"to": 2, "text": "refused"})], 20, "tool_use")
    t.result(20.25, "toolu_14", "Input validation error: to must be a string", is_error=True)
    t.assistant(20.4, "msg_a15", [use("toolu_15", "Bash", {"command": "coop-peek; coop-agents",
                                                           "description": "Peek and list"})], 20, "tool_use")
    t.result(20.7, "toolu_15", "0\nagent1\nagent2", {"stdout": "0\nagent1\nagent2", "stderr": "",
                                                     "interrupted": False, "isImage": False})
    t.assistant(21.0, "msg_a16", [{"type": "text", "text": "All done. Patch written."}], 80, "end_turn")
    return t


def agent2():
    t = Transcript(SESSION2)
    t.prompt(0.6, PROMPT2)
    t.assistant(5.0, "msg_b01", [use("toolu_b1", SEND, {"to": "agent1", "text": MSG_PLAN2})], 30, "tool_use")
    t.result(5.2, "toolu_b1", [{"type": "text", "text": "sent to agent1"}])
    t.assistant(7.0, "msg_b02", [use("toolu_b2", BROADCAST, {"text": MSG_STATUS2})], 30, "tool_use")
    t.result(7.2, "toolu_b2", [{"type": "text", "text": ""}])
    t.assistant(15.0, "msg_b03", [use("toolu_b3", RECV, {})], 20, "tool_use")
    inbox = [{"from": "agent1", "to": "agent2", "content": MSG_PLAN1, "timestamp": EPOCH + 4.05,
              "timestamp_iso": naive(4.05)}]
    t.result(15.2, "toolu_b3", [{"type": "text", "text": json.dumps(inbox, indent=2)}])
    t.assistant(22.0, "msg_b04", [{"type": "text", "text": "Finished feature 6."}], 40, "end_turn")
    return t


def events():
    rows = []

    def emit(t, kind, agent=None, **data):
        rows.append({"seq": len(rows), "t": t, "kind": kind, "agent": agent, **data})
        return rows[-1]["seq"]

    def call(t, agent, tool, via, duration_ms, blocked_ms=0.0, then=None):
        seq = emit(t, "tool", agent, tool=tool, via=via)
        if then:
            then(seq)
        emit(round(t + duration_ms / 1000, 3), "tool_done", agent, tool=tool, duration_ms=duration_ms,
             blocked_ms=blocked_ms, is_error=False, cancelled=False)
        return seq

    def deliver(t, sender, to, content, send_id):
        seq = len(rows)
        emit(t, "message", sender, to=to, chars=len(content), content=content, timestamp=EPOCH + t,
             timestamp_iso=naive(t), message_id=seq, send_id=send_id)
        return seq

    emit(0.0, "run_started", None, epoch=EPOCH, iso=datetime.fromtimestamp(EPOCH, timezone.utc).isoformat(),
         arm="coop", agents=[{"id": "agent1", "role": "agent", "features": [1]},
                             {"id": "agent2", "role": "agent", "features": [6]}], tool_surface="mcp")
    ids = {}
    call(4.0, "agent1", "send_message", "mcp", 60, then=lambda s: ids.update(plan1=deliver(4.05, "agent1", "agent2",
                                                                                           MSG_PLAN1, s)))
    call(5.0, "agent2", "send_message", "mcp", 60, then=lambda s: ids.update(plan2=deliver(5.05, "agent2", "agent1",
                                                                                           MSG_PLAN2, s)))
    call(7.0, "agent2", "broadcast", "mcp", 60, then=lambda s: ids.update(status2=deliver(7.05, "agent2", "agent1",
                                                                                          MSG_STATUS2, s)))
    call(14.1, "agent1", "receive_messages", "mcp", 40,
         then=lambda s: emit(14.12, "receive", "agent1", count=2, message_ids=[ids["plan2"], ids["status2"]],
                             call_id=s))
    call(15.05, "agent2", "receive_messages", "mcp", 40,
         then=lambda s: emit(15.07, "receive", "agent2", count=1, message_ids=[ids["plan1"]], call_id=s))
    call(16.1, "agent1", "send_message", "shell", 70, then=lambda s: deliver(16.15, "agent1", "agent2", MSG_DONE1, s))
    call(19.05, "agent1", "wait_for_message", "mcp", 1010, blocked_ms=1000.0)
    call(20.45, "agent1", "peek_messages", "shell", 20)
    call(20.55, "agent1", "list_agents", "shell", 20)
    call(30.0, "agent1", "list_agents", "shell", 20)
    return rows


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="ascii")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    agent1().write(OUT / "agent1_session.jsonl")
    agent2().write(OUT / "agent2_session.jsonl")
    (OUT / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events()), encoding="ascii")
    (OUT / "agent1_prompt.md").write_text(PROMPT1 + "\n", encoding="ascii")
    (OUT / "agent2_prompt.md").write_text(PROMPT2 + "\n", encoding="ascii")
    init = {"type": "system", "subtype": "init", "session_id": SESSION1, "model": MODEL,
            "tools": ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "TodoWrite", "Task", SEND, RECV],
            "mcp_servers": [{"name": "plugin:coop:coordbench", "status": "connected"}], "claude_code_version": "2.1.300"}
    usage = {"input_tokens": 64, "output_tokens": 520, "cache_read_input_tokens": 32000,
             "cache_creation_input_tokens": 16000}
    final = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 21000, "duration_api_ms": 12000,
             "num_turns": 17, "result": "All done. Patch written.", "session_id": SESSION1, "total_cost_usd": 0.1234,
             "usage": usage, "modelUsage": {MODEL: {"inputTokens": 64, "outputTokens": 520, "costUSD": 0.1234}}}
    (OUT / "agent1_stream.jsonl").write_text(json.dumps(init) + "\nstderr noise\n" + json.dumps(final) + "\n",
                                             encoding="ascii")
    agent_record = {"status": "Submitted", "cost": 0.1234, "steps": 17, "input_tokens": 64, "output_tokens": 520,
                    "cache_read_tokens": 32000, "cache_write_tokens": 16000, "patch_lines": 12, "error": None,
                    "role": "agent", "duration_ms": 21000, "api_duration_ms": 12000, "end_reason": None,
                    "model_requested": "claude-sonnet-5-5", "model": MODEL, "model_differs": False}
    timings = [{"phase": "run_create", "agent": None, "started": naive(-0.01), "ended": naive(0.01), "seconds": 0.02},
               {"phase": "agent", "agent": "agent1", "started": naive(0.0), "ended": naive(21.5), "seconds": 21.5},
               {"phase": "agent", "agent": "agent2", "started": naive(0.0), "ended": naive(22.5), "seconds": 22.5}]
    result = {"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "coop",
              "run_id": "coop-2068-f1_f6-abc123", "run_name": "fixture", "agent_framework": "claude_code",
              "model": "claude-sonnet-5-5", "started_at": naive(-2.0), "ended_at": naive(40.0), "duration_seconds": 42.0,
              "agents": {"agent1": {"feature_id": 1, **agent_record},
                         "agent2": {"feature_id": 6, **agent_record, "cost": 0.05, "steps": 4}},
              "total_cost": 0.1734, "total_steps": 21, "messages_sent": 4, "log_dir": "/logs/fixture/coop/pallets_click_task/2068/f1_f6",
              "arm": "coop", "driver": "claude-cli", "timings": timings}
    write_json(OUT / "result.json", result)
    cascade = {"verdict_63b9d44": False, "verdict_paper_era": True, "route": "union",
               "has_naive_merge_conflict": True,
               "naive": {"conflict": True, "conflict_sections": 1, "conflict_lines": 6, "avg_lines_per_conflict": 6.0,
                         "f1": None, "f2": None, "f1_regex": None, "f2_regex": None},
               "union": {"ran": True, "clean": True, "f1": True, "f2": True, "f1_regex": True, "f2_regex": True},
               "llm": {"ran": False, "needed": False, "reason": "not needed: the union merge passed both suites"},
               "agent_alone": {"agent1": {"f1": True, "f2": False}, "agent2": {"f1": False, "f2": True}}}
    evaluation = {"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "coop",
                  "apply_status": {"agent1": "applied", "agent2": "applied"},
                  "merge": {"status": "conflicts", "strategy": "solo-agent1", "diff": ""},
                  "feature1": {"feature_id": 1, "passed": True, "tests_passed": 3, "tests_failed": 0},
                  "feature2": {"feature_id": 6, "passed": False, "tests_passed": 0, "tests_failed": 2},
                  "both_passed": False, "error": None, "cascade": cascade}
    write_json(OUT / "eval.json", evaluation)
    judge = {"backend": "fake", "model": "fake-judge", "prompt_variant": "appF-7", "created_at": "2026-10-10T00:00:00Z",
             "commerrors": {"issue": {"code": "C1a", "group": "Unresponsiveness",
                                      "description": "agent2's question about core.py lines is never answered"},
                            "evidence": ["Which lines of core.py do you need?", MSG_DONE1], "verified": [True, True]},
             "symptoms": {"level": "coordination", "primary": "Unresponsiveness", "secondary": ["Work overlap"],
                          "family": "Communication", "evidence": [MSG_PLAN2]},
             "speech_acts": {"1": "Plan"},
             "emergent": {"role_division": {"present": True, "evidence": [MSG_PLAN1]},
                          "resource_division": {"present": True, "evidence": ["I take src/util.py"]},
                          "negotiation": {"present": False, "evidence": []}}}
    write_json(OUT / "judge.json", judge)
    (OUT / "agent1.patch").write_text("--- a/src/core.py\n+++ b/src/core.py\n@@ -11 +11 @@\n-    pass\n+    return 1\n",
                                      encoding="ascii")
    (OUT / "agent6.patch").write_text("--- a/src/util.py\n+++ b/src/util.py\n@@ -1 +1 @@\n-a = 0\n+a = 6\n",
                                      encoding="ascii")


main()
