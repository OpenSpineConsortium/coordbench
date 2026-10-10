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
  prepare and finish). It sees no stream, so it measures no usage and
  knows no duration; result.json holds null for both.

A driver of one's own is any class with start() and wait(); run.py loads
it from path/to/file.py:ClassName. Class attributes tell the runner what
the driver wants: `host_workspace` (the repository as a host directory),
`resumable` (start and wait may run in different processes), `framework`
(the agent_framework recorded in result.json) and `measures_usage`
(whether the outcome's token counts, steps and cost were measured; a
driver without the attribute is taken not to measure them, and
result.json then holds null in their place rather than zeros).

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
HELD_BACK = (ADMIN_TOKEN, AGENT_TOKEN)

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
    plugins: list = field(default_factory=list)
    # Plugin .zip URLs, handed to Claude Code as they are (--plugin-url).
    plugin_urls: list = field(default_factory=list)
    mcp_servers: dict = field(default_factory=dict)
    model: str = ""
    timeout_s: float = 7200.0
    max_turns: int | None = None
    log_path: str = ""
    workspace: str = ""


@dataclass
class AgentOutcome:
    """How an agent ended, in CooperBench's terms: status is Submitted,
    LimitsExceeded or Error; cost is the result event's total_cost_usd.
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
    finish."""

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
    patch: str = ""


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


def summarize_stream(text):
    """Usage, cost, turns, status and the two durations from Claude Code's
    stream-json, read from the first result event as parse_stream_json
    reads it. A stream without one (the CLI crashed or was killed) is an
    Error with nothing counted and no duration known."""
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
            error=None if status == "Submitted" else str(event.get("result") or event.get("subtype"))[:500],
        )
    return AgentOutcome(status="Error", error=NO_RESULT)


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
    flags = ["--verbose", "--output-format=stream-json", "--permission-mode=bypassPermissions",
             "--plugin-dir", container_arm_dir(job.arm_dir)]
    for plugin in job.plugins:
        flags += ["--plugin-dir", plugin]
    for url in job.plugin_urls:
        flags += ["--plugin-url", url]
    if job.mcp_servers:
        flags += ["--mcp-config", CONTAINER_MCP_CONFIG]
    if job.max_turns:
        flags += ["--max-turns", str(int(job.max_turns))]
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
            self.problem = f"timed out after {self.job.timeout_s:.0f} seconds"
            stop_claude(self.job.container)
        except Exception as problem:  # the thread must report, never die silently
            self.problem = f"{type(problem).__name__}: {problem}"
        finally:
            self.ended_at = time.time()

    def outcome(self):
        log = Path(self.job.log_path)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        outcome = summarize_stream(text)
        if outcome.error == NO_RESULT and text.strip():
            # Whatever ended the stream early (a refused sign-in, a container
            # that went away) said so last; keep it where a reader looks.
            outcome.error += f"; its last line: {text.strip().splitlines()[-1][:300]}"
        if self.problem:
            outcome.status, outcome.error = "Error", self.problem
        outcome.ended_at = self.ended_at
        outcome.patch = normalize_patch(read_container_file(self.job.container, f"{CONTAINER_REPO}/patch.txt"))
        return outcome


def save_session_transcript(job):
    """The session JSONL, which the adapter also keeps beside the stream."""
    found = docker("exec", job.container, "sh", "-c",
                   f"find {CONTAINER_CLAUDE_CONFIG_DIR}/projects -name '*.jsonl' -type f 2>/dev/null "
                   "| xargs -r cat")
    if found.returncode == 0 and found.stdout:
        write_text(Path(job.log_path).with_name(f"{safe_name(job.agent)}_session.jsonl"), found.stdout)


class ClaudeCliDriver:
    name = "claude-cli"
    framework = "claude_code"
    host_workspace = False
    resumable = False
    measures_usage = True

    def start(self, job):
        put_container_file(job.container, CONTAINER_PROMPT, job.prompt)
        if job.mcp_servers:
            put_container_file(job.container, CONTAINER_MCP_CONFIG,
                               json.dumps({"mcpServers": job.mcp_servers}, indent=2))
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
        workspaces=workspace.parent, marker=workspace / DONE_MARKER)


class SessionDriver:
    name = "session"
    framework = "claude_code_subagent"
    host_workspace = True
    resumable = True
    # A subagent leaves no stream on the host, so its tokens, steps, cost
    # and durations are unknown, and result.json says so rather than
    # count zero; the agent's wall time the runner measures itself, in
    # result.json's timings.
    measures_usage = False

    def __init__(self, poll_seconds=10.0):
        self.poll_seconds = poll_seconds

    def start(self, job):
        if not job.workspace:
            raise ValueError(f"the session driver needs a host workspace for {job.agent}")
        prompt_file = session_prompt_path(job)
        write_text(prompt_file, session_preface(job) + SECTION_RULE + job.prompt)
        (Path(job.workspace) / DONE_MARKER).unlink(missing_ok=True)
        return {"agent": job.agent, "workspace": str(job.workspace), "log_path": str(job.log_path),
                "prompt_file": str(prompt_file), "timeout_s": job.timeout_s,
                "deadline": time.time() + job.timeout_s}

    def wait(self, handle):
        """Look for the marker every poll_seconds until it is there or the
        deadline passes; the last look comes after the last sleep, so a
        marker that appeared as the deadline passed is still found."""
        marker = Path(handle["workspace"]) / DONE_MARKER
        found = poll_marker(handle["agent"], marker)
        while not found and time.time() < handle["deadline"]:
            time.sleep(max(0.0, min(self.poll_seconds, handle["deadline"] - time.time())))
            found = poll_marker(handle["agent"], marker)
        outcome = AgentOutcome(status="Submitted")
        if not found:
            minutes = handle.get("timeout_s", 0) / 60
            outcome = AgentOutcome(status="Error", error=f"no {DONE_MARKER} within {minutes:.0f} minutes")
        else:
            outcome.ended_at = marker.stat().st_mtime
        patch = Path(handle["workspace"]) / "patch.txt"
        if patch.exists():
            outcome.patch = normalize_patch(patch.read_text(encoding="utf-8", errors="replace"))
        record_session_outcome(handle, outcome)
        return outcome


def poll_marker(agent, marker):
    """One look for the agent's marker; in debug each look is a
    "marker_poll" event, so a trace shows how long finish waited and how
    often it asked."""
    found = marker.exists()
    trace.debug("marker_poll", agent=agent, found=found)
    return found


def record_session_outcome(handle, outcome):
    """A subagent leaves no stream on the host; its log says how it ended."""
    entry = {"type": "coordbench_session", "agent": handle["agent"], "status": outcome.status,
             "error": outcome.error, "patch_lines": len(outcome.patch.splitlines()), "at": time.time()}
    write_text(handle["log_path"], json.dumps(entry) + "\n", append=True)


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
