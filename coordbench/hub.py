"""The hub: one process that hosts every run and serves each agent its arm's tools.

The runner asks the hub for a run per task pair (POST /api/runs); the hub
loads the arm, lays out the agents, seeds the run and writes each agent's
prompt. Agents then reach the same tools three ways, all answered here:

- MCP over Streamable HTTP (POST /mcp/{run}/{agent}), the way Claude Code
  and any other MCP client loads a plugin's server from .mcp.json;
- a stdio bridge, for clients that only speak stdio, which relays their
  messages to the hub;
- shell commands, which read the tool list and call tools over the plain
  JSON endpoint /rpc/{run}/{agent}.

Every request runs in a thread of its own, so a blocking tool such as
wait_for_message holds up only the agent that called it. The hub keeps
everything in memory; with a log directory it also appends each run's
events to <log_dir>/<run>/events.jsonl as they happen, best effort, so a
crashed hub still leaves the record behind. A run stays hosted, with its
events, prompts and sessions, until the runner releases it with
DELETE /api/runs/{run} once it has fetched the metrics and the events;
that closes the run first if it is still open, then forgets it, so a hub
that serves many runs does not grow with every one it ever hosted.

Closing a run (POST /api/runs/{run}/close) answers {"metrics",
"artifacts"}: the arm's metrics, and the files the run leaves as {file
name: a JSON value or text}, taken once, by the close that closes it,
before the arm's teardown frees what they are read from: git_log.txt
for a run with a shared remote (the options git_remote_dir or
git_remote_host), and what the arm adds (the team arm's task_log.json
and tasks.json). The run's first event is run_started, before anything
the arm's setup records.

On /mcp a body the hub cannot read as a request (not JSON, or nested too
deep to parse, or a lone message with no id to answer under: no JSON-RPC
object, an object with no method that is no response either, an id that
is not a string or an integer, an empty batch) is answered 400 with a
JSON-RPC error that has no id, as the Streamable HTTP transport asks, and
so is an MCP-Protocol-Version the hub does not speak on anything but an
initialize; what the hub can read is answered 200 message by message,
each request of a batch on its own, and a session id is issued only to
an initialize that was answered with initialize's own result under that
very id (one id, by type as well as value: an initialize with id true,
refused for its shape, gets no session from a ping with id 1 answered
beside it). An error the hub itself causes while answering is -32603 for
the request it failed on, with the traceback in the hub's log; a client
that drops its connection leaves no trace there, since that is the
client's doing.

A client may give up on a tool that blocks, and the hub then makes sure
nothing is taken for nobody: a cancelled call takes nothing, and one
cancelled after it took something puts it back. A notifications/cancelled,
which Streamable HTTP carries as a second POST on the same
Mcp-Session-Id, reaches the tools/call it names among the calls in
flight on that session (kept per session, by request id, on the hosted
run): mcp.handle sets the call's cancelled event under the run's lock
and wakes the run, and the blocking tool returns without taking what it
waited for. A call that had already taken something when the cancel
landed (the message was popped, the reply not yet on its way) sees the
cancel when its handler returns, since it leaves the calls in flight
under the same lock, and puts back what it took (Call.lost) before its
answer is written. The answer is still written, the no-message marker or
the message that went back; a client that still listens discards a
response to a cancelled request, as the specification tells it to, and
one that has gone makes the write fail. When writing the answer to a
request that ran tool calls (on /mcp or /rpc) fails because the
connection dropped, the hub runs the undo each of its calls registered
(Call.on_lost), newest first, under the run's lock, and wakes the run: a
message or a response taken for a reply that reached nobody goes back
where the next call finds it, and once only, however the reply was lost.

Timings and events, the hub's own and every run's, go through trace.py,
one stream with one shape, so a reader adds a measurement anywhere in one
line: `trace.event("kind", run=run.id, agent=agent, ...)` records an
event, `with trace.span("kind", run=run.id): ...` records two, the start
and `kind_done` with duration_ms and ok; a Run's emit puts the same
record on that stream under the run's id (arm.py, Run), beside keeping it
as the run's own events and events.jsonl. In prod that is all that is
kept: the hub registers no listener of its own, so an event outside a
run costs one check. With --debug (or COORD_TRACE=debug in the
environment) the hub also records, to <log_dir>/hub-trace.jsonl with
--log-dir and to stderr without, every run's events with the run's id
beside its own:
"http_request" per request (method, path, status, request_bytes,
response_bytes, duration_ms, thread), "lock_wait" per Run.wait_until
(agent, waited_ms, woke), "tool_arguments" per tool call (the arguments'
size in bytes, never their content), "session_opened" per MCP session
and "cancel" per notifications/cancelled; `trace.debug(...)` and
`trace.debug_span(...)` add more at one comparison's cost in prod. Debug
is never on by default, and the hub says on stderr once when it is.
COORD_TRACE=off silences the stream; a run's own events and its
events.jsonl are kept at every level, since they are the hub's data,
not its logging.

Command line:

    python3 hub.py --host 0.0.0.0 --port 8765 [--log-dir DIR] [--public-url URL]
        [--arm-dir DIR ...] [--debug]

prints the base URL agents should use as its first line, then serves.

Loading an arm runs its arm.py inside the hub, and agents can reach the
hub, so the hub loads only the built-in arms and arms under the
directories named with --arm-dir, never a directory a request names
elsewhere (an agent could write one into a shared volume). When the
environment holds COORD_ADMIN_TOKEN, the management API (/api/runs and
below) also wants it as a bearer token, so only the runner, which set it,
can create or close runs; agents never see it.

A run can want a credential from each agent as well, since the hub binds
to every address of its host when containers must reach it, and an agent
told apart by its URL alone could speak as another. With "agent_tokens":
true in POST /api/runs the hub makes a token per agent
(secrets.token_urlsafe) and answers it as "token" in that agent's entry
of the create answer, the one with the prompts, which goes to the runner
alone; GET /api/runs/{run} and everything an agent can reach never carry
one. From then on that agent's /mcp (POST) and /rpc (GET and POST) want
"Authorization: Bearer <its token>" and answer 401 with {"error": ...} to
anything else, after the Origin check and before any body is read as
MCP, the comparison in constant time. A run created without agent_tokens
stays open, for harnesses that tell their agents apart some other way.
"""

import argparse
import json
import re
import secrets
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import os

import kit
import mcp
import trace
from arm import Arm, Run, builtin_arms_dir, load_arm_class, tool_surface, unmet_needs

MAX_BODY = 1024 * 1024
# A task pair is two features; the room above that is for arms that lay
# out more agents, not for a run whose prompts and inboxes grow with the
# square of the count.
MAX_FEATURES = 16
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
# The MCP specification asks servers to check Origin against DNS
# rebinding: a web page the operator happens to open must not be able to
# drive the agents' tools. Agents in containers reach the host as
# host.docker.internal, so that name is as local as localhost here.
LOCAL_ORIGIN = re.compile(r"https?://(localhost|127\.0\.0\.1|host\.docker\.internal)(:\d{1,5})?",
                          re.IGNORECASE)
JSON_MEDIA = ("application/json", "application/*", "*/*")
FAILED = mcp.FAILED


class HubError(Exception):
    """A request the hub refuses, with the HTTP status that says why."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Hosted:
    """A run as the hub keeps it: the run itself, the prompt each agent was
    given, the bearer token each agent's /mcp and /rpc want (empty for a
    run created without agent_tokens, which wants none), the MCP session
    ids issued to each agent, the tools/calls under way by session id
    (None for a client that opened none) and then by request id, which is
    where a notifications/cancelled on a session finds the call it names,
    and the artifacts the close that closed the run took (None before)."""

    run: Run
    prompts: dict
    tokens: dict = field(default_factory=dict)
    sessions: dict = field(default_factory=dict)
    calls: dict = field(default_factory=dict)
    artifacts: dict | None = None


class Hub:
    """Every run this process hosts, by id, behind one lock.

    The lock guards only the registry; a run's own state has its own lock
    (Run.lock), so a tool that blocks never stops other runs or agents.
    """

    def __init__(self, log_dir=None, public_url=None, arm_dirs=(), admin_token=None):
        self.log_dir = Path(log_dir) if log_dir else None
        self.public_url = public_url.rstrip("/") if public_url else None
        self.arm_dirs = [Path(d).resolve() for d in arm_dirs]
        self.admin_token = admin_token or None
        self.bound_url = None
        self.lock = threading.Lock()
        self.runs = {}
        # The (class, config) of each arm directory loaded so far, so that
        # arm.py runs once per directory, not once per run.
        self.arm_classes = {}

    @property
    def base_url(self):
        # Before a server is bound there is no address to give, so the
        # urls stay relative rather than name a port nobody listens on.
        return self.public_url or self.bound_url or ""

    def url(self, kind, run_id, agent):
        return f"{self.base_url}/{kind}/{quote(run_id, safe='')}/{quote(agent, safe='')}"

    def create_run(self, arm, features, specs, options=None, run_id=None, agent_tokens=False):
        """Load the arm, lay out its agents and answer as POST /api/runs
        does. With agent_tokens each agent gets a bearer token of its own,
        which its /mcp and /rpc want from then on and which only this
        answer carries, in the agent's entry as "token"; without, the run
        is open to anyone who knows its URLs."""
        features = parse_features(features)
        specs = parse_specs(specs, features)
        if options is not None and not isinstance(options, dict):
            raise HubError(400, "options must be an object")
        if not isinstance(agent_tokens, bool):
            raise HubError(400, "agent_tokens must be true or false")
        arm = self.resolve_arm(arm)
        missing = unmet_needs(arm, options)
        if missing:
            raise HubError(400, f"the {arm_name(arm)} arm needs {', '.join(missing)}, "
                                "which the run's options do not provide")
        check_tool_surface(arm, options)
        run_id = self.reserve(run_id)
        try:
            hosted = self.build(arm, run_id, features, specs, options)
        except BaseException:
            with self.lock:
                del self.runs[run_id]
            raise
        if agent_tokens:
            hosted.tokens = {agent: secrets.token_urlsafe(32) for agent in hosted.run.agents}
        with self.lock:
            self.runs[run_id] = hosted
        return self.describe(hosted, with_prompts=True)

    def reserve(self, run_id):
        """Hold the id while the run is built, so two creates of the same id
        cannot both pass the check; a held id reads as unknown meanwhile."""
        if run_id not in (None, "") and not (isinstance(run_id, str) and RUN_ID.fullmatch(run_id)):
            raise HubError(400, "run_id must be letters, digits, '.', '_' or '-', at most 128")
        with self.lock:
            if run_id in (None, ""):
                run_id = fresh_run_id(self.runs)
            elif run_id in self.runs:
                raise HubError(409, f"a run named {run_id!r} already exists")
            self.runs[run_id] = None
        return run_id

    def build(self, arm, run_id, features, specs, options):
        """The run, seeded, with each agent's prompt. Its first event, seq
        0, is run_started, before anything the arm's setup records. A run
        whose arm fails to set it up or prompt for it has its log closed
        before the failure goes up, since nobody will release it."""
        agents = arm.layout(features)
        run = Run(run_id, arm, agents, options, self.events_path(run_id), specs)
        try:
            run.emit_started()
            arm.setup(run)
            prompts = {agent: arm.prompt(run, agent, specs) for agent in run.agents}
        except BaseException:
            run.close_log()
            raise
        return Hosted(run, prompts)

    def events_path(self, run_id):
        """A fresh events.jsonl for the run. An id reused after the hub
        restarts, or after its run was released, names a new run, since
        the old one is gone, so its log starts empty rather than mixing
        two runs."""
        if not self.log_dir:
            return None
        run_dir = self.log_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "events.jsonl"
        path.write_text("", encoding="utf-8")
        return path

    def hosted(self, run_id, agent=None):
        with self.lock:
            hosted = self.runs.get(run_id)
        if hosted is None:
            raise HubError(404, f"no run named {run_id!r}")
        if agent is not None and agent not in hosted.run.agents:
            raise HubError(404, f"no agent named {agent!r} in run {run_id!r}")
        return hosted

    def get_run(self, run_id):
        return self.describe(self.hosted(run_id), with_prompts=False)

    def close_run(self, run_id):
        return self.close(self.hosted(run_id))

    def close_answer(self, run_id):
        """POST /api/runs/{run}/close: the metrics and the artifacts, the
        files the arm and the hub took when the run closed."""
        hosted = self.hosted(run_id)
        metrics = self.close(hosted)
        return {"metrics": metrics, "artifacts": hosted.artifacts or {}}

    def close(self, hosted):
        """Mark the run closed and give its metrics. Tools keep answering,
        since an agent may still be finishing its last call; the runner
        decides what a closed run's late events mean. The close that
        closes the run also takes its artifacts, before the arm's
        teardown, while what they are read from is still there."""
        run = hosted.run
        with run.lock:
            first_close = not run.closed
            run.closed = True
            run.lock.notify_all()
            metrics = run.arm.metrics(run)
        if first_close:
            # Outside the lock: an arm freeing a resource may need to wait
            # on its own locks, which a tool holding run.lock could hold,
            # and the git log takes seconds no tool should wait for.
            hosted.artifacts = collect_artifacts(run)
            try:
                run.arm.teardown(run)
            except Exception:
                complain(f"the {arm_name(run.arm)} arm's teardown of run {run.id} failed")
        return metrics

    def release_run(self, run_id):
        """Forget the run, closing it first if it is still open, so the
        teardown runs once however the runner ends; the runner releases a
        run once it holds the metrics and the events, and a hub that
        hosts many runs keeps none of the finished ones. Only the run that
        was closed here is forgotten: a second release that overlaps the
        first answers while the first is still in the teardown, and a run
        created under the same id meanwhile is another run, which stays
        hosted, open, until its own release."""
        hosted = self.hosted(run_id)
        self.close(hosted)
        with self.lock:
            if self.runs.get(run_id) is hosted:
                del self.runs[run_id]
        hosted.run.close_log()
        return {"run_id": run_id, "released": True}

    def events(self, run_id):
        run = self.hosted(run_id).run
        with run.lock:
            return list(run.events)

    def run_count(self):
        with self.lock:
            return sum(1 for hosted in self.runs.values() if hosted is not None)

    def describe(self, hosted, with_prompts):
        """The run as the API answers it. With the prompts it is the create
        answer, which the runner alone receives, so each agent's token
        goes with its prompt; without them it is GET /api/runs/{run},
        which carries neither."""
        run = hosted.run
        agents = [self.describe_agent(run, agent, hosted.prompts[agent] if with_prompts else None,
                                      hosted.tokens.get(agent) if with_prompts else None)
                  for agent in run.agents]
        answer = {"run_id": run.id, "arm": arm_name(run.arm), "agents": agents}
        if not with_prompts:
            with run.lock:
                answer["closed"] = run.closed
                answer["metrics"] = run.arm.metrics(run)
        return answer

    def describe_agent(self, run, agent, prompt=None, token=None):
        spec = run.agents[agent]
        entry = {"id": agent, "role": spec.role, "features": list(spec.features),
                 "mcp_url": self.url("mcp", run.id, agent), "rpc_url": self.url("rpc", run.id, agent)}
        if prompt is not None:
            entry["prompt"] = prompt
        if token is not None:
            entry["token"] = token
        return entry

    def check_agent(self, hosted, agent, authorization):
        """For a run made with agent_tokens, the request must carry this
        agent's own token, compared in constant time for two strings of
        one length, so the time taken tells a guesser nothing about where
        a wrong token of the right length diverges; a run made without
        wants none."""
        token = hosted.tokens.get(agent)
        if token is None:
            return
        given = (authorization or "").encode("utf-8")
        if not secrets.compare_digest(given, f"Bearer {token}".encode("utf-8")):
            raise HubError(401, f"run {hosted.run.id} wants {agent}'s bearer token in Authorization")

    def open_session(self, hosted, agent):
        session = uuid.uuid4().hex
        with self.lock:
            hosted.sessions.setdefault(agent, set()).add(session)
        trace.debug("session_opened", run=hosted.run.id, agent=agent, session=session)
        return session

    def knows_session(self, hosted, agent, session):
        with self.lock:
            return session in hosted.sessions.get(agent, ())

    def in_flight(self, hosted, session):
        """The tools/calls under way on one session, by request id: the
        dict mcp.handle enters a call into before its handler runs and
        removes it from after. The threads of one session share it
        without a lock, since each step on a dict is one the interpreter
        keeps whole; only its making is under the registry's lock."""
        with self.lock:
            return hosted.calls.setdefault(session, {})

    def describe_tools(self, run, agent):
        return [{**tool.describe(), "alias": tool.alias, "argv": list(tool.argv), "flags": dict(tool.flags)}
                for tool in run.arm.tools_for(run, agent).values()]

    def call(self, run, agent, name, arguments, sink=None):
        """One tool call outside MCP, for /rpc: the same checks and events
        as tools/call, answered as {"text", "isError"}; arguments left out
        mean none, and arguments that are not an object are refused as
        tools/call refuses them. The Call goes onto `sink`, a list, before
        its handler runs, as mcp.handle's does."""
        tools = run.arm.tools_for(run, agent)

        def started(call):
            call.via = "shell"
            if sink is not None:
                sink.append(call)

        result, problem = mcp.call_tool(run, agent, tools, {"name": name, "arguments": arguments}, started)
        if problem is not None:
            raise HubError(400, problem[1])
        answer = {"text": result["content"][0]["text"], "isError": result["isError"]}
        if result["isError"]:
            answer["exitCode"] = (result.get("_meta") or {}).get("coordbench/exitCode", 1)
        return answer

    def resolve_arm(self, arm):
        """An arm object as given, a built-in arm by name, or an arm under one
        of the directories the hub was started with; nothing else, since
        loading an arm runs its code in the hub."""
        if isinstance(arm, Arm):
            return arm
        if not isinstance(arm, str) or not arm:
            raise HubError(400, "arm must name a built-in arm or a directory holding arm.json")
        builtin = builtin_arms_dir() / arm
        if "/" not in arm and (builtin / "arm.json").exists():
            return self.instantiate(builtin.resolve())
        path = Path(arm).resolve()
        if not any(path == root or root in path.parents for root in self.arm_dirs):
            raise HubError(403, f"{arm!r} is neither a built-in arm nor under a directory the hub "
                                "was started with (--arm-dir)")
        if not (path / "arm.json").exists():
            raise HubError(400, f"no arm.json in {arm!r}")
        return self.instantiate(path)

    def instantiate(self, root):
        """A fresh arm object for one run from the arm directory `root`,
        whose class is loaded once and kept: running arm.py for every run
        (and, for the team arm, the arms it imports) grew a long-lived hub
        without bound. The load runs outside the lock, since it executes
        the arm's code; two first loads at once keep the first."""
        with self.lock:
            loaded = self.arm_classes.get(root)
        if loaded is None:
            loaded = load_arm_class(root)
            with self.lock:
                loaded = self.arm_classes.setdefault(root, loaded)
        cls, config = loaded
        return cls(root, config)

    def check_admin(self, authorization):
        if self.admin_token and authorization != f"Bearer {self.admin_token}":
            raise HubError(401, "the management API wants the runner's bearer token")


def arm_name(arm):
    return arm.config.get("name") or arm.name


def check_tool_surface(arm, options):
    """The run option tool_surface, refused before the run exists when it
    names no surface, or one the arm does not offer: an arm whose prompt
    names its MCP tools cannot run without them."""
    try:
        surface = tool_surface(options)
    except ValueError as problem:
        raise HubError(400, str(problem)) from None
    if surface not in arm.tool_surfaces:
        raise HubError(400, f"the {arm_name(arm)} arm offers tool_surface {', '.join(arm.tool_surfaces)}, "
                            f"not {surface}")


def collect_artifacts(run):
    """The files a run leaves for the runner, taken as it closes: the
    shared remote's history as git_log.txt for a run that has one (the
    hub's, so every arm with a git remote gets it), then whatever the arm
    adds (Arm.artifacts). The remote is read where the options say:
    git_remote_dir, the bare repository's directory on this host, when
    the runner can name it, which gives git's reflog as well, and
    otherwise git_remote_host, the URL the hub fetches from. Either
    failing costs its own files and a complaint on stderr, never the
    close."""
    artifacts = {}
    remote = run.options.get("git_remote_dir") or run.options.get("git_remote_host")
    if remote:
        try:
            artifacts["git_log.txt"] = kit.git_log(remote, run.options.get("base_ref"))
        except RuntimeError as problem:
            print(f"coordbench hub: run {run.id} keeps no git_log.txt: {problem}", file=sys.stderr, flush=True)
    try:
        artifacts.update(run.arm.artifacts(run))
    except Exception:
        complain(f"the {arm_name(run.arm)} arm's artifacts of run {run.id} failed")
    return artifacts


def parse_features(features):
    """The feature numbers of a run, refused before the arm lays anything
    out: a repeated feature would give two agents one issue (the sdlc arm
    maps it to the last of them), and a long list would grow every
    prompt and inbox with the square of its length."""
    if not isinstance(features, (list, tuple)) or not features:
        raise HubError(400, "features must be a non-empty list of feature numbers")
    if len(features) > MAX_FEATURES:
        raise HubError(400, f"at most {MAX_FEATURES} features in one run; a task pair is 2")
    try:
        numbers = [int(number) for number in features]
    except (TypeError, ValueError):
        raise HubError(400, "features must be numbers") from None
    if len(set(numbers)) != len(numbers):
        raise HubError(400, "features must not repeat; each agent gets a feature of its own")
    return numbers


def parse_specs(specs, features):
    """Each feature's spec by number. A missing spec is refused here, since
    an agent sent off with half a prompt would only fail later and quietly."""
    if not isinstance(specs, dict):
        raise HubError(400, "specs must be an object of feature number to spec text")
    try:
        parsed = {int(number): str(text) for number, text in specs.items()}
    except ValueError:
        raise HubError(400, "specs must be keyed by feature number") from None
    missing = [str(number) for number in features if number not in parsed]
    if missing:
        raise HubError(400, f"no spec for feature {', '.join(missing)}")
    return parsed


def fresh_run_id(taken):
    while True:
        run_id = "r" + secrets.token_hex(4)
        if run_id not in taken:
            return run_id


def origin_allowed(origin):
    return origin is None or bool(LOCAL_ORIGIN.fullmatch(origin.strip()))


def accepts_json(accept):
    """An absent or empty Accept takes anything; otherwise one of its media
    ranges must cover application/json, since JSON is all the hub sends."""
    if accept is None or not accept.strip():
        return True
    ranges = (item.split(";")[0].strip().lower() for item in accept.split(","))
    return any(media in JSON_MEDIA for media in ranges)


def as_list(parsed):
    return parsed if isinstance(parsed, list) else [parsed]


def initialize_ids(body):
    """The ids of the initialize requests in a body; one of them answered
    with initialize's result starts a session."""
    try:
        parsed = mcp.loads(body)
    except ValueError:
        return []
    return [m["id"] for m in as_list(parsed)
            if isinstance(m, dict) and m.get("method") == "initialize" and "id" in m]


def opens_session(body):
    """Whether the body holds an initialize request."""
    return bool(initialize_ids(body))


def opened_session(body, answer):
    """Whether an initialize in the body was answered with a result: an
    answer carrying initialize's own result (mcp.is_initialize_result)
    under an id that is one of an initialize's by type and value, so a
    ping's result with id 1 says nothing for an initialize with id true,
    and a ping's empty result nothing for an initialize that shares its
    id and was refused. One refused for its shape starts no session: a
    client holding a session id for a handshake that failed would send
    it with every request."""
    asked = initialize_ids(body)
    return any(isinstance(a, dict) and mcp.is_initialize_result(a.get("result"))
               and any(mcp.same_id(a.get("id"), request_id) for request_id in asked)
               for a in as_list(answer))


def unreadable(answer):
    """Whether a lone answer says the hub could not read the request at
    all: not JSON, or no request with an id to answer under. The
    transport wants an HTTP error status for such input, with a JSON-RPC
    error that has no id; an error that can name its request goes back
    as a normal JSON-RPC answer, which the client matches to it."""
    return isinstance(answer, dict) and "error" in answer and "id" not in answer


def internal_errors(body):
    """A JSON-RPC error for each request of a body the hub failed on, so a
    client waiting on an id hears back rather than hangs."""
    try:
        parsed = mcp.loads(body)
    except ValueError:
        return mcp.error(None, mcp.INTERNAL_ERROR, FAILED)
    answers = [mcp.error(mcp.request_id_of(m), mcp.INTERNAL_ERROR, FAILED)
               for m in as_list(parsed) if isinstance(m, dict) and "id" in m and "method" in m]
    if isinstance(parsed, list):
        return answers or None
    return answers[0] if answers else None


def complain(what):
    """Problems go to the operator on stderr, never to a client."""
    print(f"coordbench hub: {what}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)


def undo_lost(calls):
    """What the tool calls of a reply that was never written took goes
    back, newest call first, so that two waits of one batch that took two
    messages leave the inbox in its old order."""
    for call in reversed(calls):
        call.lost()


def parse_path(raw):
    """The route a request path names, and the parts of it that vary."""
    parts = [unquote(part) for part in urlsplit(raw).path.strip("/").split("/")]
    if len(parts) == 3 and parts[0] in ("mcp", "rpc"):
        return parts[0], (parts[1], parts[2])
    if parts == ["api", "health"]:
        return "health", ()
    if parts == ["api", "runs"]:
        return "runs", ()
    if len(parts) == 3 and parts[:2] == ["api", "runs"]:
        return "run", (parts[2],)
    if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] in ("events", "close"):
        return parts[3], (parts[2],)
    return None, ()


# Route, then method, then the handler method that answers it; a route
# asked with a method it lacks gets 405 with these methods in Allow.
ROUTES = {
    "mcp": {"POST": "post_mcp"},
    "rpc": {"GET": "get_rpc", "POST": "post_rpc"},
    "health": {"GET": "get_health"},
    "runs": {"POST": "post_runs"},
    "run": {"GET": "get_run", "DELETE": "delete_run"},
    "events": {"GET": "get_events"},
    "close": {"POST": "post_close"},
}


class HubHandler(BaseHTTPRequestHandler):
    """The hub's HTTP API. The body is read before anything is checked, so
    every answer leaves the connection clean for the client's next request."""

    protocol_version = "HTTP/1.1"
    server_version = "coordbench-hub/0.1"
    # An idle keep-alive connection gives its thread back after this long.
    timeout = 300

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    @property
    def hub(self):
        return self.server.hub

    def dispatch(self, method):
        self.replied, self.status_sent, self.bytes_sent, self.body = False, None, 0, b""
        began = trace.now_ms() if trace.level == "debug" else None
        try:
            self.body = self.read_body()
            self.route(method)
        except HubError as refusal:
            self.refuse(refusal)
        except ConnectionError:
            self.close_connection = True
        except Exception:
            complain(f"{method} {self.path} failed")
            if not self.replied:
                self.reply(500, {"error": FAILED})
        finally:
            if began is not None:
                self.debug_request(method, began)

    def debug_request(self, method, began):
        """One "http_request" event per request, in debug: what was asked,
        what went back (status None and no bytes when the connection
        dropped first) and how long it took, with the thread that served
        it, so a slow request is laid at its own door."""
        trace.debug("http_request", method=method, path=self.path, status=self.status_sent,
                    request_bytes=len(self.body), response_bytes=self.bytes_sent,
                    duration_ms=round(trace.now_ms() - began, 3), thread=threading.current_thread().name)

    def route(self, method):
        if not origin_allowed(self.headers.get("Origin")):
            raise HubError(403, "this Origin may not use the hub")
        name, params = parse_path(self.path)
        if name is None:
            raise HubError(404, "no such endpoint")
        methods = ROUTES[name]
        if method not in methods:
            self.reply(405, {"error": f"{method} is not allowed here"}, {"Allow": ", ".join(methods)})
            return
        getattr(self, methods[method])(*params)

    def read_body(self):
        """The request body, refused when it is too large to hold; a refused
        body is never read, so that connection closes after the answer."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self.close_connection = True
            raise HubError(411, "send a Content-Length; chunked bodies are not accepted")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            raise HubError(400, "bad Content-Length")
        if length > MAX_BODY:
            self.close_connection = True
            raise HubError(413, f"bodies over {MAX_BODY} bytes are refused")
        return self.rfile.read(length) if length else b""

    def json_body(self):
        try:
            payload = mcp.loads(self.body or b"{}")
        except ValueError:
            raise HubError(400, "the body is not JSON") from None
        if not isinstance(payload, dict):
            raise HubError(400, "the body must be a JSON object")
        return payload

    def reply(self, status, payload=None, headers=None):
        body = b"" if payload is None else json.dumps(payload, default=str).encode("ascii")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.replied, self.status_sent = True, status
        self.wfile.write(body)
        self.bytes_sent = len(body)

    def reply_or_undo(self, status, payload, headers, calls):
        """reply(), for an answer that holds tool calls' results: when the
        connection drops before it is written (a BrokenPipeError or a
        ConnectionResetError, both ConnectionErrors), what the calls took
        on the client's behalf goes back where it was, and the drop goes
        on up to dispatch, which ends the connection quietly."""
        try:
            self.reply(status, payload, headers)
        except ConnectionError:
            undo_lost(calls)
            raise

    def refuse(self, refusal):
        if not self.replied:
            self.reply(refusal.status, {"error": refusal.message})

    def log_message(self, format, *args):
        if self.server.verbose:
            super().log_message(format, *args)

    def post_mcp(self, run_id, agent):
        """MCP's Streamable HTTP transport (2025-06-18), JSON answers only:
        the hub never streams, so it has no use for an SSE response. The
        agent's token, when the run wants one, is checked before the body
        is read as anything."""
        hosted = self.hub.hosted(run_id, agent)
        self.hub.check_agent(hosted, agent, self.headers.get("Authorization"))
        if not accepts_json(self.headers.get("Accept")):
            raise HubError(406, "the hub answers in application/json only")
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in mcp.PROTOCOL_VERSIONS and not opens_session(self.body):
            # The transport asks for 400 on a version the server does not
            # speak, and a client probing with a newer binding (Claude
            # Code does, with 2026-07-28) reads the 4xx as "fall back to
            # initialize"; an initialize names its version in its params.
            self.reply(400, mcp.error(None, mcp.INVALID_REQUEST,
                                      f"unsupported MCP-Protocol-Version {version!r}; the hub speaks "
                                      + ", ".join(mcp.PROTOCOL_VERSIONS)))
            return
        session = self.headers.get("Mcp-Session-Id")
        if session is not None and not self.hub.knows_session(hosted, agent, session):
            raise HubError(404, "unknown Mcp-Session-Id; send initialize again without one")
        calls = []
        answer = self.answer_mcp(hosted, agent, session, calls)
        headers = {}
        if opened_session(self.body, answer):
            headers["Mcp-Session-Id"] = self.hub.open_session(hosted, agent)
        if answer is None:
            self.reply(202, None, headers)
        else:
            self.reply_or_undo(400 if unreadable(answer) else 200, answer, headers, calls)

    def answer_mcp(self, hosted, agent, session, calls):
        run = hosted.run
        try:
            return mcp.handle_body(self.body, run, agent, run.arm.instructions(run, agent),
                                   sink=calls, in_flight=self.hub.in_flight(hosted, session))
        except Exception:
            complain(f"MCP request from {agent} in run {run.id} failed")
            return internal_errors(self.body)

    def get_rpc(self, run_id, agent):
        hosted = self.hub.hosted(run_id, agent)
        self.hub.check_agent(hosted, agent, self.headers.get("Authorization"))
        run = hosted.run
        self.reply(200, {"agent": agent, "role": run.role_of(agent),
                         "tools": self.hub.describe_tools(run, agent), "prompt": hosted.prompts[agent]})

    def post_rpc(self, run_id, agent):
        hosted = self.hub.hosted(run_id, agent)
        self.hub.check_agent(hosted, agent, self.headers.get("Authorization"))
        run = hosted.run
        payload = self.json_body()
        calls = []
        answer = self.hub.call(run, agent, payload.get("tool"), payload.get("arguments"), calls)
        self.reply_or_undo(200, answer, None, calls)

    def get_health(self):
        self.reply(200, {"ok": True, "runs": self.hub.run_count()})

    def post_runs(self):
        self.hub.check_admin(self.headers.get("Authorization"))
        payload = self.json_body()
        answer = self.hub.create_run(payload.get("arm"), payload.get("features"), payload.get("specs"),
                                     payload.get("options"), payload.get("run_id"),
                                     payload.get("agent_tokens", False))
        self.reply(201, answer)

    def get_run(self, run_id):
        self.hub.check_admin(self.headers.get("Authorization"))
        self.reply(200, self.hub.get_run(run_id))

    def delete_run(self, run_id):
        self.hub.check_admin(self.headers.get("Authorization"))
        self.reply(200, self.hub.release_run(run_id))

    def get_events(self, run_id):
        self.hub.check_admin(self.headers.get("Authorization"))
        self.reply(200, {"events": self.hub.events(run_id)})

    def post_close(self, run_id):
        self.hub.check_admin(self.headers.get("Authorization"))
        self.reply(200, self.hub.close_answer(run_id))


class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    # Every agent of every run may connect at once when a batch starts.
    request_queue_size = 128

    def __init__(self, address, hub, verbose=False):
        super().__init__(address, HubHandler)
        self.hub = hub
        self.verbose = verbose

    def handle_error(self, request, client_address):
        """A client that drops its keep-alive connection (Claude Code does,
        each time it exits) fails the handler's wait for the next request
        line, outside any answer. That is the client's doing, not a hub
        failure, so it leaves no traceback in the log; anything else is
        reported as socketserver does."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def make_server(hub, host, port, verbose=False):
    """A server for the hub, bound but not yet serving; port 0 picks a free
    port. Without a public URL the hub's urls name the bound address, with
    a wildcard host read as loopback, the one address a client can use."""
    server = HubServer((host, port), hub, verbose)
    if hub.public_url is None:
        bound_host = server.server_address[0]
        if bound_host in ("0.0.0.0", ""):
            bound_host = "127.0.0.1"
        hub.bound_url = f"http://{bound_host}:{server.server_address[1]}"
    return server


def serve_in_background(hub, host="127.0.0.1", port=0):
    """Start a server on a daemon thread, for runners and tests that keep
    the hub in their own process; stop it with shutdown() and server_close()."""
    server = make_server(hub, host, port)
    # A short poll keeps shutdown() quick; the cost is a wake-up a tenth
    # of a second, which an idle hub can afford.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1},
                     name="coordbench-hub", daemon=True).start()
    return server


def configure_trace(debug, log_dir=None):
    """The hub's trace level and where its own events go. --debug sets
    debug; otherwise the level is the environment's (COORD_TRACE). In
    debug the hub's events, and every run's with the run's id, go to
    <log_dir>/hub-trace.jsonl, or to stderr without a log directory, and
    the hub says so once; at any other level it registers no listener of
    its own. Returns the listener registered, or None."""
    if debug:
        trace.set_level("debug")
    if trace.level != "debug":
        return None
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        where = Path(log_dir) / "hub-trace.jsonl"
    else:
        where = sys.stderr
    print(f"coordbench hub: tracing at debug to {where if log_dir else 'stderr'}", file=sys.stderr, flush=True)
    return trace.listen(trace.JsonLines(where))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve coordbench arms to agents over MCP and HTTP.")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (0.0.0.0 for containers)")
    parser.add_argument("--port", type=int, default=8765, help="port to bind; 0 picks a free one")
    parser.add_argument("--log-dir", help="write each run's events to <dir>/<run>/events.jsonl")
    parser.add_argument("--public-url", help="the base URL agents reach the hub at, if not the bound one")
    parser.add_argument("--arm-dir", action="append", default=[],
                        help="a directory whose arms (in it or below it) the hub may load; repeatable")
    parser.add_argument("--verbose", action="store_true", help="log every request to stderr")
    parser.add_argument("--debug", action="store_true",
                        help="trace every request, wait and cancel to <log-dir>/hub-trace.jsonl "
                             "(stderr without --log-dir); never on by default")
    args = parser.parse_args(argv)
    configure_trace(args.debug, args.log_dir)
    hub = Hub(args.log_dir, args.public_url, args.arm_dir, os.environ.get("COORD_ADMIN_TOKEN"))
    server = make_server(hub, args.host, args.port, args.verbose)
    print(hub.base_url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
