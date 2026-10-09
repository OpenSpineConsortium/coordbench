"""The Model Context Protocol's server side, as much of it as coordination needs.

MCP is JSON-RPC 2.0: a client sends initialize, then the notification
notifications/initialized, then tools/list and tools/call; ping may come at
any time. This module answers those for a set of tools and nothing else
(no resources, prompts or sampling), and is shared by the hub's HTTP
endpoint and the stdio bridge's tests, so both speak the same protocol.

Errors follow the specification. A message the server cannot read as a
request (not an object, no "jsonrpc": "2.0", an id that is not a string
or an integer, a method that is not a string, or an object with no method
that is no response either, having neither result nor error) is -32600;
params that are not an object (for ping, tools/list and initialize as
much as for tools/call, since MCP has no by-position params), an unknown
tool, a tool name that is not a string, arguments that are not an object
or an argument the tool does not take are -32602; an unknown method is
-32601; and a tool that runs and fails is a normal result with isError
set, which the model sees and can act on. Each message of a batch is
checked and answered on its own, so a bad member, or one the hub fails
on (-32603), never loses the others' answers, which may be tool calls
that already ran. An error for a message whose id could not be read
carries no id at all: JSON-RPC would put null there, but neither MCP
schema allows a null id. A body nested too deep for the parser to read
is a parse error (-32700) like any other body that is not JSON.

A tool may block (wait_for_message, a request with a wait), and its
caller may give up first. The notification notifications/cancelled names
the request to stop: handle() finds that tools/call among the ones in
flight on the session, sets its Call's cancelled event and wakes the
run, so the tool returns without taking what it waited for, and the
notification gets no answer, as none does. A cancelled call takes
nothing, and one cancelled after it took something puts it back: the
cancel, and the call's leaving the calls in flight when its handler
returns, both happen under the run's lock, so every cancel that found
the call in flight is seen at that return, and call_tool then runs the
undos the handler registered (Call.on_lost) before the answer goes out.
The answer is still produced and written (for a wait, the no-message
marker, or the message that went back); a client that still listens
discards it, as the specification tells it to, and one that has gone
makes the write fail. An id that is not in flight is ignored, as the
specification allows: the answer may have crossed the cancellation on
the way, the call may have run under another session, or the id names a
member of a batch that has not started yet, which then runs whole. When
the write of an answer fails, for a cancelled call or any other, the hub
runs the same undos, so a message or a response taken for a reply that
reached nobody goes back where the next call finds it.

Every tools/call that runs is framed by two of the run's events, "tool"
before its handler and "tool_done" after, the second with the handler's
wall time and the part of it spent waiting for another agent; Run's
docstring in arm.py has the fields.
"""

import json
import sys
import time
import traceback

from arm import Call, ToolError

# Newest first; a client asking for one of these gets it back, any other
# version gets the newest, and the client decides whether it can go on.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "coordbench", "version": "0.1.0"}

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)
FAILED = "the hub failed while answering; its log has the details"


def loads(body):
    """json.loads, with a body nested too deep for the parser (which gives
    up with a RecursionError) refused as any other body that is not JSON:
    the client sent it, so it is a parse error, not a failure of the hub."""
    try:
        return json.loads(body)
    except RecursionError:
        raise ValueError("the body is nested too deep to read") from None


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


def same_id(one, other):
    """Whether two request ids are one id: equal, and of one type, so that
    1 is neither true nor "1", which Python's equality alone would allow
    for the first."""
    return type(one) is type(other) and one == other


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


def is_initialize_result(result):
    """Whether a result is initialize's: among the results this server
    gives, only that one names the protocol version."""
    return isinstance(result, dict) and "protocolVersion" in result


def unknown_arguments(schema, arguments):
    """The argument names the tool does not take: those not among its
    input schema's properties, when the schema names some and does not
    open itself to other names with additionalProperties (true, or a
    schema for them). A tool that names no properties takes any name,
    since its schema says nothing about them."""
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return []
    additional = schema.get("additionalProperties")
    if not schema["properties"] or additional is True or isinstance(additional, dict):
        return []
    return [key for key in arguments if key not in schema["properties"]]


def milliseconds(seconds):
    return round(seconds * 1000, 3)


def call_tool(run, agent, tools, params, started=None, finished=None):
    """Run one tools/call; returns (result, None) or (None, (code, message)).
    The name, the arguments and their names are checked first, so a call
    the tool cannot take is refused before anything runs or is recorded:
    an agent that typed timeout for timeout_ms hears so, rather than
    getting the default quietly. started(call), when given, runs before
    the handler does, so the caller can reach a call that blocks: to
    cancel it, or to undo what it took when its reply was never written.
    finished(call), when given, runs under the run's lock once the
    handler has returned and before the call's cancellation is read; the
    caller takes the call out of its calls in flight there, so a cancel
    that found the call in flight is seen, and one that comes later finds
    nothing."""
    name = params.get("name")
    # Only an arguments key that is missing (or null) means no arguments:
    # [] or false is not an object and is refused below, where `or {}`
    # would have run the tool with nothing and told nobody.
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(name, str):
        return None, (INVALID_PARAMS, "the tool name must be a string")
    if name not in tools:
        return None, (INVALID_PARAMS, f"unknown tool {name!r}")
    if not isinstance(arguments, dict):
        return None, (INVALID_PARAMS, "arguments must be an object")
    tool = tools[name]
    unknown = unknown_arguments(tool.input_schema, arguments)
    if unknown:
        return None, (INVALID_PARAMS, f"{name} takes no argument {', '.join(map(repr, unknown))}; "
                                      f"its arguments are {', '.join(tool.input_schema['properties'])}")
    call = Call(run, agent, arguments)
    if started is not None:
        started(call)
    run.emit("tool", agent, tool=name)
    began = time.monotonic()
    result, is_error = run_handler(run, tool, call)
    duration = time.monotonic() - began
    cancelled = settle(call, finished)
    run.emit("tool_done", agent, tool=name, duration_ms=milliseconds(duration),
             blocked_ms=milliseconds(call.blocked_s), is_error=is_error, cancelled=cancelled)
    return result, None


def run_handler(run, tool, call):
    """The handler's answer as a tools/call result, and whether it is an
    error: a refusal (ToolError) carries the tool's exit code for the
    shell command; a crash is the arm's bug, which fails this one call,
    not the agent's session or the other calls of a batch, and leaves
    its traceback in the hub's log."""
    try:
        return text_result(tool.handler(call)), False
    except ToolError as problem:
        result = text_result(str(problem), is_error=True)
        result["_meta"] = {"coordbench/exitCode": problem.exit_code}
        return result, True
    except Exception as problem:
        traceback.print_exc(file=sys.stderr)
        run.emit("tool_crash", call.agent, tool=tool.name, error=type(problem).__name__)
        return text_result(f"the {tool.name} tool failed inside the hub ({type(problem).__name__}); "
                           "the run's log has the details", is_error=True), True


def settle(call, finished):
    """The call's end, once its handler has returned: it leaves the calls
    in flight (finished) and its cancellation is read, in one hold of the
    run's lock, the lock cancel() sets the event under, so no cancel
    falls between the two. A call cancelled after it took something puts
    it back (Call.lost) before its answer goes out, since the client
    discards that answer. Returns whether the call was cancelled."""
    with call.run.lock:
        if finished is not None:
            finished(call)
        cancelled = call.cancelled.is_set()
    if cancelled and call.undos:
        call.lost()
    return cancelled


def cancel(params, in_flight):
    """notifications/cancelled: the call in flight under params.requestId
    is marked cancelled and its run woken, so a blocking tool returns
    without taking anything. The call is looked up again under the run's
    lock, which settle() takes the call out of flight under, so a cancel
    is either seen when the call's handler returns or finds nothing. An
    id not in flight is left alone, as the specification allows: its
    answer may have crossed the cancellation, the call may have run under
    another session, or it names a member of a batch not started yet. The
    reason the client gives is its own word for its log and changes
    nothing here."""
    request_id = params.get("requestId") if isinstance(params, dict) else None
    if in_flight is None or not valid_id(request_id):
        return
    call = in_flight.get(request_id)
    if call is None:
        return
    with call.run.lock:
        if in_flight.get(request_id) is call:
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
        if "result" in message or "error" in message:
            return None
        return error(message["id"] if valid_id(message.get("id")) else None, INVALID_REQUEST,
                     "neither a request (no method) nor a response (no result, no error)")
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
    cancellation that arrives meanwhile finds it. The entry goes only
    while it is this call's: a client that reuses an id for a second
    call while the first still runs keeps the second's entry when the
    first ends."""
    entered = []

    def started(call):
        entered.append(call)
        if sink is not None:
            sink.append(call)
        if in_flight is not None:
            in_flight[request_id] = call

    def finished(call):
        if in_flight is not None and in_flight.get(request_id) is call:
            del in_flight[request_id]

    try:
        return call_tool(run, agent, tools, params, started, finished)
    finally:
        # Normally done by settle(); this covers a handler that let a
        # BaseException through, so the entry never outlives its call.
        for call in entered:
            finished(call)


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
        message = loads(body)
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
