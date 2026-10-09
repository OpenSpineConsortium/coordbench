"""The Model Context Protocol's server side, as much of it as coordination needs.

MCP is JSON-RPC 2.0: a client sends initialize, then the notification
notifications/initialized, then tools/list and tools/call; ping may come at
any time. This module answers those for a set of tools and nothing else
(no resources, prompts or sampling), and is shared by the hub's HTTP
endpoint and the stdio bridge's tests, so both speak the same protocol.

Errors follow the specification. A message the server cannot read as a
request (not an object, no "jsonrpc": "2.0", an id that is not a string
or an integer, a method that is not a string) is -32600; params that are
not an object, an unknown tool, a tool name that is not a string or bad
arguments are -32602; an unknown method is -32601; and a tool that runs
and fails is a normal result with isError set, which the model sees and
can act on. Each message of a batch is checked and answered on its own,
so a bad member, or one the hub fails on (-32603), never loses the
others' answers, which may be tool calls that already ran. An error for a
message whose id could not be read carries no id at all: JSON-RPC would
put null there, but neither MCP schema allows a null id.

A tool may block (wait_for_message, a request with a wait), and its
caller may give up first. The notification notifications/cancelled names
the request to stop: handle() finds that tools/call among the ones in
flight on the session, sets its Call's cancelled event and wakes the
run, so the tool returns without taking what it waited for, and the
notification gets no answer, as none does. An id that is not in flight
is ignored, as the specification allows, since the answer may have
crossed the cancellation on the way. The call still produces its result
(for a wait, the no-message marker), which the hub tries to write; a
client that still listens discards it, as the specification tells it
to, and one that has gone makes the write fail. When that write fails,
for a cancelled call or any other, the hub runs the undo each Call
registered with on_lost, so a message or a response taken for a reply
that reached nobody goes back where the next call finds it.
"""

import json
import sys
import traceback

from arm import Call, ToolError

# Newest first; a client asking for one of these gets it back, any other
# version gets the newest, and the client decides whether it can go on.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "coordbench", "version": "0.1.0"}

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)
FAILED = "the hub failed while answering; its log has the details"


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id, code, message):
    """A JSON-RPC error; with no id (None) the id key is left out, since
    the request's id could not be read and MCP allows no null id."""
    answer = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if request_id is not None:
        answer["id"] = request_id
    return answer


def valid_id(request_id):
    """A string or an integer, as MCP's RequestId; bool is an int in Python
    and must not pass as one."""
    return isinstance(request_id, (str, int)) and not isinstance(request_id, bool)


def request_id_of(message):
    """The id an answer to this message can carry: the message's own when it
    is a request with a readable id, else None."""
    if isinstance(message, dict) and "method" in message and valid_id(message.get("id")):
        return message["id"]
    return None


def text_result(value, is_error=False):
    text = value if isinstance(value, str) else json.dumps(value, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def initialize_result(params, instructions=""):
    asked = params.get("protocolVersion")
    result = {
        "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
    }
    if instructions:
        result["instructions"] = instructions
    return result


def call_tool(run, agent, tools, params, started=None):
    """Run one tools/call; returns (result, None) or (None, (code, message)).
    started(call), when given, runs before the handler does, so the
    caller can reach a call that blocks: to cancel it, or to undo what it
    took when its reply was never written."""
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return None, (INVALID_PARAMS, "the tool name must be a string")
    if name not in tools:
        return None, (INVALID_PARAMS, f"unknown tool {name!r}")
    if not isinstance(arguments, dict):
        return None, (INVALID_PARAMS, "arguments must be an object")
    call = Call(run, agent, arguments)
    if started is not None:
        started(call)
    run.emit("tool", agent, tool=name)
    try:
        return text_result(tools[name].handler(call)), None
    except ToolError as problem:
        result = text_result(str(problem), is_error=True)
        result["_meta"] = {"coordbench/exitCode": problem.exit_code}
        return result, None
    except Exception as problem:
        # A bug in an arm fails this one call, not the agent's session or
        # the other calls of a batch; the traceback goes to the hub's log.
        traceback.print_exc(file=sys.stderr)
        run.emit("tool_crash", agent, tool=name, error=type(problem).__name__)
        return text_result(f"the {name} tool failed inside the hub ({type(problem).__name__}); "
                           "the run's log has the details", is_error=True), None


def cancel(params, in_flight):
    """notifications/cancelled: the call in flight under params.requestId
    is marked cancelled and its run woken, so a blocking tool returns
    without taking anything. An id not in flight is left alone, as the
    specification allows: its answer may have crossed the cancellation,
    or the call ran under another session. The reason the client gives
    is its own word for its log and changes nothing here."""
    request_id = params.get("requestId") if isinstance(params, dict) else None
    if in_flight is None or not valid_id(request_id):
        return
    call = in_flight.get(request_id)
    if call is None:
        return
    with call.run.lock:
        call.cancelled.set()
        call.run.lock.notify_all()


def handle(message, run, agent, instructions="", sink=None, in_flight=None):
    """Answer one JSON-RPC message from `agent`; None for a notification
    or a response, which get no answer. The shape is checked before
    anything is dispatched, so a malformed request is refused with the
    code that names what is wrong rather than failing inside a handler.

    `sink` and `in_flight` are the hub's way to reach a tools/call that
    blocks. The Call of every tools/call goes onto `sink`, a list, before
    its handler runs. `in_flight` is a dict of the session's calls under
    way by request id: a tools/call is entered there before its handler
    runs and removed after it returns, and notifications/cancelled looks
    its id up there (cancel(), above)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    if "method" not in message:
        return None
    if "id" not in message:
        if message["method"] == "notifications/cancelled":
            cancel(message.get("params"), in_flight)
        return None
    request_id, method = message["id"], message["method"]
    if not valid_id(request_id):
        return error(None, INVALID_REQUEST, "the id must be a string or an integer")
    if not isinstance(method, str):
        return error(request_id, INVALID_REQUEST, "the method must be a string")
    params = message.get("params")
    params = {} if params is None else params
    if not isinstance(params, dict):
        return error(request_id, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        return response(request_id, initialize_result(params, instructions))
    if method == "ping":
        return response(request_id, {})
    tools = run.arm.tools_for(run, agent)
    if method == "tools/list":
        return response(request_id, {"tools": [t.describe() for t in tools.values()]})
    if method == "tools/call":
        result, problem = call_in_flight(request_id, run, agent, tools, params, sink, in_flight)
        return response(request_id, result) if problem is None else error(request_id, *problem)
    return error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")


def call_in_flight(request_id, run, agent, tools, params, sink, in_flight):
    """call_tool, with the Call entered under its request id among the
    session's calls in flight for as long as its handler runs, so a
    cancellation that arrives meanwhile finds it."""
    def started(call):
        if sink is not None:
            sink.append(call)
        if in_flight is not None:
            in_flight[request_id] = call

    try:
        return call_tool(run, agent, tools, params, started)
    finally:
        if in_flight is not None:
            in_flight.pop(request_id, None)


def handle_safely(message, run, agent, instructions="", sink=None, in_flight=None):
    """handle(), with a failure of the hub itself answered as -32603 for
    this message alone; the traceback goes to the hub's log."""
    try:
        return handle(message, run, agent, instructions, sink, in_flight)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return error(request_id_of(message), INTERNAL_ERROR, FAILED)


def handle_body(body, run, agent, instructions="", sink=None, in_flight=None):
    """Answer a request body: one message or a batch (a JSON array, which
    the 2025-03-26 revision allows). None when nothing needs an answer.
    An empty batch is one invalid request, as JSON-RPC says. `sink` and
    `in_flight` are handle()'s."""
    try:
        message = json.loads(body)
    except ValueError:
        return error(None, PARSE_ERROR, "the body is not JSON")
    if isinstance(message, list):
        if not message:
            return error(None, INVALID_REQUEST, "an empty batch")
        answers = [a for a in (handle_safely(m, run, agent, instructions, sink, in_flight)
                               for m in message)
                   if a is not None]
        return answers or None
    return handle_safely(message, run, agent, instructions, sink, in_flight)
