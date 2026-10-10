"""The team arm: CooperBench's team mode, the best arm of the authors' ablation.

One agent per feature, numbered in the order of the sorted features. The
first, which has the lowest feature, is the lead and the rest are members,
as runner/team.py lays them out. Every agent gets the same tools:

- the shared task list (team_harness/task_list.py and coop_task.py):
  create, claim, update and list, with CooperBench's fields, its four
  statuses and its claim rule, under which a task owned by another agent,
  a pre-assigned one included, cannot be claimed;
- the typed request protocol (team_harness/protocol.py): request, respond
  and pending, for questions that want an answer by id;
- CooperBench's messaging (coop_msg.py, from kit.py) and wait_for_message,
  a tool with the name, text and schema of the long poll CooperBench's
  team mode registers for its CLI agents (team_harness/mcp_server.py),
  which here returns a message where CooperBench's never did; the list of
  differences below says why.

Each tool carries the shell command CooperBench installs for it
(install_snippet.sh) as its alias and coop_task.py's options (--assign,
-n/--note, --mine, --open, --wait) as its flags, so
`coop-task-update <id> blocked -n "<why>"` works as in the authors' runs,
and an MCP client sees the same arguments as plain fields. The words are
read as coop_task.py's argparse reads them: one word per positional, so a
title or a body of several words wants its quotes and a word too many is
refused, by cli.py, with argparse's exit code 2; an empty title or body
is a value, as "" is to argparse; and a request is queued for whatever
name it is addressed to, since protocol.py checks the recipient against
nobody, so a wait on a request to a misspelt peer runs out. A refused
call exits with coop_task.py's own code, so the prompt's "exit 0 if you
got it, 2 if someone else owns it" stays true: 1 for a task that does not
exist, 2 for a claim another agent holds, 3 for an update by a non-owner,
4 for a bad status, 5 for a wait that ran out, 6 for an unknown request
and 7 for a request addressed to someone else. A missing argument exits
2 with argparse's words, as coop_task.py exits before it reaches Redis:
the shell command sends a required positional it has no word for as
missing, rather than reading it from stdin as it reads a body left off,
and required() refuses it. A --wait that is not a number is refused by
the shell command itself, before anything is sent, and so are the words
true, false and null, which the handler would otherwise read as 1.0,
0.0 and no wait at all.

setup() seeds the list as runner/team.py does: one task per member
feature, assigned to that member, and the lead's integrate-and-submit
task. The prompt is build_team_instruction's (team_harness/prompt.py),
word for word, with one sentence added after the command list naming the
MCP tool behind each command. The arm needs the shared scratchpad the
prompt names (/workspace/shared) and runs without git, as the authors'
ablation did; given a shared remote (the run option "git_remote") it adds
CooperBench's git block, which coop-git holds.

The run option tool_surface=bash reproduces what CooperBench's
claude_code team agent had at 63b9d44: the coop-* commands in Bash, and
one MCP server, cooperbench-team, serving wait_for_message alone, which
its adapter registers in the agent's .claude.json. The driver then loads
no plugin (no skill, no coordbench server), hands the hub over under
that server's name (Arm.claude_surface), the hub's MCP endpoint lists
that one tool, and the prompt leaves the added sentence out.

What CooperBench's runner keeps of a team run is kept too: the task-log
and the final list, handed to the runner at close as task_log.json and
tasks.json in CooperBench's shapes (artifacts), and the lead and the
team's switches beside the metrics (lead_agent, team_features, all on,
since this arm has no switch). A request's and a response's body, which
CooperBench's task-log does not keep, is in the run's events as content.

Where this differs from CooperBench, and why:

- The list lives in the run's memory, not Redis, and every check-and-write
  holds the run's lock, so a claim is atomic in fact; CooperBench reads
  the owner and then writes it, which its own comments call race-safe
  only because contention is small.
- A request's wait is capped (LONGEST_WAIT_SECONDS, 300 seconds) and a
  closed run wakes it, where coop_task.py's BLPOP waits as long as it is
  asked to. The cap keeps a blocked call inside the limits of its callers,
  which sit above it on purpose: Claude Code gives up on an MCP call after
  the timeout the arm's .mcp.json sets (330 seconds) and cli.py after 600,
  so an unbounded wait would be cut off by the client anyway. A wait the
  client cancels takes no response; one cancelled after the take, or
  whose answer the client was not there to read, has its response put
  back by the hub (hub.py says how), through the undo send_request
  registers. The cap is about answering the caller, not about what a
  cut-off wait would lose.
- wait_for_message returns a message. At 63b9d44 CooperBench's never did:
  its server BLPOPs <agent>:inbox, with no prefix, while coop_msg.py
  pushes to run:<id>:<agent>:inbox, so every call there ran out with the
  no-message marker and left the inbox for coop-recv. Here the tool hands
  over the oldest message and removes it from the inbox, and holds the
  wait between 100 ms and 300 s, the same cap as a request's wait
  (kit.py says why the cap stands above the schema's 30000). The
  difference is deliberate: a long poll that never answers measures
  nothing.
- The mirror of the list under /workspace/shared/tasks, which coop-task-list
  writes as it lists, is written by the hub from its host, so it needs the
  shared directory as the hub's host sees it (the run option
  "shared_dir_host"); without one there is no mirror. The volume is the
  agents' to write, so the hub treats it as untrusted: it writes only into
  a real directory named tasks at the top of the volume, never through a
  symlink an agent put in its place, and not when the run was already
  closed when the list was read. A close that lands between that read
  and the writes still gets one mirror, which is harmless: the runner
  removes the volume only after the close has answered, so that mirror
  goes into a volume about to go, or nowhere once it is gone.

Every ported piece is from CooperBench at 63b9d44 (MIT licence) and says
where it came from.
"""

import functools
import importlib.util
import json
import os
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import kit
from arm import (SUBMISSION_BLOCK, TOOL_SURFACES, AgentSpec, Arm, Tool, ToolError, builtin_arms_dir,
                 tool_surface)

# build_team_instruction joins its sections with this rule.
SECTION_RULE = "\n\n---\n\n"

# CooperBench's text uses the em dash; the escape keeps this file ASCII
# while the prompt keeps the authors' character.
DASH = "\u2014"

# task_list.py's VALID_STATUSES.
VALID_STATUSES = frozenset({"open", "in_progress", "blocked", "done"})

# runner/team.py seeds the list in this name.
BENCH_RUNNER = "bench-runner"

# coop_task.py waits as long as asked; the hub caps it under the limits of
# its callers, so each hears back before it gives up: cli.py waits 600
# seconds, and Claude Code the 330 the arm's .mcp.json sets (without that
# key it would give up at 60, cancel the call, and get exit 5 for every
# wait longer than that).
LONGEST_WAIT_SECONDS = 300

# The run event each audit-log entry is recorded as, so events.jsonl
# keeps the same trail CooperBench keeps in its task-log.
EVENT_KINDS = {"create": "task_create", "claim": "task_claim", "update": "task_update",
               "request": "request", "response": "response"}

# coop_task.py's exit codes, which its callers branch on. USAGE is
# argparse's code for words it cannot read, which coop_task.py exits with
# before it does anything.
NO_SUCH_TASK = 1
USAGE = 2
OWNED_BY_ANOTHER = 2
NOT_THE_OWNER = 3
BAD_STATUS = 4
TIMED_OUT = 5
UNKNOWN_REQUEST = 6
NOT_ADDRESSED_TO_YOU = 7

TEXT = kit.TEXT
BOOLEAN = {"type": "boolean"}


def fresh_id(*taken):
    """A ten-hex-digit id, as task_list.py and protocol.py make them, that
    none of the given collections holds."""
    while True:
        candidate = uuid.uuid4().hex[:10]
        if not any(candidate in ids for ids in taken):
            return candidate


class Board:
    """The team's shared state as CooperBench keeps it in Redis: the tasks,
    the open requests, the responses not yet collected, and one audit log
    of both (the task-log). Every method holds the run's lock across its
    whole read, check and write, which is what makes a claim atomic: of
    any number of agents racing for an unowned task, exactly one wins."""

    def __init__(self, run):
        self.run = run
        self.tasks = {}
        self.log = []
        self.requests = {}
        self.responses = {}

    def record(self, kind, said=None, **fields):
        """One task-log entry, shaped as CooperBench's _log writes it, kept
        also as a run event. `said` is what only the event carries: a
        request's or a response's text (content, which stays off the trace
        stream), its timestamps and the seq of the call that sent it
        (send_id). CooperBench's task-log holds no body, so the entry
        keeps its shape, and the text, which no other record keeps once
        the run is released, is in events.jsonl."""
        self.log.append({"kind": kind, **fields, "ts": time.time()})
        details = {key: value for key, value in fields.items() if key != "by"}
        self.run.emit(EVENT_KINDS[kind], fields.get("by"), **details, **(said or {}))

    def create(self, title, by, owner="", metadata=None):
        """task_list.py's create. A task starts open even when it has an
        owner: only a claim moves it to in_progress."""
        with self.run.lock:
            task_id = fresh_id(self.tasks)
            self.tasks[task_id] = {
                "id": task_id, "title": title, "owner": owner or "", "status": "open",
                "created_by": by, "created_at": time.time(), "last_note": "",
                "metadata": dict(metadata or {}),
            }
            self.record("create", task_id=task_id, by=by, title=title)
        return task_id

    def claim(self, task_id, by):
        """coop_task.py's cmd_claim. A task another agent owns is refused,
        whether it claimed it or was assigned it; claiming one's own task
        again succeeds and is logged again, as in CooperBench."""
        with self.run.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ToolError(f"task {task_id} does not exist", NO_SUCH_TASK)
            if task["owner"] not in ("", by):
                raise ToolError(f"task {task_id} owned by {task['owner']}", OWNED_BY_ANOTHER)
            task.update(owner=by, status="in_progress")
            self.record("claim", task_id=task_id, by=by)

    def update(self, task_id, by, status, note=None):
        """coop_task.py's cmd_update: the owner only, a known status only."""
        with self.run.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ToolError(f"task {task_id} does not exist", NO_SUCH_TASK)
            if task["owner"] != by:
                raise ToolError(f"task {task_id} owned by {task['owner']!r}, not you", NOT_THE_OWNER)
            if status not in VALID_STATUSES:
                raise ToolError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}",
                                BAD_STATUS)
            task["status"] = status
            details = {"status": status}
            if note is not None:
                task["last_note"] = details["note"] = note
            self.record("update", task_id=task_id, by=by, **details)

    def snapshot(self):
        """Copies of every task, in id order as coop-task-list prints them,
        and of the log, taken at one moment."""
        with self.run.lock:
            tasks = [{**self.tasks[i], "metadata": dict(self.tasks[i]["metadata"])}
                     for i in sorted(self.tasks)]
            return tasks, [dict(entry) for entry in self.log]

    def request(self, sender, recipient, kind, body, send_id=None):
        """protocol.py's request: an open request in the recipient's queue,
        whoever the recipient is. protocol.py checks the name against
        nobody, so a request to a misspelt peer waits for an answer that
        never comes, as it does in CooperBench."""
        with self.run.lock:
            request_id = fresh_id(self.requests, self.responses)
            sent = time.time()
            self.requests[request_id] = {"id": request_id, "from": sender, "to": recipient,
                                         "kind": kind, "body": body, "ts": sent}
            self.record("request", said=message_fields(body, sent, send_id),
                        request_id=request_id, by=sender, to=recipient, request_kind=kind)
        return request_id

    def await_response(self, request_id, seconds, call=None):
        """protocol.py's await_response, a BLPOP on the request's responses:
        the oldest response once one is queued, or None when the time is
        up, the run has closed, or `call` (the calling Call) has been
        cancelled, which takes nothing: the caller has given up and its
        answer would reach nobody. The wait and the pop are one hold of
        the lock, so no second wait can empty the queue between them. The
        seconds spent in wait_for are booked on the call (Call.waited),
        so the hub's tool_done event carries them as blocked_ms: time
        spent waiting for another agent, not the hub's own."""
        run = self.run
        gone = call.cancelled.is_set if call is not None else (lambda: False)
        with run.lock:
            started = time.monotonic()
            run.wait_until(lambda: run.closed or gone() or bool(self.responses.get(request_id)),
                           seconds, agent=call.agent if call is not None else None)
            if call is not None:
                call.waited(time.monotonic() - started)
            queue = self.responses.get(request_id)
            return queue.pop(0) if queue and not gone() else None

    def put_back(self, request_id, response):
        """The undo of a wait whose reply was never written: the response
        goes back at the head of its request's queue, for the next wait."""
        with self.run.lock:
            self.responses.setdefault(request_id, []).insert(0, response)

    def respond(self, responder, request_id, body, send_id=None):
        """protocol.py's respond: only the addressee answers, and only once,
        since answering closes the request."""
        with self.run.lock:
            request = self.requests.get(request_id)
            if request is None:
                raise ToolError(f"unknown request {request_id}", UNKNOWN_REQUEST)
            if request["to"] != responder:
                raise ToolError(f"request {request_id} not addressed to {responder}", NOT_ADDRESSED_TO_YOU)
            response = {"id": request_id, "from": responder, "to": request["from"],
                        "body": body, "ts": time.time()}
            self.responses.setdefault(request_id, []).append(response)
            del self.requests[request_id]
            self.record("response", said=message_fields(body, response["ts"], send_id),
                        request_id=request_id, by=responder, to=request["from"])

    def pending(self, agent):
        """protocol.py's list_pending: the open requests to this agent, by id."""
        with self.run.lock:
            return [dict(self.requests[i]) for i in sorted(self.requests)
                    if self.requests[i]["to"] == agent]


def message_fields(body, sent, send_id):
    """The fields of a request's or response's event that the task-log
    leaves out: its text and when it was sent, in coop_msg.py's two forms,
    and the call that sent it."""
    return {"content": body, "timestamp": sent, "timestamp_iso": datetime.fromtimestamp(sent).isoformat(),
            "send_id": send_id}


def board_of(run):
    with run.lock:
        if "team" not in run.state:
            run.state["team"] = Board(run)
        return run.state["team"]


def lead_of(run):
    """The lead's id. build_team_instruction's fallback name stands in for a
    run without one, which this arm's layout never makes."""
    return next((agent for agent, spec in run.agents.items() if spec.role == "lead"), "team-lead")


# Arguments, as coop_task.py's argparse would hand them over. The shell's
# words reach these as plain fields: cli.py maps them onto each tool's
# argv and flags, so the handlers never parse a word themselves and an
# MCP client's note is never re-read as an option.

def required(call, name):
    """An argument the command cannot go without, refused as argparse
    refuses a missing positional: with its message, exiting 2. An empty
    string is a value, as "" is to argparse."""
    value = call.arguments.get(name)
    if value is None:
        raise ToolError(f"the following arguments are required: {name}", USAGE)
    return str(value)


def optional_text(call, name):
    value = call.arguments.get(name)
    return None if value is None else str(value)


def switch(call, name):
    """A store_true option: set by its flag or an MCP client's true, and by
    the words true, 1 or yes from a key=value shell call."""
    value = call.arguments.get(name)
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def wait_seconds(value):
    """protocol.py's clamp from below, and this hub's cap from above. A
    value that is not a number is refused as argparse's type=float
    refuses it, before anything is sent."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"argument --wait: invalid float value: {value!r}", USAGE) from None
    return min(LONGEST_WAIT_SECONDS, max(0.05, seconds))


# The handlers. Each prints what coop_task.py prints: an id, JSON, or
# nothing at all on success.

def task_create(call):
    title = required(call, "title")
    return board_of(call.run).create(title, call.agent, owner=optional_text(call, "assign") or "")


def task_claim(call):
    board_of(call.run).claim(required(call, "task_id"), call.agent)
    return ""


def task_update(call):
    task_id, status = required(call, "task_id"), required(call, "status")
    board_of(call.run).update(task_id, call.agent, status, optional_text(call, "note"))
    return ""


def task_list(call):
    mine, open_only = switch(call, "mine"), switch(call, "open")
    run = call.run
    with run.lock:
        # One hold for the snapshot and the run's state, so the mirror is
        # of a list that was listed while the run was open.
        tasks, log = board_of(run).snapshot()
        closed = run.closed
    chosen = [task for task in tasks
              if (not mine or task["owner"] == call.agent) and (not open_only or task["status"] == "open")]
    mirror_if_shared(run, tasks, log, closed)
    return json.dumps(chosen, indent=2)


def send_request(call):
    recipient, kind = required(call, "recipient"), required(call, "kind")
    wait = call.arguments.get("wait")
    seconds = None if wait is None else wait_seconds(wait)
    board = board_of(call.run)
    request_id = board.request(call.agent, recipient, kind, optional_text(call, "body") or "", call.tool_seq)
    if seconds is None:
        return request_id
    response = board.await_response(request_id, seconds, call)
    if response is None:
        # Also the cancelled case: the caller gave up, and hears what
        # coop-request says of a wait that ran out.
        raise ToolError(f"timed out waiting for response to {request_id}", TIMED_OUT)
    call.on_lost(lambda: board.put_back(request_id, response))
    return json.dumps(response)


def answer_request(call):
    board_of(call.run).respond(call.agent, required(call, "request_id"), optional_text(call, "body") or "",
                               call.tool_seq)
    return ""


def pending_requests(call):
    return json.dumps(board_of(call.run).pending(call.agent), indent=2)


def task_tools():
    statuses = {"type": "string", "enum": sorted(VALID_STATUSES)}
    assign = {"type": "string", "description": "an agent id to pre-assign it to; that agent still claims it"}
    return [
        Tool("task_create",
             "Create a task on the team's shared list and print its id. It starts open; assign "
             "pre-sets its owner, who must still claim it. "
             "Shell: coop-task-create [--assign <agent>] \"<title>\".",
             task_create, kit.schema(["title"], title=TEXT, assign=assign),
             alias="coop-task-create", argv=("title",), flags={"--assign": "assign"}),
        Tool("task_claim",
             "Claim a task: you become its owner and it moves to in_progress. Refused when another "
             "agent owns it, by claim or by assignment (the command exits 2: pick another task); "
             "claiming your own again is fine. Shell: coop-task-claim <task_id>.",
             task_claim, kit.schema(["task_id"], task_id=TEXT),
             alias="coop-task-claim", argv=("task_id",)),
        Tool("task_update",
             "Set the status of a task you own (open, in_progress, blocked or done), with an "
             "optional note the others read as its last_note. Refused for a task you do not own "
             "(exit 3) or another status (exit 4). "
             "Shell: coop-task-update <task_id> <status> [-n \"<note>\"].",
             task_update, kit.schema(["task_id", "status"], task_id=TEXT, status=statuses, note=TEXT),
             alias="coop-task-update", argv=("task_id", "status"), flags={"-n": "note", "--note": "note"}),
        Tool("task_list",
             "Every task on the team's list, as JSON; mine keeps the tasks you own, open keeps "
             "those whose status is open. Shell: coop-task-list [--mine] [--open].",
             task_list, kit.schema(mine=BOOLEAN, open=BOOLEAN),
             alias="coop-task-list", flags={"--mine": "mine", "--open": "open"}),
    ]


def protocol_tools():
    wait = {"type": "number", "minimum": 0, "maximum": LONGEST_WAIT_SECONDS,
            "description": "seconds to block for the response; leave out to return the request id at once"}
    return [
        Tool("request",
             "Send a typed request (a kind, such as plan_approval, and a body) to one agent and "
             "print its id; with wait, block up to that many seconds for the response and print it "
             "instead, or exit 5 when none comes. "
             "Shell: coop-request <recipient> <kind> [body] [--wait N].",
             send_request, kit.schema(["recipient", "kind"], recipient=TEXT, kind=TEXT, body=TEXT, wait=wait),
             alias="coop-request", argv=("recipient", "kind", "body"), flags={"--wait": "wait"}),
        Tool("respond",
             "Answer an open request addressed to you; the response goes to the requester waiting "
             "on it, and the request closes. Refused for an unknown or closed request (exit 6) and "
             "for one addressed to another agent (exit 7). Shell: coop-respond <request_id> [body].",
             answer_request, kit.schema(["request_id"], request_id=TEXT, body=TEXT),
             alias="coop-respond", argv=("request_id", "body")),
        Tool("pending",
             "The open requests addressed to you, as JSON. Shell: coop-pending.",
             pending_requests, alias="coop-pending"),
    ]


# The list's mirror on the shared scratchpad. The hub writes it from its
# host, as root when the volume's host path asks for it, into a directory
# the agents fill as they like, so every step is relative to a descriptor
# of the real tasks directory: a symlink an agent planted in its place is
# refused by the kernel as the directory is opened, and nothing an agent
# does to the path afterwards moves where the writes and the unlinks land.
# A check of the path before the writes would leave a window between the
# two, which is why there is none.

def open_tasks_directory(shared):
    """A descriptor on <shared>/tasks, the leaf made when it is missing.
    The volume's root is the runner's, taken as given; only the leaf is
    made, so a volume the runner has removed is not brought back as a
    stray directory. O_NOFOLLOW and O_DIRECTORY make the kernel refuse a
    symlink, or anything but a directory, in the open itself."""
    tasks = Path(shared).resolve() / "tasks"
    try:
        os.mkdir(tasks)
    except FileExistsError:
        pass
    return os.open(tasks, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def unlink_quietly(directory, name):
    try:
        os.unlink(name, dir_fd=directory)
    except OSError:
        pass


def write_atomically(directory, name, text):
    """Write the file called name in the open directory through a temporary
    file and a rename, so a reader sees the old file or the new one, never
    half of one (fs_mirror.py). The temporary file is made with O_EXCL, so
    a file an agent left under its name is refused rather than written."""
    temporary = f".{name}.{uuid.uuid4().hex[:8]}"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory)
    try:
        with open(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        unlink_quietly(directory, temporary)
        raise


def mirror_task_list(directory, tasks, log):
    """coop_task.py's _snapshot_to_directory, into the open directory: one
    JSON file per task, stale ones removed, an index and a copy of the log.
    A stale entry that will not go (a directory an agent named so) is left,
    as CooperBench leaves it, and the rest of the mirror is still written."""
    live = {task["id"] for task in tasks}
    for task in tasks:
        write_atomically(directory, f"{task['id']}.json", json.dumps(task, indent=2, default=str))
    with os.scandir(directory) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        if name.endswith(".json") and not name.startswith("_") and name[:-5] not in live:
            unlink_quietly(directory, name)
    index = {"updated_at": time.time(), "ids": sorted(live)}
    write_atomically(directory, "_index.json", json.dumps(index, indent=2))
    write_atomically(directory, "_log.jsonl", "".join(json.dumps(entry) + "\n" for entry in log))


def mirror_if_shared(run, tasks, log, closed):
    """The side effect of coop-task-list, best effort as CooperBench's is,
    and not for a run that was already closed when the list was read: the
    runner removes the volume once the run is closed, and a mirror then
    would leave a directory of its own. A close that lands between the
    read and these writes still gets one mirror, harmlessly: the volume
    goes only after the close has answered, so the mirror lands in a
    volume about to be removed or, the root already gone, nowhere, since
    open_tasks_directory makes the leaf and never the root."""
    shared = run.options.get("shared_dir_host")
    if not shared or closed:
        return
    try:
        directory = open_tasks_directory(shared)
    except OSError:
        return
    try:
        mirror_task_list(directory, tasks, log)
    except OSError:
        pass
    finally:
        os.close(directory)


# The prompt: team_harness/prompt.py, word for word.

TEAM_LIST_USAGE = """Available shell commands (Redis-backed, atomic):

```bash
coop-task-create "<title>"                 # creates an open, unassigned task; prints task_id
coop-task-create --assign <agent> "<title>" # creates and pre-assigns; agent must still claim
coop-task-claim <task_id>                   # exit 0 if you got it, 2 if someone else owns it
coop-task-update <task_id> <status> [-n "<note>"]
                                            # statuses: open | in_progress | blocked | done
coop-task-list                              # JSON list of every task in the team
coop-task-list --mine                       # tasks you own
coop-task-list --open                       # open, unassigned tasks
```

Messaging (`coop-send` / `coop-recv` / `coop-broadcast` / `coop-peek`)
also works and is the right tool for short questions that don't
warrant a new task."""


def lead_block(agent_id, members, usage):
    """_lead_block, with the command list passed in so the MCP sentence can
    follow it."""
    member_list = ", ".join(f"`{m}`" for m in members)
    return f"""## You are the team-lead

You are **{agent_id}** {DASH} the team-lead.  Members reporting to you:
{member_list}.

### The integration step is the WHOLE point of your job {DASH} DO NOT SKIP IT

The benchmark scores the **merged** team output, not your individual
contribution.  If you submit `patch.txt` containing only your own
feature, the team **fails** {DASH} even if your feature works perfectly.
The single most common failure mode here is "lead ran `git diff > patch.txt`
before pulling in the member's work."  Avoid this.

Workflow:

1. **Plan to avoid conflicts.** Read the feature spec.  If features
   touch the same file/function, divide the work so changes occupy
   disjoint regions (e.g., one feature owns the function signature,
   the other owns helper additions).  Drop a one-paragraph plan in
   `/workspace/shared/PLAN.md` so members see your decomposition.
2. Break the spec into 2-4 concrete tasks and assign one per member
   with `coop-task-create --assign <agent> "..."`.
3. You **may also implement a task yourself** in parallel.
4. Poll progress: `coop-task-list` periodically.  If a member's task
   is `blocked`, read the `last_note` and either reassign or
   unblock it with `coop-send`.

### Before submitting {DASH} MANDATORY integration checklist

You may submit ONLY after **all five** of these are true:

- [ ] `coop-task-list` shows every team task as `done`.
- [ ] Every member has dropped their patch at
      `/workspace/shared/<agent_id>.patch`.  (Run
      `ls /workspace/shared/*.patch` to verify.)
- [ ] You have read each member's patch (`cat /workspace/shared/<agent>.patch`)
      and applied it to your working tree (`git apply
      /workspace/shared/<agent>.patch`, fixing any conflicts
      manually with Edit until the tree builds).
- [ ] The merged tree builds / compiles.  Run the project's build
      or import test before continuing.
- [ ] `git diff` now shows BOTH features {DASH} your own and every
      member's {DASH} present in the working tree.

Only when ALL five boxes are checked, write `/workspace/repo/patch.txt`
via `git diff` and exit {DASH} this is REQUIRED:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

If `wc -l patch.txt` looks too small to contain everyone's work, you
skipped the integration step {DASH} go back to step 3 of the checklist.

{usage}

A shared scratchpad volume is mounted at `/workspace/shared/` for
coordination {DASH} files there are NOT evaluated.  Drop design notes,
interface contracts, or member-produced patches there so the whole
team can see them with `ls /workspace/shared/`."""


def member_block(agent_id, lead, usage):
    """_member_block, with the command list passed in as lead_block's is."""
    return f"""## You are a team member

You are **{agent_id}**.  The team-lead is **{lead}**, who will
organize work into tasks for you to claim.

### Stay in your lane

The lead may have left a plan at `/workspace/shared/PLAN.md`.  Read it
first {DASH} it tells you which file regions your feature owns, vs. those
reserved for your peers.  Edit ONLY the regions your task owns.

Workflow:

1. Run `coop-task-list --open` to see what needs doing.  If your
   `agent_id` appears as a pre-assigned `owner` on a task, that's the
   one the lead expects you to take.  You can also `ls
   /workspace/shared/tasks/` to browse without the CLI.
2. `coop-task-claim <task_id>`.  If you lose the race (exit code 2),
   pick another.
3. Read `/workspace/shared/PLAN.md` if it exists, then implement.
   Stay within your region.  When you hit a blocker, run
   `coop-task-update <task_id> blocked -n "<what you need>"` and
   `coop-send {lead} "blocked on task <id>: ..."`.
4. When done, ALWAYS export your diff for the lead to consume:

   ```bash
   cd /workspace/repo && git diff > /workspace/shared/{agent_id}.patch
   ```

   Then `coop-task-update <task_id> done -n "patch at
   /workspace/shared/{agent_id}.patch"`.  The lead will integrate.

### Final submission {DASH} REQUIRED

The bench scores per-agent.  Before exiting you MUST write your own
`/workspace/repo/patch.txt`:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

Your `/workspace/repo/patch.txt` should reflect your final working
tree.  If the lead asked you to merge in their plan, do so first.

{usage}

A shared scratchpad volume is mounted at `/workspace/shared/` for
coordination {DASH} files there are NOT evaluated.  Use it for anything
your peers might need to see {DASH} partial diffs, interface sketches,
error logs from your reproduction script."""


def joined_names(pairs):
    """`a` is `b`, `c` is `d`, and `e` is `f`: the pairs as one clause."""
    phrases = [f"`{alias}` is `{name}`" for alias, name in pairs]
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + ", and " + phrases[-1]


def mcp_sentence(tools):
    """The one sentence this arm adds to CooperBench's prompt, built from the
    tools themselves so it names whatever the hub really serves."""
    pairs = [(tool.alias, tool.name) for tool in tools if tool.alias.startswith("coop-")]
    if not pairs:
        return ""
    return ("Every coop-* command is also a tool of the MCP server `coordbench`, with the same "
            f"task list and inbox behind it: {joined_names(pairs)}.")


@functools.cache
def coop_git_module():
    """The built-in coop-git arm's module, which holds CooperBench's git block
    (_coop/prompt.py's _git_block, which build_team_instruction also
    imports). Loaded only for a run with a shared remote, so a team run
    without git never depends on it."""
    path = builtin_arms_dir() / "coop-git" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_coop_git_for_team", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_block(agent_id, partners):
    return coop_git_module().git_block(agent_id, partners)


def task_text(run, agent, specs):
    """The agent's spec as CooperBench passes it: the feature file's text,
    unstripped, since team.py hands read_text() straight to the prompt."""
    return SECTION_RULE.join(specs[number] for number in run.agents[agent].features)


# team_harness/metrics.py.

def compute_metrics(events, final_tasks):
    """compute_metrics from the task-log and the tasks at the end, unchanged."""
    creates = [e for e in events if e.get("kind") == "create"]
    claims = [e for e in events if e.get("kind") == "claim"]
    updates = [e for e in events if e.get("kind") == "update"]
    first_create = min((e.get("ts", 0.0) for e in creates), default=None)
    first_claim = min((e.get("ts", 0.0) for e in claims), default=None)
    time_to_first_claim = None
    if first_create is not None and first_claim is not None:
        time_to_first_claim = round(first_claim - first_create, 3)
    return {
        "tasks_total": len(final_tasks),
        "tasks_done": sum(1 for t in final_tasks if t.get("status") == "done"),
        "unowned_at_end": sum(1 for t in final_tasks if not t.get("owner")),
        "time_to_first_claim_seconds": time_to_first_claim,
        "claims_per_agent": dict(Counter(e.get("by", "?") for e in claims)),
        "updates_per_agent": dict(Counter(e.get("by", "?") for e in updates)),
    }


def tool_call_counts(run):
    """How often each tool was called, from the hub's "tool" events, as the
    coop arms count them."""
    return dict(Counter(event["tool"] for event in run.events if event["kind"] == "tool"))


# runner/team.py's team_features with every switch on, which is how
# coordbench's team arm always runs: it has no switch to turn one off.
# auto_refresh drives a poller CooperBench attaches to its Python-loop
# adapters only, so for a Claude Code agent it changes nothing in either
# harness, and the record keeps CooperBench's value for a default run.
TEAM_FEATURES = {"task_list": True, "scratchpad": True, "mcp": True, "auto_refresh": True, "protocol": True}

# The MCP server CooperBench's team mode registers for its CLI agents
# (team_harness MCP_SERVER_NAME), and the one tool it serves; on the bash
# tool surface the hub stands in for it under the same name.
COOPERBENCH_MCP_SERVER = "cooperbench-team"


class TeamArm(Arm):
    name = "team"
    description = ("CooperBench's team mode, the best arm of the authors' ablation: a lead and "
                   "members, one per feature, with a shared task list, typed requests, messages "
                   "and a shared scratchpad.")
    needs = ("shared_volume",)
    tool_surfaces = TOOL_SURFACES
    bash_mcp_server = COOPERBENCH_MCP_SERVER
    bash_mcp_tools = ("wait_for_message",)

    def layout(self, features):
        # runner/team.py zips agent1, agent2 and so on with the sorted
        # features and makes the first the lead.
        return [AgentSpec(f"agent{i}", "lead" if i == 1 else "member", (number,))
                for i, number in enumerate(sorted(features), 1)]

    def setup(self, run):
        """runner/team.py's seeding: each member's feature as a task assigned
        to it, then the lead's task, in that order and in that wording."""
        board = board_of(run)
        lead = lead_of(run)
        for agent, spec in run.agents.items():
            if agent != lead:
                feature = spec.features[0]
                board.create(f"Implement feature {feature}", BENCH_RUNNER, owner=agent,
                             metadata={"feature_id": feature, "assigned_to": agent})
        feature = run.agents[lead].features[0]
        board.create(f"Lead-only: integrate and submit feature {feature}", BENCH_RUNNER, owner=lead,
                     metadata={"feature_id": feature, "assigned_to": lead, "lead_task": True})

    def tools(self, run, agent):
        return task_tools() + protocol_tools() + kit.messaging_tools(wait=True)

    def prompt(self, run, agent, specs):
        return SECTION_RULE.join(self.sections(run, agent, specs))

    def sections(self, run, agent, specs):
        """build_team_instruction's sections in its order: the task, the
        submission block, the role's block, and the git block when the run
        has a shared remote. A team of one gets the first two alone, as
        CooperBench falls back to the solo prompt."""
        sections = [task_text(run, agent, specs), SUBMISSION_BLOCK]
        partners = run.peers(agent)
        if not partners:
            return sections
        sections.append(self.role_block(run, agent))
        if run.options.get("git_remote"):
            sections.append(git_block(agent, partners))
        return sections

    def role_block(self, run, agent):
        note = ""
        if tool_surface(run.options) == "mcp":
            note = mcp_sentence(self.tools_for(run, agent).values())
        usage = TEAM_LIST_USAGE + (f"\n\n{note}" if note else "")
        if run.role_of(agent) == "lead":
            return lead_block(agent, run.peers(agent), usage)
        return member_block(agent, lead_of(run), usage)

    def metrics(self, run):
        """compute_metrics' six, which are all CooperBench's result.json
        holds under metrics, beside coordbench's counts, and the two
        facts CooperBench's runner writes beside them: the lead and the
        team's switches."""
        tasks, log = board_of(run).snapshot()
        return {**super().metrics(run), **compute_metrics(log, tasks), **kit.message_metrics(run),
                "tool_calls": tool_call_counts(run), "lead_agent": lead_of(run),
                "team_features": dict(TEAM_FEATURES)}

    def artifacts(self, run):
        """runner/team.py's two files, taken when the run closes: the
        task-log as it stands (task_log.json) and the final list in id
        order (tasks.json), each in CooperBench's shape."""
        tasks, log = board_of(run).snapshot()
        return {"task_log.json": log, "tasks.json": tasks}
