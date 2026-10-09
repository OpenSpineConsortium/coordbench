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
  skill rides along. Usage and turns come from the stream's final result
  event, parsed as the adapter's parsers.py parses it.
- SessionDriver hands the agent to a Claude Code session's own subagents,
  which run on the host. The agent's repository is a host directory
  bind-mounted at /workspace/repo in its container, so the subagent edits
  it with its own tools and runs every command in the container; the
  coordination commands are wrappers on the host. start() writes the
  prompt the orchestrating session gives its subagent, and wait() watches
  for the marker the prompt asks the subagent to leave last. Because its
  handle is plain JSON, the wait can happen in another process (run.py's
  prepare and finish).

A driver of one's own is any class with start() and wait(); run.py loads
it from path/to/file.py:ClassName. Class attributes tell the runner what
the driver wants: `host_workspace` (the repository as a host directory),
`resumable` (start and wait may run in different processes), and
`framework` (the agent_framework recorded in result.json).

Every docker command goes through docker() below, so tests swap in a fake.
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

CONTAINER_REPO = "/workspace/repo"
CONTAINER_COORDBENCH = "/opt/coordbench"
CONTAINER_PLUGINS = "/opt/coordbench-plugins"
CONTAINER_PROMPT = "/tmp/coord-prompt.txt"
CONTAINER_MCP_CONFIG = "/tmp/coord-mcp.json"
# The adapter keeps Claude Code's state under /tmp, so the session
# transcript is always in one known place after the run.
CONTAINER_CLAUDE_CONFIG_DIR = "/tmp/claude-cfg"
DONE_MARKER = ".coordbench-done"

# Secrets reach the agent by name only: `docker exec -e NAME` makes the
# docker client copy the value from its own environment, so the value is
# never on a command line, in a file or in a log.
PASSED_BY_NAME = ("CLAUDE_CODE_OAUTH_TOKEN",)
# The hub's admin token is the runner's alone: with it an agent could
# create or close runs. It never reaches a container, nor even the docker
# client, so no `-e` added by mistake could hand it on.
ADMIN_TOKEN = "COORD_ADMIN_TOKEN"
HELD_BACK = (ADMIN_TOKEN,)

# The adapter's own exports beside the model and the config directory
# (_build_claude_command), kept so the agent runs as the authors' did.
CLAUDE_ENVIRONMENT = {
    "IS_SANDBOX": "1",
    "FORCE_AUTO_BACKGROUND_TASKS": "1",
    "ENABLE_BACKGROUND_TASKS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

SECTION_RULE = "\n\n---\n\n"
NO_RESULT = "the stream ended without a result event"
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def docker(*args, input=None, timeout=None, output=None):
    """Run `docker <args>` and return the CompletedProcess, whatever its exit
    code; the caller decides what a failure means. With `output`, a path,
    stdout and stderr go to that file as they arrive instead of being
    captured, which is how a long agent run is teed to its log."""
    command = ["docker", *(str(arg) for arg in args)]
    environment = docker_environment()
    if output is not None:
        with open(output, "w", encoding="utf-8") as stream:
            return subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=timeout, env=environment)
    text = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": timeout,
            "env": environment}
    if input is None:
        return subprocess.run(command, stdin=subprocess.DEVNULL, **text)
    return subprocess.run(command, input=input, **text)


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
    mcp_servers: dict = field(default_factory=dict)
    model: str = ""
    timeout_s: float = 7200.0
    max_turns: int | None = None
    log_path: str = ""
    workspace: str = ""


@dataclass
class AgentOutcome:
    """How an agent ended, in CooperBench's terms: status is Submitted,
    LimitsExceeded or Error. `patch` is the agent's patch.txt."""

    status: str = "Error"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    steps: int = 0
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


def summarize_stream(text):
    """Usage, turns and status from Claude Code's stream-json, read from the
    first result event as parse_stream_json reads it. A stream without one
    (the CLI crashed or was killed) is an Error with nothing counted."""
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


def container_arm_dir(arm_dir):
    return f"{CONTAINER_COORDBENCH}/arms/{Path(arm_dir).name}"


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
    if job.mcp_servers:
        flags += ["--mcp-config", CONTAINER_MCP_CONFIG]
    if job.max_turns:
        flags += ["--max-turns", str(int(job.max_turns))]
    return flags


def claude_script(job):
    """The adapter's command line, with the arm's plugin added. The prompt
    is read from a file, as the adapter reads it, so a long prompt neither
    meets argv limits nor needs escaping."""
    return ('export PATH="$HOME/.local/bin:$PATH"; '
            f"mkdir -p {CONTAINER_CLAUDE_CONFIG_DIR}; "
            f"cd {CONTAINER_REPO} && claude {shlex.join(claude_flags(job))} "
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


def stop_claude(container):
    """Best effort: a timed-out agent must not keep working in a container
    the runner keeps (--keep); otherwise teardown removes it anyway."""
    try:
        docker("exec", container, "sh", "-c", "pkill -f 'claude --verbose' 2>/dev/null; true", timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


class CliRun:
    """One headless Claude Code, on a thread of its own so that every agent
    of a run starts at the same moment."""

    def __init__(self, job):
        self.job = job
        self.problem = None
        self.thread = threading.Thread(target=self.run, name=f"coordbench-{job.agent}", daemon=True)

    def run(self):
        try:
            docker(*claude_exec_arguments(self.job), timeout=self.job.timeout_s, output=self.job.log_path)
        except subprocess.TimeoutExpired:
            self.problem = f"timed out after {self.job.timeout_s:.0f} seconds"
            stop_claude(self.job.container)
        except Exception as problem:  # the thread must report, never die silently
            self.problem = f"{type(problem).__name__}: {problem}"

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
        outcome.patch = normalize_patch(read_container_file(self.job.container, f"{CONTAINER_REPO}/patch.txt"))
        return outcome


def save_session_transcript(job):
    """The session JSONL, which the adapter also keeps beside the stream."""
    found = docker("exec", job.container, "sh", "-c",
                   f"find {CONTAINER_CLAUDE_CONFIG_DIR}/projects -name '*.jsonl' -type f 2>/dev/null "
                   "| xargs -r cat")
    if found.returncode == 0 and found.stdout:
        Path(job.log_path).with_name(f"{safe_name(job.agent)}_session.jsonl").write_text(
            found.stdout, encoding="utf-8")


class ClaudeCliDriver:
    name = "claude-cli"
    framework = "claude_code"
    host_workspace = False
    resumable = False

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

    def __init__(self, poll_seconds=10.0):
        self.poll_seconds = poll_seconds

    def start(self, job):
        if not job.workspace:
            raise ValueError(f"the session driver needs a host workspace for {job.agent}")
        prompt_file = session_prompt_path(job)
        prompt_file.write_text(session_preface(job) + SECTION_RULE + job.prompt, encoding="utf-8")
        (Path(job.workspace) / DONE_MARKER).unlink(missing_ok=True)
        return {"agent": job.agent, "workspace": str(job.workspace), "log_path": str(job.log_path),
                "prompt_file": str(prompt_file), "timeout_s": job.timeout_s,
                "deadline": time.time() + job.timeout_s}

    def wait(self, handle):
        marker = Path(handle["workspace"]) / DONE_MARKER
        while not marker.exists() and time.time() < handle["deadline"]:
            time.sleep(max(0.0, min(self.poll_seconds, handle["deadline"] - time.time())))
        outcome = AgentOutcome(status="Submitted")
        if not marker.exists():
            minutes = handle.get("timeout_s", 0) / 60
            outcome = AgentOutcome(status="Error", error=f"no {DONE_MARKER} within {minutes:.0f} minutes")
        patch = Path(handle["workspace"]) / "patch.txt"
        if patch.exists():
            outcome.patch = normalize_patch(patch.read_text(encoding="utf-8", errors="replace"))
        record_session_outcome(handle, outcome)
        return outcome


def record_session_outcome(handle, outcome):
    """A subagent leaves no stream on the host; its log says how it ended."""
    entry = {"type": "coordbench_session", "agent": handle["agent"], "status": outcome.status,
             "error": outcome.error, "patch_lines": len(outcome.patch.splitlines()), "at": time.time()}
    with open(handle["log_path"], "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")


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
