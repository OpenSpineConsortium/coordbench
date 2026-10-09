"""The runner: task pairs under one arm with one driver, scored as CooperBench scores them.

    python3 run.py run --arm coop --repo pallets_click_task --task 2068 --features 1,6
        [--driver claude-cli|session|path/to/driver.py:Class] [--model M] [--name RUN]
        [--log-dir DIR] [--hub URL] [--timeout-min 120] [--max-turns N] [--keep] [--force]
        [--option KEY=VALUE ...] [--cooperbench-dir DIR]
    python3 run.py run ... --subset flash     instead of --features: every flash pair of the task
    python3 run.py prepare ... --features 1,6  provision one pair for the session driver
    python3 run.py finish --run-dir DIR        wait for it, score it, write its logs, tear it down

For each pair the runner reads the features' specs from CooperBench's
dataset, provisions what the arm needs (a shared git remote, a shared
volume), asks the hub for a run (POST /api/runs), starts one container per
agent from the task's image with coordbench inside it, hands each agent to
the driver, and once they stop collects their patches, closes the run on
the hub and scores the patches with CooperBench's own evaluation. The hub
is the one named by --hub, or hub.py started here on a free port.

The logs take CooperBench's layout, so experiment/report.py and
`cooperbench eval` read them as they read the authors' runs:

    <log_dir>/<run_name>/<setting>/<repo>/<task>/f<a>_f<b>/
        result.json  eval.json  solo.patch or agent<feature>.patch
        <agent>_stream.jsonl  <agent>_prompt.md  events.jsonl  metrics.json  plan.json

The setting is "solo" when the arm lays out one agent and "coop"
otherwise, which is what decides how CooperBench scores the pair. A pair
whose result.json shows no agent in Error is finished and skipped, unless
--force; one that lacks only its eval.json is scored again without
running the agents, as CooperBench's runner and evaluator behave.

prepare and finish split a run in two for the session driver, so the
orchestrating Claude Code session can spawn its subagents in between:
prepare provisions the pair, writes each agent's prompt and prints
{"run_dir", "agents": [{"id", "prompt_file"}]}; finish waits for every
agent's marker and does the rest. prepare keeps what finish needs in
<run_dir>/plan.json, and a hub prepare started stays up until finish.

Every docker command goes through drivers.docker, so tests swap in a fake.
The subscription token reaches agents by name only (drivers.PASSED_BY_NAME)
and is never written anywhere by this module.
"""

import argparse
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cli  # noqa: E402
import drivers  # noqa: E402
from arm import builtin_arms_dir, find_arm  # noqa: E402
from drivers import CONTAINER_REPO, checked, safe_name  # noqa: E402

NETWORK = "coordbench"
# Named apart from CooperBench's own cooperbench-git, which publishes no
# port, so the two never fight over a name and the hub's host can reach
# this one on 127.0.0.1.
GIT_SERVER = "coordbench-git"
GIT_IMAGE = "cooperbench-git-server:local"
GIT_DATA_VOLUME = "coordbench-git-data"
GIT_PORT = 9418
SHARED_DIR = "/workspace/shared"
KNOWN_NEEDS = ("git_remote", "shared_volume")
# sleep infinity is GNU; busybox's sleep may refuse it, so fall back to a loop.
KEEP_ALIVE = "sleep infinity 2>/dev/null || while :; do sleep 86400; done"
SCORING_TIMEOUT_SECONDS = 3 * 3600

# The hub is local, so a proxy set for outbound traffic must not carry it.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(text):
    print(f"coordbench run: {text}", file=sys.stderr, flush=True)


def now():
    return datetime.now()


# The dataset.

def cooperbench_home():
    return Path(os.environ.get("COOPERBENCH_DIR") or Path.home() / "cooperbench")


def read_spec(dataset_dir, repo, task_id, feature):
    path = Path(dataset_dir) / repo / f"task{task_id}" / f"feature{feature}" / "feature.md"
    if not path.is_file():
        raise FileNotFoundError(f"no spec for feature {feature} of {repo}/{task_id} at {path}")
    return path.read_text(encoding="utf-8")


def read_specs(dataset_dir, repo, task_id, features):
    return {feature: read_spec(dataset_dir, repo, task_id, feature) for feature in features}


def subset_pairs(dataset_dir, subset, repo, task_id):
    """Every pair a subset (dataset/subsets/<name>.json) names for one task."""
    path = Path(dataset_dir) / "subsets" / f"{subset}.json"
    entries = json.loads(path.read_text(encoding="utf-8"))["tasks"]
    for entry in entries:
        if entry["repo"] == repo and int(entry["task_id"]) == int(task_id):
            return [sorted(int(f) for f in pair) for pair in entry.get("pairs", [])]
    raise ValueError(f"the {subset} subset has no task {repo}/{task_id}")


def image_name(repo, task_id):
    """CooperBench's get_image_name (utils.py at 63b9d44)."""
    return f"akhatua/cooperbench-{repo.replace('_task', '').replace('_', '-')}:task{task_id}"


def feature_dir_name(features):
    return "_".join(f"f{feature}" for feature in sorted(features))


def pair_dir(log_dir, run_name, setting, repo, task_id, features):
    return Path(log_dir) / run_name / setting / repo / str(task_id) / feature_dir_name(features)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def pair_finished(run_dir):
    """CooperBench reruns a pair any of whose agents ended in Error."""
    result = read_json(Path(run_dir) / "result.json")
    if not result or result.get("error"):
        return False
    return not any(a.get("status") == "Error" for a in (result.get("agents") or {}).values())


# The hub.

class HubError(RuntimeError):
    """The hub refused a request or could not be reached."""


class HubClient:
    """The runner's side of the hub's HTTP API, at the URL the runner
    reaches it by (which is not the one containers use)."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, body=None, timeout=120):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json",
                                                  "Accept": "application/json"})
        try:
            with OPENER.open(request, timeout=timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as problem:
            raise HubError(f"{method} {path}: HTTP {problem.code}: {cli.hub_error_text(problem)}") from None
        except (OSError, ValueError) as problem:
            raise HubError(f"{method} {path}: {problem}") from None

    def healthy(self):
        try:
            return bool(self.request("GET", "/api/health", timeout=5).get("ok"))
        except HubError:
            return False

    def create_run(self, arm, run_id, features, specs, options):
        return self.request("POST", "/api/runs", {"arm": arm, "run_id": run_id, "features": features,
                                                  "specs": specs, "options": options})

    def close_run(self, run_id):
        return self.request("POST", f"/api/runs/{run_id}/close").get("metrics") or {}

    def events(self, run_id):
        return self.request("GET", f"/api/runs/{run_id}/events").get("events") or []


def free_port(host):
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def environment_without_secrets():
    """For the hub, the scorer and git: none of them needs the agents'
    credentials, so none of them gets them."""
    return {name: value for name, value in os.environ.items() if name not in drivers.PASSED_BY_NAME}


def wait_for_first_line(path, process, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline and process.poll() is None:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "\n" in text:
            return text.splitlines()[0]
        time.sleep(0.05)
    return ""


def start_hub(hub_dir, host="0.0.0.0", timeout=30):
    """Start hub.py on a free port, bound to `host`, telling agents to reach
    it as host.docker.internal; returns the process and the URL this host
    reaches it by. Its stdout goes to a file rather than a pipe, so a hub
    left running for `finish` never writes into a pipe nobody reads."""
    hub_dir = Path(hub_dir)
    hub_dir.mkdir(parents=True, exist_ok=True)
    port = free_port(host)
    command = [sys.executable, str(HERE / "hub.py"), "--host", host, "--port", str(port),
               "--public-url", f"http://host.docker.internal:{port}", "--log-dir", str(hub_dir)]
    first_line = hub_dir / f"hub-{port}.out"
    with open(first_line, "w") as out, open(hub_dir / "hub.log", "a") as errors:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=errors,
                                   env=environment_without_secrets(), start_new_session=True)
    if not wait_for_first_line(first_line, process, timeout):
        stop_hub(process)
        raise HubError(f"hub.py did not start; see {hub_dir / 'hub.log'}")
    return process, f"http://127.0.0.1:{port}"


def stop_hub(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def stop_hub_pid(pid):
    """Stop the hub prepare left running, making sure the pid is still that
    hub rather than whatever process took the number since."""
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError):
        return
    if b"hub.py" in command:
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), signal.SIGTERM)


def connect_hub(hub_url, hub_dir):
    """The hub named by --hub, or one started here; the process is None for
    a hub this runner did not start."""
    if hub_url:
        client = HubClient(hub_url)
        if not client.healthy():
            raise HubError(f"no hub answers at {hub_url}/api/health")
        return client, None
    process, url = start_hub(hub_dir)
    return HubClient(url), process


# The plan: everything about one pair, kept as JSON so finish can pick it up.

def arm_request(arm_given):
    """What POST /api/runs is sent: a built-in name as given, a directory
    made absolute so a hub with another working directory finds it."""
    candidate = Path(arm_given)
    return str(candidate.resolve()) if (candidate / "arm.json").exists() else arm_given


def arm_needs(arm):
    needs = list(arm.needs) + [n for n in arm.config.get("needs") or [] if n not in arm.needs]
    unknown = [n for n in needs if n not in KNOWN_NEEDS]
    if unknown:
        raise ValueError(f"the arm needs {', '.join(unknown)}, which this runner cannot provision")
    return needs


def arm_name(arm):
    return arm.config.get("name") or arm.name


def new_run_id(name, task_id, features):
    return safe_name(f"{name}-{task_id}-{feature_dir_name(features)}-{secrets.token_hex(3)}")


def new_plan(settings, driver, repo, task_id, features):
    arm = find_arm(settings["arm"])
    layout = arm.layout(features)
    setting = "solo" if len(layout) == 1 else "coop"
    return {
        "repo": repo, "task_id": int(task_id), "features": list(features), "setting": setting,
        "run_name": settings["run_name"], "run_id": new_run_id(arm_name(arm), task_id, features),
        "arm": arm_request(settings["arm"]), "arm_name": arm_name(arm), "arm_dir": str(arm.root),
        "needs": arm_needs(arm), "plugin_entries": list(arm.config.get("plugins") or []),
        "mcp_servers": dict(arm.config.get("mcp_servers") or {}),
        "driver": settings["driver"], "agent_framework": getattr(driver, "framework", settings["driver"]),
        "host_workspace": bool(getattr(driver, "host_workspace", False)),
        "model": settings["model"], "image": image_name(repo, task_id),
        "timeout_s": settings["timeout_s"], "max_turns": settings["max_turns"], "keep": settings["keep"],
        "cooperbench_dir": str(settings["cooperbench_dir"]),
        "run_dir": str(pair_dir(settings["log_dir"], settings["run_name"], setting, repo, task_id, features)),
        "hub_url": "", "hub_pid": None, "hub_run": False, "started_at": now().isoformat(), "error": None,
        "plugins": [], "warnings": [],
        "resources": {"containers": [], "volume": None, "git_repo": None, "workspaces": None},
        "agents": [{"id": a.id, "role": a.role, "features": list(a.features)} for a in layout],
    }


def save_plan(plan):
    write_json(Path(plan["run_dir"]) / "plan.json", plan)


def load_plan(run_dir):
    plan = read_json(Path(run_dir) / "plan.json")
    if not plan:
        raise FileNotFoundError(f"no plan.json in {run_dir}; run prepare first")
    return plan


# Shared infrastructure: the network, the git server, the volume.

def ensure_network():
    if drivers.docker("network", "inspect", NETWORK).returncode != 0:
        created = drivers.docker("network", "create", NETWORK)
        if created.returncode != 0 and drivers.docker("network", "inspect", NETWORK).returncode != 0:
            checked(created, f"creating the network {NETWORK}")


def ensure_git_server():
    """One git daemon for every run, as CooperBench keeps one: each run is a
    directory under /git. Port 9418 is published on 127.0.0.1 too, for arms
    whose tools act on the remote from the hub's host."""
    state = drivers.docker("container", "inspect", "-f", "{{.State.Running}}", GIT_SERVER)
    if state.returncode != 0:
        checked(drivers.docker("run", "-d", "--name", GIT_SERVER, "--network", NETWORK,
                               "-p", f"127.0.0.1:{GIT_PORT}:{GIT_PORT}", "-v", f"{GIT_DATA_VOLUME}:/git",
                               "--restart", "unless-stopped", GIT_IMAGE),
                f"starting the git server (build {GIT_IMAGE} with experiment/setup.sh)")
    elif state.stdout.strip() != "true":
        checked(drivers.docker("start", GIT_SERVER), "starting the git server")
    wait_for_git_daemon()


def wait_for_git_daemon(tries=60):
    """_wait_for_port from CooperBench's git_servers/docker.py: bash's own
    /dev/tcp, since the server image has nothing else to probe with."""
    for _ in range(tries):
        probe = drivers.docker("exec", GIT_SERVER, "bash", "-c", f"exec 3<>/dev/tcp/127.0.0.1/{GIT_PORT}",
                               timeout=10)
        if probe.returncode == 0:
            return
        time.sleep(0.5)
    raise drivers.DockerError(f"the git daemon did not listen on {GIT_PORT}")


def init_repository_script(path):
    """DockerGitServer.create's per-run init (git_servers/docker.py)."""
    quoted = shlex.quote(path)
    return (f"set -e && mkdir -p {quoted} && cd {quoted} && git init -q --bare && "
            "git config receive.denyCurrentBranch ignore && touch git-daemon-export-ok")


def seed_script(url):
    """Print the image's HEAD, then push it as main: the commit every agent
    starts from, which the options must name before the run exists."""
    return f"cd {CONTAINER_REPO} && git rev-parse HEAD && git push -q {shlex.quote(url)} HEAD:refs/heads/main"


def base_commit(output):
    for line in output.splitlines():
        if re.fullmatch(r"[0-9a-f]{40,64}", line.strip()):
            return line.strip()
    raise drivers.DockerError("seeding the shared repository printed no commit")


def create_git_repository(plan):
    ensure_git_server()
    run_id = plan["run_id"]
    plan["resources"]["git_repo"] = f"/git/{run_id}"
    checked(drivers.docker("exec", GIT_SERVER, "sh", "-c", init_repository_script(f"/git/{run_id}/repo.git")),
            "creating the run's repository")
    url = f"git://{GIT_SERVER}:{GIT_PORT}/{run_id}/repo.git"
    seeded = checked(drivers.docker("run", "--rm", "--network", NETWORK, "--entrypoint", "sh", plan["image"],
                                    "-c", seed_script(url)),
                     "seeding the shared repository from the task image")
    return {"git_remote": url, "git_remote_host": f"git://127.0.0.1:{GIT_PORT}/{run_id}/repo.git",
            "base_ref": base_commit(seeded.stdout or "")}


def add_agent_branches(plan):
    repository = f"/git/{plan['run_id']}/repo.git"
    for agent in plan["agents"]:
        checked(drivers.docker("exec", GIT_SERVER, "git", "-C", repository, "branch", agent["id"], "main"),
                f"creating the branch {agent['id']}")


def create_shared_volume(plan):
    """CooperBench's team scratchpad: one named volume per run at
    /workspace/shared. The team arm mirrors its task list there from the
    hub's host, so the volume's host path goes along as shared_dir_host."""
    name = safe_name(f"coordbench-shared-{plan['run_id']}")
    plan["resources"]["volume"] = name
    checked(drivers.docker("volume", "create", name), "creating the shared volume")
    options = {"shared_dir": SHARED_DIR}
    mount = drivers.docker("volume", "inspect", "-f", "{{.Mountpoint}}", name)
    if mount.returncode == 0 and (mount.stdout or "").strip():
        options["shared_dir_host"] = mount.stdout.strip()
    return options


def provision_shared(plan):
    """What the arm's needs ask for, as run options; created before the run
    exists on the hub, since the options go with POST /api/runs."""
    ensure_network()
    options = {}
    if "git_remote" in plan["needs"]:
        options.update(create_git_repository(plan))
    if "shared_volume" in plan["needs"]:
        options.update(create_shared_volume(plan))
    return options


# Extra plugins an arm brings: directories, or git URLs fetched on this host.

def is_url(entry):
    return "://" in entry or entry.startswith("git@")


def fetch_plugin(url, destination):
    if destination.exists():
        return destination
    fetched = subprocess.run(["git", "clone", "-q", "--depth", "1", url, str(destination)],
                             capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
                             env=environment_without_secrets())
    if fetched.returncode != 0:
        raise RuntimeError(f"fetching the plugin {url} failed: {fetched.stderr.strip()[-300:]}")
    return destination


def resolve_plugin(entry, arm_dir, cache_dir, index):
    if is_url(entry):
        name = safe_name(Path(urlsplit(entry).path).stem or f"plugin{index}")
        return fetch_plugin(entry, Path(cache_dir) / f"{index}-{name}")
    path = Path(entry).expanduser()
    path = (path if path.is_absolute() else Path(arm_dir) / path).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"the arm's plugin {entry!r} is not a directory ({path})")
    return path


def resolve_plugins(plan):
    cache = Path(plan["run_dir"]) / "plugins"
    for index, entry in enumerate(plan["plugin_entries"]):
        source = resolve_plugin(entry, plan["arm_dir"], cache, index)
        plan["plugins"].append({"source": str(source),
                                "container": f"{drivers.CONTAINER_PLUGINS}/{index}-{safe_name(source.name)}"})


# One agent's container.

def adopt_agents(plan, answer):
    """The hub's layout is the run's; it must agree with the one the setting
    and the log directory were chosen from."""
    agents = answer.get("agents") or []
    if ("solo" if len(agents) == 1 else "coop") != plan["setting"]:
        raise HubError("the hub laid out the agents differently from this runner's copy of the arm")
    run_dir = Path(plan["run_dir"])
    plan["agents"] = []
    for agent in agents:
        prompt_file = run_dir / f"{safe_name(agent['id'])}_prompt.md"
        prompt_file.write_text(agent.get("prompt") or "", encoding="utf-8")
        plan["agents"].append({"id": agent["id"], "role": agent["role"], "features": list(agent["features"]),
                               "mcp_url": agent["mcp_url"], "rpc_url": agent["rpc_url"],
                               "prompt_file": str(prompt_file),
                               "log_path": str(run_dir / f"{safe_name(agent['id'])}_stream.jsonl")})


def copy_repository_out(plan, agent):
    """For a driver that works from the host: the image's /workspace/repo as
    a host directory, which the container then mounts in its place."""
    workspaces = Path(plan["run_dir"]) / "workspaces"
    plan["resources"]["workspaces"] = str(workspaces)
    workspace = workspaces / safe_name(agent["id"])
    workspaces.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(workspace, ignore_errors=True)
    holder = f"{agent['container']}-seed"
    checked(drivers.docker("create", "--name", holder, "--entrypoint", "sh", plan["image"]),
            "creating a container to copy the repository from")
    try:
        checked(drivers.docker("cp", f"{holder}:{CONTAINER_REPO}", str(workspace)),
                "copying the repository out of the image")
    finally:
        drivers.docker("rm", "-f", holder)
    keep_marker_out_of_git(workspace)
    return str(workspace)


def keep_marker_out_of_git(workspace):
    """The done marker is no part of the work, so `git add -A` must not take it."""
    info = Path(workspace) / ".git" / "info"
    if (Path(workspace) / ".git").is_dir():
        info.mkdir(parents=True, exist_ok=True)
        with open(info / "exclude", "a", encoding="utf-8") as exclude:
            exclude.write(f"\n{drivers.DONE_MARKER}\n")


def container_arguments(plan, agent):
    arguments = ["run", "-d", "--name", agent["container"], "--label", f"coordbench.run={plan['run_id']}",
                 "--network", NETWORK, "--add-host", "host.docker.internal:host-gateway",
                 "-e", f"COORD_MCP_URL={agent['mcp_url']}", "-e", f"COORD_RPC_URL={agent['rpc_url']}"]
    if plan["resources"]["volume"]:
        arguments += ["-v", f"{plan['resources']['volume']}:{SHARED_DIR}"]
    if agent.get("workspace"):
        arguments += ["-v", f"{agent['workspace']}:{CONTAINER_REPO}"]
    return arguments + ["-w", CONTAINER_REPO, "--entrypoint", "sh", plan["image"], "-c", KEEP_ALIVE]


def arm_is_builtin(arm_dir):
    return Path(arm_dir).resolve().parent == builtin_arms_dir().resolve()


def install_coordbench(plan, container):
    """coordbench at /opt/coordbench, so the arm's plugin and cli.py are
    there; an arm from elsewhere and its extra plugins beside it."""
    checked(drivers.docker("exec", container, "mkdir", "-p", "/opt", drivers.CONTAINER_PLUGINS),
            "making /opt in the container")
    checked(drivers.docker("cp", f"{HERE}/.", f"{container}:{drivers.CONTAINER_COORDBENCH}"),
            "copying coordbench into the container")
    if not arm_is_builtin(plan["arm_dir"]):
        inside = drivers.container_arm_dir(plan["arm_dir"])
        checked(drivers.docker("cp", f"{plan['arm_dir']}/.", f"{container}:{inside}"),
                "copying the arm into the container")
    for plugin in plan["plugins"]:
        checked(drivers.docker("cp", f"{plugin['source']}/.", f"{container}:{plugin['container']}"),
                f"copying the plugin {plugin['source']} into the container")


def install_aliases(plan, container):
    """The arm's shell commands on the agent's PATH, when the image has a
    python3 to run cli.py with; the commands read COORD_RPC_URL at run time."""
    if drivers.docker("exec", container, "sh", "-c", "command -v python3").returncode != 0:
        plan["warnings"].append(f"{container} has no python3, so its shell commands are not installed")
        return
    checked(drivers.docker("exec", container, "python3", f"{drivers.CONTAINER_COORDBENCH}/cli.py",
                           "install", "/usr/local/bin"),
            "installing the arm's shell commands (the container may not reach the hub)")


def git_setup_command(agent_id, server_url):
    """build_git_setup_command (_coop/runtime.py at 63b9d44), unchanged: the
    remote `team`, the agent's own branch, pushed, and main as the base."""
    server = shlex.quote(server_url)
    aid = shlex.quote(agent_id)
    branch = shlex.quote(agent_id)
    return (
        f"cd {shlex.quote(CONTAINER_REPO)} && "
        f"git config user.email 'agent@cooperbench.local' && "
        f"git config user.name {aid} && "
        f"(git remote add team {server} 2>/dev/null || git remote set-url team {server}) && "
        f"(git checkout -b {branch} 2>/dev/null || git checkout {branch}) && "
        f"git push -u team {branch} --force && "
        "git push team HEAD:refs/heads/main --force 2>/dev/null || true"
    )


def configure_git(plan, container, agent_id, server_url):
    # The adapter only warns when this fails, and so does the runner.
    result = drivers.docker("exec", "-w", CONTAINER_REPO, container, "bash", "-lc",
                            git_setup_command(agent_id, server_url))
    if result.returncode != 0:
        plan["warnings"].append(f"git setup for {agent_id} exited {result.returncode}")


def install_host_wrappers(plan, agent, hub):
    """The session driver's agent runs on this host, so its commands are
    wrappers here, each carrying the agent's /rpc URL as this host reaches it."""
    url = hub.base_url + urlsplit(agent["rpc_url"]).path
    bin_dir = drivers.session_bin_dir(plan["run_dir"], agent["id"])
    with contextlib.redirect_stdout(sys.stderr):
        code = cli.main(["install", str(bin_dir), "--url", url, "--python", sys.executable])
    if code != 0:
        raise HubError(f"installing {agent['id']}'s commands on this host failed")


def provision_agent(plan, agent, hub, options):
    agent["container"] = safe_name(f"coordbench-{plan['run_id']}-{agent['id']}")
    if plan["host_workspace"]:
        agent["workspace"] = copy_repository_out(plan, agent)
    plan["resources"]["containers"].append(agent["container"])
    checked(drivers.docker(*container_arguments(plan, agent)), f"starting {agent['id']}'s container")
    install_coordbench(plan, agent["container"])
    install_aliases(plan, agent["container"])
    if "git_remote" in plan["needs"]:
        configure_git(plan, agent["container"], agent["id"], options["git_remote"])
    if plan["host_workspace"]:
        install_host_wrappers(plan, agent, hub)


# A pair from start to end.

class PairFailed(RuntimeError):
    """Provisioning a pair failed; it was recorded and torn down."""


def agent_job(plan, agent):
    return drivers.AgentJob(
        run_id=plan["run_id"], agent=agent["id"], role=agent["role"], features=tuple(agent["features"]),
        container=agent["container"], prompt=Path(agent["prompt_file"]).read_text(encoding="utf-8"),
        env={"COORD_MCP_URL": agent["mcp_url"], "COORD_RPC_URL": agent["rpc_url"]},
        arm_dir=plan["arm_dir"], plugins=[p["container"] for p in plan["plugins"]],
        mcp_servers=plan["mcp_servers"], model=plan["model"], timeout_s=plan["timeout_s"],
        max_turns=plan["max_turns"], log_path=agent["log_path"], workspace=agent.get("workspace", ""))


def start_agents(plan, driver):
    """Start every agent; one that fails to start is recorded and the rest
    run on, as CooperBench lets one agent's error leave the other running."""
    handles = {}
    for agent in plan["agents"]:
        try:
            handle = driver.start(agent_job(plan, agent))
        except Exception as problem:
            agent["start_error"] = f"{type(problem).__name__}: {problem}"
            continue
        handles[agent["id"]] = handle
        agent["handle"] = handle if isinstance(handle, dict) else None
    return handles


def prepare_pair(settings, hub, driver, repo, task_id, features):
    """Provision a pair and start its agents; returns the plan and handles."""
    plan = new_plan(settings, driver, repo, task_id, features)
    plan["hub_url"] = hub.base_url
    Path(plan["run_dir"]).mkdir(parents=True, exist_ok=True)
    try:
        specs = read_specs(Path(settings["cooperbench_dir"]) / "dataset", repo, task_id, features)
        resolve_plugins(plan)
        options = {**settings["options"], **provision_shared(plan)}
        answer = hub.create_run(plan["arm"], plan["run_id"], plan["features"], specs, options)
        plan["hub_run"] = True
        adopt_agents(plan, answer)
        if "git_remote" in plan["needs"]:
            add_agent_branches(plan)
        for agent in plan["agents"]:
            provision_agent(plan, agent, hub, options)
    except Exception as problem:
        record_failure(plan, hub, problem)
        raise PairFailed(plan["error"]) from problem
    except BaseException:
        teardown(plan)
        raise
    handles = start_agents(plan, driver)
    save_plan(plan)
    return plan, handles


def wait_agents(plan, driver, handles):
    outcomes = {}
    for agent in plan["agents"]:
        if agent["id"] not in handles:
            outcomes[agent["id"]] = drivers.AgentOutcome(error=agent.get("start_error") or "never started")
            continue
        try:
            outcomes[agent["id"]] = driver.wait(handles[agent["id"]])
        except Exception as problem:
            outcomes[agent["id"]] = drivers.AgentOutcome(error=f"{type(problem).__name__}: {problem}")
    return outcomes


def close_run(plan, hub):
    """The run's metrics and events, final once it is closed; a hub that has
    gone away leaves them empty and says so."""
    if not plan["hub_run"]:
        return {}, []
    try:
        metrics = hub.close_run(plan["run_id"])
        return metrics, hub.events(plan["run_id"])
    except HubError as problem:
        plan["warnings"].append(f"closing the run on the hub failed: {problem}")
        return {}, []


def finish_pair(plan, driver, handles, hub):
    """Wait, record, tear down, score; returns the pair's verdict. The
    containers go even when the wait is interrupted."""
    try:
        outcomes = wait_agents(plan, driver, handles)
        ended = now()
        metrics, events = close_run(plan, hub)
        write_outputs(plan, outcomes, metrics, events, ended)
    finally:
        teardown(plan)
    return verdict(outcomes, score(plan))


def record_failure(plan, hub, problem):
    plan["error"] = f"{type(problem).__name__}: {problem}"
    outcomes = {a["id"]: drivers.AgentOutcome(error=plan["error"]) for a in plan["agents"]}
    metrics, events = close_run(plan, hub)
    write_outputs(plan, outcomes, metrics, events, now())
    teardown(plan)
    save_plan(plan)


def verdict(outcomes, evaluation):
    if any(o.status == "Error" for o in outcomes.values()) or evaluation.get("error"):
        return "error"
    return "pass" if evaluation.get("both_passed") else "fail"


# What a pair leaves behind.

def patch_files(plan, outcomes):
    """Patches named as CooperBench's evaluate.py reads them: solo.patch, or
    agent<feature>.patch for the agent given that feature."""
    if plan["setting"] == "solo":
        return {"solo.patch": outcomes[plan["agents"][0]["id"]].patch}
    files = {}
    for agent in plan["agents"]:
        patch = outcomes[agent["id"]].patch
        for feature in agent["features"]:
            files[f"agent{feature}.patch"] = patch
        if not agent["features"]:
            files[f"{safe_name(agent['id'])}.patch"] = patch
    return files


def agent_record(agent, outcome):
    features = agent["features"]
    return {"feature_id": features[0] if len(features) == 1 else features, "role": agent["role"],
            "status": outcome.status, "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens, "cache_read_tokens": outcome.cache_read_tokens,
            "cache_write_tokens": outcome.cache_write_tokens, "steps": outcome.steps, "error": outcome.error}


def result_record(plan, outcomes, metrics, ended):
    started = datetime.fromisoformat(plan["started_at"])
    record = {
        "repo": plan["repo"], "task_id": plan["task_id"], "features": plan["features"],
        "setting": plan["setting"], "run_id": plan["run_id"], "run_name": plan["run_name"],
        "agent_framework": plan["agent_framework"], "model": plan["model"], "arm": plan["arm_name"],
        "driver": plan["driver"], "started_at": plan["started_at"], "ended_at": ended.isoformat(),
        "duration_seconds": (ended - started).total_seconds(),
        "agents": {a["id"]: agent_record(a, outcomes[a["id"]]) for a in plan["agents"]},
        "metrics": metrics,
    }
    if plan["error"]:
        record["error"] = plan["error"]
    return record


def write_outputs(plan, outcomes, metrics, events, ended):
    run_dir = Path(plan["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, text in patch_files(plan, outcomes).items():
        (run_dir / name).write_text(text, encoding="utf-8")
    write_json(run_dir / "result.json", result_record(plan, outcomes, metrics, ended))
    write_json(run_dir / "metrics.json", metrics)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e, default=str) + "\n" for e in events),
                                          encoding="utf-8")
    for warning in plan["warnings"]:
        say(f"{plan['run_id']}: {warning}")


def teardown(plan):
    """Remove the run's containers, volume, repository and workspaces; the
    network and the git server are shared, as in CooperBench, and stay."""
    resources = plan["resources"]
    if plan["keep"]:
        say(f"kept for inspection: {json.dumps(resources)}")
        return
    if resources["containers"]:
        drivers.docker("rm", "-f", *resources["containers"])
    if resources["volume"]:
        drivers.docker("volume", "rm", "-f", resources["volume"])
    if resources["git_repo"]:
        drivers.docker("exec", GIT_SERVER, "rm", "-rf", resources["git_repo"])
    if resources["workspaces"]:
        shutil.rmtree(resources["workspaces"], ignore_errors=True)
    plan["resources"] = {"containers": [], "volume": None, "git_repo": None, "workspaces": None}


# Scoring, with CooperBench's own evaluation.

EVALUATION_SCRIPT = """
import json, sys
source, request_file, answer_file = sys.argv[1:4]
sys.path.insert(0, source)
from cooperbench.eval.sandbox import test_merged, test_solo
request = json.load(open(request_file))
test = test_solo if request.pop("kind") == "solo" else test_merged
with open(answer_file, "w") as answer:
    json.dump(test(**request), answer, default=str)
"""


def cooperbench_test(kind, arguments, cooperbench_dir):
    """CooperBench's test_solo or test_merged (eval/sandbox.py), with its
    src on sys.path. It runs in CooperBench's own virtualenv when there is
    one, since the evaluation needs packages (docker, rich) this module
    does without. Tests replace this one function."""
    cooperbench_dir = Path(cooperbench_dir)
    python = cooperbench_dir / ".venv" / "bin" / "python"
    with tempfile.TemporaryDirectory(prefix="coordbench-eval-") as scratch:
        request, answer = Path(scratch) / "request.json", Path(scratch) / "answer.json"
        request.write_text(json.dumps({"kind": kind, **arguments}), encoding="utf-8")
        done = subprocess.run([str(python if python.exists() else sys.executable), "-c", EVALUATION_SCRIPT,
                               str(cooperbench_dir / "src"), str(request), str(answer)],
                              cwd=cooperbench_dir, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=SCORING_TIMEOUT_SECONDS, env=environment_without_secrets())
        if done.returncode != 0 or not answer.exists():
            raise RuntimeError(f"CooperBench's evaluation failed: {done.stderr.strip()[-500:]}")
        return json.loads(answer.read_text(encoding="utf-8"))


def read_patch(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def run_cooperbench_tests(plan):
    """The sandbox call _evaluate_single (eval/evaluate.py) makes for this
    pair; a failure to score is the pair's error, never a crash."""
    if len(plan["features"]) != 2:
        return {"error": "CooperBench scores pairs of features only"}
    run_dir, (first, second) = Path(plan["run_dir"]), plan["features"]
    common = {"repo_name": plan["repo"], "task_id": plan["task_id"], "feature1_id": first,
              "feature2_id": second, "backend": "docker",
              "dataset_dir": str(Path(plan["cooperbench_dir"]) / "dataset")}
    try:
        if plan["setting"] == "solo":
            return cooperbench_test("solo", {**common, "patch": read_patch(run_dir / "solo.patch")},
                                    plan["cooperbench_dir"])
        return cooperbench_test("merged", {**common, "patch1": read_patch(run_dir / f"agent{first}.patch"),
                                           "patch2": read_patch(run_dir / f"agent{second}.patch")},
                                plan["cooperbench_dir"])
    except Exception as problem:
        return {"error": f"{type(problem).__name__}: {problem}"}


def evaluation_record(plan, result):
    """eval.json with the fields _evaluate_single writes, in its order."""
    record = {"repo": plan["repo"], "task_id": plan["task_id"], "features": plan["features"],
              "setting": plan["setting"]}
    if plan["setting"] == "solo":
        record["merge"] = None
    else:
        record["apply_status"] = result.get("apply_status")
        record["merge"] = result.get("merge", {})
    record.update(feature1=result.get("feature1", {}), feature2=result.get("feature2", {}),
                  both_passed=result.get("both_passed", False), error=result.get("error"),
                  evaluated_at=now().isoformat())
    return record


def score(plan):
    if plan["error"]:
        return {"error": plan["error"]}
    record = evaluation_record(plan, run_cooperbench_tests(plan))
    write_json(Path(plan["run_dir"]) / "eval.json", record)
    return record


# The command line.

def parse_features(text):
    features = sorted(int(word) for word in text.replace(" ", "").split(",") if word)
    if not features:
        raise argparse.ArgumentTypeError("name at least one feature, as 1,6")
    return features


def parse_option(text):
    key, equals, value = text.partition("=")
    if not equals or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value


def settings_from(args, default_driver):
    cooperbench_dir = Path(args.cooperbench_dir) if args.cooperbench_dir else cooperbench_home()
    return {
        "arm": args.arm, "driver": args.driver or default_driver, "model": args.model,
        "run_name": args.name or f"coordbench-{arm_name(find_arm(args.arm))}",
        "log_dir": Path(args.log_dir) if args.log_dir else cooperbench_dir / "logs",
        "cooperbench_dir": cooperbench_dir, "hub": args.hub, "timeout_s": args.timeout_min * 60,
        "max_turns": args.max_turns, "keep": args.keep, "force": args.force, "options": dict(args.option),
    }


def hub_dir(settings):
    return Path(settings["log_dir"]) / settings["run_name"] / "hub"


def plan_summary(plan, handles):
    agents = []
    for agent in plan["agents"]:
        handle = handles.get(agent["id"])
        prompt = handle.get("prompt_file") if isinstance(handle, dict) else None
        agents.append({"id": agent["id"], "prompt_file": prompt or agent.get("prompt_file")})
    return {"run_dir": plan["run_dir"], "agents": agents}


def run_one(settings, hub, driver, repo, task_id, features):
    """One pair, unless it is finished; returns its verdict."""
    plan = new_plan(settings, driver, repo, task_id, features)
    run_dir = Path(plan["run_dir"])
    if pair_finished(run_dir) and not settings["force"]:
        if (run_dir / "eval.json").exists():
            return "skipped"
        return verdict({}, score(plan))
    try:
        plan, handles = prepare_pair(settings, hub, driver, repo, task_id, features)
    except PairFailed:
        return "error"
    if getattr(driver, "resumable", False):
        print(json.dumps(plan_summary(plan, handles)), flush=True)
    return finish_pair(plan, driver, handles, hub)


def warn_about_credentials(driver):
    if isinstance(driver, drivers.ClaudeCliDriver) and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        say("CLAUDE_CODE_OAUTH_TOKEN is not set, so the agents cannot sign in")


def command_run(args):
    settings = settings_from(args, "claude-cli")
    dataset = Path(settings["cooperbench_dir"]) / "dataset"
    pairs = [args.features] if args.features else subset_pairs(dataset, args.subset, args.repo, args.task)
    driver = drivers.load_driver(settings["driver"])
    warn_about_credentials(driver)
    hub, process = connect_hub(settings["hub"], hub_dir(settings))
    failures = 0
    try:
        for features in pairs:
            outcome = run_one(settings, hub, driver, args.repo, args.task, sorted(features))
            failures += outcome == "error"
            say(f"{args.repo}/{args.task} {feature_dir_name(features)}: {outcome}")
    finally:
        if process is not None:
            stop_hub(process)
    return 1 if failures else 0


def command_prepare(args):
    settings = settings_from(args, "session")
    driver = drivers.load_driver(settings["driver"])
    if not getattr(driver, "resumable", False):
        raise SystemExit(f"prepare needs a driver whose wait can run in another process, "
                         f"and {settings['driver']} is not one")
    hub, process = connect_hub(settings["hub"], hub_dir(settings))
    try:
        plan, handles = prepare_pair(settings, hub, driver, args.repo, args.task, args.features)
    except PairFailed as failure:
        if process is not None:
            stop_hub(process)
        say(f"prepare failed: {failure}")
        return 1
    if process is not None:
        plan["hub_pid"] = process.pid
        save_plan(plan)
    print(json.dumps(plan_summary(plan, handles)), flush=True)
    return 0


def command_finish(args):
    plan = load_plan(args.run_dir)
    driver = drivers.load_driver(plan["driver"])
    handles = {a["id"]: a["handle"] for a in plan["agents"] if a.get("handle") is not None}
    try:
        outcome = finish_pair(plan, driver, handles, HubClient(plan["hub_url"]))
    finally:
        if plan.get("hub_pid"):
            stop_hub_pid(plan["hub_pid"])
    save_plan(plan)
    print(json.dumps({"run_dir": plan["run_dir"], "verdict": outcome}), flush=True)
    return 0 if outcome != "error" else 1


def add_pair_arguments(parser, subset):
    parser.add_argument("--arm", required=True, help="a built-in arm's name or an arm's directory")
    parser.add_argument("--repo", required=True, help="the dataset's repository, as pallets_click_task")
    parser.add_argument("--task", required=True, type=int, help="the task's number, as 2068")
    pairs = parser.add_mutually_exclusive_group(required=True)
    pairs.add_argument("--features", type=parse_features, help="the features, as 1,6")
    if subset:
        pairs.add_argument("--subset", help="every pair the subset names for this task, as flash")
    parser.add_argument("--driver", help="claude-cli, session, or path/to/driver.py:Class")
    parser.add_argument("--model", default=os.environ.get("MODEL") or "claude-sonnet-5-5",
                        help="the agents' model (default $MODEL or claude-sonnet-5-5, as run_arms.sh)")
    parser.add_argument("--name", help="the run's name in the logs (default coordbench-<arm>)")
    parser.add_argument("--log-dir", help="the logs' root (default <cooperbench>/logs)")
    parser.add_argument("--cooperbench-dir",
                        help="CooperBench's checkout (default $COOPERBENCH_DIR or ~/cooperbench)")
    parser.add_argument("--hub", help="a running hub's URL as this host reaches it; else one is started")
    parser.add_argument("--timeout-min", type=float, default=120, help="each agent's time limit")
    parser.add_argument("--max-turns", type=int, help="each agent's turn limit (default none)")
    parser.add_argument("--option", action="append", type=parse_option, default=[], metavar="KEY=VALUE",
                        help="a run option for the arm, the value read as JSON when it parses")
    parser.add_argument("--keep", action="store_true", help="leave containers, volume and repository in place")
    parser.add_argument("--force", action="store_true", help="run a pair again even when it is finished")


def parser():
    top = argparse.ArgumentParser(prog="run.py", description="Run CooperBench task pairs under a coordbench arm.")
    commands = top.add_subparsers(dest="command", required=True)
    running = commands.add_parser("run", help="run pairs one after another and score them")
    add_pair_arguments(running, subset=True)
    running.set_defaults(func=command_run)
    preparing = commands.add_parser("prepare", help="provision one pair for the session driver")
    add_pair_arguments(preparing, subset=False)
    preparing.set_defaults(func=command_prepare)
    finishing = commands.add_parser("finish", help="wait for a prepared pair, score it and tear it down")
    finishing.add_argument("--run-dir", required=True)
    finishing.set_defaults(func=command_finish)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (HubError, FileNotFoundError, ValueError) as problem:
        say(str(problem))
        return 2


if __name__ == "__main__":
    sys.exit(main())
