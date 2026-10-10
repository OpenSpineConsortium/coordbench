"""The shell surface and the stdio bridge are what an agent without an HTTP
MCP client sees of the hub, so these pin both against a small fake of the
hub's /rpc and /mcp endpoints served on an ephemeral port: the commands'
argument mapping (argv and flags) and exit codes, the wrappers install
writes, and the bridge's session handling, streaming, and errors. The
stdin rule and the typing of words are also run as drivers.py runs a
command, cli.py as a subprocess with stdin at /dev/null, since that is
where the in-process runs with a patched stdin missed two regressions.
TraceTest pins what a command records in debug (trace.py beside it) and
that prod, the default, prints exactly what it printed before."""

import io
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
TRACE_PY = COORDBENCH / "trace.py"
sys.path.insert(0, str(COORDBENCH))

import cli  # noqa: E402

TEXT = {"type": "string"}
BOOLEAN = {"type": "boolean"}
TOOLS = [
    # send_message takes one word per name and is marked for stderr, as
    # kit lists it, so its echo lands on stderr in the tests of it. The
    # "required" lists are the arms' own: coop_msg.py's content and
    # coop_task.py's body are optional positionals, the rest are not.
    {"name": "send_message", "description": "Send a message to one peer, by agent id.",
     "inputSchema": {"type": "object", "properties": {"to": TEXT, "text": TEXT}, "required": ["to"],
                     "x-coordbench-prints": "stderr"},
     "alias": "coop-send", "argv": ["to", "text"]},
    {"name": "broadcast", "description": "Send a message to every other agent.",
     "inputSchema": {"type": "object", "properties": {"text": TEXT}},
     "alias": "coop-broadcast", "argv": ["text"]},
    {"name": "receive_messages", "description": "Drain your inbox.",
     "inputSchema": {"type": "object", "properties": {}}, "alias": "coop-recv", "argv": []},
    {"name": "claim_task", "description": "Claim a task by number.",
     "inputSchema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
     "alias": "", "argv": ["task_id"]},
    {"name": "task_create", "description": "Create a task and print its id.",
     "inputSchema": {"type": "object", "properties": {"title": TEXT, "assign": TEXT}, "required": ["title"]},
     "alias": "coop-task-create", "argv": ["title"], "flags": {"--assign": "assign"}},
    {"name": "wait_for_message", "description": "Block until a message arrives.\nPrefer it to polling.",
     "inputSchema": {"type": "object", "properties": {"timeout_ms": {"type": "integer"}}},
     "alias": "", "argv": []},
    {"name": "refuse", "description": "Always refuses, with the exit code it is given.",
     "inputSchema": {"type": "object", "properties": {"code": {"type": "integer"}}},
     "alias": "", "argv": ["code"]},
    # The tools above are listed as by a hub that lists no flags, which
    # must read as tools without any; these carry coop_task.py's options
    # the way the team arm lists them, except that request's "body..."
    # keeps cli.py's rest-of-line form under test: the team arm now reads
    # one word per positional, as coop_task.py does, and no shipped arm
    # uses "..." any more.
    {"name": "task_update", "description": "Set the status of a task you own.",
     "inputSchema": {"type": "object", "properties": {"task_id": TEXT, "status": TEXT, "note": TEXT},
                     "required": ["task_id", "status"]},
     "alias": "coop-task-update", "argv": ["task_id", "status"], "flags": {"-n": "note", "--note": "note"}},
    {"name": "task_list", "description": "Every task on the list.",
     "inputSchema": {"type": "object", "properties": {"mine": BOOLEAN, "open": BOOLEAN}},
     "alias": "coop-task-list", "argv": [], "flags": {"--mine": "mine", "--open": "open"}},
    {"name": "request", "description": "Send a typed request.",
     "inputSchema": {"type": "object", "properties": {"recipient": TEXT, "kind": TEXT, "body": TEXT,
                                                      "wait": {"type": "number"}},
                     "required": ["recipient", "kind"]},
     "alias": "coop-request", "argv": ["recipient", "kind", "body..."], "flags": {"--wait": "wait"}},
]
ALIASES = {"coop-send", "coop-broadcast", "coop-recv", "coop-task-create", "coop-task-update", "coop-task-list",
           "coop-request"}
PROMPT = "You are agent1. Implement feature 1."
RPC_PATH = "/rpc/run1/agent1"
MCP_PATH = "/mcp/run1/agent1"
SESSION_ID = "session-abc"
AGENT_TOKEN = "coordbench-fake-agent-token-2c7e5a1f93b6"
REFUSED = "run run1 wants agent1's bearer token in Authorization"


class FakeHub(BaseHTTPRequestHandler):
    """Just enough of hub.py's HTTP API to exercise the clients: /rpc
    echoes each call back as JSON, and /mcp answers by tool name. With a
    token set on the server, /rpc wants it as the hub does, 401 in the
    hub's own words otherwise; the Authorization header of every /rpc
    request is kept, so a test sees what the command sent."""

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

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def authorized(self):
        self.server.authorizations.append(self.headers.get("Authorization"))
        wanted = self.server.token
        if wanted and self.headers.get("Authorization") != f"Bearer {wanted}":
            self.send_json(401, {"error": REFUSED})
            return False
        return True

    def do_GET(self):
        if self.path != RPC_PATH:
            return self.send_json(404, {"error": "no run or agent at this path"})
        if self.authorized():
            self.send_json(200, {"agent": "agent1", "role": "peer", "tools": TOOLS, "prompt": PROMPT})

    def do_POST(self):
        if self.path == RPC_PATH:
            request = json.loads(self.body())
            if self.authorized():
                self.answer_rpc(request)
            return
        if self.path == MCP_PATH:
            return self.answer_mcp(json.loads(self.body()))
        self.send_json(404, {"error": "no run or agent at this path"})

    def answer_rpc(self, request):
        self.server.calls.append(request)
        name, arguments = request["tool"], request.get("arguments", {})
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return self.send_json(400, {"error": f"unknown tool {name!r}"})
        missing = [n for n in tool["inputSchema"].get("required", []) if n not in arguments]
        if missing:
            # As the team arm's handlers refuse a missing argument: in
            # argparse's words, exiting 2, before anything is done.
            return self.send_json(200, {"text": f"the following arguments are required: {missing[0]}",
                                        "isError": True, "exitCode": 2})
        if name == "refuse":
            # Without a code the answer has no exitCode, as from a hub
            # that predates it; with one, the code is sent as given.
            refusal = {"text": "not allowed", "isError": True}
            if "code" in arguments:
                refusal["exitCode"] = arguments["code"]
            return self.send_json(200, refusal)
        echo = json.dumps({"tool": name, "arguments": arguments}, sort_keys=True)
        self.send_json(200, {"text": echo, "isError": False})

    def answer_mcp(self, message):
        self.server.mcp_requests.append((self.headers, message))
        if "method" not in message or "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method, request_id = message["method"], message["id"]
        if method == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
            return self.send_json(200, reply(request_id, result), {"Mcp-Session-Id": SESSION_ID})
        if method == "ping":
            return self.send_json(200, reply(request_id, {}))
        tool = message.get("params", {}).get("name")
        if tool == "streamed":
            return self.stream(request_id)
        if tool == "explode":
            return self.send_json(500, {"error": "boom"})
        self.send_json(200, reply(request_id, self.result_of(tool)))

    def result_of(self, tool):
        if tool == "slow":
            released = self.server.release.wait(timeout=10)
            return text_result("released" if released else "timed out")
        if tool == "release":
            self.server.release.set()
        return text_result(f"ran {tool}")

    def stream(self, request_id):
        """An SSE answer: a comment, a notification, an event that is not
        JSON, then the response split over two data lines."""
        progress = {"jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {"progressToken": 1, "progress": 0.5}}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [": keep-alive\n\n",
                  f"event: message\ndata: {json.dumps(progress)}\n\n",
                  "data: not json at all\n\n",
                  f'data: {{"jsonrpc": "2.0", "id": {json.dumps(request_id)},\n'
                  f'data: "result": {json.dumps(text_result("streamed answer"))}}}\n\n']
        for chunk in chunks:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()


def reply(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def text_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def closed_port_url(path):
    """A URL on 127.0.0.1 where nothing listens: bind a port, then free it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{path}"


class HubTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
        self.server.calls, self.server.mcp_requests, self.server.authorizations = [], [], []
        self.server.token = None
        self.server.release = threading.Event()
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.rpc_url, self.mcp_url = base + RPC_PATH, base + MCP_PATH
        environment = mock.patch.dict(os.environ)
        environment.start()
        os.environ.pop("COORD_RPC_URL", None)
        os.environ.pop("COORD_AGENT_TOKEN", None)
        self.addCleanup(environment.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def run_cli_process(self, *argv, cwd=None):
        """cli.py as drivers.py runs a command in a container: a process of
        its own with stdin at /dev/null, which is no terminal and holds no
        text, where a patched sys.stdin in this process is one or the
        other by the test's choice."""
        return subprocess.run([sys.executable, str(COORDBENCH / "cli.py"), "--url", self.rpc_url, *argv],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, cwd=cwd)

    def sent_arguments(self):
        return [call.get("arguments") for call in self.server.calls]


class TerminalInput(io.StringIO):
    def isatty(self):
        return True


def run_cli(*argv, stdin="", tty=False):
    out, err = io.StringIO(), io.StringIO()
    source = TerminalInput(stdin) if tty else io.StringIO(stdin)
    with mock.patch.object(sys, "stdin", source), redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CommandTest(HubTestCase):
    def call_arguments(self, stdout):
        return json.loads(stdout)["arguments"]

    def test_call_with_a_json_object(self):
        code, out, err = run_cli("--url", self.rpc_url, "call", "receive_messages", '{"all": true}')
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"tool": "receive_messages", "arguments": {"all": True}})

    def test_call_with_key_value_words_typed_by_the_schema(self):
        code, _, err = run_cli("--url", self.rpc_url, "call", "send_message", "to=agent2", "text=a=b")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(err), {"to": "agent2", "text": "a=b"})
        code, out, _ = run_cli("--url", self.rpc_url, "call", "wait_for_message", "timeout_ms=1500")
        self.assertEqual(self.call_arguments(out), {"timeout_ms": 1500})

    def test_a_tools_text_goes_to_stderr_when_its_schema_says_so(self):
        # coop-send says "sent to agent2" on stderr in coop_msg.py and
        # nothing on stdout; the mark in the schema is how the command
        # knows, whether the tool is called by its alias or by name.
        for words in (["alias-run", "send_message", "agent2", "hi"],
                      ["call", "send_message", '{"to": "agent2", "text": "hi"}'],
                      ["call", "send_message", "to=agent2", "text=hi"]):
            with self.subTest(words=words):
                code, out, err = run_cli("--url", self.rpc_url, *words)
                self.assertEqual((code, out), (0, ""))
                self.assertEqual(self.call_arguments(err), {"to": "agent2", "text": "hi"})
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "broadcast", "hi")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.call_arguments(out), {"text": "hi"})

    def test_call_takes_the_url_from_the_environment(self):
        os.environ["COORD_RPC_URL"] = self.rpc_url
        code, out, _ = run_cli("call", "receive_messages")
        self.assertEqual((code, self.call_arguments(out)), (0, {}))

    def test_tools_lists_each_tool_with_its_command_and_description(self):
        code, out, _ = run_cli("--url", self.rpc_url, "tools")
        self.assertEqual(code, 0)
        self.assertIn("send_message  (shell: coop-send <to> <text>)\n"
                      "    Send a message to one peer, by agent id.", out)
        self.assertIn("claim_task  (shell: coord-claim_task <task_id>)", out)
        self.assertIn("wait_for_message  (shell: coord-wait_for_message [key=value ...])\n"
                      "    Block until a message arrives.\n    Prefer it to polling.", out)
        self.assertIn("receive_messages  (shell: coop-recv)", out)

    def test_tools_shows_each_tools_flags_before_its_words(self):
        code, out, _ = run_cli("--url", self.rpc_url, "tools")
        self.assertEqual(code, 0)
        self.assertIn("task_update  (shell: coop-task-update [-n|--note <note>] <task_id> <status>)\n"
                      "    Set the status of a task you own.", out)
        self.assertIn("task_list  (shell: coop-task-list [--mine] [--open])\n", out)
        self.assertIn("request  (shell: coop-request [--wait <wait>] <recipient> <kind> <body...>)\n", out)

    def test_usage_offers_key_value_words_only_for_arguments_no_flag_sets(self):
        tool = {"name": "list", "inputSchema": {"properties": {"mine": BOOLEAN, "limit": {"type": "integer"}}},
                "flags": {"--mine": "mine"}}
        self.assertEqual(cli.usage(tool), "coord-list [--mine] [key=value ...]")
        tool["flags"]["--limit"] = "limit"
        self.assertEqual(cli.usage(tool), "coord-list [--mine] [--limit <limit>]")

    def test_prompt_prints_the_agents_prompt(self):
        self.assertEqual(run_cli("--url", self.rpc_url, "prompt"), (0, PROMPT + "\n", ""))

    def test_alias_run_maps_words_onto_argv_and_a_trailing_name_joins_the_rest(self):
        # -x is not one of request's flags, so it is part of the body.
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "request",
                               "agent2", "review", "hello", "there", "-x")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(out), {"recipient": "agent2", "kind": "review",
                                                    "body": "hello there -x"})
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "claim_task", "7")
        self.assertEqual(self.call_arguments(out), {"task_id": 7})

    def test_alias_run_reads_the_last_names_missing_word_from_stdin(self):
        # coop_msg.py's content is one optional word read from stdin when
        # missing, so the last argv name reads stdin whether or not it
        # ends in "...".
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2",
                               stdin="from stdin\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(err), {"to": "agent2", "text": "from stdin\n"})
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "broadcast", stdin="to all\n")
        self.assertEqual(self.call_arguments(out), {"text": "to all\n"})
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "request", "agent1", "review",
                               stdin="the body\n")
        self.assertEqual(self.call_arguments(out), {"recipient": "agent1", "kind": "review", "body": "the body\n"})

    def test_alias_run_leaves_a_terminal_alone(self):
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2",
                               stdin="typed", tty=True)
        self.assertEqual(self.call_arguments(err), {"to": "agent2"})

    def test_alias_run_reads_no_stdin_while_an_earlier_word_is_missing(self):
        # `coop-send` alone: the recipient is missing, so the text is not
        # read either, and the hub refuses as argparse does.
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", stdin="text")
        self.assertEqual((code, out, err), (2, "", "the following arguments are required: to\n"))
        self.assertEqual(self.sent_arguments(), [{}])

    def test_a_required_last_word_is_not_read_from_stdin(self):
        # drivers.py hands every command a stdin at /dev/null, which is no
        # terminal, so the old rule read "" from it for any missing last
        # word: coop-task-create made a task titled "" where coop_task.py
        # exits 2, coop-task-claim asked after task "" and exited 1, and
        # coop-task-update <id> sent status "" and exited 4. A word the
        # schema requires is left missing for the hub to refuse.
        for words, missing in ((["task_create"], "title"), (["task_create", "--assign", "agent2"], "title"),
                               (["claim_task"], "task_id"), (["task_update", "3"], "status")):
            with self.subTest(words=words):
                done = self.run_cli_process("alias-run", *words)
                self.assertEqual((done.returncode, done.stdout), (2, ""))
                self.assertEqual(done.stderr, f"the following arguments are required: {missing}\n")
        self.assertEqual(self.sent_arguments(), [{}, {"assign": "agent2"}, {}, {"task_id": "3"}])
        # An optional last word still comes from stdin, "" at /dev/null's
        # end, as coop_msg.py's content and coop_task.py's body do.
        sent = self.run_cli_process("alias-run", "send_message", "agent2")
        self.assertEqual((sent.returncode, sent.stdout), (0, ""), sent.stderr)
        self.assertEqual(json.loads(sent.stderr)["arguments"], {"to": "agent2", "text": ""})
        asked = self.run_cli_process("alias-run", "request", "agent1", "review")
        self.assertEqual(asked.returncode, 0, asked.stderr)
        self.assertEqual(json.loads(asked.stdout)["arguments"], {"recipient": "agent1", "kind": "review", "body": ""})

    def test_a_word_that_is_true_false_or_null_is_refused_where_a_number_is_wanted(self):
        # coop_task.py's --wait has type=float, which exits 2 on these
        # words; parsed as JSON they reached the hub as True, False and
        # None, which float() read as 1.0 and 0.0, and None as no wait.
        for words, wording in (
                (["request", "agent1", "review", "look", "--wait", "true"], "--wait: invalid float value: 'true'"),
                (["request", "agent1", "review", "look", "--wait=false"], "--wait: invalid float value: 'false'"),
                (["request", "agent1", "review", "--wait", "null", "look"], "--wait: invalid float value: 'null'"),
                (["claim_task", "true"], "task_id: invalid int value: 'true'")):
            with self.subTest(words=words):
                done = self.run_cli_process("alias-run", *words)
                self.assertEqual((done.returncode, done.stdout), (2, ""))
                self.assertEqual(done.stderr, f"coordbench: argument {wording}\n")
        keyed = self.run_cli_process("call", "request", "recipient=agent1", "kind=review", "wait=null")
        self.assertEqual((keyed.returncode, keyed.stderr),
                         (2, "coordbench: argument wait: invalid float value: 'null'\n"))
        self.assertEqual(self.server.calls, [])
        # A number still goes through; so does a word that is no JSON,
        # which the hub refuses in the same words.
        done = self.run_cli_process("alias-run", "request", "agent1", "review", "look", "--wait", "2.5")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["arguments"]["wait"], 2.5)
        self.assertEqual(json.loads(self.run_cli_process("alias-run", "claim_task", "7").stdout)["arguments"],
                         {"task_id": 7})

    def test_coerce_keeps_a_boolean_or_null_the_schema_allows_beside_a_number(self):
        tool = {"inputSchema": {"properties": {"wait": {"type": ["number", "null"]},
                                               "limit": {"type": ["integer", "boolean"]}}}}
        self.assertIsNone(cli.coerce(tool, "wait", "null"))
        self.assertEqual(cli.coerce(tool, "wait", "2"), 2)
        self.assertIs(cli.coerce(tool, "limit", "true"), True)
        with self.assertRaises(cli.Failure) as refused:
            cli.coerce(tool, "wait", "true", label="--wait")
        self.assertEqual(str(refused.exception), "argument --wait: invalid float value: 'true'")
        with self.assertRaises(cli.Failure) as refused:
            cli.coerce(tool, "limit", "null")
        self.assertEqual(str(refused.exception), "argument limit: invalid int value: 'null'")

    def test_alias_run_refuses_extra_words(self):
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "claim_task", "7", "8")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("too many words; usage: coord-claim_task <task_id>", err)
        self.assertEqual(self.server.calls, [])

    def test_an_unquoted_message_is_a_word_too_many_as_argparse_says(self):
        # coop_msg.py's argparse refuses `coop-send agent2 hello there`
        # with exit 2; joining the words would send what argparse refused.
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2", "hello", "there")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("too many words; usage: coop-send <to> <text>", err)
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "broadcast", "hello", "there")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("too many words; usage: coop-broadcast <text>", err)
        self.assertEqual(self.server.calls, [])

    def test_flags_set_their_arguments_before_among_or_after_the_words(self):
        for words in (["3", "done", "-n", "patch ready"],
                      ["--note", "patch ready", "3", "done"],
                      ["3", "--note=patch ready", "done"],
                      ["3", "done", "-n=patch ready"]):
            with self.subTest(words=words):
                code, out, err = run_cli("--url", self.rpc_url, "alias-run", "task_update", *words)
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(self.call_arguments(out),
                                 {"task_id": "3", "status": "done", "note": "patch ready"})

    def test_a_flag_whose_argument_is_a_boolean_is_a_switch(self):
        for words, arguments in ((["--open", "--mine"], {"open": True, "mine": True}),
                                 (["--mine"], {"mine": True}),
                                 (["--mine=false"], {"mine": False}),
                                 ([], {})):
            with self.subTest(words=words):
                code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "task_list", *words)
                self.assertEqual((code, self.call_arguments(out)), (0, arguments))

    def test_a_flag_takes_the_next_word_typed_by_the_schema_and_the_text_stays_whole(self):
        for words, wait in ((["--wait", "10"], 10), (["--wait=0.5"], 0.5), (["--wait", "soon"], "soon")):
            with self.subTest(words=words):
                code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "request",
                                       "agent1", "review", "look", "at", "this", *words)
                self.assertEqual(code, 0)
                self.assertEqual(self.call_arguments(out), {"recipient": "agent1", "kind": "review",
                                                            "body": "look at this", "wait": wait})

    def test_flags_leave_the_trailing_text_to_stdin(self):
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "request", "agent1", "review",
                               "--wait", "5", stdin="the plan:\n- one\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(out), {"recipient": "agent1", "kind": "review",
                                                    "body": "the plan:\n- one\n", "wait": 5})

    def test_words_that_only_look_like_flags_stay_words(self):
        # --note is another tool's flag and --waiting only starts like this
        # one's; a message may start with a dash, and keeps it.
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "request", "agent1", "review",
                               "-x", "--note", "--waiting=5", "-1")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(out)["body"], "-x --note --waiting=5 -1")
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2", "--wait")
        self.assertEqual(self.call_arguments(err), {"to": "agent2", "text": "--wait"})
        code, out, _ = run_cli("--url", self.rpc_url, "alias-run", "task_update", "3", "-blocked")
        self.assertEqual(self.call_arguments(out), {"task_id": "3", "status": "-blocked"})

    def test_a_flag_left_without_its_value_is_exit_2_and_calls_nothing(self):
        self.assertEqual(run_cli("--url", self.rpc_url, "alias-run", "task_update", "3", "done", "-n"),
                         (2, "", "coordbench: -n takes a value; usage: "
                                 "coop-task-update [-n|--note <note>] <task_id> <status>\n"))
        self.assertEqual(self.server.calls, [])

    def test_words_left_over_after_the_flags_are_refused(self):
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "task_update", "3", "done", "now",
                               "-n", "a note")
        self.assertEqual(code, 2)
        self.assertIn("too many words; usage: coop-task-update [-n|--note <note>] <task_id> <status>", err)
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "task_list", "--mine", "--all")
        self.assertEqual(code, 2)
        self.assertIn("expected key=value or one JSON object, got '--all'", err)
        self.assertEqual(self.server.calls, [])

    def test_a_tool_error_goes_to_stderr_with_exit_1(self):
        self.assertEqual(run_cli("--url", self.rpc_url, "call", "refuse"), (1, "", "not allowed\n"))

    def test_a_tool_error_exits_with_the_code_the_tool_gave(self):
        # A ported command keeps its codes: coop-task-claim exits 2 when
        # another agent holds the task. 0 is a code too, if an arm gives it.
        for words, code in ((["call", "refuse", "code=2"], 2), (["alias-run", "refuse", "7"], 7),
                            (["call", "refuse", '{"code": 0}'], 0)):
            with self.subTest(words=words):
                self.assertEqual(run_cli("--url", self.rpc_url, *words), (code, "", "not allowed\n"))

    def test_a_code_no_shell_can_exit_with_reads_as_1(self):
        for code in ('"2"', "true", "2.0", "256", "-1", "null"):
            with self.subTest(code=code):
                self.assertEqual(run_cli("--url", self.rpc_url, "call", "refuse", f'{{"code": {code}}}'),
                                 (1, "", "not allowed\n"))

    def test_an_unreachable_hub_is_one_line_with_exit_2(self):
        code, out, err = run_cli("--url", closed_port_url(RPC_PATH), "call", "receive_messages")
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("coordbench: cannot reach the coordbench hub at "), err)
        self.assertEqual(err.count("\n"), 1)

    def test_an_http_error_names_the_hubs_reason_with_exit_2(self):
        code, _, err = run_cli("--url", self.rpc_url, "call", "no_such_tool")
        self.assertEqual(code, 2)
        self.assertIn("HTTP 400: unknown tool 'no_such_tool'", err)
        code, _, err = run_cli("--url", self.rpc_url.replace("agent1", "agent9"), "prompt")
        self.assertEqual(code, 2)
        self.assertIn("HTTP 404: no run or agent at this path", err)

    def test_no_url_at_all_is_exit_2(self):
        code, _, err = run_cli("tools")
        self.assertEqual(code, 2)
        self.assertIn("COORD_RPC_URL", err)

    def test_the_agents_token_goes_with_every_request_when_set_and_nothing_otherwise(self):
        # A command asks the hub to describe the agent, then calls: both
        # requests carry the token, from COORD_AGENT_TOKEN; unset or blank,
        # no Authorization header is sent at all.
        code, _, _ = run_cli("--url", self.rpc_url, "call", "receive_messages")
        self.assertEqual((code, self.server.authorizations), (0, [None, None]))
        self.server.token = AGENT_TOKEN
        os.environ["COORD_AGENT_TOKEN"] = AGENT_TOKEN
        code, out, _ = run_cli("--url", self.rpc_url, "call", "receive_messages")
        self.assertEqual(code, 0)
        self.assertEqual(self.call_arguments(out), {})
        self.assertEqual(self.server.authorizations[2:], [f"Bearer {AGENT_TOKEN}"] * 2)
        self.server.token = None
        os.environ["COORD_AGENT_TOKEN"] = " "
        self.assertEqual(run_cli("--url", self.rpc_url, "prompt")[0], 0)
        self.assertIsNone(self.server.authorizations[-1])

    def test_a_hub_that_refuses_the_token_is_reported_in_its_own_words_with_exit_2(self):
        self.server.token = AGENT_TOKEN
        code, out, err = run_cli("--url", self.rpc_url, "call", "receive_messages")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"coordbench: the coordbench hub at {self.rpc_url} answered HTTP 401: {REFUSED}\n")
        self.assertEqual(self.server.calls, [])
        os.environ["COORD_AGENT_TOKEN"] = "not-this-agent-s"
        code, _, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2", "hi")
        self.assertEqual(code, 2)
        self.assertIn(f"HTTP 401: {REFUSED}", err)
        self.assertEqual(self.server.calls, [])


class InstallTest(HubTestCase):
    def setUp(self):
        super().setUp()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.bin_dir = Path(scratch.name) / "bin"

    def run_command(self, name, *words, stdin="", rpc_url=None, cwd=None):
        environment = {k: v for k, v in os.environ.items() if k not in ("COORD_RPC_URL", "COORD_AGENT_TOKEN")}
        if rpc_url:
            environment["COORD_RPC_URL"] = rpc_url
        return subprocess.run([str(self.bin_dir / name), *words], input=stdin, env=environment,
                              capture_output=True, text=True, timeout=30, cwd=cwd)

    def test_install_writes_one_wrapper_per_alias_and_per_tool(self):
        code, _, _ = run_cli("install", str(self.bin_dir), "--url", self.rpc_url)
        self.assertEqual(code, 0)
        names = {p.name for p in self.bin_dir.iterdir()}
        self.assertEqual(names, ALIASES | {f"coord-{t['name']}" for t in TOOLS})
        for path in self.bin_dir.iterdir():
            self.assertTrue(os.access(path, os.X_OK), path)
        script = (self.bin_dir / "coop-send").read_text().splitlines()
        self.assertEqual(script[0], "#!/bin/sh")
        self.assertEqual(script[1], f"export COORD_RPC_URL={self.rpc_url}")
        self.assertTrue(script[2].endswith(f'{COORDBENCH / "cli.py"} alias-run send_message "$@"'), script)

    def test_wrappers_with_a_baked_url_reach_the_hub(self):
        run_cli("install", str(self.bin_dir), "--url", self.rpc_url)
        # coop-send's text goes to stderr and stdout stays empty, as with
        # coop_msg.py's "sent to agent2".
        sent = self.run_command("coop-send", "agent2", "hello from a wrapper")
        self.assertEqual((sent.returncode, sent.stdout), (0, ""), sent.stderr)
        self.assertEqual(json.loads(sent.stderr)["arguments"],
                         {"to": "agent2", "text": "hello from a wrapper"})
        unquoted = self.run_command("coop-send", "agent2", "hello", "there")
        self.assertEqual((unquoted.returncode, unquoted.stdout), (2, ""))
        piped = self.run_command("coop-broadcast", stdin="piped text\n")
        self.assertEqual(json.loads(piped.stdout)["arguments"], {"text": "piped text\n"})
        refused = self.run_command("coord-refuse")
        self.assertEqual((refused.returncode, refused.stdout, refused.stderr), (1, "", "not allowed\n"))
        held = self.run_command("coord-refuse", "2")
        self.assertEqual((held.returncode, held.stdout, held.stderr), (2, "", "not allowed\n"))
        noted = self.run_command("coop-task-update", "-n", "waiting on agent1", "3", "blocked")
        self.assertEqual(noted.returncode, 0, noted.stderr)
        self.assertEqual(json.loads(noted.stdout)["arguments"],
                         {"task_id": "3", "status": "blocked", "note": "waiting on agent1"})

    def test_wrappers_without_a_url_use_the_environment(self):
        os.environ["COORD_RPC_URL"] = self.rpc_url
        run_cli("install", str(self.bin_dir))
        self.assertNotIn("export", (self.bin_dir / "coop-recv").read_text())
        received = self.run_command("coop-recv", rpc_url=self.rpc_url)
        self.assertEqual(received.returncode, 0, received.stderr)
        self.assertEqual(json.loads(received.stdout)["tool"], "receive_messages")
        lost = self.run_command("coop-recv", rpc_url=closed_port_url(RPC_PATH))
        self.assertEqual(lost.returncode, 2)
        self.assertIn("cannot reach", lost.stderr)
        self.assertNotIn("COORD_AGENT_TOKEN", (self.bin_dir / "coop-recv").read_text())

    def test_install_with_a_token_file_writes_wrappers_that_read_it_when_they_run(self):
        # The hub wants the token from install's own request too, which
        # takes it from the file; the wrappers read the file with cat at
        # run time, so the token is in no wrapper and on no command line.
        self.server.token = AGENT_TOKEN
        token_file = self.bin_dir.parent / "token"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(AGENT_TOKEN + "\n")
        code, _, err = run_cli("install", str(self.bin_dir), "--url", self.rpc_url,
                               "--token-file", str(token_file))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.server.authorizations, [f"Bearer {AGENT_TOKEN}"])
        script = (self.bin_dir / "coop-send").read_text()
        self.assertIn(f'\nexport COORD_AGENT_TOKEN="$(cat {token_file})"\n', script)
        self.assertNotIn(AGENT_TOKEN, script)
        sent = self.run_command("coop-send", "agent2", "hello")
        self.assertEqual((sent.returncode, sent.stdout), (0, ""), sent.stderr)
        self.assertEqual(json.loads(sent.stderr)["arguments"], {"to": "agent2", "text": "hello"})
        self.assertEqual(self.server.authorizations[-2:], [f"Bearer {AGENT_TOKEN}"] * 2)
        token_file.unlink()
        refused = self.run_command("coop-recv")
        self.assertEqual((refused.returncode, refused.stdout), (2, ""))
        self.assertIn(f"HTTP 401: {REFUSED}", refused.stderr)

    def test_install_makes_a_relative_token_file_absolute_so_the_wrappers_work_from_anywhere(self):
        # `install --token-file token` used to bake `cat token` into the
        # wrappers, which found the file only from install's own directory
        # and answered the hub's 401 from any other.
        self.server.token = AGENT_TOKEN
        home = self.bin_dir.parent
        home.mkdir(parents=True, exist_ok=True)
        (home / "token").write_text(AGENT_TOKEN + "\n")
        done = self.run_cli_process("install", str(self.bin_dir), "--token-file", "token", cwd=home)
        self.assertEqual(done.returncode, 0, done.stderr)
        script = (self.bin_dir / "coop-send").read_text()
        self.assertIn(f'\nexport COORD_AGENT_TOKEN="$(cat {home.resolve() / "token"})"\n', script)
        self.assertNotIn("$(cat token)", script)
        self.assertNotIn(AGENT_TOKEN, script)
        sent = self.run_command("coop-send", "agent2", "hello", cwd="/")
        self.assertEqual((sent.returncode, sent.stdout), (0, ""), sent.stderr)
        self.assertEqual(json.loads(sent.stderr)["arguments"], {"to": "agent2", "text": "hello"})
        self.assertEqual(self.server.authorizations[-2:], [f"Bearer {AGENT_TOKEN}"] * 2)

    def test_a_token_file_that_cannot_be_read_is_exit_2(self):
        code, _, err = run_cli("install", str(self.bin_dir), "--url", self.rpc_url,
                               "--token-file", str(self.bin_dir / "no-such-file"))
        self.assertEqual(code, 2)
        self.assertIn("cannot read the token file", err)
        self.assertEqual(self.server.authorizations, [])


class BridgeProcess:
    """bridge.py as an MCP client would run it, with its stdout read on a
    thread so a test can wait for a line without blocking forever."""

    def __init__(self, url):
        environment = dict(os.environ, COORD_MCP_URL=url)
        self.process = subprocess.Popen([sys.executable, str(COORDBENCH / "bridge.py")],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=environment, text=True)
        self.lines = queue.Queue()
        self.pumping = threading.Thread(target=self.pump, daemon=True)
        self.pumping.start()

    def pump(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def send(self, message):
        line = message if isinstance(message, str) else json.dumps(message)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def receive(self, timeout=15):
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise AssertionError("the bridge ended before answering")
        return json.loads(line)

    def stop(self):
        """Make sure the process is gone and its pipes closed, whatever
        state a test left it in."""
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=15)
        self.pumping.join(timeout=15)
        self.process.stdin.close()
        self.process.stdout.close()

    def finish(self):
        """Close stdin and return what the bridge still wrote before it ended."""
        self.process.stdin.close()
        self.process.wait(timeout=15)
        rest = []
        while (line := self.lines.get(timeout=15)) is not None:
            rest.append(line)
        return rest


def request(request_id, method, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def tool_call(request_id, name):
    return request(request_id, "tools/call", {"name": name, "arguments": {}})


class BridgeTest(HubTestCase):
    def start_bridge(self, url=None):
        bridge = BridgeProcess(url or self.mcp_url)
        self.addCleanup(bridge.stop)
        return bridge

    def initialize(self, bridge):
        bridge.send(request(1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "0"}}))
        return bridge.receive()

    def test_initialize_teaches_the_session_id_and_version(self):
        bridge = self.start_bridge()
        answer = self.initialize(bridge)
        self.assertEqual(answer["result"]["protocolVersion"], "2025-03-26")
        bridge.send(request(2, "ping"))
        self.assertEqual(bridge.receive()["id"], 2)
        (first_headers, _), (second_headers, _) = self.server.mcp_requests
        self.assertNotIn("Mcp-Session-Id", first_headers)
        self.assertEqual(second_headers["Mcp-Session-Id"], SESSION_ID)
        self.assertEqual(second_headers["MCP-Protocol-Version"], "2025-03-26")
        self.assertEqual(second_headers["Content-Type"], "application/json")
        self.assertEqual(second_headers["Accept"], "application/json, text/event-stream")
        self.assertNotIn("Authorization", first_headers)
        self.assertNotIn("Authorization", second_headers)
        self.assertEqual(bridge.finish(), [])
        self.assertEqual(bridge.process.returncode, 0)

    def test_the_agents_token_goes_with_every_post_when_set(self):
        os.environ["COORD_AGENT_TOKEN"] = AGENT_TOKEN
        bridge = self.start_bridge()
        self.initialize(bridge)
        bridge.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        bridge.send(tool_call(4, "anything"))
        self.assertEqual(bridge.receive(), reply(4, text_result("ran anything")))
        self.assertEqual(bridge.finish(), [])
        self.assertEqual([headers["Authorization"] for headers, _ in self.server.mcp_requests],
                         [f"Bearer {AGENT_TOKEN}"] * 3)

    def test_a_notification_writes_nothing(self):
        bridge = self.start_bridge()
        self.initialize(bridge)
        bridge.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        bridge.send(request(3, "ping"))
        self.assertEqual(bridge.receive(), reply(3, {}))
        self.assertEqual(self.server.mcp_requests[1][1]["method"], "notifications/initialized")
        self.assertEqual(bridge.finish(), [])

    def test_an_event_stream_answer_is_forwarded_message_by_message(self):
        bridge = self.start_bridge()
        self.initialize(bridge)
        bridge.send(tool_call(4, "streamed"))
        self.assertEqual(bridge.receive()["method"], "notifications/progress")
        self.assertEqual(bridge.receive(), reply(4, text_result("streamed answer")))
        self.assertEqual(bridge.finish(), [])

    def test_a_slow_call_does_not_hold_up_the_next(self):
        bridge = self.start_bridge()
        self.initialize(bridge)
        bridge.send(tool_call(5, "slow"))
        bridge.send(tool_call(6, "release"))
        answers = {a["id"]: a["result"]["content"][0]["text"] for a in (bridge.receive(), bridge.receive())}
        self.assertEqual(answers, {5: "released", 6: "ran release"})

    def test_a_line_that_is_not_json_is_a_parse_error(self):
        bridge = self.start_bridge()
        bridge.send("this is not json")
        answer = bridge.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (None, -32700))
        self.assertEqual(self.server.mcp_requests, [])

    def test_an_http_error_is_an_internal_error_for_that_request(self):
        bridge = self.start_bridge()
        self.initialize(bridge)
        bridge.send(tool_call(7, "explode"))
        answer = bridge.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (7, -32603))
        self.assertIn("HTTP 500", answer["error"]["message"])

    def test_an_unreachable_hub_fails_requests_and_ignores_notifications(self):
        bridge = self.start_bridge(closed_port_url(MCP_PATH))
        bridge.send(request(8, "tools/list"))
        answer = bridge.receive()
        self.assertEqual((answer["id"], answer["error"]["code"]), (8, -32603))
        self.assertIn("cannot reach the coordbench hub", answer["error"]["message"])
        bridge.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        bridge.send(request(9, "ping"))
        self.assertEqual(bridge.receive()["id"], 9)
        self.assertEqual(bridge.finish(), [])


def python_says(code, env=None, cwd=None):
    """What a fresh, isolated interpreter prints for code; its stderr on failure."""
    done = subprocess.run([sys.executable, "-I", "-c", code], env=env or os.environ.copy(), cwd=cwd,
                          capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return done.stdout.strip()


class TraceTest(HubTestCase):
    """In debug a command leaves one "rpc_call" span per request to the hub
    and one "command" event per alias-run, on stderr or in COORD_TRACE_FILE;
    in prod it registers nothing and prints what it always printed. The
    process runs here read COORD_TRACE as the wrappers' environment sets
    it; the in-process runs set the level on the module cli loaded."""

    def setUp(self):
        super().setUp()
        for name in ("COORD_TRACE", "COORD_TRACE_FILE"):
            os.environ.pop(name, None)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.trace_file = self.scratch / "cli-trace.jsonl"
        self.trace = cli.coordtrace
        self.addCleanup(self.reset_trace)

    def reset_trace(self):
        self.trace.set_level("prod")
        for listener in list(self.trace._listeners):
            self.trace.unlisten(listener)

    def memory(self):
        self.trace.set_level("debug")
        return self.trace.listen(self.trace.Memory())

    def trace_lines(self, text=None):
        text = self.trace_file.read_text(encoding="utf-8") if text is None else text
        return [json.loads(line) for line in text.splitlines()]

    def alone(self):
        """A directory holding cli.py and nothing else, as a copy of the one
        file into a container leaves it."""
        alone = self.scratch / "alone"
        alone.mkdir()
        (alone / "cli.py").write_text((COORDBENCH / "cli.py").read_text(encoding="utf-8"), encoding="utf-8")
        return alone

    def test_cli_loads_coordbench_s_trace_not_the_standard_library_s(self):
        self.assertTrue(cli.same_file(self.trace, TRACE_PY), self.trace)
        self.assertTrue(hasattr(self.trace, "JsonLines"))
        self.assertFalse(cli.same_file(None, TRACE_PY))
        self.assertFalse(cli.same_file(cli, TRACE_PY))

    def test_in_debug_a_command_writes_its_rpc_calls_and_itself_to_the_trace_file_and_never_a_secret(self):
        self.server.token = AGENT_TOKEN
        os.environ.update(COORD_TRACE="debug", COORD_TRACE_FILE=str(self.trace_file), COORD_AGENT_TOKEN=AGENT_TOKEN)
        done = self.run_cli_process("alias-run", "send_message", "agent2", "a secret plan")
        # What the command prints is what it was: the tool's text alone.
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(json.loads(done.stderr)["arguments"], {"to": "agent2", "text": "a secret plan"})
        self.assertEqual(done.stderr.count("\n"), 1)
        events = self.trace_lines()
        self.assertEqual([e["kind"] for e in events],
                         ["rpc_call", "rpc_call_done", "rpc_call", "rpc_call_done", "command"])
        describe, described, call, called, command = events
        for event in events:
            self.assertIsInstance(event["t"], float)
            self.assertIsInstance(event["ts"], float)
        self.assertEqual({k: describe[k] for k in ("method", "path", "tool", "request_bytes")},
                         {"method": "GET", "path": RPC_PATH, "tool": None, "request_bytes": 0})
        self.assertEqual(set(describe), {"kind", "t", "ts", "method", "path", "tool", "request_bytes"})
        self.assertEqual(set(described), set(describe) | {"duration_ms", "ok", "status", "response_bytes"})
        self.assertEqual((described["status"], described["ok"]), (200, True))
        self.assertGreater(described["response_bytes"], 0)
        self.assertIsInstance(described["duration_ms"], float)
        self.assertEqual(described["duration_ms"], round(described["duration_ms"], 3))
        sent = {"tool": "send_message", "arguments": {"to": "agent2", "text": "a secret plan"}}
        self.assertEqual({k: call[k] for k in ("method", "path", "tool", "request_bytes")},
                         {"method": "POST", "path": RPC_PATH, "tool": "send_message",
                          "request_bytes": len(json.dumps(sent))})
        echo = {"text": json.dumps(sent, sort_keys=True), "isError": False}
        self.assertEqual((called["status"], called["ok"], called["response_bytes"]),
                         (200, True, len(json.dumps(echo))))
        self.assertEqual({k: command[k] for k in ("command", "tool", "exit_code")},
                         {"command": "coop-send", "tool": "send_message", "exit_code": 0})
        self.assertEqual(set(command), {"kind", "t", "ts", "command", "tool", "exit_code", "duration_ms"})
        self.assertGreaterEqual(command["duration_ms"], described["duration_ms"] + called["duration_ms"])
        text = self.trace_file.read_text(encoding="utf-8")
        for secret in (AGENT_TOKEN, "a secret plan", "agent2"):
            self.assertNotIn(secret, text)

    def test_in_debug_without_a_file_the_events_go_to_stderr_and_stdout_is_untouched(self):
        os.environ["COORD_TRACE"] = "debug"
        sent = self.run_cli_process("alias-run", "send_message", "agent2", "hi")
        self.assertEqual((sent.returncode, sent.stdout), (0, ""), sent.stderr)
        lines = [json.loads(line) for line in sent.stderr.splitlines()]
        events = [line for line in lines if "kind" in line]
        self.assertEqual([e["kind"] for e in events],
                         ["rpc_call", "rpc_call_done", "rpc_call", "rpc_call_done", "command"])
        told = [line for line in lines if "kind" not in line]
        self.assertEqual(told, [{"tool": "send_message", "arguments": {"to": "agent2", "text": "hi"}}])
        self.assertEqual(lines.index(told[0]), 4, "the tool's text comes after its call, before the command event")
        # A tool whose text goes to stdout: stdout is exactly that text.
        shown = self.run_cli_process("alias-run", "broadcast", "hello")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout), {"tool": "broadcast", "arguments": {"text": "hello"}})
        self.assertEqual(shown.stdout.count("\n"), 1)
        self.assertEqual([json.loads(line)["kind"] for line in shown.stderr.splitlines()],
                         ["rpc_call", "rpc_call_done", "rpc_call", "rpc_call_done", "command"])
        self.assertFalse(self.trace_file.exists())

    def test_in_prod_nothing_is_written_and_stderr_is_what_it_was(self):
        os.environ["COORD_TRACE_FILE"] = str(self.trace_file)
        for level in (None, "prod", "off"):
            with self.subTest(level=level):
                os.environ.pop("COORD_TRACE", None)
                if level:
                    os.environ["COORD_TRACE"] = level
                sent = self.run_cli_process("alias-run", "send_message", "agent2", "hi")
                self.assertEqual((sent.returncode, sent.stdout), (0, ""))
                self.assertEqual(sent.stderr, json.dumps({"arguments": {"to": "agent2", "text": "hi"},
                                                          "tool": "send_message"}, sort_keys=True) + "\n")
                shown = self.run_cli_process("call", "receive_messages")
                self.assertEqual((shown.returncode, shown.stderr), (0, ""))
                self.assertEqual(json.loads(shown.stdout), {"tool": "receive_messages", "arguments": {}})
        self.assertFalse(self.trace_file.exists())
        # The same in-process: a listener hears nothing from a prod command.
        memory = self.trace.listen(self.trace.Memory())
        self.assertEqual(run_cli("--url", self.rpc_url, "alias-run", "broadcast", "hi")[0], 0)
        self.assertEqual(memory, [])

    def test_a_refusal_and_an_unreachable_hub_are_recorded_as_not_ok(self):
        memory = self.memory()
        self.server.token = AGENT_TOKEN
        code, out, err = run_cli("--url", self.rpc_url, "alias-run", "send_message", "agent2", "hi")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("HTTP 401", err)
        self.assertEqual([e["kind"] for e in memory], ["rpc_call", "rpc_call_done", "command"])
        refused, command = memory[1], memory[2]
        self.assertEqual((refused["status"], refused["ok"]), (401, False))
        self.assertNotIn("response_bytes", refused)
        self.assertEqual({k: command[k] for k in ("command", "tool", "exit_code")},
                         {"command": "send_message", "tool": "send_message", "exit_code": 2},
                         "the hub could not be asked for the alias, so the tool's name stands in")
        del memory[:]
        code, _, _ = run_cli("--url", closed_port_url(RPC_PATH), "call", "receive_messages")
        self.assertEqual(code, 2)
        self.assertEqual([e["kind"] for e in memory], ["rpc_call", "rpc_call_done"])
        self.assertIs(memory[1]["ok"], False)
        self.assertNotIn("status", memory[1])
        self.assertNotIn("response_bytes", memory[1])
        # A refusal by the tool is still a call that went through, exit code and all.
        del memory[:]
        self.server.token = None
        self.assertEqual(run_cli("--url", self.rpc_url, "alias-run", "refuse", "7")[0], 7)
        self.assertEqual([(e["kind"], e.get("status"), e.get("ok")) for e in memory][1::2],
                         [("rpc_call_done", 200, True), ("rpc_call_done", 200, True)])
        self.assertEqual((memory[-1]["kind"], memory[-1]["command"], memory[-1]["exit_code"]),
                         ("command", "coord-refuse", 7))

    def test_configure_trace_listens_in_debug_alone_and_release_takes_it_off(self):
        self.assertIsNone(cli.configure_trace())
        self.trace.set_level("debug")
        os.environ["COORD_TRACE_FILE"] = str(self.trace_file)
        sink = cli.configure_trace()
        self.assertIsInstance(sink, self.trace.JsonLines)
        self.trace.event("probe", n=1)
        cli.release_trace(sink)
        cli.release_trace(None)
        self.trace.event("after")
        self.assertEqual([e["kind"] for e in self.trace_lines()], ["probe"])
        os.environ["COORD_TRACE_FILE"] = " "
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            sink = cli.configure_trace()
            self.trace.event("loud")
            cli.release_trace(sink)
        self.assertEqual(json.loads(stderr.getvalue())["kind"], "loud")
        self.assertFalse(stderr.closed)

    def test_a_copy_of_cli_py_alone_works_and_records_nothing(self):
        alone = self.alone()
        os.environ.update(COORD_TRACE="debug", COORD_TRACE_FILE=str(self.trace_file))
        done = subprocess.run([sys.executable, "-I", str(alone / "cli.py"), "--url", self.rpc_url,
                               "alias-run", "send_message", "agent2", "hi"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)
        self.assertEqual(json.loads(done.stderr)["arguments"], {"to": "agent2", "text": "hi"})
        self.assertFalse(self.trace_file.exists())
        self.assertEqual(python_says(f"""
import sys
sys.path.insert(0, {str(alone)!r})
import cli
assert cli.coordtrace is cli.NoTrace, cli.coordtrace
assert cli.coordtrace.level == "off"
assert "trace" not in sys.modules
with cli.coordtrace.debug_span("rpc_call", method="GET") as span:
    span.fields["status"] = 200
assert span.fields == {{"method": "GET", "status": 200}}
@cli.coordtrace.debug_span("command")
def run():
    return 3
assert run() == 3 and run.__name__ == "run"
assert cli.coordtrace.event("k", a=1) is None and cli.coordtrace.debug("k", a=1) is None
assert cli.configure_trace() is None
print("stand-in ok")
"""), "stand-in ok")

    def test_the_standard_library_s_trace_is_left_alone_when_it_got_there_first(self):
        # A process that imported the standard library's trace, with
        # coordbench last on sys.path: `import trace` would be the wrong
        # one, so cli loads its own by location and takes nobody's name.
        self.assertEqual(python_says(f"""
import sys, trace
assert hasattr(trace, "Trace"), "the standard library's"
sys.path.append({str(COORDBENCH)!r})
import cli
assert cli.coordtrace is not trace
assert hasattr(cli.coordtrace, "JsonLines") and cli.coordtrace.level == "prod"
assert cli.coordtrace.__file__ == {str(TRACE_PY)!r}
assert sys.modules["trace"] is trace
print("own copy")
""", cwd=self.scratch), "own copy")

    def test_cli_and_the_rest_of_coordbench_share_one_trace_whichever_is_imported_first(self):
        first = f"import sys; sys.path.insert(0, {str(COORDBENCH)!r}); import trace, cli; " \
                "print(cli.coordtrace is trace and sys.modules['trace'] is trace)"
        second = f"import sys; sys.path.insert(0, {str(COORDBENCH)!r}); import cli, trace; " \
                 "print(cli.coordtrace is trace and sys.modules['trace'] is trace)"
        self.assertEqual(python_says(first, cwd=self.scratch), "True")
        self.assertEqual(python_says(second, cwd=self.scratch), "True")
        # The level the environment sets reaches cli's events.
        env = dict(os.environ, COORD_TRACE="debug")
        self.assertEqual(python_says(second.replace("print(", "print(trace.level, ")[:-1] + ")", env=env,
                                     cwd=self.scratch), "debug True")


if __name__ == "__main__":
    unittest.main()
