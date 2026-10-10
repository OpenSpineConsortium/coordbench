"""The hub is what every agent of every arm talks to, so these pin its HTTP
API and its MCP transport against the contract the runner, the bridge and
the arms code against. They use a small arm of their own, written to a
temporary directory, so they hold whatever the built-in arms become."""

import contextlib
import http.client
import io
import json
import os
import select
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py, which hub.py imports, shares its name with the
# standard library's tracer; a process that loaded that one first gets
# ours here instead.
if getattr(sys.modules.get("trace"), "__file__", None) != str(COORDBENCH / "trace.py"):
    sys.modules.pop("trace", None)
import hub as hub_module  # noqa: E402
import kit  # noqa: E402
import mcp  # noqa: E402
import trace  # noqa: E402
from arm import Arm, Call, Run, Tool, ToolError  # noqa: E402

ARM_JSON = {"entry": "arm.py:TestArm", "name": "testarm", "description": "messages and two odd tools",
            "needs": [], "plugins": [], "mcp_servers": {}}

ARM_PY = '''
import json
import time

from arm import Arm, Tool, ToolError
import kit


def refuse(call):
    raise ToolError("refused on purpose")


def crash(call):
    raise RuntimeError("a bug in the arm")


def doze(call):
    # Waits for a poke, booking the wait as time blocked on another agent,
    # then works a little, so the call's wall time exceeds its wait.
    run = call.run
    began = time.monotonic()
    with run.lock:
        run.lock.wait_for(lambda: run.state.get("poked") or call.cancelled.is_set(),
                          timeout=call.arg("seconds", 5))
    call.waited(time.monotonic() - began)
    time.sleep(0.05)
    return "dozed"


def poke(call):
    with call.run.lock:
        call.run.state["poked"] = True
        call.run.lock.notify_all()
    return "poked"


def echo(call):
    return json.dumps(call.arguments)


OPEN_SCHEMA = {"type": "object", "properties": {"known": kit.TEXT}, "additionalProperties": True}


class TestArm(Arm):
    name = "testarm"

    def tools(self, run, agent):
        return kit.messaging_tools() + [Tool("refuse", "Always refuses.", refuse),
                                        Tool("crash", "Always crashes.", crash),
                                        Tool("doze", "Waits for a poke.", doze,
                                             kit.schema(seconds={"type": "number"})),
                                        Tool("poke", "Wakes the dozers.", poke),
                                        Tool("anything", "Takes any argument.", echo, OPEN_SCHEMA)]

    def instructions(self, run, agent):
        # No built-in arm sends any; this one does, to show the hook reaches
        # the client through initialize.
        return f"testarm run {run.id}: you are {agent}"

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run)}
'''

SPECS = {"1": "Add the first feature.", "2": "Add the second feature."}
INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}}


def write_test_arm(directory):
    directory.mkdir()
    (directory / "arm.json").write_text(json.dumps(ARM_JSON), encoding="utf-8")
    (directory / "arm.py").write_text(ARM_PY, encoding="utf-8")


def rpc_message(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        message["id"] = request_id
    return message


class Answer:
    def __init__(self, response):
        self.status = response.status
        self.headers = response.headers
        self.raw = response.read()

    @property
    def json(self):
        return json.loads(self.raw)


@contextlib.contextmanager
def dropped_replies(gone):
    """The hub's reply failing with ConnectionResetError, as a write to a
    client that closed its connection does, for every payload gone() picks."""
    original = hub_module.HubHandler.reply

    def reply(handler, status, payload=None, headers=None):
        if gone(payload):
            raise ConnectionResetError("the client closed the connection")
        return original(handler, status, payload, headers)

    with mock.patch.object(hub_module.HubHandler, "reply", reply):
        yield


def contents(messages_json):
    return [message["content"] for message in json.loads(messages_json)]


def listen_in_test(test, level=None):
    """A Memory on the trace stream for one test, at the level asked when
    one is, both undone when the test ends so the next starts as it did."""
    if level is not None:
        test.addCleanup(trace.set_level, trace.level)
        trace.set_level(level)
    memory = trace.listen(trace.Memory())
    test.addCleanup(trace.unlisten, memory)
    return memory


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


class HubServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.arm_dir = self.dir / "testarm"
        write_test_arm(self.arm_dir)
        self.hub = hub_module.Hub(log_dir=self.dir / "logs", arm_dirs=[self.dir])
        self.server = hub_module.serve_in_background(self.hub, "127.0.0.1", 0)
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for hosted in self.hub.runs.values():
            if hosted is not None:
                hosted.run.close_log()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
            all_headers = {"Content-Type": "application/json"} if data is not None else {}
            all_headers.update(headers or {})
            connection.request(method, path, body=data, headers=all_headers)
            return Answer(connection.getresponse())
        finally:
            connection.close()

    def create(self, run_id="pair1", features=(1, 2)):
        answer = self.request("POST", "/api/runs", {"arm": str(self.arm_dir), "run_id": run_id,
                                                    "features": list(features), "specs": SPECS})
        self.assertEqual(answer.status, 201, answer.raw)
        return answer.json

    def mcp(self, body, agent="agent1", run_id="pair1", headers=None):
        all_headers = {"Accept": "application/json, text/event-stream"}
        all_headers.update(headers or {})
        return self.request("POST", f"/mcp/{run_id}/{agent}", body, all_headers)

    def initialize(self, agent="agent1"):
        answer = self.mcp(rpc_message("initialize", INIT_PARAMS), agent)
        self.assertEqual(answer.status, 200, answer.raw)
        return answer

    def call_tool(self, name, arguments=None, agent="agent1", request_id=7):
        params = {"name": name, "arguments": arguments or {}}
        return self.mcp(rpc_message("tools/call", params, request_id), agent)

    def rpc(self, tool, arguments=None, agent="agent1", run_id="pair1"):
        return self.request("POST", f"/rpc/{run_id}/{agent}", {"tool": tool, "arguments": arguments or {}})


class ApiTest(HubServerCase):
    def test_health_counts_runs(self):
        self.assertEqual(self.request("GET", "/api/health").json, {"ok": True, "runs": 0})
        self.create()
        self.assertEqual(self.request("GET", "/api/health").json, {"ok": True, "runs": 1})

    def test_create_answers_agents_with_urls_and_prompts(self):
        created = self.create()
        base = f"http://127.0.0.1:{self.port}"
        self.assertEqual(created["run_id"], "pair1")
        self.assertEqual(created["arm"], "testarm")
        first, second = created["agents"]
        self.assertEqual((first["id"], first["role"], first["features"]), ("agent1", "peer", [1]))
        self.assertEqual(second["features"], [2])
        self.assertEqual(first["mcp_url"], base + "/mcp/pair1/agent1")
        self.assertEqual(first["rpc_url"], base + "/rpc/pair1/agent1")
        self.assertIn("Add the first feature.", first["prompt"])
        self.assertIn("patch.txt", first["prompt"])
        self.assertNotIn("Add the second feature.", first["prompt"])

    def test_get_and_close_a_run(self):
        self.create()
        self.assertEqual(self.rpc("send_message", {"to": "agent2", "text": "hi"}).status, 200)
        state = self.request("GET", "/api/runs/pair1").json
        self.assertEqual(state["run_id"], "pair1")
        self.assertFalse(state["closed"])
        self.assertNotIn("prompt", state["agents"][0])
        self.assertEqual(state["agents"][0]["mcp_url"], f"http://127.0.0.1:{self.port}/mcp/pair1/agent1")
        self.assertEqual(state["metrics"]["messages_sent"], 1)
        closed = self.request("POST", "/api/runs/pair1/close")
        self.assertEqual(closed.status, 200)
        self.assertEqual(closed.json["metrics"]["messages_sent"], 1)
        self.assertTrue(self.request("GET", "/api/runs/pair1").json["closed"])

    def test_events_endpoint_lists_the_run_events(self):
        # A call over /rpc is framed like one over MCP: "tool" before the
        # handler, "tool_done" after it with what the call cost.
        self.create()
        self.rpc("send_message", {"to": "agent2", "text": "hi"})
        events = self.request("GET", "/api/runs/pair1/events").json["events"]
        self.assertEqual([event["kind"] for event in events], ["run_started", "tool", "message", "tool_done"])
        self.assertEqual(events[1]["via"], "shell")
        done = events[3]
        self.assertEqual((done["agent"], done["tool"], done["is_error"], done["cancelled"]),
                         ("agent1", "send_message", False, False))
        self.assertEqual(done["blocked_ms"], 0)
        self.assertGreaterEqual(done["duration_ms"], 0)
        self.assertLess(done["duration_ms"], 1000)

    def test_duplicate_run_id_is_409(self):
        self.create()
        again = self.request("POST", "/api/runs", {"arm": str(self.arm_dir), "run_id": "pair1",
                                                   "features": [1, 2], "specs": SPECS})
        self.assertEqual(again.status, 409)
        self.assertIn("error", again.json)

    def test_run_id_is_generated_when_absent(self):
        answer = self.request("POST", "/api/runs", {"arm": str(self.arm_dir), "features": [1, 2],
                                                    "specs": SPECS})
        self.assertEqual(answer.status, 201)
        self.assertRegex(answer.json["run_id"], r"^r[0-9a-f]{8}$")

    def test_bad_create_requests_are_400(self):
        missing_spec = {"arm": str(self.arm_dir), "features": [1, 3], "specs": SPECS}
        unknown_arm = {"arm": str(self.dir / "nowhere"), "features": [1], "specs": SPECS}
        bad_id = {"arm": str(self.arm_dir), "run_id": "../up", "features": [1], "specs": SPECS}
        for body in (missing_spec, unknown_arm, bad_id):
            answer = self.request("POST", "/api/runs", body)
            self.assertEqual(answer.status, 400, body)
            self.assertIn("error", answer.json)
        self.assertEqual(self.request("GET", "/api/health").json["runs"], 0)

    def test_unknown_run_and_routes_are_404(self):
        self.create()
        self.assertEqual(self.request("GET", "/api/runs/nope").status, 404)
        self.assertEqual(self.request("POST", "/api/runs/nope/close").status, 404)
        self.assertEqual(self.request("GET", "/elsewhere").status, 404)
        self.assertEqual(self.mcp(rpc_message("ping"), run_id="nope").status, 404)
        self.assertEqual(self.mcp(rpc_message("ping"), agent="agent9").status, 404)
        self.assertIn("error", self.mcp(rpc_message("ping"), agent="agent9").json)

    def test_events_are_written_under_the_log_dir(self):
        self.create()
        self.rpc("send_message", {"to": "agent2", "text": "hello"})
        path = self.dir / "logs" / "pair1" / "events.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([e["kind"] for e in events], ["run_started", "tool", "message", "tool_done"])
        self.assertEqual((events[2]["to"], events[2]["content"]), ("agent2", "hello"))
        self.assertIn("duration_ms", events[3])

    def test_delete_releases_the_run(self):
        # A released run is gone from the hub: its events, prompts and
        # sessions with it, so a long-lived hub does not keep every run.
        self.create()
        self.rpc("send_message", {"to": "agent2", "text": "hi"})
        released = self.request("DELETE", "/api/runs/pair1")
        self.assertEqual(released.status, 200, released.raw)
        self.assertEqual(released.json, {"run_id": "pair1", "released": True})
        self.assertEqual(self.request("GET", "/api/runs/pair1").status, 404)
        self.assertEqual(self.request("DELETE", "/api/runs/pair1").status, 404)
        self.assertEqual(self.rpc("peek_messages", agent="agent2").status, 404)
        self.assertEqual(self.request("GET", "/api/health").json["runs"], 0)
        self.assertEqual(self.create()["run_id"], "pair1")

    def test_repeated_or_too_many_features_are_400(self):
        for features in ([1, 1], [1, 2, 1], list(range(1, hub_module.MAX_FEATURES + 2))):
            specs = {str(number): f"Add feature {number}." for number in features}
            body = {"arm": str(self.arm_dir), "features": features, "specs": specs}
            answer = self.request("POST", "/api/runs", body)
            self.assertEqual(answer.status, 400, features)
            self.assertIn("error", answer.json)
        self.assertEqual(self.request("GET", "/api/health").json["runs"], 0)


class McpTest(HubServerCase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_handshake_and_a_message_between_agents(self):
        hello = self.initialize("agent1")
        session = hello.headers["Mcp-Session-Id"]
        self.assertRegex(session, r"^[0-9a-f]{32}$")
        result = hello.json["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        # The test arm's own instructions, through Arm.instructions.
        self.assertEqual(result["instructions"], "testarm run pair1: you are agent1")
        with_session = {"Mcp-Session-Id": session}
        initialized = self.mcp(rpc_message("notifications/initialized", request_id=None),
                               headers=with_session)
        self.assertEqual(initialized.status, 202)
        self.assertEqual(initialized.raw, b"")
        listed = self.mcp(rpc_message("tools/list", request_id=2), headers=with_session).json
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertIn("send_message", names)
        self.assertIn("inputSchema", listed["result"]["tools"][0])
        sent = self.mcp(rpc_message("tools/call", {"name": "send_message",
                                                   "arguments": {"to": "agent2", "text": "I take parser.py"}},
                                    request_id=3), headers=with_session).json
        self.assertEqual(sent["result"]["content"][0]["text"], "sent to agent2")
        self.assertFalse(sent["result"]["isError"])
        self.initialize("agent2")
        received = self.call_tool("receive_messages", agent="agent2").json["result"]
        messages = json.loads(received["content"][0]["text"])
        self.assertEqual([(m["from"], m["content"]) for m in messages], [("agent1", "I take parser.py")])

    def test_each_initialize_gets_its_own_session(self):
        first = self.initialize().headers["Mcp-Session-Id"]
        second = self.initialize().headers["Mcp-Session-Id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.mcp(rpc_message("ping"), headers={"Mcp-Session-Id": first}).status, 200)

    def test_ping(self):
        answer = self.mcp(rpc_message("ping", request_id="p"))
        self.assertEqual(answer.json, {"jsonrpc": "2.0", "id": "p", "result": {}})
        self.assertNotIn("Mcp-Session-Id", answer.headers)

    def test_a_batch_answers_each_request(self):
        batch = [rpc_message("ping", request_id=1), rpc_message("notifications/initialized", request_id=None),
                 rpc_message("tools/list", request_id=2)]
        answers = self.mcp(batch).json
        self.assertEqual([a["id"] for a in answers], [1, 2])
        self.assertIn("tools", answers[1]["result"])

    def test_unknown_method(self):
        answer = self.mcp(rpc_message("resources/list", request_id=4))
        self.assertEqual(answer.status, 200)
        self.assertEqual(answer.json["error"]["code"], -32601)

    def test_unknown_tool(self):
        self.assertEqual(self.call_tool("no_such_tool").json["error"]["code"], -32602)

    def test_arguments_that_are_not_an_object_are_refused(self):
        # [], false, 0 and "" are not objects: the call is refused, over
        # MCP and /rpc alike, rather than run with no arguments at all,
        # which `or {}` once made of them. Only a missing (or null) key
        # means no arguments.
        for arguments in ([], False, 0, ""):
            params = {"name": "anything", "arguments": arguments}
            answer = self.mcp(rpc_message("tools/call", params, request_id=3))
            self.assertEqual(answer.json["error"]["code"], -32602, repr(arguments))
            self.assertIn("must be an object", answer.json["error"]["message"])
            over_rpc = self.request("POST", "/rpc/pair1/agent1", {"tool": "anything", "arguments": arguments})
            self.assertEqual(over_rpc.status, 400, repr(arguments))
            self.assertIn("must be an object", over_rpc.json["error"])
        self.assertEqual(self.mcp(rpc_message("tools/call", {"name": "anything"}, request_id=4)).status, 200)
        self.assertNotIn("error", self.mcp(rpc_message("tools/call", {"name": "anything", "arguments": None},
                                                       request_id=5)).json)
        self.assertEqual(self.request("POST", "/rpc/pair1/agent1", {"tool": "anything"}).status, 200)
        # The three calls above that carried no arguments ran; the eight refused ones left no event.
        self.assertEqual(sum(1 for e in self.hub.runs["pair1"].run.events if e["kind"] == "tool"), 3)

    def test_a_refusing_tool_is_an_error_result(self):
        result = self.call_tool("refuse").json["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "refused on purpose")

    def test_a_crashing_tool_fails_that_call_only_without_traceback(self):
        # A bug in an arm is the arm's, not the agent's: the call comes back
        # as a failed result naming the tool, the session goes on, and the
        # traceback stays in the hub's log.
        with contextlib.redirect_stderr(io.StringIO()) as log:
            answer = self.call_tool("crash", request_id=9)
        self.assertEqual(answer.status, 200)
        self.assertEqual(answer.json["id"], 9)
        self.assertTrue(answer.json["result"]["isError"])
        self.assertIn("crash tool failed", answer.json["result"]["content"][0]["text"])
        self.assertNotIn(b"Traceback", answer.raw)
        self.assertNotIn(b"a bug in the arm", answer.raw)
        self.assertIn("a bug in the arm", log.getvalue())
        self.assertEqual(self.call_tool("peek_messages", request_id=10).status, 200)

    def test_unknown_session_is_404(self):
        self.initialize()
        answer = self.mcp(rpc_message("ping"), headers={"Mcp-Session-Id": "0" * 32})
        self.assertEqual(answer.status, 404)
        other_agents = self.initialize("agent2").headers["Mcp-Session-Id"]
        self.assertEqual(self.mcp(rpc_message("ping"), headers={"Mcp-Session-Id": other_agents}).status, 404)

    def test_get_and_delete_are_405(self):
        for method in ("GET", "DELETE"):
            answer = self.request(method, "/mcp/pair1/agent1", headers={"Accept": "text/event-stream"})
            self.assertEqual(answer.status, 405, method)
            self.assertEqual(answer.headers["Allow"], "POST")

    def test_origin_must_be_local(self):
        for origin in ("http://evil.example", "null", "https://localhost.evil.example"):
            answer = self.mcp(rpc_message("ping"), headers={"Origin": origin})
            self.assertEqual(answer.status, 403, origin)
        hostile = {"Origin": "http://evil.example"}
        self.assertEqual(self.request("GET", "/api/health", headers=hostile).status, 403)
        for origin in ("http://localhost:3000", "https://127.0.0.1", "http://host.docker.internal:8765"):
            self.assertEqual(self.mcp(rpc_message("ping"), headers={"Origin": origin}).status, 200, origin)

    def test_accept_must_take_json(self):
        self.assertEqual(self.mcp(rpc_message("ping"), headers={"Accept": "text/html"}).status, 406)
        self.assertEqual(self.mcp(rpc_message("ping"), headers={"Accept": "*/*"}).status, 200)
        self.assertEqual(self.mcp(rpc_message("ping"), headers={"Accept": "application/json"}).status, 200)

    def test_a_body_over_a_megabyte_is_413(self):
        # Only the headers go out: the hub refuses on Content-Length alone
        # and closes, and an unsent body keeps that close clean.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.putrequest("POST", "/mcp/pair1/agent1")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(hub_module.MAX_BODY + 1))
            connection.endheaders()
            answer = Answer(connection.getresponse())
        finally:
            connection.close()
        self.assertEqual(answer.status, 413)
        self.assertEqual(answer.headers["Connection"], "close")

    def test_one_connection_serves_requests_after_a_refusal(self):
        # MCP clients keep their connection open, so a refused request must
        # leave nothing of its body behind to be read as the next request.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        accept = {"Accept": "application/json", "Content-Type": "application/json"}
        try:
            for path, status in (("/mcp/nope/agent1", 404), ("/mcp/pair1/agent1", 200)):
                connection.request("POST", path, body=json.dumps(rpc_message("ping")), headers=accept)
                answer = Answer(connection.getresponse())
                self.assertEqual(answer.status, status, path)
        finally:
            connection.close()

    def test_a_body_that_is_not_json_is_a_parse_error_with_status_400(self):
        # Input the hub cannot read gets an HTTP error status, as the
        # transport asks, and the error names no id: there is none to
        # name, and MCP allows no null id.
        answer = self.mcp(b"{not json")
        self.assertEqual(answer.status, 400)
        self.assertEqual(answer.json["error"]["code"], -32700)
        self.assertNotIn("id", answer.json)

    def test_a_lone_message_that_is_no_request_is_400(self):
        for body in ([], {"hello": "hub"}, rpc_message("ping", request_id=None) | {"id": None},
                     rpc_message("ping", request_id=True), rpc_message("ping", request_id=1.5)):
            answer = self.mcp(body)
            self.assertEqual(answer.status, 400, body)
            self.assertEqual(answer.json["error"]["code"], -32600, body)
            self.assertNotIn("id", answer.json, body)

    def test_a_request_with_a_bad_shape_is_refused_with_its_id(self):
        # Each of these is -32600 or -32602 with the request's id, where
        # the hub used to fail on them inside a handler (-32603).
        not_a_string = self.mcp({"jsonrpc": "2.0", "id": 5, "method": ["tools/list"]})
        self.assertEqual((not_a_string.status, not_a_string.json["id"]), (200, 5))
        self.assertEqual(not_a_string.json["error"]["code"], -32600)
        # MCP has no by-position params, so a list is refused for ping and
        # tools/list as much as for the methods that read their params.
        for method in ("initialize", "tools/call", "ping", "tools/list"):
            listed = self.mcp({"jsonrpc": "2.0", "id": 6, "method": method, "params": [1]})
            self.assertEqual((listed.status, listed.json["id"]), (200, 6), method)
            self.assertEqual(listed.json["error"]["code"], -32602, method)
        named_by_list = self.call_tool(["send_message"], request_id=8)
        self.assertEqual((named_by_list.status, named_by_list.json["id"]), (200, 8))
        self.assertEqual(named_by_list.json["error"]["code"], -32602)
        self.assertEqual(self.mcp(rpc_message("ping") | {"params": None}).status, 200)

    def test_a_bad_batch_member_fails_alone(self):
        # The tool call before the bad member ran and is answered as such;
        # a client that saw -32603 for it would retry a message already sent.
        batch = [rpc_message("tools/call", {"name": "send_message",
                                            "arguments": {"to": "agent2", "text": "once"}}, 1),
                 rpc_message("ping", request_id=True),
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": [1]},
                 rpc_message("ping", request_id=3)]
        answer = self.mcp(batch)
        self.assertEqual(answer.status, 200)
        answers = answer.json
        self.assertEqual([a.get("id") for a in answers], [1, None, 2, 3])
        self.assertEqual(answers[0]["result"]["content"][0]["text"], "sent to agent2")
        self.assertNotIn("id", answers[1])
        self.assertEqual(answers[1]["error"]["code"], -32600)
        self.assertEqual(answers[2]["error"]["code"], -32602)
        self.assertEqual(answers[3]["result"], {})
        self.assertEqual(self.rpc("peek_messages", agent="agent2").json["text"], "1")

    def test_a_hub_failure_answers_only_the_request_it_failed_on(self):
        batch = [rpc_message("ping", request_id=1), rpc_message("tools/list", request_id=2),
                 rpc_message("ping", request_id=3)]
        broken = mock.patch.object(type(self.hub.hosted("pair1").run.arm), "tools_for",
                                   side_effect=RuntimeError("a bug in the arm's listing"))
        with broken, contextlib.redirect_stderr(io.StringIO()) as log:
            answers = self.mcp(batch).json
        self.assertEqual([a["id"] for a in answers], [1, 2, 3])
        self.assertEqual(answers[0]["result"], {})
        self.assertEqual(answers[1]["error"]["code"], -32603)
        self.assertEqual(answers[2]["result"], {})
        self.assertIn("a bug in the arm's listing", log.getvalue())

    def test_an_initialize_refused_for_its_shape_opens_no_session(self):
        refused = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1]})
        self.assertEqual(refused.status, 200)
        self.assertIn("error", refused.json)
        self.assertNotIn("Mcp-Session-Id", refused.headers)
        unnamed = self.mcp({"jsonrpc": "2.0", "id": None, "method": "initialize", "params": INIT_PARAMS})
        self.assertEqual(unnamed.status, 400)
        self.assertNotIn("Mcp-Session-Id", unnamed.headers)

    def test_initialize_carries_no_instructions_unless_the_arm_gives_some(self):
        # CooperBench's agents get no server instructions (its team server
        # sends none, its other modes have no server), so neither do the
        # built-in arms: the key is left out, not sent empty.
        created = self.request("POST", "/api/runs", {"arm": "coop", "run_id": "plain", "features": [1, 2],
                                                     "specs": SPECS})
        self.assertEqual(created.status, 201, created.raw)
        hello = self.mcp(rpc_message("initialize", INIT_PARAMS), run_id="plain")
        self.assertEqual(hello.status, 200)
        self.assertNotIn("instructions", hello.json["result"])

    def test_an_unsupported_protocol_version_is_400_except_on_initialize(self):
        # Claude Code probes with a newer binding's version first and reads
        # a 4xx as "this server wants the initialize handshake".
        probe = self.mcp(rpc_message("ping"), headers={"MCP-Protocol-Version": "2026-07-28"})
        self.assertEqual(probe.status, 400)
        self.assertEqual(probe.json["error"]["code"], -32600)
        self.assertNotIn("id", probe.json)
        self.assertIn("2026-07-28", probe.json["error"]["message"])
        hello = self.mcp(rpc_message("initialize", INIT_PARAMS),
                         headers={"MCP-Protocol-Version": "2026-07-28"})
        self.assertEqual(hello.status, 200)
        self.assertEqual(hello.json["result"]["protocolVersion"], "2025-06-18")
        for version in ("2025-06-18", "2025-03-26", "2024-11-05"):
            answer = self.mcp(rpc_message("ping"), headers={"MCP-Protocol-Version": version})
            self.assertEqual(answer.status, 200, version)

    def test_a_dropped_connection_leaves_no_traceback(self):
        # Claude Code drops its keep-alive connection each time it exits;
        # the reset lands in the handler's wait for the next request line,
        # which only the server's handle_error sees. The hub log must not
        # read as if the hub had failed.
        handled = threading.Event()
        original = hub_module.HubServer.handle_error

        def watched(server, request, client_address):
            try:
                original(server, request, client_address)
            finally:
                handled.set()

        with mock.patch.object(hub_module.HubServer, "handle_error", watched), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            client = socket.create_connection(("127.0.0.1", self.port), timeout=10)
            client.sendall(b"POST /mcp/pair1/agent1 HTTP/1.1\r\nContent-Len")
            # Closing with a zero linger sends a reset instead of a FIN.
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()
            self.assertTrue(handled.wait(timeout=10), "the server never saw the dropped connection")
        self.assertEqual(log.getvalue(), "")
        self.assertEqual(self.mcp(rpc_message("ping")).status, 200)

    def test_wait_for_message_blocks_only_its_caller(self):
        self.initialize("agent2")
        outcome = {}

        def wait():
            started = time.monotonic()
            answer = self.call_tool("wait_for_message", {"timeout_ms": 20000}, agent="agent2")
            outcome["elapsed"] = time.monotonic() - started
            outcome["answer"] = answer.json

        waiter = threading.Thread(target=wait)
        waiter.start()
        time.sleep(0.3)
        self.assertTrue(waiter.is_alive(), "wait_for_message returned before any message")
        started = time.monotonic()
        sent = self.rpc("send_message", {"to": "agent2", "text": "done with parser.py"})
        self.assertEqual(sent.status, 200)
        self.assertLess(time.monotonic() - started, 2.0)
        waiter.join(timeout=10)
        self.assertFalse(waiter.is_alive())
        self.assertLess(outcome["elapsed"], 5.0)
        message = json.loads(outcome["answer"]["result"]["content"][0]["text"])
        self.assertEqual((message["from"], message["content"]), ("agent1", "done with parser.py"))

    def test_a_cancelled_wait_gives_the_marker_at_once_and_takes_nothing(self):
        # Claude Code gives up on a call with notifications/cancelled, a
        # second POST on the same session. The wait ends with the marker,
        # well before its timeout, and a message sent afterwards is still
        # in the inbox for the next call. A cancellation for an id not in
        # flight, or on another session, is taken in (202) and changes
        # nothing, as the specification allows: its answer may have
        # crossed it.
        session = {"Mcp-Session-Id": self.initialize("agent2").headers["Mcp-Session-Id"]}
        run = self.hub.hosted("pair1").run
        outcome = {}

        def wait():
            started = time.monotonic()
            wait_call = rpc_message("tools/call", {"name": "wait_for_message",
                                                   "arguments": {"timeout_ms": 20000}}, request_id=11)
            answer = self.mcp(wait_call, agent="agent2", headers=session)
            outcome["elapsed"] = time.monotonic() - started
            outcome["answer"] = answer.json

        def cancel(request_id, headers):
            notice = rpc_message("notifications/cancelled", {"requestId": request_id, "reason": "gave up"},
                                 request_id=None)
            return self.mcp(notice, agent="agent2", headers=headers)

        waiter = threading.Thread(target=wait)
        waiter.start()
        # The "tool" event is emitted once the call is in flight.
        self.assertTrue(run.wait_until(lambda: bool(run.events), 5))
        for stray in (cancel(999, session), cancel(11, {})):
            self.assertEqual((stray.status, stray.raw), (202, b""))
        time.sleep(0.3)
        self.assertTrue(waiter.is_alive(), "a cancellation of another id or session ended the wait")
        self.assertEqual(cancel(11, session).status, 202)
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive(), "the cancellation did not end the wait")
        self.assertLess(outcome["elapsed"], 5.0)
        self.assertEqual(outcome["answer"]["result"]["content"][0]["text"], kit.NO_MESSAGE)
        self.assertEqual(self.rpc("send_message", {"to": "agent2", "text": "after"}).status, 200)
        self.assertEqual(contents(self.rpc("receive_messages", agent="agent2").json["text"]), ["after"])
        kinds = [event["kind"] for event in self.hub.events("pair1")]
        self.assertEqual(kinds.count("receive"), 1)
        done = [e for e in self.hub.events("pair1") if e["kind"] == "tool_done" and e["tool"] == "wait_for_message"]
        self.assertEqual([(e["agent"], e["cancelled"], e["is_error"]) for e in done], [("agent2", True, False)])

    def test_a_cancel_that_lands_after_the_take_puts_the_message_back(self):
        # The wait had popped its message and not yet answered when the
        # cancel arrived. The answer still carries the message, which the
        # client discards as the specification tells it to, so the hub
        # puts the message back for the next call and the record says so.
        session = {"Mcp-Session-Id": self.initialize("agent2").headers["Mcp-Session-Id"]}
        run = self.hub.hosted("pair1").run
        original = run.emit
        cancel_statuses = []

        def emit_then_cancel(kind, agent=None, **data):
            # The "receive" event follows the pop, inside the handler.
            event = original(kind, agent, **data)
            if kind == "receive" and not cancel_statuses:
                notice = rpc_message("notifications/cancelled", {"requestId": 11, "reason": "gave up"},
                                     request_id=None)
                cancel_statuses.append(self.mcp(notice, agent="agent2", headers=session).status)
            return event

        self.rpc("send_message", {"to": "agent2", "text": "taken"})
        wait_call = rpc_message("tools/call", {"name": "wait_for_message", "arguments": {"timeout_ms": 5000}},
                                request_id=11)
        with mock.patch.object(run, "emit", emit_then_cancel):
            answer = self.mcp(wait_call, agent="agent2", headers=session)
        self.assertEqual(cancel_statuses, [202])
        self.assertEqual(answer.status, 200)
        self.assertEqual(json.loads(answer.json["result"]["content"][0]["text"])["content"], "taken")
        self.assertEqual(contents(self.rpc("receive_messages", agent="agent2").json["text"]), ["taken"])
        events = self.hub.events("pair1")
        kinds = [e["kind"] for e in events if e["kind"].startswith("receive") or e["kind"] == "tool_done"]
        # The send's end, the wait's take and its put-back, the wait's end,
        # then the drain that found the message again, and its end.
        self.assertEqual(kinds, ["tool_done", "receive", "receive_lost", "tool_done", "receive", "tool_done"])
        waited = [e for e in events if e["kind"] == "tool_done" and e["tool"] == "wait_for_message"]
        self.assertEqual([(e["cancelled"], e["is_error"]) for e in waited], [(True, False)])

    def test_every_tool_call_is_framed_by_tool_and_tool_done_events(self):
        # The pair tells a slow hub from an agent that chose to wait:
        # duration_ms is the handler's wall time, blocked_ms the part of
        # it spent waiting for another agent, which the handler books.
        self.initialize("agent2")
        run = self.hub.hosted("pair1").run
        outcome = {}

        def doze():
            outcome["answer"] = self.call_tool("doze", {"seconds": 20}, agent="agent2").json

        dozer = threading.Thread(target=doze)
        dozer.start()
        self.assertTrue(run.wait_until(lambda: any(e["kind"] == "tool" for e in run.events), 5))
        time.sleep(0.3)
        self.assertEqual(self.rpc("poke").json["text"], "poked")
        dozer.join(timeout=10)
        self.assertFalse(dozer.is_alive())
        self.assertEqual(outcome["answer"]["result"]["content"][0]["text"], "dozed")
        events = self.hub.events("pair1")[1:]
        framed = [(e["kind"], e["agent"], e["tool"]) for e in events]
        self.assertEqual(framed, [("tool", "agent2", "doze"), ("tool", "agent1", "poke"),
                                  ("tool_done", "agent1", "poke"), ("tool_done", "agent2", "doze")])
        self.assertEqual([e["via"] for e in events[:2]], ["mcp", "shell"])
        poked, dozed = events[2], events[3]
        self.assertEqual(set(dozed), {"seq", "t", "kind", "agent", "tool", "duration_ms", "blocked_ms",
                                      "is_error", "cancelled"})
        self.assertEqual((dozed["is_error"], dozed["cancelled"]), (False, False))
        self.assertGreater(dozed["blocked_ms"], 200)
        self.assertLess(dozed["blocked_ms"], dozed["duration_ms"])
        self.assertLess(dozed["duration_ms"], 5000)
        self.assertGreater(dozed["t"], events[0]["t"])
        self.assertEqual(poked["blocked_ms"], 0)
        self.assertLessEqual(0, poked["duration_ms"])

    def test_tool_done_marks_a_refusal_and_a_crash_as_errors(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.call_tool("refuse")
            self.call_tool("crash")
        kinds = [e["kind"] for e in self.hub.events("pair1")]
        self.assertEqual(kinds, ["run_started", "tool", "tool_done", "tool", "tool_crash", "tool_done"])
        done = [(e["tool"], e["is_error"], e["cancelled"]) for e in self.hub.events("pair1")
                if e["kind"] == "tool_done"]
        self.assertEqual(done, [("refuse", True, False), ("crash", True, False)])

    def test_an_argument_the_tool_does_not_take_is_refused_before_it_runs(self):
        # The pilot's bug: an agent typed timeout for timeout_ms and waited
        # the default five seconds without a word. The refusal names the
        # stray key and the names the tool takes, and nothing ran.
        started = time.monotonic()
        answer = self.call_tool("wait_for_message", {"timeout": 180}, request_id=12)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual((answer.status, answer.json["id"]), (200, 12))
        self.assertEqual(answer.json["error"]["code"], -32602)
        self.assertIn("'timeout'", answer.json["error"]["message"])
        self.assertIn("timeout_ms", answer.json["error"]["message"])
        self.assertEqual([e["kind"] for e in self.hub.events("pair1")], ["run_started"])
        # A schema open to other names, or one naming none, takes any name.
        anything = self.call_tool("anything", {"known": "k", "other": 1}).json["result"]
        self.assertEqual(json.loads(anything["content"][0]["text"]), {"known": "k", "other": 1})
        drained = self.call_tool("receive_messages", {"stray": True}).json["result"]
        self.assertEqual(drained["content"][0]["text"], "[]")

    def test_an_object_that_is_neither_request_nor_response_is_invalid(self):
        # JSON-RPC says -32600; the hub used to take it for a response and
        # answer nothing. With a readable id the error carries it, else
        # it is id-less, so a lone one is a 400.
        for body in ({"jsonrpc": "2.0"}, {"jsonrpc": "2.0", "foo": 1}, {"jsonrpc": "2.0", "id": None}):
            answer = self.mcp(body)
            self.assertEqual(answer.status, 400, body)
            self.assertEqual(answer.json["error"]["code"], -32600, body)
            self.assertNotIn("id", answer.json, body)
        named = self.mcp({"jsonrpc": "2.0", "id": 1})
        self.assertEqual((named.status, named.json["id"], named.json["error"]["code"]), (200, 1, -32600))
        batch = self.mcp([{"jsonrpc": "2.0", "id": 5}, rpc_message("ping", request_id=6)]).json
        self.assertEqual([(a.get("id"), "error" in a) for a in batch], [(5, True), (6, False)])
        for body in ({"jsonrpc": "2.0", "id": 1, "result": {}},
                     {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "theirs"}}):
            self.assertEqual(self.mcp(body).status, 202, body)

    def test_a_body_nested_too_deep_to_parse_is_a_parse_error(self):
        # json.loads gives up on it with a RecursionError, which used to
        # end in a 500 with tracebacks; the client sent it, so it is a
        # parse error wherever the hub reads a body.
        deep = b"[" * 100000
        with contextlib.redirect_stderr(io.StringIO()) as log:
            answer = self.mcp(deep)
            probe = self.mcp(deep, headers={"MCP-Protocol-Version": "2026-07-28"})
            over_rpc = self.request("POST", "/rpc/pair1/agent1", deep)
            create = self.request("POST", "/api/runs", deep)
        self.assertEqual(answer.status, 400)
        self.assertEqual(answer.json["error"]["code"], -32700)
        self.assertNotIn("id", answer.json)
        self.assertEqual(probe.status, 400)
        self.assertEqual(probe.json["error"]["code"], -32600)
        self.assertEqual(over_rpc.status, 400)
        self.assertIn("error", over_rpc.json)
        self.assertEqual(create.status, 400)
        self.assertIn("error", create.json)
        self.assertEqual(log.getvalue(), "")
        self.assertEqual(self.mcp(rpc_message("ping")).status, 200)

    def test_a_session_is_issued_only_for_initializes_own_result_under_its_own_id(self):
        # An initialize refused for its shape gets no session from a ping
        # answered beside it: 1 is not true, 0 is not false, and a ping's
        # empty result under a shared id is not initialize's.
        refused = ([rpc_message("initialize", INIT_PARAMS, True), rpc_message("ping", request_id=1)],
                   [rpc_message("initialize", INIT_PARAMS, False), rpc_message("ping", request_id=0)],
                   [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [1]},
                    rpc_message("ping", request_id=1)])
        for batch in refused:
            answer = self.mcp(batch)
            self.assertEqual(answer.status, 200, batch)
            self.assertTrue(any("error" in a for a in answer.json), batch)
            self.assertNotIn("Mcp-Session-Id", answer.headers, batch)
        opened = self.mcp([rpc_message("ping", request_id=1), rpc_message("initialize", INIT_PARAMS, "1")])
        self.assertEqual(opened.status, 200)
        self.assertIn("Mcp-Session-Id", opened.headers)

    def test_a_lost_reply_puts_the_messages_back_at_the_head_of_the_inbox(self):
        # The client dropped its connection while two waits of one batch
        # took the first two messages; the write of the answer fails, the
        # hub runs the calls' undos newest first, and the next call reads
        # the inbox in its old order. The hub itself goes on, and logs
        # nothing: the drop was the client's doing.
        for text in ("first", "second", "third"):
            self.rpc("send_message", {"to": "agent2", "text": text})
        batch = [rpc_message("tools/call", {"name": "wait_for_message", "arguments": {"timeout_ms": 1000}},
                             request_id=request_id) for request_id in (11, 12)]
        with dropped_replies(lambda payload: isinstance(payload, list) and payload[0].get("id") == 11), \
                contextlib.redirect_stderr(io.StringIO()) as log, self.assertRaises(ConnectionError):
            self.mcp(batch, agent="agent2")
        self.assertEqual(log.getvalue(), "")
        self.assertEqual(contents(self.rpc("receive_messages", agent="agent2").json["text"]),
                         ["first", "second", "third"])
        kinds = [event["kind"] for event in self.hub.events("pair1") if event["kind"].startswith("receive")]
        self.assertEqual(kinds, ["receive", "receive", "receive_lost", "receive_lost", "receive"])


class TraceTest(HubServerCase):
    """The hub's events and timings go through trace.py. A run's own record
    keeps its shape exactly, the hub records nothing of its own in prod,
    and --debug records every request, wait, cancel and session."""

    def test_a_tool_and_tool_done_pair_is_whole_as_before(self):
        self.create()
        self.rpc("poke")
        _, started, done = self.hub.events("pair1")
        self.assertEqual(started, {"seq": 1, "t": started["t"], "kind": "tool", "agent": "agent1", "tool": "poke",
                                   "via": "shell"})
        self.assertEqual(done, {"seq": 2, "t": done["t"], "kind": "tool_done", "agent": "agent1", "tool": "poke",
                                "duration_ms": done["duration_ms"], "blocked_ms": 0, "is_error": False,
                                "cancelled": False})
        self.assertEqual(list(done), ["seq", "t", "kind", "agent", "tool", "duration_ms", "blocked_ms",
                                      "is_error", "cancelled"])
        self.assertIsInstance(done["duration_ms"], float)
        self.assertEqual(done["duration_ms"], round(done["duration_ms"], 3))
        self.assertGreaterEqual(done["t"], started["t"])
        self.assertIsInstance(started["t"], float)

    def test_events_jsonl_holds_the_runs_events_line_for_line_in_their_shape(self):
        self.create()
        self.rpc("send_message", {"to": "agent2", "text": "hi"})
        self.rpc("poke")
        events = self.hub.events("pair1")
        written = read_jsonl(self.dir / "logs" / "pair1" / "events.jsonl")
        self.assertEqual(written, events)
        self.assertEqual([list(line) for line in written], [list(event) for event in events])
        self.assertEqual([e["kind"] for e in written],
                         ["run_started", "tool", "message", "tool_done", "tool", "tool_done"])
        for event in written:
            self.assertEqual(list(event)[:4], ["seq", "t", "kind", "agent"])
            self.assertNotIn("ts", event)
            self.assertNotIn("run", event)

    def test_the_runs_events_reach_the_stream_under_its_id_and_prod_adds_nothing(self):
        self.assertEqual(trace.level, "prod")
        memory = listen_in_test(self)
        self.create()
        self.rpc("poke")
        self.assertEqual([(e["kind"], e["run"]) for e in memory],
                         [("run_started", "pair1"), ("tool", "pair1"), ("tool_done", "pair1")],
                         "in prod the stream carries the runs' events and nothing of the hub's own")
        started = memory[1]
        self.assertEqual(set(started), {"kind", "t", "ts", "run", "seq", "agent", "tool", "via"})
        self.assertEqual((started["seq"], started["agent"], started["tool"]), (1, "agent1", "poke"))
        self.assertEqual(started["t"], self.hub.events("pair1")[1]["t"], "t is the run's clock, not trace's")
        self.assertEqual(memory[2]["duration_ms"], self.hub.events("pair1")[2]["duration_ms"])

    def test_debug_records_one_http_request_per_request_with_its_fields(self):
        memory = listen_in_test(self, "debug")
        health = self.request("GET", "/api/health")
        self.create()
        arguments = {"to": "agent2", "text": "hi"}
        sent = self.rpc("send_message", arguments)
        self.request("GET", "/nowhere")
        # Each event is emitted once its reply is on the wire, on the
        # request's own thread, so the client may hold the answer before
        # the event lands, and the next request's event may land first:
        # the test waits for the fourth and reads the events by path.
        deadline = time.monotonic() + 5
        while len([e for e in memory if e["kind"] == "http_request"]) < 4 and time.monotonic() < deadline:
            time.sleep(0.005)
        requests = {e["path"]: e for e in memory if e["kind"] == "http_request"}
        self.assertEqual(sorted((e["method"], e["path"], e["status"]) for e in requests.values()),
                         sorted([("GET", "/api/health", 200), ("POST", "/api/runs", 201),
                                 ("POST", "/rpc/pair1/agent1", 200), ("GET", "/nowhere", 404)]))
        self.assertEqual(len([e for e in memory if e["kind"] == "http_request"]), 4)
        for event in requests.values():
            self.assertEqual(set(event), {"kind", "t", "ts", "method", "path", "status", "request_bytes",
                                          "response_bytes", "duration_ms", "thread"})
            self.assertIsInstance(event["duration_ms"], float)
            self.assertGreaterEqual(event["duration_ms"], 0)
            self.assertEqual(event["duration_ms"], round(event["duration_ms"], 3))
            self.assertTrue(event["thread"])
        checked, called = requests["/api/health"], requests["/rpc/pair1/agent1"]
        self.assertEqual((checked["request_bytes"], checked["response_bytes"]), (0, len(health.raw)))
        self.assertEqual(called["request_bytes"],
                         len(json.dumps({"tool": "send_message", "arguments": arguments}).encode()))
        self.assertEqual(called["response_bytes"], len(sent.raw))
        sizes = [e for e in memory if e["kind"] == "tool_arguments"]
        self.assertEqual([(e["run"], e["agent"], e["tool"], e["bytes"]) for e in sizes],
                         [("pair1", "agent1", "send_message", len(json.dumps(arguments)))])
        self.assertNotIn("hi", json.dumps(sizes[0]), "the arguments' size is recorded, never their content")
        self.assertEqual([e["kind"] for e in self.hub.events("pair1")],
                         ["run_started", "tool", "message", "tool_done"],
                         "debug adds to the hub's stream, never to the run's record")
        self.assertEqual(read_jsonl(self.dir / "logs" / "pair1" / "events.jsonl"), self.hub.events("pair1"))

    def test_debug_records_each_session_opened_and_each_cancel(self):
        memory = listen_in_test(self, "debug")
        self.create()
        session = self.initialize("agent2").headers["Mcp-Session-Id"]
        opened = [e for e in memory if e["kind"] == "session_opened"]
        self.assertEqual([(e["run"], e["agent"], e["session"]) for e in opened], [("pair1", "agent2", session)])
        notice = rpc_message("notifications/cancelled", {"requestId": 99, "reason": "gave up"}, request_id=None)
        self.assertEqual(self.mcp(notice, agent="agent2", headers={"Mcp-Session-Id": session}).status, 202)
        cancels = [e for e in memory if e["kind"] == "cancel"]
        self.assertEqual([(e["request_id"], e["found"]) for e in cancels], [(99, False)])

    def test_the_log_is_held_open_from_the_first_event_and_closed_when_the_run_is_released(self):
        # The run's first event, run_started, comes as the run is made, so
        # the file is open from the create on.
        self.create()
        run = self.hub.hosted("pair1").run
        path = self.dir / "logs" / "pair1" / "events.jsonl"
        self.assertEqual([e["kind"] for e in read_jsonl(path)], ["run_started"])
        self.assertTrue(run.log_file.opened)
        self.rpc("poke")
        self.assertEqual(self.request("DELETE", "/api/runs/pair1").status, 200)
        self.assertFalse(run.log_file.opened)
        self.assertEqual([e["kind"] for e in read_jsonl(path)], ["run_started", "tool", "tool_done"])
        run.emit("late")
        self.assertEqual([e["kind"] for e in read_jsonl(path)], ["run_started", "tool", "tool_done", "late"])
        run.close_log()


class AgentTokenTest(HubServerCase):
    """A run made with agent_tokens wants each agent's own bearer token on
    its /mcp and /rpc: the transport asks a local server to authenticate
    every connection, and by URL alone one agent could speak as another."""

    def setUp(self):
        super().setUp()
        created = self.request("POST", "/api/runs", {"arm": str(self.arm_dir), "run_id": "pair1",
                                                     "features": [1, 2], "specs": SPECS, "agent_tokens": True})
        self.assertEqual(created.status, 201, created.raw)
        self.tokens = {agent["id"]: agent["token"] for agent in created.json["agents"]}

    def bearer(self, agent):
        return {"Authorization": f"Bearer {self.tokens[agent]}"}

    def test_the_create_answer_alone_carries_the_tokens(self):
        self.assertEqual(set(self.tokens), {"agent1", "agent2"})
        self.assertNotEqual(self.tokens["agent1"], self.tokens["agent2"])
        for token in self.tokens.values():
            self.assertRegex(token, r"^[A-Za-z0-9_-]{40,}$")
        self.assertEqual(self.hub.hosted("pair1").tokens, self.tokens)
        state = self.request("GET", "/api/runs/pair1")
        self.assertEqual(state.status, 200)
        self.assertFalse([agent for agent in state.json["agents"] if "token" in agent])
        described = self.request("GET", "/rpc/pair1/agent1", headers=self.bearer("agent1"))
        self.assertEqual(described.status, 200)
        for answer in (state, described, self.request("GET", "/api/runs/pair1/events")):
            for token in self.tokens.values():
                self.assertNotIn(token.encode(), answer.raw)

    def test_mcp_and_rpc_want_this_agents_own_token(self):
        wrong = ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer wrong"},
                 {"Authorization": f"bearer {self.tokens['agent1']}"}, self.bearer("agent2"))
        for headers in wrong:
            refused = self.mcp(rpc_message("ping"), headers=headers)
            self.assertEqual(refused.status, 401, headers)
            self.assertIn("agent1", refused.json["error"])
            self.assertEqual(self.request("GET", "/rpc/pair1/agent1", headers=headers).status, 401, headers)
            self.assertEqual(self.request("POST", "/rpc/pair1/agent1", {"tool": "peek_messages"}, headers).status,
                             401, headers)
            # /mcp takes POST only; a GET is 405 for the method, token or no token.
            self.assertEqual(self.request("GET", "/mcp/pair1/agent1", headers=headers).status, 405, headers)
        self.assertEqual(self.request("GET", "/mcp/pair1/agent1", headers=self.bearer("agent1")).status, 405)
        self.assertEqual(self.mcp(rpc_message("ping"), headers=self.bearer("agent1")).status, 200)
        hello = self.mcp(rpc_message("initialize", INIT_PARAMS), headers=self.bearer("agent1"))
        self.assertEqual(hello.status, 200)
        self.assertIn("Mcp-Session-Id", hello.headers)
        self.assertEqual(self.request("GET", "/rpc/pair1/agent1", headers=self.bearer("agent1")).status, 200)
        sent = self.request("POST", "/rpc/pair1/agent1",
                            {"tool": "send_message", "arguments": {"to": "agent2", "text": "hi"}},
                            self.bearer("agent1"))
        self.assertEqual(sent.json, {"text": "sent to agent2", "isError": False})
        peeked = self.request("POST", "/rpc/pair1/agent2", {"tool": "peek_messages"}, self.bearer("agent2"))
        self.assertEqual(peeked.json["text"], "1")

    def test_the_token_is_checked_after_the_origin_and_before_the_body(self):
        hostile = self.mcp(rpc_message("ping"), headers={"Origin": "http://evil.example", **self.bearer("agent1")})
        self.assertEqual(hostile.status, 403)
        self.assertEqual(self.mcp(b"{not json").status, 401)
        self.assertEqual(self.mcp(b"{not json", headers=self.bearer("agent1")).status, 400)
        self.assertEqual(self.request("POST", "/rpc/pair1/agent1", b"{not json").status, 401)
        self.assertEqual(self.mcp(rpc_message("ping"), agent="agent9", headers=self.bearer("agent1")).status, 404)

    def test_a_run_made_without_agent_tokens_stays_open(self):
        created = self.create("open")
        self.assertFalse([agent for agent in created["agents"] if "token" in agent])
        self.assertEqual(self.hub.hosted("open").tokens, {})
        for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Bearer anything"}):
            self.assertEqual(self.mcp(rpc_message("ping"), run_id="open", headers=headers).status, 200, headers)
            self.assertEqual(self.request("GET", "/rpc/open/agent1", headers=headers).status, 200, headers)

    def test_agent_tokens_must_be_true_or_false(self):
        for value in ("yes", 1, None, [True]):
            body = {"arm": str(self.arm_dir), "run_id": "typed", "features": [1], "specs": SPECS,
                    "agent_tokens": value}
            answer = self.request("POST", "/api/runs", body)
            self.assertEqual(answer.status, 400, value)
            self.assertIn("agent_tokens", answer.json["error"])
        self.assertEqual(self.request("GET", "/api/health").json["runs"], 1)


class RpcTest(HubServerCase):
    def setUp(self):
        super().setUp()
        self.create()

    def test_get_describes_the_agent_and_its_tools(self):
        answer = self.request("GET", "/rpc/pair1/agent1")
        self.assertEqual(answer.status, 200)
        described = answer.json
        self.assertEqual((described["agent"], described["role"]), ("agent1", "peer"))
        self.assertIn("Add the first feature.", described["prompt"])
        tools = {tool["name"]: tool for tool in described["tools"]}
        send = tools["send_message"]
        self.assertEqual(send["alias"], "coop-send")
        # As coop_msg.py's argparse: one word of text, and only the
        # recipient required (kit.py ports it; an empty text is accepted).
        self.assertEqual(send["argv"], ["to", "text"])
        self.assertEqual(send["inputSchema"]["required"], ["to"])
        self.assertTrue(send["description"])
        self.assertEqual(tools["refuse"]["alias"], "")

    def test_post_calls_a_tool(self):
        answer = self.rpc("send_message", {"to": "agent2", "text": "hi"})
        self.assertEqual(answer.json, {"text": "sent to agent2", "isError": False})
        self.assertEqual(self.rpc("peek_messages", agent="agent2").json["text"], "1")

    def test_a_tool_error_is_is_error(self):
        self.assertEqual(self.rpc("refuse").json, {"text": "refused on purpose", "isError": True, "exitCode": 1})
        missing = self.rpc("send_message", {"text": "to nobody"}).json
        self.assertTrue(missing["isError"])
        # kit.py refuses a missing recipient in argparse's words, as
        # coop-send does.
        self.assertIn("required: recipient", missing["text"])

    def test_unknown_tool_is_400(self):
        answer = self.rpc("no_such_tool")
        self.assertEqual(answer.status, 400)
        self.assertIn("error", answer.json)

    def test_a_tool_name_that_is_not_a_string_is_400(self):
        for name in (["send_message"], {"name": "send_message"}, 7, None):
            answer = self.rpc(name)
            self.assertEqual(answer.status, 400, name)
            self.assertIn("error", answer.json)

    def test_an_argument_the_tool_does_not_take_is_400_with_the_reason(self):
        # The shell command exits with the hub's reason, so an agent that
        # typed coop-wait timeout=180 reads which name the tool takes.
        answer = self.rpc("wait_for_message", {"timeout": 180})
        self.assertEqual(answer.status, 400)
        self.assertIn("'timeout'", answer.json["error"])
        self.assertIn("timeout_ms", answer.json["error"])
        self.assertEqual(self.rpc("send_message", {"to": "agent2", "text": "hi", "cc": "agent3"}).status, 400)
        self.assertEqual([e["kind"] for e in self.hub.events("pair1")], ["run_started"])
        self.assertEqual(self.rpc("wait_for_message", {"timeout_ms": 100}).json["text"], kit.NO_MESSAGE)

    def test_unknown_run_or_agent_is_404(self):
        self.assertEqual(self.rpc("list_agents", agent="agent9").status, 404)
        self.assertEqual(self.rpc("list_agents", run_id="nope").status, 404)
        self.assertEqual(self.request("GET", "/rpc/pair1/agent9").status, 404)

    def test_a_crashing_tool_is_a_failed_call_without_traceback(self):
        with contextlib.redirect_stderr(io.StringIO()):
            answer = self.rpc("crash")
        self.assertEqual(answer.status, 200)
        self.assertTrue(answer.json["isError"])
        self.assertNotIn(b"Traceback", answer.raw)

    def test_a_lost_reply_puts_the_message_back_at_the_head_of_the_inbox(self):
        # A shell command's wait took a message and its caller was gone
        # before the answer could be written: the message goes back
        # first, and the next command reads it.
        for text in ("first", "second"):
            self.rpc("send_message", {"to": "agent2", "text": text})

        def took_first(payload):
            return isinstance(payload, dict) and "first" in payload.get("text", "")

        with dropped_replies(took_first), self.assertRaises(ConnectionError):
            self.rpc("wait_for_message", {"timeout_ms": 1000}, agent="agent2")
        self.assertEqual(contents(self.rpc("receive_messages", agent="agent2").json["text"]), ["first", "second"])


class CallTest(unittest.TestCase):
    """What a Call carries for a tool that blocks: lost() runs the undos
    newest first under the run's lock, wakes the run, and lets a failing
    undo cost a traceback rather than the others."""

    def test_lost_runs_the_undos_newest_first_wakes_the_run_and_survives_a_bad_one(self):
        run = Run("r1", Arm(), [])
        call = Call(run, "agent1", {})
        done = []
        call.on_lost(lambda: done.append("first"))
        call.on_lost(lambda: 1 / 0)
        call.on_lost(lambda: done.append("last"))
        woken = []
        waiter = threading.Thread(target=lambda: woken.append(run.wait_until(lambda: len(done) == 2, 5)))
        waiter.start()
        time.sleep(0.2)
        with contextlib.redirect_stderr(io.StringIO()) as log:
            call.lost()
        waiter.join(timeout=5)
        self.assertEqual(done, ["last", "first"])
        self.assertEqual(woken, [True])
        self.assertIn("ZeroDivisionError", log.getvalue())
        self.assertFalse(call.cancelled.is_set())

    def test_waited_adds_up_and_lost_runs_each_undo_once(self):
        # A call cancelled after its take, whose reply then also fails to
        # be written, puts back what it took once, not twice.
        call = Call(Run("r1", Arm(), []), "agent1", {})
        self.assertEqual(call.blocked_s, 0.0)
        call.waited(0.25)
        call.waited(0.5)
        self.assertAlmostEqual(call.blocked_s, 0.75)
        put_back = []
        call.on_lost(lambda: put_back.append("message"))
        call.lost()
        call.lost()
        self.assertEqual(put_back, ["message"])
        self.assertEqual(call.undos, [])


class InProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arm_dir = Path(self.tmp.name) / "testarm"
        write_test_arm(self.arm_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_get_close_without_a_server(self):
        hub = hub_module.Hub(public_url="http://host.docker.internal:9000/", arm_dirs=[self.arm_dir])
        created = hub.create_run(str(self.arm_dir), [1, 2], SPECS, {"base_ref": "abc"}, "x1")
        self.assertEqual(created["agents"][0]["mcp_url"], "http://host.docker.internal:9000/mcp/x1/agent1")
        self.assertEqual(hub.hosted("x1").run.options, {"base_ref": "abc"})
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run(str(self.arm_dir), [1], SPECS, run_id="x1")
        self.assertEqual(refused.exception.status, 409)
        self.assertFalse(hub.get_run("x1")["closed"])
        self.assertIn("events", hub.close_run("x1"))
        self.assertTrue(hub.get_run("x1")["closed"])
        self.assertEqual([e["kind"] for e in hub.events("x1")], ["run_started"])

    def test_a_failed_create_frees_its_run_id(self):
        class BrokenArm(Arm):
            def setup(self, run):
                raise RuntimeError("setup failed")

        hub = hub_module.Hub(arm_dirs=[self.arm_dir])
        with self.assertRaises(RuntimeError):
            hub.create_run(BrokenArm(), [1], {"1": "spec"}, run_id="y1")
        self.assertEqual(hub.run_count(), 0)
        with self.assertRaises(hub_module.HubError) as unknown:
            hub.get_run("y1")
        self.assertEqual(unknown.exception.status, 404)
        created = hub.create_run(str(self.arm_dir), [1], {"1": "spec"}, run_id="y1")
        self.assertEqual(created["run_id"], "y1")

    def test_an_arm_directory_is_loaded_once_and_instantiated_per_run(self):
        # Running arm.py for every run grew a long-lived hub without bound;
        # the class is kept per directory, the arm object is fresh per run.
        hub = hub_module.Hub(arm_dirs=[self.arm_dir])
        hub.create_run(str(self.arm_dir), [1], {"1": "spec"}, run_id="a1")
        hub.create_run(str(self.arm_dir), [1], {"1": "spec"}, run_id="a2")
        hub.create_run("coop", [1, 2], SPECS, run_id="c1")
        hub.create_run("coop", [1, 2], SPECS, run_id="c2")
        first, second = hub.hosted("a1").run.arm, hub.hosted("a2").run.arm
        self.assertIs(type(first), type(second))
        self.assertIsNot(first, second)
        self.assertEqual(first.root, second.root)
        self.assertIs(type(hub.hosted("c1").run.arm), type(hub.hosted("c2").run.arm))
        self.assertEqual(len(hub.arm_classes), 2)


class RunStartedTest(unittest.TestCase):
    """The run's first event anchors every other: the wall clock its t
    counts from and who the agents are, before the arm's setup records."""

    def test_the_first_event_names_the_clock_the_arm_and_the_agents(self):
        hub = hub_module.Hub()
        hub.create_run("team", [7, 3], {"3": "a", "7": "b"}, {"shared_dir": "/workspace/shared"}, "t1")
        events = hub.events("t1")
        started = events[0]
        run = hub.hosted("t1").run
        self.assertEqual((started["seq"], started["kind"], started["agent"]), (0, "run_started", None))
        self.assertEqual(started["epoch"], run.started)
        self.assertAlmostEqual(datetime.fromisoformat(started["iso"]).timestamp(), run.started, places=5)
        self.assertTrue(started["iso"].endswith("+00:00"))
        self.assertEqual((started["arm"], started["tool_surface"]), ("team", "mcp"))
        self.assertEqual(started["agents"], [{"id": "agent1", "role": "lead", "features": [3]},
                                             {"id": "agent2", "role": "member", "features": [7]}])
        self.assertEqual(events[1]["kind"], "task_create", "the arm's setup comes after")

    def test_a_run_made_directly_has_none(self):
        self.assertEqual(Run("r1", Arm(), []).events, [])


class ToolSurfaceTest(unittest.TestCase):
    """The run option tool_surface: refused when unknown or not the arm's,
    and on "bash" the MCP endpoint serves only what CooperBench did."""

    def test_an_unknown_surface_is_refused_before_the_run_exists(self):
        hub = hub_module.Hub()
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run("coop", [1, 2], SPECS, {"tool_surface": "telepathy"}, "s1")
        self.assertEqual(refused.exception.status, 400)
        self.assertIn("mcp, bash", refused.exception.message)
        self.assertEqual(hub.run_count(), 0)

    def test_an_arm_that_offers_only_mcp_refuses_bash(self):
        hub = hub_module.Hub()
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run(Arm(), [1], {"1": "spec"}, {"tool_surface": "bash"}, "s2")
        self.assertEqual(refused.exception.status, 400)
        self.assertIn("offers tool_surface mcp", refused.exception.message)

    def test_on_bash_mcp_lists_only_the_arms_mcp_tools_and_the_shell_keeps_all(self):
        hub = hub_module.Hub()
        hub.create_run("coop", [1, 2], SPECS, {"tool_surface": "bash"}, "s3")
        run = hub.hosted("s3").run
        listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, run, "agent1")
        self.assertEqual(listed["result"]["tools"], [])
        called = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "send_message", "arguments": {"to": "agent2"}}}, run, "agent1")
        self.assertEqual(called["error"]["code"], mcp.INVALID_PARAMS)
        self.assertIn("send_message", [t["name"] for t in hub.describe_tools(run, "agent1")])
        self.assertFalse(hub.call(run, "agent1", "send_message", {"to": "agent2", "text": "hi"})["isError"])
        self.assertEqual(hub.close_run("s3")["tool_surface"], "bash")

    def test_the_surface_is_what_a_driver_hands_claude_code(self):
        self.assertEqual(Arm().claude_surface({}), {"tool_surface": "mcp", "plugin": True, "mcp_servers": {}})
        with self.assertRaises(ValueError):
            Arm().claude_surface({"tool_surface": "carrier pigeon"})


class TraceFieldsTest(unittest.TestCase):
    def test_a_private_field_stays_in_the_record_and_off_the_stream(self):
        memory = listen_in_test(self)
        run = Run("r1", Arm(), [])
        run.emit("message", "agent1", to="agent2", content="the text", chars=8)
        self.assertEqual(run.events[0]["content"], "the text")
        self.assertEqual(set(memory[0]), {"kind", "t", "ts", "run", "seq", "agent", "to", "chars"})

    def test_next_seq_is_the_seq_the_next_emit_gets(self):
        run = Run("r1", Arm(), [])
        run.emit("first")
        with run.lock:
            expected = run.next_seq()
            self.assertEqual(run.emit("second")["seq"], expected)


class ArtifactArm(Arm):
    """Leaves one file of each kind, and says when it was torn down."""

    name = "artifacts"

    def artifacts(self, run):
        run.state.setdefault("order", []).append("artifacts")
        return {"notes.json": {"closed": run.closed}, "notes.txt": "plain text\n"}

    def teardown(self, run):
        run.state.setdefault("order", []).append("teardown")


class BrokenArtifactArm(Arm):
    name = "broken"

    def artifacts(self, run):
        raise RuntimeError("a bug in the arm")


class ArtifactsTest(unittest.TestCase):
    """The close answers the files a run leaves beside its metrics, taken
    once, before teardown, and never at the cost of the close."""

    def test_the_close_answer_carries_the_arms_artifacts_taken_once_before_teardown(self):
        hub = hub_module.Hub()
        hub.create_run(ArtifactArm(), [1], {"1": "spec"}, run_id="a1")
        answer = hub.close_answer("a1")
        self.assertEqual(set(answer), {"metrics", "artifacts"})
        self.assertEqual(answer["artifacts"], {"notes.json": {"closed": True}, "notes.txt": "plain text\n"})
        self.assertEqual(hub.close_answer("a1")["artifacts"], answer["artifacts"])
        self.assertEqual(answer["metrics"]["events"], {"run_started": 1})

    def test_teardown_comes_after_the_artifacts(self):
        hub = hub_module.Hub()
        hub.create_run(ArtifactArm(), [1], {"1": "spec"}, run_id="a2")
        run = hub.hosted("a2").run
        hub.close_run("a2")
        hub.close_run("a2")
        self.assertEqual(run.state["order"], ["artifacts", "teardown"])

    def test_an_arm_whose_artifacts_fail_still_closes(self):
        hub = hub_module.Hub()
        hub.create_run(BrokenArtifactArm(), [1], {"1": "spec"}, run_id="a3")
        with contextlib.redirect_stderr(io.StringIO()) as log:
            answer = hub.close_answer("a3")
        self.assertEqual(answer["artifacts"], {})
        self.assertIn("RuntimeError", log.getvalue())

    def test_a_run_with_a_shared_remote_leaves_its_git_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote = Path(tmp) / "remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            hub = hub_module.Hub()
            hub.create_run(Arm(), [1], {"1": "spec"}, {"git_remote_host": str(remote)}, "g1")
            with mock.patch.object(kit, "git_log", return_value="commit abc\n") as logged:
                answer = hub.close_answer("g1")
        self.assertEqual(answer["artifacts"], {"git_log.txt": "commit abc\n"})
        logged.assert_called_once_with(str(remote), None)

    def test_the_remotes_directory_on_this_host_is_read_before_its_url(self):
        hub = hub_module.Hub()
        options = {"git_remote_host": "git://127.0.0.1:9418/g/repo.git", "git_remote_dir": "/srv/git/g/repo.git",
                   "base_ref": "abc"}
        hub.create_run(Arm(), [1], {"1": "spec"}, options, "g3")
        with mock.patch.object(kit, "git_log", return_value="") as logged:
            hub.close_answer("g3")
        logged.assert_called_once_with("/srv/git/g/repo.git", "abc")

    def test_a_git_log_that_fails_costs_only_its_file(self):
        hub = hub_module.Hub()
        hub.create_run(ArtifactArm(), [1], {"1": "spec"}, {"git_remote_host": "git://127.0.0.1:1/none.git",
                                                           "base_ref": "abc"}, "g2")
        with mock.patch.object(kit, "git_log", side_effect=RuntimeError("git clone failed: refused")), \
                contextlib.redirect_stderr(io.StringIO()) as log:
            answer = hub.close_answer("g2")
        self.assertEqual(set(answer["artifacts"]), {"notes.json", "notes.txt"})
        self.assertIn("keeps no git_log.txt", log.getvalue())

    def test_the_close_endpoint_answers_both(self):
        hub = hub_module.Hub()
        server = hub_module.serve_in_background(hub, "127.0.0.1", 0)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        hub.create_run(ArtifactArm(), [1], {"1": "spec"}, run_id="h1")
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
        connection.request("POST", "/api/runs/h1/close", body=b"{}", headers={"Content-Type": "application/json"})
        answer = json.loads(connection.getresponse().read())
        connection.close()
        self.assertEqual(answer["artifacts"]["notes.txt"], "plain text\n")
        self.assertIn("events", answer["metrics"])


class EmitTest(unittest.TestCase):
    """The events log is a side record: the runner reads events from memory,
    so a disk that refuses the log must not fail the tool call or leave
    the tools waiting on the run asleep."""

    def test_a_failed_log_write_keeps_the_event_wakes_waiters_and_complains_once(self):
        # The log's directory is not there when the first event comes; the
        # events stay in memory under one complaint, and once it is there
        # the next write lands, so a disk that empties again loses only
        # the events in between.
        with tempfile.TemporaryDirectory() as logs:
            path = Path(logs) / "gone" / "events.jsonl"
            run = Run("r1", Arm(), [], events_path=path)
            woke = []
            waiter = threading.Thread(target=lambda: woke.append(
                run.wait_until(lambda: len(run.events) > 1, 10)))
            waiter.start()
            time.sleep(0.2)
            with contextlib.redirect_stderr(io.StringIO()) as log:
                run.emit("first")
                event = run.emit("second", "agent1")
            waiter.join(timeout=10)
            self.assertFalse(path.exists())
            path.parent.mkdir()
            with contextlib.redirect_stderr(io.StringIO()) as later:
                run.emit("third")
            written = read_jsonl(path)
            run.close_log()
        self.assertEqual(event, {"seq": 1, "t": event["t"], "kind": "second", "agent": "agent1"})
        self.assertEqual([e["kind"] for e in run.events], ["first", "second", "third"])
        self.assertEqual(woke, [True])
        self.assertEqual(log.getvalue().count("cannot write"), 1)
        self.assertIn("r1", log.getvalue())
        self.assertEqual(later.getvalue(), "")
        self.assertEqual(written, [run.events[2]])

    def test_the_run_keeps_its_record_whatever_the_trace_level(self):
        # The run's events are its data, not its logging: the stream may
        # be silent (off) or unheard, and the record is whole either way.
        self.addCleanup(trace.set_level, trace.level)
        run = Run("r1", Arm(), [])
        trace.set_level("off")
        run.emit("first", "agent1", n=1)
        trace.set_level("prod")
        run.emit("second")
        self.assertEqual(run.events, [{"seq": 0, "t": run.events[0]["t"], "kind": "first", "agent": "agent1", "n": 1},
                                      {"seq": 1, "t": run.events[1]["t"], "kind": "second", "agent": None}])


class WaitTraceTest(unittest.TestCase):
    def test_lock_wait_records_a_timeout_and_a_wake_in_debug_only(self):
        run = Run("r1", Arm(), [])
        memory = listen_in_test(self)
        self.assertFalse(run.wait_until(lambda: False, 0.01))
        self.assertEqual(memory, [], "prod records no wait")
        self.addCleanup(trace.set_level, trace.level)
        trace.set_level("debug")
        self.assertFalse(run.wait_until(lambda: False, 0.05))
        woke = []
        waiter = threading.Thread(target=lambda: woke.append(
            run.wait_until(lambda: run.state.get("go"), 10, agent="agent1")))
        waiter.start()
        time.sleep(0.05)
        with run.lock:
            run.state["go"] = True
            run.lock.notify_all()
        waiter.join(timeout=10)
        self.assertEqual(woke, [True])
        waits = [e for e in memory if e["kind"] == "lock_wait"]
        self.assertEqual([(e["run"], e["agent"], e["woke"]) for e in waits],
                         [("r1", None, False), ("r1", "agent1", True)])
        timed_out, woken = waits
        self.assertEqual(set(timed_out), {"kind", "t", "ts", "run", "agent", "waited_ms", "woke"})
        self.assertGreaterEqual(timed_out["waited_ms"], 50)
        self.assertLess(timed_out["waited_ms"], 5000)
        self.assertGreater(woken["waited_ms"], 0)
        self.assertLess(woken["waited_ms"], 10000)
        self.assertEqual(run.events, [], "a wait is the hub's event, not the run's")

    def test_a_cancel_that_finds_its_call_is_recorded_in_debug_only(self):
        run = Run("r1", Arm(), [])
        memory = listen_in_test(self)
        call = Call(run, "agent1", {})
        mcp.cancel({"requestId": 7}, {7: call})
        self.assertTrue(call.cancelled.is_set())
        self.assertEqual(memory, [])
        self.addCleanup(trace.set_level, trace.level)
        trace.set_level("debug")
        other = Call(run, "agent2", {})
        mcp.cancel({"requestId": "b", "reason": "gave up"}, {"b": other})
        mcp.cancel({"requestId": "b"}, {})
        self.assertTrue(other.cancelled.is_set())
        self.assertEqual([(e["kind"], e.get("run"), e.get("agent"), e["request_id"], e["found"]) for e in memory],
                         [("cancel", "r1", "agent2", "b", True), ("cancel", None, None, "b", False)])
        self.assertEqual(run.events, [])


class ConfigureTraceTest(unittest.TestCase):
    """hub.py's --debug and the environment's COORD_TRACE meet in
    configure_trace: debug gets a sink and a note, any other level nothing."""

    def setUp(self):
        self.addCleanup(trace.set_level, trace.level)

    def test_prod_registers_no_listener_and_says_nothing(self):
        with contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertIsNone(hub_module.configure_trace(False, None))
        self.assertEqual(trace.level, "prod")
        self.assertEqual(log.getvalue(), "")

    def test_debug_writes_hub_trace_jsonl_under_the_log_dir_and_says_so_once(self):
        with tempfile.TemporaryDirectory() as logs:
            log_dir = Path(logs) / "deeper"
            with contextlib.redirect_stderr(io.StringIO()) as log:
                sink = hub_module.configure_trace(True, log_dir)
            self.addCleanup(trace.unlisten, sink)
            self.assertEqual(trace.level, "debug")
            self.assertEqual(log.getvalue().count("\n"), 1)
            self.assertIn(f"tracing at debug to {log_dir / 'hub-trace.jsonl'}", log.getvalue())
            trace.debug("probe", n=1)
            sink.close()
            self.assertEqual([(e["kind"], e["n"]) for e in read_jsonl(log_dir / "hub-trace.jsonl")], [("probe", 1)])

    def test_debug_without_a_log_dir_writes_to_stderr(self):
        with contextlib.redirect_stderr(io.StringIO()) as log:
            sink = hub_module.configure_trace(True)
            self.addCleanup(trace.unlisten, sink)
            trace.event("probe")
        notice, line = log.getvalue().splitlines()
        self.assertIn("tracing at debug to stderr", notice)
        self.assertEqual(json.loads(line)["kind"], "probe")

    def test_off_is_left_as_the_operator_set_it(self):
        trace.set_level("off")
        with contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertIsNone(hub_module.configure_trace(False, None))
        self.assertEqual(trace.level, "off")
        self.assertEqual(log.getvalue(), "")


class GuardTest(unittest.TestCase):
    """Loading an arm runs its code in the hub, and agents can reach the
    hub, so only allowed arms load; the management API can want a token;
    a run must have what its arm needs; teardown runs once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.arm_dir = Path(self.tmp.name) / "testarm"
        write_test_arm(self.arm_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_arm_outside_the_allowed_directories_is_refused(self):
        hub = hub_module.Hub()
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run(str(self.arm_dir), [1], {"1": "spec"})
        self.assertEqual(refused.exception.status, 403)
        self.assertEqual(hub.create_run("coop", [1, 2], SPECS)["arm"], "coop")

    def test_a_run_without_what_its_arm_needs_is_refused(self):
        hub = hub_module.Hub()
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run("coop-git", [1, 2], SPECS)
        self.assertEqual(refused.exception.status, 400)
        self.assertIn("git_remote", refused.exception.message)
        created = hub.create_run("coop-git", [1, 2], SPECS, {"git_remote": "git://g/r.git"})
        self.assertEqual(len(created["agents"]), 2)

    def test_teardown_runs_once_and_specs_reach_the_run(self):
        calls = []

        class Holding(Arm):
            def setup(self, run):
                calls.append(("setup", dict(run.specs)))

            def teardown(self, run):
                calls.append(("teardown", run.id))

        hub = hub_module.Hub()
        hub.create_run(Holding(), [1], {"1": "spec"}, run_id="t1")
        hub.close_run("t1")
        hub.close_run("t1")
        self.assertEqual(calls, [("setup", {1: "spec"}), ("teardown", "t1")])

    def test_a_release_closes_an_open_run_first_and_tears_down_once(self):
        calls = []

        class Holding(Arm):
            def teardown(self, run):
                calls.append(run.id)

        hub = hub_module.Hub()
        hub.create_run(Holding(), [1], {"1": "spec"}, run_id="t2")
        self.assertEqual(hub.release_run("t2"), {"run_id": "t2", "released": True})
        self.assertEqual(calls, ["t2"])
        self.assertEqual(hub.run_count(), 0)
        for forgotten in (hub.release_run, hub.close_run, hub.get_run):
            with self.assertRaises(hub_module.HubError) as unknown:
                forgotten("t2")
            self.assertEqual(unknown.exception.status, 404)
        hub.create_run(Holding(), [1], {"1": "spec"}, run_id="t3")
        hub.close_run("t3")
        hub.release_run("t3")
        self.assertEqual(calls, ["t2", "t3"])

    def test_a_release_overlapping_a_re_create_of_the_id_keeps_the_new_run(self):
        # The first release is still in the old run's teardown when a
        # second release answers and a run of the same id is created; the
        # first must then forget only the run it closed, never the new one.
        torn = []
        in_teardown = threading.Barrier(2)
        let_go = threading.Event()

        class Slow(Arm):
            def teardown(self, run):
                torn.append(run.id)
                in_teardown.wait(timeout=10)
                let_go.wait(timeout=10)

        hub = hub_module.Hub()
        hub.create_run(Slow(), [1], {"1": "spec"}, run_id="R")
        old = hub.hosted("R")
        first = threading.Thread(target=hub.release_run, args=("R",))
        first.start()
        in_teardown.wait(timeout=10)
        self.assertEqual(hub.release_run("R"), {"run_id": "R", "released": True})
        self.assertEqual(hub.run_count(), 0)
        hub.create_run(Arm(), [1], {"1": "spec"}, run_id="R")
        new = hub.hosted("R")
        self.assertIsNot(new, old)
        let_go.set()
        first.join(timeout=10)
        self.assertFalse(first.is_alive())
        self.assertIs(hub.hosted("R"), new)
        self.assertFalse(new.run.closed)
        self.assertEqual(hub.run_count(), 1)
        self.assertEqual(torn, ["R"])
        self.assertEqual(hub.release_run("R"), {"run_id": "R", "released": True})
        self.assertEqual(hub.run_count(), 0)

    def test_the_management_api_wants_the_token_when_one_is_set(self):
        hub = hub_module.Hub(admin_token="s3cret")
        server = hub_module.serve_in_background(hub, "127.0.0.1", 0)
        try:
            def send(method, path, headers, body=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                connection.request(method, path, body, {"Content-Type": "application/json", **headers})
                status = connection.getresponse().status
                connection.close()
                return status

            def post(headers):
                body = json.dumps({"arm": "coop", "run_id": "guarded", "features": [1, 2], "specs": SPECS})
                return send("POST", "/api/runs", headers, body)
            self.assertEqual(post({}), 401)
            self.assertEqual(post({"Authorization": "Bearer wrong"}), 401)
            self.assertEqual(post({"Authorization": "Bearer s3cret"}), 201)
            self.assertEqual(send("DELETE", "/api/runs/guarded", {}), 401)
            self.assertEqual(send("DELETE", "/api/runs/guarded", {"Authorization": "Bearer s3cret"}), 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_refusal_carries_its_exit_code_and_tools_their_flags(self):
        class Coded(Arm):
            def tools(self, run, agent):
                def lose(call):
                    raise ToolError("someone else holds it", exit_code=2)
                return [Tool("claim", "Claim it.", lose, flags={"--force": "force"})]

        hub = hub_module.Hub()
        hub.create_run(Coded(), [1], {"1": "spec"}, run_id="c1")
        run = hub.hosted("c1").run
        self.assertEqual(hub.call(run, "agent1", "claim", {}),
                         {"text": "someone else holds it", "isError": True, "exitCode": 2})
        self.assertEqual(hub.describe_tools(run, "agent1")[0]["flags"], {"--force": "force"})


class CommandLineTest(unittest.TestCase):
    def test_it_prints_its_url_then_serves(self):
        with tempfile.TemporaryDirectory() as logs:
            process = subprocess.Popen(
                [sys.executable, str(COORDBENCH / "hub.py"), "--host", "127.0.0.1", "--port", "0",
                 "--log-dir", logs], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready, _, _ = select.select([process.stdout], [], [], 20)
                self.assertTrue(ready, "the hub printed nothing")
                url = process.stdout.readline().strip()
                self.assertRegex(url, r"^http://127\.0\.0\.1:\d+$")
                self.assertNotEqual(urlsplit(url).port, 0)
                connection = http.client.HTTPConnection("127.0.0.1", urlsplit(url).port, timeout=10)
                connection.request("GET", "/api/health")
                self.assertEqual(json.loads(connection.getresponse().read()), {"ok": True, "runs": 0})
                connection.close()
            finally:
                process.terminate()
                process.communicate(timeout=10)

    def serve(self, arguments, env=None):
        """hub.py on a free port with the arguments, and the port it took."""
        process = subprocess.Popen(
            [sys.executable, str(COORDBENCH / "hub.py"), "--host", "127.0.0.1", "--port", "0", *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        ready, _, _ = select.select([process.stdout], [], [], 20)
        self.assertTrue(ready, "the hub printed nothing")
        return process, urlsplit(process.stdout.readline().strip()).port

    def ask(self, port, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            data = None if body is None else json.dumps(body).encode()
            connection.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def test_debug_sets_the_level_says_so_once_and_logs_each_request(self):
        with tempfile.TemporaryDirectory() as logs:
            process, port = self.serve(["--log-dir", logs, "--debug"])
            try:
                self.assertEqual(self.ask(port, "GET", "/api/health"), (200, {"ok": True, "runs": 0}))
                # The event is written once the answer is out; the client
                # may read the answer first.
                deadline = time.monotonic() + 5
                path = Path(logs) / "hub-trace.jsonl"
                while time.monotonic() < deadline and not (path.exists() and path.read_text(encoding="utf-8")):
                    time.sleep(0.02)
            finally:
                process.terminate()
                _, stderr = process.communicate(timeout=10)
            self.assertEqual(stderr.count("tracing at debug"), 1, stderr)
            requests = [e for e in read_jsonl(path) if e["kind"] == "http_request"]
            self.assertEqual([(e["method"], e["path"], e["status"]) for e in requests], [("GET", "/api/health", 200)])

    def test_off_in_the_environment_silences_the_stream_and_a_run_still_keeps_its_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm_dir = Path(tmp) / "testarm"
            write_test_arm(arm_dir)
            process, port = self.serve(["--arm-dir", tmp], env={**os.environ, "COORD_TRACE": "off"})
            try:
                status, _ = self.ask(port, "POST", "/api/runs", {"arm": str(arm_dir), "run_id": "pair1",
                                                                 "features": [1, 2], "specs": SPECS})
                self.assertEqual(status, 201)
                call = {"tool": "send_message", "arguments": {"to": "agent2", "text": "hi"}}
                self.assertEqual(self.ask(port, "POST", "/rpc/pair1/agent1", call)[0], 200)
                _, listed = self.ask(port, "GET", "/api/runs/pair1/events")
            finally:
                process.terminate()
                _, stderr = process.communicate(timeout=10)
            self.assertEqual([e["kind"] for e in listed["events"]], ["run_started", "tool", "message", "tool_done"])
            self.assertEqual(stderr, "")


if __name__ == "__main__":
    unittest.main()
