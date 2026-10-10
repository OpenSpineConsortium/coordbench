"""The bridge is a Streamable HTTP client on behalf of a stdio client that
has no session of its own, so these pin what the transport asks of such
a client when the hub forgets the session: a 404 to a request sent with
Mcp-Session-Id starts a new session (initialize again, then
notifications/initialized, neither shown to the stdio client) and the
request is sent once more; and what the client hears when the hub
refuses a message it cannot read (400 with an id-less JSON-RPC error):
the hub's own error under each id it sent. The fake hub here does one
thing the real hub's tests cannot make it do on cue: forget its
session. TraceTest pins what the bridge records in debug (one
"bridge_post" span per POST, a "bridge_reinit" per session reopened)
and that prod, the default, records and writes nothing."""

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
TRACE_PY = COORDBENCH / "trace.py"
sys.path.insert(0, str(COORDBENCH))

import bridge as bridge_module  # noqa: E402

MCP_PATH = "/mcp/run1/agent1"
INIT_PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "stdio-client", "version": "0"}}


def reply(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def text_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def request(request_id, method, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def tool_call(request_id, name):
    return request(request_id, "tools/call", {"name": name, "arguments": {}})


def unreadable(message):
    """Whether the hub could not answer the message under an id: a batch
    any of whose requests has an id that is null or a boolean, or one
    such request on its own (MCP allows no null id, and bool is no int)."""
    members = message if isinstance(message, list) else [message]
    return any(isinstance(m, dict) and "method" in m and "id" in m
               and (m["id"] is None or isinstance(m["id"], bool)) for m in members)


class ForgetfulHub(BaseHTTPRequestHandler):
    """Just enough of hub.py's /mcp: a session per initialize, 404 for a
    request carrying a session it does not know (the hub's own words),
    400 with an id-less JSON-RPC error for a request it cannot answer
    under an id, 400 with a plain body when told to refuse plainly, and
    a counter of tool calls on which it forgets its session first."""

    def log_message(self, *args):
        pass

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        self.server.seen.append((self.headers.get("Mcp-Session-Id"), message))
        self.server.authorizations.append(self.headers.get("Authorization"))
        if self.path != MCP_PATH:
            return self.send_json(404, {"error": "no run or agent at this path"})
        if self.server.refuses_plainly and message.get("method") == "tools/call":
            return self.send_json(400, {"error": "the body is not a request"})
        if unreadable(message):
            # As the real hub: a message it cannot answer under an id is
            # 400 with a JSON-RPC error that names no id.
            return self.send_json(400, {"jsonrpc": "2.0", "error": {
                "code": -32600, "message": "the id must be a string or an integer"}})
        if isinstance(message, list):
            # Just enough of a batch: an empty result under each request's id.
            return self.send_json(200, [reply(m["id"], {}) for m in message if "id" in m])
        method = message.get("method")
        if method == "initialize":
            return self.open_session(message)
        if method == "tools/call" and self.server.forget_calls > 0:
            self.server.forget_calls -= 1
            self.server.session = None
        session = self.headers.get("Mcp-Session-Id")
        # As the real hub: a request without a session passes, one with a
        # session the hub does not know is refused.
        if session is not None and session != self.server.session:
            return self.send_json(404, {"error": "unknown Mcp-Session-Id; send initialize again without one"})
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "ping":
            return self.send_json(200, reply(message["id"], {}))
        if method == "tools/call" and message["params"]["name"] == "streamed":
            return self.stream(message["id"])
        self.send_json(200, reply(message["id"], text_result(f"ran {message['params']['name']}")))

    def stream(self, request_id):
        """An SSE answer in two events, a notification then the response;
        its size is kept so a test can check the bridge counted it."""
        progress = {"jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {"progressToken": 1, "progress": 0.5}}
        body = (f"data: {json.dumps(progress)}\n\n"
                f"data: {json.dumps(reply(request_id, text_result('streamed answer')))}\n\n").encode("utf-8")
        self.server.streamed_bytes = len(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body)

    def open_session(self, message):
        self.server.sessions += 1
        self.server.session = f"session-{self.server.sessions}"
        result = {"protocolVersion": message["params"]["protocolVersion"],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        self.send_json(200, reply(message["id"], result), {"Mcp-Session-Id": self.server.session})


class QueuedOutput:
    """The bridge's stdout, one written line per queue entry, so a test
    waits for an answer the way a client reading a pipe would."""

    def __init__(self):
        self.lines = queue.Queue()

    def write(self, text):
        self.lines.put(text)

    def flush(self):
        pass


class BridgeCase(unittest.TestCase):
    """A fake hub on an ephemeral port and a bridge writing to a queue."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ForgetfulHub)
        self.server.seen, self.server.sessions, self.server.session = [], 0, None
        self.server.authorizations = []
        self.server.forget_calls = 0
        self.server.refuses_plainly = False
        self.server.streamed_bytes = None
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}{MCP_PATH}"
        self.out = QueuedOutput()
        self.bridge = bridge_module.Bridge(self.url, out=self.out)

    def tearDown(self):
        self.bridge.serve(())
        self.server.shutdown()
        self.server.server_close()

    def send(self, message):
        """One line from the stdio client; requests are answered on a
        thread, so a test receives each answer before it sends the next
        when the order of what the hub sees matters."""
        self.bridge.take(json.dumps(message) + "\n")

    def receive(self, timeout=15):
        return json.loads(self.out.lines.get(timeout=timeout))

    def methods_seen(self):
        """Every method the hub saw, a batch's members one by one."""
        return [m.get("method") for _, message in self.server.seen
                for m in (message if isinstance(message, list) else [message])]


class ReinitializeTest(BridgeCase):
    def test_a_forgotten_session_is_reopened_and_the_request_retried(self):
        self.server.forget_calls = 1
        self.send(request(1, "initialize", INIT_PARAMS))
        self.assertEqual(self.receive()["id"], 1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send(tool_call(2, "send_message"))
        self.assertEqual(self.receive(), reply(2, text_result("ran send_message")))
        self.send(request(3, "ping"))
        self.assertEqual(self.receive(), reply(3, {}))
        self.assertEqual(self.methods_seen(),
                         ["initialize", "notifications/initialized", "tools/call",
                          "initialize", "notifications/initialized", "tools/call", "ping"])
        sessions = [session for session, _ in self.server.seen]
        self.assertEqual(sessions, [None, "session-1", "session-1", None, "session-2", "session-2",
                                    "session-2"])
        repeated = self.server.seen[3][1]
        self.assertEqual(repeated["params"], INIT_PARAMS)
        self.assertNotEqual(repeated["id"], 1)
        self.assertTrue(self.out.lines.empty(), "the repeated handshake must not reach the client")

    def test_a_second_refusal_is_the_hubs_answer(self):
        # One retry: a hub that forgets the new session as well has said
        # what it has to say, and the client hears the 404.
        self.server.forget_calls = 2
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "send_message"))
        answer = self.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (2, -32603))
        self.assertIn("HTTP 404", answer["error"]["message"])
        self.assertEqual(self.methods_seen().count("initialize"), 2)

    def test_a_404_to_a_request_without_a_session_is_not_retried(self):
        self.bridge = bridge_module.Bridge(self.url.replace("agent1", "agent9"), out=self.out)
        self.send(request(1, "tools/list"))
        answer = self.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (1, -32603))
        self.assertIn("HTTP 404", answer["error"]["message"])
        self.assertEqual(self.methods_seen(), ["tools/list"])

    def test_the_agents_token_goes_with_every_post_the_reopening_handshake_included(self):
        # A run the hub made with agent tokens wants the token on every
        # request, so the handshake the bridge repeats on its own carries
        # it too; without a token no Authorization header is sent.
        self.server.forget_calls = 1
        self.bridge = bridge_module.Bridge(self.url, out=self.out, token="the-agent-s-token")
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "send_message"))
        self.assertEqual(self.receive(), reply(2, text_result("ran send_message")))
        self.assertEqual(self.methods_seen(),
                         ["initialize", "tools/call", "initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(self.server.authorizations, ["Bearer the-agent-s-token"] * 5)
        for plain in (bridge_module.Bridge(self.url), bridge_module.Bridge(self.url, token="")):
            self.assertNotIn("Authorization", plain.headers())

    def test_a_400_carrying_the_hubs_jsonrpc_error_reaches_the_client_as_that_error(self):
        """The hub refuses a request it cannot answer under an id with a
        JSON-RPC error of its own and no id; the stdio client waits on
        the ids it sent, so it hears the hub's code and message under
        each of them, as it would from the hub on stdio, rather than an
        internal error quoting HTTP 400."""
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(None, "send_message"))
        self.assertEqual(self.receive(), {"jsonrpc": "2.0", "id": None, "error": {
            "code": -32600, "message": "the id must be a string or an integer"}})
        self.send([tool_call(True, "send_message"), request(7, "ping"), {"jsonrpc": "2.0", "method": "note"}])
        answers = self.receive()
        self.assertEqual([a["id"] for a in answers], [True, 7])
        for answer in answers:
            self.assertEqual(answer["error"], {"code": -32600, "message": "the id must be a string or an integer"})
        self.assertEqual(self.methods_seen()[1:], ["tools/call", "tools/call", "ping", "note"])
        self.assertTrue(self.out.lines.empty())

    def test_a_400_without_a_jsonrpc_body_is_a_hub_failure(self):
        self.server.refuses_plainly = True
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "send_message"))
        answer = self.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (2, -32603))
        self.assertIn("HTTP 400 Bad Request", answer["error"]["message"])
        self.assertIn("the body is not a request", answer["error"]["message"])
        self.assertIsNone(bridge_module.jsonrpc_error_in('{"error": "plain"}'))
        self.assertIsNone(bridge_module.jsonrpc_error_in('[{"jsonrpc": "2.0", "error": {"code": 1, "message": "m"}}]'))
        self.assertIsNone(bridge_module.jsonrpc_error_in("not json"))

    def test_parallel_requests_that_lose_the_session_share_one_reopen(self):
        self.server.forget_calls = 1
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "first"))
        self.send(tool_call(3, "second"))
        answers = {a["id"]: a for a in (self.receive(), self.receive())}
        self.assertEqual(answers[2], reply(2, text_result("ran first")))
        self.assertEqual(answers[3], reply(3, text_result("ran second")))
        self.assertEqual(self.methods_seen().count("initialize"), 2)
        self.assertEqual(self.server.sessions, 2)


class TraceTest(BridgeCase):
    """The bridge's debug events, on the module bridge loaded from beside
    itself; every test leaves the level at prod with nobody listening."""

    def setUp(self):
        super().setUp()
        self.trace = bridge_module.coordtrace
        self.addCleanup(self.reset_trace)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("COORD_TRACE", "COORD_TRACE_FILE", "COORD_AGENT_TOKEN"):
            os.environ.pop(name, None)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.trace_file = self.scratch / "bridge-trace.jsonl"

    def reset_trace(self):
        self.trace.set_level("prod")
        for listener in list(self.trace._listeners):
            self.trace.unlisten(listener)

    def memory(self):
        self.trace.set_level("debug")
        return self.trace.listen(self.trace.Memory())

    def settled(self):
        """Every worker joined, so every span has closed."""
        self.bridge.serve(())

    def trace_lines(self):
        return [json.loads(line) for line in self.trace_file.read_text(encoding="utf-8").splitlines()]

    def test_bridge_loads_coordbench_s_trace_by_its_location(self):
        self.assertTrue(bridge_module.same_file(self.trace, TRACE_PY), self.trace)
        self.assertTrue(hasattr(self.trace, "JsonLines"))
        self.assertFalse(bridge_module.same_file(None, TRACE_PY))

    def test_each_post_is_a_bridge_post_span_and_a_reopened_session_a_bridge_reinit(self):
        memory = self.memory()
        self.server.forget_calls = 1
        initialize = request(1, "initialize", INIT_PARAMS)
        self.send(initialize)
        self.assertEqual(self.receive()["id"], 1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.send(tool_call(2, "send_message"))
        self.assertEqual(self.receive(), reply(2, text_result("ran send_message")))
        self.settled()
        self.assertEqual([e["kind"] for e in memory],
                         ["bridge_post", "bridge_post_done",            # initialize
                          "bridge_post", "bridge_post_done",            # notifications/initialized
                          "bridge_post", "bridge_post_done",            # the call the hub answers 404
                          "bridge_reinit",
                          "bridge_post", "bridge_post_done",            # initialize again
                          "bridge_post", "bridge_post_done",            # notifications/initialized again
                          "bridge_post", "bridge_post_done"])           # the call, retried
        opened, told, lost, reinit, reopened, retried = memory[1], memory[3], memory[5], memory[6], memory[8], memory[12]
        self.assertEqual(set(memory[0]), {"kind", "t", "ts", "ids", "request_bytes"})
        self.assertEqual((memory[0]["ids"], memory[0]["request_bytes"]), ([1], len(json.dumps(initialize))))
        self.assertEqual(set(opened), {"kind", "t", "ts", "ids", "request_bytes", "duration_ms", "ok", "status",
                                       "response_bytes"})
        self.assertEqual((opened["ids"], opened["status"], opened["ok"]), ([1], 200, True))
        self.assertGreater(opened["response_bytes"], 0)
        self.assertIsInstance(opened["duration_ms"], float)
        self.assertEqual(opened["duration_ms"], round(opened["duration_ms"], 3))
        self.assertEqual((told["ids"], told["status"], told["response_bytes"], told["ok"]), ([], 202, 0, True))
        self.assertEqual((lost["ids"], lost["status"], lost["ok"]), ([2], 404, False))
        self.assertNotIn("response_bytes", lost, "a 404 that reopens the session has its body unread")
        self.assertEqual(reinit, {"kind": "bridge_reinit", "t": reinit["t"], "ts": reinit["ts"],
                                  "session": "session-1", "reopened": 1})
        self.assertEqual((reopened["ids"], reopened["status"]), (["coordbench-bridge-initialize-1"], 200))
        answer = json.dumps(reply(2, text_result("ran send_message")))
        self.assertEqual((retried["ids"], retried["status"], retried["ok"], retried["response_bytes"]),
                         ([2], 200, True, len(answer)))
        self.assertEqual(retried["request_bytes"], len(json.dumps(tool_call(2, "send_message"))))
        text = json.dumps(memory)
        for content in ("send_message", "stdio-client", "ran "):
            self.assertNotIn(content, text, "no message's content is in the trace")

    def test_a_streamed_answer_s_bytes_are_counted_and_a_batch_s_ids_listed(self):
        memory = self.memory()
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "streamed"))
        self.assertEqual(self.receive()["method"], "notifications/progress")
        self.assertEqual(self.receive(), reply(2, text_result("streamed answer")))
        self.send([request(3, "ping"), tool_call(4, "second"), {"jsonrpc": "2.0", "method": "note"}])
        self.receive()
        self.settled()
        done = [e for e in memory if e["kind"] == "bridge_post_done"]
        self.assertEqual([e["ids"] for e in done], [[1], [2], [3, 4]])
        streamed, batched = done[1], done[2]
        self.assertEqual((streamed["status"], streamed["ok"], streamed["response_bytes"]),
                         (200, True, self.server.streamed_bytes))
        self.assertEqual((batched["status"], batched["ok"], batched["response_bytes"]),
                         (200, True, len(json.dumps([reply(3, {}), reply(4, {})]))))

    def test_an_unreachable_hub_and_a_plain_refusal_are_not_ok(self):
        memory = self.memory()
        self.bridge = bridge_module.Bridge("http://127.0.0.1:9/mcp/none", out=self.out, timeout=5)
        self.send(request(1, "tools/list"))
        self.assertEqual(self.receive()["error"]["code"], -32603)
        self.settled()
        self.assertEqual([e["kind"] for e in memory], ["bridge_post", "bridge_post_done"])
        self.assertIs(memory[1]["ok"], False)
        self.assertNotIn("status", memory[1])
        del memory[:]
        self.bridge = bridge_module.Bridge(self.url, out=self.out)
        self.server.refuses_plainly = True
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "send_message"))
        self.assertEqual(self.receive()["error"]["code"], -32603)
        self.settled()
        refused = memory[-1]
        self.assertEqual((refused["kind"], refused["ids"], refused["status"], refused["ok"]),
                         ("bridge_post_done", [2], 400, False))
        self.assertEqual([e["kind"] for e in memory if e["kind"] == "bridge_reinit"], [])

    def test_in_prod_the_bridge_records_nothing(self):
        memory = self.trace.listen(self.trace.Memory())
        self.server.forget_calls = 1
        self.send(request(1, "initialize", INIT_PARAMS))
        self.receive()
        self.send(tool_call(2, "send_message"))
        self.assertEqual(self.receive(), reply(2, text_result("ran send_message")))
        self.settled()
        self.assertEqual(memory, [])
        self.assertEqual(self.server.sessions, 2, "the session was reopened all the same")

    def test_configure_trace_listens_in_debug_alone_and_release_takes_it_off(self):
        self.assertIsNone(bridge_module.configure_trace())
        self.trace.set_level("debug")
        os.environ["COORD_TRACE_FILE"] = str(self.trace_file)
        sink = bridge_module.configure_trace()
        self.assertIsInstance(sink, self.trace.JsonLines)
        self.trace.event("probe")
        bridge_module.release_trace(sink)
        bridge_module.release_trace(None)
        self.trace.event("after")
        self.assertEqual([e["kind"] for e in self.trace_lines()], ["probe"])
        del os.environ["COORD_TRACE_FILE"]
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            sink = bridge_module.configure_trace()
            self.trace.event("loud")
            bridge_module.release_trace(sink)
        self.assertEqual(json.loads(stderr.getvalue())["kind"], "loud")
        self.assertFalse(stderr.closed)

    def run_process(self, script=None, **environment):
        """bridge.py as an MCP client runs it, given one initialize and then
        the end of its input; its stdout, its stderr and its exit code."""
        env = {k: v for k, v in os.environ.items() if k not in ("COORD_TRACE", "COORD_TRACE_FILE")}
        env.update(COORD_MCP_URL=self.url, **environment)
        line = json.dumps(request(1, "initialize", INIT_PARAMS)) + "\n"
        done = subprocess.run([sys.executable, "-I", str(script or COORDBENCH / "bridge.py")], input=line,
                              env=env, capture_output=True, text=True, timeout=30)
        return done.returncode, done.stdout, done.stderr

    def test_the_process_writes_its_events_to_the_file_or_stderr_in_debug_and_nothing_in_prod(self):
        code, out, err = self.run_process(COORD_TRACE="debug", COORD_TRACE_FILE=str(self.trace_file))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["id"], 1)
        self.assertEqual(out.count("\n"), 1)
        events = self.trace_lines()
        self.assertEqual([(e["kind"], e["ids"]) for e in events], [("bridge_post", [1]), ("bridge_post_done", [1])])
        self.assertEqual(events[1]["status"], 200)
        self.trace_file.unlink()
        code, out, err = self.run_process(COORD_TRACE="debug")
        self.assertEqual((code, json.loads(out)["id"]), (0, 1))
        self.assertEqual([json.loads(line)["kind"] for line in err.splitlines()], ["bridge_post", "bridge_post_done"])
        for environment in ({}, {"COORD_TRACE": "prod"}, {"COORD_TRACE": "off"}):
            with self.subTest(environment=environment):
                code, out, err = self.run_process(COORD_TRACE_FILE=str(self.trace_file), **environment)
                self.assertEqual((code, err, json.loads(out)["id"]), (0, "", 1))
        self.assertFalse(self.trace_file.exists())

    def test_a_copy_of_bridge_py_alone_works_and_records_nothing(self):
        alone = self.scratch / "alone"
        alone.mkdir()
        (alone / "bridge.py").write_text((COORDBENCH / "bridge.py").read_text(encoding="utf-8"), encoding="utf-8")
        code, out, err = self.run_process(alone / "bridge.py", COORD_TRACE="debug",
                                          COORD_TRACE_FILE=str(self.trace_file))
        self.assertEqual((code, err, json.loads(out)["id"]), (0, "", 1))
        self.assertFalse(self.trace_file.exists())
        probe = f"""
import sys
sys.path.insert(0, {str(alone)!r})
import bridge
assert bridge.coordtrace is bridge.NoTrace and bridge.coordtrace.level == "off"
assert "trace" not in sys.modules
with bridge.coordtrace.debug_span("bridge_post", ids=[1]) as span:
    span.fields["status"] = 200
assert span.fields == {{"ids": [1], "status": 200}}
assert bridge.coordtrace.debug("bridge_reinit", session="s") is None
assert bridge.configure_trace() is None
print("stand-in ok")
"""
        done = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "stand-in ok"), done.stderr)


if __name__ == "__main__":
    unittest.main()
