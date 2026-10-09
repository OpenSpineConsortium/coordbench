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

On /mcp a body the hub cannot read as a request (not JSON, or a lone
message with no id to answer under: no JSON-RPC object, an id that is
not a string or an integer, an empty batch) is answered 400 with a
JSON-RPC error that has no id, as the Streamable HTTP transport asks, and
so is an MCP-Protocol-Version the hub does not speak on anything but an
initialize; what the hub can read is answered 200 message by message,
each request of a batch on its own, and a session id is issued only to
an initialize that was answered with a result. An error the hub itself
causes while answering is -32603 for the request it failed on, with the
traceback in the hub's log; a client that drops its connection leaves no
trace there, since that is the client's doing.

Command line:

    python3 hub.py --host 0.0.0.0 --port 8765 [--log-dir DIR] [--public-url URL]
        [--arm-dir DIR ...]

prints the base URL agents should use as its first line, then serves.

Loading an arm runs its arm.py inside the hub, and agents can reach the
hub, so the hub loads only the built-in arms and arms under the
directories named with --arm-dir, never a directory a request names
elsewhere (an agent could write one into a shared volume). When the
environment holds COORD_ADMIN_TOKEN, the management API (/api/runs and
below) also wants it as a bearer token, so only the runner, which set it,
can create or close runs; agents never see it.
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

import mcp
from arm import Arm, Run, builtin_arms_dir, load_arm_class, unmet_needs

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
    given, and the MCP session ids issued to each agent."""

    run: Run
    prompts: dict
    sessions: dict = field(default_factory=dict)


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

    def create_run(self, arm, features, specs, options=None, run_id=None):
        """Load the arm, lay out its agents and answer as POST /api/runs does."""
        features = parse_features(features)
        specs = parse_specs(specs, features)
        if options is not None and not isinstance(options, dict):
            raise HubError(400, "options must be an object")
        arm = self.resolve_arm(arm)
        missing = unmet_needs(arm, options)
        if missing:
            raise HubError(400, f"the {arm_name(arm)} arm needs {', '.join(missing)}, "
                                "which the run's options do not provide")
        run_id = self.reserve(run_id)
        try:
            hosted = self.build(arm, run_id, features, specs, options)
        except BaseException:
            with self.lock:
                del self.runs[run_id]
            raise
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
        agents = arm.layout(features)
        run = Run(run_id, arm, agents, options, self.events_path(run_id), specs)
        arm.setup(run)
        prompts = {agent: arm.prompt(run, agent, specs) for agent in run.agents}
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
        """Mark the run closed and give its metrics. Tools keep answering,
        since an agent may still be finishing its last call; the runner
        decides what a closed run's late events mean."""
        run = self.hosted(run_id).run
        with run.lock:
            first_close = not run.closed
            run.closed = True
            run.lock.notify_all()
            metrics = run.arm.metrics(run)
        if first_close:
            # Outside the lock: an arm freeing a resource may need to wait
            # on its own locks, which a tool holding run.lock could hold.
            try:
                run.arm.teardown(run)
            except Exception:
                complain(f"the {arm_name(run.arm)} arm's teardown of run {run.id} failed")
        return metrics

    def release_run(self, run_id):
        """Forget the run, closing it first if it is still open, so the
        teardown runs once however the runner ends; the runner releases a
        run once it holds the metrics and the events, and a hub that
        hosts many runs keeps none of the finished ones."""
        self.close_run(run_id)
        with self.lock:
            self.runs.pop(run_id, None)
        return {"run_id": run_id, "released": True}

    def events(self, run_id):
        run = self.hosted(run_id).run
        with run.lock:
            return list(run.events)

    def run_count(self):
        with self.lock:
            return sum(1 for hosted in self.runs.values() if hosted is not None)

    def describe(self, hosted, with_prompts):
        run = hosted.run
        agents = [self.describe_agent(run, agent, hosted.prompts[agent] if with_prompts else None)
                  for agent in run.agents]
        answer = {"run_id": run.id, "arm": arm_name(run.arm), "agents": agents}
        if not with_prompts:
            with run.lock:
                answer["closed"] = run.closed
                answer["metrics"] = run.arm.metrics(run)
        return answer

    def describe_agent(self, run, agent, prompt=None):
        spec = run.agents[agent]
        entry = {"id": agent, "role": spec.role, "features": list(spec.features),
                 "mcp_url": self.url("mcp", run.id, agent), "rpc_url": self.url("rpc", run.id, agent)}
        if prompt is not None:
            entry["prompt"] = prompt
        return entry

    def open_session(self, hosted, agent):
        session = uuid.uuid4().hex
        with self.lock:
            hosted.sessions.setdefault(agent, set()).add(session)
        return session

    def knows_session(self, hosted, agent, session):
        with self.lock:
            return session in hosted.sessions.get(agent, ())

    def describe_tools(self, run, agent):
        return [{**tool.describe(), "alias": tool.alias, "argv": list(tool.argv), "flags": dict(tool.flags)}
                for tool in run.arm.tools_for(run, agent).values()]

    def call(self, run, agent, name, arguments):
        """One tool call outside MCP, for /rpc: the same checks and events
        as tools/call, answered as {"text", "isError"}."""
        tools = run.arm.tools_for(run, agent)
        result, problem = mcp.call_tool(run, agent, tools, {"name": name, "arguments": arguments})
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
    with a result starts a session."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return []
    return [m["id"] for m in as_list(parsed)
            if isinstance(m, dict) and m.get("method") == "initialize" and "id" in m]


def opens_session(body):
    """Whether the body holds an initialize request."""
    return bool(initialize_ids(body))


def opened_session(body, answer):
    """Whether an initialize in the body was answered with a result. One
    refused for its shape starts no session: a client holding a session
    id for a handshake that failed would send it with every request."""
    asked = initialize_ids(body)
    return any(isinstance(a, dict) and "result" in a and a.get("id") in asked
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
        parsed = json.loads(body)
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
        self.replied = False
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
            payload = json.loads(self.body or b"{}")
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
        self.replied = True
        self.wfile.write(body)

    def refuse(self, refusal):
        if not self.replied:
            self.reply(refusal.status, {"error": refusal.message})

    def log_message(self, format, *args):
        if self.server.verbose:
            super().log_message(format, *args)

    def post_mcp(self, run_id, agent):
        """MCP's Streamable HTTP transport (2025-06-18), JSON answers only:
        the hub never streams, so it has no use for an SSE response."""
        hosted = self.hub.hosted(run_id, agent)
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
        answer = self.answer_mcp(hosted.run, agent)
        headers = {}
        if opened_session(self.body, answer):
            headers["Mcp-Session-Id"] = self.hub.open_session(hosted, agent)
        if answer is None:
            self.reply(202, None, headers)
        else:
            self.reply(400 if unreadable(answer) else 200, answer, headers)

    def answer_mcp(self, run, agent):
        try:
            return mcp.handle_body(self.body, run, agent, run.arm.instructions(run, agent))
        except Exception:
            complain(f"MCP request from {agent} in run {run.id} failed")
            return internal_errors(self.body)

    def get_rpc(self, run_id, agent):
        hosted = self.hub.hosted(run_id, agent)
        run = hosted.run
        self.reply(200, {"agent": agent, "role": run.role_of(agent),
                         "tools": self.hub.describe_tools(run, agent), "prompt": hosted.prompts[agent]})

    def post_rpc(self, run_id, agent):
        run = self.hub.hosted(run_id, agent).run
        payload = self.json_body()
        self.reply(200, self.hub.call(run, agent, payload.get("tool"), payload.get("arguments") or {}))

    def get_health(self):
        self.reply(200, {"ok": True, "runs": self.hub.run_count()})

    def post_runs(self):
        self.hub.check_admin(self.headers.get("Authorization"))
        payload = self.json_body()
        answer = self.hub.create_run(payload.get("arm"), payload.get("features"), payload.get("specs"),
                                     payload.get("options"), payload.get("run_id"))
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
        self.reply(200, {"metrics": self.hub.close_run(run_id)})


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve coordbench arms to agents over MCP and HTTP.")
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (0.0.0.0 for containers)")
    parser.add_argument("--port", type=int, default=8765, help="port to bind; 0 picks a free one")
    parser.add_argument("--log-dir", help="write each run's events to <dir>/<run>/events.jsonl")
    parser.add_argument("--public-url", help="the base URL agents reach the hub at, if not the bound one")
    parser.add_argument("--arm-dir", action="append", default=[],
                        help="a directory whose arms (in it or below it) the hub may load; repeatable")
    parser.add_argument("--verbose", action="store_true", help="log every request to stderr")
    args = parser.parse_args(argv)
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
