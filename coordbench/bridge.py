"""A stdio MCP server that forwards every message to the hub over HTTP.

Some agents' MCP clients speak only the stdio transport: they start a
server as a child process and exchange newline-delimited JSON-RPC
messages on its stdin and stdout. This bridge is such a server with no
tools of its own. It POSTs each message it reads to the hub's Streamable
HTTP endpoint for this agent (COORD_MCP_URL) and writes back whatever the
hub answers, so a stdio client sees exactly the tools, prompts and errors
an HTTP client would. An agent's MCP configuration for it:

    {"command": "python3", "args": ["/opt/coordbench/bridge.py"],
     "env": {"COORD_MCP_URL": "http://host.docker.internal:8765/mcp/<run>/<agent>"}}

The file imports nothing from coordbench but trace.py, which it takes
from beside itself when the directory was copied whole and does without
otherwise (a stand-in that records nothing), so it can be copied alone
into an agent's container and run by whatever Python 3 is there. A run
the hub made with agent tokens wants this agent's bearer token on every
request, so every POST carries "Authorization: Bearer <COORD_AGENT_TOKEN>"
when that variable is set and not empty.

In debug (trace.py's level, COORD_TRACE=debug in the bridge's
environment) every POST to the hub is a "bridge_post" span: the ids of
the requests it carries and request_bytes as it starts, and status,
response_bytes, duration_ms and ok on its bridge_post_done, the bytes of
a streamed answer counted as they arrive; each new session the bridge
opens after a 404 is a "bridge_reinit" event with the session the hub
forgot and how many times that has happened. They go as JSON lines to
stderr, the agent's own stream, or to the file COORD_TRACE_FILE names
when it is set; no message's content and no token is ever among them.
In prod, the default, nothing is registered and nothing is written.

Requests run on threads of their own, because a coordination tool may
block (wait_for_message waits up to 30 seconds) and a client that calls
tools in parallel must not see a quick send queue behind a slow wait.
initialize alone runs before anything else is read, so the session id and
the protocol version it yields are known to every request that follows.

A hub that has forgotten the session (it restarted, or the run was
released) answers 404 to a request that carries its id, and the transport
says the client MUST then start a new session. The stdio client has no
session of its own and cannot, so the bridge does it for it: it sends the
client's initialize again, with the params it kept, then
notifications/initialized, neither written to the client, and retries the
request once with the new session.

A hub that cannot read a message as a request (an id that is null or a
boolean, a lone object with no method) answers 400 with a JSON-RPC error
that names no id, as the transport asks. The stdio client sent that
message and is waiting on its ids, so the bridge writes it the hub's own
error, code and message, under each id the hub left unanswered, as the
hub would have answered on stdio; a 400 whose body is no JSON-RPC error
is a hub failure like any other HTTP error, an internal error (-32603)
that quotes the status and the body.
"""

import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path

PARSE_ERROR, INTERNAL_ERROR = -32700, -32603

# Where a debug run's events go instead of stderr, when set.
TRACE_FILE_VARIABLE = "COORD_TRACE_FILE"

# Generous, because a tool may block for a while by design; the hub, not
# this timeout, decides how long a call lasts.
TIMEOUT_SECONDS = 600

# The hub is always local to the agent (localhost or the Docker host), so
# a proxy configured for the container's outbound traffic must not carry
# these requests: an empty ProxyHandler ignores HTTP_PROXY and its kin.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class NoSpan:
    """What the stand-in's debug_span gives: a block that is not timed and
    a decorator that leaves its function alone. fields is where the caller
    puts what it learns on the way, read by nobody here."""

    def __init__(self, fields):
        self.fields = fields

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def __call__(self, function):
        return function


class NoTrace:
    """What stands in for trace.py when the file is not beside this one:
    the level off and the three calls this file makes, each doing nothing,
    so a copy of bridge.py alone works as before and debug is never a
    dependency of the agent's MCP server. The same stand-in is in cli.py,
    by design: each file has to stand alone in a container."""

    level = "off"

    @staticmethod
    def event(kind, **fields):
        return None

    @staticmethod
    def debug(kind, **fields):
        return None

    @staticmethod
    def debug_span(name, **fields):
        return NoSpan(fields)


def same_file(module, path):
    try:
        return Path(module.__file__).resolve() == path
    except (AttributeError, TypeError, OSError):
        return False


def load_trace():
    """coordbench's trace.py, beside this file when the directory was
    copied whole (drivers put it at /opt/coordbench), loaded by its
    location: the standard library has a module named trace too, and
    `import trace` would take that one whenever this directory is not
    first on sys.path. A process that imported coordbench's already
    shares it, so its events and the bridge's are one stream; a fresh
    process registers the module under its name when no module holds it
    yet, so the rest of coordbench imported later finds the same one, and
    leaves another's alone. With no trace.py beside this file, the
    stand-in."""
    path = Path(__file__).resolve().with_name("trace.py")
    known = sys.modules.get("trace")
    if same_file(known, path):
        return known
    if not path.is_file():
        return NoTrace
    spec = importlib.util.spec_from_file_location("trace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if known is None:
        sys.modules["trace"] = module
    return module


coordtrace = load_trace()


class HubFailure(Exception):
    """The hub could not be reached, or answered with an HTTP error."""


class HubRefused(HubFailure):
    """The hub answered 400 with a JSON-RPC error that names no request:
    it could not read the message. Carries the hub's code and message,
    which the client hears under each id it is waiting on."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class SessionLost(Exception):
    """The hub answered 404 to a request that carried a session id: it no
    longer knows the session, and a new one must be opened. Carries the
    id the hub refused, so one re-initialize serves every request that
    failed on the same stale id."""

    def __init__(self, session_id):
        super().__init__(session_id)
        self.session_id = session_id


def error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def as_list(message):
    return message if isinstance(message, list) else [message]


def request_ids(message):
    """The ids of the requests in a message or a batch; notifications and
    responses carry no method and an id, or a method and no id."""
    return [m["id"] for m in as_list(message)
            if isinstance(m, dict) and "method" in m and "id" in m]


def is_initialize(message):
    return any(isinstance(m, dict) and m.get("method") == "initialize" for m in as_list(message))


def initialize_params(message):
    """The params of the initialize in a message or a batch, as the client
    sent them, so the handshake can be repeated word for word."""
    for item in as_list(message):
        if isinstance(item, dict) and item.get("method") == "initialize":
            params = item.get("params")
            return params if isinstance(params, dict) else {}
    return None


def is_jsonrpc(payload):
    if isinstance(payload, list):
        return bool(payload) and all(is_jsonrpc(item) for item in payload)
    return isinstance(payload, dict) and payload.get("jsonrpc") == "2.0"


def one_line(text, limit=300):
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def server_sent_data(lines):
    """Yield the data of each event in a text/event-stream, as the SSE
    specification assembles it: data lines joined by newlines, an empty
    line ending the event, comments and other fields ignored. An event
    left open when the stream ends is still yielded, to be lenient with
    servers that close without the final blank line."""
    data = []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
            data = []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if name == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield "\n".join(data)


def messages_in_stream(lines):
    for payload in server_sent_data(lines):
        try:
            message = json.loads(payload)
        except ValueError:
            continue
        if is_jsonrpc(message):
            yield message


def http_error_body(problem):
    """The body of an HTTP error, read once and the response closed."""
    try:
        return problem.read().decode("utf-8", "replace")
    except (OSError, HTTPException):
        return ""
    finally:
        problem.close()


def http_error_text(problem, body):
    detail = f": {one_line(body)}" if body.strip() else ""
    return f"HTTP {problem.code} {problem.reason}{detail}"


def counted_lines(response, fields):
    """The lines of a streamed response, their bytes summed into the
    bridge_post span's fields as they are read, so response_bytes is
    right however the stream ends."""
    for raw in iter(response.readline, b""):
        fields["response_bytes"] += len(raw)
        yield raw


def jsonrpc_error_in(body):
    """The error of a lone JSON-RPC error message, when the body is one
    (an integer code and a string message under "error"); else None."""
    try:
        message = json.loads(body)
    except ValueError:
        return None
    if isinstance(message, list) or not is_jsonrpc(message):
        return None
    found = message.get("error")
    if isinstance(found, dict) and isinstance(found.get("code"), int) and isinstance(found.get("message"), str):
        return found
    return None


class Bridge:
    """One stdio session's link to the hub: where to post, the bearer
    token to post with when the run wants one, what the hub has said
    about the session so far, and where answers go."""

    def __init__(self, url, out=None, timeout=TIMEOUT_SECONDS, token=None):
        self.url = url
        self.out = out or sys.stdout
        self.timeout = timeout
        self.token = token or None
        self.session_id = None
        self.protocol_version = None
        self.initialize_params = None
        self.reopened = 0
        self.write_lock = threading.Lock()
        self.session_lock = threading.Lock()
        self.workers = []

    def serve(self, lines):
        """Answer every line until the input ends, then wait for the
        requests still in flight so none of their answers is lost."""
        for raw in lines:
            self.take(raw)
        for worker in self.workers:
            worker.join()

    def take(self, raw):
        if not raw.strip():
            return
        try:
            message = json.loads(raw)
        except ValueError:
            self.write(error(None, PARSE_ERROR, "parse error: the line is not JSON"))
            return
        if request_ids(message) and not is_initialize(message):
            self.forward_in_background(message)
        else:
            self.forward(message)

    def forward_in_background(self, message):
        worker = threading.Thread(target=self.forward, args=(message,), daemon=True)
        self.workers = [w for w in self.workers if w.is_alive()] + [worker]
        worker.start()

    def forward(self, message):
        """Post one message and write back every JSON-RPC message the hub
        answers. A request the hub leaves unanswered gets an error under
        its id, so the client never waits on an id forever: the hub's own
        error when the hub refused the message as unreadable (HubRefused),
        an internal error for a failure or any other reason."""
        unanswered = request_ids(message)
        code, problem = INTERNAL_ERROR, "the hub gave no answer to this request"
        try:
            for answer in self.post(message):
                self.learn_from(message, answer)
                self.write(answer)
                answered = [a.get("id") for a in as_list(answer) if isinstance(a, dict)]
                unanswered = [i for i in unanswered if i not in answered]
        except HubRefused as refused:
            code, problem = refused.code, refused.message
        except HubFailure as failure:
            problem = str(failure)
        if unanswered:
            errors = [error(i, code, problem) for i in unanswered]
            self.write(errors if isinstance(message, list) else errors[0])

    def post(self, message):
        """Yield the JSON-RPC messages the hub answers to one message. A
        session the hub has forgotten is reopened and the message sent
        once more; a second refusal is the hub's answer."""
        if is_initialize(message):
            self.session_id = self.protocol_version = None
            self.initialize_params = initialize_params(message)
        try:
            yield from self.exchange(message)
        except SessionLost as lost:
            self.reopen_session(lost.session_id)
            yield from self.exchange(message, reopen=False)

    def exchange(self, message, reopen=True):
        """Post one message once and yield what the hub answers. With
        `reopen`, a 404 to a request sent with a session id is raised as
        SessionLost before anything is yielded, so a retry repeats no
        answer; without it, a 404 is an HTTP error like any other."""
        sent_with = self.session_id
        body = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers=self.headers(), method="POST")
        # In debug, one bridge_post span per POST: the ids and the size on
        # the way out, and on the way back the status and the answer's
        # size, which answers_in puts on the span's fields once the hub
        # has answered, so the closing event, bridge_post_done, says it
        # all. The span sits in a generator, so it closes when the answers
        # have been read and its duration is the whole exchange; an
        # unreachable hub leaves status out. In prod it is one comparison
        # on enter and a flag on exit.
        with coordtrace.debug_span("bridge_post", ids=request_ids(message), request_bytes=len(body)) as span:
            try:
                with OPENER.open(request, timeout=self.timeout) as response:
                    self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                    yield from self.answers_in(response, span.fields)
            except urllib.error.HTTPError as problem:
                span.fields["status"] = problem.code
                if reopen and problem.code == 404 and sent_with:
                    problem.close()
                    raise SessionLost(sent_with) from None
                raise self.refusal(problem)
            except (OSError, HTTPException) as problem:
                raise HubFailure(self.explain(problem))

    def refusal(self, problem):
        """What an HTTP error from the hub is to the client: the hub's own
        JSON-RPC error when a 400 carries one (HubRefused), else a
        HubFailure naming the status and the body."""
        body = http_error_body(problem)
        found = jsonrpc_error_in(body) if problem.code == 400 else None
        if found is not None:
            return HubRefused(found["code"], found["message"])
        return HubFailure(f"the coordbench hub at {self.url} answered {http_error_text(problem, body)}")

    def reopen_session(self, lost):
        """Start a new session as the transport asks after a 404: the
        client's initialize again, then notifications/initialized, their
        answers kept from the client, which already had its handshake.
        Requests run in parallel, so the first to find the session gone
        reopens it and the others, failed on the same id, reuse it."""
        with self.session_lock:
            if self.session_id != lost:
                return
            self.session_id = self.protocol_version = None
            if self.initialize_params is None:
                raise HubFailure(f"the coordbench hub at {self.url} forgot the session "
                                 "and no initialize was sent to repeat")
            self.reopened += 1
            coordtrace.debug("bridge_reinit", session=lost, reopened=self.reopened)
            initialize = {"jsonrpc": "2.0", "id": f"coordbench-bridge-initialize-{self.reopened}",
                          "method": "initialize", "params": self.initialize_params}
            for answer in self.exchange(initialize, reopen=False):
                self.learn_from(initialize, answer)
            for _ in self.exchange({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   reopen=False):
                pass

    def explain(self, problem):
        """One line naming what went wrong between the bridge and the hub
        when no HTTP answer came; an HTTP error is refusal's."""
        hub = f"the coordbench hub at {self.url}"
        if isinstance(problem, urllib.error.URLError):
            return f"cannot reach {hub}: {one_line(problem.reason)}"
        return f"lost {hub}: {one_line(problem) or type(problem).__name__}"

    def headers(self):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def answers_in(self, response, fields):
        """Yield the JSON-RPC messages in the hub's response. fields, the
        bridge_post span's, learn the status and the bytes that came
        back, counted line by line for a stream."""
        fields["status"], fields["response_bytes"] = response.status, 0
        if response.status == 202:
            return
        if response.headers.get_content_type() == "text/event-stream":
            yield from messages_in_stream(counted_lines(response, fields))
            return
        body = response.read()
        fields["response_bytes"] = len(body)
        if not body.strip():
            return
        try:
            answer = json.loads(body)
        except ValueError:
            answer = None
        if not is_jsonrpc(answer):
            raise HubFailure(f"the coordbench hub at {self.url} answered with no JSON-RPC message")
        yield answer

    def learn_from(self, message, answer):
        """Keep the protocol version the hub agreed to in its answer to
        initialize; every later request names it, as the transport asks."""
        asked = [m.get("id") for m in as_list(message)
                 if isinstance(m, dict) and m.get("method") == "initialize"]
        for item in as_list(answer):
            mine = isinstance(item, dict) and item.get("id") in asked
            result = item.get("result") if mine else None
            if isinstance(result, dict) and result.get("protocolVersion"):
                self.protocol_version = result["protocolVersion"]

    def write(self, message):
        line = json.dumps(message) + "\n"
        with self.write_lock:
            self.out.write(line)
            self.out.flush()


def configure_trace():
    """Where this process's debug events go: one JsonLines on the file
    COORD_TRACE_FILE names, appended, else on stderr, the agent's own
    stream. In prod, and with the stand-in, nothing is registered and
    every event stays one check. Returns the listener, for release_trace,
    or None."""
    if coordtrace.level != "debug":
        return None
    path = os.environ.get(TRACE_FILE_VARIABLE, "").strip()
    return coordtrace.listen(coordtrace.JsonLines(path or sys.stderr))


def release_trace(sink):
    """Take configure_trace's listener off again and close its file; a
    stream it was given stays open, since it is the process's own."""
    if sink is not None:
        coordtrace.unlisten(sink)
        sink.close()


def main():
    url = os.environ.get("COORD_MCP_URL", "").strip()
    if not url:
        print("bridge.py: COORD_MCP_URL is not set; it names this agent's /mcp endpoint on the hub",
              file=sys.stderr)
        return 2
    token = os.environ.get("COORD_AGENT_TOKEN", "").strip()
    sink = configure_trace()
    try:
        Bridge(url, token=token).serve(iter(sys.stdin.buffer.readline, b""))
    finally:
        release_trace(sink)
    return 0


if __name__ == "__main__":
    sys.exit(main())
