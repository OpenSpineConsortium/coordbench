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


def call_tool(run, agent, tools, params):
    """Run one tools/call; returns (result, None) or (None, (code, message))."""
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return None, (INVALID_PARAMS, "the tool name must be a string")
    if name not in tools:
        return None, (INVALID_PARAMS, f"unknown tool {name!r}")
    if not isinstance(arguments, dict):
        return None, (INVALID_PARAMS, "arguments must be an object")
    run.emit("tool", agent, tool=name)
    try:
        return text_result(tools[name].handler(Call(run, agent, arguments))), None
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


def handle(message, run, agent, instructions=""):
    """Answer one JSON-RPC message from `agent`; None for a notification
    or a response, which get no answer. The shape is checked before
    anything is dispatched, so a malformed request is refused with the
    code that names what is wrong rather than failing inside a handler."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    if "method" not in message or "id" not in message:
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
        result, problem = call_tool(run, agent, tools, params)
        return response(request_id, result) if problem is None else error(request_id, *problem)
    return error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")


def handle_safely(message, run, agent, instructions=""):
    """handle(), with a failure of the hub itself answered as -32603 for
    this message alone; the traceback goes to the hub's log."""
    try:
        return handle(message, run, agent, instructions)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return error(request_id_of(message), INTERNAL_ERROR, FAILED)


def handle_body(body, run, agent, instructions=""):
    """Answer a request body: one message or a batch (a JSON array, which
    the 2025-03-26 revision allows). None when nothing needs an answer.
    An empty batch is one invalid request, as JSON-RPC says."""
    try:
        message = json.loads(body)
    except ValueError:
        return error(None, PARSE_ERROR, "the body is not JSON")
    if isinstance(message, list):
        if not message:
            return error(None, INVALID_REQUEST, "an empty batch")
        answers = [a for a in (handle_safely(m, run, agent, instructions) for m in message)
                   if a is not None]
        return answers or None
    return handle_safely(message, run, agent, instructions)
