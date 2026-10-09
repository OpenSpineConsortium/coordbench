"""The Model Context Protocol's server side, as much of it as coordination needs.

MCP is JSON-RPC 2.0: a client sends initialize, then the notification
notifications/initialized, then tools/list and tools/call; ping may come at
any time. This module answers those for a set of tools and nothing else
(no resources, prompts or sampling), and is shared by the hub's HTTP
endpoint and the stdio bridge's tests, so both speak the same protocol.

Errors follow the specification: an unknown method is -32601, an unknown
tool or bad arguments -32602, and a tool that runs and fails is a normal
result with isError set, which the model sees and can act on.
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


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def text_result(value, is_error=False):
    text = value if isinstance(value, str) else json.dumps(value, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def initialize_result(params, instructions=""):
    asked = (params or {}).get("protocolVersion")
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
    name = (params or {}).get("name")
    arguments = (params or {}).get("arguments") or {}
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
    or a response, which get no answer."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    method = message.get("method")
    request_id = message.get("id")
    if method is None or "id" not in message:
        return None
    params = message.get("params") or {}
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


def handle_body(body, run, agent, instructions=""):
    """Answer a request body: one message or a batch (a JSON array, which
    the 2025-03-26 revision allows). None when nothing needs an answer."""
    try:
        message = json.loads(body)
    except ValueError:
        return error(None, PARSE_ERROR, "the body is not JSON")
    if isinstance(message, list):
        answers = [a for a in (handle(m, run, agent, instructions) for m in message) if a is not None]
        return answers or None
    return handle(message, run, agent, instructions)
