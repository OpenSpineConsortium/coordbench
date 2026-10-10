"""Building blocks most arms share, so an arm is mostly a choice of blocks.

messaging_tools() is CooperBench's messaging (src/cooperbench/agents/_coop/
coop_msg.py at 63b9d44): one inbox per agent, send to one peer or to all,
drain the inbox as a JSON list, count what is unread, list the agents.
The tools carry CooperBench's command names as aliases (coop-send and the
rest) and keep coop_msg.py's arguments, exit codes and output, with one
exception that cli.py chooses and says: a text word that starts with a
dash is sent as the message, where coop_msg.py's argparse would take it
for an option and refuse it. The rest is kept as it is: a message goes to
whatever name it is addressed to, an inbox being made for the name, since
coop_msg.py pushes to <recipient>:inbox without looking at COOP_AGENTS,
and list_agents is how an agent learns the names; an empty text is sent,
and a missing one is sent as "", since coop_msg.py reads stdin and finds
it empty; a missing recipient is refused with exit code 2 in argparse's
words; coop-send says "sent to <recipient>" on stderr and nothing on
stdout, coop-broadcast says nothing at all; and every entry carries
timestamp and timestamp_iso.

wait_for_message is the tool CooperBench's team mode registers for its
CLI agents (team_harness/mcp_server.py), with its text and its schema's
properties (kit.schema adds "required": [], which mcp_server.py's schema
leaves out), and one deliberate difference. At 63b9d44 the tool never
finds a message: coop_msg.py keeps the #run:<id> fragment of
COOP_REDIS_URL as a key prefix and pushes to run:<id>:<agent>:inbox,
while mcp_server.py strips the same fragment from CB_TEAM_REDIS_URL and
BLPOPs <agent>:inbox, so every call there times out with the no-message
marker and the inbox is left for coop-recv. coordbench's wait_for_message
works on purpose: it hands over the oldest message and removes it from
the inbox. The wait is held between SHORTEST_WAIT_MS and LONGEST_WAIT_MS,
where CooperBench clamps only the lower end; wait_seconds says why the
cap stands above the maximum the schema advertises. README names this
among the team arm's deviations.

What the agents never see is the record. Each delivery is a "message"
event that carries the text, its timestamps, its own seq as message_id
and the seq of the call that sent it as send_id; each receive and each
undo of one names the message ids it handed over or put back. That is
what conversation.py derives CooperBench's conversation.json and
<agent>_sent.jsonl from, and the paper-era store with its read receipts.
git_log() is the history of a run's shared remote, which the hub keeps
when the run closes.
"""

import json
import math
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from arm import Tool, ToolError

TEXT = {"type": "string"}

# The bounds mcp_server.py's schema puts on timeout_ms, and its default.
# The schema keeps the advertised maximum; the handler takes more, up to
# LONGEST_WAIT_MS, for the reason wait_seconds gives.
SHORTEST_WAIT_MS, ADVERTISED_LONGEST_WAIT_MS, DEFAULT_WAIT_MS = 100, 30000, 5000

# The cap a wait is held to, coordbench's own: the team arm's cap on a
# request's wait (300 seconds, its LONGEST_WAIT_SECONDS), under the 330
# seconds every arm's .mcp.json lets one call to the hub run.
LONGEST_WAIT_MS = 300000

NO_MESSAGE = "(no message arrived within the timeout)"

# mcp_server.py's text for the tool, word for word, double spaces included.
WAIT_DESCRIPTION = ("Block server-side until a message arrives in your team inbox, "
                    "or until timeout_ms elapses.  Returns the message text on hit, "
                    "or a 'no message' marker on timeout.  Prefer this to busy-polling "
                    "coop-recv when you're idle between substantive actions.")
WAIT_TIMEOUT_DESCRIPTION = "How long to block before giving up."

# argparse's refusal of `coop-send` typed without a recipient, with its code.
MISSING_RECIPIENT = "the following arguments are required: recipient"


def schema(required=(), **properties):
    return {"type": "object", "properties": properties, "required": list(required)}


def marked_for_stderr(input_schema):
    """Mark a tool's input schema so that cli.py prints the tool's text on
    stderr, as coop_msg.py prints "sent to <recipient>". The mark is a
    keyword of the schema because the schema is what the hub's /rpc
    description already carries whole, JSON Schema lets a schema carry
    keywords it does not define, and an MCP client ignores one."""
    return {**input_schema, "x-coordbench-prints": "stderr"}


def inbox(run, agent):
    return run.state.setdefault("inboxes", {}).setdefault(agent, [])


class Entry(dict):
    """An inbox entry: the dict coop_msg.py pushes, which is all an agent
    ever reads (json.dumps writes a dict's items, never its attributes),
    and beside it the id of the "message" event that queued it, so the
    receive that hands it over, or the undo that puts it back, can name
    it in the record without the agent's output changing by a byte."""

    __slots__ = ("message_id",)

    def __init__(self, fields, message_id=None):
        super().__init__(fields)
        self.message_id = message_id


def message_ids(entries):
    """The message ids of inbox entries, None for one queued without."""
    return [getattr(entry, "message_id", None) for entry in entries]


def string_argument(call, name, default=None):
    """The argument as a string, or default when it is absent or null; any
    other type is refused rather than coerced, before anything is queued."""
    value = call.arguments.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolError(f"{name} must be a string, not {type(value).__name__}")
    return value


def recipient_of(call):
    """The recipient, refused as argparse refuses coop-send without one."""
    recipient = string_argument(call, "to")
    if recipient is None:
        raise ToolError(MISSING_RECIPIENT, exit_code=2)
    return recipient


def deliver(run, sender, recipient, content, send_id=None):
    """Queue content for recipient, whoever that is. coop_msg.py pushes to
    <recipient>:inbox without checking the name against the agents, so a
    message to a misspelt peer is queued where nobody reads; this does the
    same, so the commands behave alike in every arm. The count is taken
    before the append, so nothing is queued that cannot be reported.

    The "message" event is the record of the delivery, one per recipient
    as coop_msg.py logs one line per recipient: to, chars, the text
    itself (content, which stays off the trace stream), the entry's two
    timestamps, message_id (the event's own seq, read in the same hold of
    the lock as the emit, so it is exact) and send_id, the seq of the
    send's or broadcast's "tool" event, which ties the deliveries of one
    broadcast together. Writing the text into events.jsonl as it is sent
    keeps it when the hub crashes or forgets the run."""
    chars = len(content)
    fields = {"from": sender, "to": recipient, "content": content, "timestamp": time.time(),
              "timestamp_iso": datetime.now().isoformat()}
    with run.lock:
        message_id = run.next_seq()
        inbox(run, recipient).append(Entry(fields, message_id))
        run.emit("message", sender, to=recipient, chars=chars, content=content,
                 timestamp=fields["timestamp"], timestamp_iso=fields["timestamp_iso"],
                 message_id=message_id, send_id=send_id)


def send(call):
    recipient = recipient_of(call)
    deliver(call.run, call.agent, recipient, string_argument(call, "text", ""), call.tool_seq)
    return f"sent to {recipient}"


def broadcast(call):
    # The text is checked once, before the first peer, so a bad one is
    # delivered to nobody rather than to the peers before it.
    text = string_argument(call, "text", "")
    for peer in call.run.peers(call.agent):
        deliver(call.run, call.agent, peer, text, call.tool_seq)
    return ""


def receive(call):
    """Drain the inbox and hand over everything in it, oldest first. What
    was drained for a reply the hub then could not write (the client was
    gone) goes back at the head of the inbox, in its order, through
    call.on_lost, as a wait's one message does: in a pilot the reply to
    a drain met a broken pipe and the messages were lost with it. A
    drain of an empty inbox has nothing to put back and registers no
    undo. The "receive" event names what it handed over by message id
    and the call by its tool_seq (call_id), so a reader knows which
    message was read, when and by which call."""
    run, me = call.run, call.agent
    with run.lock:
        box = inbox(run, me)
        messages, box[:] = list(box), []
        if messages:
            call.on_lost(lambda: put_back(run, me, messages, call.tool_seq))
    run.emit("receive", me, count=len(messages), message_ids=message_ids(messages), call_id=call.tool_seq)
    return json.dumps(messages, indent=2)


def peek(call):
    with call.run.lock:
        return str(len(inbox(call.run, call.agent)))


def list_agents(call):
    return "\n".join(call.run.agents)


def wait_seconds(timeout_ms):
    """timeout_ms as the seconds to wait: a number, or a string holding
    one, finite, held between SHORTEST_WAIT_MS and LONGEST_WAIT_MS;
    anything else is refused rather than crashed on.

    The cap stands above the maximum the schema advertises, and the two
    differ on purpose. The schema keeps CooperBench's 30000, so an MCP
    client that validates the arguments against it stops there, exactly
    as with CooperBench's server; the shell command and the hub's /rpc
    path check nothing against the schema, and an agent idle between
    actions is better served by one long wait than by ten short ones,
    each a turn of its own. 300 seconds is the team arm's cap on a
    request's wait, and sits under the 330 seconds the arms' .mcp.json
    gives one call, so the hub answers before the client gives up."""
    try:
        milliseconds = float(timeout_ms)
    except (TypeError, ValueError):
        raise ToolError(f"timeout_ms must be a number of milliseconds, not {timeout_ms!r}")
    if not math.isfinite(milliseconds):
        raise ToolError(f"timeout_ms must be finite, not {milliseconds}")
    return min(LONGEST_WAIT_MS, max(SHORTEST_WAIT_MS, milliseconds)) / 1000


def wait_for_message(call):
    """Block until the inbox holds a message, then hand over the oldest.

    The wait and the pop are one hold of run.lock, which wait_for lets go
    of while it waits, so a receive_messages or a second wait by the same
    agent cannot empty the inbox between the two. The seconds spent in
    wait_for are reported through call.waited, so the hub's tool_done
    event tells a call that waited for another agent (blocked_ms) from
    one the hub was slow to answer. A closed run wakes the wait, which
    then hands over a message the inbox already holds, or gives the
    marker at once rather than sleeping on. A cancelled call takes
    nothing, since its answer would reach nobody; one cancelled after
    the take has its message put back by the hub, through the undo
    registered with call.on_lost, the same undo that serves a reply the
    hub could not write (the client was gone): the message goes back at
    the head of the inbox, for the next call to find."""
    timeout_ms = call.arg("timeout_ms")
    seconds = wait_seconds(DEFAULT_WAIT_MS if timeout_ms is None else timeout_ms)
    run, me = call.run, call.agent
    with run.lock:
        started = time.monotonic()
        run.wait_until(lambda: run.closed or call.cancelled.is_set() or bool(inbox(run, me)),
                       seconds, agent=me)
        call.waited(time.monotonic() - started)
        if call.cancelled.is_set() or not inbox(run, me):
            return NO_MESSAGE
        message = inbox(run, me).pop(0)
        call.on_lost(lambda: put_back(run, me, [message], call.tool_seq))
    run.emit("receive", me, count=1, message_ids=message_ids([message]), call_id=call.tool_seq)
    return json.dumps(message)


def put_back(run, agent, messages, call_id=None):
    """The undo of a take whose reply was never written: the messages go
    back at the head of the inbox, in the order they were taken, and the
    record says so. The "receive" event stays, since the hub did hand
    them over; the "receive_lost" that follows it, with the same count
    and the same message ids, is what lets a reader of the events tell
    that handover from one the agent saw, rather than count a message
    received twice when the next call takes it again. call_id is the
    call whose take this undoes."""
    inbox(run, agent)[0:0] = messages
    run.emit("receive_lost", agent, count=len(messages), message_ids=message_ids(messages), call_id=call_id)


def messaging_tools(wait=True):
    """coop_msg.py's five commands as tools, each with its command name as
    alias and its positional arguments as argv, and with wait=True the
    long poll of CooperBench's team mode."""
    tools = [
        Tool("send_message", "Send a message to one peer, by agent id.", send,
             marked_for_stderr(schema(["to"], to=TEXT, text=TEXT)), alias="coop-send", argv=("to", "text")),
        Tool("broadcast", "Send a message to every other agent.", broadcast,
             schema(text=TEXT), alias="coop-broadcast", argv=("text",)),
        Tool("receive_messages", "Drain your inbox; prints the messages as a JSON list, oldest first.",
             receive, alias="coop-recv"),
        Tool("peek_messages", "How many unread messages your inbox holds.", peek, alias="coop-peek"),
        Tool("list_agents", "Every agent id in this run, one per line.", list_agents, alias="coop-agents"),
    ]
    if wait:
        tools.append(Tool(
            "wait_for_message", WAIT_DESCRIPTION, wait_for_message,
            schema(timeout_ms={"type": "integer", "minimum": SHORTEST_WAIT_MS,
                               "maximum": ADVERTISED_LONGEST_WAIT_MS, "default": DEFAULT_WAIT_MS,
                               "description": WAIT_TIMEOUT_DESCRIPTION})))
    return tools


def message_metrics(run):
    """One count per delivery, as CooperBench's len(sent_msgs) counts the
    lines coop_msg.py logs: a broadcast to two peers is two. The words
    are the text split on whitespace, the unit the paper's "10 messages
    totaling over 3,000 words" counts in."""
    sent = [e for e in run.events if e["kind"] == "message"]
    by_agent = {}
    for event in sent:
        by_agent[event["agent"]] = by_agent.get(event["agent"], 0) + 1
    return {"messages_sent": len(sent), "messages_by_agent": by_agent,
            "message_chars": sum(e.get("chars", 0) for e in sent),
            "message_words": sum(len((e.get("content") or "").split()) for e in sent)}


# The shared git remote's history, for the arms that have one: the hub
# takes it when the run closes, before anything is torn down, and hands it
# to the runner as git_log.txt. The runner gives a close 120 seconds
# (run.py's HubClient), so the fetch and the log are held well inside
# that, and the fetch is shallow: the remote is seeded with the task
# image's whole clone, and only the run's own commits are wanted.

GIT_FETCH_DEPTH = 500
GIT_FETCH_TIMEOUT_SECONDS = 60
GIT_LOG_TIMEOUT_SECONDS = 30
GIT_LOG = ("log", "--all", "--reflog", "--stat", "--decorate=short", "--no-color")


def run_git(arguments, timeout=GIT_LOG_TIMEOUT_SECONDS):
    """git with no prompt and no stdin, its output as text; a failure, a
    timeout or a host without git is raised as RuntimeError, with git's
    own words when it said any. The operator's own git configuration is
    left out (a log.date, a format.pretty, a signature check would change
    the text), so git_log.txt reads the same on every host."""
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_CONFIG_NOSYSTEM": "1"}
    try:
        done = subprocess.run(["git", *arguments], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=environment)
    except (OSError, subprocess.TimeoutExpired) as problem:
        raise RuntimeError(f"git {git_subcommand(arguments)} failed: {problem}") from None
    if done.returncode != 0:
        raise RuntimeError(f"git {git_subcommand(arguments)} failed: {done.stderr.strip()[-300:]}")
    return done.stdout


def git_subcommand(arguments):
    """The subcommand a git command line runs, past -C and its directory."""
    words = list(arguments)
    while words[:1] == ["-C"]:
        words = words[2:]
    return words[0] if words else "git"


def commit_exists(repository, ref):
    try:
        run_git(["-C", repository, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    except RuntimeError:
        return False
    return True


def log_of(repository, base_ref=None):
    """git log --all --reflog --stat in a repository on this host. The
    upstream history below base_ref, the commit every agent started from,
    is left out, since it would bury the run's few commits under the
    project's thousands; a base the repository lacks leaves all in."""
    arguments = ["-C", repository, *GIT_LOG]
    if base_ref and commit_exists(repository, base_ref):
        arguments += ["--not", base_ref]
    return run_git(arguments)


def fetched_copy(remote, scratch):
    """Every ref of the remote, GIT_FETCH_DEPTH commits deep, in a bare
    repository under scratch: the refs and the commits the run made,
    though no reflog, since git's protocol sends none."""
    copy = str(Path(scratch) / "remote.git")
    run_git(["init", "--quiet", "--bare", copy])
    run_git(["-C", copy, "fetch", "--quiet", f"--depth={GIT_FETCH_DEPTH}", str(remote), "+refs/*:refs/*"],
            timeout=GIT_FETCH_TIMEOUT_SECONDS)
    return copy


def git_log(remote, base_ref=None):
    """The shared remote's history as text. A remote that is a directory
    on this host is read in place, its reflog included when the
    repository keeps one; any other (the runner's git:// URL) is fetched
    into a scratch repository first."""
    if Path(remote).is_dir():
        return log_of(str(remote), base_ref)
    with tempfile.TemporaryDirectory(prefix="coordbench-git-log-") as scratch:
        return log_of(fetched_copy(remote, scratch), base_ref)
