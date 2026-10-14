"""The runner and the drivers are what turn an arm into scored CooperBench
results, so these pin what they do to docker, to the hub and to the logs.
Docker is a fake that records every command and answers the few whose
output the runner reads; the hub is the real one, in this process on an
ephemeral port; CooperBench's scorer is a fake with the shape of its
results. Nothing here starts a container or a Claude."""

import bz2
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import os
import random
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
import zipfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py shares its name with the standard library's tracer;
# a process that happened to load that one first gets ours here instead.
if getattr(sys.modules.get("trace"), "__file__", None) != str(COORDBENCH / "trace.py"):
    sys.modules.pop("trace", None)
import trace  # noqa: E402
import drivers  # noqa: E402
import hub as hub_module  # noqa: E402
import run as runner  # noqa: E402

REAL_COOPERBENCH_TEST = runner.cooperbench_test
REAL_DOCKER = drivers.docker
REAL_START_HUB = runner.start_hub

# Distinctive, so a leak is found by a plain search, and shaped like no
# real credential, so the repository's own secret check lets them be.
SECRET = "coordbench-fake-oauth-token-7f3a9c2e51d0"
ADMIN_SECRET = "coordbench-fake-admin-token-4b8d1e6a90c3"
# What the scrub tests plant in a pair's files in the token's place, so
# they can see it found and replaced: a stand-in, not a credential, and
# named for what it is, since a scanner reads a name like SECRET written to
# a file as a leak.
PLANTED = "coordbench-planted-stand-in-7f3a9c2e51d0"
AGENT_SECRET = "coordbench-fake-agent-token-2c7e5a1f93b6"
BASE = "0123456789abcdef0123456789abcdef01234567"
GIT_EMAIL = "agent@cooperbench.local"
HAS_PROC = Path("/proc/self/environ").exists()


def cooperbench_coop_module(name):
    """A file of CooperBench's _coop package (runtime.py, prompt.py), loaded
    on its own from the checkout COOPERBENCH_DIR names, or ~/cooperbench:
    the package's __init__ pulls in the whole runner, the files need only
    the standard library."""
    for root in filter(None, [os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")]):
        path = Path(root) / "src" / "cooperbench" / "agents" / "_coop" / f"{name}.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location(f"cooperbench_coop_{name}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


COOPERBENCH_RUNTIME = cooperbench_coop_module("runtime")
COOPERBENCH_PROMPT = cooperbench_coop_module("prompt")
REPO, TASK = "pallets_click_task", 2068
IMAGE = "akhatua/cooperbench-pallets-click:task2068"
PATCHES = {
    "agent1": "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-a\n+A\n",
    "agent2": "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-b\n+B\n",
}
SAMPLE_STREAM = "\n".join(json.dumps(event) for event in [
    {"type": "system", "subtype": "init", "session_id": "s1", "tools": ["Bash", "mcp__coordbench__send_message"],
     "mcp_servers": [{"name": "coordbench", "status": "connected"}], "model": "claude-test",
     "claude_code_version": "2.1.300"},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Working."}]}},
    "not json at all",
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 12, "total_cost_usd": 0.42,
     "duration_ms": 95000, "duration_api_ms": 61000,
     "result": "Done.", "usage": {"input_tokens": 1200, "output_tokens": 340,
                                  "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 7000}},
]) + "\n"
MERGED_RESULT = {
    "apply_status": {"agent1": "clean", "agent2": "clean"},
    "merge": {"status": "clean", "strategy": "naive", "diff": ""},
    "feature1": {"feature_id": 1, "passed": True, "tests_passed": 3, "tests_failed": 0, "test_output": "ok"},
    "feature2": {"feature_id": 6, "passed": True, "tests_passed": 2, "tests_failed": 0, "test_output": "ok"},
    "both_passed": True, "error": None,
}
SOLO_RESULT = {"setting": "solo", "patch_lines": 6, "feature1": {"passed": True, "test_output": "ok"},
               "feature2": {"passed": False, "test_output": "1 failed"}, "both_passed": False, "error": None}
# CooperBench's coop.py writes these, in this order (coop.py:210-241 at
# 63b9d44); solo.py writes "agent" for "agents" and neither
# messages_sent nor anything after total_steps but log_dir; team.py adds
# lead_agent before the agents and metrics and team_features after.
COOPERBENCH_COOP_KEYS = ["repo", "task_id", "features", "setting", "run_id", "run_name", "agent_framework", "model",
                         "started_at", "ended_at", "duration_seconds", "agents", "total_cost", "total_steps",
                         "messages_sent", "log_dir"]
COOPERBENCH_SOLO_KEYS = ["repo", "task_id", "features", "setting", "run_id", "run_name", "agent_framework", "model",
                         "started_at", "ended_at", "duration_seconds", "agent", "total_cost", "total_steps", "log_dir"]
# What coordbench adds after them.
COORDBENCH_RESULT_KEYS = {"arm", "driver", "agents_seconds", "isolation", "options", "agent_models", "max_turns",
                          "error_kind",                          "timeout_s", "disallowed_tools", "provenance", "image", "metrics", "timings",
                          "utc_offset_seconds"}
RESULT_KEYS = set(COOPERBENCH_COOP_KEYS) | COORDBENCH_RESULT_KEYS
# coop.py's per-agent fields in its order, then coordbench's.
COOPERBENCH_AGENT_KEYS = ["feature_id", "status", "cost", "steps", "input_tokens", "output_tokens",
                          "cache_read_tokens", "cache_write_tokens", "patch_lines", "error"]
COORDBENCH_AGENT_KEYS = {"role", "duration_ms", "api_duration_ms", "end_reason", "model_requested", "model",
                         "model_differs", "claude_code_version", "tools", "mcp_servers", "cost_source",
                         "output_tokens_lower_bound", "cost_lower_bound"}
AGENT_KEYS = set(COOPERBENCH_AGENT_KEYS) | COORDBENCH_AGENT_KEYS
USAGE_KEYS = {"cost", "steps", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
TRAJECTORY_KEYS = ["repo", "task_id", "feature_id", "agent_id", "model", "status", "cost", "steps", "messages"]
CASCADE_BLOCK = {"route": "naive", "naive": {"conflict": False}, "verdict_63b9d44": True,
                 "verdict_paper_era": True}
# Claude Code's own wall and API time for the agent, from its result event.
DURATION_KEYS = {"duration_ms", "api_duration_ms"}
TIMING_KEYS = {"phase", "agent", "started", "ended", "seconds"}
COOP_EVAL_KEYS = ["repo", "task_id", "features", "setting", "apply_status", "merge", "feature1", "feature2",
                  "both_passed", "error", "evaluated_at"]
# eval.json of a merged pair once the cascade (cascade.py) ran: its block
# after CooperBench's keys, never in their place.
CASCADE_EVAL_KEYS = COOP_EVAL_KEYS + ["cascade"]
SOLO_EVAL_KEYS = ["repo", "task_id", "features", "setting", "merge", "feature1", "feature2", "both_passed",
                  "error", "evaluated_at"]
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def env_value(arguments, name):
    """The value a docker command passes for -e NAME=value."""
    for index, word in enumerate(arguments[:-1]):
        if word == "-e" and arguments[index + 1].startswith(f"{name}="):
            return arguments[index + 1].split("=", 1)[1]
    return None


def quietly():
    """What the runner says on stderr, kept out of the test's output."""
    return contextlib.redirect_stderr(io.StringIO())


def call_tool(rpc_url, tool, arguments, token=None):
    body = json.dumps({"tool": tool, "arguments": arguments}).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(rpc_url, data=body, method="POST", headers=headers)
    with NO_PROXY.open(request, timeout=10) as response:
        return json.loads(response.read())


def pairs(arguments):
    return [arguments[i:i + 2] for i in range(len(arguments) - 1)]


def phases(timings):
    """Each timing as (phase, agent), in the order they were recorded."""
    return [(record["phase"], record["agent"]) for record in timings]


def runner_events(memory, kind=None):
    """The runner's own events among those a listener saw: the hub in this
    process marks its own with a "run" field, the runner's have none."""
    return [event for event in memory if "run" not in event and kind in (None, event["kind"])]


def trace_lines(run_dir):
    return [json.loads(line) for line in (Path(run_dir) / "trace.jsonl").read_text().splitlines()]


class TracedCase(unittest.TestCase):
    """Every test starts at trace's prod level with nobody listening and no
    debug sink open, and leaves it so, since main's --debug sets the
    process's level and the runner registers listeners for a run."""

    def setUp(self):
        self.reset_trace()
        self.addCleanup(self.reset_trace)

    @staticmethod
    def reset_trace():
        runner.close_debug_sinks()
        trace.set_level("prod")
        for listener in list(trace._listeners):
            trace.unlisten(listener)

    def memory(self):
        return runner.listen(trace.Memory())


def timing(phase, agent, started, seconds):
    """A timing record as the runner writes one, for a summary's test."""
    began = datetime(2026, 10, 9, 12, 0, 0) + timedelta(seconds=started)
    return {"phase": phase, "agent": agent, "started": began.isoformat(),
            "ended": (began + timedelta(seconds=seconds)).isoformat(), "seconds": float(seconds)}


def git_answer(command, url, agent):
    """What git prints for one read of the git set-up in a repository that
    CooperBench's setup command configured for `agent` against `url`."""
    if not agent:
        return ""
    if command == "git remote get-url team":
        return url
    if command in ("git symbolic-ref --short HEAD", "git config user.name"):
        return agent
    if "--abbrev-ref" in command:
        return f"team/{agent}"
    if command == "git config user.email":
        return GIT_EMAIL
    return BASE


class FakeDocker:
    """Stands in for drivers.docker: records every command and answers the
    ones whose output the runner reads, the way docker would. Its git
    remembers each container's set-up and answers the read-back as git
    would; with git_breaks the set-up does nothing, as when git refuses
    the repository, and says why. A container keeps the environment the
    docker client had when it was started (extra_env), and the claude
    run in it is handed that, as an exec inherits it from docker."""

    def __init__(self, python=True, mountpoint="/var/lib/docker/volumes/v/_data", fail_seed=False):
        self.stream = SAMPLE_STREAM
        self.calls = []
        self.lock = threading.Lock()
        self.python = python
        self.mountpoint = mountpoint
        self.fail_seed = fail_seed
        self.info_fails = False
        self.claude_times_out = False
        self.on_claude = None
        self.git_setups = {}
        self.git_breaks = False
        self.environments = {}
        # What the agent's session transcript holds, as the driver reads
        # it back from the container's config directory.
        self.transcript = ""

    def __call__(self, *args, input=None, timeout=None, output=None, extra_env=None):
        args = [str(arg) for arg in args]
        with self.lock:
            # The daemon check every run makes first is not a command that
            # touches a container, so it is not recorded with those.
            if args != ["info"]:
                self.calls.append({"args": args, "input": input, "output": output, "extra_env": extra_env})
            if args[:3] == ["run", "-d", "--name"]:
                self.environments[args[3]] = dict(extra_env or {})
        code, out = self.answer(args, output)
        return subprocess.CompletedProcess(["docker", *args], code, out, "" if code == 0 else "fake failure")

    def answer(self, args, output):
        if output is not None:
            return self.run_claude(args, output)
        if args == ["info"]:
            return (1, "") if self.info_fails else (0, "")
        if args[:2] in (["network", "inspect"], ["container", "inspect"]):
            return 1, ""
        if args[:2] == ["run", "--rm"]:
            return (1, "") if self.fail_seed else (0, BASE + "\n")
        if args[:2] == ["volume", "inspect"]:
            return 0, self.mountpoint + "\n"
        if args[0] == "cp" and args[1].endswith(":/workspace/repo"):
            return self.copy_repository(Path(args[2]))
        if args[0] == "exec" and args[-1] == "command -v python3":
            return (0, "/usr/bin/python3\n") if self.python else (1, "")
        if args[0] == "exec" and args[-2:] == ["cat", "/workspace/repo/patch.txt"]:
            return self.patch_of(args[1])
        if args[0] == "exec" and args[-2] == "-c" and "projects -name '*.jsonl'" in args[-1]:
            return 0, self.transcript
        if args[0] == "exec" and args[-2] == "-lc" and "git remote add team" in args[-1]:
            return self.set_up_git(args[-4], args[-1])
        if args[0] == "exec" and args[-2] == "-lc" and runner.GIT_READ_MARK in args[-1]:
            return self.read_git(args[-4], args[-1])
        return 0, ""

    def set_up_git(self, container, script):
        if self.git_breaks:
            return 0, "fatal: detected dubious ownership in repository at '/workspace/repo'\n"
        url = re.search(r"git remote add team (\S+)", script).group(1)
        agent = re.search(r"git config user\.name (\S+)", script).group(1)
        self.git_setups[container] = (url, agent)
        return 0, ""

    def read_git(self, container, script):
        url, agent = self.git_setups.get(container, ("", ""))
        commands = re.findall(r'"\$\((.*?) 2>/dev/null\)"', script)
        return 0, "".join(f"{runner.GIT_READ_MARK}{git_answer(c, url, agent)}\n" for c in commands)

    def run_claude(self, args, output):
        if self.claude_times_out:
            raise subprocess.TimeoutExpired(args, 1)
        if self.on_claude:
            self.on_claude(args, self.environments.get(args[-4], {}))
        Path(output).write_text(self.stream, encoding="utf-8")
        return 0, None

    def copy_repository(self, destination):
        (destination / ".git" / "info").mkdir(parents=True)
        (destination / "README.md").write_text("the task's repository\n", encoding="utf-8")
        return 0, ""

    def patch_of(self, container):
        for agent, patch in PATCHES.items():
            if container.endswith("-" + agent):
                return 0, patch
        return 1, ""

    def commands(self):
        with self.lock:
            return [call["args"] for call in self.calls]

    def matching(self, *head):
        return [args for args in self.commands() if args[:len(head)] == list(head)]


class FakeScorer:
    """Stands in for run.cooperbench_test, CooperBench's test_solo and test_merged."""

    def __init__(self):
        self.calls = []

    def __call__(self, kind, arguments, cooperbench_dir):
        self.calls.append((kind, arguments, str(cooperbench_dir)))
        return dict(SOLO_RESULT if kind == "solo" else MERGED_RESULT)


class FakeCascade(types.ModuleType):
    """Stands in for cascade.py (part of the parity work, owned apart):
    run(plan, evaluation, settings) gives the block score() stores, and
    the calls are kept to check what it was handed."""

    def __init__(self, block=None, problem=None):
        super().__init__("cascade")
        self.block, self.problem, self.calls = block or CASCADE_BLOCK, problem, []

    def run(self, plan, evaluation, settings):
        self.calls.append((plan["run_id"], dict(evaluation), dict(settings)))
        if self.problem:
            raise self.problem
        return dict(self.block)


class FakeConversation(types.ModuleType):
    """Stands in for conversation.py, built to its interface: pure
    functions over the events list. It reads the 'message' events'
    content, as the message logging adds it."""

    def __init__(self):
        super().__init__("conversation")

    @staticmethod
    def messages(events):
        return [e for e in events if e.get("kind") == "message"]

    def conversation(self, events):
        return [{"from": e.get("agent"), "to": e.get("to"), "message": e.get("content", ""),
                 "timestamp": e.get("t"), "feature_id": None} for e in self.messages(events)]

    def sent_by(self, events):
        rows = {}
        for e in self.messages(events):
            rows.setdefault(e.get("agent"), []).append({"from": e.get("agent"), "to": e.get("to"),
                                                       "content": e.get("content", "")})
        return rows

    def store(self, events):
        return [{"id": e.get("seq"), "sender_id": e.get("agent"), "recipient_id": e.get("to"),
                 "content": e.get("content", ""), "is_read": 0} for e in self.messages(events)]


class RecordingDriver:
    """A driver that starts nothing, for tests of provisioning alone."""

    name = "recording"
    framework = "test"
    host_workspace = False
    resumable = False

    def __init__(self):
        self.jobs = []

    def start(self, job):
        self.jobs.append(job)
        return job

    def wait(self, job):
        return drivers.AgentOutcome(status="Submitted", patch=PATCHES.get(job.agent, ""))


class StreamingDriver(RecordingDriver):
    """A driver whose agents stream to the host until their containers
    are removed, as the claude-cli driver's docker exec does: each
    stream's thread writes its last line, the stand-in for the credential
    in it (the test puts PLANTED in the environment as the token), once
    docker rm -f names its container, and then ends. Each join says
    whether the removal came first, the order a teardown needs: a join
    before it waits out its timeout while the agent still streams."""

    def __init__(self, docker, refuse_after=None):
        super().__init__()
        self.docker = docker
        self.refuse_after = refuse_after
        self.joined_after_removal = []

    def removed(self, container):
        return any(container in args[2:] for args in self.docker.matching("rm", "-f"))

    def start(self, job):
        if self.refuse_after is not None and len(self.jobs) >= self.refuse_after:
            raise KeyboardInterrupt
        super().start(job)
        driver = self

        def stream_until_removed():
            deadline = time.time() + 5
            while not driver.removed(job.container) and time.time() < deadline:
                time.sleep(0.01)
            with open(job.log_path, "a", encoding="utf-8") as stream:
                stream.write(f"my environment: CLAUDE_CODE_OAUTH_TOKEN={PLANTED}\n")

        class Stream(threading.Thread):
            def join(self, timeout=None):
                driver.joined_after_removal.append(driver.removed(job.container))
                super().join(timeout)

        handle = types.SimpleNamespace(thread=Stream(target=stream_until_removed))
        handle.thread.start()
        return handle


# A subagent's transcript as Claude Code writes one: the prompt, then one
# API message over two lines (a text block and a tool call) whose usage
# repeats and whose output count grows to its final figure on the line
# carrying the stop_reason, then a second message. With final=False no
# line carries one, as in most of Claude Code 2.1.295's messages, and the
# output counts are the stream-start snapshot.
TRANSCRIPT_TOKENS = {"input_tokens": 3 + 4, "output_tokens": 128 + 20, "cache_read_tokens": 0 + 51258,
                     "cache_write_tokens": 51258 + 900}
TRANSCRIPT_COST = (7 * 3.0 + 148 * 15.0 + 51258 * 0.3 + 52158 * 3.75) / 1_000_000


def transcript_lines(token, model="claude-sonnet-4-5", final=True):
    def assistant(message_id, blocks, usage, stop=None):
        return {"type": "assistant", "isSidechain": True, "version": "2.1.295",
                "timestamp": "2026-10-09T21:05:50.000Z",
                "message": {"id": message_id, "role": "assistant", "model": model, "content": blocks,
                            "stop_reason": stop if final else None, "usage": usage}}
    first = {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 51258}
    return [
        {"type": "user", "isSidechain": True, "version": "2.1.295", "timestamp": "2026-10-09T21:05:48.000Z",
         "message": {"role": "user", "content": "Your task is in /tmp/agent1.session-prompt.md."}},
        assistant("msg_1", [{"type": "text", "text": f"{token}\nReading the task."}], first),
        assistant("msg_1", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}],
                  {**first, "output_tokens": 128}, stop="tool_use"),
        assistant("msg_2", [{"type": "text", "text": "Done."}],
                  {"input_tokens": 4, "output_tokens": 20, "cache_read_input_tokens": 51258,
                   "cache_creation_input_tokens": 900}, stop="end_turn"),
    ]


def write_transcript(path, token, model="claude-sonnet-4-5", age=10, final=True):
    """A transcript `age` seconds old, so the copy need not wait for it to settle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in transcript_lines(token, model, final)))
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def user_line(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write_lines(path, lines):
    """A transcript of the given lines, ten seconds old like write_transcript's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    os.utime(path, (time.time() - 10, time.time() - 10))
    return path


def write_dataset(cooperbench_dir):
    task = cooperbench_dir / "dataset" / REPO / f"task{TASK}"
    for feature in (1, 2, 6, 10):
        (task / f"feature{feature}").mkdir(parents=True)
        (task / f"feature{feature}" / "feature.md").write_text(f"**Title**: Feature {feature}\n\nDo {feature}.\n",
                                                                encoding="utf-8")
    subsets = cooperbench_dir / "dataset" / "subsets"
    subsets.mkdir(parents=True)
    flash = {"name": "flash", "tasks": [{"repo": "other_task", "task_id": 1, "pairs": [[1, 2]]},
                                        {"repo": REPO, "task_id": TASK, "pairs": [[6, 1], [2, 10]]}]}
    (subsets / "flash.json").write_text(json.dumps(flash), encoding="utf-8")


class RunnerCase(TracedCase):
    """A temporary CooperBench tree, a live hub in this process that wants
    the admin token the runner's environment holds and may load the arms
    under <tmp>/arms, and docker and the scorer replaced by fakes for the
    length of each test."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-run-test-"))
        self.cooperbench = self.tmp / "cooperbench"
        write_dataset(self.cooperbench)
        self.logs = self.cooperbench / "logs"
        self.arms = self.tmp / "arms"
        self.hub = hub_module.Hub(arm_dirs=[self.arms], admin_token=ADMIN_SECRET)
        self.server = hub_module.serve_in_background(self.hub)
        self.hub_url = self.hub.bound_url
        (self.tmp / "volume").mkdir()
        self.docker = FakeDocker(mountpoint=str(self.tmp / "volume"))
        self.scorer = FakeScorer()
        self.cascade, self.conversation = FakeCascade(), FakeConversation()
        self.transcripts = self.tmp / "claude-projects"
        environment = {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "COORD_ADMIN_TOKEN": ADMIN_SECRET}
        # The session driver looks for transcripts here, never in the
        # home directory of whoever runs the tests.
        for patcher in (mock.patch.object(drivers, "docker", self.docker),
                        mock.patch.object(runner, "cooperbench_test", self.scorer),
                        mock.patch.object(drivers, "transcript_roots", lambda: [self.transcripts]),
                        mock.patch.dict(os.environ, environment)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.use_module("cascade", self.cascade)
        self.use_module("conversation", self.conversation)

    def use_module(self, name, module):
        """Put a fake in sys.modules under the name for this test alone,
        and nothing else there: patch.dict would put back a copy of the
        whole table, dropping whatever the test imported."""
        previous = sys.modules.get(name)
        sys.modules[name] = module
        self.addCleanup(lambda: sys.modules.__setitem__(name, previous) if previous is not None
                        else sys.modules.pop(name, None))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, arm, **overrides):
        return {"arm": arm, "driver": "recording", "model": "claude-test", "run_name": "t",
                "log_dir": self.logs, "cooperbench_dir": self.cooperbench, "hub": self.hub_url,
                "timeout_s": 60.0, "max_turns": None, "keep": False, "force": False, "options": {},
                "arm_dirs": [], **overrides}

    def prepare(self, arm, driver=None, **overrides):
        driver = driver or RecordingDriver()
        hub = runner.HubClient(self.hub_url, ADMIN_SECRET)
        plan, handles = runner.prepare_pair(self.settings(arm, **overrides), hub, driver, REPO, TASK, [1, 6])
        return plan, handles, driver

    def copy_arm(self, name, source="coop", **config):
        """A built-in arm copied to <tmp>/arms/<name>, as an arm from elsewhere."""
        arm_dir = self.arms / name
        shutil.copytree(COORDBENCH / "arms" / source, arm_dir, ignore=shutil.ignore_patterns("__pycache__"))
        settings = json.loads((arm_dir / "arm.json").read_text())
        settings.update(name=name, **config)
        (arm_dir / "arm.json").write_text(json.dumps(settings))
        return arm_dir

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runner.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def run_arguments(self, arm, *extra):
        return ["run", "--arm", arm, "--repo", REPO, "--task", str(TASK), "--hub", self.hub_url,
                "--cooperbench-dir", str(self.cooperbench), "--name", "t", "--model", "claude-test", *extra]

    def pair(self, setting="coop", features="f1_f6"):
        return self.logs / "t" / setting / REPO / str(TASK) / features

    def container_work(self):
        """The docker commands but the look at a task image config.json
        and result.json take, which a finished pair also gets."""
        return [c for c in self.docker.commands() if c[:2] != ["image", "inspect"]]

    def options_of(self, plan):
        return self.hub.runs[plan["run_id"]].run.options

    def assert_secret_nowhere(self, *said, secrets=()):
        """Neither the agents' credential nor the hub's admin token (nor any
        other secret given) is on a docker command line, in what was piped to
        docker, in any file the run left, or in what the runner printed."""
        for secret in (SECRET, ADMIN_SECRET, *secrets):
            for call in self.docker.commands():
                self.assertNotIn(secret, " ".join(call))
            for call in self.docker.calls:
                self.assertNotIn(secret, call["input"] or "")
            for path in self.tmp.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), str(path))
            for text in said:
                self.assertNotIn(secret, text)

    def assert_timings_well_formed(self, timings):
        """Every record has the contract's five fields, ISO 8601 times
        that do not run backwards, and seconds that agree with them."""
        self.assertTrue(timings)
        for record in timings:
            self.assertEqual(set(record), TIMING_KEYS, record)
            started, ended = datetime.fromisoformat(record["started"]), datetime.fromisoformat(record["ended"])
            self.assertLessEqual(started, ended, record)
            self.assertAlmostEqual(record["seconds"], (ended - started).total_seconds(), places=3)
            self.assertIn(record["phase"], runner.PHASES)
            self.assertEqual(record["agent"] is not None, record["phase"] in ("provision", "agent"), record)

    def assert_tokens_kept_private(self, plan, *said):
        """Each agent's hub token is on no docker command line, in nothing
        piped to docker, in nothing the runner printed, and in no file but
        plan.json and the session driver's token files, which are the
        owner's alone."""
        tokens = [agent["token"] for agent in plan["agents"]]
        self.assertTrue(all(tokens), plan["agents"])
        run_dir = Path(plan["run_dir"])
        allowed = {run_dir / "plan.json"}
        allowed |= {drivers.session_bin_dir(run_dir, agent["id"]) / "token" for agent in plan["agents"]}
        for path in allowed:
            if path.exists():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))
        for token in tokens:
            for call in self.docker.calls:
                self.assertNotIn(token, " ".join(call["args"]))
                self.assertNotIn(token, call["input"] or "")
            for path in self.tmp.rglob("*"):
                if path.is_file() and path not in allowed:
                    self.assertNotIn(token.encode(), path.read_bytes(), str(path))
            for text in said:
                self.assertNotIn(token, text)


class TheDataset(RunnerCase):
    def test_specs_are_read_from_each_features_feature_md(self):
        specs = runner.read_specs(self.cooperbench / "dataset", REPO, TASK, [1, 6])
        self.assertEqual(specs, {1: "**Title**: Feature 1\n\nDo 1.\n", 6: "**Title**: Feature 6\n\nDo 6.\n"})

    def test_a_missing_spec_names_its_path(self):
        with self.assertRaisesRegex(FileNotFoundError, "feature7"):
            runner.read_specs(self.cooperbench / "dataset", REPO, TASK, [1, 7])

    def test_a_subset_gives_every_pair_of_the_task_sorted(self):
        pairs = runner.subset_pairs(self.cooperbench / "dataset", "flash", REPO, TASK)
        self.assertEqual(pairs, [[1, 6], [2, 10]])
        with self.assertRaises(ValueError):
            runner.subset_pairs(self.cooperbench / "dataset", "flash", REPO, 9999)

    def test_image_names_follow_cooperbench(self):
        self.assertEqual(runner.image_name(REPO, TASK), IMAGE)
        self.assertEqual(runner.image_name("dottxt_ai_outlines_task", 1655),
                         "akhatua/cooperbench-dottxt-ai-outlines:task1655")


class Provisioning(RunnerCase):
    def test_an_arm_with_no_needs_gets_a_container_per_agent_with_coordbench_inside(self):
        plan, _, driver = self.prepare("coop")
        commands = self.docker.commands()
        self.assertIn(["network", "create", "coordbench"], commands)
        self.assertFalse([c for c in commands if "coordbench-git" in c or c[0] == "volume"])
        self.assertEqual([a["id"] for a in plan["agents"]], ["agent1", "agent2"])
        for agent in plan["agents"]:
            container = agent["container"]
            started = [c for c in self.docker.matching("run", "-d", "--name", container)]
            self.assertEqual(len(started), 1)
            arguments = started[0]
            for pair in (["--network", "coordbench"], ["--add-host", "host.docker.internal:host-gateway"],
                         ["--entrypoint", "sh"]):
                self.assertIn(pair, [arguments[i:i + 2] for i in range(len(arguments) - 1)])
            self.assertEqual(env_value(arguments, "COORD_MCP_URL"), agent["mcp_url"])
            self.assertEqual(env_value(arguments, "COORD_RPC_URL"), agent["rpc_url"])
            self.assertEqual(arguments[-3:-1], [IMAGE, "-c"])
            self.assertIn("sleep infinity", arguments[-1])
            self.assertIn(["cp", f"{COORDBENCH}/.", f"{container}:/opt/coordbench"], commands)
            self.assertIn(["exec", container, "python3", "/opt/coordbench/cli.py", "install", "/usr/local/bin"],
                          commands)
        self.assertFalse([c for c in commands if "bash" in c and "-lc" in c])
        self.assertFalse([c for c in commands if "safe.directory" in c])
        self.assertEqual(self.options_of(plan), {})
        self.assertEqual([job.agent for job in driver.jobs], ["agent1", "agent2"])
        self.assertEqual(driver.jobs[0].env, {"COORD_MCP_URL": plan["agents"][0]["mcp_url"],
                                              "COORD_RPC_URL": plan["agents"][0]["rpc_url"]})
        self.assertIn("Feature 1", driver.jobs[0].prompt)

    def test_each_agent_gets_a_hub_token_that_reaches_its_container_by_name_alone(self):
        """The runner asks the hub for agent tokens, so the run refuses a
        call without the agent's own. The token goes to the container in
        the docker client's environment for the one docker run that
        starts it, named by -e and never on a command line; the plan that
        keeps it is the owner's alone."""
        plan, _, _ = self.prepare("coop")
        tokens = {agent["id"]: agent["token"] for agent in plan["agents"]}
        self.assertEqual(tokens, self.hub.runs[plan["run_id"]].tokens)
        self.assertEqual(len(set(tokens.values())), 2)
        for agent in plan["agents"]:
            started, = [call for call in self.docker.calls
                        if call["args"][:4] == ["run", "-d", "--name", agent["container"]]]
            self.assertIn(["-e", "COORD_AGENT_TOKEN"], pairs(started["args"]))
            self.assertIsNone(env_value(started["args"], "COORD_AGENT_TOKEN"))
            self.assertEqual(started["extra_env"], {"COORD_AGENT_TOKEN": agent["token"]})
            with self.assertRaises(urllib.error.HTTPError) as refused:
                call_tool(agent["rpc_url"], "peek_messages", {})
            self.assertEqual(refused.exception.code, 401)
            self.assertEqual(call_tool(agent["rpc_url"], "peek_messages", {}, token=agent["token"])["text"], "0")
        others = [call for call in self.docker.calls if call["args"][:2] != ["run", "-d"]]
        self.assertTrue(others)
        self.assertFalse([call for call in others if call["extra_env"]])
        saved = json.loads((Path(plan["run_dir"]) / "plan.json").read_text())
        self.assertEqual([agent["token"] for agent in saved["agents"]], list(tokens.values()))
        self.assert_tokens_kept_private(plan)

    def test_an_image_without_python_runs_without_shell_commands(self):
        self.docker.python = False
        plan, _, _ = self.prepare("coop")
        self.assertFalse([c for c in self.docker.commands() if "/opt/coordbench/cli.py" in c])
        self.assertTrue(any("no python3" in warning for warning in plan["warnings"]))

    def test_a_git_remote_is_a_seeded_repository_on_the_shared_git_server(self):
        plan, _, _ = self.prepare("coop-git")
        run_id = plan["run_id"]
        url = f"git://coordbench-git:9418/{run_id}/repo.git"
        commands = self.docker.commands()
        server = self.docker.matching("run", "-d", "--name", "coordbench-git")[0]
        self.assertIn("127.0.0.1:9418:9418", server)
        self.assertIn("coordbench-git-data:/git", server)
        self.assertEqual(server[-1], "cooperbench-git-server:local")
        init = [c for c in self.docker.matching("exec", "coordbench-git", "sh", "-c")
                if f"/git/{run_id}/repo.git" in c[-1]][0]
        self.assertIn("git init -q --bare", init[-1])
        seed = self.docker.matching("run", "--rm")[0]
        self.assertEqual(seed[:8], ["run", "--rm", "--network", "coordbench", "--entrypoint", "sh", IMAGE, "-c"])
        self.assertIn(f"git push -q {url} HEAD:refs/heads/main", seed[-1])
        for agent in ("agent1", "agent2"):
            self.assertIn(["exec", "coordbench-git", "git", "-C", f"/git/{run_id}/repo.git", "branch", agent, "main"],
                          commands)
        shells = [c for c in self.docker.matching("exec", "-w", "/workspace/repo") if c[-2] == "-lc"]
        setups = [c for c in shells if "git remote add team" in c[-1]]
        self.assertEqual(len(setups), 2)
        self.assertEqual(setups[0][-1], runner.git_setup_command("agent1", url))
        self.assertIn(f"git remote add team {url}", setups[0][-1])
        self.assertIn("git checkout -b agent1", setups[0][-1])
        self.assertIn("git push -u team agent2 --force", setups[1][-1])
        reads = [c for c in shells if runner.GIT_READ_MARK in c[-1]]
        self.assertEqual([c[-4] for c in reads], [a["container"] for a in plan["agents"]])
        for setup, read in zip(setups, reads):
            self.assertLess(shells.index(setup), shells.index(read))
        self.assertEqual(self.options_of(plan), {"git_remote": url, "base_ref": BASE,
                                                 "git_remote_host": f"git://127.0.0.1:9418/{run_id}/repo.git"})
        self.assertEqual(plan["warnings"], [])
        first_agent = self.docker.matching("run", "-d", "--name", plan["agents"][0]["container"])[0]
        self.assertLess(commands.index(seed), commands.index(first_agent))
        runner.teardown(plan)
        self.assertIn(["exec", "coordbench-git", "rm", "-rf", f"/git/{run_id}"], self.docker.commands())

    def test_a_shared_volume_is_one_volume_per_run_at_workspace_shared(self):
        plan, _, _ = self.prepare("team")
        volume = f"coordbench-shared-{plan['run_id']}"
        self.assertIn(["volume", "create", volume], self.docker.commands())
        for agent in plan["agents"]:
            started = self.docker.matching("run", "-d", "--name", agent["container"])[0]
            self.assertIn(f"{volume}:/workspace/shared", started)
        self.assertEqual(self.options_of(plan), {"shared_dir": "/workspace/shared",
                                                 "shared_dir_host": str(self.tmp / "volume")})
        self.assertEqual(plan["warnings"], [])
        self.assertFalse([c for c in self.docker.commands() if "coordbench-git" in c])
        runner.teardown(plan)
        commands = self.docker.commands()
        self.assertIn(["volume", "rm", "-f", volume], commands)
        removed = ["rm", "-f", *[a["container"] for a in plan["agents"]]]
        self.assertIn(removed, commands)
        # The volume's last state is copied out of a container that mounts
        # it, before the containers and the volume go.
        copied = ["cp", f"{plan['agents'][0]['container']}:/workspace/shared/.", str(Path(plan["run_dir"]) / "shared")]
        self.assertLess(commands.index(copied), commands.index(removed))
        self.assertTrue((Path(plan["run_dir"]) / "shared").is_dir())

    def test_a_pair_run_again_packs_the_earlier_attempt_s_files_first(self):
        plan, _, _ = self.prepare("coop")
        runner.teardown(plan)
        run_dir = Path(plan["run_dir"])
        (run_dir / "judge.json").write_text('{"speech_acts": {"6": "Plan"}}')
        (run_dir / "agent2_sent.jsonl").write_text("{}\n")
        (run_dir / "bin" / "agent1").mkdir(parents=True)
        (run_dir / "bin" / "agent1" / "token").write_text("secret")
        (run_dir / "workspaces" / "agent1").mkdir(parents=True)
        again, _, _ = self.prepare("coop")
        self.assertEqual(again["run_dir"], plan["run_dir"])
        archives = sorted(run_dir.glob("previous-*.tar.gz"))
        self.assertEqual(len(archives), 1)
        self.assertFalse((run_dir / "judge.json").exists())
        self.assertFalse((run_dir / "agent2_sent.jsonl").exists())
        with tarfile.open(archives[0]) as bundle:
            names = bundle.getnames()
        self.assertIn("judge.json", names)
        self.assertIn("agent2_sent.jsonl", names)
        self.assertFalse([name for name in names if name.split("/")[0] in ("bin", "workspaces")])
        self.assertIn(f"an earlier attempt's files in the pair directory were packed into {archives[0].name}",
                      again["warnings"])
        runner.teardown(again)
        self.assertIsNone(runner.set_aside_earlier_attempt(self.tmp / "no-such-pair"))

    def test_a_shared_volume_with_no_host_path_fails_the_pair_and_one_not_writable_is_warned_of(self):
        self.docker.mountpoint = ""
        with self.assertRaisesRegex(runner.PairFailed, "no host path"), quietly():
            self.prepare("team")
        self.assertEqual(self.hub.run_count(), 0)
        self.docker.mountpoint = str(self.tmp / "nowhere")
        plan, _, _ = self.prepare("team")
        self.assertEqual(self.options_of(plan)["shared_dir_host"], str(self.tmp / "nowhere"))
        self.assertTrue(any("not writable" in warning for warning in plan["warnings"]))

    def test_a_run_gets_every_option_of_every_need_or_does_not_start(self):
        with mock.patch.object(runner, "create_git_repository", return_value={"git_remote": "git://x/r.git"}):
            with self.assertRaisesRegex(runner.PairFailed, "without git_remote_host, base_ref"), quietly():
                self.prepare("coop-git")
        self.assertEqual(self.hub.run_count(), 0)

    def test_the_bash_tool_surface_loads_no_arm_plugin_and_gives_team_cooperbench_s_one_server(self):
        """tool_surface=bash is the surface CooperBench's claude_code agents
        had: no arm plugin, so no skill, and for team the cooperbench-team
        server alone (Arm.claude_surface); the default keeps the plugin."""
        _, _, driver = self.prepare("coop", options={"tool_surface": "bash"})
        flags = drivers.claude_flags(driver.jobs[0])
        self.assertFalse(driver.jobs[0].arm_plugin)
        self.assertNotIn("--plugin-dir", flags)
        self.assertNotIn("--mcp-config", flags)
        plan, _, driver = self.prepare("team", options={"tool_surface": "bash"})
        self.assertEqual(plan["tool_surface"], "bash")
        for job in driver.jobs:
            self.assertEqual(list(job.mcp_servers), ["cooperbench-team"])
            self.assertIn("--mcp-config", drivers.claude_flags(job))
            self.assertNotIn("--plugin-dir", drivers.claude_flags(job))
        _, _, driver = self.prepare("coop")
        self.assertTrue(driver.jobs[0].arm_plugin)
        self.assertIn("/opt/coordbench/arms/coop", drivers.claude_flags(driver.jobs[0]))
        with self.assertRaisesRegex(ValueError, "tool_surface must be one of"):
            self.prepare("coop", options={"tool_surface": "carrier pigeon"})

    def test_an_option_naming_a_need_gets_it_provisioned_as_cooperbench_s_git_gives_team_a_remote(self):
        plan, _, driver = self.prepare("team", options={"git_remote": True, "note": "kept"})
        self.assertEqual(plan["needs"], ["shared_volume", "git_remote"])
        run_id = plan["run_id"]
        url = f"git://coordbench-git:9418/{run_id}/repo.git"
        self.assertEqual(self.options_of(plan), {
            "note": "kept", "git_remote": url, "git_remote_host": f"git://127.0.0.1:9418/{run_id}/repo.git",
            "base_ref": BASE, "shared_dir": "/workspace/shared", "shared_dir_host": str(self.tmp / "volume")})
        self.assertEqual(sorted(self.docker.git_setups.values()), [(url, a["id"]) for a in plan["agents"]])
        for job in driver.jobs:
            self.assertIn("A shared git remote named `team` is already configured", job.prompt)
            self.assertIn(f"Your branch: `{job.agent}` (already created and pushed)", job.prompt)
        self.assertEqual(runner.arm_needs(runner.find_arm("coop"), {"shared_dir": "/x"}), ["shared_volume"])
        self.assertEqual(runner.arm_needs(runner.find_arm("coop"), {"git_remote": ""}), [])
        # What the runner provisioned is timed, need by need, before the
        # run is created and each agent's container set up.
        self.assert_timings_well_formed(plan["timings"])
        self.assertEqual(phases(plan["timings"]), [("git_remote", None), ("shared_volume", None), ("run_create", None),
                                                   ("provision", "agent1"), ("provision", "agent2")])

    def test_a_git_set_up_that_leaves_a_promise_unkept_fails_the_pair(self):
        self.docker.git_breaks = True
        code, out, err = self.main(*self.run_arguments("coop-git", "--features", "1,6"))
        self.assertEqual(code, 1)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertIn("the git set-up for agent1 left its git block untrue", result["error"])
        for promise in ("the remote team (found nothing)", "the branch agent1 checked out",
                        "agent1 pushed with -u", "team/main at the base commit", "user.name the agent id",
                        f"user.email {GIT_EMAIL}", "git said: fatal: detected dubious ownership"):
            self.assertIn(promise, result["error"])
        self.assertIn("left its git block untrue", err)
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Error"})
        self.assertNotIn(result["run_id"], self.hub.runs)  # closed, then released
        # No agent ran, so nothing was measured: null, not CooperBench's 0.
        self.assertEqual((result["total_cost"], result["total_steps"]), (None, None))
        self.assertEqual({a[name] for a in result["agents"].values() for name in USAGE_KEYS}, {None})
        self.assertEqual({a["patch_lines"] for a in result["agents"].values()}, {0})
        # The provisioning error is CooperBench's error (an exception) and coordbench's end_reason alike.
        self.assertEqual({a["error"] for a in result["agents"].values()}, {result["error"]})
        self.assertEqual({a["end_reason"] for a in result["agents"].values()}, {result["error"]})
        self.assertIn(["rm", "-f", f"coordbench-{result['run_id']}-agent1"], self.docker.commands())
        self.assertEqual(self.scorer.calls, [])
        self.assert_secret_nowhere(out, err)

    def test_an_arm_from_elsewhere_brings_its_plugins_and_mcp_servers(self):
        servers = {"docs": {"type": "http", "url": "http://docs.example/mcp"}}
        arm_dir = self.copy_arm("myarm", plugins=["extra-plugin"], mcp_servers=servers)
        (arm_dir / "extra-plugin" / ".claude-plugin").mkdir(parents=True)
        (arm_dir / "extra-plugin" / ".claude-plugin" / "plugin.json").write_text('{"name": "extra"}')
        plan, _, driver = self.prepare(str(arm_dir))
        container = plan["agents"][0]["container"]
        commands = self.docker.commands()
        inside = f"/opt/coordbench-arms/{drivers.safe_name(arm_dir.resolve())}"
        self.assertIn(["exec", container, "mkdir", "-p", "/opt", "/opt/coordbench-plugins", "/opt/coordbench-arms"],
                      commands)
        self.assertIn(["cp", f"{arm_dir}/.", f"{container}:{inside}"], commands)
        self.assertIn(["cp", f"{arm_dir / 'extra-plugin'}/.", f"{container}:/opt/coordbench-plugins/0-extra-plugin"],
                      commands)
        job = driver.jobs[0]
        self.assertEqual(job.plugins, ["/opt/coordbench-plugins/0-extra-plugin"])
        self.assertEqual(job.plugin_urls, [])
        self.assertEqual(job.mcp_servers, servers)
        flags = drivers.claude_flags(job)
        self.assertIn(inside, flags)
        self.assertIn("/opt/coordbench-plugins/0-extra-plugin", flags)
        self.assertNotIn("--plugin-url", flags)

    def test_an_arm_from_elsewhere_named_like_a_built_in_arm_is_not_copied_over_the_built_in_one(self):
        """The copy of coordbench fills /opt/coordbench/arms/coop in every
        container; an arm from elsewhere called coop copied there too would
        merge with it, and Claude would load a mix of the two."""
        arm_dir = self.copy_arm("coop")
        self.assertFalse(drivers.arm_is_builtin(arm_dir))
        self.assertTrue(drivers.arm_is_builtin(COORDBENCH / "arms" / "coop"))
        self.assertEqual(drivers.container_arm_dir(COORDBENCH / "arms" / "coop"), "/opt/coordbench/arms/coop")
        plan, _, driver = self.prepare(str(arm_dir))
        copies = [c for c in self.docker.matching("cp") if c[1] == f"{arm_dir}/."]
        self.assertEqual(len(copies), 2)
        for copy in copies:
            self.assertTrue(copy[2].endswith(f":/opt/coordbench-arms/{drivers.safe_name(arm_dir.resolve())}"), copy)
            self.assertNotIn("/opt/coordbench/arms/", copy[2])
        self.assertNotIn("/opt/coordbench/arms/coop", drivers.claude_flags(driver.jobs[0]))

    def test_a_plugin_zip_url_is_handed_to_claude_as_plugin_url_and_a_git_url_is_cloned_here(self):
        """--plugin-url takes a .zip and nothing else, so a .zip URL is not
        cloned; any other URL is a git repository, cloned on this host and
        copied into the container like a directory."""
        zip_url = "https://plugins.example/review/latest.ZIP?channel=stable"
        git_url = "https://git.example/org/memory.git"
        arm_dir = self.copy_arm("myarm", plugins=[zip_url, git_url, "extra-plugin"])
        (arm_dir / "extra-plugin").mkdir()

        def clone(url, destination):
            destination.mkdir(parents=True)
            (destination / "cloned-from").write_text(url)
            return destination

        with mock.patch.object(runner, "fetch_plugin", side_effect=clone) as fetch:
            plan, _, driver = self.prepare(str(arm_dir))
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [git_url])
        cloned = plan["run_dir"] + "/plugins/1-memory"
        self.assertEqual(plan["plugins"], [
            {"url": zip_url},
            {"source": cloned, "container": "/opt/coordbench-plugins/1-1-memory"},
            {"source": str(arm_dir / "extra-plugin"), "container": "/opt/coordbench-plugins/2-extra-plugin"}])
        copied = [c[1] for c in self.docker.matching("cp")]
        self.assertIn(f"{cloned}/.", copied)
        self.assertFalse([source for source in copied if "latest" in source or "plugins.example" in source])
        job = driver.jobs[0]
        self.assertEqual(job.plugins, ["/opt/coordbench-plugins/1-1-memory", "/opt/coordbench-plugins/2-extra-plugin"])
        self.assertEqual(job.plugin_urls, [zip_url])
        flags = drivers.claude_flags(job)
        self.assertEqual(flags[flags.index("--plugin-url") + 1], zip_url)
        self.assertNotIn(zip_url, [flags[i + 1] for i, flag in enumerate(flags[:-1]) if flag == "--plugin-dir"])
        self.assertFalse(runner.is_zip_url("git@github.com:org/plugin.zip"))
        self.assertFalse(runner.is_zip_url("https://git.example/org/memory.git"))

    def test_a_driver_that_does_not_say_it_measures_usage_is_taken_not_to(self):
        plan, _, _ = self.prepare("coop")
        self.assertFalse(plan["measures_usage"])
        self.assertTrue(drivers.ClaudeCliDriver.measures_usage)
        self.assertFalse(drivers.SessionDriver.measures_usage)

    def test_a_failed_provision_is_recorded_as_an_error_and_torn_down(self):
        self.docker.fail_seed = True
        code, _, err = self.main(*self.run_arguments("coop-git", "--features", "1,6"))
        self.assertEqual(code, 1)
        self.assertIn("error", err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertIn("seeding the shared repository", result["error"])
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Error"})
        self.assertFalse((self.pair() / "eval.json").exists())
        self.assertTrue([c for c in self.docker.commands() if c[:4] == ["exec", "coordbench-git", "rm", "-rf"]])
        self.assertEqual(self.scorer.calls, [])
        # The phase that failed is still timed, and so is the teardown
        # after it; the summary names what happened and nothing else.
        self.assert_timings_well_formed(result["timings"])
        self.assertEqual(phases(result["timings"]), [("hub", None), ("git_remote", None), ("teardown", None)])
        self.assertRegex(err, r"timings: hub [0-9.]+ s, git_remote [0-9.]+ s, teardown [0-9.]+ s\n")


    def test_a_failure_after_the_agents_started_tears_them_down_and_records_the_error(self):
        """The plan's write comes after the agents start streaming to the
        host (the disk full is the realistic cause): the pair is an
        error like a failed provision, its containers removed and its
        files scrubbed, never left running with no record."""
        calls = []

        def failing_save(plan):
            calls.append(plan["run_id"])
            if len(calls) == 1:
                raise OSError("No space left on device")

        with mock.patch.object(runner, "save_plan", side_effect=failing_save):
            code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 1)
        self.assertIn("No space left on device", err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertIn("OSError: No space left on device", result["error"])
        self.assertEqual(result["error_kind"], "infrastructure")
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Error"})
        self.assertFalse((self.pair() / "eval.json").exists())
        self.assertTrue([c for c in self.docker.commands() if c[:2] == ["rm", "-f"]])
        self.assertEqual(self.scorer.calls, [])
        self.assertFalse(runner.pair_finished(self.pair()))

    def planting(self):
        """The stand-in as the agents' token, so the scrub replaces it
        wherever a test or a stream writes it."""
        return mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED})

    def assert_streams_scrubbed(self, count):
        streams = sorted(self.pair().glob("*_stream.jsonl"))
        self.assertEqual(len(streams), count)
        for stream in streams:
            self.assertNotIn(PLANTED, stream.read_text())
            self.assertIn(runner.SCRUBBED, stream.read_text())

    def test_a_failure_whose_own_record_fails_still_stops_the_agents_before_the_scrub(self):
        """A full disk fails the record's writes as it failed the plan's:
        the containers are removed, then the threads that write the
        agents' streams waited for, which the removal ends, all the same
        and before the scrub, so a stream's last line is scrubbed too; and
        the caller gets PairFailed, so prepare stops the hub it started
        and a run goes on."""
        driver = StreamingDriver(self.docker)
        full = OSError(28, "No space left on device")
        with mock.patch.object(runner, "save_plan", side_effect=full), \
                mock.patch.object(runner, "write_outputs", side_effect=full), \
                self.planting(), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(runner.PairFailed):
                self.prepare("coop", driver=driver)
        self.assertIn("recording the failure failed too: OSError", err.getvalue())
        self.assertEqual(driver.joined_after_removal, [True, True])
        self.assert_streams_scrubbed(2)

    def test_a_failure_said_to_a_closed_stderr_still_stops_the_agents_and_scrubs(self):
        """Saying the pair's error is part of recording it, so a stderr
        gone (a run piped into head) fails the say inside the record,
        never before the teardown and the scrub."""
        driver = StreamingDriver(self.docker)
        with mock.patch.object(runner, "save_plan", side_effect=OSError(28, "No space left on device")), \
                mock.patch.object(runner, "say", side_effect=BrokenPipeError(32, "Broken pipe")), self.planting():
            with self.assertRaises(OSError):
                self.prepare("coop", driver=driver)
        self.assertEqual(driver.joined_after_removal, [True, True])
        self.assert_streams_scrubbed(2)

    def test_an_interrupt_between_two_starts_stops_the_started_agent_and_scrubs(self):
        """The first agent streams from its start, so an interrupt before
        the second one starts still removes the first's container, waits
        for its stream and scrubs it, as finish_pair does however it ends;
        and the run on the hub is closed."""
        driver = StreamingDriver(self.docker, refuse_after=1)
        with self.assertRaises(KeyboardInterrupt), self.planting(), quietly():
            self.prepare("coop", driver=driver)
        self.assertEqual(driver.joined_after_removal, [True])
        self.assert_streams_scrubbed(1)
        self.assertEqual(self.hub.run_count(), 0)

    def test_an_interrupt_while_provisioning_tears_down_and_scrubs(self):
        real = runner.provision_agent

        def provision_then_interrupt(plan, agent, hub, options):
            if agent is plan["agents"][1]:
                (Path(plan["run_dir"]) / "provisioned.log").write_text(f"token {PLANTED}\n")
                raise KeyboardInterrupt
            return real(plan, agent, hub, options)

        with mock.patch.object(runner, "provision_agent", side_effect=provision_then_interrupt), \
                self.assertRaises(KeyboardInterrupt), self.planting(), quietly():
            self.prepare("coop")
        started = [args[3] for args in self.docker.matching("run", "-d", "--name")]
        self.assertEqual(len(started), 1)
        self.assertTrue([args for args in self.docker.matching("rm", "-f") if started[0] in args])
        self.assertEqual((self.pair() / "provisioned.log").read_text(), f"token {runner.SCRUBBED}\n")
        self.assertEqual(self.hub.run_count(), 0)

    def test_a_shared_copy_that_cannot_be_made_still_lets_the_teardown_remove_everything(self):
        """The team arm's scratchpad is copied out before the teardown; a
        copy whose directory cannot be made (a full disk, here a file in
        its way) is said and costs the pair nothing, so the containers
        and the volume still go."""
        plan, _, _ = self.prepare("team")
        (Path(plan["run_dir"]) / runner.SHARED_COPY).write_text("in the way\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            runner.teardown(plan)
        self.assertIn(f"copying {runner.SHARED_DIR} before the teardown failed", err.getvalue())
        commands = self.docker.commands()
        self.assertIn(["rm", "-f", *[a["container"] for a in plan["agents"]]], commands)
        self.assertIn(["volume", "rm", "-f", f"coordbench-shared-{plan['run_id']}"], commands)


class TheClaudeCliDriver(RunnerCase):
    def job(self, **overrides):
        fields = {"run_id": "r1", "agent": "agent1", "role": "peer", "features": (1,), "container": "c-agent1",
                  "prompt": "Implement feature 1.\n", "env": {"COORD_MCP_URL": "http://h:1/mcp/r1/agent1",
                                                              "COORD_RPC_URL": "http://h:1/rpc/r1/agent1"},
                  "arm_dir": str(COORDBENCH / "arms" / "coop"), "plugins": ["/opt/coordbench-plugins/0-extra"],
                  "mcp_servers": {"docs": {"type": "http", "url": "http://docs"}},
                  "model": "anthropic/claude-test", "timeout_s": 30, "max_turns": 40,
                  "log_path": str(self.tmp / "agent1_stream.jsonl")}
        fields.update(overrides)
        return drivers.AgentJob(**fields)

    def test_it_runs_headless_claude_with_the_arm_as_a_plugin(self):
        driver = drivers.ClaudeCliDriver()
        outcome = driver.wait(driver.start(self.job()))
        writes = self.docker.matching("exec", "-i", "c-agent1", "sh", "-c")
        self.assertEqual([(c[-1]) for c in writes], ["cat > /tmp/coord-prompt.txt", "cat > /tmp/coord-mcp.json"])
        inputs = [call["input"] for call in self.docker.calls if call["args"] in writes]
        self.assertEqual(inputs[0], "Implement feature 1.\n")
        self.assertEqual(json.loads(inputs[1]), {"mcpServers": {"docs": {"type": "http", "url": "http://docs"}}})
        runs = [call for call in self.docker.calls if call["output"]]
        self.assertEqual(len(runs), 1)
        arguments = runs[0]["args"]
        self.assertEqual(runs[0]["output"], str(self.tmp / "agent1_stream.jsonl"))
        self.assertEqual(arguments[:3], ["exec", "-w", "/workspace/repo"])
        self.assertEqual(arguments[-4:-1], ["c-agent1", "bash", "-lc"])
        for name, value in (("ANTHROPIC_MODEL", "claude-test"), ("CLAUDE_CONFIG_DIR", "/tmp/claude-cfg"),
                            ("IS_SANDBOX", "1"), ("COORD_MCP_URL", "http://h:1/mcp/r1/agent1"),
                            ("COORD_RPC_URL", "http://h:1/rpc/r1/agent1")):
            self.assertEqual(env_value(arguments, name), value)
        # The team arm's request tool waits up to 300 s; Claude Code's own
        # limit on an HTTP MCP tool call is 60 s unless this says otherwise.
        self.assertEqual(env_value(arguments, "MCP_TOOL_TIMEOUT"), "330000")
        self.assertGreater(int(env_value(arguments, "MCP_TOOL_TIMEOUT")), 300 * 1000)
        self.assertIn(["-e", "CLAUDE_CODE_OAUTH_TOKEN"], [arguments[i:i + 2] for i in range(len(arguments) - 1)])
        script = arguments[-1]
        self.assertIn("claude --verbose --output-format=stream-json --permission-mode=bypassPermissions "
                      "--plugin-dir /opt/coordbench/arms/coop --plugin-dir /opt/coordbench-plugins/0-extra "
                      "--mcp-config /tmp/coord-mcp.json --max-turns 40 "
                      '--print -- "$(cat /tmp/coord-prompt.txt)"', script)
        self.assertIn("cd /workspace/repo", script)
        self.assertEqual(outcome.status, "Submitted")
        self.assertEqual((outcome.input_tokens, outcome.output_tokens, outcome.steps), (1200, 340, 12))
        self.assertEqual(outcome.cost, 0.42)
        self.assertEqual((outcome.duration_ms, outcome.api_duration_ms), (95000, 61000))
        self.assertEqual(outcome.patch, PATCHES["agent1"])
        self.assertIn(["exec", "c-agent1", "cat", "/workspace/repo/patch.txt"], self.docker.commands())
        self.assert_secret_nowhere()

    def test_claude_runs_under_timeout_inside_the_container_and_the_shell_leaves_its_pid_first(self):
        """The task images have no pkill, so the time limit has to hold
        inside the container on its own, and stopping early needs a pid."""
        script = drivers.claude_script(self.job(timeout_s=90.5))
        self.assertIn("echo $$ > /tmp/coord-claude.pid; cd /workspace/repo && exec timeout -k 30 90 claude --verbose",
                      script)
        self.assertLess(script.index("echo $$ >"), script.index("exec timeout"))
        self.assertTrue(script.endswith('--print -- "$(cat /tmp/coord-prompt.txt)" 2>&1'))
        self.assertIn("timeout -k 30 1 claude", drivers.claude_script(self.job(timeout_s=0.2)))

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"), "needs bash and coreutils timeout")
    def test_the_pid_the_shell_leaves_is_one_the_stop_script_s_kill_ends_claude_through(self):
        """The script's head run by a real shell, with sleep in claude's
        place: after exec the pid in the file is the timeout's, and the
        stop script's builtin kill, through it, ends the command."""
        paths = {"CONTAINER_REPO": str(self.tmp), "CONTAINER_CLAUDE_CONFIG_DIR": str(self.tmp / "cfg"),
                 "CONTAINER_PID_FILE": str(self.tmp / "claude.pid")}
        with mock.patch.multiple(drivers, **paths):
            head = drivers.claude_script(self.job(timeout_s=60)).split("claude --verbose", 1)[0]
            stop = drivers.stop_script()
        self.assertNotIn("pkill", stop)
        agent = subprocess.Popen(["bash", "-c", head + "sleep 60"], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(agent.kill)
        deadline = time.time() + 10
        while not (self.tmp / "claude.pid").exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(int((self.tmp / "claude.pid").read_text()), agent.pid)
        time.sleep(0.2)
        stopped = subprocess.run(["sh", "-c", stop], capture_output=True, text=True, timeout=30)
        self.assertEqual((stopped.returncode, stopped.stdout, stopped.stderr), (0, "", ""))
        agent.wait(10)
        self.assertNotEqual(agent.returncode, 0)
        (self.tmp / "claude.pid").unlink()
        gone = subprocess.run(["sh", "-c", stop], capture_output=True, text=True, timeout=30)
        self.assertEqual((gone.returncode, gone.stdout, gone.stderr), (0, "", ""))

    def test_the_token_is_not_passed_when_it_is_not_set(self):
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN")
        driver = drivers.ClaudeCliDriver()
        driver.wait(driver.start(self.job(mcp_servers={}, plugins=[], max_turns=None)))
        arguments = [call for call in self.docker.calls if call["output"]][0]["args"]
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", arguments)
        self.assertNotIn("--mcp-config", arguments[-1])
        self.assertNotIn("--max-turns", arguments[-1])
        self.assertEqual(len(self.docker.matching("exec", "-i", "c-agent1")), 1)

    def test_an_agent_past_its_time_is_an_error_and_is_stopped_by_pid_since_the_images_have_no_pkill(self):
        """pkill -f 'claude --verbose' used to be the stop; no Debian-slim
        task image has pkill, so the agent kept running after the timeout."""
        self.docker.claude_times_out = True
        driver = drivers.ClaudeCliDriver()
        outcome = driver.wait(driver.start(self.job()))
        self.assertEqual(outcome.status, "Error")
        # The adapter's timeout is an exception, which CooperBench writes as the error.
        self.assertIn("timed out", outcome.error)
        self.assertEqual(outcome.end_reason, outcome.error)
        scripts = [c[-1] for c in self.docker.matching("exec", "c-agent1", "sh", "-c")]
        self.assertFalse([script for script in scripts if "pkill" in script])
        stops = [script for script in scripts if "/tmp/coord-claude.pid" in script]
        self.assertEqual(len(stops), 1)
        self.assertIn("cat /tmp/coord-claude.pid", stops[0])
        self.assertIn('kill "$pid"', stops[0])

    def test_a_stream_cut_short_keeps_its_last_line_as_the_end_reason(self):
        self.docker.stream = '{"type": "system"}\nInvalid API key. Please run /login\n'
        driver = drivers.ClaudeCliDriver()
        outcome = driver.wait(driver.start(self.job()))
        self.assertEqual(outcome.status, "Error")
        self.assertIn("Invalid API key", outcome.end_reason)
        self.assertIsNone(outcome.error, "no exception stopped the adapter, so CooperBench's error is null")

    def test_the_session_transcript_is_written_even_when_empty_as_the_adapter_writes_it(self):
        driver = drivers.ClaudeCliDriver()
        driver.wait(driver.start(self.job()))
        self.assertEqual((self.tmp / "agent1_session.jsonl").read_text(), "")
        finds = [c for c in self.docker.matching("exec", "c-agent1", "sh", "-c") if "xargs -r cat" in c[-1]]
        self.assertEqual(len(finds), 1)
        self.assertIn("find /tmp/claude-cfg/projects -name '*.jsonl'", finds[0][-1])

    def test_disallowed_tools_reach_claude_as_one_comma_joined_word(self):
        flags = drivers.claude_flags(self.job(disallowed_tools=["WebFetch", "WebSearch"]))
        self.assertIn(["--disallowedTools", "WebFetch,WebSearch"], pairs(flags))
        self.assertNotIn("--disallowedTools", drivers.claude_flags(self.job()))


class StreamParsing(unittest.TestCase):
    def result(self, **fields):
        return json.dumps({"type": "result", "subtype": "success", "num_turns": 3,
                           "usage": {"input_tokens": 10, "output_tokens": 5}, **fields}) + "\n"

    def test_the_final_result_event_gives_usage_cost_and_turns(self):
        outcome = drivers.summarize_stream(SAMPLE_STREAM)
        self.assertEqual(outcome.status, "Submitted")
        self.assertEqual((outcome.input_tokens, outcome.output_tokens, outcome.cache_read_tokens,
                          outcome.cache_write_tokens, outcome.steps), (1200, 340, 50000, 7000, 12))
        self.assertEqual(outcome.cost, 0.42)
        self.assertIsNone(outcome.error)
        self.assertEqual(drivers.summarize_stream(self.result(total_cost_usd=None)).cost, 0.0)

    def test_a_turn_limit_is_limits_exceeded_and_other_failures_are_errors(self):
        limited = drivers.summarize_stream(self.result(subtype="error_max_turns", is_error=True))
        self.assertEqual((limited.status, limited.steps, limited.input_tokens), ("LimitsExceeded", 3, 10))
        failed = drivers.summarize_stream(self.result(is_error=True, result="Usage limit reached"))
        self.assertEqual(failed.status, "Error")
        # CooperBench's error is its adapter's exception, null for an
        # Error the result event reports; coordbench says why apart.
        self.assertIsNone(failed.error)
        self.assertIn("Usage limit", failed.end_reason)
        self.assertIsNone(limited.error)
        self.assertEqual(limited.end_reason, "error_max_turns")

    def test_a_stream_without_a_result_is_an_error_with_nothing_counted(self):
        outcome = drivers.summarize_stream('{"type": "assistant"}\ngarbage\n')
        self.assertEqual((outcome.status, outcome.input_tokens, outcome.steps, outcome.cost), ("Error", 0, 0, 0.0))
        self.assertEqual((outcome.duration_ms, outcome.api_duration_ms), (None, None))
        # Nothing was measured, so result.json writes null where CooperBench writes 0.
        self.assertEqual(outcome.measured, ())
        self.assertEqual((outcome.error, outcome.end_reason), (None, drivers.NO_RESULT))

    def test_the_init_event_says_what_the_agent_ran_with(self):
        outcome = drivers.summarize_stream(SAMPLE_STREAM)
        self.assertEqual((outcome.model, outcome.claude_code_version), ("claude-test", "2.1.300"))
        self.assertEqual(outcome.tools, ["Bash", "mcp__coordbench__send_message"])
        self.assertEqual(outcome.mcp_servers, [{"name": "coordbench", "status": "connected"}])
        self.assertEqual((outcome.measured, outcome.cost_source), (drivers.USAGE_FIELDS, "claude code"))
        self.assertEqual(drivers.stream_init(self.result()), {})

    def test_the_result_event_s_durations_are_read_and_unknown_when_it_lacks_them(self):
        """Claude Code's result event carries duration_ms, the agent's wall
        time, and duration_api_ms, the part spent on the API; a result
        event without them (an older CLI) leaves both unknown, not zero."""
        timed = drivers.summarize_stream(self.result(duration_ms=123456, duration_api_ms=98765))
        self.assertEqual((timed.duration_ms, timed.api_duration_ms), (123456, 98765))
        untimed = drivers.summarize_stream(self.result())
        self.assertEqual((untimed.duration_ms, untimed.api_duration_ms), (None, None))
        self.assertEqual(drivers.summarize_stream(self.result(duration_ms=0, duration_api_ms=0)).duration_ms, 0)

    def test_patches_are_normalized_as_cooperbench_normalizes_them(self):
        self.assertEqual(drivers.normalize_patch("\n\ndiff\n \n\n"), "diff\n \n")
        self.assertEqual(drivers.normalize_patch("  \n"), "")


class TheSessionDriver(RunnerCase):
    def job(self):
        workspace = self.tmp / "run" / "workspaces" / "agent1"
        workspace.mkdir(parents=True)
        return drivers.AgentJob(run_id="r1", agent="agent1", role="peer", features=(1,), container="c-agent1",
                                prompt="THE ARM'S PROMPT", env={}, arm_dir=str(COORDBENCH / "arms" / "coop"),
                                timeout_s=0.2, log_path=str(self.tmp / "run" / "agent1_stream.jsonl"),
                                workspace=str(workspace))

    def test_the_prompt_says_where_to_work_how_to_run_and_how_to_finish(self):
        job = self.job()
        handle = drivers.SessionDriver().start(job)
        prompt_file = self.tmp / "run" / "agent1.session-prompt.md"
        self.assertEqual(handle["prompt_file"], str(prompt_file))
        json.dumps(handle)
        text = prompt_file.read_text()
        self.assertTrue(text.startswith("# coordbench: you are agent1 in run r1"))
        self.assertIn(f"Your workspace is `{job.workspace}`", text)
        self.assertIn("docker exec -w /workspace/repo c-agent1 bash -lc '<command>'", text)
        self.assertIn(str(self.tmp / "run" / "bin" / "agent1"), text)
        self.assertIn(f"Do not read or change the other agents' workspaces (the other\n  directories under "
                      f"`{self.tmp / 'run' / 'workspaces'}`)", text)
        self.assertIn(f"As the very last step, after `patch.txt` is written, create the file\n"
                      f"  `{job.workspace}/.coordbench-done`", text)
        self.assertTrue(text.endswith("\n\n---\n\nTHE ARM'S PROMPT"))
        self.assertEqual(self.docker.calls, [])

    def test_wait_returns_once_the_marker_appears_with_the_patch(self):
        job = self.job()
        driver = drivers.SessionDriver(poll_seconds=0.01)
        handle = driver.start(job)
        (Path(job.workspace) / "patch.txt").write_text("\n" + PATCHES["agent1"] + "\n\n")
        (Path(job.workspace) / ".coordbench-done").touch()
        # The marker's own time is when the agent stopped, however late
        # finish looks at it.
        os.utime(Path(job.workspace) / ".coordbench-done", (1_700_000_000, 1_700_000_000))
        outcome = driver.wait(handle)
        self.assertEqual((outcome.status, outcome.patch), ("Submitted", PATCHES["agent1"]))
        self.assertEqual(outcome.ended_at, 1_700_000_000)
        # A subagent leaves no result event, so its durations are unknown.
        self.assertEqual((outcome.duration_ms, outcome.api_duration_ms), (None, None))
        self.assertFalse(Path(job.log_path).exists(), "no file named like a stream that is not one")
        logged = json.loads((self.tmp / "run" / "agent1_session_outcome.json").read_text())
        self.assertEqual((logged["type"], logged["status"], logged["patch_lines"]), ("coordbench_session", "Submitted", 6))
        # The marker's time, which export.py takes for the agent's end.
        self.assertEqual(logged["ended_at"], 1_700_000_000)
        self.assertEqual(logged["transcript"], {"source": None, "matches": 0,
                                                "note": "no subagent transcript carried the run token"})
        # Without a transcript nothing is measured, so result.json writes null.
        self.assertEqual(outcome.measured, ())

    def test_an_agents_phase_ends_when_the_driver_says_the_agent_stopped(self):
        """finish may run long after the marker appeared; the recorded wall
        time is the agent's, not the delay."""
        started = runner.now()
        plan = {"agents": [{"id": "agent1", "started_at": started.isoformat()}], "timings": []}

        class Stub:
            def wait(self, handle):
                return drivers.AgentOutcome(status="Submitted", ended_at=started.timestamp() + 90)

        runner.wait_agents(plan, Stub(), {"agent1": {}})
        record = plan["timings"][0]
        self.assertEqual((record["phase"], record["agent"]), ("agent", "agent1"))
        self.assertAlmostEqual(record["seconds"], 90, places=3)

    def test_wait_gives_up_at_the_deadline(self):
        driver = drivers.SessionDriver(poll_seconds=0.01)
        started = time.time()
        outcome = driver.wait(driver.start(self.job()))
        self.assertLess(time.time() - started, 5)
        self.assertEqual(outcome.status, "Error")
        self.assertIn(".coordbench-done", outcome.error)

    def test_each_look_for_the_marker_is_a_marker_poll_in_debug_alone(self):
        memory = self.memory()
        job = self.job()
        driver = drivers.SessionDriver(poll_seconds=0.01)
        marker = Path(job.workspace) / ".coordbench-done"
        marker.touch()
        self.assertEqual(driver.wait(driver.start(job)).status, "Error", "start removes a marker left over")
        self.assertEqual(memory, [])
        trace.set_level("debug")
        job.timeout_s = 5.0
        handle = driver.start(job)
        threading.Timer(0.1, marker.touch).start()
        outcome = driver.wait(handle)
        self.assertEqual(outcome.status, "Submitted")
        polls = [event for event in memory if event["kind"] == "marker_poll"]
        self.assertGreaterEqual(len(polls), 2)
        self.assertEqual({event["agent"] for event in polls}, {"agent1"})
        self.assertEqual([event["found"] for event in polls], [False] * (len(polls) - 1) + [True])
        for poll in polls:
            self.assertEqual(set(poll), {"kind", "t", "ts", "agent", "found"})
        # The prompt it wrote and the line it logged are announced too.
        self.assertEqual([event["file"] for event in memory if event["kind"] == "wrote"],
                         ["agent1.session-prompt.md", "agent1_session_outcome.json"])

    def test_drivers_load_by_name_or_from_a_file(self):
        self.assertIsInstance(drivers.load_driver("session"), drivers.SessionDriver)
        path = self.tmp / "mine.py"
        path.write_text("class Mine:\n    name = 'mine'\n    def start(self, job): return job\n"
                        "    def wait(self, handle): return None\n")
        self.assertEqual(drivers.load_driver(f"{path}:Mine").name, "mine")
        with self.assertRaises(ValueError):
            drivers.load_driver("nonesuch")


class Runs(RunnerCase):
    def test_a_credential_an_agent_printed_is_scrubbed_from_every_file_the_pair_left(self):
        """An agent that prints its environment writes the subscription
        token into its stream, its transcript and what it told its peer;
        a pair's files may be published (a workflow's artifact), so the
        exact value is replaced wherever it landed, the gzip trajectory
        included, once the pair's last file is written."""
        confession = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"my environment: CLAUDE_CODE_OAUTH_TOKEN={SECRET}"}]}})
        self.docker.stream = confession + "\n" + SAMPLE_STREAM
        self.docker.transcript = json.dumps({"type": "assistant", "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
                                             "message": {"role": "assistant", "content": [
                                                 {"type": "text", "text": f"token {SECRET} here"}]}}) + "\n"

        def agent_tells_its_peer(arguments, environment):
            if env_value(arguments, "COORD_RPC_URL").endswith("/agent1"):
                call_tool(env_value(arguments, "COORD_RPC_URL"), "send_message",
                          {"to": "agent2", "text": f"use {SECRET}"}, token=environment["COORD_AGENT_TOKEN"])

        self.docker.on_claude = agent_tells_its_peer
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        pair = self.pair()
        stream = (pair / "agent1_stream.jsonl").read_text()
        self.assertIn(runner.SCRUBBED, stream)
        self.assertIn(runner.SCRUBBED, (pair / "events.jsonl").read_text())
        self.assertIn(runner.SCRUBBED, (pair / "agent1_session.jsonl").read_text())
        self.assertIn("is scrubbed out", err)
        for path in self.logs.rglob("*"):
            if path.is_file():
                data = gzip.open(path, "rb").read() if path.suffix == ".gz" else path.read_bytes()
                self.assertNotIn(SECRET.encode(), data, str(path))
        self.assert_secret_nowhere()

    def test_a_coop_pair_leaves_cooperbench_s_layout(self):
        def agent_sends_a_message(arguments, environment):
            # As claude in the container would: the hub's token from the
            # container's environment, which the exec inherits.
            if env_value(arguments, "COORD_RPC_URL").endswith("/agent1"):
                call_tool(env_value(arguments, "COORD_RPC_URL"), "send_message", {"to": "agent2", "text": "hi"},
                          token=environment["COORD_AGENT_TOKEN"])

        self.docker.on_claude = agent_sends_a_message
        code, _, err = self.main(*self.run_arguments("coop", "--features", "6,1"))
        self.assertEqual(code, 0, err)
        pair = self.pair()
        for name in ("result.json", "eval.json", "agent1.patch", "agent6.patch", "agent1_stream.jsonl",
                     "agent2_stream.jsonl", "agent1_session.jsonl", "agent2_session.jsonl", "agent1_prompt.md",
                     "agent1_traj.json", "agent6_traj.json", "conversation.json", "agent1_sent.jsonl",
                     "conversation_store.json", "events.jsonl", "metrics.json"):
            self.assertTrue((pair / name).is_file(), name)
        self.assertFalse((pair / "agent2_sent.jsonl").exists(), "a sender's log is written only when it sent")
        # export.py's trajectory for the viewers, made once the pair was scored.
        self.assertNotIn("exporting the trajectory failed", err)
        with gzip.open(pair / "trajectory.json.gz", "rt", encoding="utf-8") as exported:
            trajectory = json.load(exported)
        self.assertEqual(trajectory["metadata"]["coordbench"]["outcome"]["both_passed"], True)
        self.assertEqual(trajectory["metadata"]["coordbench"]["anchor"], "run_started")
        self.assertEqual((pair / "agent1.patch").read_text(), PATCHES["agent1"])
        self.assertEqual((pair / "agent6.patch").read_text(), PATCHES["agent2"])
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(list(result)[:len(COOPERBENCH_COOP_KEYS)], COOPERBENCH_COOP_KEYS)
        self.assertEqual((result["setting"], result["arm"], result["driver"], result["agent_framework"]),
                         ("coop", "coop", "claude-cli", "claude_code"))
        self.assertEqual((result["repo"], result["task_id"], result["features"], result["model"]),
                         (REPO, TASK, [1, 6], "claude-test"))
        self.assertEqual((result["log_dir"], result["isolation"], result["messages_sent"]), (str(pair), "container", 1))
        self.assertEqual(set(result["agents"]), {"agent1", "agent2"})
        for agent, feature in (("agent1", 1), ("agent2", 6)):
            record = result["agents"][agent]
            self.assertEqual(set(record), AGENT_KEYS)
            self.assertEqual(list(record)[:len(COOPERBENCH_AGENT_KEYS)], COOPERBENCH_AGENT_KEYS)
            self.assertEqual((record["feature_id"], record["status"], record["input_tokens"], record["steps"]),
                             (feature, "Submitted", 1200, 12))
            # The fields CooperBench's coop.py writes and its summary reads.
            self.assertEqual((record["cost"], record["patch_lines"], record["error"]), (0.42, 6, None))
            self.assertEqual((record["end_reason"], record["cost_source"]), (None, "claude code"))
            # Claude Code's own clock for the agent, from the result event.
            self.assertEqual((record["duration_ms"], record["api_duration_ms"]), (95000, 61000))
            # What the stream's init event says the agent ran with.
            self.assertEqual((record["model_requested"], record["model"], record["model_differs"]),
                             ("claude-test", "claude-test", False))
            self.assertEqual((record["claude_code_version"], record["tools"], record["mcp_servers"]),
                             ("2.1.300", ["Bash", "mcp__coordbench__send_message"],
                              [{"name": "coordbench", "status": "connected"}]))
        self.assertAlmostEqual(result["total_cost"], 0.84)
        self.assertEqual(result["total_steps"], 24)
        self.assertEqual(result["metrics"]["messages_sent"], 1)
        agents = [r for r in result["timings"] if r["phase"] == "agent"]
        self.assertAlmostEqual(result["agents_seconds"], runner.span_seconds(agents), places=6)
        # The trajectories coop.py writes, named by feature, from each
        # agent's session transcript (empty here, so no messages).
        for name, agent, feature in (("agent1_traj.json", "agent1", 1), ("agent6_traj.json", "agent2", 6)):
            trajectory = json.loads((pair / name).read_text())
            self.assertEqual(list(trajectory), TRAJECTORY_KEYS)
            self.assertEqual((trajectory["agent_id"], trajectory["feature_id"], trajectory["model"],
                              trajectory["status"], trajectory["cost"], trajectory["messages"]),
                             (agent, feature, "claude-test", "Submitted", 0.42, []))
        # The conversation files, from the message events through conversation.py.
        conversation = json.loads((pair / "conversation.json").read_text())
        self.assertEqual([(m["from"], m["to"]) for m in conversation], [("agent1", "agent2")])
        store = json.loads((pair / "conversation_store.json").read_text())
        self.assertEqual((store["total_messages"], len(store["messages"])), (1, 1))
        self.assertEqual(set(store), {"messages", "export_timestamp", "total_messages"})
        # The run's config.json and summary.json, as CooperBench's runner writes them.
        config = json.loads((self.logs / "t" / "config.json").read_text())
        self.assertEqual((config["run_name"], config["setting"], config["total_tasks"], config["concurrency"]),
                         ("t", "coop", 1, 1))
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(summary["results"], [{"task": f"{REPO}/{TASK}/1,6", "status": "done",
                                               "cost": result["total_cost"], "eval": "pass"}])
        self.assertEqual((summary["completed"], summary["pass_rate"]), (1, 1.0))
        # Every phase of the pair is timed, the agents and their
        # provisioning one by one, and the score and the teardown are in
        # result.json though it was written before them.
        self.assert_timings_well_formed(result["timings"])
        self.assertEqual(phases(result["timings"]),
                         [("hub", None), ("run_create", None), ("provision", "agent1"), ("provision", "agent2"),
                          ("agent", "agent1"), ("agent", "agent2"), ("close", None), ("teardown", None),
                          ("score", None), ("cascade", None)])
        self.assertRegex(err, r"timings: hub [0-9.]+ s, run_create [0-9.]+ s, provision [0-9.]+ s, "
                              r"agents [0-9.]+ s \(agent1 [0-9.]+ s, agent2 [0-9.]+ s\), close [0-9.]+ s, "
                              r"score [0-9.]+ s, cascade [0-9.]+ s, teardown [0-9.]+ s\n")
        self.assertEqual(json.loads((pair / "metrics.json").read_text()), result["metrics"])
        events = [json.loads(line) for line in (pair / "events.jsonl").read_text().splitlines()]
        self.assertIn("message", [event["kind"] for event in events])
        evaluation = json.loads((pair / "eval.json").read_text())
        self.assertEqual(list(evaluation), CASCADE_EVAL_KEYS)
        self.assertEqual((evaluation["both_passed"], evaluation["setting"], evaluation["features"]),
                         (True, "coop", [1, 6]))
        self.assertEqual(evaluation["cascade"], CASCADE_BLOCK)
        (run_id, handed, cascade_settings), = self.cascade.calls
        self.assertEqual(run_id, result["run_id"])
        self.assertEqual({key: handed[key] for key in COOP_EVAL_KEYS}, {key: evaluation[key] for key in COOP_EVAL_KEYS})
        self.assertEqual(cascade_settings, {"cooperbench_dir": str(self.cooperbench),
                                            "dataset_dir": str(self.cooperbench / "dataset"), "run_dir": str(pair),
                                            "scoring_timeout_s": runner.SCORING_TIMEOUT_SECONDS})
        kind, arguments, cooperbench_dir = self.scorer.calls[0]
        self.assertEqual(kind, "merged")
        self.assertEqual(arguments, {"repo_name": REPO, "task_id": TASK, "feature1_id": 1, "feature2_id": 6,
                                     "backend": "docker", "dataset_dir": str(self.cooperbench / "dataset"),
                                     "patch1": PATCHES["agent1"], "patch2": PATCHES["agent2"]})
        self.assertEqual(cooperbench_dir, str(self.cooperbench))
        containers = [c["container"] for c in json.loads((pair / "plan.json").read_text())["agents"]]
        self.assertIn(["rm", "-f", *containers], self.docker.commands())
        # Once the metrics and the events are in hand the run is released,
        # so the hub no longer holds it (it used to keep every run closed).
        self.assertNotIn(result["run_id"], self.hub.runs)
        self.assertEqual(self.hub.run_count(), 0)
        self.assertNotIn("releasing the run", err)
        self.assert_secret_nowhere()
        self.assert_tokens_kept_private(json.loads((pair / "plan.json").read_text()), err)

    def test_a_hub_that_cannot_release_a_run_leaves_a_warning_and_the_pair_its_verdict(self):
        """A hub without the DELETE route answers 405; the run then stays
        on it, which costs memory and nothing else."""
        with mock.patch.object(runner.HubClient, "release",
                               side_effect=runner.HubError("DELETE /api/runs/x: HTTP 405: no", 405)):
            code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertIn("releasing the run on the hub failed: DELETE /api/runs/x: HTTP 405", err)
        self.assertNotIn("closing the run on the hub failed", err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertNotIn("error", result)
        self.assertEqual(result["metrics"]["messages_sent"], 0)
        self.assertEqual(json.loads((self.pair() / "eval.json").read_text())["both_passed"], True)
        self.assertTrue(self.hub.runs[result["run_id"]].run.closed)

    def test_a_hub_that_closed_the_run_but_cannot_give_its_events_keeps_the_metrics(self):
        """Only the events failed: the metrics the close returned are the
        run's, the warning says what is missing and nothing else, and the
        run is released all the same (it used to stay on the hub, with
        its metrics dropped under a warning that said closing failed)."""
        with mock.patch.object(runner.HubClient, "events",
                               side_effect=runner.HubError("GET /api/runs/x/events: HTTP 500: no", 500)):
            code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertIn("fetching the run's events from the hub failed", err)
        self.assertIn("HTTP 500", err)
        self.assertNotIn("closing the run on the hub failed", err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertNotIn("error", result)
        self.assertEqual(result["metrics"]["messages_sent"], 0)
        self.assertIn("tool_calls", result["metrics"])
        self.assertEqual((self.pair() / "events.jsonl").read_text(), "")
        self.assertEqual(self.hub.run_count(), 0)
        self.assertEqual(phases(result["timings"]).count(("close", None)), 1)

    def test_a_hub_that_cannot_close_the_run_leaves_it_unreleased_with_empty_metrics(self):
        with mock.patch.object(runner.HubClient, "close_run",
                               side_effect=runner.HubError("POST /api/runs/x/close: HTTP 500: no", 500)):
            code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertIn("closing the run on the hub failed", err)
        self.assertNotIn("fetching the run's events", err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertEqual(result["metrics"], {})
        self.assertEqual(self.hub.run_count(), 1)

    def test_the_summary_line_reads_the_phases_that_happened_and_tells_the_agents_apart(self):
        """The agents run at once, so their phase is the span from the
        first start to the last end, each agent's own time in brackets;
        a run's own phase is its seconds; what did not happen is absent."""
        plan = {"timings": [
            timing("provision", "agent1", 0, 20), timing("provision", "agent2", 20, 22),
            timing("agent", "agent1", 42, 1030), timing("agent", "agent2", 102, 980),
            timing("score", None, 1100, 95), timing("teardown", None, 1195, 6),
        ]}
        self.assertEqual(runner.timings_summary(plan),
                         "timings: provision 42 s, agents 1040 s (agent1 1030 s, agent2 980 s), score 95 s, "
                         "teardown 6.0 s")
        solo = {"timings": [timing("hub", None, 0, 0.25), timing("agent", "agent1", 1, 300)]}
        self.assertEqual(runner.timings_summary(solo), "timings: hub 0.2 s, agents 300 s")
        self.assertEqual(runner.timings_summary({}), "timings: nothing recorded")

    def test_release_asks_the_hub_to_forget_the_run_and_an_unknown_run_is_404(self):
        client = runner.HubClient(self.hub_url, ADMIN_SECRET)
        answer = client.create_run("coop", "r-release", [1, 6], {1: "Do 1.", 6: "Do 6."}, {})
        self.assertEqual(len(answer["agents"]), 2)
        self.assertEqual(client.release("r-release"), {"run_id": "r-release", "released": True})
        self.assertEqual(self.hub.run_count(), 0)
        with self.assertRaises(runner.HubError) as refused:
            client.release("r-release")
        self.assertEqual(refused.exception.status, 404)
        with self.assertRaises(runner.HubError) as refused:
            client.close_run("r-release")
        self.assertEqual(refused.exception.status, 404)
        plan = {"hub_run": False, "run_id": "r-release", "warnings": []}
        self.assertEqual(runner.close_run(plan, client), ({}, [], {}))
        self.assertEqual(plan["warnings"], [])

    def test_a_solo_pair_is_scored_as_one_patch_and_recorded_as_solo_py_records_it(self):
        code, _, err = self.main(*self.run_arguments("solo", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        pair = self.pair("solo")
        self.assertEqual((pair / "solo.patch").read_text(), PATCHES["agent1"])
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual(result["setting"], "solo")
        # solo.py's singular "agent" block, its keys first and in its
        # order, coordbench's after them; no "agents", no messages_sent.
        self.assertEqual(list(result)[:len(COOPERBENCH_SOLO_KEYS)], COOPERBENCH_SOLO_KEYS)
        self.assertNotIn("agents", result)
        self.assertNotIn("messages_sent", result)
        block = result["agent"]
        self.assertEqual(list(block)[:9], ["status", "cost", "steps", "input_tokens", "output_tokens",
                                          "cache_read_tokens", "cache_write_tokens", "patch_lines", "error"])
        self.assertEqual((block["id"], block["features"], block["role"], block["status"]),
                         ("agent1", [1, 6], "solo", "Submitted"))
        self.assertEqual((result["total_cost"], result["total_steps"]), (0.42, 12))
        self.assertTrue(runner.pair_finished(pair))
        # CooperBench's solo agent is "solo": its files are named so.
        for name in ("solo_stream.jsonl", "solo_session.jsonl", "solo_prompt.md", "solo_traj.json"):
            self.assertTrue((pair / name).is_file(), name)
        self.assertFalse((pair / "agent1_stream.jsonl").exists())
        trajectory = json.loads((pair / "solo_traj.json").read_text())
        self.assertEqual(list(trajectory), ["repo", "task_id", "features", "agent_id", "model", "status", "cost",
                                            "steps", "messages"])
        self.assertEqual((trajectory["agent_id"], trajectory["features"]), ("solo", [1, 6]))
        # Solo has no partner: no conversation files, and no cascade.
        self.assertFalse((pair / "conversation.json").exists())
        evaluation = json.loads((pair / "eval.json").read_text())
        self.assertEqual(list(evaluation), SOLO_EVAL_KEYS)
        self.assertIsNone(evaluation["merge"])
        self.assertEqual(self.cascade.calls, [])
        kind, arguments, _ = self.scorer.calls[0]
        self.assertEqual((kind, arguments["patch"]), ("solo", PATCHES["agent1"]))
        # timeline.py finds the agent behind the singular block.
        import timeline
        self.assertEqual([row["agent"] for row in timeline.analyse(timeline.load_run(pair))["agents"]], ["agent1"])

    def test_a_subset_runs_every_pair_and_finished_pairs_are_kept(self):
        code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash"))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.pair(features="f1_f6") / "eval.json").exists())
        self.assertTrue((self.pair(features="f2_f10") / "eval.json").exists())
        before = len(self.container_work())
        code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.container_work()), before)
        self.assertIn("skipped", err)
        # The second session's summary counts both pairs as skipped, their
        # evaluations too, as CooperBench's runner and evaluator count them.
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual((summary["completed"], summary["skipped"], summary["eval"]["skipped"]), (0, 2, 2))
        self.assertEqual([r["status"] for r in summary["results"]], ["skip", "skip"])

    def test_a_finished_pair_without_eval_json_is_only_scored_again(self):
        self.main(*self.run_arguments("coop", "--features", "1,6"))
        (self.pair() / "eval.json").unlink()
        before = len(self.container_work())
        code, _, _ = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.container_work()), before)
        self.assertEqual(len(self.scorer.calls), 2)
        self.assertTrue((self.pair() / "eval.json").exists())

    def test_a_finished_pair_whose_scoring_failed_is_scored_again_and_one_scored_is_not(self):
        """The Actions kit fetches such a pair for its scoring (artifacts.py
        calls it unscored), so a run that skipped it for having an
        eval.json would leave it unscored on every dispatch of the tag;
        the trajectory the viewers read is made again with the new
        verdict, as rescore makes it."""
        self.main(*self.run_arguments("coop", "--features", "1,6"))
        evaluation = json.loads((self.pair() / "eval.json").read_text())
        (self.pair() / "eval.json").write_text(json.dumps({**evaluation, "both_passed": False,
                                                           "error": "DockerError: the sandbox did not start"}))
        runner.export_pair({"run_dir": str(self.pair()), "cooperbench_dir": str(self.cooperbench), "run_id": "t"})

        def exported_verdict():
            with gzip.open(self.pair() / "trajectory.json.gz", "rt", encoding="utf-8") as exported:
                return json.load(exported)["metadata"]["coordbench"]["outcome"]["both_passed"]

        self.assertIs(exported_verdict(), False)
        self.assertFalse(runner.scored(self.pair()))
        before = len(self.container_work())
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.container_work()), before, "no container, no agent")
        self.assertEqual(len(self.scorer.calls), 2)
        self.assertIsNone(json.loads((self.pair() / "eval.json").read_text())["error"])
        self.assertTrue(runner.scored(self.pair()))
        self.assertIs(exported_verdict(), True)
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual((summary["eval"]["passed"], summary["eval"]["skipped"]), (1, 0))
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual((code, len(self.scorer.calls)), (0, 2))
        self.assertIn("skipped", err)

    def test_a_pair_whose_agent_ran_out_its_time_is_scored_kept_and_no_error(self):
        """A timed-out agent is marked Error, as CooperBench marks it, and
        its pair is kept with its patch scored (pair_finished); the run's
        exit status says the same, so the workflow's job is not red and
        its marker is uploaded, and the next run leaves the pair alone."""
        self.docker.claude_times_out = True
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertEqual(len(result["agents"]), 2)
        for agent in result["agents"].values():
            self.assertEqual(agent["status"], "Error")
            self.assertTrue(agent["end_reason"].startswith(drivers.TIMED_OUT), agent)
        self.assertTrue((self.pair() / "eval.json").exists())
        self.assertTrue(runner.pair_finished(self.pair()))
        before = len(self.container_work())
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual((code, len(self.container_work())), (0, before))
        self.assertIn("skipped", err)
        # An agent that failed for another cause is still the pair's error.
        failed = drivers.AgentOutcome(status="Error", end_reason="Usage limit reached")
        timed_out = drivers.AgentOutcome(status="Error", end_reason=f"{drivers.TIMED_OUT}7200 seconds")
        self.assertEqual(runner.verdict({"a": timed_out}, {"both_passed": True}), "pass")
        self.assertEqual(runner.verdict({"a": timed_out, "b": failed}, {"both_passed": True}), "error")
        self.assertEqual(runner.verdict({"a": timed_out}, {"error": "DockerError: gone"}), "error")

    def test_rescore_scores_from_the_patches_and_never_starts_an_agent(self):
        """An agent in Error makes a pair unfinished, so run would run it
        again with its agents; rescore scores it from its patches alone,
        and leaves the run's config.json and result.json as they were."""
        self.main(*self.run_arguments("coop", "--features", "1,6"))
        pair = self.pair()
        result = json.loads((pair / "result.json").read_text())
        result["agents"]["agent2"]["status"] = "Error"
        (pair / "result.json").write_text(json.dumps(result))
        evaluation = json.loads((pair / "eval.json").read_text())
        evaluation.pop("cascade")
        (pair / "eval.json").write_text(json.dumps(evaluation))
        config, kept = (self.logs / "t" / "config.json").read_bytes(), (pair / "result.json").read_bytes()
        before, scored = len(self.container_work()), len(self.scorer.calls)
        code, _, err = self.main("rescore", "--run-dir", str(self.logs / "t"))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.container_work()), before, "no container, no agent")
        self.assertEqual(len(self.scorer.calls), scored + 1)
        self.assertIn("cascade", json.loads((pair / "eval.json").read_text()))
        self.assertEqual(((self.logs / "t" / "config.json").read_bytes(), (pair / "result.json").read_bytes()),
                         (config, kept))
        self.assertIn("rescored: 1 pass", err)
        # A pair whose eval.json has its cascade is scored again only with --force.
        code, _, err = self.main("rescore", "--run-dir", str(pair))
        self.assertEqual((code, len(self.scorer.calls)), (0, scored + 1))
        self.assertIn("rescored: 1 skipped", err)
        code, _, err = self.main("rescore", "--run-dir", str(pair), "--force")
        self.assertEqual((code, len(self.scorer.calls)), (0, scored + 2))
        self.assertEqual(self.main("rescore", "--run-dir", str(self.tmp / "nothing"))[0], 1)

    def test_rescore_scores_again_a_pair_whose_scoring_failed_whatever_its_setting(self):
        """rescore decides as run does (scored): a pair whose eval.json
        holds an error has no verdict, solo or merged, with a cascade
        block or without."""
        for setting in ("solo", "coop"):
            with self.subTest(setting=setting):
                self.main(*self.run_arguments(setting, "--features", "1,6"))
                pair = self.pair(setting)
                evaluation = json.loads((pair / "eval.json").read_text())
                (pair / "eval.json").write_text(json.dumps({**evaluation, "error": "DockerError: gone"}))
                scored = len(self.scorer.calls)
                code, _, err = self.main("rescore", "--run-dir", str(pair))
                self.assertEqual((code, len(self.scorer.calls)), (0, scored + 1), err)
                self.assertTrue(runner.scored(pair))
                code, _, err = self.main("rescore", "--run-dir", str(pair))
                self.assertEqual((code, len(self.scorer.calls)), (0, scored + 1), err)

    def test_prepare_then_finish_with_the_session_driver(self):
        code, out, err = self.main("prepare", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                   "--features", "1,6", "--hub", self.hub_url, "--name", "t",
                                   "--cooperbench-dir", str(self.cooperbench))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        pair = self.pair()
        self.assertEqual(plan["run_dir"], str(pair))
        self.assertEqual([a["id"] for a in plan["agents"]], ["agent1", "agent2"])
        # The model to run each subagent with goes to the orchestrating session.
        self.assertEqual(set(plan["agents"][0]), {"id", "prompt_file", "model"})
        self.assertEqual({a["model"] for a in plan["agents"]}, {"claude-sonnet-5-5"})
        # The first prepare writes the run's config.json.
        config = json.loads((self.logs / "t" / "config.json").read_text())
        self.assertEqual((config["driver"], config["isolation"], config["total_tasks"]), ("session", "advisory", 1))
        saved = json.loads((pair / "plan.json").read_text())
        self.assertTrue(self.hub.runs[saved["run_id"]].tokens)
        for agent, entry in zip(saved["agents"], plan["agents"]):
            workspace = Path(agent["workspace"])
            self.assertEqual(workspace, pair / "workspaces" / agent["id"])
            self.assertIn(".coordbench-done", (workspace / ".git" / "info" / "exclude").read_text())
            started = self.docker.matching("run", "-d", "--name", agent["container"])[0]
            self.assertIn(f"{workspace}:/workspace/repo", started)
            self.assertIn(["exec", agent["container"], "git", "config", "--global", "--add", "safe.directory",
                           "/workspace/repo"], self.docker.commands())
            self.assertEqual(entry["prompt_file"], str(pair / f"{agent['id']}.session-prompt.md"))
            self.assertIn("Do 1." if agent["id"] == "agent1" else "Do 6.", Path(entry["prompt_file"]).read_text())
            wrapper = pair / "bin" / agent["id"] / "coop-send"
            self.assertIn(f"{self.hub_url}/rpc/{saved['run_id']}/{agent['id']}", wrapper.read_text())
            # The agent's token is in a file of its own beside the wrappers,
            # which read it when they run; the wrapper's text has none.
            token_file = pair / "bin" / agent["id"] / "token"
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(token_file.read_text(), agent["token"] + "\n")
            self.assertIn(f'export COORD_AGENT_TOKEN="$(cat {token_file})"\n', wrapper.read_text())
            self.assertNotIn(agent["token"], wrapper.read_text())
        # One word of text: coop-send refuses more, as coop_msg.py's argparse
        # does. The wrapper brings the token itself; the environment has none.
        without_token = {name: value for name, value in os.environ.items() if name != "COORD_AGENT_TOKEN"}
        sent = subprocess.run([str(pair / "bin" / "agent1" / "coop-send"), "agent2", "hello there"],
                              capture_output=True, text=True, timeout=60, env=without_token)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        for agent in saved["agents"]:
            workspace = Path(agent["workspace"])
            (workspace / "patch.txt").write_text(PATCHES[agent["id"]])
            (workspace / ".coordbench-done").touch()
        # agent1's subagent opened its first reply with its run token, as
        # its prompt asks; agent2's left no transcript to find.
        token = saved["agents"][0]["handle"]["run_token"]
        self.assertEqual(token, f"coordbench-run-token {saved['run_id']} agent1")
        self.assertIn(f"`{token}`", Path(plan["agents"][0]["prompt_file"]).read_text())
        transcript = write_transcript(self.transcripts / "proj" / "s1" / "subagents" / "agent-a1.jsonl", token)

        said = out + err
        code, out, err = self.main("finish", "--run-dir", str(pair))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"run_dir": str(pair), "verdict": "pass"})
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual((result["driver"], result["agent_framework"]), ("session", "claude_code_subagent"))
        self.assertEqual(result["isolation"], "advisory")
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Submitted"})
        for record in result["agents"].values():
            self.assertEqual(set(record), AGENT_KEYS)
            self.assertEqual(record["patch_lines"], 6)
            # No result event: steps and Claude Code's durations stay unknown.
            self.assertEqual({record[name] for name in ("steps", *DURATION_KEYS)}, {None})
        # agent1's tokens are its transcript's, each API message once, and
        # its cost an estimate from them at its model's price.
        first = result["agents"]["agent1"]
        self.assertEqual({name: first[name] for name in TRANSCRIPT_TOKENS}, TRANSCRIPT_TOKENS)
        self.assertAlmostEqual(first["cost"], TRANSCRIPT_COST)
        self.assertEqual((first["cost_source"], first["model"], first["claude_code_version"]),
                         ("estimated from tokens", "claude-sonnet-4-5", "2.1.295"))
        self.assertEqual((first["model_requested"], first["model_differs"]), ("claude-sonnet-5-5", True))
        self.assertEqual((pair / "agent1_session.jsonl").read_text(), transcript.read_text())
        outcome = json.loads((pair / "agent1_session_outcome.json").read_text())
        self.assertEqual(outcome["transcript"], {"source": str(transcript), "matches": 1})
        self.assertFalse((pair / "agent1_stream.jsonl").exists())
        trajectory = json.loads((pair / "agent1_traj.json").read_text())
        # One message per transcript line, as parse_session_jsonl gives them.
        self.assertEqual([m["role"] for m in trajectory["messages"]], ["user", "assistant", "assistant", "assistant"])
        self.assertTrue(trajectory["messages"][1]["content"].startswith(token))
        # agent2 left no transcript: its usage is unknown, never zero.
        second = result["agents"]["agent2"]
        self.assertEqual({second[name] for name in USAGE_KEYS}, {None})
        self.assertEqual((second["cost_source"], second["model"]), (None, None))
        self.assertAlmostEqual(result["total_cost"], TRANSCRIPT_COST)
        self.assertIsNone(result["total_steps"])
        self.assertEqual(result["metrics"]["messages_sent"], 1)
        # finish refreshes summary.json from every pair of the run on disk.
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(summary["results"], [{"task": f"{REPO}/{TASK}/1,6", "status": "done",
                                               "cost": result["total_cost"], "eval": "pass"}])
        self.assertEqual(summary["total_time_source"], "run_totals wall_time")
        self.assertEqual(summary["claude_code"]["cost_sources"], ["estimated from tokens"])
        # The agent's phase runs from prepare's start, kept in the plan,
        # to the marker finish saw: one record across the two processes.
        self.assert_timings_well_formed(result["timings"])
        self.assertEqual(phases(result["timings"]),
                         [("hub", None), ("run_create", None), ("provision", "agent1"), ("provision", "agent2"),
                          ("agent", "agent1"), ("agent", "agent2"), ("close", None), ("teardown", None),
                          ("score", None), ("cascade", None)])
        for agent, record in zip(saved["agents"], [r for r in result["timings"] if r["phase"] == "agent"]):
            self.assertEqual(record["started"], agent["started_at"])
            self.assertGreater(record["seconds"], 0)
        prepared = phases(saved["timings"])
        self.assertEqual(result["timings"][:len(prepared)], saved["timings"])
        self.assertRegex(err, r"timings: .*agents [0-9.]+ s \(agent1 [0-9.]+ s, agent2 [0-9.]+ s\).*teardown")
        self.assertEqual(json.loads((pair / "plan.json").read_text())["timings"], result["timings"])
        # Released after its metrics and events were fetched; finish saves
        # the plan last, so it says the hub holds the run no more.
        self.assertEqual(self.hub.run_count(), 0)
        self.assertFalse(json.loads((pair / "plan.json").read_text())["hub_run"])
        self.assertEqual((pair / "agent6.patch").read_text(), PATCHES["agent2"])
        self.assertEqual(list(json.loads((pair / "eval.json").read_text())), CASCADE_EVAL_KEYS)
        self.assertFalse((pair / "workspaces").exists())
        self.assertIn(["rm", "-f", *[a["container"] for a in saved["agents"]]], self.docker.commands())
        self.assert_secret_nowhere(said, out, err)
        self.assert_tokens_kept_private(saved, said, out, err, sent.stdout, sent.stderr)

    def test_a_hub_named_by_hub_is_asked_for_its_token_before_anything_is_provisioned(self):
        os.environ.pop("COORD_ADMIN_TOKEN")
        code, out, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 2)
        self.assertIn("wants its admin token in COORD_ADMIN_TOKEN", err)
        os.environ["COORD_ADMIN_TOKEN"] = "not-the-hub-s-token"
        code, out, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 2)
        self.assertIn("refuses the token in COORD_ADMIN_TOKEN", err)
        self.assertNotIn("not-the-hub-s-token", out + err)
        self.assertEqual(self.docker.calls, [])
        self.assertEqual(self.hub.run_count(), 0)

    def test_a_hub_the_runner_starts_may_load_the_arm_directory_and_each_arm_dir(self):
        arm_dir = self.copy_arm("myarm")
        more = self.tmp / "more-arms"
        started = []

        def start_and_keep(*arguments, **keywords):
            process, client = REAL_START_HUB(*arguments, **keywords)
            started.append((process, client))
            return process, client

        with mock.patch.object(runner, "start_hub", side_effect=start_and_keep):
            code, out, err = self.main("run", "--arm", str(arm_dir), "--arm-dir", str(more), "--repo", REPO,
                                       "--task", str(TASK), "--features", "1,6", "--name", "t",
                                       "--model", "claude-test", "--cooperbench-dir", str(self.cooperbench))
        self.assertEqual(code, 0, err)
        (process, client), = started
        command = process.args
        allowed = [command[i + 1] for i, word in enumerate(command) if word == "--arm-dir"]
        self.assertEqual(allowed, [str(more.resolve()), str(arm_dir.resolve())])
        self.assertIsNotNone(process.poll())
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertEqual(result["arm"], "myarm")
        self.assertIn("events", result["metrics"])
        self.assertNotIn("closing the run on the hub failed", err)
        self.assertEqual(self.hub.run_count(), 0)
        self.assert_secret_nowhere(out, err, " ".join(command), secrets=(client.admin_token,))

    @unittest.skipUnless(HAS_PROC, "finish reads the token back through Linux's /proc")
    def test_prepare_then_finish_with_a_hub_the_runner_starts(self):
        code, out, err = self.main("prepare", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                   "--features", "1,6", "--name", "t", "--cooperbench-dir", str(self.cooperbench))
        self.assertEqual(code, 0, err)
        pair = self.pair()
        saved = json.loads((pair / "plan.json").read_text())
        pid, url = saved["hub_pid"], saved["hub_url"]
        self.addCleanup(runner.stop_hub_pid, pid, url)
        token = runner.hub_admin_token(pid, url)
        self.assertRegex(token, "^[0-9a-f]{64}$")
        self.assertNotIn(token.encode(), Path(f"/proc/{pid}/cmdline").read_bytes())
        self.assertFalse(runner.HubClient(url, ADMIN_SECRET).authorized())
        sent = subprocess.run([str(pair / "bin" / "agent1" / "coop-send"), "agent2", "hello"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        for agent in saved["agents"]:
            (Path(agent["workspace"]) / "patch.txt").write_text(PATCHES[agent["id"]])
            (Path(agent["workspace"]) / ".coordbench-done").touch()
        said = out + err
        code, out, err = self.main("finish", "--run-dir", str(pair))
        self.assertEqual(code, 0, err)
        self.assertNotIn("closing the run on the hub failed", err)
        self.assertEqual(json.loads((pair / "result.json").read_text())["metrics"]["messages_sent"], 1)
        deadline = time.time() + 10
        while runner.is_hub(pid, url) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(runner.is_hub(pid, url))
        self.assert_secret_nowhere(said, out, err, sent.stdout, sent.stderr, secrets=(token,))


def cooperbench_parsers():
    """CooperBench's claude_code/parsers.py, loaded on its own from the
    checkout COOPERBENCH_DIR names, or ~/cooperbench; it needs only the
    standard library."""
    for root in filter(None, [os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")]):
        path = Path(root) / "src" / "cooperbench" / "agents" / "claude_code" / "parsers.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("cooperbench_claude_code_parsers", path)
            module = importlib.util.module_from_spec(spec)
            # Its dataclass looks its own module up in sys.modules.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    return None


COOPERBENCH_PARSERS = cooperbench_parsers()


class CooperBenchParity(RunnerCase):
    """What coordbench records the way CooperBench's runner records it,
    and what it records beside it, pair by pair and run by run."""

    def result(self, setting="coop", features="f1_f6"):
        return json.loads((self.pair(setting, features) / "result.json").read_text())

    def test_the_team_arm_writes_team_under_team_as_team_py_does(self):
        code, _, err = self.main(*self.run_arguments("team", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertFalse(self.pair("coop").exists())
        result = self.result("team")
        self.assertEqual((result["setting"], result["arm"], result["lead_agent"]), ("team", "team", "agent1"))
        self.assertLess(list(result).index("lead_agent"), list(result).index("agents"))
        self.assertEqual([(a["team_role"], a["role"]) for a in result["agents"].values()],
                         [("lead", "lead"), ("member", "member")])
        self.assertEqual(list(result["agents"]["agent1"])[:2], ["feature_id", "team_role"])
        self.assertEqual(json.loads((self.pair("team") / "agent1_traj.json").read_text())["team_role"], "lead")
        # CooperBench's evaluator writes "coop" for every merged evaluation.
        self.assertEqual(json.loads((self.pair("team") / "eval.json").read_text())["setting"], "coop")
        config = json.loads((self.logs / "t" / "config.json").read_text())
        self.assertEqual(config["setting"], "team")

    def test_a_team_pair_finished_under_coop_before_the_team_setting_is_skipped_there(self):
        legacy = self.pair("coop")
        legacy.mkdir(parents=True)
        (legacy / "result.json").write_text(json.dumps({
            "repo": REPO, "task_id": TASK, "features": [1, 6], "setting": "coop", "arm": "team",
            "total_cost": 1.5, "agents": {"agent1": {"status": "Submitted"}, "agent2": {"status": "Submitted"}}}))
        (legacy / "eval.json").write_text(json.dumps({"both_passed": True, "error": None}))
        code, _, err = self.main(*self.run_arguments("team", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        self.assertIn("skipped", err)
        self.assertEqual(self.container_work(), [])
        self.assertFalse(self.pair("team").exists())
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(summary["results"], [{"task": f"{REPO}/{TASK}/1,6", "status": "skip", "cost": 1.5,
                                               "eval": "pass"}])

    def test_an_arm_may_name_its_setting_and_one_agent_is_always_solo(self):
        arm = runner.find_arm(str(self.copy_arm("mine", setting="team")))
        self.assertEqual(runner.pair_setting(arm, arm.layout([1, 6])), "team")
        self.assertEqual(runner.pair_setting(arm, arm.layout([1])), "solo")
        wrong = runner.find_arm(str(self.copy_arm("wrong", setting="pairs")))
        with self.assertRaisesRegex(ValueError, "not one of coop, team"):
            runner.pair_setting(wrong, wrong.layout([1, 6]))
        for name, setting in (("coop", "coop"), ("coop-git", "coop"), ("sdlc", "coop"), ("solo", "solo")):
            arm = runner.find_arm(name)
            self.assertEqual(runner.pair_setting(arm, arm.layout([1, 6])), setting, name)

    def test_a_subset_alone_runs_every_task_it_names_as_discover_tasks_orders_them(self):
        other = self.cooperbench / "dataset" / "other_task" / "task1"
        for feature in (1, 2, 3):
            (other / f"feature{feature}").mkdir(parents=True)
            (other / f"feature{feature}" / "feature.md").write_text(f"Do {feature}.\n")
        dataset = self.cooperbench / "dataset"
        self.assertEqual(runner.subset_tasks(dataset, "flash"),
                         [("other_task", 1, [1, 2]), (REPO, TASK, [6, 1]), (REPO, TASK, [2, 10])])
        self.assertEqual(runner.subset_tasks(dataset, "flash", repo=REPO), [(REPO, TASK, [6, 1]), (REPO, TASK, [2, 10])])
        # team3_all.json names its sets "groups"; a task it names without
        # sets gets every pair of its features; a set whose feature the task
        # lacks is left out, as discover_tasks leaves it.
        (dataset / "subsets" / "groups.json").write_text(json.dumps({"tasks": [
            {"repo": "other_task", "task_id": 1}, {"repo": REPO, "task_id": TASK, "groups": [[1, 2, 6], [1, 99]]}]}))
        self.assertEqual(runner.subset_tasks(dataset, "groups"),
                         [("other_task", 1, [1, 2]), ("other_task", 1, [1, 3]), ("other_task", 1, [2, 3]),
                          (REPO, TASK, [1, 2, 6])])
        code, _, err = self.main("run", "--arm", "coop", "--subset", "flash", "--hub", self.hub_url,
                                 "--cooperbench-dir", str(self.cooperbench), "--name", "t", "--model", "claude-test")
        self.assertEqual(code, 0, err)
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual([r["task"] for r in summary["results"]],
                         ["other_task/1/1,2", f"{REPO}/{TASK}/6,1", f"{REPO}/{TASK}/2,10"])
        self.assertTrue((self.logs / "t" / "coop" / "other_task" / "1" / "f1_f2" / "eval.json").exists())
        config = json.loads((self.logs / "t" / "config.json").read_text())
        self.assertEqual(config["total_tasks"], 3)
        self.assertEqual(set(config["images"]), {runner.image_name("other_task", 1), IMAGE})
        code, _, err = self.main("run", "--arm", "coop", "--features", "1,6", "--hub", self.hub_url,
                                 "--cooperbench-dir", str(self.cooperbench))
        self.assertEqual(code, 2)
        self.assertIn("--features needs --repo and --task", err)

    def test_concurrency_runs_pairs_at_once_and_refuses_debug(self):
        code, _, err = self.main(*self.run_arguments("coop-git", "--subset", "flash", "--concurrency", "2"))
        self.assertEqual(code, 0, err)
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(sorted(r["task"] for r in summary["results"]),
                         [f"{REPO}/{TASK}/2,10", f"{REPO}/{TASK}/6,1"])
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(json.loads((self.logs / "t" / "config.json").read_text())["concurrency"], 2)
        # The fake never reports the git server running, so each pair
        # started it, one at a time under the runner's lock.
        self.assertEqual(len(self.docker.matching("run", "-d", "--name", "coordbench-git")), 2)
        code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash", "--concurrency", "2", "--debug"))
        self.assertEqual(code, 2)
        self.assertIn("--debug traces one pair at a time", err)

    def test_a_pair_that_raises_is_its_error_and_the_run_goes_on(self):
        real = runner.run_pair

        def first_raises(settings, hub, driver, repo, task_id, features):
            if features == [1, 6]:
                raise RuntimeError("the disk is full")
            return real(settings, hub, driver, repo, task_id, features)

        with mock.patch.object(runner, "run_pair", side_effect=first_raises):
            code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash"))
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError: the disk is full", err)
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual([(r["status"], r["eval"]) for r in summary["results"]], [("error", None), ("done", "pass")])
        self.assertEqual((summary["failed"], summary["completed"]), (1, 1))

    def test_config_json_keeps_cooperbench_s_keys_first_and_says_what_its_file_cannot(self):
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6", "--option", "tool_surface=bash",
                                                     "--max-turns", "100", "--disallowed-tools", "WebFetch,WebSearch"))
        self.assertEqual(code, 0, err)
        config = json.loads((self.logs / "t" / "config.json").read_text())
        self.assertEqual(list(config)[:7], ["run_name", "agent_framework", "model", "setting", "concurrency",
                                            "total_tasks", "started_at"])
        self.assertEqual((config["agent_framework"], config["arm"], config["driver"]), ("claude_code", "coop", "claude-cli"))
        self.assertEqual((config["options"], config["max_turns"], config["timeout_s"], config["disallowed_tools"]),
                         ({"tool_surface": "bash"}, 100, 7200.0, ["WebFetch", "WebSearch"]))
        self.assertEqual((config["scoring_timeout_s"], config["cooperbench_sandbox_timeout_s"]),
                         (runner.SCORING_TIMEOUT_SECONDS, 600))
        self.assertEqual(config["differences_from_cooperbench"], list(drivers.CLI_DIFFERENCES))
        provenance = config["provenance"]
        self.assertEqual(provenance["cooperbench"]["dir"], str(self.cooperbench))
        self.assertEqual(provenance["dataset"]["dir"], str(self.cooperbench / "dataset"))
        self.assertRegex(provenance["dataset"]["sha256"], "^[0-9a-f]{64}$")
        self.assertEqual(config["images"], {IMAGE: {"image": IMAGE, "id": None, "repo_digests": None, "overlay": None}})
        self.assertEqual(config["pairs"], [{"repo": REPO, "task_id": TASK, "features": [1, 6]}])
        result = self.result()
        self.assertEqual(result["provenance"], provenance)
        self.assertEqual((result["options"], result["max_turns"]), ({"tool_surface": "bash"}, 100))
        flags = self.docker.matching("exec", "-w", "/workspace/repo")[-1][-1]
        self.assertIn("--max-turns 100 --disallowedTools WebFetch,WebSearch", flags)

    def test_the_summary_tallies_evaluations_as_cooperbench_does_whatever_the_agents_status(self):
        """An agent in Error whose pair still evaluated counts by eval.json,
        as _process_eval_result counts it; coordbench's verdict sets the pair
        apart (exit 1), the summary does not."""
        self.docker.stream = '{"type": "system", "subtype": "init"}\n'
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 1, err)
        result = self.result()
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Error"})
        for record in result["agents"].values():
            self.assertTrue(record["end_reason"].startswith(drivers.NO_RESULT), record["end_reason"])
        self.assertEqual({a["error"] for a in result["agents"].values()}, {None})
        self.assertEqual((result["total_cost"], result["total_steps"]), (None, None))
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(summary["results"], [{"task": f"{REPO}/{TASK}/1,6", "status": "done", "cost": None,
                                               "error_kind": "agent", "eval": "pass"}])
        self.assertEqual((summary["eval"]["passed"], summary["pass_rate"], summary["total_cost"]), (1, 1.0, None))
        self.assertIn("summary: 1 completed, 0 skipped, 0 failed; evals: 1 evaluated, 1 passed", err)

    def test_a_pair_that_failed_to_provision_is_an_error_in_the_summary_and_never_evaluated(self):
        self.docker.fail_seed = True
        code, _, err = self.main(*self.run_arguments("coop-git", "--features", "1,6"))
        self.assertEqual(code, 1)
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        (entry,) = summary["results"]
        self.assertEqual((entry["status"], entry["eval"], summary["failed"], summary["eval"]["total_evaluated"]),
                         ("error", None, 1, 0))
        self.assertIn("seeding the shared repository", entry["error"])

    def test_the_arm_s_artifacts_land_beside_result_json_and_none_replaces_the_runner_s_files(self):
        real = runner.HubClient.close_run

        def with_artifacts(client, run_id):
            metrics, _ = real(client, run_id)
            return metrics, {"git_log.txt": "commit 0123\n", "tasks.json": [{"id": 1, "status": "done"}],
                             "result.json": {"forged": True}, "../escape.txt": "x", "judge.json": {"forged": True}}

        with mock.patch.object(runner.HubClient, "close_run", with_artifacts):
            code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        pair = self.pair()
        self.assertEqual((pair / "git_log.txt").read_text(), "commit 0123\n")
        self.assertEqual(json.loads((pair / "tasks.json").read_text()), [{"id": 1, "status": "done"}])
        self.assertNotIn("forged", self.result())
        self.assertFalse((pair.parent / "escape.txt").exists())
        self.assertIn("the artifact 'result.json' was not written: the runner writes a file of that name", err)
        self.assertIn("the artifact '../escape.txt' was not written: it is not a plain file name", err)
        # Nor one the judge or the cascade writes there later.
        self.assertIn("the artifact 'judge.json' was not written", err)
        self.assertFalse((pair / "judge.json").exists())

    def test_the_hub_s_close_answer_gives_metrics_and_artifacts_however_it_nests_them(self):
        client = runner.HubClient("http://hub")
        answers = {"top": {"metrics": {"events": {}}, "artifacts": {"a.txt": "x"}},
                   "nested": {"metrics": {"events": {}, "artifacts": {"a.txt": "x"}}},
                   "older": {"metrics": {"events": {}}}}
        for name, answer in answers.items():
            with mock.patch.object(client, "request", return_value=answer):
                metrics, artifacts = client.close_run("r1")
            self.assertEqual(metrics, {"events": {}}, name)
            self.assertEqual(artifacts, {} if name == "older" else {"a.txt": "x"}, name)

    def test_a_cascade_that_fails_says_so_and_leaves_cooperbench_s_keys_alone(self):
        self.use_module("cascade", FakeCascade(problem=RuntimeError("no sandbox")))
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        evaluation = json.loads((self.pair() / "eval.json").read_text())
        self.assertEqual(list(evaluation), CASCADE_EVAL_KEYS)
        self.assertEqual(evaluation["cascade"], {"error": "RuntimeError: no sandbox"})
        self.assertTrue(evaluation["both_passed"])

    def test_no_cascade_runs_when_cooperbench_could_not_evaluate_or_when_cascade_py_is_absent(self):
        with mock.patch.object(runner, "cooperbench_test", side_effect=RuntimeError("docker is down")):
            self.main(*self.run_arguments("coop", "--features", "1,6"))
        evaluation = json.loads((self.pair() / "eval.json").read_text())
        self.assertEqual(list(evaluation), COOP_EVAL_KEYS)
        self.assertEqual(self.cascade.calls, [])
        self.assertIsNone(runner.sibling_module("coordbench_no_such_module"))
        self.assertIs(runner.sibling_module("cascade"), self.cascade)
        # A module of that name from elsewhere on the path is not coordbench's.
        self.assertIsNone(runner.sibling_module("json"))
        self.assertIs(runner.sibling_module("runconfig"), runner.runconfig)

    def test_each_agent_may_have_its_own_model_and_a_difference_from_what_ran_is_flagged(self):
        plan, _, driver = self.prepare("coop", agent_models={"agent2": "claude-opus-4-6"})
        self.assertEqual([job.model for job in driver.jobs], ["claude-test", "claude-opus-4-6"])
        self.assertEqual([a["model"] for a in runner.plan_summary(plan, {})["agents"]],
                         ["claude-test", "claude-opus-4-6"])
        before = len(self.docker.calls)
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6", "--agent-model", "agent3=claude-y"))
        self.assertEqual(code, 2)
        self.assertIn("--agent-model names agent3, which the arm does not lay out (agent1, agent2)", err)
        self.assertEqual(len(self.docker.calls), before, "refused before anything ran")
        for requested, observed, differs in (("claude-x", "claude-x", False), ("claude-x", "claude-x-20250929", False),
                                             ("anthropic/claude-x", "claude-x", False), ("claude-x", "claude-y", True),
                                             ("claude-x", None, None)):
            self.assertEqual(runner.model_differs(requested, observed), differs, (requested, observed))

    def test_a_dataset_dir_of_its_own_feeds_the_specs_and_the_scorer(self):
        paper = self.tmp / "paper-dataset"
        shutil.copytree(self.cooperbench / "dataset", paper)
        (paper / REPO / f"task{TASK}" / "feature1" / "feature.md").write_text("The paper's spec 1.\n")
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6", "--dataset-dir", str(paper)))
        self.assertEqual(code, 0, err)
        self.assertIn("The paper's spec 1.", (self.pair() / "agent1_prompt.md").read_text())
        _, arguments, cooperbench_dir = self.scorer.calls[0]
        self.assertEqual((arguments["dataset_dir"], cooperbench_dir), (str(paper), str(self.cooperbench)))
        self.assertEqual(self.result()["provenance"]["dataset"]["dir"], str(paper))

    @unittest.skipUnless(COOPERBENCH_PARSERS, "needs CooperBench's checkout for its parsers.py")
    def test_the_trajectory_messages_are_cooperbench_s_parse_session_jsonl(self):
        text = "".join(json.dumps(line) + "\n" for line in transcript_lines("t0"))
        text += '{"type": "user", "timestamp": "2026-10-09T21:05:49.000Z", "message": {"role": "user", "content": [' \
                '{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "a.py"}]}]}}\n' \
                'not json\n{"type": "summary"}\n'
        self.assertEqual(drivers.parse_session_jsonl(text), COOPERBENCH_PARSERS.parse_session_jsonl(text))
        for content in (None, "plain", {"a": 1}, [{"type": "thinking", "thinking": "hm"}, {"type": "image"}, 3]):
            self.assertEqual(drivers.content_blocks_to_text(content),
                             COOPERBENCH_PARSERS._content_blocks_to_text(content))


class SessionTranscripts(unittest.TestCase):
    """How the session driver finds, copies and counts a subagent's
    transcript, on files written here."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-transcripts-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "projects"

    def test_the_transcript_is_the_subagent_s_whose_first_reply_or_prompt_carries_the_token(self):
        mine = write_transcript(self.root / "p" / "s" / "subagents" / "agent-1.jsonl", "tok-1")
        write_transcript(self.root / "p" / "s" / "subagents" / "agent-2.jsonl", "tok-2")
        write_transcript(self.root / "p" / "s.jsonl", "tok-1")  # the orchestrator's own: not a subagent's
        old = write_transcript(self.root / "p" / "old" / "subagents" / "agent-3.jsonl", "tok-1", age=3600)
        self.assertEqual(drivers.find_transcripts("tok-1", [self.root], time.time() - 600), [mine])
        self.assertEqual(drivers.find_transcripts("tok-1", [self.root], 0), [mine, old])
        pasted = self.root / "p" / "s" / "subagents" / "agent-4.jsonl"
        write_lines(pasted, [user_line("[Workflow harness - user request] ...tok-5..."),
                             user_line("[Workflow harness - computed task] ...tok-6..."),
                             {"type": "assistant", "message": {"role": "assistant", "content": [
                                 {"type": "text", "text": "Reading it."}]}},
                             user_line("tok-7, read back from a file after the first reply")])
        self.assertEqual(drivers.carries_token(pasted, "tok-5"), "prompt")
        self.assertEqual(drivers.carries_token(pasted, "tok-6"), "prompt", "every user line before a reply is the prompt")
        self.assertIsNone(drivers.carries_token(pasted, "tok-7"))

    def test_an_orchestrator_that_pasted_both_prompts_is_neither_agent_s(self):
        """An orchestrating subagent carries both agents' tokens in its
        tool calls' inputs, and is the newest file, since it runs finish;
        a token in a tool call's input never counts, and a transcript that
        names another agent of the run is nobody's."""
        one, two = drivers.run_token("run-1", "agent1"), drivers.run_token("run-1", "agent10")
        subagents = self.root / "p" / "s" / "subagents"
        mine = write_transcript(subagents / "agent-a1.jsonl", one, age=100)
        write_transcript(subagents / "agent-a10.jsonl", two, age=90)
        launch = [{"type": "tool_use", "id": f"t{n}", "name": "Agent", "input": {"prompt": f"Begin with {token}."}}
                  for n, token in enumerate((one, two))]
        orchestrator = write_lines(subagents / "agent-orchestrator.jsonl", [
            user_line("Run the pair."), {"type": "assistant", "message": {"role": "assistant", "content": launch}}])
        self.assertIsNone(drivers.carries_token(orchestrator, one))
        self.assertEqual(drivers.find_transcripts(one, [self.root], 0), [mine])
        self.assertEqual(drivers.find_transcripts(two, [self.root], 0), [subagents / "agent-a10.jsonl"])
        alone = write_lines(subagents / "agent-launcher.jsonl", [
            user_line("Run agent1."), {"type": "assistant", "message": {"role": "assistant", "content": launch[:1]}}])
        self.assertIsNone(drivers.carries_token(alone, one), "a token in a tool call's input is not a reply")

    def test_a_transcript_whose_reply_carries_the_token_comes_before_a_newer_one_whose_prompt_does(self):
        token = drivers.run_token("run-2", "agent1")
        subagents = self.root / "p" / "s" / "subagents"
        replied = write_transcript(subagents / "agent-replied.jsonl", token, age=100)
        prompted = write_lines(subagents / "agent-prompted.jsonl", [user_line(f"Begin with {token}.")])
        self.assertEqual(drivers.find_transcripts(token, [self.root], 0), [replied, prompted])

    def test_usage_counts_each_api_message_once_with_its_largest_figures(self):
        usage = drivers.transcript_usage("".join(json.dumps(line) + "\n" for line in transcript_lines("t")))
        self.assertEqual(usage["tokens"], TRANSCRIPT_TOKENS)
        self.assertEqual((usage["messages"], usage["models"], usage["claude_code_version"], usage["output_final"]),
                         (2, ["claude-sonnet-4-5"], "2.1.295", True))
        self.assertAlmostEqual(drivers.estimated_cost(usage["models"], usage["tokens"]), TRANSCRIPT_COST)
        synthetic = json.dumps({"type": "assistant", "message": {"id": "s", "model": "<synthetic>", "usage": {}}})
        self.assertEqual(drivers.transcript_usage(synthetic + "\n")["models"], [])

    def test_a_model_without_a_price_or_a_mix_of_models_gets_no_estimate(self):
        tokens = dict.fromkeys(drivers.TOKEN_FIELDS, 1000)
        self.assertIsNone(drivers.estimated_cost(["claude-sonnet-5-5"], tokens))
        self.assertIsNone(drivers.estimated_cost(["claude-sonnet-4-5", "claude-haiku-4-5"], tokens))
        self.assertIsNone(drivers.estimated_cost([], tokens))
        self.assertAlmostEqual(drivers.estimated_cost(["anthropic/claude-haiku-4-5"], tokens),
                               (1.0 + 5.0 + 0.1 + 1.25) / 1000)

    def test_the_driver_copies_the_transcript_and_fills_the_outcome_from_it(self):
        workspace = self.tmp / "run" / "workspaces" / "agent1"
        workspace.mkdir(parents=True)
        job = drivers.AgentJob(run_id="r1", agent="agent1", role="peer", features=(1,), container="c", prompt="P",
                               env={}, arm_dir=str(COORDBENCH / "arms" / "coop"), timeout_s=5.0, model="claude-x",
                               log_path=str(self.tmp / "run" / "agent1_stream.jsonl"), workspace=str(workspace))
        driver = drivers.SessionDriver(poll_seconds=0.01, settle_seconds=0, transcript_roots=[self.root])
        handle = driver.start(job)
        source = write_transcript(self.root / "p" / "s" / "subagents" / "agent-a.jsonl", handle["run_token"])
        (workspace / ".coordbench-done").touch()
        outcome = driver.wait(handle)
        self.assertEqual(set(outcome.measured), {"cost", *drivers.TOKEN_FIELDS})
        self.assertEqual((outcome.cost_source, outcome.model, outcome.input_tokens), ("estimated from tokens",
                                                                                      "claude-sonnet-4-5", 7))
        self.assertEqual((self.tmp / "run" / "agent1_session.jsonl").read_text(), source.read_text())
        logged = json.loads((self.tmp / "run" / "agent1_session_outcome.json").read_text())
        self.assertEqual((logged["model_requested"], logged["model"], logged["tokens"]),
                         ("claude-x", "claude-sonnet-4-5", TRANSCRIPT_TOKENS))
        self.assertAlmostEqual(logged["cost"], TRANSCRIPT_COST)

    def test_output_without_its_final_count_is_a_lower_bound_and_the_cost_unknown(self):
        """Most messages in Claude Code's transcripts carry no stop_reason,
        and their output count is the stream-start snapshot: summing it
        would undercount, so output_tokens and the cost stay unknown and
        their lower bounds are kept apart."""
        text = "".join(json.dumps(line) + "\n" for line in transcript_lines("t", final=False))
        usage = drivers.transcript_usage(text)
        self.assertFalse(usage["output_final"])
        outcome = drivers.AgentOutcome(status="Submitted", measured=())
        drivers.fill_from_transcript(outcome, usage)
        self.assertEqual(set(outcome.measured), {"input_tokens", "cache_read_tokens", "cache_write_tokens"})
        self.assertEqual((outcome.cost_source, outcome.output_tokens_lower_bound), (None, 148))
        self.assertAlmostEqual(outcome.cost_lower_bound, TRANSCRIPT_COST)

    def test_settling_waits_for_a_transcript_still_being_written_and_no_longer(self):
        path = self.tmp / "t.jsonl"
        path.write_text("{}\n")
        started = time.time()
        drivers.settle(path, 0.3, poll=0.05)
        self.assertGreaterEqual(time.time() - started, 0.25)
        os.utime(path, (time.time() - 10, time.time() - 10))
        started = time.time()
        drivers.settle(path, 0.3)
        self.assertLess(time.time() - started, 0.2)


class Tracing(RunnerCase):
    """The phases are also events on trace's stream, for a deployment that
    listens (run.listen); --debug adds the rest and a trace.jsonl per run,
    and prod records nothing beyond the phases."""

    DEBUG_KINDS = {"docker", "hub_call", "wrote", "marker_poll"}

    def test_the_phases_are_seen_live_by_a_listener_and_the_timings_keep_their_shape(self):
        memory = trace.Memory()
        self.assertIs(runner.listen(memory), memory)
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assert_timings_well_formed(result["timings"])
        for record in result["timings"]:
            self.assertEqual(set(record), TIMING_KEYS)
        # One "phase" as each phase begins and one "phase_done" as it ends,
        # in the order of the records, the agents' phase among them though
        # its start and its end are recorded apart.
        begun, ended = runner_events(memory, "phase"), runner_events(memory, "phase_done")
        self.assertEqual([(e["phase"], e["agent"]) for e in begun], phases(result["timings"]))
        self.assertEqual([(e["phase"], e["agent"]) for e in ended], phases(result["timings"]))
        for event in begun:
            self.assertEqual(set(event), {"kind", "t", "ts", "phase", "agent"})
        for event, record in zip(ended, result["timings"]):
            self.assertEqual(set(event), {"kind", "t", "ts", "duration_ms", "ok", "phase", "agent"})
            self.assertEqual(event["duration_ms"], round(record["seconds"] * 1000, 3))
            self.assertIs(event["ok"], True)
        begins, ends = [e["t"] for e in begun], [e["t"] for e in ended]
        self.assertEqual(begins, sorted(begins))
        self.assertTrue(all(begin <= end for begin, end in zip(begins, ends)))
        # Prod is the phases alone: no file is written for the trace and
        # nothing debug is recorded, however many listeners there are.
        self.assertFalse((self.pair() / "trace.jsonl").exists())
        self.assertEqual({e["kind"] for e in runner_events(memory)} & self.DEBUG_KINDS, set())
        self.assertEqual(runner.DEBUG_SINKS, {})
        # The summary line is what it was.
        self.assertRegex(err, r"timings: hub [0-9.]+ s, run_create [0-9.]+ s, provision [0-9.]+ s, agents ")
        seen = len(memory)
        runner.unlisten(memory)
        trace.event("phase", phase="score", agent=None)
        self.assertEqual(len(memory), seen)

    def test_a_phase_that_failed_ends_in_a_phase_done_that_is_not_ok(self):
        memory = self.memory()
        self.docker.fail_seed = True
        code, _, _ = self.main(*self.run_arguments("coop-git", "--features", "1,6"))
        self.assertEqual(code, 1)
        ended = runner_events(memory, "phase_done")
        self.assertEqual([(e["phase"], e["ok"]) for e in ended], [("hub", True), ("git_remote", False),
                                                                 ("teardown", True)])
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertEqual(phases(result["timings"]), [(e["phase"], e["agent"]) for e in ended])

    def test_debug_traces_hub_calls_and_files_to_trace_jsonl_and_never_a_secret(self):
        memory = self.memory()
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6", "--debug"))
        self.assertEqual(code, 0, err)
        self.assertEqual(trace.level, "debug")
        pair = self.pair()
        events = trace_lines(pair)
        self.assertEqual(runner.DEBUG_SINKS, {}, "the sink is closed once the pair is over")
        kinds = {e["kind"] for e in runner_events(events)}
        self.assertTrue({"phase", "phase_done", "hub_call", "wrote"} <= kinds, kinds)
        self.assertEqual([e["kind"] for e in runner_events(events, "phase_done")],
                         ["phase_done"] * 9, "the hub's phase came before the run directory existed")
        result = json.loads((pair / "result.json").read_text())
        calls = runner_events(events, "hub_call")
        for call in calls:
            self.assertEqual(set(call), {"kind", "t", "ts", "method", "path", "status", "duration_ms"})
            self.assertGreaterEqual(call["duration_ms"], 0)
        self.assertEqual([(c["method"], c["path"], c["status"]) for c in calls],
                         [("POST", "/api/runs", 201), ("POST", f"/api/runs/{result['run_id']}/close", 200),
                          ("GET", f"/api/runs/{result['run_id']}/events", 200),
                          ("DELETE", f"/api/runs/{result['run_id']}", 200)])
        # Every file the run left was announced with its name and its
        # size, the last announcement of each being the file as it is.
        written = {}
        for event in runner_events(events, "wrote"):
            self.assertEqual(set(event), {"kind", "t", "ts", "file", "bytes"})
            written[event["file"]] = event["bytes"]
        self.assertTrue({"plan.json", "agent1_prompt.md", "agent2_prompt.md", "agent1.patch", "agent6.patch",
                         "result.json", "metrics.json", "events.jsonl", "eval.json"} <= set(written), written)
        for name, size in written.items():
            self.assertEqual(size, (pair / name).stat().st_size, name)
        # The listener a deployment attached saw the same events the file holds.
        ours = runner_events(events)
        # summary.json is written once the pair's sink has closed.
        seen = [e for e in runner_events(memory) if e.get("file") != "summary.json"]
        self.assertEqual([e["kind"] for e in seen][-len(ours):], [e["kind"] for e in ours])
        self.assert_secret_nowhere(err)
        self.assert_tokens_kept_private(json.loads((pair / "plan.json").read_text()), err)

    def test_prepare_and_finish_in_debug_append_to_one_trace_file_with_the_marker_polls(self):
        code, out, err = self.main("prepare", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                   "--features", "1,6", "--hub", self.hub_url, "--name", "t",
                                   "--cooperbench-dir", str(self.cooperbench), "--debug")
        self.assertEqual(code, 0, err)
        pair = self.pair()
        prepared = trace_lines(pair)
        self.assertEqual([(e["phase"], e["agent"]) for e in runner_events(prepared, "phase_done")],
                         [("run_create", None), ("provision", "agent1"), ("provision", "agent2")])
        self.assertEqual([(e["phase"], e["agent"]) for e in runner_events(prepared, "phase")][-2:],
                         [("agent", "agent1"), ("agent", "agent2")])
        written = [e["file"] for e in runner_events(prepared, "wrote")]
        self.assertIn("agent1.session-prompt.md", written)
        self.assertIn("token", written)
        self.assertEqual(runner.DEBUG_SINKS, {})
        saved = json.loads((pair / "plan.json").read_text())
        for agent in saved["agents"]:
            (Path(agent["workspace"]) / "patch.txt").write_text(PATCHES[agent["id"]])
            (Path(agent["workspace"]) / ".coordbench-done").touch()
        self.reset_trace()

        code, out, err = self.main("finish", "--run-dir", str(pair), "--debug")
        self.assertEqual(code, 0, err)
        finished = trace_lines(pair)
        self.assertEqual(finished[:len(prepared)], prepared, "finish appends to the file prepare left")
        later = runner_events(finished[len(prepared):])
        self.assertEqual([(e["phase"], e["agent"]) for e in later if e["kind"] == "phase_done"],
                         [("agent", "agent1"), ("agent", "agent2"), ("close", None), ("teardown", None),
                          ("score", None), ("cascade", None)])
        polls = [e for e in later if e["kind"] == "marker_poll"]
        self.assertEqual([(e["agent"], e["found"]) for e in polls], [("agent1", True), ("agent2", True)])
        for poll in polls:
            self.assertEqual(set(poll), {"kind", "t", "ts", "agent", "found"})
        self.assertIn("result.json", [e["file"] for e in later if e["kind"] == "wrote"])
        self.assert_secret_nowhere(out, err)
        self.assert_tokens_kept_private(saved, out, err)

    def test_debug_reaches_the_agents_containers_and_the_session_drivers_wrappers(self):
        """--debug traces the agents' side too: each agent's container starts
        with COORD_TRACE=debug, which every command run in it inherits, and
        the session driver's wrappers on this host export it, so a command
        an agent runs writes its timing to the agent's stderr even from a
        shell with no COORD_TRACE of its own. In prod neither is so, and
        what the agents' commands print is what it was."""
        quiet = {name: value for name, value in os.environ.items()
                 if name not in ("COORD_TRACE", "COORD_TRACE_FILE", "COORD_AGENT_TOKEN")}
        for name, debug in (("prod", False), ("debug", True)):
            with self.subTest(level=name):
                code, out, err = self.main("prepare", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                           "--features", "1,6", "--hub", self.hub_url, "--name", name,
                                           "--cooperbench-dir", str(self.cooperbench),
                                           *(["--debug"] if debug else []))
                self.assertEqual(code, 0, err)
                run_dir = Path(json.loads(out)["run_dir"])
                for agent in json.loads((run_dir / "plan.json").read_text())["agents"]:
                    started, = self.docker.matching("run", "-d", "--name", agent["container"])
                    self.assertEqual(env_value(started, "COORD_TRACE"), "debug" if debug else None)
                    wrapper = (run_dir / "bin" / agent["id"] / "coop-peek").read_text()
                    self.assertEqual("export COORD_TRACE=debug\n" in wrapper, debug)
                peek = subprocess.run([str(run_dir / "bin" / "agent1" / "coop-peek")], capture_output=True,
                                      text=True, timeout=60, env=quiet)
                self.assertEqual((peek.returncode, peek.stdout.strip()), (0, "0"), peek.stderr)
                if not debug:
                    self.assertEqual(peek.stderr, "")
                    continue
                events = [json.loads(line) for line in peek.stderr.splitlines()]
                command, = [event for event in events if event["kind"] == "command"]
                self.assertEqual((command["command"], command["tool"], command["exit_code"]),
                                 ("coop-peek", "peek_messages", 0))
                self.assertGreaterEqual(command["duration_ms"], 0)
                self.assertIn("rpc_call_done", {event["kind"] for event in events})

    def test_hub_calls_are_traced_in_debug_alone_with_the_status_the_hub_gave(self):
        memory = self.memory()
        client = runner.HubClient(self.hub_url, ADMIN_SECRET)
        self.assertTrue(client.healthy())
        self.assertEqual(runner_events(memory, "hub_call"), [])
        trace.set_level("debug")
        self.assertTrue(client.healthy())
        with self.assertRaises(runner.HubError):
            client.request("GET", "/api/runs/nonesuch")
        self.assertFalse(runner.HubClient("http://127.0.0.1:1").healthy())
        calls = runner_events(memory, "hub_call")
        self.assertEqual([(c["method"], c["path"], c["status"]) for c in calls],
                         [("GET", "/api/health", 200), ("GET", "/api/runs/nonesuch", 404),
                          ("GET", "/api/health", None)])


class DockerTracing(TracedCase):
    """drivers.docker in debug records what each command was, how it ended
    and what it cost, without its command line; in prod it records nothing."""

    def run_docker(self, *args, answer=None, **kwargs):
        answer = answer or (lambda command, **_: subprocess.CompletedProcess(command, 0, "abc", ""))
        with mock.patch.object(drivers.subprocess, "run", side_effect=answer):
            return REAL_DOCKER(*args, **kwargs)

    def test_nothing_is_recorded_in_prod(self):
        memory = self.memory()
        self.run_docker("ps")
        self.run_docker("run", "-d", "--name", "c1", "img", extra_env={"COORD_AGENT_TOKEN": AGENT_SECRET})
        self.assertEqual(memory, [])

    def test_a_debug_record_names_the_subcommand_and_its_argument_never_the_line(self):
        memory = self.memory()
        trace.set_level("debug")
        done = self.run_docker("run", "-d", "--name", "c1", "-e", "COORD_AGENT_TOKEN", "--network", "net",
                               "--add-host", "host.docker.internal:host-gateway", "img", "-c", "sleep",
                               extra_env={"COORD_AGENT_TOKEN": AGENT_SECRET})
        self.assertEqual(done.returncode, 0)
        self.assertEqual(len(memory), 1)
        event = memory[0]
        self.assertEqual(set(event), {"kind", "t", "ts", "subcommand", "argument", "exit_code", "duration_ms",
                                      "output_bytes"})
        self.assertEqual((event["kind"], event["subcommand"], event["argument"], event["exit_code"]),
                         ("docker", "run", "img", 0))
        self.assertIsInstance(event["duration_ms"], float)
        self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertEqual(event["output_bytes"], 3)
        dumped = json.dumps(event)
        for unwanted in (AGENT_SECRET, "COORD_AGENT_TOKEN", "c1", "net", "host-gateway"):
            self.assertNotIn(unwanted, dumped)

    def test_the_argument_skips_an_option_s_value_and_the_exit_code_is_the_command_s(self):
        memory = self.memory()
        trace.set_level("debug")

        def failing(command, **_):
            return subprocess.CompletedProcess(command, 125, "", "no such container")

        self.run_docker("exec", "-w", "/workspace/repo", "c-agent1", "bash", "-lc", "git diff", answer=failing)
        self.run_docker("volume", "inspect", "-f", "{{.Mountpoint}}", "vol")
        self.run_docker("cp", "/here/.", "c-agent1:/opt/coordbench")
        self.assertEqual([(e["subcommand"], e["argument"], e["exit_code"], e["output_bytes"]) for e in memory],
                         [("exec", "c-agent1", 125, len("no such container")), ("volume", "inspect", 0, 3),
                          ("cp", "/here/.", 0, 3)])

    def test_a_command_that_timed_out_is_recorded_without_an_exit_code_and_still_raises(self):
        memory = self.memory()
        trace.set_level("debug")

        def too_long(command, **_):
            raise subprocess.TimeoutExpired(command, 1)

        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_docker("exec", "c-agent1", "claude", answer=too_long, timeout=1)
        self.assertEqual((memory[0]["subcommand"], memory[0]["argument"], memory[0]["exit_code"],
                          memory[0]["output_bytes"]), ("exec", "c-agent1", None, 0))

    def test_a_teed_command_s_output_bytes_are_the_log_s(self):
        memory = self.memory()
        trace.set_level("debug")
        log = Path(tempfile.mkdtemp(prefix="coordbench-docker-test-")) / "agent1_stream.jsonl"
        self.addCleanup(shutil.rmtree, log.parent, True)

        def tee(command, **kwargs):
            kwargs["stdout"].write("x" * 5)
            return subprocess.CompletedProcess(command, 0)

        self.run_docker("exec", "c-agent1", "claude", answer=tee, output=str(log))
        self.assertEqual((memory[0]["exit_code"], memory[0]["output_bytes"]), (0, 5))
        self.assertEqual(log.read_text(), "xxxxx")

    def test_write_text_announces_a_file_s_name_and_bytes_in_debug_alone(self):
        memory = self.memory()
        directory = Path(tempfile.mkdtemp(prefix="coordbench-write-test-"))
        self.addCleanup(shutil.rmtree, directory, True)
        drivers.write_text(directory / "quiet.txt", "nothing said\n")
        self.assertEqual(memory, [])
        trace.set_level("debug")
        drivers.write_text(directory / "plan.json", "{}\n")
        drivers.write_text(directory / "plan.json", "more\n", append=True)
        self.assertEqual((directory / "plan.json").read_text(), "{}\nmore\n")
        self.assertEqual([(e["kind"], e["file"], e["bytes"]) for e in memory],
                         [("wrote", "plan.json", 3), ("wrote", "plan.json", 5)])


class TheCommandLine(unittest.TestCase):
    def test_debug_is_a_flag_of_run_prepare_and_finish_and_off_by_default(self):
        pair = ["--arm", "coop", "--repo", REPO, "--task", str(TASK), "--features", "1,6"]
        for command in (["run", *pair], ["prepare", *pair], ["finish", "--run-dir", "d"]):
            self.assertFalse(runner.parser().parse_args(command).debug, command)
            self.assertTrue(runner.parser().parse_args([*command, "--debug"]).debug, command)

    def settings(self, *extra):
        args = runner.parser().parse_args(["run", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                           "--features", "1,6", "--name", "t", *extra])
        return runner.settings_from(args, "claude-cli")

    def test_relative_directories_are_made_absolute_once_for_docker_and_the_scorer(self):
        """docker reads a relative path in -v as a volume name, and the
        scorer runs with CooperBench's checkout as its working directory
        while being handed paths under it, so both are resolved here."""
        settings = self.settings("--cooperbench-dir", "rel/cooperbench", "--log-dir", "rel/logs")
        self.assertEqual(settings["cooperbench_dir"], Path.cwd() / "rel" / "cooperbench")
        self.assertEqual(settings["log_dir"], Path.cwd() / "rel" / "logs")
        settings = self.settings("--cooperbench-dir", "rel/cooperbench")
        self.assertEqual(settings["log_dir"], Path.cwd() / "rel" / "cooperbench" / "logs")
        with mock.patch.dict(os.environ, {"COOPERBENCH_DIR": "rel/from-env"}):
            self.assertEqual(runner.cooperbench_home(), Path.cwd() / "rel" / "from-env")
            self.assertEqual(self.settings()["cooperbench_dir"], Path.cwd() / "rel" / "from-env")
        with mock.patch.dict(os.environ, {"COOPERBENCH_DIR": ""}):
            self.assertEqual(runner.cooperbench_home(), Path.home().resolve() / "cooperbench")


class Scoring(unittest.TestCase):
    def test_cooperbench_s_own_test_functions_are_called_from_its_src(self):
        with tempfile.TemporaryDirectory() as scratch:
            sandbox = Path(scratch) / "src" / "cooperbench" / "eval"
            sandbox.mkdir(parents=True)
            (sandbox.parent / "__init__.py").write_text("")
            (sandbox / "__init__.py").write_text("")
            (sandbox / "sandbox.py").write_text(
                "import os\n"
                "def test_merged(**kw):\n"
                "    return {'called': 'merged', 'kw': kw, 'token': os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')}\n"
                "def test_solo(**kw):\n"
                "    return {'called': 'solo', 'kw': kw}\n")
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}):
                merged = REAL_COOPERBENCH_TEST("merged", {"repo_name": REPO, "patch1": "p1"}, scratch)
            solo = REAL_COOPERBENCH_TEST("solo", {"patch": "p"}, scratch)
        self.assertEqual(merged, {"called": "merged", "kw": {"repo_name": REPO, "patch1": "p1"}, "token": None})
        self.assertEqual(solo, {"called": "solo", "kw": {"patch": "p"}})

    def test_a_scorer_that_fails_says_why(self):
        with tempfile.TemporaryDirectory() as scratch:
            with self.assertRaisesRegex(RuntimeError, "CooperBench's evaluation failed"):
                REAL_COOPERBENCH_TEST("solo", {}, scratch)


def copy_coop_arm(destination):
    shutil.copytree(COORDBENCH / "arms" / "coop", destination, ignore=shutil.ignore_patterns("__pycache__"))
    return destination


class TheHubProcess(unittest.TestCase):
    """hub.py as the runner starts it, a process of its own, with the
    runner's own environment holding both secrets it must not pass on."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-hub-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        secrets = {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "COORD_ADMIN_TOKEN": ADMIN_SECRET}
        environment = mock.patch.dict(os.environ, secrets)
        environment.start()
        self.addCleanup(environment.stop)

    def start(self, **keywords):
        process, client = runner.start_hub(self.tmp / "hub", host="127.0.0.1", **keywords)
        self.addCleanup(runner.stop_hub, process)
        return process, client

    def test_start_hub_serves_on_a_free_port_and_tells_agents_the_docker_host(self):
        process, client = self.start()
        self.assertTrue(client.healthy())
        port = client.base_url.rsplit(":", 1)[1]
        self.assertEqual(client.base_url, f"http://127.0.0.1:{port}")
        first = next((self.tmp / "hub").glob("hub-*.out")).read_text().splitlines()[0]
        self.assertEqual(first, f"http://host.docker.internal:{port}")
        runner.stop_hub(process)
        self.assertIsNotNone(process.poll())

    def test_it_wants_a_fresh_token_and_loads_only_the_arm_directories_it_was_given(self):
        mine = copy_coop_arm(self.tmp / "arms" / "mine")
        elsewhere = copy_coop_arm(self.tmp / "elsewhere")
        process, client = self.start(arm_dirs=[self.tmp / "arms"])
        self.assertRegex(client.admin_token, "^[0-9a-f]{64}$")
        self.assertNotEqual(client.admin_token, ADMIN_SECRET)
        self.assertTrue(client.authorized())
        self.assertFalse(runner.HubClient(client.base_url).authorized())
        self.assertFalse(runner.HubClient(client.base_url, ADMIN_SECRET).authorized())
        specs = {1: "Do 1.", 6: "Do 6."}
        answer = client.create_run(str(mine), "r-mine", [1, 6], specs, {})
        self.assertEqual([agent["id"] for agent in answer["agents"]], ["agent1", "agent2"])
        with self.assertRaises(runner.HubError) as refused:
            client.create_run(str(elsewhere), "r-elsewhere", [1, 6], specs, {})
        self.assertEqual(refused.exception.status, 403)
        with self.assertRaises(runner.HubError) as refused:
            runner.HubClient(client.base_url).close_run("r-mine")
        self.assertEqual(refused.exception.status, 401)
        metrics, artifacts = client.close_run("r-mine")
        self.assertIn("events", metrics)
        self.assertEqual(artifacts, {})
        self.assertNotIn(client.admin_token, " ".join(process.args))
        self.assertIn(str((self.tmp / "arms").resolve()), process.args)

    @unittest.skipUnless(HAS_PROC, "reading a process's environment back needs Linux's /proc")
    def test_the_token_lives_in_the_hub_s_environment_alone_and_finish_reads_it_back_from_there(self):
        process, client = self.start()
        token = client.admin_token
        command = Path(f"/proc/{process.pid}/cmdline").read_bytes()
        environment = Path(f"/proc/{process.pid}/environ").read_bytes()
        for secret in (token, SECRET, ADMIN_SECRET):
            self.assertNotIn(secret.encode(), command)
        self.assertIn(f"COORD_ADMIN_TOKEN={token}".encode(), environment.split(b"\0"))
        self.assertNotIn(SECRET.encode(), environment)
        self.assertNotIn(ADMIN_SECRET.encode(), environment)
        self.assertEqual(runner.hub_admin_token(process.pid, client.base_url), token)
        self.assertIsNone(runner.hub_admin_token(process.pid, "http://127.0.0.1:1"))
        self.assertIsNone(runner.hub_admin_token(os.getpid(), client.base_url))
        finishing = runner.finish_client({"hub_pid": process.pid, "hub_url": client.base_url})
        self.assertEqual(finishing.admin_token, token)
        self.assertTrue(finishing.authorized())
        named = runner.finish_client({"hub_pid": None, "hub_url": client.base_url})
        self.assertEqual(named.admin_token, ADMIN_SECRET)
        runner.stop_hub_pid(os.getpid(), client.base_url)
        runner.stop_hub_pid(process.pid, "http://127.0.0.1:1")
        self.assertIsNone(process.poll())
        runner.stop_hub_pid(process.pid, client.base_url)
        process.wait(10)
        for path in (self.tmp / "hub").rglob("*"):
            self.assertNotIn(token.encode(), path.read_bytes(), str(path))


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TheGitSetUp(unittest.TestCase):
    """CooperBench's setup command run by real git, in a repository cloned
    from a task's, against a bare remote seeded as the runner seeds the
    git server's, and read back as the runner reads it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-git-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # No user's or system's git configuration takes part.
        self.environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        self.remote = str(self.tmp / "remote.git")
        self.git("init", "-q", "--bare", self.remote)
        self.git("-C", self.remote, "config", "receive.denyCurrentBranch", "ignore")
        self.task = self.tmp / "task"
        self.git("init", "-q", str(self.task))
        (self.task / "README.md").write_text("the task's repository\n")
        self.git("-C", str(self.task), "add", "README.md")
        self.git("-C", str(self.task), "-c", "user.name=task", "-c", "user.email=task@example.invalid",
                 "commit", "-q", "-m", "the task's base")
        self.base = self.git("-C", str(self.task), "rev-parse", "HEAD").strip()
        self.git("-C", str(self.task), "push", "-q", self.remote, "HEAD:refs/heads/main")
        for agent in ("agent1", "agent2"):
            self.git("-C", self.remote, "branch", agent, "main")

    def git(self, *arguments):
        done = subprocess.run(["git", *arguments], capture_output=True, text=True, env=self.environment,
                              timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def shell(self, script):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=self.environment,
                              timeout=60, stdin=subprocess.DEVNULL)

    def agent_repository(self, agent):
        repository = self.tmp / agent
        self.git("clone", "-q", str(self.task), str(repository))
        return str(repository)

    def unkept(self, agent, repository):
        promises = runner.git_promises(agent, {"git_remote": self.remote, "base_ref": self.base})
        return runner.unkept_promises(promises, self.shell(runner.git_read_script(promises, repository)).stdout)

    def test_the_runs_repository_keeps_a_reflog_so_the_git_log_shows_a_force_push(self):
        """The git server's per-run init, run by real git, keeps a reflog,
        and the hub's git_log of that directory (kit.git_log, read in place
        through git_remote_dir) still names the commit a force-push threw
        away, which a fetch over git:// could not carry."""
        import kit
        remote = str(self.tmp / "server" / "run" / "repo.git")
        self.assertEqual(self.shell(runner.init_repository_script(remote)).returncode, 0)
        self.git("-C", str(self.task), "push", "-q", remote, "HEAD:refs/heads/agent1")
        (self.task / "README.md").write_text("work an agent pushed\n")
        self.git("-C", str(self.task), "-c", "user.name=agent1", "-c", "user.email=a@example.invalid",
                 "commit", "-q", "-am", "the pushed work")
        lost = self.git("-C", str(self.task), "rev-parse", "HEAD").strip()
        self.git("-C", str(self.task), "push", "-q", remote, "HEAD:refs/heads/agent1")
        self.git("-C", str(self.task), "push", "-q", "--force", remote, f"{self.base}:refs/heads/agent1")
        self.assertIn(f"commit {lost}", kit.git_log(remote, self.base))

    def test_the_repository_is_named_to_the_hub_only_where_this_host_owns_it(self):
        volume = self.tmp / "volume"
        (volume / "run-1" / "repo.git").mkdir(parents=True)
        inspected = subprocess.CompletedProcess([], 0, stdout=f"{volume}\n", stderr="")
        with mock.patch.object(drivers, "docker", return_value=inspected):
            self.assertEqual(runner.repository_on_host("run-1"), str(volume / "run-1" / "repo.git"))
            self.assertIsNone(runner.repository_on_host("run-2"))
        with mock.patch.object(drivers, "docker", return_value=subprocess.CompletedProcess([], 1, "", "no volume")):
            self.assertIsNone(runner.repository_on_host("run-1"))

    def test_the_setup_command_keeps_every_promise_of_the_git_block(self):
        for agent in ("agent1", "agent2"):
            repository = self.agent_repository(agent)
            setup = self.shell(runner.git_setup_command(agent, self.remote, repository))
            self.assertEqual(setup.returncode, 0, setup.stderr)
            self.assertEqual(self.unkept(agent, repository), [])
        self.assertEqual(self.git("-C", self.remote, "rev-parse", "main").strip(), self.base)

    def test_a_set_up_that_failed_is_found_out_though_its_exit_code_says_nothing(self):
        repository = self.agent_repository("agent1")
        setup = self.shell(runner.git_setup_command("agent1", str(self.tmp / "no-such-remote.git"), repository))
        self.assertEqual(setup.returncode, 0)
        unkept = self.unkept("agent1", repository)
        self.assertTrue(unkept[0].startswith("the remote team (found "), unkept)
        for promise in ("agent1 pushed with -u (found nothing)", "team/agent1 at the base commit (found nothing)",
                        "team/main at the base commit (found nothing)"):
            self.assertIn(promise, unkept)
        self.assertEqual(len(unkept), 4, unkept)
        untouched = self.unkept("agent2", self.agent_repository("agent2"))
        self.assertEqual(len(untouched), 7, untouched)
        self.assertFalse([promise for promise in untouched if promise.startswith("agent2 at the base commit")])

    @unittest.skipIf(COOPERBENCH_RUNTIME is None or COOPERBENCH_PROMPT is None,
                     "CooperBench's source is not here; set COOPERBENCH_DIR to its checkout")
    def test_the_command_and_the_promises_are_cooperbench_s(self):
        for agent, url in (("agent1", "git://coordbench-git:9418/r1/repo.git"), ("lead", "git://h/a b.git")):
            self.assertEqual(runner.git_setup_command(agent, url),
                             COOPERBENCH_RUNTIME.build_git_setup_command(agent_id=agent, server_url=url))
        self.assertEqual(COOPERBENCH_RUNTIME.CONTAINER_REPO_PATH, drivers.CONTAINER_REPO)
        block = COOPERBENCH_PROMPT._git_block("agent1", ["agent2"])
        for promise in ("A shared git remote named `team` is already configured in this repo.",
                        "- Your branch: `agent1` (already created and pushed)",
                        "- Base reference: `team/main` (pristine starting state)"):
            self.assertIn(promise, block)
        self.assertIn(f"git config user.email '{GIT_EMAIL}'",
                      COOPERBENCH_RUNTIME.build_git_setup_command(agent_id="agent1", server_url="u"))


class SecretsInTheDrivers(unittest.TestCase):
    def test_the_docker_client_holds_the_agents_credential_but_never_the_tokens_of_the_runner(self):
        # A COORD_AGENT_TOKEN in the runner's own environment is nobody's
        # agent's, so it is held back like the admin token; an agent's own
        # goes with extra_env, for that one call.
        environment = {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "COORD_ADMIN_TOKEN": ADMIN_SECRET,
                       "COORD_AGENT_TOKEN": "the-runner-s-own"}
        with mock.patch.dict(os.environ, environment), mock.patch.object(drivers.subprocess, "run") as run:
            REAL_DOCKER("ps")
            REAL_DOCKER("ps", input="text")
            REAL_DOCKER("ps", output=os.devnull)
            REAL_DOCKER("run", "-e", "COORD_AGENT_TOKEN", extra_env={"COORD_AGENT_TOKEN": AGENT_SECRET})
            REAL_DOCKER("ps")
        self.assertEqual(len(run.call_args_list), 5)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["env"].get("CLAUDE_CODE_OAUTH_TOKEN"), SECRET)
            self.assertNotIn("COORD_ADMIN_TOKEN", call.kwargs["env"])
        for call in run.call_args_list[:3] + run.call_args_list[4:]:
            self.assertNotIn("COORD_AGENT_TOKEN", call.kwargs["env"])
        self.assertEqual(run.call_args_list[3].kwargs["env"].get("COORD_AGENT_TOKEN"), AGENT_SECRET)

    def test_a_secret_in_a_job_s_environment_never_reaches_the_command_line(self):
        job = drivers.AgentJob(run_id="r1", agent="agent1", role="peer", features=(1,), container="c-agent1",
                               prompt="p", arm_dir=str(COORDBENCH / "arms" / "coop"),
                               env={"COORD_RPC_URL": "http://h:1/rpc/r1/agent1", "CLAUDE_CODE_OAUTH_TOKEN": SECRET,
                                    "COORD_ADMIN_TOKEN": ADMIN_SECRET, "COORD_AGENT_TOKEN": AGENT_SECRET})
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": SECRET}):
            arguments = drivers.claude_exec_arguments(job)
        line = " ".join(arguments)
        for unwanted in (SECRET, ADMIN_SECRET, AGENT_SECRET, "COORD_ADMIN_TOKEN", "COORD_AGENT_TOKEN"):
            self.assertNotIn(unwanted, line)
        self.assertIn(["-e", "CLAUDE_CODE_OAUTH_TOKEN"], pairs(arguments))
        self.assertEqual(env_value(arguments, "COORD_RPC_URL"), "http://h:1/rpc/r1/agent1")

    def test_the_scorer_the_hub_and_git_get_no_secret(self):
        environment = {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "COORD_ADMIN_TOKEN": ADMIN_SECRET,
                       "COORD_AGENT_TOKEN": AGENT_SECRET, "KEEP": "1"}
        with mock.patch.dict(os.environ, environment):
            cleaned = runner.environment_without_secrets()
        self.assertEqual(cleaned.get("KEEP"), "1")
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "COORD_ADMIN_TOKEN", "COORD_AGENT_TOKEN"):
            self.assertNotIn(name, cleaned)


class MachineFailuresAndReferenceMerges(RunnerCase):
    """The docker preflight, the infrastructure kind of an error, and the
    --reference-merge choice of pairs by CooperBench's gold report."""

    def write_gold_report(self, conflicting):
        rows = [{"repo": REPO, "task_id": TASK, "f1": f1, "f2": f2, "has_conflict": conflicting[(f1, f2)]}
                for f1, f2 in conflicting]
        (self.cooperbench / "dataset" / "gold_conflict_report.json").write_text(
            json.dumps({"all_results": rows}), encoding="utf-8")

    def test_a_run_refuses_to_start_while_the_docker_daemon_does_not_answer(self):
        self.docker.info_fails = True
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 2)
        self.assertIn("bash bench/cooperbench/setup.sh", err)
        self.assertFalse((self.logs / "t").exists())

    def test_a_pair_lost_to_docker_is_an_infrastructure_error_in_its_result_and_the_summary(self):
        self.docker.fail_seed = True
        code, _, err = self.main(*self.run_arguments("coop-git", "--features", "1,6"))
        self.assertEqual(code, 1)
        result = json.loads((self.pair() / "result.json").read_text())
        self.assertEqual(result["error_kind"], "infrastructure")
        summary = json.loads((self.logs / "t" / "summary.json").read_text())
        self.assertEqual(summary["infrastructure_errors"], 1)
        self.assertIn("1 failed (1 infrastructure)", err)

    def test_an_error_kind_names_the_machine_the_runner_or_the_agent(self):
        agent_error = {"agent1": types.SimpleNamespace(status="Error")}
        clean = {"agent1": types.SimpleNamespace(status="Submitted")}
        self.assertEqual(runner.error_kind({"error_kind": "infrastructure"}, agent_error), "infrastructure")
        self.assertEqual(runner.error_kind({}, agent_error), "agent")
        self.assertEqual(runner.error_kind({}, clean), None)

    def test_a_reference_merge_keeps_only_the_pairs_the_gold_patches_merge_that_way(self):
        self.write_gold_report({(1, 6): False, (2, 10): True})
        pairs = [(REPO, TASK, [1, 6]), (REPO, TASK, [2, 10])]
        dataset = self.cooperbench / "dataset"
        self.assertEqual(runner.by_reference_merge(pairs, dataset, "clean"), [(REPO, TASK, [1, 6])])
        self.assertEqual(runner.by_reference_merge(pairs, dataset, "conflicting"), [(REPO, TASK, [2, 10])])

    def test_a_reference_merge_names_a_missing_report_a_missing_pair_and_an_empty_choice(self):
        pairs = [(REPO, TASK, [1, 6])]
        dataset = self.cooperbench / "dataset"
        with self.assertRaisesRegex(ValueError, "needs gold_conflict_report.json"):
            runner.by_reference_merge(pairs, dataset, "clean")
        self.write_gold_report({(2, 10): True})
        with self.assertRaisesRegex(ValueError, "no result for"):
            runner.by_reference_merge(pairs, dataset, "clean")
        self.write_gold_report({(1, 6): True})
        with self.assertRaisesRegex(ValueError, "merges clean"):
            runner.by_reference_merge(pairs, dataset, "clean")

    def test_the_command_line_applies_the_reference_merge_before_anything_runs(self):
        self.write_gold_report({(1, 6): False, (2, 10): True})
        code, _, err = self.main(*self.run_arguments("coop", "--features", "1,6", "--reference-merge", "conflicting"))
        self.assertEqual(code, 2)
        self.assertIn("merges conflicting", err)
        self.assertFalse((self.logs / "t").exists())



class TheRerunRule(unittest.TestCase):
    """pair_finished: CooperBench's rule, an agent in Error reruns the
    pair, but for an agent that ran out the pair's time, whose scored
    attempt is kept (PARITY.md, row 23)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-rerun-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def written(self, result):
        (self.tmp / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return self.tmp

    def test_a_pair_whose_agent_ran_out_its_time_is_finished_and_one_that_failed_is_not(self):
        timed_out = {"status": "Error", "error": f"{drivers.TIMED_OUT}7200 seconds",
                     "end_reason": f"{drivers.TIMED_OUT}7200 seconds"}
        self.assertTrue(runner.pair_finished(self.written(
            {"agents": {"agent1": {"status": "Submitted"}, "agent2": timed_out}})))
        self.assertTrue(runner.pair_finished(self.written({"agent": timed_out})))
        self.assertFalse(runner.pair_finished(self.written(
            {"agents": {"agent1": {"status": "Error", "error": None, "end_reason": "Usage limit reached"}}})))
        self.assertFalse(runner.pair_finished(self.written(
            {"agents": {"agent1": {"status": "Error", "error": None, "end_reason": drivers.NO_RESULT}}})))
        self.assertFalse(runner.pair_finished(self.written({"error": "HubError: gone", "agents": {}})))
        self.assertTrue(runner.pair_finished(self.written({"agents": {"agent1": {"status": "LimitsExceeded"}}})))
        self.assertTrue(runner.agent_ran_out_of_time(timed_out))
        self.assertFalse(runner.agent_ran_out_of_time({"status": "Error"}))


# A blob's type in a packfile (git's object.h).
BLOB = 3


def packed_header(kind, size):
    """A packed object's type-and-size header, as git writes it."""
    byte = (kind << 4) | (size & 0x0F)
    size >>= 4
    out = bytearray()
    while size:
        out.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7
    out.append(byte)
    return bytes(out)


def base_distance_bytes(distance):
    """An offset delta's distance back to its base, as git's pack-objects
    writes it: each byte before the last one less than its value."""
    out = [distance & 0x7F]
    distance >>= 7
    while distance:
        distance -= 1
        out.insert(0, 0x80 | (distance & 0x7F))
        distance >>= 7
    return bytes(out)


def packfile(*objects):
    """A git packfile as git writes one: the header, each object's
    type-and-size header, a delta's base, its zlib stream, and the SHA-1
    of it all. An object is a blob's bytes, ("ofs", index, delta) for an
    offset delta on the object at that index, or ("ref", id, delta) for a
    ref delta on the object of that id."""
    out = bytearray(b"PACK" + (2).to_bytes(4, "big") + len(objects).to_bytes(4, "big"))
    starts = []
    for entry in objects:
        starts.append(len(out))
        if isinstance(entry, bytes):
            out += packed_header(BLOB, len(entry)) + zlib.compress(entry)
            continue
        how, base, delta = entry
        if how == "ofs":
            out += packed_header(runner.OFS_DELTA, len(delta)) + base_distance_bytes(starts[-1] - starts[base])
        else:
            out += packed_header(runner.REF_DELTA, len(delta)) + base
        out += zlib.compress(delta)
    return bytes(out) + hashlib.sha1(out).digest()


def size_bytes(size):
    """A delta's size: seven bits a byte, the lowest first."""
    out = bytearray()
    while size > 0x7F:
        out.append(0x80 | (size & 0x7F))
        size >>= 7
    out.append(size)
    return bytes(out)


def delta_on(base, *steps):
    """A git delta on base: each step ("copy", offset, size) or ("insert",
    bytes), encoded as git's diff-delta encodes it."""
    body, target = bytearray(), 0
    for step in steps:
        if step[0] == "insert":
            assert 1 <= len(step[1]) <= 127, "git inserts at most 127 bytes at a time"
            body += bytes([len(step[1])]) + step[1]
            target += len(step[1])
            continue
        _, offset, size = step
        command, arguments = 0x80, bytearray()
        for bit in range(4):
            if (offset >> (8 * bit)) & 0xFF:
                command |= 1 << bit
                arguments.append((offset >> (8 * bit)) & 0xFF)
        for bit in range(3):
            if (size >> (8 * bit)) & 0xFF:
                command |= 0x10 << bit
                arguments.append((size >> (8 * bit)) & 0xFF)
        body += bytes([command]) + arguments
        target += size
    return size_bytes(len(base)) + size_bytes(target) + bytes(body)


def blob_id(content):
    return hashlib.sha1(b"blob %d\0" % len(content) + content).digest()


class PackReading(unittest.TestCase):
    """pack_contents: a packfile's objects as git shows them, deltas
    applied, so a credential the scrub must find is seen whole however a
    delta cut it, and whatever git would refuse is refused."""

    # A line longer than one insert run: git inserts at most 127 bytes at
    # a time, so a delta that adds this line cuts the planted value in two.
    LINE = b"x" * 110 + b"export TOKEN=" + PLANTED.encode() + b"\n"
    BASE = b"".join(b"line %d of the file the agent changed\n" % n for n in range(12))

    def test_a_value_an_offset_delta_inserts_in_two_runs_is_seen_whole(self):
        delta = delta_on(self.BASE, ("copy", 0, len(self.BASE)), ("insert", self.LINE[:127]),
                         ("insert", self.LINE[127:]))
        self.assertNotIn(PLANTED.encode(), delta, "the delta's own bytes cut the value")
        pack = packfile(self.BASE, ("ofs", 0, delta))
        self.assertEqual(runner.pack_contents(pack), [self.BASE, self.BASE + self.LINE])
        self.assertEqual([packed.kind for packed in runner.packed_objects(pack)], [BLOB, runner.OFS_DELTA])

    def test_a_ref_delta_finds_its_base_after_it_and_a_delta_may_build_on_a_delta(self):
        middle = self.BASE + self.LINE
        last = middle + b"one more line\n"
        pack = packfile(("ref", blob_id(self.BASE), delta_on(self.BASE, ("copy", 0, len(self.BASE)),
                                                             ("insert", self.LINE[:127]),
                                                             ("insert", self.LINE[127:]))),
                        self.BASE,
                        ("ofs", 0, delta_on(middle, ("copy", 0, len(middle)), ("insert", b"one more line\n"))))
        self.assertEqual(sorted(runner.pack_contents(pack)), sorted([middle, self.BASE, last]))

    def test_the_objects_of_packs_git_writes_read_back_as_git_holds_them(self):
        # No user's or system's git configuration takes part, and no
        # maintenance of git's own races the cleanup.
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
                       "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false"}
        with tempfile.TemporaryDirectory(prefix="coordbench-pack-test-") as scratch:
            subprocess.run(["git", "init", "-q", scratch], env=environment, check=True, timeout=60)
            lines = [b"line %d: text that repeats, with a number %d\n" % (n, n * 7) for n in range(200)]
            versions = []
            for version in range(12):
                text = list(lines)
                text.insert(50 + version, b"version %d: " % version + b"x" * 150 + PLANTED.encode() + b"\n")
                versions.append(b"".join(text))
            ids = b"".join(subprocess.run(["git", "-C", scratch, "hash-object", "-w", "--stdin"], input=content,
                                          env=environment, capture_output=True, check=True, timeout=60).stdout
                           for content in versions)
            for flags, delta in (([], runner.REF_DELTA), (["--delta-base-offset"], runner.OFS_DELTA)):
                with self.subTest(flags=flags):
                    pack = subprocess.run(["git", "-C", scratch, "pack-objects", "--stdout", *flags], input=ids,
                                          env=environment, capture_output=True, check=True, timeout=60).stdout
                    self.assertIn(delta, [packed.kind for packed in runner.packed_objects(pack)])
                    self.assertEqual(sorted(runner.pack_contents(pack)), sorted(versions))

    def test_the_decompressor_is_handed_a_chunk_at_a_time_never_the_rest_of_the_pack(self):
        noise = random.Random(7)
        pack = packfile(*(noise.randbytes(1024) for _ in range(300)))
        fed = []
        real = zlib.decompressobj

        class Counting:
            def __init__(self):
                self.inner = real()

            def decompress(self, data):
                fed.append(len(data))
                return self.inner.decompress(data)

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(runner.zlib, "decompressobj", Counting):
            self.assertEqual(len(runner.pack_contents(pack)), 300)
        self.assertGreater(len(pack), 4 * runner.INFLATE_CHUNK)
        self.assertLessEqual(max(fed), runner.INFLATE_CHUNK)
        self.assertLess(sum(fed), 2 * len(pack))

    def test_a_copy_with_no_size_bytes_copies_64_kib_as_git_writes_it(self):
        # git's diff-delta writes a copy of 0x10000 bytes, its largest,
        # with no size byte at all: the size 0 stands for 0x10000.
        base = bytes(range(256)) * 300
        delta = size_bytes(len(base)) + size_bytes(0x10000 + 3) + b"\x80" + b"\x03end"
        self.assertEqual(runner.pack_contents(packfile(base, ("ofs", 0, delta))), [base, base[:0x10000] + b"end"])

    def test_what_git_refuses_is_refused(self):
        good = packfile(self.BASE, ("ofs", 0, delta_on(self.BASE, ("copy", 0, 10))))
        self.assertEqual(len(runner.pack_contents(good)), 2)

        def sealed(body):
            return body + hashlib.sha1(body).digest()

        def one_object(header_and_stream):
            return sealed(b"PACK" + (2).to_bytes(4, "big") + (1).to_bytes(4, "big") + header_and_stream)

        size = len(self.BASE)
        # A copy that runs ten bytes past the base, made up by an insert so
        # the result has the size the delta names: only the copy's own
        # bound catches it.
        past = size_bytes(size) + size_bytes(15) + delta_on(self.BASE, ("copy", size - 5, 15),
                                                            ("insert", b"0123456789"))[len(size_bytes(size)) + 1:]
        too_long = b"\xff" * 12 + b"\x01"
        refused = {
            "a checksum that does not match": (good[:-1] + bytes([good[-1] ^ 1]), "checksum"),
            "bytes after the last object": (sealed(good[:-20] + b"\0"), "after the packfile's last object"),
            "a truncated pack": (sealed(good[:-25]), "truncated"),
            "a base not in the pack": (packfile(("ref", bytes(20), delta_on(self.BASE, ("copy", 0, 10)))),
                                       "base is not in the packfile"),
            "a copy past the base": (packfile(self.BASE, ("ofs", 0, past)), "past the end of its base"),
            "a base of another size": (packfile(self.BASE, ("ofs", 0, size_bytes(5) + size_bytes(1) + b"\x01a")),
                                       "base of another size"),
            "the reserved instruction": (packfile(self.BASE, ("ofs", 0, size_bytes(size) + size_bytes(1) + b"\x00")),
                                         "reserved instruction"),
            "an insert past the delta's end": (packfile(self.BASE, ("ofs", 0, size_bytes(size) + size_bytes(5)
                                                                    + b"\x05ab")), "insert runs past its end"),
            "a result of another size": (packfile(self.BASE, ("ofs", 0, size_bytes(size) + size_bytes(9) + b"\x01a")),
                                         "less than the size it names"),
            "a type git does not have": (one_object(packed_header(5, 1) + zlib.compress(b"a")), "type 5"),
            "a size its header does not give": (one_object(packed_header(3, 2) + zlib.compress(b"a")),
                                                "not the one its header gives"),
            "not a pack": (b"KCAP" + good[4:], "not a packfile"),
            # What would cost time or memory before any size is compared:
            # numbers longer than git's 64 bits, a stream that inflates
            # past its header, a size past what the scrub reads.
            "a header longer than git reads": (one_object(b"\xb0" + too_long + zlib.compress(b"a")),
                                               "header longer than git reads"),
            "a distance longer than git reads": (sealed(b"PACK" + (2).to_bytes(4, "big") + (2).to_bytes(4, "big")
                                                        + packed_header(BLOB, size) + zlib.compress(self.BASE)
                                                        + packed_header(runner.OFS_DELTA, 1) + too_long
                                                        + zlib.compress(b"\x01")),
                                                 "distance longer than git reads"),
            "a delta size longer than git reads": (packfile(self.BASE, ("ofs", 0, too_long + b"\x01a")),
                                                   "delta size longer than git reads"),
            "an object that inflates past its header": (one_object(packed_header(3, 1) + zlib.compress(b"a" * 1000)),
                                                        "larger than its header gives"),
            "an object past what the scrub reads": (one_object(packed_header(3, runner.LARGEST_OBJECT + 1)
                                                               + zlib.compress(b"a")), "more than the scrub reads"),
            "a delta past what the scrub reads": (packfile(self.BASE, ("ofs", 0, size_bytes(size)
                                                                       + size_bytes(runner.LARGEST_OBJECT + 1)
                                                                       + b"\x01a")), "more than the scrub reads"),
        }
        for why, (pack, said) in refused.items():
            with self.subTest(why=why), self.assertRaisesRegex(ValueError, said):
                runner.pack_contents(pack)


class Scrubbing(unittest.TestCase):
    """scrub_credentials on its own: every file under the directories, a
    gzip file through gzip, the longest value first, a short placeholder
    left alone, and a symlink never followed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-scrub-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_file_under_the_directories_is_rewritten_and_the_rest_left_alone(self):
        deep = self.tmp / "pair" / "deep"
        deep.mkdir(parents=True)
        (deep / "stream.jsonl").write_text(f"a {PLANTED} b\n")
        (self.tmp / "pair" / "clean.txt").write_text("nothing here\n")
        with gzip.open(self.tmp / "pair" / "trajectory.json.gz", "wb") as packed:
            packed.write(f'{{"text": "{PLANTED}"}}'.encode())
        (self.tmp / "hub").mkdir()
        (self.tmp / "hub" / "events.log").write_bytes(b"\x00" + PLANTED.encode() + b"\xff")
        (self.tmp / "elsewhere.txt").write_text(PLANTED + "\n")
        before = (self.tmp / "pair" / "clean.txt").stat().st_mtime_ns
        rewritten = runner.scrub_credentials([self.tmp / "pair", self.tmp / "hub", self.tmp / "missing"],
                                             values=[PLANTED])
        self.assertEqual(sorted(str(path.relative_to(self.tmp)) for path in rewritten),
                         ["hub/events.log", "pair/deep/stream.jsonl", "pair/trajectory.json.gz"])
        self.assertEqual((deep / "stream.jsonl").read_text(), f"a {runner.SCRUBBED} b\n")
        self.assertEqual(gzip.open(self.tmp / "pair" / "trajectory.json.gz", "rb").read(),
                         f'{{"text": "{runner.SCRUBBED}"}}'.encode())
        self.assertEqual((self.tmp / "hub" / "events.log").read_bytes(),
                         b"\x00" + runner.SCRUBBED.encode() + b"\xff")
        self.assertEqual((self.tmp / "pair" / "clean.txt").stat().st_mtime_ns, before)
        self.assertEqual((self.tmp / "elsewhere.txt").read_text(), PLANTED + "\n")

    def test_the_values_come_from_the_token_s_names_longest_first_and_short_ones_are_ignored(self):
        environ = {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED, "COORDBENCH_CLAUDE_TOKEN": PLANTED + "-longer",
                   "COORD_ADMIN_TOKEN": ADMIN_SECRET}
        self.assertEqual(runner.credential_values(environ), [PLANTED + "-longer", PLANTED])
        self.assertEqual(runner.credential_values({"CLAUDE_CODE_OAUTH_TOKEN": "short"}), [])
        self.assertEqual(runner.credential_values({}), [])
        (self.tmp / "file.txt").write_text("short text\n")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "short"}, clear=False):
            self.assertEqual(runner.scrub_credentials([self.tmp]), [])
        self.assertEqual((self.tmp / "file.txt").read_text(), "short text\n")
        self.assertEqual(runner.scrub_bytes(b"x" + PLANTED.encode() + b"-longer", [PLANTED + "-longer", PLANTED]),
                         b"x" + runner.SCRUBBED.encode())

    def test_a_symlink_is_not_followed(self):
        outside = self.tmp / "outside.txt"
        outside.write_text(PLANTED + "\n")
        inside = self.tmp / "pair"
        inside.mkdir()
        (inside / "link.txt").symlink_to(outside)
        self.assertEqual(runner.scrub_credentials([inside], values=[PLANTED]), [])
        self.assertEqual(outside.read_text(), PLANTED + "\n")

    def test_an_archive_is_left_whole_and_an_earlier_attempt_is_scrubbed_before_it_is_packed(self):
        run_dir = self.tmp / "pair"
        run_dir.mkdir()
        (run_dir / "a.txt").write_text(f"token {PLANTED}\n")
        (run_dir / "b.txt").write_text("clean\n")
        stray = self.tmp / "pair" / "agent-made.tar.gz"
        with tarfile.open(stray, "w:gz") as bundle:
            bundle.add(run_dir / "a.txt", arcname="a.txt")
        stray_bytes = stray.read_bytes()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False):
            archive = runner.set_aside_earlier_attempt(run_dir)
        with tarfile.open(archive) as bundle:
            self.assertEqual(sorted(bundle.getnames()), ["a.txt", "agent-made.tar.gz", "b.txt"])
            self.assertEqual(bundle.extractfile("a.txt").read(), f"token {runner.SCRUBBED}\n".encode())
            self.assertEqual(bundle.extractfile("agent-made.tar.gz").read(), stray_bytes)
        archive_bytes = archive.read_bytes()
        self.assertEqual(runner.scrub_credentials([run_dir], values=[PLANTED]), [])
        self.assertEqual(archive.read_bytes(), archive_bytes)
        self.assertFalse((run_dir / "a.txt").exists())

    def test_a_scrubbed_gzip_has_no_name_and_no_time_in_its_header(self):
        path = self.tmp / "trajectory.json.gz"
        path.write_bytes(gzip.compress(f'{{"text": "{PLANTED}"}}'.encode(), mtime=0))
        self.assertEqual(runner.scrub_credentials([self.tmp], values=[PLANTED]), [path])
        header = path.read_bytes()[:10]
        self.assertEqual(header[4:8], b"\x00\x00\x00\x00")  # no time
        self.assertEqual(header[3] & 0x08, 0)  # no name
        self.assertEqual(gzip.decompress(path.read_bytes()), f'{{"text": "{runner.SCRUBBED}"}}'.encode())
        # The same content scrubbed twice gives the same bytes, whatever
        # the Python's gzip writes in the operating-system byte.
        path.write_bytes(gzip.compress(f'{{"text": "{PLANTED}"}}'.encode(), mtime=0))
        first = path.read_bytes() if not runner.scrub_credentials([self.tmp], values=[PLANTED]) else path.read_bytes()
        path.write_bytes(gzip.compress(f'{{"text": "{PLANTED}"}}'.encode(), mtime=0))
        runner.scrub_credentials([self.tmp], values=[PLANTED])
        self.assertEqual(path.read_bytes(), first)

    def test_a_broken_gzip_is_said_and_the_other_files_are_still_scrubbed(self):
        (self.tmp / "broken.gz").write_bytes(b"\x1f\x8b\x08\x00" + PLANTED.encode())
        (self.tmp / "stream.jsonl").write_text(PLANTED + "\n")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rewritten = runner.scrub_credentials([self.tmp], values=[PLANTED])
        self.assertEqual(rewritten, [self.tmp / "stream.jsonl"])
        self.assertIn("scrubbing", err.getvalue())
        self.assertIn("broken.gz", err.getvalue())
        self.assertEqual((self.tmp / "stream.jsonl").read_text(), runner.SCRUBBED + "\n")

    def test_a_file_that_cannot_be_rewritten_fails_the_scrub_once_the_rest_is_done(self):
        (self.tmp / "a_stream.jsonl").write_text(PLANTED + "\n")
        (self.tmp / "b_stream.jsonl").write_text(PLANTED + "\n")
        real = runner.write_scrubbed

        def refusing(path, cleaned):
            if path.name == "a_stream.jsonl":
                raise PermissionError(13, "Permission denied")
            real(path, cleaned)

        with mock.patch.object(runner, "write_scrubbed", refusing):
            with self.assertRaises(runner.CredentialKept) as caught:
                runner.scrub_credentials([self.tmp], values=[PLANTED])
        self.assertIn("a_stream.jsonl", str(caught.exception))
        self.assertIn("could not be rewritten", str(caught.exception))
        self.assertEqual((self.tmp / "b_stream.jsonl").read_text(), runner.SCRUBBED + "\n")

    def test_the_scrub_command_rewrites_a_run_directory_and_refuses_to_publish_what_it_cannot_clean(self):
        run_dir = self.tmp / "run"
        (run_dir / "coop").mkdir(parents=True)
        (run_dir / "coop" / "stream.jsonl").write_text(f"a {PLANTED} b\n")
        (run_dir / "coop" / "clean.txt").write_text("nothing here\n")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 0)
        self.assertEqual((run_dir / "coop" / "stream.jsonl").read_text(), f"a {runner.SCRUBBED} b\n")
        self.assertIn("1 files", err.getvalue())
        self.assertIn("no file holds", err.getvalue())
        # The credential inside an archive an agent packed, or a file that cannot be read: not published.
        with tarfile.open(run_dir / "coop" / "packed.tar.gz", "w:gz") as packed:
            data = io.BytesIO(PLANTED.encode())
            info = tarfile.TarInfo("env.txt")
            info.size = len(PLANTED)
            packed.addfile(info, data)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 2)
        self.assertIn("packed.tar.gz: holds the agents' credential, read through gzip", err.getvalue())
        (run_dir / "coop" / "packed.tar.gz").unlink()
        (run_dir / "coop" / "broken.gz").write_bytes(b"\x1f\x8b\x08\x00 not gzip")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 2)
        self.assertIn("broken.gz: could not be read", err.getvalue())
        # A git object the team arm's shared/ copy brings is read inflated: a loose object, a packfile.
        (run_dir / "coop" / "broken.gz").unlink()
        objects = run_dir / "coop" / "shared" / ".git" / "objects"
        (objects / "ab").mkdir(parents=True)
        (objects / "pack").mkdir()
        loose = objects / "ab" / ("c" * 38)
        loose.write_bytes(zlib.compress(b"blob 60\0the token " + PLANTED.encode() + b" in a committed file\n"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 2)
        self.assertIn(f"{loose}: holds the agents' credential, read through zlib", err.getvalue())
        loose.write_bytes(zlib.compress(b"blob 5\0hello"))
        pack = objects / "pack" / "pack-1.pack"
        pack.write_bytes(packfile(b"a" * 300, b"export TOKEN=" + PLANTED.encode() + b"\n", b"tail"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 2)
        self.assertIn(f"{pack}: holds the agents' credential, read through git packfile", err.getvalue())
        pack.write_bytes(packfile(b"a" * 300, b"nothing", b"tail")[:-40])
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 2)
        self.assertIn(f"{pack}: could not be read", err.getvalue())
        pack.write_bytes(packfile(b"a" * 300, b"nothing", b"tail"))
        (objects / "pack" / "pack-1.idx").write_bytes(b"\xfftOc" + bytes(40))
        # A clean archive passes, no credential in the environment is nothing to find, and no directory is 2.
        with tarfile.open(run_dir / "coop" / "packed.tar", "w") as packed:
            info = tarfile.TarInfo("note.txt")
            info.size = 5
            packed.addfile(info, io.BytesIO(b"hello"))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 0)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "", "COORDBENCH_CLAUDE_TOKEN": ""}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 0)
            self.assertEqual(runner.main(["scrub", "--run-dir", str(self.tmp / "missing")]), 2)
        self.assertIn("nothing to scrub", err.getvalue())

    def test_the_scrub_command_removes_symlinks_and_special_files_which_the_upload_would_follow(self):
        """actions/upload-artifact follows a symlink and packs what it
        points at, which the scrub never reads; so the command removes
        every symlink (to a file or a directory, outside the run or not)
        and special file before it reads anything, whatever the
        environment holds, and leaves what they point at alone."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "env.txt").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={PLANTED}\n")
        run_dir = self.tmp / "run"
        (run_dir / "coop" / "shared").mkdir(parents=True)
        (run_dir / "coop" / "clean.txt").write_text("nothing here\n")
        (run_dir / "coop" / "shared" / "env.txt").symlink_to(outside / "env.txt")
        (run_dir / "coop" / "shared" / "dir").symlink_to(outside, target_is_directory=True)
        (run_dir / "coop" / "dangling").symlink_to(self.tmp / "nothing")
        links = ["coop/dangling", "coop/shared/dir", "coop/shared/env.txt"]
        if hasattr(os, "mkfifo"):
            os.mkfifo(run_dir / "coop" / "pipe")
            links.append("coop/pipe")
        kept = runner.unscrubbed(run_dir, [PLANTED])
        self.assertEqual(sorted(line.split(": ", 1)[0] for line in kept), sorted(str(run_dir / link) for link in links))
        self.assertIn("is a symlink, which the upload would follow", "\n".join(kept))
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 0)
        self.assertIn(f"removed {len(links)} symlinks and special files", err.getvalue())
        self.assertIn("no file holds", err.getvalue())
        self.assertEqual(sorted(str(path.relative_to(run_dir)) for path in run_dir.rglob("*")),
                         ["coop", "coop/clean.txt", "coop/shared"])
        self.assertEqual((outside / "env.txt").read_text(), f"CLAUDE_CODE_OAUTH_TOKEN={PLANTED}\n")
        # With no credential in the environment, a fake run's, a link goes all the same.
        (run_dir / "coop" / "again").symlink_to(outside / "env.txt")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "", "COORDBENCH_CLAUDE_TOKEN": ""}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["scrub", "--run-dir", str(run_dir)]), 0)
        self.assertFalse((run_dir / "coop" / "again").is_symlink())

    def test_a_pair_scrubs_its_own_hub_events_and_the_hub_s_files_wait_for_the_hub_to_stop(self):
        run_dir = self.tmp / "run" / "arm" / "pair"
        hub = self.tmp / "run" / "hub"
        (hub / "mine").mkdir(parents=True)
        (hub / "other").mkdir()
        run_dir.mkdir(parents=True)
        for path in (run_dir / "stream.jsonl", hub / "mine" / "events.jsonl", hub / "other" / "events.jsonl",
                     hub / "hub.log"):
            path.write_text(PLANTED + "\n")
        plan = {"log_dir": str(self.tmp), "run_name": "run", "run_dir": str(run_dir), "run_id": "mine"}
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                runner.scrub_pair(plan)
            self.assertIn("was in 2 files", err.getvalue())
            self.assertEqual((run_dir / "stream.jsonl").read_text(), runner.SCRUBBED + "\n")
            self.assertEqual((hub / "mine" / "events.jsonl").read_text(), runner.SCRUBBED + "\n")
            self.assertEqual((hub / "other" / "events.jsonl").read_text(), PLANTED + "\n")
            self.assertEqual((hub / "hub.log").read_text(), PLANTED + "\n")
            with contextlib.redirect_stderr(io.StringIO()):
                runner.scrub_hub(hub)
            self.assertEqual((hub / "hub.log").read_text(), runner.SCRUBBED + "\n")
            self.assertEqual((hub / "other" / "events.jsonl").read_text(), PLANTED + "\n")


def tar_of(members, compression=""):
    """A tar's bytes: each member a name and its bytes, or a name and
    ("link", target) for a symlink."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode=f"w:{compression}") as packed:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(content, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                packed.addfile(info)
            else:
                info.size = len(content)
                packed.addfile(info, io.BytesIO(content))
    return out.getvalue()


def zip_of(members, comment=b"", method=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as packed:
        packed.comment = comment
        for name, content in members.items():
            packed.writestr(name, content)
    return out.getvalue()


def gzipped(data, times=1, name=""):
    for _ in range(times):
        out = io.BytesIO()
        with gzip.GzipFile(filename=name, mode="wb", fileobj=out, mtime=0) as packed:
            packed.write(data)
        data = out.getvalue()
    return data


class ReadingBack(unittest.TestCase):
    """unscrubbed, the read-back before an upload: every layer a file is
    packed in is opened by what its bytes are, whatever its name or
    place, with the plain search at every layer, so a header's names are
    searched too; a path's name is searched; what cannot be opened is
    refused; and nothing it says quotes the credential."""

    # No user's or system's git configuration takes part, and git's
    # detached maintenance never races the cleanup (symphony-template#63).
    GIT_SETTINGS = {"gc.auto": "0", "gc.autoDetach": "false", "maintenance.auto": "false",
                    "maintenance.autoDetach": "false", "receive.autogc": "false"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-read-back-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()

    def said(self, name, data):
        """What the read-back says of one file, alone in the run directory."""
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            return runner.unscrubbed(self.run_dir, [PLANTED])
        finally:
            path.unlink()

    def assertHeld(self, name, data, *layers):
        through = f", read through {', '.join(layers)}" if layers else ""
        self.assertEqual(self.said(name, data),
                         [f"{self.run_dir / name}: holds the agents' credential{through}"])

    def assertRefused(self, name, data, why):
        said = self.said(name, data)
        self.assertEqual(len(said), 1, said)
        self.assertRegex(said[0], rf"^{re.escape(str(self.run_dir / name))}: could not be read \(.*{why}")

    def git(self, *arguments):
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_COUNT": str(len(self.GIT_SETTINGS))}
        for index, (key, value) in enumerate(self.GIT_SETTINGS.items()):
            environment[f"GIT_CONFIG_KEY_{index}"] = key
            environment[f"GIT_CONFIG_VALUE_{index}"] = value
        subprocess.run(["git", *arguments], env=environment, check=True, capture_output=True, timeout=120)
        if arguments[0] == "init":
            for key, value in self.GIT_SETTINGS.items():
                subprocess.run(["git", "-C", arguments[-1], "config", key, value], check=True, timeout=60)

    def repository_tarball(self, repo, compression):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode=f"w:{compression}") as packed:
            packed.add(repo, arcname="repo")
        return out.getvalue()

    def test_a_tarball_of_a_repository_shows_a_token_committed_and_removed_from_its_history(self):
        repo = self.tmp / "repo"
        self.git("init", "-q", str(repo))
        (repo / "README").write_text("a clean repository\n")
        self.git("-C", str(repo), "add", "README")
        self.git("-C", str(repo), "commit", "-q", "-m", "first")
        self.assertEqual(self.said("repo.tar.xz", self.repository_tarball(repo, "xz")), [])
        (repo / "env.txt").write_text(f"CLAUDE_CODE_OAUTH_TOKEN={PLANTED}\n")
        self.git("-C", str(repo), "add", "env.txt")
        self.git("-C", str(repo), "commit", "-q", "-m", "oops")
        self.git("-C", str(repo), "rm", "-q", "env.txt")
        self.git("-C", str(repo), "commit", "-q", "-m", "removed")
        self.assertHeld("repo.tar.xz", self.repository_tarball(repo, "xz"), "xz", "tar", "zlib")
        self.git("-C", str(repo), "gc", "-q")
        self.assertFalse(list((repo / ".git" / "objects").glob("??/*")), "gc left loose objects")
        self.assertHeld("repo.tar.bz2", self.repository_tarball(repo, "bz2"), "bzip2", "tar", "git packfile")
        self.git("-C", str(repo), "bundle", "create", str(self.tmp / "repo.bundle"), "--all")
        self.assertHeld("history", (self.tmp / "repo.bundle").read_bytes(), "git bundle", "git packfile")

    def test_layers_are_opened_by_their_bytes_whatever_the_file_is_named_or_where_it_is(self):
        secret = f"export TOKEN={PLANTED}\n".encode() * 40
        self.assertHeld("dist/pkg-1.0-py3-none-any.whl", zip_of({"pkg/env.py": secret}), "zip")
        self.assertHeld("work.tar.bz2", tar_of({"env.txt": secret}, "bz2"), "bzip2")
        self.assertHeld("work.bin", gzipped(secret), "gzip")
        self.assertHeld("shared/.git/objects/incoming-x1/tmp_obj_ab12", zlib.compress(b"blob 9\0" + secret), "zlib")
        # A committed archive's object is opened as the archive would be.
        self.assertHeld("loose", zlib.compress(b"blob 99\0" + gzipped(secret)), "zlib", "gzip")
        # A gzip file's members are one content, as zcat gives it, and what follows the last is opened too.
        half = len(PLANTED) // 2
        self.assertHeld("split.gz", gzipped(b"a " + PLANTED[:half].encode()) + gzipped(PLANTED[half:].encode()),
                        "gzip")
        self.assertHeld("then.gz", gzipped(b"clean") + zip_of({"env.txt": secret}), "gzip", "zip")
        streams = lzma.compress(b"clean") + bytes(4) + lzma.compress(secret)
        self.assertHeld("two.xz", streams, "xz")
        self.assertHeld("two.bz2", bz2.compress(b"clean") + bz2.compress(secret), "bzip2")
        self.assertHeld("deep.tgz", tar_of({"inner.zip": zip_of({"env.txt": secret})}, "gz"), "gzip", "tar", "zip")
        self.assertEqual(self.said("clean.tgz", tar_of({"inner.zip": zip_of({"note.txt": b"hello" * 99, "dir/": b""}),
                                                         "elsewhere": ("link", "/nowhere")}, "gz")), [])

    def test_headers_are_searched_as_they_are_and_the_scrub_leaves_a_container_whole(self):
        self.assertHeld("notes.zip", zip_of({"note.txt": b"hello"}, comment=PLANTED.encode()))
        self.assertHeld("named.zip", zip_of({f"{PLANTED}.txt": b"hello"}))
        self.assertHeld("named.gz", gzipped(b"hello", name=f"{PLANTED}.txt"))
        self.assertHeld("linked.tgz", tar_of({"env": ("link", f"/run/{PLANTED}")}, "gz"), "gzip")
        # A container named as anything is left whole by the scrub, and refused by the read-back.
        container = self.run_dir / "notes.dat"
        container.write_bytes(zip_of({"env.txt": PLANTED.encode()}, method=zipfile.ZIP_STORED))
        before = container.read_bytes()
        self.assertEqual(runner.scrub_credentials([self.run_dir], [PLANTED]), [])
        self.assertEqual(container.read_bytes(), before)
        self.assertEqual(runner.unscrubbed(self.run_dir, [PLANTED]), [f"{container}: holds the agents' credential"])

    def test_a_name_that_holds_the_credential_is_refused_and_never_quoted(self):
        (self.run_dir / f"{PLANTED}.txt").write_text("clean\n")
        (self.run_dir / f"dir-{PLANTED}").mkdir()
        (self.run_dir / f"dir-{PLANTED}" / "note.txt").write_text("clean\n")
        (self.run_dir / f"link-{PLANTED}").symlink_to(self.run_dir / f"{PLANTED}.txt")
        scrubbed = runner.SCRUBBED
        self.assertEqual(runner.unscrubbed(self.run_dir, [PLANTED]), [
            f"{self.run_dir / (scrubbed + '.txt')}: its name holds the agents' credential",
            f"{self.run_dir / ('dir-' + scrubbed)}: its name holds the agents' credential",
            f"{self.run_dir / ('link-' + scrubbed)}: its name holds the agents' credential",
            f"{self.run_dir / ('link-' + scrubbed)}: is a symlink, which the upload would follow to what the "
            "scrub never read"])
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": PLANTED}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(self.run_dir)]), 2)
        self.assertIn(f"removed 1 symlinks and special files, which the upload would follow: link-{scrubbed}",
                      err.getvalue())
        self.assertIn("its name holds the agents' credential", err.getvalue())
        self.assertNotIn(PLANTED, err.getvalue())

    def test_what_the_read_back_cannot_open_is_refused(self):
        self.assertRefused("archive.tar.zst", b"\x28\xb5\x2f\xfd" + bytes(20), "a zstd file, which the scrub cannot open")
        self.assertRefused("archive.7z", b"7z\xbc\xaf\x27\x1c" + bytes(20), "a 7z file")
        stored = bytearray(zip_of({"env.txt": b"hello"}, method=zipfile.ZIP_STORED))
        stored[8:10] = (93).to_bytes(2, "little")
        directory = stored.find(b"PK\x01\x02")
        stored[directory + 10:directory + 12] = (93).to_bytes(2, "little")
        self.assertRefused("odd.zip", bytes(stored), "compression method")
        broken = bytearray(zip_of({"env.txt": b"hello " * 50}))
        broken[40] ^= 0xFF
        self.assertRefused("broken.whl", bytes(broken), "")
        self.assertRefused("cut.gz", gzipped(b"hello " * 1000)[:-30], "")
        self.assertRefused("cut.xz", lzma.compress(b"hello " * 1000)[:-30], "a truncated stream")
        self.assertRefused("cut.bz2", bz2.compress(b"hello " * 1000)[:-30], "")
        self.assertRefused("cut.bundle", b"# v2 git bundle\nabc refs/heads/main\n", "no packfile")
        self.assertRefused("odd.bundle", b"# v3 git bundle\nabc refs/heads/main\n\nnot a pack", "no packfile")
        self.assertRefused("cut.tar", tar_of({"env.txt": b"hello " * 1000})[:3000], "")

    def test_layers_open_eight_deep_and_no_deeper(self):
        self.assertHeld("deep.gz", gzipped(PLANTED.encode(), runner.DEEPEST_NESTING), *["gzip"] * runner.DEEPEST_NESTING)
        self.assertRefused("deeper.gz", gzipped(b"clean", runner.DEEPEST_NESTING + 1), "nested deeper")

    def test_a_file_that_unpacks_past_the_budget_is_refused_and_each_step_is_charged_first(self):
        big = b"a" * (2 << 20)
        taken = []
        real_take = runner.Budget.take

        def recording(budget, size):
            taken.append(size)
            return real_take(budget, size)

        with mock.patch.object(runner, "LARGEST_EXPANSION", 1 << 20), mock.patch.object(runner.Budget, "take", recording):
            for name, data in (("big.gz", gzipped(big)), ("big.xz", lzma.compress(big)), ("big.bz2", bz2.compress(big)),
                               ("big.zip", zip_of({"a": big})), ("big.tar", tar_of({"a": big})),
                               ("big.pack", packfile(big)), ("big.zlib", zlib.compress(big))):
                with self.subTest(name=name):
                    taken.clear()
                    self.assertRefused(name, data, "unpacks to more than the scrub reads")
                    if name != "big.pack":
                        self.assertLessEqual(max(taken), runner.EXPANSION_STEP)
            self.assertEqual(self.said("small.gz", gzipped(b"a" * (1 << 19))), [])

    def test_a_fake_run_reads_its_files_back_for_a_stand_in_and_a_real_run_for_its_token(self):
        action = (ROOT / "bench" / "cooperbench" / "actions" / "run-pair" / "action.yml").read_text(encoding="utf-8")
        stand_in = re.search(r"COORDBENCH_CLAUDE_TOKEN: \$\{\{ inputs.mode == 'fake' && '([^']+)' \|\| '' \}\}",
                             action).group(1)
        self.assertEqual(runner.credential_values({"COORDBENCH_CLAUDE_TOKEN": stand_in}), [stand_in])
        self.assertEqual(runner.credential_values({"COORDBENCH_CLAUDE_TOKEN": "", "CLAUDE_CODE_OAUTH_TOKEN": PLANTED}),
                         [PLANTED])
        (self.run_dir / "coop").mkdir()
        (self.run_dir / "coop" / "stream.jsonl").write_text("{}\n")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "", "COORDBENCH_CLAUDE_TOKEN": stand_in}), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(runner.main(["scrub", "--run-dir", str(self.run_dir)]), 0)
        self.assertIn("no file holds the agents' credential", err.getvalue())

    def test_bytes_that_only_begin_like_a_zlib_stream_are_searched_never_refused(self):
        for text in (b"x^2 + y^2 = r^2\n", b"x  marks the spot\n", b"x\x9c not a stream at all\n"):
            with self.subTest(text=text):
                self.assertEqual(self.said("notes.txt", text), [])
        # A loose object cut short is searched as far as it inflates.
        content = b"blob 99999\0" + f"TOKEN={PLANTED}\n".encode() + b"".join(b"line %d\n" % n for n in range(9000))
        cut = zlib.compress(content)[:-100]
        self.assertNotIn(PLANTED.encode(), cut)
        self.assertHeld("cut-object", cut, "zlib")


if __name__ == "__main__":
    unittest.main()
