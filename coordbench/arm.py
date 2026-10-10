"""The plug-in contract: what a coordination arm is, and the run it acts on.

A coordination arm is one way of wiring agents together: solo, messages
only, a shared git remote, a team with a lead, the claim protocol of this
repository, or anything new. It is a directory that is at once a Claude
Code plugin and a coordbench plug-in:

    my-arm/
      .claude-plugin/plugin.json   the plugin's name and description
      .mcp.json                    the hub's MCP endpoint, so any Claude
                                   Code that loads the plugin gets the tools
      skills/<name>/SKILL.md       the protocol, in words, for the agents
      arm.json                     coordbench's part: the entry point and
                                   what the arm needs from the runner
      arm.py                       a subclass of Arm, below

The hub (hub.py) loads the arm, creates a Run per task pair, and serves the
arm's tools to every agent over MCP, over a stdio bridge for agents that
only speak stdio, and as shell commands. The runner (run.py) gives each
agent the arm's prompt, provisions what the arm needs (a shared git remote,
a shared scratchpad), starts the agents with a driver, and scores what
they leave with CooperBench's own evaluation. An arm therefore says only
how agents coordinate; tasks, agents and scoring stay the same across arms,
which is what makes two arms comparable.

Everything here is the Python standard library, plus trace.py beside it,
which is standard library too, so the hub runs anywhere Python 3.11 does.
"""

import importlib.util
import json
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import trace

SUBMISSION_BLOCK = """## Submission protocol

When you are done editing the codebase, write your final unified diff to
`/workspace/repo/patch.txt` BEFORE exiting.  The bench evaluator reads
that file; nothing else is inspected.  A typical pattern:

```bash
cd /workspace/repo
git diff > patch.txt
cat patch.txt   # sanity-check that the diff is what you intend to submit
```

Constraints on `patch.txt`:

- Must be a unified diff (`git diff` output is fine).
- Should contain ONLY source files you intentionally modified to implement
  the feature.  Exclude reproduction scripts, scratch tests, or
  helper files you wrote during development.
- Do not include changes to test files unless the task explicitly asks
  you to modify tests.

You are free to read files, run shell commands, and run tests as needed."""
# The block above is CooperBench's own (src/cooperbench/agents/_coop/
# prompt.py at 63b9d44, MIT licence), kept word for word so every arm asks
# for its result the same way the authors' runs did.


class ToolError(Exception):
    """A tool refused its call; the agent sees the message as an error result.
    exit_code is what the tool's shell command exits with, so a port of a
    command whose callers branch on its code (CooperBench's coop-task-claim
    exits 2 when someone else holds the task) can keep the code."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# What the runner provides for each need, as the run options that say
# where it is; the hub refuses a run whose arm needs something the options
# do not provide, rather than let a prompt promise what is not there.
NEED_OPTIONS = {"git_remote": ("git_remote",), "shared_volume": ("shared_dir",)}


def unmet_needs(arm, options):
    options = options or {}
    return [need for need in arm.needs
            if not all(options.get(name) for name in NEED_OPTIONS.get(need, ()))]


# Fields of a run's event that stay in the run's own record and off the
# trace stream: the text the agents wrote to each other. The record is
# the benchmark's data (conversation.py derives CooperBench's files from
# it); the stream is logging, which the hub's --debug file and any
# deployment's sink receive, and carries no message text.
PRIVATE_FIELDS = frozenset({"content"})


def public_fields(data):
    return {key: value for key, value in data.items() if key not in PRIVATE_FIELDS}


# How an agent reaches the arm's tools, the run option "tool_surface".
# "mcp", the default, is coordbench's own: the arm loaded as a Claude Code
# plugin, so its tools arrive over MCP and its skill rides along, beside
# the shell commands. "bash" reproduces CooperBench's claude_code agent at
# 63b9d44: no plugin, so no skill and no coordbench MCP server, the prompt
# without the sentence naming the MCP tools, and the coop-* commands in
# Bash, plus whatever MCP server CooperBench itself registered (team
# mode's cooperbench-team, which serves wait_for_message alone).
TOOL_SURFACES = ("mcp", "bash")
DEFAULT_TOOL_SURFACE = "mcp"


def tool_surface(options):
    """The run's tool surface, refused when it names none of TOOL_SURFACES."""
    surface = (options or {}).get("tool_surface") or DEFAULT_TOOL_SURFACE
    if surface not in TOOL_SURFACES:
        raise ValueError(f"tool_surface must be one of {', '.join(TOOL_SURFACES)}, not {surface!r}")
    return surface


def hub_mcp_server():
    """The hub as one MCP server's config, the entry every built-in arm's
    .mcp.json holds: Claude Code expands both variables from the agent's
    environment, and the timeout outlasts the longest wait an arm allows."""
    return {"type": "http", "url": "${COORD_MCP_URL}", "timeout": 330000,
            "headers": {"Authorization": "Bearer ${COORD_AGENT_TOKEN:-}"}}


@dataclass(frozen=True)
class AgentSpec:
    """One agent of a run: its id, its role in the arm, and the features
    (by number) it is asked to implement."""

    id: str
    role: str
    features: tuple


@dataclass
class Call:
    """One tool call: the run, the calling agent and its arguments, and
    what a tool that blocks needs to know about its caller.

    `cancelled` is set, with the run's lock woken, when the client cancels
    the request (MCP's notifications/cancelled); a blocking tool reads it
    in its wait predicate and, once it is set, takes nothing and returns,
    since the answer would reach nobody. on_lost(undo) registers a
    callable the hub runs, through lost(), when the reply to this call
    reaches nobody: the connection dropped before it was written, or the
    call was cancelled after it took something; a handler that took
    something from shared state puts it back there for the next call.

    `blocked_s` is the time the handler spent waiting on the run's lock
    for another agent (a long poll for a message, a request's wait for its
    response), which the handler books with waited(seconds) around each
    wait. The hub reports it beside the call's wall time in the
    "tool_done" event (Run), so a reader can tell a slow hub from an
    agent that chose to wait.

    `tool_seq` is the seq of the "tool" event that opened this call, set
    by the hub before the handler runs (None for a handler called
    directly), so an event the handler emits can name the call that
    produced it: a "message" event's send_id is its send's tool_seq.
    `via` is the transport the call came by, "mcp" or "shell" (the /rpc
    path the shell commands use), recorded in the "tool" event so a
    reader can match the call to an MCP tool use or to a Bash command."""

    run: "Run"
    agent: str
    arguments: dict
    cancelled: threading.Event = field(default_factory=threading.Event)
    undos: list = field(default_factory=list)
    blocked_s: float = 0.0
    tool_seq: int | None = None
    via: str = "mcp"

    def arg(self, name, default=None, required=False):
        value = self.arguments.get(name, default)
        if required and value in (None, ""):
            raise ToolError(f"{name} is required")
        return value

    def waited(self, seconds):
        """Book time the handler spent waiting for another agent."""
        self.blocked_s += seconds

    def on_lost(self, undo):
        self.undos.append(undo)

    def lost(self):
        """Put back what this call took: every undo registered, newest
        first, under the run's lock, which is then woken so a tool waiting
        for what went back sees it. Each undo runs once: a call cancelled
        after its take, whose reply then also fails to be written, puts
        back what it took one time, not two. An undo that fails is a bug
        in the arm; it costs the log a traceback, not the other undos."""
        with self.run.lock:
            undos, self.undos = self.undos, []
            for undo in reversed(undos):
                try:
                    undo()
                except Exception:
                    traceback.print_exc(file=sys.stderr)
            self.run.lock.notify_all()


@dataclass
class Tool:
    """A coordination tool. The handler returns text (or a dict, sent as
    JSON text) or raises ToolError. `argv` maps a shell command's
    positional words to arguments, one word per name, so `alias` can stand
    in for CooperBench's commands (coop-send <to> <text>); a last name
    ending in "..." takes the rest of the line instead, a form cli.py keeps
    for an arm that wants it, though none shipped here does, since
    CooperBench's commands read one word per positional. `flags` maps a
    shell flag to the argument it sets (--note to "note"); a flag whose
    argument is a boolean in the schema is a switch, any other takes the
    next word."""

    name: str
    description: str
    handler: Callable[[Call], object]
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    roles: tuple = ()
    alias: str = ""
    argv: tuple = ()
    flags: dict = field(default_factory=dict)

    def describe(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class Run:
    """The shared state of one task pair under one arm. The hub serves it
    from many threads at once, so every change goes through `lock`, a
    condition that blocking tools wait on and every change wakes.

    Every event (emit) carries "seq", "t" (seconds since the run started),
    "kind" and "agent", plus what its kind adds. Two kinds frame every
    tool call, on the MCP path and the /rpc path alike, so a reader of
    events.jsonl can tell what the hub spent from what the agent chose:

    - {"kind": "tool", "tool": name, "via": "mcp" or "shell"} when the
      call starts, before its handler runs; its seq is the call's
      tool_seq, which the events the handler emits refer to;
    - {"kind": "tool_done", "tool": name, "duration_ms", "blocked_ms",
      "is_error", "cancelled"} when the handler has returned. duration_ms
      is the handler's wall time; blocked_ms is the part of it the tool
      spent waiting on this lock for another agent (Call.waited), so that
      duration_ms minus blocked_ms is the hub's own cost; is_error is true
      for a ToolError or a crash in the handler; cancelled is whether the
      client had cancelled the call by then.

    A call refused before its handler runs (an unknown tool, an argument
    the tool does not take) leaves no event: nothing ran.

    Each event is kept twice: in `events` (a trace.Memory) and, with an
    events_path, in events.jsonl (a trace.JsonLines), in the shape above;
    and it goes out on trace.py's stream, the one the hub, the runner and
    the arms share, as the same record under run=<this run's id> (so the
    field names seq, t, kind, agent and run are this class's, and no kind
    adds one of them), with trace's own "ts" beside it, for whoever
    listens there: the hub's --debug log, a runner's sink. The one
    difference is the text agents wrote to each other: a field named in
    PRIVATE_FIELDS ("content", a message's or a request's text) is kept
    in the run's own record, which is the data a benchmark reads back,
    and left off the stream, which is logging, so the stream carries no
    message text at any level. An arm adds an
    event in one line, `run.emit("claim", agent, issue=7)`, and a timing
    in one, `with trace.span("rebase", run=run.id, agent=agent): ...`,
    whose two events reach the stream's listeners beside the run's but
    are not the run's own. In debug (trace.level) wait_until records a
    "lock_wait" per wait there as well. The run's own record does not
    depend on the stream, so it is whole at every trace level and
    whoever listens."""

    def __init__(self, run_id, arm, agents, options=None, events_path=None, specs=None):
        self.id = run_id
        self.arm = arm
        self.agents = {a.id: a for a in agents}
        self.options = dict(options or {})
        self.specs = dict(specs or {})
        self.state = {}
        self.lock = threading.Condition()
        self.events = trace.Memory()
        self.events_path = Path(events_path) if events_path else None
        self.log_file = trace.JsonLines(self.events_path) if self.events_path else None
        self.log_complained = False
        self.started = time.time()
        self.closed = False

    def emit(self, kind, agent=None, **data):
        """Record one coordination event; the metrics come from these. The
        event goes into `events` and the log first, then out on the trace
        stream under this run's id, less its private fields. The waiters
        are woken whatever happens after the event is in, so a failure
        here never leaves a blocking tool asleep."""
        with self.lock:
            try:
                seq, t = len(self.events), round(time.time() - self.started, 3)
                event = {"seq": seq, "t": t, "kind": kind, "agent": agent, **data}
                self.events.append(event)
                self.log(event)
                trace.event(kind, run=self.id, seq=seq, t=t, agent=agent, **public_fields(data))
            finally:
                self.lock.notify_all()
        return event

    def next_seq(self):
        """The seq the next event will get. Read it under run.lock and emit
        in the same hold, as kit.deliver does to give a message event its
        own seq as message_id: no other emit can come between the two."""
        with self.lock:
            return len(self.events)

    def emit_started(self):
        """The run's first event, seq 0 when the hub emits it before the
        arm's setup: the wall clock the run's t counts from (epoch, and
        iso in UTC), the arm, the agents with their roles and features,
        and the tool surface. A reader of events.jsonl alone can then put
        every event at its absolute time (epoch + t) and every agent at
        its feature."""
        return self.emit("run_started", None, epoch=self.started,
                         iso=datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
                         arm=self.arm.config.get("name") or self.arm.name,
                         agents=[{"id": spec.id, "role": spec.role, "features": list(spec.features)}
                                 for spec in self.agents.values()],
                         tool_surface=tool_surface(self.options))

    def log(self, event):
        """Append the event to events.jsonl, best effort. The file is a side
        record (the runner reads the events from memory), so a disk that
        refuses the write, or a log directory not there when the run's
        first event comes, costs one complaint on stderr per run and never
        the tool call that emitted; later writes are still tried, so a
        disk that fills and empties again loses only the events in
        between. The file is opened at the first event and kept open
        until close_log()."""
        if self.log_file is None:
            return
        try:
            self.log_file(event)
        except OSError as problem:
            if not self.log_complained:
                self.log_complained = True
                print(f"coordbench run {self.id}: cannot write {self.events_path} ({problem}); "
                      "the events stay in memory", file=sys.stderr, flush=True)

    def close_log(self):
        """Close events.jsonl, once the hub forgets the run, so a long-lived
        hub holds no file for a run it no longer hosts; an event emitted
        after this opens it again, so none is lost."""
        if self.log_file is not None:
            self.log_file.close()

    def wait_until(self, ready, timeout, agent=None):
        """Block until ready() holds or the timeout (seconds) passes;
        ready() runs under the lock. In debug a "lock_wait" event records
        the wait: the run, the agent when the caller names one, waited_ms,
        and woke, true when ready() held and false when the timeout
        passed."""
        debugging = trace.level == "debug"
        began = trace.now_ms() if debugging else 0.0
        with self.lock:
            woke = self.lock.wait_for(ready, timeout=max(0.0, timeout))
        if debugging:
            trace.debug("lock_wait", run=self.id, agent=agent,
                        waited_ms=round(trace.now_ms() - began, 3), woke=bool(woke))
        return woke

    def peers(self, agent):
        return [a for a in self.agents if a != agent]

    def role_of(self, agent):
        return self.agents[agent].role


class Arm:
    """Subclass this to make an arm. Override what the arm changes:

    - layout(features): the agents and their roles; one per feature by
      default, agent1, agent2 and so on in the order of the features,
      every agent a "peer";
    - setup(run): seed the run's state, such as a task list;
    - tools(run, agent): the tools this agent gets;
    - prompt(run, agent, specs): what the agent is told, given each
      feature's spec by number;
    - instructions(run, agent): text sent as MCP server instructions, which
      Claude Code puts into the system prompt; empty by default, because
      CooperBench's agents got none and a fair comparison keeps it so;
    - metrics(run): numbers worth keeping, from run.events and run.state;
    - artifacts(run): files worth keeping, {file name: a JSON value or
      text}, taken once when the run closes, before teardown, and handed
      to the runner with the metrics (the team arm's task_log.json and
      tasks.json); none by default;
    - teardown(run): free what setup or the tools took (a scratch clone, a
      thread), called once when the run closes, outside run.lock.

    run.specs holds each feature's spec by number from setup() on.

    `needs` tells the runner what to provision: "git_remote" (a bare
    repository every agent can push to and fetch from, as the remote
    `team`) and "shared_volume" (one directory mounted in every agent's
    container at /workspace/shared).

    `tool_surfaces` are the run option tool_surface's values the arm
    offers (TOOL_SURFACES says what each means); an arm whose prompt
    names its MCP tools offers "mcp" alone, and the hub refuses a run
    that asks for another. On the "bash" surface the MCP endpoint serves
    only the tools named in `bash_mcp_tools`, and claude_surface names
    the hub to the driver as `bash_mcp_server`, which is how
    CooperBench's own agents met those tools.
    """

    name = "arm"
    description = ""
    needs = ()
    tool_surfaces = (DEFAULT_TOOL_SURFACE,)
    bash_mcp_server = ""
    bash_mcp_tools = ()

    def __init__(self, root=None, config=None):
        self.root = Path(root) if root else None
        self.config = dict(config or {})

    def layout(self, features):
        # Numbered by position, as CooperBench numbers its agents; the
        # patch files are named by feature, which the runner takes care of.
        return [AgentSpec(f"agent{i}", "peer", (n,)) for i, n in enumerate(features, 1)]

    def setup(self, run):
        pass

    def tools(self, run, agent):
        return []

    def prompt(self, run, agent, specs):
        mine = run.agents[agent].features
        parts = [specs[n].strip() for n in mine]
        return "\n\n".join(parts + [SUBMISSION_BLOCK])

    def instructions(self, run, agent):
        return ""

    def teardown(self, run):
        pass

    def artifacts(self, run):
        return {}

    def metrics(self, run):
        kinds = {}
        for event in run.events:
            kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
        return {"events": kinds, "tool_surface": run.options.get("tool_surface") or DEFAULT_TOOL_SURFACE}

    def tools_for(self, run, agent):
        """The tools of this agent's role, by name: every tool it has, which
        the shell commands (/rpc) always reach."""
        role = run.role_of(agent)
        return {t.name: t for t in self.tools(run, agent) if not t.roles or role in t.roles}

    def mcp_tools_for(self, run, agent):
        """The tools the MCP endpoint serves this agent: all of them on the
        "mcp" surface, and on "bash" only those CooperBench served over
        MCP itself (bash_mcp_tools), so an MCP client of a bash-surface
        run sees what CooperBench's agent saw and no more."""
        tools = self.tools_for(run, agent)
        if tool_surface(run.options) == DEFAULT_TOOL_SURFACE:
            return tools
        return {name: tool for name, tool in tools.items() if name in self.bash_mcp_tools}

    def claude_surface(self, options):
        """What a driver hands Claude Code for this arm on the run's tool
        surface: {"tool_surface", "plugin", "mcp_servers"}. plugin says
        whether to load the arm's directory with --plugin-dir; mcp_servers
        are the servers to hand over with --mcp-config, arm.json's own and,
        on the bash surface, the hub under CooperBench's server name when
        the arm has one (bash_mcp_server)."""
        surface = tool_surface(options)
        servers = dict(self.config.get("mcp_servers") or {})
        if surface == DEFAULT_TOOL_SURFACE:
            return {"tool_surface": surface, "plugin": True, "mcp_servers": servers}
        if self.bash_mcp_server and self.bash_mcp_tools:
            servers[self.bash_mcp_server] = hub_mcp_server()
        return {"tool_surface": surface, "plugin": False, "mcp_servers": servers}


def load_arm_class(path):
    """The Arm subclass and the config an arm directory holds, from its
    arm.json: {"entry": "arm.py:ClassName", ...}; the rest of arm.json is
    the config. Every call executes arm.py into a module of its own, so a
    caller that makes many arms from one directory (the hub) keeps what
    this returns and instantiates from it."""
    root = Path(path).resolve()
    config = json.loads((root / "arm.json").read_text(encoding="utf-8"))
    module_file, _, class_name = config["entry"].partition(":")
    spec = importlib.util.spec_from_file_location(f"coordbench_arm_{root.name.replace('-', '_')}",
                                                  root / module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, class_name)
    if not (isinstance(cls, type) and issubclass(cls, Arm)):
        raise TypeError(f"{class_name} in {root} is not an Arm")
    return cls, config


def load_arm(path):
    """Load the arm in a directory: its class, instantiated with the
    directory and its config."""
    root = Path(path).resolve()
    cls, config = load_arm_class(root)
    return cls(root, config)


def builtin_arms_dir():
    return Path(__file__).resolve().parent / "arms"


def find_arm(name_or_path):
    """An arm by the name of a built-in one, or by its directory."""
    candidate = Path(name_or_path)
    if (candidate / "arm.json").exists():
        return load_arm(candidate)
    builtin = builtin_arms_dir() / name_or_path
    if (builtin / "arm.json").exists():
        return load_arm(builtin)
    raise FileNotFoundError(f"no arm named {name_or_path!r} and no arm.json under it")
