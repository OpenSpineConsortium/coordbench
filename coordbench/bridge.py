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

The file imports nothing from coordbench, so it can be copied alone into
an agent's container and run by whatever Python 3 is there.

Requests run on threads of their own, because a coordination tool may
block (wait_for_message waits up to 30 seconds) and a client that calls
tools in parallel must not see a quick send queue behind a slow wait.
initialize alone runs before anything else is read, so the session id and
the protocol version it yields are known to every request that follows.
"""

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.client import HTTPException

PARSE_ERROR, INTERNAL_ERROR = -32700, -32603

# Generous, because a tool may block for a while by design; the hub, not
# this timeout, decides how long a call lasts.
TIMEOUT_SECONDS = 600

# The hub is always local to the agent (localhost or the Docker host), so
# a proxy configured for the container's outbound traffic must not carry
# these requests: an empty ProxyHandler ignores HTTP_PROXY and its kin.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HubFailure(Exception):
    """The hub could not be reached, or answered with an HTTP error."""


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


def http_error_text(problem):
    try:
        body = problem.read().decode("utf-8", "replace")
    except (OSError, HTTPException):
        body = ""
    finally:
        problem.close()
    detail = f": {one_line(body)}" if body.strip() else ""
    return f"HTTP {problem.code} {problem.reason}{detail}"


class Bridge:
    """One stdio session's link to the hub: where to post, what the hub
    has said about the session so far, and where answers go."""

    def __init__(self, url, out=None, timeout=TIMEOUT_SECONDS):
        self.url = url
        self.out = out or sys.stdout
        self.timeout = timeout
        self.session_id = None
        self.protocol_version = None
        self.write_lock = threading.Lock()
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
        answers. A request the hub leaves unanswered, for a failure or
        any other reason, gets an internal error, so the client never
        waits on an id forever."""
        unanswered = request_ids(message)
        problem = "the hub gave no answer to this request"
        try:
            for answer in self.post(message):
                self.learn_from(message, answer)
                self.write(answer)
                answered = [a.get("id") for a in as_list(answer) if isinstance(a, dict)]
                unanswered = [i for i in unanswered if i not in answered]
        except HubFailure as failure:
            problem = str(failure)
        if unanswered:
            errors = [error(i, INTERNAL_ERROR, problem) for i in unanswered]
            self.write(errors if isinstance(message, list) else errors[0])

    def post(self, message):
        """Yield the JSON-RPC messages the hub answers to one message."""
        if is_initialize(message):
            self.session_id = self.protocol_version = None
        body = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers=self.headers(), method="POST")
        try:
            with OPENER.open(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                yield from self.answers_in(response)
        except (OSError, HTTPException) as problem:
            raise HubFailure(self.explain(problem))

    def explain(self, problem):
        """One line naming what went wrong between the bridge and the hub."""
        hub = f"the coordbench hub at {self.url}"
        if isinstance(problem, urllib.error.HTTPError):
            return f"{hub} answered {http_error_text(problem)}"
        if isinstance(problem, urllib.error.URLError):
            return f"cannot reach {hub}: {one_line(problem.reason)}"
        return f"lost {hub}: {one_line(problem) or type(problem).__name__}"

    def headers(self):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def answers_in(self, response):
        if response.status == 202:
            return
        if response.headers.get_content_type() == "text/event-stream":
            yield from messages_in_stream(iter(response.readline, b""))
            return
        body = response.read()
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


def main():
    url = os.environ.get("COORD_MCP_URL", "").strip()
    if not url:
        print("bridge.py: COORD_MCP_URL is not set; it names this agent's /mcp endpoint on the hub",
              file=sys.stderr)
        return 2
    Bridge(url).serve(iter(sys.stdin.buffer.readline, b""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
