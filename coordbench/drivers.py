"""Drivers: how one agent of a run is started, and how its result comes back.

The runner (run.py) gives every agent a container, a prompt and the hub's
addresses, then hands it to a driver:

    handle = driver.start(job)       # an AgentJob; returns at once
    outcome = driver.wait(handle)    # an AgentOutcome, once the agent stops

Two drivers come with coordbench, and both leave the agent's work in its
container's /workspace/repo/patch.txt, where every arm's prompt asks for it:

- ClaudeCliDriver runs headless Claude Code inside the agent's container,
  as CooperBench's claude_code adapter does (src/cooperbench/agents/
  claude_code/adapter.py at 63b9d44, MIT licence), with the arm loaded as
  a Claude Code plugin, so the hub's tools arrive over MCP and the arm's
  skill rides along; the arm's extra plugins go along as directories
  copied into the container (--plugin-dir) or, for a .zip URL, as the URL
  itself (--plugin-url). Usage, cost and turns come from the stream's
  final result event, parsed as the adapter's parsers.py parses it, and
  so do the agent's wall time and the part of it spent waiting on the
  API (the event's duration_ms and duration_api_ms, which parsers.py
  keeps only inside its raw copy of the event). The agent's time limit
  holds inside the container: claude runs under
  coreutils timeout, which ends it when the limit passes whether or not
  the docker client that started it is still there, and the shell leaves
  its pid in a file so the runner can stop the agent early with the shell
  builtin kill, since the task images have no pkill.
- SessionDriver hands the agent to a Claude Code session's own subagents,
  which run on the host. The agent's repository is a host directory
  bind-mounted at /workspace/repo in its container, so the subagent edits
  it with its own tools and runs every command in the container; the
  coordination commands are wrappers on the host. start() writes the
  prompt the orchestrating session gives its subagent, and wait() watches
  for the marker the prompt asks the subagent to leave last. Because its
  handle is plain JSON, the wait can happen in another process (run.py's
  prepare and finish). It sees no stream and no result event, so the
  agent's steps and durations stay unknown (null). Its tokens come from
  the subagent's own transcript instead: the prompt asks the subagent to
  open its first reply with a run token naming the run and the agent,
  wait() finds the transcript that carries it under Claude Code's
  projects directory, copies it beside the run's logs as
  <agent>_session.jsonl, sums each API message's usage once (one message
  spans several transcript lines) and estimates the cost from the price
  table below, marked as an estimate. A transcript holds the final output
  count only on a message's stop_reason line, which most messages lack;
  then output_tokens and the cost stay unknown and their lower bounds are
  recorded instead (transcript_usage). How the agent ended goes in
  <agent>_session_outcome.json, never in a file named like a stream,
  which CooperBench's own parser would read as an Error.

A driver of one's own is any class with start() and wait(); run.py loads
it from path/to/file.py:ClassName. Class attributes tell the runner what
the driver wants: `host_workspace` (the repository as a host directory),
`resumable` (start and wait may run in different processes), `framework`
(the agent_framework recorded in result.json), `measures_usage`
(whether the outcome's token counts, steps and cost were measured; a
driver without the attribute is taken not to measure them, and
result.json then holds null in their place rather than zeros; an
outcome's own `measured` names the fields it measured when that differs
by agent), `isolation` ("container" when the agent can reach nothing but
its container, "advisory" when the isolation holds by instruction only;
a host_workspace driver without the attribute is taken as advisory) and
`differences_from_cooperbench` (what config.json lists as this driver's
departures from CooperBench's claude_code adapter).

Every docker command goes through docker() below, so tests swap in a fake.
In debug (trace.py; run.py's --debug) each one leaves a "docker" event
naming its subcommand, the argument it acted on, its exit code, its
duration and the bytes of output it produced, never its command line; a
file written here or by the runner (write_text) leaves a "wrote" event
with its name and size; and the session driver's every look for its
marker leaves a "marker_poll". In prod each of those is one comparison,
and for a docker command the clock read before it.
"""

import importlib.util
import json
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import trace
from arm import builtin_arms_dir

CONTAINER_REPO = "/workspace/repo"
CONTAINER_COORDBENCH = "/opt/coordbench"
CONTAINER_PLUGINS = "/opt/coordbench-plugins"
# Arms from outside coordbench's own arms/ live under a root of their own
# in the container, never beside the built-in ones (container_arm_dir).
CONTAINER_ARMS = "/opt/coordbench-arms"
CONTAINER_PROMPT = "/tmp/coord-prompt.txt"
CONTAINER_MCP_CONFIG = "/tmp/coord-mcp.json"
CONTAINER_AGENT_FILE = "/tmp/coord-agent.json"
# The adapter keeps Claude Code's state under /tmp, so the session
# transcript is always in one known place after the run.
CONTAINER_CLAUDE_CONFIG_DIR = "/tmp/claude-cfg"
# Where the shell that runs claude leaves its pid, for stop_claude.
CONTAINER_PID_FILE = "/tmp/coord-claude.pid"
# How long timeout gives claude to end on SIGTERM before SIGKILL.
KILL_GRACE_SECONDS = 30
DONE_MARKER = ".coordbench-done"

# Secrets reach the agent by name only: `docker exec -e NAME` makes the
# docker client copy the value from its own environment, so the value is
# never on a command line, in a file or in a log.
PASSED_BY_NAME = ("CLAUDE_CODE_OAUTH_TOKEN",)
# The hub's admin token is the runner's alone: with it an agent could
# create or close runs. It never reaches a container, nor even the docker
# client, so no `-e` added by mistake could hand it on.
ADMIN_TOKEN = "COORD_ADMIN_TOKEN"
# Each agent's own hub token, which its /mcp and /rpc want as a bearer
# token. It is the agent's, not the runner's: run.py gives it to the
# docker client's environment for the one `docker run` that starts the
# agent's container, named by `-e` alone, and the exec that runs claude
# inherits it from the container. One in the runner's own environment is
# nobody's and is held back like the admin token.
AGENT_TOKEN = "COORD_AGENT_TOKEN"
# The subscription token may also arrive under this name. A Claude Code
# cloud environment runs the session's own Claude Code on dozens of
# CLAUDE_CODE_* variables of its own and withholds a user-supplied
# CLAUDE_CODE_OAUTH_TOKEN, which would fight with the session's sign-in,
# so there the owner sets COORDBENCH_CLAUDE_TOKEN and adopt_token_alias()
# copies it to the name Claude Code reads, in the runner's process only.
# The alias itself never reaches a container or the docker client.
TOKEN_ALIAS = "COORDBENCH_CLAUDE_TOKEN"
HELD_BACK = (ADMIN_TOKEN, AGENT_TOKEN, TOKEN_ALIAS)


def adopt_token_alias(environ=None):
    """Give CLAUDE_CODE_OAUTH_TOKEN the alias's value when only the alias
    is set; a token set under its own name always wins. Returns whether
    the token is now set."""
    environ = os.environ if environ is None else environ
    if not environ.get(PASSED_BY_NAME[0]) and environ.get(TOKEN_ALIAS):
        environ[PASSED_BY_NAME[0]] = environ[TOKEN_ALIAS]
    return bool(environ.get(PASSED_BY_NAME[0]))

# The adapter's own exports beside the model and the config directory
# (_build_claude_command), kept so the agent runs as the authors' did.
CLAUDE_ENVIRONMENT = {
    "IS_SANDBOX": "1",
    "FORCE_AUTO_BACKGROUND_TASKS": "1",
    "ENABLE_BACKGROUND_TASKS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    # Claude Code aborts an HTTP MCP tool call after 60 s by default, and
    # the team arm's request tool waits up to 300 s. Each arm's .mcp.json
    # sets the same per-server timeout; this is the belt to those braces,
    # since an arm author may rewrite a plugin's .mcp.json. Milliseconds.
    "MCP_TOOL_TIMEOUT": "330000",
}

SECTION_RULE = "\n\n---\n\n"
NO_RESULT = "the stream ended without a result event"
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def docker(*args, input=None, timeout=None, output=None, extra_env=None):
    """Run `docker <args>` and return the CompletedProcess, whatever its exit
    code; the caller decides what a failure means. With `output`, a path,
    stdout and stderr go to that file as they arrive instead of being
    captured, which is how a long agent run is teed to its log. With
    `extra_env`, names to values, the docker client's environment holds
    those for this one call and no other, which is how a value that
    `-e NAME` names by itself reaches a container and no command line.
    In debug the command leaves a "docker" event (docker_record), however
    it ended, a timeout included; in prod that costs a clock read before
    the command and one comparison after it."""
    command = ["docker", *(str(arg) for arg in args)]
    environment = {**docker_environment(), **(extra_env or {})}
    started, done = time.monotonic(), None
    try:
        done = run_docker(command, input, timeout, output, environment)
        return done
    finally:
        if trace.level == "debug":
            trace.event("docker", **docker_record(command, done, output, started))


def run_docker(command, input, timeout, output, environment):
    if output is not None:
        with open(output, "w", encoding="utf-8") as stream:
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=timeout, env=environment)
    text = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": timeout,
            "env": environment}
    if input is None:
        return subprocess.run(command, stdin=subprocess.DEVNULL, **text)
    return subprocess.run(command, input=input, **text)


# The options the runner passes docker that take a value, so that the
# argument a "docker" event names is the thing acted on (a container, an
# image, a path) and not -w's directory or -e's variable name.
VALUED_OPTIONS = {"-w", "-e", "-v", "-p", "-f", "--name", "--label", "--network", "--add-host", "--entrypoint",
                  "--restart"}


def docker_argument(words):
    """The first word that is neither an option nor an option's value."""
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word in VALUED_OPTIONS:
            skip = True
        elif not word.startswith("-"):
            return word
    return None


def docker_record(command, done, output, started):
    """The debug record of one docker command: its subcommand and the
    argument it acted on, never the whole line, which names a secret's
    variable even when it carries no value; its exit code (None when it
    did not end, as on a timeout); its duration; and the bytes of output
    it produced, the captured text's as UTF-8 or the teed file's size."""
    return {"subcommand": command[1] if len(command) > 1 else None, "argument": docker_argument(command[2:]),
            "exit_code": None if done is None else done.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "output_bytes": output_size(done, output)}


def output_size(done, output):
    if output is None:
        return 0 if done is None else sum(len((text or "").encode("utf-8")) for text in (done.stdout, done.stderr))
    try:
        return os.path.getsize(output)
    except OSError:
        return 0


def write_text(path, text, append=False):
    """Write a text file, as everything the runner and the drivers leave
    under a run directory is written, so that in debug each one is a
    "wrote" event with the file's name and its bytes, never its text; a
    file holding a credential is made private by its caller first."""
    path = Path(path)
    with open(path, "a" if append else "w", encoding="utf-8") as stream:
        stream.write(text)
    if trace.level == "debug":
        trace.event("wrote", file=path.name, bytes=len(text.encode("utf-8")))


def docker_environment():
    """The docker client's environment: this process's, which holds what
    PASSED_BY_NAME names for `-e NAME` to copy, but nothing HELD_BACK."""
    return {name: value for name, value in os.environ.items() if name not in HELD_BACK}


class DockerError(RuntimeError):
    """A docker command the run cannot do without failed."""


def checked(result, doing):
    """The result when the command succeeded; otherwise DockerError naming
    what was being done and the last thing docker said."""
    if result.returncode == 0:
        return result
    said = (result.stderr or result.stdout or "").strip().splitlines()
    raise DockerError(f"{doing} failed (exit {result.returncode}): {said[-1] if said else 'no output'}")


def safe_name(text):
    """A string fit for a container name or a file name."""
    return UNSAFE_NAME.sub("-", str(text)).strip("-.") or "x"


@dataclass
class AgentJob:
    """One agent, provisioned and ready to start."""

    run_id: str
    agent: str
    role: str
    features: tuple
    container: str
    prompt: str
    env: dict
    arm_dir: str
    # Whether Claude Code loads the arm itself as a plugin (--plugin-dir):
    # false on the bash tool surface, where CooperBench's agents had no
    # plugin and no skill (Arm.claude_surface).
    arm_plugin: bool = True
    plugins: list = field(default_factory=list)
    # Plugin .zip URLs, handed to Claude Code as they are (--plugin-url).
    plugin_urls: list = field(default_factory=list)
    mcp_servers: dict = field(default_factory=dict)
    model: str = ""
    timeout_s: float = 7200.0
    max_turns: int | None = None
    log_path: str = ""
    workspace: str = ""
    # Claude Code tools the agent may not use (--disallowedTools), as
    # WebFetch and WebSearch for the paper's "local operations" only.
    disallowed_tools: list = field(default_factory=list)
    # The agent the session runs as (--agent-file; agent_file.py): its
    # name, the sha256 of its file and the `claude --agents` JSON.
    agent_definition: dict | None = None


# The usage fields of an agent's record, in the order result.json lists them.
USAGE_FIELDS = ("cost", "steps", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
TOKEN_FIELDS = USAGE_FIELDS[2:]


@dataclass
class AgentOutcome:
    """How an agent ended, in CooperBench's terms: status is Submitted,
    LimitsExceeded or Error; cost is the result event's total_cost_usd.
    `error` is what CooperBench's adapter would write there, the message
    of an exception that stopped the agent's run (a timeout, a container
    that went away), and None otherwise, even for an Error read from the
    result event; `end_reason` is coordbench's own account of how a run
    that did not submit ended (the result event's text, a stream with no
    result event, the timeout), None for one that submitted.
    `patch` is the agent's patch.txt. duration_ms is the agent's wall
    time as Claude Code measured it and api_duration_ms the part of it
    spent waiting on the API (the result event's duration_ms and
    duration_api_ms); a driver that cannot know them leaves them None,
    which result.json writes as null, since a zero would read as a
    measurement. ended_at is when the agent itself stopped, as epoch
    seconds, for a driver that knows it better than the moment wait()
    returned: the session driver reads its marker's mtime, the CLI driver
    the moment docker exec returned; None means "when wait returned", so
    an agent's wall time in the timings is never stretched by a late
    finish.

    `measured` names the USAGE_FIELDS this outcome measured, when the
    driver knows it per agent (the session driver measures tokens from a
    transcript it may not find); None leaves it to the driver's
    measures_usage. cost_source says where a measured cost came from:
    "claude code" (its own total_cost_usd) or "estimated from tokens".
    model, claude_code_version, tools and mcp_servers are what the agent
    actually ran with, from the stream's init event or the transcript,
    None when the driver could not see them."""

    status: str = "Error"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    steps: int = 0
    cost: float = 0.0
    ended_at: float | None = None
    duration_ms: int | None = None
    api_duration_ms: int | None = None
    error: str | None = None
    end_reason: str | None = None
    patch: str = ""
    measured: tuple | None = None
    cost_source: str | None = None
    model: str | None = None
    claude_code_version: str | None = None
    tools: list | None = None
    mcp_servers: list | None = None
    # When the session driver's transcript holds the output count only as
    # a lower bound (fill_from_transcript): that bound and the cost it gives.
    output_tokens_lower_bound: int | None = None
    cost_lower_bound: float | None = None


def json_lines(text):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def status_of(result_event):
    """parse_stream_json's status rule (claude_code/parsers.py at 63b9d44)."""
    is_error = bool(result_event.get("is_error")) or result_event.get("subtype", "success") != "success"
    if not is_error:
        return "Submitted"
    subtype = result_event.get("subtype", "")
    if "max_turns" in subtype or "limit" in subtype or "budget" in subtype:
        return "LimitsExceeded"
    return "Error"


def milliseconds_or_none(value):
    """A duration the result event may carry: None when it does not."""
    return None if value is None else int(value)


def stream_init(text):
    """What the agent ran with, from the stream's system init event: the
    model, Claude Code's version, its tools and its MCP servers (each as
    the event gives it, a name with a status). Empty when the stream has
    no init event; CooperBench's parsers keep none of it."""
    for event in json_lines(text):
        if event.get("type") == "system" and event.get("subtype") == "init":
            return {"model": event.get("model"), "claude_code_version": event.get("claude_code_version"),
                    "tools": event.get("tools"), "mcp_servers": event.get("mcp_servers")}
    return {}


def summarize_stream(text):
    """Usage, cost, turns, status and the two durations from Claude Code's
    stream-json, read from the first result event as parse_stream_json
    reads it, with what the init event says the agent ran with. A stream
    without a result event (the CLI crashed or was killed) is an Error
    with nothing measured: CooperBench writes zeros there, coordbench
    null, since a zero would read as a measurement. Neither case sets
    `error`, which CooperBench keeps for an exception in its adapter; how
    the agent ended goes in end_reason."""
    init = stream_init(text)
    for event in json_lines(text):
        if event.get("type") != "result":
            continue
        usage = event.get("usage") or {}
        status = status_of(event)
        return AgentOutcome(
            status=status,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            steps=int(event.get("num_turns") or 0),
            cost=float(event.get("total_cost_usd") or 0),
            duration_ms=milliseconds_or_none(event.get("duration_ms")),
            api_duration_ms=milliseconds_or_none(event.get("duration_api_ms")),
            end_reason=None if status == "Submitted" else str(event.get("result") or event.get("subtype"))[:500],
            measured=USAGE_FIELDS, cost_source="claude code", **init,
        )
    return AgentOutcome(status="Error", end_reason=NO_RESULT, measured=(), **init)


# CooperBench's session parser (src/cooperbench/agents/claude_code/
# parsers.py at 63b9d44, MIT licence), kept verbatim but for the names, so
# agent<feature>_traj.json holds the messages CooperBench's own runner
# would have written from the same <agent>_session.jsonl.

def content_blocks_to_text(content):
    """_content_blocks_to_text: a message's content blocks as one string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)
    parts = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif btype == "thinking":
            thought = block.get("thinking") or block.get("text") or ""
            if thought:
                parts.append(f"[thinking] {thought}")
        elif btype == "tool_use":
            name = block.get("name", "")
            args = block.get("input")
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except TypeError:
                args_str = str(args)
            parts.append(f"[tool_use {name}] {args_str}")
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner_text = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in inner)
            elif inner is None:
                inner_text = ""
            else:
                inner_text = str(inner)
            parts.append(f"[tool_result] {inner_text}")
        else:
            try:
                parts.append(json.dumps(block, ensure_ascii=False))
            except TypeError:
                parts.append(str(block))
    return "\n".join(p for p in parts if p)


def parse_session_jsonl(text):
    """parse_session_jsonl: the session JSONL as chat messages, sorted by
    timestamp, each {"role", "content"} with the content a string."""
    events = list(json_lines(text))
    events.sort(key=lambda e: e.get("timestamp") or "")
    messages = []
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role") or event.get("type")
        if role not in {"user", "assistant", "system"}:
            continue
        messages.append({"role": role, "content": content_blocks_to_text(message.get("content"))})
    return messages


# Usage from a session transcript, for a driver that sees no result event.

# Claude Code writes these for messages it made up itself (an interrupted
# turn, a refusal it reports), which no model produced and nobody paid for.
SYNTHETIC_MODEL = "<synthetic>"


def transcript_usage(text):
    """The transcript's token counts, summed once per API message: one
    message spans several lines, one per content block, each repeating
    the message's usage, so each message counts with the largest value of
    each field its lines carry. The input and cache counts are known when
    the message starts and are exact. The output count is not: Claude
    Code writes a line's usage as the stream stood when the block ended,
    and only a line carrying the message's stop_reason holds the final
    count (in 2.1.295's transcripts most messages have none, and their
    lines say 2 to 5 output tokens for thousands of characters), so
    output_final says whether every message had one; when not, the output
    sum is only a lower bound. Also the models the assistant lines name
    and Claude Code's version."""
    per_message, finals, models, version = {}, set(), [], None
    for event in json_lines(text):
        version = version or event.get("version")
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        model = message.get("model")
        if model and model != SYNTHETIC_MODEL and model not in models:
            models.append(model)
        key = message.get("id") or id(message)
        if message.get("stop_reason"):
            finals.add(key)
        usage = message.get("usage") or {}
        counts = per_message.setdefault(key, dict.fromkeys(TOKEN_FIELDS, 0))
        for name, source in zip(TOKEN_FIELDS, ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                               "cache_creation_input_tokens")):
            counts[name] = max(counts[name], int(usage.get(source) or 0))
    totals = {name: sum(counts[name] for counts in per_message.values()) for name in TOKEN_FIELDS}
    return {"tokens": totals, "messages": len(per_message), "output_final": finals >= set(per_message),
            "models": models, "claude_code_version": version}


# The cost estimate. CooperBench's agents/pricing.py prices a model that
# reports no cost through litellm's table first and its own (MiniMax only)
# second; these are the Claude rows of that litellm table as CooperBench
# 63b9d44's environment ships it (litellm 1.81.12,
# model_prices_and_context_window_backup.json, provider anthropic), in
# dollars per million tokens: input, output, cache read, cache write.
# A model missing here gets no estimate: a guess from a neighbouring model
# would read as a price. The long-context rates above 200k tokens are left
# out, since a request over 200k needs the 1M-context beta, which no
# coordbench driver turns on.
CLAUDE_PRICES = {
    "claude-3-5-haiku-20241022": (0.8, 4.0, 0.08, 1.0),
    "claude-3-5-haiku-latest": (1.0, 5.0, 0.1, 1.25),
    "claude-3-5-sonnet-20240620": (3.0, 15.0, 0.3, 3.75),
    "claude-3-5-sonnet-20241022": (3.0, 15.0, 0.3, 3.75),
    "claude-3-5-sonnet-latest": (3.0, 15.0, 0.3, 3.75),
    "claude-3-7-sonnet-20250219": (3.0, 15.0, 0.3, 3.75),
    "claude-3-7-sonnet-latest": (3.0, 15.0, 0.3, 3.75),
    "claude-3-haiku-20240307": (0.25, 1.25, 0.03, 0.3),
    "claude-3-opus-20240229": (15.0, 75.0, 1.5, 18.75),
    "claude-3-opus-latest": (15.0, 75.0, 1.5, 18.75),
    "claude-4-opus-20250514": (15.0, 75.0, 1.5, 18.75),
    "claude-4-sonnet-20250514": (3.0, 15.0, 0.3, 3.75),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1, 1.25),
    "claude-opus-4-1": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-1-20250805": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-20250514": (15.0, 75.0, 1.5, 18.75),
    "claude-opus-4-5": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-5-20251101": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-6": (5.0, 25.0, 0.5, 6.25),
    "claude-opus-4-6-20260205": (5.0, 25.0, 0.5, 6.25),
    "claude-sonnet-4-20250514": (3.0, 15.0, 0.3, 3.75),
    "claude-sonnet-4-5": (3.0, 15.0, 0.3, 3.75),
    "claude-sonnet-4-5-20250929": (3.0, 15.0, 0.3, 3.75),
}


def estimated_cost(models, tokens):
    """Dollars for the token counts at the price of the one model the
    transcript names, or None: for a model without a price, and for a
    transcript that names several, whose tokens cannot be split among
    them from the totals. The four fields are priced apart, as Claude
    Code's own total_cost_usd prices them, so an estimate sits beside a
    claude-cli agent's cost on the same terms."""
    if len(models) != 1 or bare_model(models[0]) not in CLAUDE_PRICES:
        return None
    prices = CLAUDE_PRICES[bare_model(models[0])]
    return sum(tokens[name] * price for name, price in zip(TOKEN_FIELDS, prices)) / 1_000_000


def normalize_patch(text):
    """normalize_patch from _coop/runtime.py at 63b9d44: trim blank lines at
    the ends and end with exactly one newline, never strip(), since a blank
    context line inside a hunk is a lone space git apply needs."""
    if not text or not text.strip():
        return ""
    return text.lstrip("\n").rstrip("\n") + "\n"


def bare_model(model):
    """_strip_provider_prefix: anthropic/claude-x is claude-x to Claude Code."""
    return model.split("/", 1)[1] if "/" in model else model


def arm_is_builtin(arm_dir):
    """Whether the arm is one shipped under coordbench's own arms/, which
    the copy of coordbench into every container brings along."""
    return Path(arm_dir).resolve().parent == builtin_arms_dir().resolve()


def container_arm_dir(arm_dir):
    """Where the arm is in the agent's container: a built-in arm where the
    copy of coordbench put it; any other under a root of its own, named
    for its resolved path, so an arm from elsewhere that bears a built-in
    arm's name is never copied over the built-in one."""
    path = Path(arm_dir).resolve()
    if arm_is_builtin(path):
        return f"{CONTAINER_COORDBENCH}/arms/{path.name}"
    return f"{CONTAINER_ARMS}/{safe_name(path)}"


def read_container_file(container, path):
    result = docker("exec", container, "cat", path)
    return (result.stdout or "") if result.returncode == 0 else ""


def put_container_file(container, path, text):
    """Write a file in the container through stdin, so no text, however
    long or full of quotes, ever passes through a command line."""
    checked(docker("exec", "-i", container, "sh", "-c", f"cat > {shlex.quote(path)}", input=text),
            f"writing {path} in {container}")


def claude_flags(job):
    flags = ["--verbose", "--output-format=stream-json", "--permission-mode=bypassPermissions"]
    if job.arm_plugin:
        flags += ["--plugin-dir", container_arm_dir(job.arm_dir)]
    for plugin in job.plugins:
        flags += ["--plugin-dir", plugin]
    for url in job.plugin_urls:
        flags += ["--plugin-url", url]
    if job.mcp_servers:
        flags += ["--mcp-config", CONTAINER_MCP_CONFIG]
    if job.max_turns:
        flags += ["--max-turns", str(int(job.max_turns))]
    if job.disallowed_tools:
        # One comma-joined word, as the adapter passes its qwen profile's.
        flags += ["--disallowedTools", ",".join(job.disallowed_tools)]
    if job.agent_definition:
        flags += ["--agents", CONTAINER_AGENT_FILE, "--agent", job.agent_definition["name"]]
    return flags


def claude_script(job):
    """The adapter's command line, with the arm's plugin added. The prompt
    is read from a file, as the adapter reads it, so a long prompt neither
    meets argv limits nor needs escaping. The shell writes its pid to
    CONTAINER_PID_FILE and execs claude under coreutils timeout, so that
    pid is the timeout's: the time limit then holds inside the container
    however the docker client that started it ends, and stop_claude has a
    pid to signal, which timeout passes on to claude and follows with
    SIGKILL after KILL_GRACE_SECONDS. Debian slim and busybox both have
    timeout; the task images have no pkill."""
    limit = f"timeout -k {KILL_GRACE_SECONDS} {max(1, int(job.timeout_s))}"
    return ('export PATH="$HOME/.local/bin:$PATH"; '
            f"mkdir -p {CONTAINER_CLAUDE_CONFIG_DIR}; "
            f"echo $$ > {CONTAINER_PID_FILE}; "
            f"cd {CONTAINER_REPO} && exec {limit} claude {shlex.join(claude_flags(job))} "
            f'--print -- "$(cat {CONTAINER_PROMPT})" 2>&1')


def claude_environment(job):
    environment = {"CLAUDE_CONFIG_DIR": CONTAINER_CLAUDE_CONFIG_DIR, **CLAUDE_ENVIRONMENT}
    if job.model:
        environment = {"ANTHROPIC_MODEL": bare_model(job.model), **environment}
    return {**environment, **job.env}


def claude_exec_arguments(job):
    arguments = ["exec", "-w", CONTAINER_REPO]
    for name, value in claude_environment(job).items():
        # A secret in job.env would put its value on the command line, so
        # those names go by name below, or not at all.
        if name not in PASSED_BY_NAME and name not in HELD_BACK:
            arguments += ["-e", f"{name}={value}"]
    for name in PASSED_BY_NAME:
        if os.environ.get(name):
            arguments += ["-e", name]
    return arguments + [job.container, "bash", "-lc", claude_script(job)]


def stop_script():
    """The shell line that stops the agent: the pid claude_script left is
    the timeout's, and the shell builtin kill sends it SIGTERM, which it
    passes on to claude. A builtin because the Debian slim task images
    have no pkill (none installs procps); nothing is said when the file is
    missing or the process already gone."""
    return f'pid=$(cat {CONTAINER_PID_FILE} 2>/dev/null) && kill "$pid" 2>/dev/null; true'


def stop_claude(container):
    """Best effort, the second line behind the timeout claude runs under
    (claude_script): when the docker client that ran the agent is killed at
    the deadline, the agent in the container runs on until its own timeout
    fires, and in a container the runner keeps (--keep) nothing else would
    end it; otherwise teardown removes the container anyway."""
    try:
        docker("exec", container, "sh", "-c", stop_script(), timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


# How CliRun says the agent ran out the pair's time limit: the start of
# its end_reason, which run.py pair_finished and the Actions kit's
# artifacts.py read to keep such a pair rather than run it again.
TIMED_OUT = "timed out after "


class CliRun:
    """One headless Claude Code, on a thread of its own so that every agent
    of a run starts at the same moment."""

    def __init__(self, job):
        self.job = job
        self.problem = None
        self.ended_at = None
        self.thread = threading.Thread(target=self.run, name=f"coordbench-{job.agent}", daemon=True)

    def run(self):
        try:
            docker(*claude_exec_arguments(self.job), timeout=self.job.timeout_s, output=self.job.log_path)
        except subprocess.TimeoutExpired:
            self.problem = f"{TIMED_OUT}{self.job.timeout_s:.0f} seconds"
            stop_claude(self.job.container)
        except Exception as problem:  # the thread must report, never die silently
            self.problem = f"{type(problem).__name__}: {problem}"
        finally:
            self.ended_at = time.time()

    def outcome(self):
        log = Path(self.job.log_path)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        outcome = summarize_stream(text)
        if outcome.end_reason == NO_RESULT and text.strip():
            # Whatever ended the stream early (a refused sign-in, a container
            # that went away) said so last; keep it where a reader looks.
            outcome.end_reason += f"; its last line: {text.strip().splitlines()[-1][:300]}"
        if self.problem:
            # The adapter's exception, which CooperBench writes as the error.
            outcome.status, outcome.error, outcome.end_reason = "Error", self.problem, self.problem
        outcome.ended_at = self.ended_at
        outcome.patch = normalize_patch(read_container_file(self.job.container, f"{CONTAINER_REPO}/patch.txt"))
        return outcome


def log_sibling(log_path, suffix):
    """Another of an agent's files beside its stream log, named the way
    the stream log is (<agent>_stream.jsonl gives <agent><suffix>)."""
    path = Path(log_path)
    stem = path.name[:-len("_stream.jsonl")] if path.name.endswith("_stream.jsonl") else path.stem
    return path.with_name(f"{stem}{suffix}")


def save_session_transcript(job):
    """The session JSONL, which the adapter keeps beside the stream, every
    *.jsonl under the config's projects concatenated as the adapter
    concatenates them, subagents' transcripts included; written even when
    empty, as the adapter writes it, so a reader can tell "no transcript"
    from "not collected"."""
    found = docker("exec", job.container, "sh", "-c",
                   f"find {CONTAINER_CLAUDE_CONFIG_DIR}/projects -name '*.jsonl' -type f 2>/dev/null "
                   "| xargs -r cat")
    write_text(log_sibling(job.log_path, "_session.jsonl"), found.stdout if found.returncode == 0 else "")


# What config.json lists as each driver's departures from CooperBench's
# claude_code adapter at 63b9d44, so a comparison knows them without
# reading the code.
CLI_DIFFERENCES = (
    "on the default tool surface (mcp) the arm loads as a plugin (--plugin-dir), its extra plugins too, and "
    "arm.json's MCP servers through --mcp-config; the adapter loads none. tool_surface=bash loads no arm plugin, "
    "and for team only the cooperbench-team server the adapter registers",
    "MCP_TOOL_TIMEOUT=330000 in the agent's environment, so a long-polling coordination call is not cut at "
    "Claude Code's 60 s default",
    "claude runs under coreutils timeout inside the container, so the time limit holds without the docker client",
    "no install step: the task image carries the claude binary the overlay (overlay_task_image.sh) pinned to the "
    "host's version, where setup.sh installs @latest at run time",
    "no ANTHROPIC_BASE_URL or ANTHROPIC_AUTH_TOKEN forwarding, no _MODEL_PROFILES budgets and no settings.json; "
    "--disallowedTools only when --disallowed-tools asks for it",
)
SESSION_DIFFERENCES = (
    "the agent is a subagent of an orchestrating Claude Code session on the host, which picks its model",
    "isolation is advisory: the subagent's file tools reach the host, and only its instructions keep it to its "
    "workspace",
    "no stream and no result event: steps and durations are unknown; tokens are summed from the subagent's "
    "transcript and the cost is estimated from them",
)


class ClaudeCliDriver:
    name = "claude-cli"
    framework = "claude_code"
    host_workspace = False
    resumable = False
    measures_usage = True
    isolation = "container"
    differences_from_cooperbench = CLI_DIFFERENCES

    def start(self, job):
        put_container_file(job.container, CONTAINER_PROMPT, job.prompt)
        if job.mcp_servers:
            put_container_file(job.container, CONTAINER_MCP_CONFIG,
                               json.dumps({"mcpServers": job.mcp_servers}, indent=2))
        if job.agent_definition:
            put_container_file(job.container, CONTAINER_AGENT_FILE,
                               json.dumps(job.agent_definition["agents"], indent=2))
        run = CliRun(job)
        run.thread.start()
        return run

    def wait(self, run):
        run.thread.join()
        outcome = run.outcome()
        save_session_transcript(run.job)
        return outcome


def session_bin_dir(log_dir, agent):
    """Where the runner writes an agent's coordination wrappers on the host."""
    return Path(log_dir) / "bin" / safe_name(agent)


def session_prompt_path(job):
    return Path(job.log_path).parent / f"{safe_name(job.agent)}.session-prompt.md"


SESSION_PREFACE = """# coordbench: you are {agent} in run {run_id}

You are one agent of a coordbench run under the arm `{arm}`, working at
the same time as the other agents of the run on one CooperBench task pair.
You run on the host, but your work lives in a container, so the
benchmark's rules hold for you exactly as for an agent inside one.

- Begin your very first reply with this line, exactly as written:
  `{token}`. The benchmark finds your transcript by it, to count the
  tokens you used.
- Your workspace is `{workspace}` on this host. It is bind-mounted at
  `/workspace/repo` in your container `{container}`, so the two are the
  same files: read and edit them at `{workspace}` with your file tools.
  Wherever the task below says `/workspace/repo`, it means your workspace;
  `/workspace/repo/patch.txt` is `{workspace}/patch.txt`.
- Run every command in the container, never on the host, so that git, the
  build and the tests see the task's own environment:

      docker exec -w /workspace/repo {container} bash -lc '<command>'

  For example, to run the tests or write your patch:
  `docker exec -w /workspace/repo {container} bash -lc 'git diff > patch.txt'`.
- Your coordination commands are wrappers in `{bin_dir}`; run them on the
  host by path, as `{bin_dir}/<command> <words>`, and `ls {bin_dir}` lists
  them. Each shell command the task below names is there under that name,
  and each MCP tool it names is there as `coord-<tool name>`, which takes
  key=value words or one JSON object. If the directory is empty, this arm
  gives you no coordination tools.
- Do not read or change the other agents' workspaces (the other
  directories under `{workspaces}`) or their containers. What the others
  do reaches you only through the coordination commands, and through the
  shared git remote when the task below names one, as in the benchmark.
- As the very last step, after `patch.txt` is written, create the file
  `{marker}` (for example `touch {marker}`). Your run ends when it
  appears; nothing you do after that counts."""


def session_preface(job):
    workspace = Path(job.workspace)
    return SESSION_PREFACE.format(
        agent=job.agent, run_id=job.run_id, arm=Path(job.arm_dir).name, workspace=workspace,
        container=job.container, bin_dir=session_bin_dir(Path(job.log_path).parent, job.agent),
        workspaces=workspace.parent, marker=workspace / DONE_MARKER, token=run_token(job.run_id, job.agent))


class SessionDriver:
    name = "session"
    framework = "claude_code_subagent"
    host_workspace = True
    resumable = True
    # A subagent leaves no stream on the host, so its steps and durations
    # are unknown, and result.json says so rather than count zero; its
    # tokens, and from them an estimated cost, come from its transcript
    # when wait() finds it (the outcome's own `measured` says so per
    # agent); the agent's wall time the runner measures itself, in
    # result.json's timings.
    measures_usage = False
    isolation = "advisory"
    differences_from_cooperbench = SESSION_DIFFERENCES

    def __init__(self, poll_seconds=10.0, settle_seconds=5.0, transcript_roots=None):
        self.poll_seconds = poll_seconds
        # The subagent may still be writing its last reply when its marker
        # appears; the copy waits until the transcript has been still for
        # this long (at most SETTLE_LIMIT_SECONDS).
        self.settle_seconds = settle_seconds
        self.transcript_roots = transcript_roots

    def start(self, job):
        if not job.workspace:
            raise ValueError(f"the session driver needs a host workspace for {job.agent}")
        prompt_file = session_prompt_path(job)
        write_text(prompt_file, session_preface(job) + SECTION_RULE + job.prompt)
        (Path(job.workspace) / DONE_MARKER).unlink(missing_ok=True)
        started = time.time()
        return {"agent": job.agent, "workspace": str(job.workspace), "log_path": str(job.log_path),
                "prompt_file": str(prompt_file), "timeout_s": job.timeout_s, "started": started,
                "deadline": started + job.timeout_s, "run_token": run_token(job.run_id, job.agent),
                "model": job.model}

    def wait(self, handle):
        """Look for the marker every poll_seconds until it is there or the
        deadline passes; the last look comes after the last sleep, so a
        marker that appeared as the deadline passed is still found. Then
        the subagent's transcript is found by its run token, copied and
        counted (take_transcript)."""
        marker = Path(handle["workspace"]) / DONE_MARKER
        found = poll_marker(handle["agent"], marker)
        while not found and time.time() < handle["deadline"]:
            time.sleep(max(0.0, min(self.poll_seconds, handle["deadline"] - time.time())))
            found = poll_marker(handle["agent"], marker)
        outcome = AgentOutcome(status="Submitted", measured=())
        if not found and handle.get("lost"):
            # The orchestrating session lost the subagent (lane.py finish
            # --dead): no time limit passed, so none is named.
            reason = "the harness lost the subagent before it made its marker"
            outcome = AgentOutcome(status="Error", error=reason, end_reason=reason, measured=())
        elif not found:
            minutes = handle.get("timeout_s", 0) / 60
            reason = f"no {DONE_MARKER} within {minutes:.0f} minutes"
            # The adapter's own timeout is an exception, which it writes as the error.
            outcome = AgentOutcome(status="Error", error=reason, end_reason=reason, measured=())
        else:
            outcome.ended_at = marker.stat().st_mtime
        patch = Path(handle["workspace"]) / "patch.txt"
        if patch.exists():
            outcome.patch = normalize_patch(patch.read_text(encoding="utf-8", errors="replace"))
        transcript = self.take_transcript(handle, outcome)
        record_session_outcome(handle, outcome, transcript)
        return outcome

    def take_transcript(self, handle, outcome):
        """Copy the subagent's transcript to <agent>_session.jsonl and fill
        the outcome's tokens, model and estimated cost from it; what was
        found, as session_outcome.json records it. A handle from before
        the run token (an older plan) has nothing to look for."""
        token = handle.get("run_token")
        if not token:
            return {"source": None, "matches": 0, "note": "the plan predates the run token"}
        roots = self.transcript_roots if self.transcript_roots is not None else transcript_roots()
        since = handle.get("started", 0) - TRANSCRIPT_CLOCK_SLACK_SECONDS
        matches = find_transcripts(token, roots, since, handle.get("prompt_file"))
        if not matches:
            return {"source": None, "matches": 0, "note": "no subagent transcript carried the run token"}
        source = matches[0]
        settle(source, self.settle_seconds)
        text = source.read_text(encoding="utf-8", errors="replace")
        write_text(log_sibling(handle["log_path"], "_session.jsonl"), text)
        fill_from_transcript(outcome, transcript_usage(text))
        return {"source": str(source), "matches": len(matches),
                "matched_by": carries_token(source, token, handle.get("prompt_file"))}


def run_token(run_id, agent):
    """The line a session subagent opens its first reply with: distinctive
    enough to be found in no other transcript, and naming run and agent."""
    return f"coordbench-run-token {run_id} {agent}"


def fill_from_transcript(outcome, usage):
    """The input and cache counts are measured once the transcript is in
    hand; the output count only when every message's final count is in it
    (transcript_usage). The cost is an estimate from all four, and stays
    unknown when the model has no price or the output count is not final;
    then the output sum and the cost it gives are kept as lower bounds."""
    for name in TOKEN_FIELDS:
        setattr(outcome, name, usage["tokens"][name])
    outcome.model = usage["models"][0] if usage["models"] else None
    outcome.claude_code_version = usage["claude_code_version"]
    cost = estimated_cost(usage["models"], usage["tokens"])
    measured = [name for name in TOKEN_FIELDS if name != "output_tokens" or usage["output_final"]]
    if not usage["output_final"]:
        outcome.output_tokens_lower_bound, outcome.cost_lower_bound = usage["tokens"]["output_tokens"], cost
    elif cost is not None:
        outcome.cost, outcome.cost_source = cost, "estimated from tokens"
        measured.insert(0, "cost")
    outcome.measured = tuple(measured)


# Where Claude Code keeps transcripts, and how far a subagent's may have
# been touched before the run's own start as the host clock reads it.
TRANSCRIPT_CLOCK_SLACK_SECONDS = 60
# How many lines of a transcript to read looking for the run token: the
# first reply comes within the first few lines, after the prompt.
TOKEN_SEARCH_LINES = 200
SETTLE_LIMIT_SECONDS = 30


def transcript_roots():
    """The projects directories of the Claude Code that runs the
    orchestrating session: CLAUDE_CONFIG_DIR's when set, and the user's."""
    roots = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
    roots.append(Path.home() / ".claude" / "projects")
    return [root for i, root in enumerate(roots) if root.is_dir() and root not in roots[:i]]


def find_transcripts(token, roots, since, prompt_file=None):
    """The subagent transcripts touched since `since` that are this
    agent's (carries_token), those whose reply carries the token first,
    then those whose prompt does, then those whose prompt names the
    agent's own prompt file, then those that lost their prompt, each
    newest first. Claude Code keeps a
    subagent's transcript under <project>/<session>/subagents/."""
    candidates = []
    for root in roots:
        for path in Path(root).rglob("*.jsonl"):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if "subagents" in path.parts and modified >= since:
                candidates.append((modified, path))
    found = [(carries_token(path, token, prompt_file), modified, path) for modified, path in candidates]
    found = [entry for entry in found if entry[0]]
    found.sort(key=lambda entry: (MATCH_ORDER.index(entry[0]), -entry[1]))
    return [path for _, _, path in found]


# How a transcript was found to be the agent's, most certain first;
# "headless" (carries_token) is taken only when nothing else is.
MATCH_ORDER = ("reply", "prompt", "named", "headless")


# A run token as run_token writes it; the agent's name ends on a letter,
# digit, hyphen or underscore, so a full stop after it is not read as its.
RUN_TOKEN = re.compile(r"coordbench-run-token (\S+) ([A-Za-z0-9_.-]*[A-Za-z0-9_-])")


def carries_token(path, token, prompt_file=None):
    """Where the transcript carries the agent's token, "reply" or "prompt",
    "named" when only its prompt names the agent's own prompt file,
    "headless" (below) when it lost its prompt, or None when it is not
    the agent's. The prompt is every user line before the first reply (a
    Workflow subagent gets the harness's request first and its pasted
    task second); a reply counts by its text blocks only, never a tool
    call's input, where an orchestrator that launched the agents would
    have pasted their prompts. A transcript that holds
    another agent's token of the same run anywhere in what is read is
    such an orchestrator's, and none of the agents'.

    "named" is for an agent that opened its prompt file with a tool call
    and never wrote the token in a text reply (six of 72 agents in the
    curse-1 step; one echoed it only through a shell command): its prompt
    names the file, and an orchestrator's prompt never does. The file's
    path is the pair's, shared by every attempt of the pair, so a
    transcript whose prompt or tool results carry any other run token is
    an earlier attempt's agent or some other run's, and is "named" for
    none. So is one whose reply carries another token, unless a tool
    result holds this one: what an agent read decides over what it typed,
    since one solo agent of the X-Blossom screen opened its reply with its
    token short of the run id's suffix, though the prompt file it had just
    read back held the token whole.

    With a prompt file given, every match but a headless one needs a
    prompt that names it, which keeps out a review agent whose prompt
    quoted the token.
    "headless" is a transcript that does not open on its prompt: a disk
    that was full as the agent was spawned dropped the first lines and
    kept the rest (one relay of the curse-1 jinja 1559 run did so). Its
    prompt cannot be read, so it is the agent's by an exact token in a
    reply alone, and last of all, since a relay that echoed the token
    in a reply would match the same way."""
    events = opening_events(path)
    found, replied, prompt = None, False, []
    read_own = read_foreign = typed_foreign = False
    for kind, message in ((event["type"], event["message"]) for event in events):
        text = content_blocks_to_text(message.get("content"))
        if names_another_agent(text, token):
            return None
        if kind == "assistant":
            replied = True
            found = "reply" if holds_token(reply_text(message), token) else found
            typed_foreign = typed_foreign or carries_other_token(text, token)
            continue
        if not replied:
            prompt.append(text)
            found = found or ("prompt" if holds_token(text, token) else None)
        else:
            read_own = read_own or holds_token(text, token)
        read_foreign = read_foreign or carries_other_token(text, token)
    if not events or not opens_on_its_prompt(events[0]):
        return "headless" if found == "reply" else None
    if prompt_file and not names_file(" ".join(prompt), prompt_file):
        return None
    if found or not prompt_file or read_foreign or (typed_foreign and not read_own):
        return found
    return "named"


def carries_other_token(text, token):
    """Whether the text holds a run token that is not this one."""
    wanted = RUN_TOKEN.fullmatch(token)
    own = wanted.groups() if wanted else None
    return any(found != own for found in RUN_TOKEN.findall(text))


def names_file(text, path):
    """Whether the text names the path whole: not as the tail of a longer
    path and not as the head of a longer name (<file>.old); a full stop
    that ends a sentence may follow it."""
    pattern = r"(?<![\w./-])" + re.escape(str(path)) + r"(?![\w-]|\.\w)"
    return re.search(pattern, text) is not None


def opening_messages(path):
    """The (type, message) of the transcript's user and assistant lines
    among its first TOKEN_SEARCH_LINES, where the prompt and the first
    replies are; empty when the file cannot be read."""
    return [(event["type"], event["message"]) for event in opening_events(path)]


def opening_events(path):
    """The transcript's user and assistant lines among its first
    TOKEN_SEARCH_LINES, whole; empty when the file cannot be read."""
    found = []
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for number, line in enumerate(stream):
                if number >= TOKEN_SEARCH_LINES:
                    break
                event = next(json_lines(line), None)
                message = event.get("message") if isinstance(event, dict) else None
                if isinstance(message, dict) and event.get("type") in ("user", "assistant"):
                    found.append(event)
    except OSError:
        return []
    return found


def opens_on_its_prompt(event):
    """Whether a transcript's first user or assistant line is its prompt:
    a user line with no parent, as Claude Code writes the first line of
    every subagent's transcript (660 of 661 on this host on 2026-10-15;
    the other lost its prompt to a full disk)."""
    return event.get("type") == "user" and event.get("parentUuid") is None


def reply_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(str(block.get("text") or "") for block in content or ()
                     if isinstance(block, dict) and block.get("type") == "text")


def holds_token(text, token):
    """Whether the text holds the token itself: a run token by its run and
    agent exactly, so agent1's is not found in agent10's; any other token
    as it is written."""
    wanted = RUN_TOKEN.fullmatch(token)
    if wanted is None:
        return token in text
    return wanted.groups() in RUN_TOKEN.findall(text)


def names_another_agent(text, token):
    wanted = RUN_TOKEN.fullmatch(token)
    if wanted is None:
        return False
    run, agent = wanted.groups()
    return any(found_run == run and found_agent != agent for found_run, found_agent in RUN_TOKEN.findall(text))


def settle(path, quiet_seconds, poll=0.5):
    """Wait until the file has not changed for quiet_seconds, at most
    SETTLE_LIMIT_SECONDS: a copy taken while the subagent still writes
    its last reply would miss that reply's tokens."""
    deadline = time.time() + min(quiet_seconds * 6, SETTLE_LIMIT_SECONDS)
    while quiet_seconds > 0 and time.time() < deadline:
        try:
            still = time.time() - Path(path).stat().st_mtime
        except OSError:
            return
        if still >= quiet_seconds:
            return
        time.sleep(min(poll, quiet_seconds - still))


def poll_marker(agent, marker):
    """One look for the agent's marker; in debug each look is a
    "marker_poll" event, so a trace shows how long finish waited and how
    often it asked."""
    found = marker.exists()
    trace.debug("marker_poll", agent=agent, found=found)
    return found


def record_session_outcome(handle, outcome, transcript):
    """How the subagent ended, in <agent>_session_outcome.json beside its
    other logs: a subagent leaves no stream, and a line in a file named
    <agent>_stream.jsonl would be read by CooperBench's stream parser as a
    stream without a result event, an Error."""
    entry = {"type": "coordbench_session", "agent": handle["agent"], "status": outcome.status,
             "error": outcome.error, "end_reason": outcome.end_reason, "patch_lines": len(outcome.patch.splitlines()),
             "at": time.time(), "ended_at": outcome.ended_at, "run_token": handle.get("run_token"),
             "transcript": transcript,
             "model_requested": handle.get("model") or None, "model": outcome.model,
             "claude_code_version": outcome.claude_code_version,
             "tokens": {name: getattr(outcome, name) for name in TOKEN_FIELDS if name in (outcome.measured or ())},
             "cost": outcome.cost if outcome.cost_source else None, "cost_source": outcome.cost_source,
             "output_tokens_lower_bound": outcome.output_tokens_lower_bound,
             "cost_lower_bound": outcome.cost_lower_bound}
    write_text(log_sibling(handle["log_path"], "_session_outcome.json"), json.dumps(entry, indent=2) + "\n")


DRIVERS = {"claude-cli": ClaudeCliDriver, "session": SessionDriver}


def load_driver(spec):
    """A built-in driver by name, or one's own as path/to/file.py:ClassName."""
    if spec in DRIVERS:
        return DRIVERS[spec]()
    path, colon, class_name = spec.rpartition(":")
    if not colon or not path.endswith(".py") or not Path(path).is_file():
        raise ValueError(f"no driver {spec!r}: name one of {', '.join(DRIVERS)} or give path/to/file.py:ClassName")
    module_spec = importlib.util.spec_from_file_location(f"coordbench_driver_{safe_name(Path(path).stem)}", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return getattr(module, class_name)()
