"""Building blocks most arms share, so an arm is mostly a choice of blocks.

messaging_tools() is CooperBench's messaging (src/cooperbench/agents/_coop/
coop_msg.py at 63b9d44): one inbox per agent, send to one peer or to all,
drain the inbox as a JSON list, count what is unread, list the agents.
The tools carry CooperBench's command names as aliases (coop-send and the
rest), so an agent can use them as MCP tools or as the shell commands the
authors' prompts name. wait_for_message is the long poll CooperBench gives
its CLI agents in team mode (team_harness/mcp_server.py), so an idle agent
waits for news instead of polling.
"""

import json
import time

from arm import Tool, ToolError

TEXT = {"type": "string"}


def schema(required=(), **properties):
    return {"type": "object", "properties": properties, "required": list(required)}


def inbox(run, agent):
    return run.state.setdefault("inboxes", {}).setdefault(agent, [])


def deliver(run, sender, recipient, content):
    if recipient not in run.agents:
        raise ToolError(f"no agent named {recipient!r}; the agents are {', '.join(run.agents)}")
    entry = {"from": sender, "to": recipient, "content": content, "timestamp": time.time()}
    with run.lock:
        inbox(run, recipient).append(entry)
    run.emit("message", sender, to=recipient, chars=len(content))


def send(call):
    deliver(call.run, call.agent, call.arg("to", required=True), call.arg("text", required=True))
    return f"sent to {call.arguments['to']}"


def broadcast(call):
    text = call.arg("text", required=True)
    peers = call.run.peers(call.agent)
    for peer in peers:
        deliver(call.run, call.agent, peer, text)
    return f"sent to {', '.join(peers) or 'nobody'}"


def receive(call):
    with call.run.lock:
        box = inbox(call.run, call.agent)
        messages, box[:] = list(box), []
    call.run.emit("receive", call.agent, count=len(messages))
    return json.dumps(messages, indent=2)


def peek(call):
    with call.run.lock:
        return str(len(inbox(call.run, call.agent)))


def list_agents(call):
    return "\n".join(call.run.agents)


def wait_for_message(call):
    """Block until the inbox holds a message, then hand over the oldest."""
    timeout_ms = min(30000, max(100, int(call.arg("timeout_ms", 5000))))
    run, me = call.run, call.agent
    if not run.wait_until(lambda: bool(inbox(run, me)), timeout_ms / 1000):
        return "(no message arrived within the timeout)"
    with run.lock:
        message = inbox(run, me).pop(0)
    run.emit("receive", me, count=1)
    return json.dumps(message)


def messaging_tools(wait=True):
    tools = [
        Tool("send_message", "Send a message to one peer, by agent id.", send,
             schema(["to", "text"], to=TEXT, text=TEXT), alias="coop-send", argv=("to", "text...")),
        Tool("broadcast", "Send a message to every other agent.", broadcast,
             schema(["text"], text=TEXT), alias="coop-broadcast", argv=("text...",)),
        Tool("receive_messages", "Drain your inbox; prints the messages as a JSON list, oldest first.",
             receive, alias="coop-recv"),
        Tool("peek_messages", "How many unread messages your inbox holds.", peek, alias="coop-peek"),
        Tool("list_agents", "Every agent id in this run, one per line.", list_agents, alias="coop-agents"),
    ]
    if wait:
        tools.append(Tool(
            "wait_for_message",
            "Block until a message arrives in your inbox, or until timeout_ms passes; returns the "
            "message, or a 'no message' marker on timeout. Prefer this to polling when you are idle.",
            wait_for_message,
            schema(timeout_ms={"type": "integer", "minimum": 100, "maximum": 30000, "default": 5000})))
    return tools


def message_metrics(run):
    sent = [e for e in run.events if e["kind"] == "message"]
    by_agent = {}
    for event in sent:
        by_agent[event["agent"]] = by_agent.get(event["agent"], 0) + 1
    return {"messages_sent": len(sent), "messages_by_agent": by_agent,
            "message_chars": sum(e.get("chars", 0) for e in sent)}
