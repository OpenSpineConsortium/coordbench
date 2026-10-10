"""The runner and the drivers are what turn an arm into scored CooperBench
results, so these pin what they do to docker, to the hub and to the logs.
Docker is a fake that records every command and answers the few whose
output the runner reads; the hub is the real one, in this process on an
ephemeral port; CooperBench's scorer is a fake with the shape of its
results. Nothing here starts a container or a Claude."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
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
    {"type": "system", "subtype": "init", "session_id": "s1", "tools": ["Bash"]},
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
# CooperBench's coop.py writes these, plus log_dir and messages_sent; the
# runner adds the arm, the driver and the hub's metrics.
RESULT_KEYS = {"repo", "task_id", "features", "setting", "run_id", "run_name", "agent_framework", "model", "arm",
               "driver", "started_at", "ended_at", "duration_seconds", "agents", "total_cost", "total_steps",
               "metrics", "timings"}
AGENT_KEYS = {"feature_id", "role", "status", "cost", "steps", "input_tokens", "output_tokens",
              "cache_read_tokens", "cache_write_tokens", "duration_ms", "api_duration_ms", "patch_lines", "error"}
USAGE_KEYS = {"cost", "steps", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"}
# Claude Code's own wall and API time for the agent, from its result event.
DURATION_KEYS = {"duration_ms", "api_duration_ms"}
TIMING_KEYS = {"phase", "agent", "started", "ended", "seconds"}
COOP_EVAL_KEYS = ["repo", "task_id", "features", "setting", "apply_status", "merge", "feature1", "feature2",
                  "both_passed", "error", "evaluated_at"]
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
        self.claude_times_out = False
        self.on_claude = None
        self.git_setups = {}
        self.git_breaks = False
        self.environments = {}

    def __call__(self, *args, input=None, timeout=None, output=None, extra_env=None):
        args = [str(arg) for arg in args]
        with self.lock:
            self.calls.append({"args": args, "input": input, "output": output, "extra_env": extra_env})
            if args[:3] == ["run", "-d", "--name"]:
                self.environments[args[3]] = dict(extra_env or {})
        code, out = self.answer(args, output)
        return subprocess.CompletedProcess(["docker", *args], code, out, "" if code == 0 else "fake failure")

    def answer(self, args, output):
        if output is not None:
            return self.run_claude(args, output)
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
        environment = {"CLAUDE_CODE_OAUTH_TOKEN": SECRET, "COORD_ADMIN_TOKEN": ADMIN_SECRET}
        for patcher in (mock.patch.object(drivers, "docker", self.docker),
                        mock.patch.object(runner, "cooperbench_test", self.scorer),
                        mock.patch.dict(os.environ, environment)):
            patcher.start()
            self.addCleanup(patcher.stop)

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
        self.assertIn(["volume", "rm", "-f", volume], self.docker.commands())
        self.assertIn(["rm", "-f", *[a["container"] for a in plan["agents"]]], self.docker.commands())

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
        self.assertEqual((result["total_cost"], result["total_steps"]), (0, 0))
        self.assertEqual({a["patch_lines"] for a in result["agents"].values()}, {0})
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
        self.assertIn("timed out", outcome.error)
        scripts = [c[-1] for c in self.docker.matching("exec", "c-agent1", "sh", "-c")]
        self.assertFalse([script for script in scripts if "pkill" in script])
        stops = [script for script in scripts if "/tmp/coord-claude.pid" in script]
        self.assertEqual(len(stops), 1)
        self.assertIn("cat /tmp/coord-claude.pid", stops[0])
        self.assertIn('kill "$pid"', stops[0])

    def test_a_stream_cut_short_keeps_its_last_line_as_the_error(self):
        self.docker.stream = '{"type": "system"}\nInvalid API key. Please run /login\n'
        driver = drivers.ClaudeCliDriver()
        outcome = driver.wait(driver.start(self.job()))
        self.assertEqual(outcome.status, "Error")
        self.assertIn("Invalid API key", outcome.error)


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
        self.assertIn("Usage limit", failed.error)

    def test_a_stream_without_a_result_is_an_error_with_nothing_counted(self):
        outcome = drivers.summarize_stream('{"type": "assistant"}\ngarbage\n')
        self.assertEqual((outcome.status, outcome.input_tokens, outcome.steps, outcome.cost), ("Error", 0, 0, 0.0))
        self.assertEqual((outcome.duration_ms, outcome.api_duration_ms), (None, None))

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
        logged = json.loads(Path(job.log_path).read_text().splitlines()[-1])
        self.assertEqual(logged["status"], "Submitted")

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
                         ["agent1.session-prompt.md", "agent1_stream.jsonl"])

    def test_drivers_load_by_name_or_from_a_file(self):
        self.assertIsInstance(drivers.load_driver("session"), drivers.SessionDriver)
        path = self.tmp / "mine.py"
        path.write_text("class Mine:\n    name = 'mine'\n    def start(self, job): return job\n"
                        "    def wait(self, handle): return None\n")
        self.assertEqual(drivers.load_driver(f"{path}:Mine").name, "mine")
        with self.assertRaises(ValueError):
            drivers.load_driver("nonesuch")


class Runs(RunnerCase):
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
                     "agent2_stream.jsonl", "agent1_prompt.md", "events.jsonl", "metrics.json"):
            self.assertTrue((pair / name).is_file(), name)
        self.assertEqual((pair / "agent1.patch").read_text(), PATCHES["agent1"])
        self.assertEqual((pair / "agent6.patch").read_text(), PATCHES["agent2"])
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual((result["setting"], result["arm"], result["driver"], result["agent_framework"]),
                         ("coop", "coop", "claude-cli", "claude_code"))
        self.assertEqual((result["repo"], result["task_id"], result["features"], result["model"]),
                         (REPO, TASK, [1, 6], "claude-test"))
        self.assertEqual(set(result["agents"]), {"agent1", "agent2"})
        for agent, feature in (("agent1", 1), ("agent2", 6)):
            record = result["agents"][agent]
            self.assertEqual(set(record), AGENT_KEYS)
            self.assertEqual((record["feature_id"], record["status"], record["input_tokens"], record["steps"]),
                             (feature, "Submitted", 1200, 12))
            # The fields CooperBench's coop.py writes and its summary reads.
            self.assertEqual((record["cost"], record["patch_lines"]), (0.42, 6))
            # Claude Code's own clock for the agent, from the result event.
            self.assertEqual((record["duration_ms"], record["api_duration_ms"]), (95000, 61000))
        self.assertAlmostEqual(result["total_cost"], 0.84)
        self.assertEqual(result["total_steps"], 24)
        self.assertEqual(result["metrics"]["messages_sent"], 1)
        # Every phase of the pair is timed, the agents and their
        # provisioning one by one, and the score and the teardown are in
        # result.json though it was written before them.
        self.assert_timings_well_formed(result["timings"])
        self.assertEqual(phases(result["timings"]),
                         [("hub", None), ("run_create", None), ("provision", "agent1"), ("provision", "agent2"),
                          ("agent", "agent1"), ("agent", "agent2"), ("close", None), ("teardown", None),
                          ("score", None)])
        self.assertRegex(err, r"timings: hub [0-9.]+ s, run_create [0-9.]+ s, provision [0-9.]+ s, "
                              r"agents [0-9.]+ s \(agent1 [0-9.]+ s, agent2 [0-9.]+ s\), close [0-9.]+ s, "
                              r"score [0-9.]+ s, teardown [0-9.]+ s\n")
        self.assertEqual(json.loads((pair / "metrics.json").read_text()), result["metrics"])
        events = [json.loads(line) for line in (pair / "events.jsonl").read_text().splitlines()]
        self.assertIn("message", [event["kind"] for event in events])
        evaluation = json.loads((pair / "eval.json").read_text())
        self.assertEqual(list(evaluation), COOP_EVAL_KEYS)
        self.assertEqual((evaluation["both_passed"], evaluation["setting"], evaluation["features"]),
                         (True, "coop", [1, 6]))
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
        self.assertEqual(runner.close_run(plan, client), ({}, []))
        self.assertEqual(plan["warnings"], [])

    def test_a_solo_pair_is_scored_as_one_patch(self):
        code, _, err = self.main(*self.run_arguments("solo", "--features", "1,6"))
        self.assertEqual(code, 0, err)
        pair = self.pair("solo")
        self.assertEqual((pair / "solo.patch").read_text(), PATCHES["agent1"])
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual(result["setting"], "solo")
        self.assertEqual(result["agents"]["agent1"]["feature_id"], [1, 6])
        evaluation = json.loads((pair / "eval.json").read_text())
        self.assertEqual(list(evaluation), SOLO_EVAL_KEYS)
        self.assertIsNone(evaluation["merge"])
        kind, arguments, _ = self.scorer.calls[0]
        self.assertEqual((kind, arguments["patch"]), ("solo", PATCHES["agent1"]))

    def test_a_subset_runs_every_pair_and_finished_pairs_are_kept(self):
        code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash"))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.pair(features="f1_f6") / "eval.json").exists())
        self.assertTrue((self.pair(features="f2_f10") / "eval.json").exists())
        before = len(self.docker.calls)
        code, _, err = self.main(*self.run_arguments("coop", "--subset", "flash"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.docker.calls), before)
        self.assertIn("skipped", err)

    def test_a_finished_pair_without_eval_json_is_only_scored_again(self):
        self.main(*self.run_arguments("coop", "--features", "1,6"))
        (self.pair() / "eval.json").unlink()
        before = len(self.docker.calls)
        code, _, _ = self.main(*self.run_arguments("coop", "--features", "1,6"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.docker.calls), before)
        self.assertEqual(len(self.scorer.calls), 2)
        self.assertTrue((self.pair() / "eval.json").exists())

    def test_prepare_then_finish_with_the_session_driver(self):
        code, out, err = self.main("prepare", "--arm", "coop", "--repo", REPO, "--task", str(TASK),
                                   "--features", "1,6", "--hub", self.hub_url, "--name", "t",
                                   "--cooperbench-dir", str(self.cooperbench))
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        pair = self.pair()
        self.assertEqual(plan["run_dir"], str(pair))
        self.assertEqual([a["id"] for a in plan["agents"]], ["agent1", "agent2"])
        self.assertEqual(set(plan["agents"][0]), {"id", "prompt_file"})
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

        said = out + err
        code, out, err = self.main("finish", "--run-dir", str(pair))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"run_dir": str(pair), "verdict": "pass"})
        result = json.loads((pair / "result.json").read_text())
        self.assertEqual((result["driver"], result["agent_framework"]), ("session", "claude_code_subagent"))
        self.assertEqual({a["status"] for a in result["agents"].values()}, {"Submitted"})
        # A subagent's usage is not measured, so it is unknown, not zero,
        # and so are the durations Claude Code's result event would give.
        for record in result["agents"].values():
            self.assertEqual(set(record), AGENT_KEYS)
            self.assertEqual({record[name] for name in USAGE_KEYS | DURATION_KEYS}, {None})
            self.assertEqual(record["patch_lines"], 6)
        self.assertEqual((result["total_cost"], result["total_steps"]), (None, None))
        self.assertEqual(result["metrics"]["messages_sent"], 1)
        # The agent's phase runs from prepare's start, kept in the plan,
        # to the marker finish saw: one record across the two processes.
        self.assert_timings_well_formed(result["timings"])
        self.assertEqual(phases(result["timings"]),
                         [("hub", None), ("run_create", None), ("provision", "agent1"), ("provision", "agent2"),
                          ("agent", "agent1"), ("agent", "agent2"), ("close", None), ("teardown", None),
                          ("score", None)])
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
        self.assertEqual(list(json.loads((pair / "eval.json").read_text())), COOP_EVAL_KEYS)
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
                         ["phase_done"] * 8, "the hub's phase came before the run directory existed")
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
        self.assertEqual([e["kind"] for e in runner_events(memory)][-len(ours):], [e["kind"] for e in ours])
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
                          ("score", None)])
        polls = [e for e in later if e["kind"] == "marker_poll"]
        self.assertEqual([(e["agent"], e["found"]) for e in polls], [("agent1", True), ("agent2", True)])
        for poll in polls:
            self.assertEqual(set(poll), {"kind", "t", "ts", "agent", "found"})
        self.assertIn("result.json", [e["file"] for e in later if e["kind"] == "wrote"])
        self.assert_secret_nowhere(out, err)
        self.assert_tokens_kept_private(saved, out, err)

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
        self.assertIn("events", client.close_run("r-mine"))
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


if __name__ == "__main__":
    unittest.main()
