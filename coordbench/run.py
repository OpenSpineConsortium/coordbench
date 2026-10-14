"""The runner: task pairs under one arm with one driver, scored as CooperBench scores them.

    python3 run.py run --arm coop --repo pallets_click_task --task 2068 --features 1,6
        [--driver claude-cli|session|path/to/driver.py:Class] [--model M] [--name RUN]
        [--agent-model AGENT=MODEL ...] [--log-dir DIR] [--hub URL] [--arm-dir DIR ...]
        [--timeout-min 120] [--max-turns N] [--disallowed-tools WebFetch,WebSearch]
        [--keep] [--force] [--option KEY=VALUE ...] [--cooperbench-dir DIR] [--dataset-dir DIR]
        [--concurrency N] [--debug]
    python3 run.py run ... --subset flash     instead of --features: every flash pair, of every
                                              task, or of the tasks --repo and --task name
    python3 run.py prepare ... --features 1,6  provision one pair for the session driver
    python3 run.py finish --run-dir DIR [--debug]  wait for it, score it, write its logs, tear it down
    python3 run.py rescore --run-dir DIR [--force] [--cooperbench-dir DIR] [--dataset-dir DIR]
                                              score finished pairs again from their patches

For each pair the runner reads the features' specs from CooperBench's
dataset, provisions what the arm needs (a shared git remote, a shared
volume), asks the hub for a run (POST /api/runs), starts one container per
agent from the task's image with coordbench inside it, hands each agent to
the driver, and once they stop collects their patches, closes the run on
the hub, takes its metrics and events, releases it (DELETE /api/runs/{run},
so the hub forgets it) and scores the patches with CooperBench's own
evaluation.

An arm's extra plugins (arm.json "plugins") are directories, copied into
each agent's container and loaded with --plugin-dir; git URLs, cloned on
this host and copied the same way; or .zip URLs, which Claude Code is
handed as --plugin-url and the runner copies nothing for.

The hub is the one named by --hub, or hub.py started here on a free port.
A hub started here may load the built-in arms, the arm when --arm names a
directory, and the arms under each --arm-dir; and it wants a bearer token
made for it alone (secrets.token_hex), which it gets in its environment
as COORD_ADMIN_TOKEN and the runner sends with every management request.
A hub named by --hub gets the runner's own COORD_ADMIN_TOKEN, when that
is set. Agents get neither: the token is what keeps them from creating or
closing runs. The runner asks the hub for a token per agent as well
("agent_tokens" in POST /api/runs), which that agent's /mcp and /rpc want
from then on as a bearer token, so no agent can speak as another and
nothing else on the network can speak at all; a hub that predates the
option gives none, and the run stays open as it was.

The logs take CooperBench's layout, so bench/cooperbench/report.py and
`cooperbench eval` read them as they read the authors' runs:

    <log_dir>/<run_name>/config.json  summary.json
    <log_dir>/<run_name>/<setting>/<repo>/<task>/f<a>_f<b>/
        result.json  eval.json  solo.patch or agent<feature>.patch
        agent<feature>_traj.json or solo_traj.json
        <agent>_stream.jsonl  <agent>_session.jsonl  <agent>_prompt.md
        conversation.json  <agent>_sent.jsonl  conversation_store.json
        events.jsonl  metrics.json  plan.json  and the arm's own artifacts

config.json (runconfig.py) is written when a run starts and summary.json
when it ends, with the keys and formulas of CooperBench's runner/core.py
and coordbench's own beside them: which CooperBench commit and which
dataset tree ran, the images, the limits, the driver's departures from
CooperBench's adapter, and what Claude Code versions, models, tools and
MCP servers the agents reported. finish refreshes summary.json from
every pair on disk, since the session driver runs a pair at a time.

The setting is CooperBench's: "solo" when the arm lays out one agent,
"team" for the team arm (as team.py, under team/), "coop" otherwise
(coop and coop --git both write coop), and an arm.json may name its own
with "setting". eval.json says "coop" for every merged evaluation, as
CooperBench's evaluate.py writes it. In the solo setting the agent's
files take CooperBench's agent id, solo_stream.jsonl and the rest,
whatever the arm calls the agent on the hub.

result.json holds what CooperBench's coop.py, team.py and solo.py write,
in their order: per agent the status, cost, steps, the four token counts,
patch_lines and error, under "agents" (team adds team_role and
lead_agent) or, for solo, under the singular "agent"; total_cost,
total_steps, messages_sent and log_dir. error is CooperBench's: the
message of an exception that stopped the agent, null otherwise, even for
an Error the result event reported; coordbench's own account of how an
agent that did not submit ended is end_reason. Then coordbench's keys:
the arm, the driver, agents_seconds (the agents' phase from the first
start to the last end; duration_seconds runs from the pair's plan to the
agents' end, which covers the run's creation on the hub and the
provisioning and leaves out close, teardown and scoring, where
CooperBench's covers each agent's container start, install, run and
cleanup and the git server, and leaves out the evaluation; the
architectures differ, so neither stretch can be cut as the other is,
and the timings give each phase on its own), the isolation ("advisory"
under the session driver, whose subagent works on the host), the options
and limits, the provenance and the image, the hub's metrics and the
timings. Each agent also records the model it was asked for and the one
it ran (model_differs flags the difference), Claude Code's version, and
the tools and MCP servers its stream's init event listed.

A driver that measures no usage (drivers.py, measures_usage, or an
outcome's own `measured`) leaves cost, steps and the token counts null
rather than zero, and the totals null when no agent was measured, so a
report shows them as unknown instead of counting nothing. cost_source
says where a cost came from: "claude code" for claude-cli's own
total_cost_usd, "estimated from tokens" for the session driver, which
sums the tokens of the subagent's transcript and prices them. A
transcript holds the final output count only where a message's line
carries its stop_reason; when a message lacks one, output_tokens and the
cost stay null and output_tokens_lower_bound and cost_lower_bound say at
least how much. Each agent
also gets duration_ms and api_duration_ms, Claude Code's own wall and API
time from its result event, null when the driver cannot know them.

write_outputs also writes what the hub handed back at close (the arm's
artifacts: the team arm's task_log.json and tasks.json, a git arm's
git_log.txt), the conversation files conversation.py derives from the
message events, and each agent's trajectory file from its session
transcript, parsed as CooperBench's parse_session_jsonl parses it.
score() adds the merge cascade (cascade.py) under eval.json's "cascade",
beside CooperBench's keys and never in their place.

result.json also holds "timings", one record per phase of the pair with
its start and end (ISO 8601) and its seconds, so a slow run can be laid
at the harness's door or the agents': "hub" (starting or connecting to
the hub), "run_create" (POST /api/runs), "git_remote", "shared_volume",
"provision" (one per agent, its container start through its wrappers),
"agent" (one per agent, from the driver's start to its wait's return;
for the session driver, from prepare's start to the marker finish sees),
"close" (close and release on the hub), "score" (CooperBench's
evaluation), "cascade" (the merge cascade, when cascade.py is there) and
"teardown". Only the phases that happened are there, and run and finish
end with one line summing them up (timings_summary). timeline.py prints
the same breakdown for a run directory.

A pair whose result.json shows no agent in Error is finished and
skipped, unless --force; one that lacks only its eval.json is scored
again without running the agents, as CooperBench's runner and evaluator
behave. A team pair finished before the team setting had its own
directory is found where it was, under coop/, and skipped there. A pair
run again in its directory has the earlier attempt's files packed into
previous-<time>.tar.gz first (set_aside_earlier_attempt), so nothing the
new attempt does not rewrite is read as its own. rescore is `cooperbench
eval` for these logs: it scores the pairs under a directory from their
patches and plan.json (those without eval.json, or whose merged eval.json
has no cascade; every one with --force), writes eval.json and the
trajectory export, and starts no hub, container or agent, whatever state
a pair is in.

--subset alone runs every pair of the subset, task by task as
CooperBench's discover_tasks orders them; --repo and --task narrow it.
--concurrency runs that many pairs at once on one hub, as CooperBench's
runner does with its thread pool (its default is 20; here 1), each pair
scored as it ends. The shared network and git server are made under a
lock, a pair that raises is recorded as an error and the others go on,
and --debug is refused with more than one pair at a time, since trace.py
has one stream per process and every pair's trace.jsonl would hold all
of them.

prepare and finish split a run in two for the session driver, so the
orchestrating Claude Code session can spawn its subagents in between:
prepare provisions the pair, writes each agent's prompt and prints
{"run_dir", "agents": [{"id", "prompt_file", "model"}]}, the model being
the one to run that agent's subagent with, which nothing else enforces;
finish waits for every agent's marker and does the rest. prepare keeps
what finish needs in <run_dir>/plan.json, and a hub prepare started
stays up until finish.
That hub's token is in no file, so finish, a process of its own, reads it
back from the hub's environment (/proc/<pid>/environ, Linux's, which only
the hub's own user can read).

The timings are also events on trace.py's one stream, as they happen: a
"phase" event when a phase begins (phase, agent) and a "phase_done" when
it ends (duration_ms, ok, phase, agent), so a deployment watching the
runner sees each phase live while result.json keeps the records. A
deployment (the App, an Actions job) attaches its sink with
run.listen(callable), which is trace.listen for everything the runner
emits. --debug on run, prepare and finish (trace.set_level("debug"); the
environment variable COORD_TRACE=debug does the same) adds what the
playground needs to find where the time goes, never on by default: every
docker command (drivers.docker: "docker", with its subcommand, the
argument it acted on, its exit code, duration and output bytes, never its
command line), every hub call ("hub_call": method, path, status,
duration_ms), every file written under the run directory ("wrote": its
name and bytes, never its text) and the session driver's every look for
its marker ("marker_poll"); and the runner writes every event of the
process to <run_dir>/trace.jsonl (open_debug_sink). In prod no listener
is registered for the file, and each of those costs one comparison, with
a clock read before a docker command or a hub call. In debug the agents'
side traces too (agent_trace_environment): every agent's container starts
with COORD_TRACE=debug, which each command run in it inherits, and the
session driver's wrappers on this host export it, so the shell commands
and the stdio bridge an agent runs (cli.py, bridge.py) write their timings
to the agent's own stderr, beside what each command prints.

Every docker command goes through drivers.docker, so tests swap in a fake.
The subscription token reaches agents by name only (drivers.PASSED_BY_NAME)
and the hub's token reaches no agent; neither is ever on a command line,
in a log or in a file this module writes. An agent's own hub token
(COORD_AGENT_TOKEN) reaches its container by name too, in the docker
client's environment for the one `docker run` that starts the container
(drivers.docker's extra_env), from which every exec inherits it; for the
session driver, whose agent runs on this host, it is a file named token
beside the agent's wrappers, which read it when they run. The plan and
that file are the two places it is written, and both are the owner's
alone (mode 0o600).
"""

import argparse
import contextlib
import gzip
import importlib
import importlib.util
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
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cli  # noqa: E402
import drivers  # noqa: E402
import runconfig  # noqa: E402
import trace  # noqa: E402
from arm import NEED_OPTIONS, find_arm  # noqa: E402
from drivers import CONTAINER_REPO, USAGE_FIELDS, arm_is_builtin, checked, safe_name, write_text  # noqa: E402

NETWORK = "coordbench"
# Named apart from CooperBench's own cooperbench-git, which publishes no
# port, so the two never fight over a name and the hub's host can reach
# this one on 127.0.0.1.
GIT_SERVER = "coordbench-git"
GIT_IMAGE = "cooperbench-git-server:local"
GIT_DATA_VOLUME = "coordbench-git-data"
GIT_PORT = 9418
SHARED_DIR = "/workspace/shared"
# Where the shared volume's last state is kept in the pair directory.
SHARED_COPY = "shared"
# The run options the runner sets for each need it provisions: where the
# thing is for the agents (git_remote, shared_dir), where it is for the
# hub's host (git_remote_host, shared_dir_host), and the commit every agent
# starts from. The hub insists on the first of each (arm.NEED_OPTIONS);
# the arms rely on the rest, so a run gets all of them or does not start.
PROVIDED_OPTIONS = {"git_remote": ("git_remote", "git_remote_host", "base_ref"),
                    "shared_volume": ("shared_dir", "shared_dir_host")}
KNOWN_NEEDS = tuple(PROVIDED_OPTIONS)
# CooperBench's git block tells every agent this is its git user.email.
GIT_EMAIL = "agent@cooperbench.local"
# Starts each line the read-back of the git set-up prints, so whatever a
# login shell's profile prints first is not taken for an answer.
GIT_READ_MARK = "coordbench-promise:"
# sleep infinity is GNU; busybox's sleep may refuse it, so fall back to a loop.
KEEP_ALIVE = "sleep infinity 2>/dev/null || while :; do sleep 86400; done"
SCORING_TIMEOUT_SECONDS = 3 * 3600
# The session driver's agent token, in its bin directory on this host.
TOKEN_FILE = "token"
# CooperBench's settings, by the directory its runner writes each under.
SETTINGS = ("solo", "coop", "team")
# The shared network and git server are made once for every pair; with
# --concurrency two pairs may both find them missing, so one makes them.
SHARED_INFRA_LOCK = threading.Lock()
# contextlib.redirect_stdout swaps the process's stdout, so what is
# printed for the orchestrating session must not cross another thread's
# redirect (install_host_wrappers).
STDOUT_LOCK = threading.Lock()

# The hub is local, so a proxy set for outbound traffic must not carry it.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def say(text):
    print(f"coordbench run: {text}", file=sys.stderr, flush=True)


def now():
    return datetime.now()


def utc_offset_seconds():
    """This host's offset from UTC now, in seconds. result.json's times are
    naive local times, as CooperBench's runner writes them, so the offset
    goes beside them for a reader on a host in another zone."""
    return int(datetime.now().astimezone().utcoffset().total_seconds())


# Timings: when each phase of a pair began and ended, kept on the plan
# (and before the plan exists, for the hub, on the settings) and written to
# result.json, so a reader can tell the harness's time from the agents'.
# Each is also two events on trace's stream, "phase" as it begins and
# "phase_done" as it ends, for whoever listens (listen) while it happens.

# The phases in the order the summary names them; "agent" reads "agents".
PHASES = ("hub", "run_create", "git_remote", "shared_volume", "provision", "agent", "close", "score", "cascade",
          "teardown")


def listen(listener):
    """Where a deployment (the App, an Actions job) attaches its sink: the
    callable gets every event the runner emits, the phases in prod and
    the rest in debug, as trace.py describes them, and is given back, so
    it works as a decorator; unlisten takes it off. It is trace.listen,
    named here so a deployment imports run alone."""
    return trace.listen(listener)


def unlisten(listener):
    trace.unlisten(listener)


def record_timing(holder, phase, agent, started, ended, ok=True):
    """One phase's record under holder["timings"]: the phase, the agent it
    was for (None for the run's own phases), when it started and ended as
    ISO 8601, and its seconds, which is what the summary and timeline.py
    read. The record is also the "phase_done" event, with the seconds as
    duration_ms and ok False for a phase that failed, so every record
    result.json gets was seen live by the listeners."""
    record = {"phase": phase, "agent": agent, "started": started.isoformat(), "ended": ended.isoformat(),
              "seconds": (ended - started).total_seconds()}
    holder.setdefault("timings", []).append(record)
    trace.event("phase_done", duration_ms=round(record["seconds"] * 1000, 3), ok=ok, phase=phase, agent=agent)


@contextlib.contextmanager
def timed(holder, phase, agent=None):
    """Record the phase the block is, however the block ends: a phase that
    failed still took its time, and the record says where the pair was.
    The block's start is the "phase" event, its end the "phase_done" the
    record emits, the pair trace.span would emit."""
    started = now()
    trace.event("phase", phase=phase, agent=agent)
    ok = False
    try:
        yield
        ok = True
    finally:
        record_timing(holder, phase, agent, started, now(), ok)


def phase_records(plan, phase):
    return [record for record in plan.get("timings") or [] if record["phase"] == phase]


def span_seconds(records):
    """The wall time a phase took over all its records, from the first
    start to the last end: the agents run at once, so their phase is as
    long as the slowest of them, not the sum."""
    started = min(datetime.fromisoformat(record["started"]) for record in records)
    ended = max(datetime.fromisoformat(record["ended"]) for record in records)
    return (ended - started).total_seconds()


def seconds_text(seconds):
    return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"


def timings_summary(plan):
    """One line over the phases that happened, as "timings: provision 42 s,
    agents 1040 s (agent1 1030 s, agent2 980 s), score 95 s, teardown 6 s";
    the agents are told apart, since one slow agent is the run's length."""
    parts = []
    for phase in PHASES:
        records = phase_records(plan, phase)
        if not records:
            continue
        part = f"{'agents' if phase == 'agent' else phase} {seconds_text(span_seconds(records))}"
        if phase == "agent" and len(records) > 1:
            part += " (" + ", ".join(f"{r['agent']} {seconds_text(r['seconds'])}" for r in records) + ")"
        parts.append(part)
    return "timings: " + (", ".join(parts) if parts else "nothing recorded")


# The dataset.

def cooperbench_home():
    """CooperBench's checkout, absolute: the scorer runs with it as its
    working directory and is handed paths under it, and docker reads a
    relative path in `-v` as a volume name."""
    return Path(os.environ.get("COOPERBENCH_DIR") or Path.home() / "cooperbench").resolve()


def read_spec(dataset_dir, repo, task_id, feature):
    path = Path(dataset_dir) / repo / f"task{task_id}" / f"feature{feature}" / "feature.md"
    if not path.is_file():
        raise FileNotFoundError(f"no spec for feature {feature} of {repo}/{task_id} at {path}")
    return path.read_text(encoding="utf-8")


def read_specs(dataset_dir, repo, task_id, features):
    return {feature: read_spec(dataset_dir, repo, task_id, feature) for feature in features}


def subset_entries(dataset_dir, subset):
    """The subset's tasks (dataset/subsets/<name>.json), keyed by repo and
    task, each with the feature sets it names: "pairs" in the pair subsets,
    "groups" in team3_all.json, None when it names none (every pair)."""
    path = Path(dataset_dir) / "subsets" / f"{subset}.json"
    if not path.is_file():
        raise ValueError(f"no subset {subset!r} at {path}")
    entries = json.loads(path.read_text(encoding="utf-8"))["tasks"]
    return {(entry["repo"], int(entry["task_id"])): entry.get("pairs", entry.get("groups")) for entry in entries}


def task_features(task_dir):
    return sorted(int(d.name[len("feature"):]) for d in Path(task_dir).iterdir()
                  if d.is_dir() and d.name.startswith("feature") and d.name[len("feature"):].isdigit())


def subset_tasks(dataset_dir, subset, repo=None, task_id=None):
    """Every (repo, task, features) of the subset, as CooperBench's
    discover_tasks (runner/tasks.py at 63b9d44) lists them: the dataset's
    repositories and tasks in sorted order, those the subset names, each
    of its feature sets as written when both features are in the task
    (every pair of the task's features when it names none), narrowed to
    one repository or task when those are given."""
    named = subset_entries(dataset_dir, subset)
    found = []
    for repo_dir in sorted(p for p in Path(dataset_dir).iterdir() if p.is_dir()):
        if repo and repo != repo_dir.name:
            continue
        for task_dir in sorted(p for p in repo_dir.iterdir()
                               if p.is_dir() and p.name.startswith("task") and p.name[len("task"):].isdigit()):
            number = int(task_dir.name[len("task"):])
            if (task_id and int(task_id) != number) or (repo_dir.name, number) not in named:
                continue
            features = task_features(task_dir)
            sets = named[(repo_dir.name, number)]
            if sets is None:  # named without sets: every pair, as discover_tasks gives them
                sets = [[a, b] for i, a in enumerate(features) for b in features[i + 1:]]
            found += [(repo_dir.name, number, [int(f) for f in s]) for s in sets
                      if all(int(f) in features for f in s)]
    return found


def subset_pairs(dataset_dir, subset, repo, task_id):
    """Every feature set a subset names for one task, each sorted."""
    pairs = [sorted(features) for _, _, features in subset_tasks(dataset_dir, subset, repo, task_id)]
    if not pairs:
        raise ValueError(f"the {subset} subset has no task {repo}/{task_id}")
    return pairs


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
    write_text(path, json.dumps(value, indent=2, default=str) + "\n")


def write_private_text(path, text):
    """A file that holds a credential, for its owner alone: the mode is
    set before the text goes in, so no other reader ever sees it."""
    path = Path(path)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    write_text(path, text)


def result_agents(result):
    """The agent records of a result.json: "agents", or solo's "agent"."""
    agents = result.get("agents")
    if isinstance(agents, dict) and agents:
        return agents
    return {"solo": result["agent"]} if isinstance(result.get("agent"), dict) else {}


def agent_ran_out_of_time(agent):
    """Whether the claude-cli driver ended the agent at the pair's time
    limit (drivers.CliRun.run): the benchmark's own limit, reached, is an
    outcome of the pair, whose patch is scored as any other's, not a
    failure of the harness."""
    return str(agent.get("end_reason") or "").startswith(drivers.TIMED_OUT)


def agent_failed(agent):
    """An agent whose attempt is no sample: Error from a usage limit, a
    refused sign-in, a container gone, the stream ended without a result;
    one that ran out its time is kept (agent_ran_out_of_time)."""
    return agent.get("status") == "Error" and not agent_ran_out_of_time(agent)


def pair_finished(run_dir):
    """CooperBench reruns a pair any of whose agents ended in Error
    (coop.py and team.py read "agents", solo.py the singular "agent");
    coordbench keeps one whose agent ran out its time (PARITY.md, row 23),
    since that attempt is scored and a rerun would most likely run out
    again, spending the account's usage to replace a sample."""
    result = read_json(Path(run_dir) / "result.json")
    if not result or result.get("error"):
        return False
    return not any(agent_failed(a) for a in result_agents(result).values())


# The hub.

class HubError(RuntimeError):
    """The hub refused a request or could not be reached; status is the
    HTTP status of a refusal, None when nothing answered."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class HubClient:
    """The runner's side of the hub's HTTP API, at the URL the runner
    reaches it by (which is not the one containers use), with the admin
    token its management API wants, when it wants one."""

    def __init__(self, base_url, admin_token=None):
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token or None

    def headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        return headers

    def request(self, method, path, body=None, timeout=120):
        """The hub's JSON answer, or HubError. In debug each call is one
        "hub_call" event with its method, path, HTTP status (None when
        nothing answered) and duration; in prod that costs a clock read
        before the call and one comparison after it."""
        started, status = time.monotonic(), None
        try:
            status, answer = self.exchange(method, path, body, timeout)
            return answer
        except HubError as problem:
            status = problem.status
            raise
        finally:
            if trace.level == "debug":
                trace.event("hub_call", method=method, path=path, status=status,
                            duration_ms=round((time.monotonic() - started) * 1000, 3))

    def exchange(self, method, path, body, timeout):
        """One HTTP exchange: the status and the JSON answer, or HubError."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=self.headers())
        try:
            with OPENER.open(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as problem:
            raise HubError(f"{method} {path}: HTTP {problem.code}: {cli.hub_error_text(problem)}",
                           problem.code) from None
        except (OSError, ValueError) as problem:
            raise HubError(f"{method} {path}: {problem}") from None

    def healthy(self):
        try:
            return bool(self.request("GET", "/api/health", timeout=5).get("ok"))
        except HubError:
            return False

    def authorized(self):
        """Whether the management API takes this client's token, or wants
        none; asked before anything is provisioned for a run it would then
        refuse. The question is a run no id can name ("-"), which the hub
        answers 401 to a wrong token before it looks for the run (404)."""
        try:
            self.request("GET", "/api/runs/-", timeout=5)
        except HubError as problem:
            return problem.status != 401
        return True

    def create_run(self, arm, run_id, features, specs, options):
        """POST /api/runs, always asking for a token per agent: each
        agent's entry then carries the bearer token its /mcp and /rpc
        want, which this answer alone holds. A hub that predates the
        option answers without tokens, and the run stays open."""
        return self.request("POST", "/api/runs", {"arm": arm, "run_id": run_id, "features": features,
                                                  "specs": specs, "options": options, "agent_tokens": True})

    def close_run(self, run_id):
        """POST /api/runs/{run}/close: the run's metrics and the artifacts
        the arm and the hub produce at close ({file name: JSON or text}),
        which a hub may also nest in the metrics; an older hub has none."""
        answer = self.request("POST", f"/api/runs/{run_id}/close")
        metrics = dict(answer.get("metrics") or {})
        nested = metrics.pop("artifacts", None)
        artifacts = answer.get("artifacts") if answer.get("artifacts") is not None else nested
        return metrics, dict(artifacts or {})

    def events(self, run_id):
        return self.request("GET", f"/api/runs/{run_id}/events").get("events") or []

    def release(self, run_id):
        """Let the hub forget the run (DELETE /api/runs/{run}), answered
        {"run_id", "released": true}; 404 for a run it does not hold."""
        return self.request("DELETE", f"/api/runs/{run_id}")


def free_port(host):
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


# Never handed on to a process that does not need it: the agents'
# credential, which goes to their containers by name alone, and the hub's
# admin token, which only the hub and the runner hold.
SECRET_NAMES = (*drivers.PASSED_BY_NAME, *drivers.HELD_BACK)


def environment_without_secrets():
    """For the hub, the scorer and git: none of them needs the agents'
    credentials or the runner's hub token, so none of them gets them."""
    return {name: value for name, value in os.environ.items() if name not in SECRET_NAMES}


# What the agents' credential reads as wherever a file held it.
SCRUBBED = "[credential scrubbed by coordbench]"
# A credential shorter than this is a placeholder, not a token, and is
# left alone: replacing a few characters everywhere would damage the
# files the scrub is meant to keep.
SHORTEST_CREDENTIAL = 12
# The archives the scrub leaves whole: a tar's member sizes are in its
# headers, so a replacement inside one loses every member after it.
# set_aside_earlier_attempt scrubs a pair's files before it packs them, so
# an archive of its own holds no credential; what an agent packed is a
# limit of the scrub, and the README says so.
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")


class CredentialKept(RuntimeError):
    """A file that holds the agents' credential could not be rewritten."""


def credential_values(environ=None):
    """The subscription token's value under every name it may carry, the
    longest first, so one that contains another is replaced whole."""
    environ = os.environ if environ is None else environ
    values = {environ.get(name) or "" for name in (*drivers.PASSED_BY_NAME, drivers.TOKEN_ALIAS)}
    return sorted((value for value in values if len(value) >= SHORTEST_CREDENTIAL), key=len, reverse=True)


def scrub_bytes(data, values):
    for value in values:
        data = data.replace(value.encode("utf-8"), SCRUBBED.encode("utf-8"))
    return data


def is_archive(path):
    return path.name.endswith(ARCHIVE_SUFFIXES)


def read_for_scrub(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as source:
            return source.read()
    return path.read_bytes()


def write_scrubbed(path, cleaned):
    """In place, so the mode stays; a gzip file gets a header with no name
    and no time, as export.py writes trajectory.json.gz, so a scrubbed
    export is still the same bytes for the same content."""
    if path.suffix == ".gz":
        with open(path, "wb") as target, gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as packed:
            packed.write(cleaned)
    else:
        path.write_bytes(cleaned)


def scrub_file(path, values):
    """True when the file held a credential and was rewritten; an archive
    is left whole (ARCHIVE_SUFFIXES). A file that holds the credential and
    cannot be rewritten raises CredentialKept, since a pair's files may be
    published and must not carry it."""
    if is_archive(path):
        return False
    data = read_for_scrub(path)
    cleaned = scrub_bytes(data, values)
    if cleaned == data:
        return False
    try:
        write_scrubbed(path, cleaned)
    except OSError as problem:
        raise CredentialKept(f"{path} holds the agents' credential and could not be rewritten: {problem}") from problem
    return True


def scrub_paths(paths, values):
    """The files, each once; a file that cannot be read (a broken gzip
    among them) is said and skipped, and when any file holds the
    credential and could not be rewritten, every other file is still
    scrubbed and then CredentialKept names them all."""
    rewritten, kept = [], []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if scrub_file(path, values):
                rewritten.append(path)
        except CredentialKept as problem:
            kept.append(str(problem))
        except (OSError, EOFError, zlib.error) as problem:
            say(f"scrubbing {path} failed: {problem}")
    if kept:
        raise CredentialKept("\n".join(kept))
    return rewritten


def scrub_credentials(directories, values=None):
    """Replace the exact value of the agents' credential wherever a file
    under the directories holds it: an agent that prints its environment
    writes the token into its stream, its session transcript and whatever
    it said to its peers, and a run whose files are published (a
    workflow's artifact) would publish it. Returns the files rewritten."""
    values = credential_values() if values is None else values
    if not values:
        return []
    paths = []
    for directory in directories:
        directory = Path(directory)
        if directory.is_dir():
            paths.extend(sorted(directory.rglob("*")))
    return scrub_paths(paths, values)


def archive_holds(path, values):
    """Whether a member of the archive holds the credential, read without
    unpacking it: the scrub leaves archives whole, so one that does keeps
    the pair from being published. An archive inside the archive is read
    as its bytes, not opened."""
    needles = [value.encode("utf-8") for value in values]
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as packed:
            return any(any(needle in packed.read(member) for needle in needles)
                       for member in packed.namelist() if not member.endswith("/"))
    with tarfile.open(path, "r:*") as packed:
        for member in packed:
            if member.isfile():
                data = packed.extractfile(member).read()
                if any(needle in data for needle in needles):
                    return True
    return False


LOOSE_OBJECT = re.compile(r"[0-9a-f]{38}")


def git_object_kind(path):
    """"loose" for a file at objects/xx/<38 hex>, "pack" for one at
    objects/pack/*.pack, else None: a repository an agent made under the
    team arm's shared/ (or a workspace kept with --keep) keeps its files
    zlib-compressed, where the scrub's plain search cannot see the
    credential; a bare repository has objects/ at its root, so the
    directory above objects/ is not looked at."""
    if path.parent.parent.name != "objects":
        return None
    if path.parent.name == "pack" and path.name.endswith(".pack"):
        return "pack"
    if len(path.parent.name) == 2 and LOOSE_OBJECT.fullmatch(path.name):
        return "loose"
    return None


def pack_contents(data):
    """Each object of a git packfile inflated, in order: after the 12-byte
    header, each object is a varint header (its type and size, and for a
    delta its base, an offset varint or a 20-byte id) then a zlib stream,
    whose end the decompressor finds. A delta's inserted bytes come out
    in the clear, so a value added whole to a file is seen; a truncated
    or malformed file raises, and the caller treats it as unreadable."""
    if data[:4] != b"PACK":
        raise ValueError("not a packfile")
    view = memoryview(data)
    count = int.from_bytes(data[8:12], "big")
    offset = 12
    for _ in range(count):
        byte = data[offset]
        offset += 1
        kind = (byte >> 4) & 7
        while byte & 0x80:
            byte = data[offset]
            offset += 1
        if kind == 6:
            byte = data[offset]
            offset += 1
            while byte & 0x80:
                byte = data[offset]
                offset += 1
        elif kind == 7:
            offset += 20
        inflater = zlib.decompressobj()
        content = inflater.decompress(view[offset:])
        if not inflater.eof:
            raise ValueError("a truncated object")
        offset = len(data) - len(inflater.unused_data)
        yield content


def git_object_holds(path, kind, values):
    """Whether a git object file holds the credential once inflated."""
    needles = [value.encode("utf-8") for value in values]
    data = path.read_bytes()
    if kind == "loose":
        return any(needle in zlib.decompress(data) for needle in needles)
    return any(any(needle in content for needle in needles) for content in pack_contents(data))


def unscrubbed(directory, values):
    """Every file under the directory that still holds the credential,
    that holds it inside an archive or a git object, or that could not be
    read, each with why, for a run whose files are about to be published."""
    kept = []
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        inside = "inside an archive" if is_archive(path) else "inside a git object" if git_object_kind(path) else ""
        try:
            if is_archive(path):
                held = archive_holds(path, values)
            elif git_object_kind(path):
                held = git_object_holds(path, git_object_kind(path), values)
            else:
                data = read_for_scrub(path)
                held = any(value.encode("utf-8") in data for value in values)
        except (OSError, EOFError, IndexError, ValueError, zlib.error, tarfile.TarError, zipfile.BadZipFile) as problem:
            kept.append(f"{path}: could not be read ({problem})")
            continue
        if held:
            kept.append(f"{path}: holds the agents' credential" + (f" {inside}" if inside else ""))
    return kept


def command_scrub(args):
    """Scrub a run directory once more and say whether it may be
    published: exit 0 when no file under it holds the agents' credential
    (none is in the environment, or every file was read and found clean),
    2 when one does, or holds it inside an archive or a git object, or
    could not be read, which the job treats as a pair with no result
    rather than publish it.
    The workflow runs this after run.py ended however it ended, since the
    run's own scrub runs only when the pair finishes."""
    directory = Path(args.run_dir)
    if not directory.is_dir():
        say(f"{directory} is not a directory")
        return 2
    values = credential_values()
    if not values:
        say(f"{directory}: no credential in the environment, nothing to scrub")
        return 0
    rewritten = scrub_credentials([directory], values)
    if rewritten:
        say(f"{directory}: the agents' credential was in {len(rewritten)} files and is scrubbed out of them")
    kept = unscrubbed(directory, values)
    if kept:
        say("\n".join(kept))
        return 2
    say(f"{directory}: no file holds the agents' credential")
    return 0


def pair_hub_dir(plan):
    return Path(plan["log_dir"]) / plan["run_name"] / "hub"


def scrub_pair(plan):
    """The pair's directory and its own part of the hub's, once the pair's
    last file is written; one line says when the credential was found,
    since an agent that printed it is worth knowing about. The hub writes
    each pair's events under that pair's run id, and its own files, which
    it appends to while any pair runs, are scrubbed once it stops
    (scrub_hub); the files of a hub started elsewhere (--hub) are that
    hub's, and the README says so."""
    rewritten = scrub_credentials([plan["run_dir"], pair_hub_dir(plan) / plan["run_id"]])
    if rewritten:
        say(f"{plan['run_id']}: the agents' credential was in {len(rewritten)} files and is scrubbed out of them")


def scrub_hub(hub_dir):
    """The hub's own files (its log, its first line, its trace), once it
    has stopped and appends to them no more."""
    hub_dir = Path(hub_dir)
    values = credential_values()
    if not hub_dir.is_dir() or not values:
        return
    rewritten = scrub_paths(sorted(hub_dir.iterdir()), values)
    if rewritten:
        say(f"the agents' credential was in {len(rewritten)} files of the hub and is scrubbed out of them")


def wait_for_first_line(path, process, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline and process.poll() is None:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "\n" in text:
            return text.splitlines()[0]
        time.sleep(0.05)
    return ""


def hub_command(host, port, hub_dir, arm_dirs):
    """hub.py's command line. A runner in debug (--debug, or COORD_TRACE)
    starts its hub in debug too, so one flag traces both sides: the hub
    writes its hub-trace.jsonl beside its events logs under hub_dir."""
    command = [sys.executable, str(HERE / "hub.py"), "--host", host, "--port", str(port),
               "--public-url", f"http://host.docker.internal:{port}", "--log-dir", str(hub_dir)]
    for directory in arm_dirs:
        command += ["--arm-dir", str(Path(directory).resolve())]
    if trace.level == "debug":
        command.append("--debug")
    return command


def start_hub(hub_dir, host="0.0.0.0", timeout=30, arm_dirs=()):
    """Start hub.py on a free port, bound to `host`, telling agents to reach
    it as host.docker.internal and allowing it the arms under `arm_dirs`;
    returns the process and a client that reaches it from this host with
    the token it was given. The token is fresh for each hub and goes in its
    environment, never on its command line, which any user of the host can
    read. Its stdout goes to a file rather than a pipe, so a hub left
    running for `finish` never writes into a pipe nobody reads."""
    hub_dir = Path(hub_dir)
    hub_dir.mkdir(parents=True, exist_ok=True)
    port = free_port(host)
    token = secrets.token_hex(32)
    first_line = hub_dir / f"hub-{port}.out"
    with open(first_line, "w") as out, open(hub_dir / "hub.log", "a") as errors:
        process = subprocess.Popen(hub_command(host, port, hub_dir, arm_dirs), stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=errors, start_new_session=True,
                                   env={**environment_without_secrets(), drivers.ADMIN_TOKEN: token})
    if not wait_for_first_line(first_line, process, timeout):
        stop_hub(process)
        raise HubError(f"hub.py did not start; see {hub_dir / 'hub.log'}")
    return process, HubClient(f"http://127.0.0.1:{port}", token)


def stop_hub(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def is_hub(pid, hub_url):
    """Whether the pid is still the hub prepare started, on the port its
    plan names, rather than whatever process took the number since."""
    port = urlsplit(hub_url).port
    try:
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return False
    runs_the_hub = any(word.endswith(b"hub.py") for word in command)
    on_its_port = any(command[i:i + 2] == [b"--port", str(port).encode()] for i in range(len(command)))
    return runs_the_hub and on_its_port


def stop_hub_pid(pid, hub_url):
    """Stop the hub prepare left running, if the pid is still that hub."""
    if is_hub(pid, hub_url):
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid), signal.SIGTERM)


def hub_admin_token(pid, hub_url):
    """The token of the hub prepare started, read from that hub's own
    environment, the one place it is kept: finish is another process, and
    handing the token over in a file or on a command line would leak it."""
    if not is_hub(pid, hub_url):
        return None
    try:
        environment = Path(f"/proc/{int(pid)}/environ").read_bytes()
    except OSError:
        return None
    prefix = f"{drivers.ADMIN_TOKEN}=".encode()
    for entry in environment.split(b"\0"):
        if entry.startswith(prefix):
            return entry[len(prefix):].decode("utf-8", "replace")
    return None


def finish_client(plan):
    """A client for the hub a prepared pair was created on: the token of the
    hub prepare started, else the runner's own, as for a hub named by --hub."""
    token = hub_admin_token(plan["hub_pid"], plan["hub_url"]) if plan.get("hub_pid") else None
    return HubClient(plan["hub_url"], token or os.environ.get(drivers.ADMIN_TOKEN))


def arm_given_as_directory(arm_given):
    return (Path(arm_given) / "arm.json").exists()


def hub_arm_dirs(settings):
    """The directories a hub started here may load arms from: each --arm-dir,
    and the arm's own directory when --arm names one, since the hub loads
    nothing else but its built-in arms."""
    directories = [Path(directory) for directory in settings.get("arm_dirs") or ()]
    if arm_given_as_directory(settings["arm"]):
        directories.append(Path(settings["arm"]))
    return directories


def connect_hub(settings):
    """The hub named by --hub, sent the runner's own COORD_ADMIN_TOKEN when
    that is set, or one started here; the process is None for a hub this
    runner did not start."""
    if not settings["hub"]:
        process, client = start_hub(hub_dir(settings), arm_dirs=hub_arm_dirs(settings))
        return client, process
    client = HubClient(settings["hub"], os.environ.get(drivers.ADMIN_TOKEN))
    if not client.healthy():
        raise HubError(f"no hub answers at {settings['hub']}/api/health")
    if not client.authorized():
        held = "refuses the token in" if client.admin_token else "wants its admin token in"
        raise HubError(f"the hub at {settings['hub']} {held} {drivers.ADMIN_TOKEN}")
    if settings.get("arm_dirs"):
        say(f"--arm-dir is for a hub this runner starts; the hub at {settings['hub']} loads its own")
    return client, None


# The plan: everything about one pair, kept as JSON so finish can pick it up.

def arm_request(arm_given):
    """What POST /api/runs is sent: a built-in name as given, a directory
    made absolute so a hub with another working directory finds it."""
    return str(Path(arm_given).resolve()) if arm_given_as_directory(arm_given) else arm_given


def arm_needs(arm, options=None):
    """What the runner provisions: the arm's needs, and each need whose
    option --option names, the way CooperBench's --git gives team mode a
    remote it does not need. Either way the runner makes the thing itself
    and sets all of its options, so a prompt that names a shared remote
    finds it set up as the prompt says."""
    needs = list(arm.needs) + [n for n in arm.config.get("needs") or [] if n not in arm.needs]
    asked = [need for need, names in NEED_OPTIONS.items()
             if need not in needs and any((options or {}).get(name) for name in names)]
    needs += asked
    unknown = [n for n in needs if n not in KNOWN_NEEDS]
    if unknown:
        raise ValueError(f"the arm needs {', '.join(unknown)}, which this runner cannot provision")
    return needs


def arm_name(arm):
    return arm.config.get("name") or arm.name


def new_run_id(name, task_id, features):
    return safe_name(f"{name}-{task_id}-{feature_dir_name(features)}-{secrets.token_hex(3)}")


def pair_setting(arm, layout):
    """CooperBench's setting for the arm's pair, which names its directory
    and decides how it is scored: solo for one agent, team for the team
    arm (team.py writes "team" under team/), coop for the rest, coop-git
    included, as --git leaves CooperBench's setting coop. An arm of one's
    own may name its setting in arm.json ("setting"); one agent is solo
    whatever it says, since only test_solo scores a single patch."""
    if len(layout) == 1:
        return "solo"
    declared = arm.config.get("setting")
    if declared is not None:
        if declared not in SETTINGS[1:]:
            raise ValueError(f"arm.json's setting {declared!r} is not one of {', '.join(SETTINGS[1:])}")
        return declared
    return "team" if arm_name(arm) == "team" else "coop"


def dataset_dir_of(settings):
    """The dataset the specs are read from and the scorer is handed: its
    own --dataset-dir (a 59bbf65 worktree of dataset/, for paper-era specs
    and tests) or the checkout's own."""
    return Path(settings.get("dataset_dir") or Path(settings["cooperbench_dir"]) / "dataset")


def new_plan(settings, driver, repo, task_id, features):
    arm = find_arm(settings["arm"])
    layout = arm.layout(features)
    setting = pair_setting(arm, layout)
    dataset_dir = dataset_dir_of(settings)
    surface = arm.claude_surface(settings["options"])
    return {
        "repo": repo, "task_id": int(task_id), "features": list(features), "setting": setting,
        "run_name": settings["run_name"], "run_id": new_run_id(arm_name(arm), task_id, features),
        "arm": arm_request(settings["arm"]), "arm_name": arm_name(arm), "arm_dir": str(arm.root),
        "needs": arm_needs(arm, settings["options"]), "plugin_entries": list(arm.config.get("plugins") or []),
        "mcp_servers": surface["mcp_servers"], "arm_plugin": surface["plugin"],
        "tool_surface": surface["tool_surface"],
        "driver": settings["driver"], "agent_framework": getattr(driver, "framework", settings["driver"]),
        "host_workspace": bool(getattr(driver, "host_workspace", False)),
        "measures_usage": bool(getattr(driver, "measures_usage", False)),
        "isolation": runconfig.driver_isolation(driver),
        "model": settings["model"], "agent_models": dict(settings.get("agent_models") or {}),
        "image": image_name(repo, task_id), "image_facts": None,
        "options": dict(settings["options"]), "disallowed_tools": list(settings.get("disallowed_tools") or []),
        "timeout_s": settings["timeout_s"], "max_turns": settings["max_turns"], "keep": settings["keep"],
        "cooperbench_dir": str(settings["cooperbench_dir"]), "dataset_dir": str(dataset_dir),
        "provenance": runconfig.provenance(settings["cooperbench_dir"], dataset_dir),
        "log_dir": str(settings["log_dir"]),
        "run_dir": str(pair_dir(settings["log_dir"], settings["run_name"], setting, repo, task_id, features)),
        "hub_url": "", "hub_pid": None, "hub_run": False, "started_at": now().isoformat(),
        "utc_offset_seconds": utc_offset_seconds(), "error": None,
        "plugins": [], "warnings": [], "timings": list(settings.get("timings") or []),
        "resources": {"containers": [], "volume": None, "git_repo": None, "workspaces": None},
        "agents": [{"id": a.id, "role": a.role, "features": list(a.features)} for a in layout],
    }


def finished_run_dir(plan):
    """Where the pair already finished, if it did: its own directory, or,
    for a team pair, the coop/ directory team runs were written under
    before the team setting had its own, so a run resumed across that
    change skips the pairs it finished instead of running them again."""
    run_dir = Path(plan["run_dir"])
    candidates = [run_dir]
    if plan["setting"] == "team":
        candidates.append(run_dir.parents[3] / "coop" / run_dir.relative_to(run_dir.parents[2]))
    return next((candidate for candidate in candidates if pair_finished(candidate)), None)


def agent_file_id(plan, agent_id):
    """The name an agent's CooperBench files carry: CooperBench's solo
    runner calls its agent "solo" (solo_stream.jsonl, solo_traj.json),
    whatever the arm calls it on the hub; any other agent its own id."""
    return "solo" if plan["setting"] == "solo" else safe_name(agent_id)


def agent_model(plan, agent):
    """The model the runner asks this agent to run: its --agent-model,
    else the run's --model."""
    return (plan.get("agent_models") or {}).get(agent["id"]) or plan["model"]


def save_plan(plan):
    """plan.json, the owner's alone (mode 0o600), because the plan holds
    each agent's hub token with the rest of what finish needs. Keeping
    them there is acceptable: a token is a per-run credential that lets
    its holder act as one benchmark agent on this host's hub, nothing
    beyond, and the run is over once the plan has served its purpose.
    What prepare prints (plan_summary) leaves them out."""
    write_private_text(Path(plan["run_dir"]) / "plan.json", json.dumps(plan, indent=2, default=str) + "\n")


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
                f"starting the git server (build {GIT_IMAGE} with bench/cooperbench/setup.sh)")
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
    """DockerGitServer.create's per-run init (git_servers/docker.py), plus
    a reflog: a bare repository keeps none by default, and without one a
    force-push leaves no trace of what it replaced, so git_log.txt could
    not show it. The reflog changes nothing an agent sees."""
    quoted = shlex.quote(path)
    return (f"set -e && mkdir -p {quoted} && cd {quoted} && git init -q --bare && "
            "git config receive.denyCurrentBranch ignore && git config core.logAllRefUpdates always && "
            "touch git-daemon-export-ok")


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
    with SHARED_INFRA_LOCK:
        ensure_git_server()
    run_id = plan["run_id"]
    plan["resources"]["git_repo"] = f"/git/{run_id}"
    checked(drivers.docker("exec", GIT_SERVER, "sh", "-c", init_repository_script(f"/git/{run_id}/repo.git")),
            "creating the run's repository")
    url = f"git://{GIT_SERVER}:{GIT_PORT}/{run_id}/repo.git"
    seeded = checked(drivers.docker("run", "--rm", "--network", NETWORK, "--entrypoint", "sh", plan["image"],
                                    "-c", seed_script(url)),
                     "seeding the shared repository from the task image")
    options = {"git_remote": url, "git_remote_host": f"git://127.0.0.1:{GIT_PORT}/{run_id}/repo.git",
               "base_ref": base_commit(seeded.stdout or "")}
    on_host = repository_on_host(run_id)
    if on_host:
        options["git_remote_dir"] = on_host
    return options


def repository_on_host(run_id):
    """The run's bare repository as a directory of this host, when the git
    server's volume is one this process owns and can read: the hub then
    reads it in place for git_log.txt, reflog included, which a fetch over
    git:// never carries. None otherwise (docker in a VM, a volume of
    another user), and the hub fetches from git_remote_host instead."""
    mount = drivers.docker("volume", "inspect", "-f", "{{.Mountpoint}}", GIT_DATA_VOLUME)
    root = (mount.stdout or "").strip() if mount.returncode == 0 else ""
    if not root:
        return None
    path = Path(root) / run_id / "repo.git"
    try:
        owned = path.is_dir() and path.stat().st_uid == os.getuid() and os.access(path, os.R_OK | os.X_OK)
    except OSError:
        return None
    return str(path) if owned else None


def add_agent_branches(plan):
    repository = f"/git/{plan['run_id']}/repo.git"
    for agent in plan["agents"]:
        checked(drivers.docker("exec", GIT_SERVER, "git", "-C", repository, "branch", agent["id"], "main"),
                f"creating the branch {agent['id']}")


def create_shared_volume(plan):
    """CooperBench's team scratchpad: one named volume per run at
    /workspace/shared. The team arm mirrors its task list there from the
    hub's host, so the volume's host path goes along as shared_dir_host.
    A path this host cannot write to (docker run by another user, or in a
    VM) is still the volume's, so it is given, and the warning says why
    the mirror may be missing."""
    name = safe_name(f"coordbench-shared-{plan['run_id']}")
    plan["resources"]["volume"] = name
    checked(drivers.docker("volume", "create", name), "creating the shared volume")
    mount = checked(drivers.docker("volume", "inspect", "-f", "{{.Mountpoint}}", name),
                    "finding the shared volume on this host")
    host_path = (mount.stdout or "").strip()
    if not host_path:
        raise drivers.DockerError(f"docker named no host path for the volume {name}")
    if not os.access(host_path, os.W_OK):
        plan["warnings"].append(f"the shared volume's host path {host_path} is not writable here, "
                                "so a hub on this host cannot write into it")
    return {"shared_dir": SHARED_DIR, "shared_dir_host": host_path}


def provision_shared(plan):
    """What the arm's needs ask for, as run options; created before the run
    exists on the hub, since the options go with POST /api/runs. Every
    option of every need is set, or the pair fails here rather than send
    an arm off without what it was promised."""
    with SHARED_INFRA_LOCK:
        ensure_network()
    options = {}
    if "git_remote" in plan["needs"]:
        with timed(plan, "git_remote"):
            options.update(create_git_repository(plan))
    if "shared_volume" in plan["needs"]:
        with timed(plan, "shared_volume"):
            options.update(create_shared_volume(plan))
    missing = [name for need in plan["needs"] for name in PROVIDED_OPTIONS[need] if not options.get(name)]
    if missing:
        raise drivers.DockerError(f"provisioning left the run without {', '.join(missing)}")
    return options


# Extra plugins an arm brings: directories, git URLs fetched on this host,
# or plugin .zip URLs handed to Claude Code as they are.

def is_url(entry):
    return "://" in entry or entry.startswith("git@")


def is_zip_url(entry):
    """A URL Claude Code loads itself with --plugin-url, which takes a .zip
    and nothing else; any other URL is a git repository to clone here."""
    return "://" in entry and urlsplit(entry).path.lower().endswith(".zip")


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
    """Each plugin as the plan records it: a .zip URL as {"url"}, which
    nothing is copied for; a directory, or a git URL cloned into one, as
    {"source", "container"}, the host path and where the container gets it."""
    cache = Path(plan["run_dir"]) / "plugins"
    for index, entry in enumerate(plan["plugin_entries"]):
        if is_zip_url(entry):
            plan["plugins"].append({"url": entry})
            continue
        source = resolve_plugin(entry, plan["arm_dir"], cache, index)
        plan["plugins"].append({"source": str(source),
                                "container": f"{drivers.CONTAINER_PLUGINS}/{index}-{safe_name(source.name)}"})


def copied_plugins(plan):
    return [plugin for plugin in plan["plugins"] if "container" in plugin]


def plugin_urls(plan):
    return [plugin["url"] for plugin in plan["plugins"] if "url" in plugin]


# One agent's container.

def adopt_agents(plan, answer):
    """The hub's layout is the run's; it must agree with the one the setting
    and the log directory were chosen from."""
    agents = answer.get("agents") or []
    if (len(agents) == 1) != (plan["setting"] == "solo"):
        raise HubError("the hub laid out the agents differently from this runner's copy of the arm")
    run_dir = Path(plan["run_dir"])
    plan["agents"] = []
    for agent in agents:
        name = agent_file_id(plan, agent["id"])
        prompt_file = run_dir / f"{name}_prompt.md"
        write_text(prompt_file, agent.get("prompt") or "")
        plan["agents"].append({"id": agent["id"], "role": agent["role"], "features": list(agent["features"]),
                               "mcp_url": agent["mcp_url"], "rpc_url": agent["rpc_url"],
                               "token": agent.get("token"), "prompt_file": str(prompt_file),
                               "log_path": str(run_dir / f"{name}_stream.jsonl")})


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
        write_text(info / "exclude", f"\n{drivers.DONE_MARKER}\n", append=True)


def container_arguments(plan, agent):
    arguments = ["run", "-d", "--name", agent["container"], "--label", f"coordbench.run={plan['run_id']}",
                 "--network", NETWORK, "--add-host", "host.docker.internal:host-gateway",
                 "-e", f"COORD_MCP_URL={agent['mcp_url']}", "-e", f"COORD_RPC_URL={agent['rpc_url']}"]
    if agent.get("token"):
        # By name alone: the docker client copies the value from its own
        # environment, which holds it for this one call (token_environment).
        arguments += ["-e", drivers.AGENT_TOKEN]
    for name, value in agent_trace_environment().items():
        arguments += ["-e", f"{name}={value}"]
    if plan["resources"]["volume"]:
        arguments += ["-v", f"{plan['resources']['volume']}:{SHARED_DIR}"]
    if agent.get("workspace"):
        arguments += ["-v", f"{agent['workspace']}:{CONTAINER_REPO}"]
    return arguments + ["-w", CONTAINER_REPO, "--entrypoint", "sh", plan["image"], "-c", KEEP_ALIVE]


def agent_trace_environment():
    """The trace level the agents' side is given: debug when this runner
    traces in debug (--debug), so an agent's own commands time themselves
    as the runner's do, and nothing otherwise, which leaves the agents'
    environment in prod exactly as it was."""
    return {cli.TRACE_VARIABLE: "debug"} if trace.level == "debug" else {}


def install_coordbench(plan, container):
    """coordbench at /opt/coordbench, so the arm's plugin and cli.py are
    there; an arm from elsewhere under a root of its own
    (drivers.container_arm_dir), so it never lands on a built-in arm of
    the same name; its copied plugins beside it. A .zip URL plugin is
    Claude Code's to fetch, so nothing is copied for it."""
    checked(drivers.docker("exec", container, "mkdir", "-p", "/opt", drivers.CONTAINER_PLUGINS,
                           drivers.CONTAINER_ARMS),
            "making /opt in the container")
    checked(drivers.docker("cp", f"{HERE}/.", f"{container}:{drivers.CONTAINER_COORDBENCH}"),
            "copying coordbench into the container")
    if not arm_is_builtin(plan["arm_dir"]):
        inside = drivers.container_arm_dir(plan["arm_dir"])
        checked(drivers.docker("cp", f"{plan['arm_dir']}/.", f"{container}:{inside}"),
                "copying the arm into the container")
    for plugin in copied_plugins(plan):
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


def git_setup_command(agent_id, server_url, repo=CONTAINER_REPO):
    """build_git_setup_command (_coop/runtime.py at 63b9d44), unchanged but
    for the repository's path, which tests point at a repository of their
    own: the remote `team`, the agent's own branch, pushed, and main as the
    base. It ends in `|| true`, so its exit code says nothing of whether it
    worked; configure_git reads the outcome back instead."""
    server = shlex.quote(server_url)
    aid = shlex.quote(agent_id)
    branch = shlex.quote(agent_id)
    return (
        f"cd {shlex.quote(repo)} && "
        f"git config user.email 'agent@cooperbench.local' && "
        f"git config user.name {aid} && "
        f"(git remote add team {server} 2>/dev/null || git remote set-url team {server}) && "
        f"(git checkout -b {branch} 2>/dev/null || git checkout {branch}) && "
        f"git push -u team {branch} --force && "
        "git push team HEAD:refs/heads/main --force 2>/dev/null || true"
    )


def git_promises(agent_id, options):
    """What CooperBench's git block (_git_block in _coop/prompt.py at
    63b9d44) tells the agent is already so, each as what it is, the git
    command that reads it back in the agent's repository, and what that
    must print: the remote team, the agent's branch checked out at the base
    commit and pushed with -u, team/main at the base commit, and the git
    user it names."""
    base = options["base_ref"]
    upstream = shlex.quote(f"{agent_id}@{{upstream}}")
    pushed = shlex.quote(f"refs/remotes/team/{agent_id}")
    return [
        ("the remote team", "git remote get-url team", options["git_remote"]),
        (f"the branch {agent_id} checked out", "git symbolic-ref --short HEAD", agent_id),
        (f"{agent_id} at the base commit", "git rev-parse HEAD", base),
        (f"{agent_id} pushed with -u", f"git rev-parse --abbrev-ref {upstream}", f"team/{agent_id}"),
        (f"team/{agent_id} at the base commit", f"git rev-parse --verify -q {pushed}", base),
        ("team/main at the base commit", "git rev-parse --verify -q refs/remotes/team/main", base),
        ("user.name the agent id", "git config user.name", agent_id),
        (f"user.email {GIT_EMAIL}", "git config user.email", GIT_EMAIL),
    ]


def git_read_script(promises, repo=CONTAINER_REPO):
    """One shell line printing each promise's answer on a marked line of
    its own, in order, an empty answer when its command fails."""
    reads = [f"printf '{GIT_READ_MARK}%s\\n' \"$({command} 2>/dev/null)\"" for _, command, _ in promises]
    return f"cd {shlex.quote(repo)} && {{ {'; '.join(reads)}; }}"


def unkept_promises(promises, output):
    answers = [line[len(GIT_READ_MARK):].strip() for line in output.splitlines()
               if line.startswith(GIT_READ_MARK)]
    answers += [""] * (len(promises) - len(answers))
    return [f"{what} (found {found or 'nothing'})"
            for (what, _, expected), found in zip(promises, answers) if found != expected]


def last_words(result):
    said = (result.stderr or result.stdout or "").strip().splitlines()
    return said[-1][:300] if said else ""


def configure_git(container, agent_id, options):
    """CooperBench's git set-up in the agent's repository, then each promise
    of its git block read back. One not kept fails the pair: an agent told
    its branch is pushed and team/main is the base, when they are not, would
    make the run measure the harness rather than the arm. CooperBench's
    adapter only warns, but the warning never comes, for the `|| true`."""
    setup = drivers.docker("exec", "-w", CONTAINER_REPO, container, "bash", "-lc",
                           git_setup_command(agent_id, options["git_remote"]))
    promises = git_promises(agent_id, options)
    read = checked(drivers.docker("exec", "-w", CONTAINER_REPO, container, "bash", "-lc",
                                  git_read_script(promises)),
                   f"reading back {agent_id}'s git set-up")
    unkept = unkept_promises(promises, read.stdout or "")
    if unkept:
        said = last_words(setup)
        raise drivers.DockerError(f"the git set-up for {agent_id} left its git block untrue: "
                                  f"{'; '.join(unkept)}" + (f"; git said: {said}" if said else ""))


def trust_workspace(container):
    """A host workspace belongs to this host's user, and git in the
    container, run as another, refuses a repository it does not own
    ("dubious ownership"), which would fail the git set-up and every git
    command the agent runs, `git diff > patch.txt` among them; so git in
    the container is told to trust that one directory."""
    checked(drivers.docker("exec", container, "git", "config", "--global", "--add", "safe.directory",
                           CONTAINER_REPO),
            "letting git in the container use the host workspace")


def token_environment(agent):
    """What the docker client's environment holds while it starts this
    agent's container: the agent's token under the name container_arguments
    passes by itself, so it reaches the container and no command line."""
    return {drivers.AGENT_TOKEN: agent["token"]} if agent.get("token") else {}


def write_token_file(bin_dir, token):
    """The agent's token in a file of its own beside its wrappers, the
    owner's alone, which each wrapper reads when it runs: the token is
    then in no wrapper's text and on no process's command line."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / TOKEN_FILE
    write_private_text(path, token + "\n")
    return path


def install_host_wrappers(plan, agent, hub):
    """The session driver's agent runs on this host, so its commands are
    wrappers here, each carrying the agent's /rpc URL as this host reaches
    it, reading the agent's token from its file when the run wants one,
    and in debug exporting the trace level, since the session's shell that
    runs them is not one the runner starts."""
    url = hub.base_url + urlsplit(agent["rpc_url"]).path
    bin_dir = drivers.session_bin_dir(plan["run_dir"], agent["id"])
    arguments = ["install", str(bin_dir), "--url", url, "--python", sys.executable]
    if agent.get("token"):
        arguments += ["--token-file", str(write_token_file(bin_dir, agent["token"]))]
    if agent_trace_environment():
        arguments.append("--debug")
    with STDOUT_LOCK, contextlib.redirect_stdout(sys.stderr):
        code = cli.main(arguments)
    if code != 0:
        raise HubError(f"installing {agent['id']}'s commands on this host failed")


def provision_agent(plan, agent, hub, options):
    agent["container"] = safe_name(f"coordbench-{plan['run_id']}-{agent['id']}")
    if plan["host_workspace"]:
        agent["workspace"] = copy_repository_out(plan, agent)
    plan["resources"]["containers"].append(agent["container"])
    checked(drivers.docker(*container_arguments(plan, agent), extra_env=token_environment(agent)),
            f"starting {agent['id']}'s container")
    install_coordbench(plan, agent["container"])
    install_aliases(plan, agent["container"])
    if plan["host_workspace"]:
        trust_workspace(agent["container"])
    if "git_remote" in plan["needs"]:
        configure_git(agent["container"], agent["id"], options)
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
        arm_dir=plan["arm_dir"], arm_plugin=plan.get("arm_plugin", True),
        plugins=[p["container"] for p in copied_plugins(plan)],
        plugin_urls=plugin_urls(plan),
        mcp_servers=plan["mcp_servers"], model=agent_model(plan, agent), timeout_s=plan["timeout_s"],
        max_turns=plan["max_turns"], log_path=agent["log_path"], workspace=agent.get("workspace", ""),
        disallowed_tools=list(plan.get("disallowed_tools") or []))


def start_agents(plan, driver):
    """Start every agent; one that fails to start is recorded and the rest
    run on, as CooperBench lets one agent's error leave the other running.
    Each agent's start goes in the plan, which prepare saves, so a finish
    in another process measures the agent's phase from there."""
    handles = {}
    for agent in plan["agents"]:
        agent["started_at"] = now().isoformat()
        try:
            handle = driver.start(agent_job(plan, agent))
        except Exception as problem:
            agent["start_error"] = f"{type(problem).__name__}: {problem}"
            continue
        handles[agent["id"]] = handle
        agent["handle"] = handle if isinstance(handle, dict) else None
        # The agent's phase begins here and ends in wait_agents, which may
        # be another process; the record there emits its "phase_done".
        trace.event("phase", phase="agent", agent=agent["id"])
    return handles


# The debug sinks this process opened, by run directory (open_debug_sink).
DEBUG_SINKS = {}


def open_debug_sink(run_dir):
    """In debug, <run_dir>/trace.jsonl takes every event of this process
    from here until close_debug_sinks: the phases, every docker command,
    hub call and file written, the session driver's polls. Opened where
    the run directory is made (prepare_pair) and by finish, which comes to
    a directory prepare made and appends to the file prepare left. In prod
    nothing is registered, so each event is one check (trace.event)."""
    key = str(run_dir)
    if trace.level == "debug" and key not in DEBUG_SINKS:
        DEBUG_SINKS[key] = trace.listen(trace.JsonLines(Path(run_dir) / "trace.jsonl"))


def close_debug_sinks():
    """Once a pair is over, so the next pair of a --subset run gets a
    trace.jsonl of its own; nothing to do in prod."""
    for sink in [DEBUG_SINKS.pop(key) for key in list(DEBUG_SINKS)]:
        trace.unlisten(sink)
        sink.close()


# An earlier attempt's files, packed by set_aside_earlier_attempt; the
# directories each attempt makes again (the session driver's workspaces and
# its wrappers, which hold the hub's token) are removed instead.
EARLIER_ATTEMPT = re.compile(r"previous-\d{8}T\d{6}(-\d+)?\.tar\.gz")
REMADE_DIRECTORIES = ("workspaces", "bin")


def set_aside_earlier_attempt(run_dir):
    """Pack an earlier attempt's files in the pair directory into
    previous-<time>.tar.gz beside them, and return the archive, or None
    when the directory holds none. A pair is run again in the same
    directory (--force, an agent in Error, a second prepare), and the new
    attempt does not rewrite every file the old one left: a judge.json, an
    agent's _sent.jsonl when it sends nothing this time, a _session.jsonl
    the session driver finds no new transcript for. Left in place, the
    post-hoc tools would read them as the new attempt's; packed, nothing
    of the old attempt is read again and nothing is lost, a judge.json
    having been paid for. The files are scrubbed of the agents' credential
    first (scrub_credentials), since the attempt may have ended before its
    own scrub."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return None
    earlier = [path for path in sorted(run_dir.iterdir()) if not EARLIER_ATTEMPT.fullmatch(path.name)]
    remade = [path for path in earlier if path.name in REMADE_DIRECTORIES and path.is_dir()]
    packed = [path for path in earlier if path not in remade]
    for path in remade:
        shutil.rmtree(path, ignore_errors=True)
    if not packed:
        return None
    # An attempt killed before its scrub may have left the credential in
    # its files; packed clean, the archive never holds it and is never
    # rewritten (ARCHIVE_SUFFIXES).
    scrub_credentials([run_dir])
    archive = unused_archive_name(run_dir)
    with tarfile.open(archive, "w:gz") as bundle:
        for path in packed:
            bundle.add(path, arcname=path.name)
    for path in packed:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    return archive


def unused_archive_name(run_dir):
    stamp = now().strftime("%Y%m%dT%H%M%S")
    archive, number = run_dir / f"previous-{stamp}.tar.gz", 1
    while archive.exists():
        number += 1
        archive = run_dir / f"previous-{stamp}-{number}.tar.gz"
    return archive


def prepare_pair(settings, hub, driver, repo, task_id, features):
    """Provision a pair and start its agents; returns the plan and handles."""
    plan = new_plan(settings, driver, repo, task_id, features)
    plan["hub_url"] = hub.base_url
    archive = set_aside_earlier_attempt(plan["run_dir"])
    if archive is not None:
        plan["warnings"].append(f"an earlier attempt's files in the pair directory were packed into {archive.name}")
    Path(plan["run_dir"]).mkdir(parents=True, exist_ok=True)
    open_debug_sink(plan["run_dir"])
    try:
        specs = read_specs(plan["dataset_dir"], repo, task_id, features)
        plan["image_facts"] = runconfig.image_facts(plan["image"])
        resolve_plugins(plan)
        options = {**settings["options"], **provision_shared(plan)}
        with timed(plan, "run_create"):
            answer = hub.create_run(plan["arm"], plan["run_id"], plan["features"], specs, options)
        plan["hub_run"] = True
        adopt_agents(plan, answer)
        if "git_remote" in plan["needs"]:
            add_agent_branches(plan)
        for agent in plan["agents"]:
            with timed(plan, "provision", agent["id"]):
                provision_agent(plan, agent, hub, options)
    except Exception as problem:
        record_failure(plan, hub, problem)
        raise PairFailed(plan["error"]) from problem
    except BaseException:
        abandon(plan, hub)
        raise
    # The agents stream to the host from the moment they start, so a
    # failure after that (the plan's write, the disk full) must tear them
    # down and scrub what they wrote, as a failure before it does.
    try:
        handles = start_agents(plan, driver)
        save_plan(plan)
    except Exception as problem:
        record_failure(plan, hub, problem)
        raise PairFailed(plan["error"]) from problem
    except BaseException:
        abandon(plan, hub)
        raise
    return plan, handles


def agent_ended(outcome):
    """When the agent's phase ended: the moment the driver says the agent
    itself stopped (the session driver's marker, the CLI driver's exec),
    else now. finish may look long after a marker appeared, and the
    agents' wall times must not carry that delay."""
    if outcome.ended_at:
        return datetime.fromtimestamp(outcome.ended_at, now().tzinfo)
    return now()


def wait_agents(plan, driver, handles):
    """Each started agent's outcome, and its "agent" phase from the start
    the plan recorded to the moment the agent stopped (agent_ended); a plan
    from before the timings, with no start to measure from, gets no record."""
    outcomes = {}
    for agent in plan["agents"]:
        if agent["id"] not in handles:
            outcomes[agent["id"]] = unmeasured_error(agent.get("start_error") or "never started")
            continue
        try:
            outcomes[agent["id"]] = driver.wait(handles[agent["id"]])
        except Exception as problem:
            outcomes[agent["id"]] = unmeasured_error(f"{type(problem).__name__}: {problem}")
        if agent.get("started_at"):
            record_timing(plan, "agent", agent["id"], datetime.fromisoformat(agent["started_at"]),
                          agent_ended(outcomes[agent["id"]]))
    return outcomes


def unmeasured_error(text):
    """An agent that never ran or whose wait raised: an Error whose text
    is CooperBench's error (an exception) and coordbench's end_reason
    alike, and nothing measured, so its usage is null rather than zero."""
    return drivers.AgentOutcome(status="Error", error=text, end_reason=text, measured=())


def fetch_events(plan, hub):
    """The closed run's events; a hub that cannot give them leaves the
    events.jsonl empty and a warning, while the metrics already fetched
    stay what they are."""
    try:
        return hub.events(plan["run_id"])
    except HubError as problem:
        plan["warnings"].append(f"fetching the run's events from the hub failed, so events.jsonl is empty "
                                f"and only result.json's metrics describe it: {problem}")
        return []


def close_run(plan, hub):
    """The run's metrics, artifacts and events, final once it is closed,
    after which the hub is asked to forget the run. A hub that cannot
    close the run leaves all three empty and says so, and the run is not
    released, since it may still be open; one that closed it but could not
    give its events keeps the metrics and artifacts, warns about the
    events alone (fetch_events) and is still asked to forget the run,
    whose events it keeps on disk when it has a log directory."""
    if not plan["hub_run"]:
        return {}, [], {}
    with timed(plan, "close"):
        try:
            metrics, artifacts = hub.close_run(plan["run_id"])
        except HubError as problem:
            plan["warnings"].append(f"closing the run on the hub failed: {problem}")
            return {}, [], {}
        events = fetch_events(plan, hub)
        release_run(plan, hub)
    return metrics, events, artifacts


def release_run(plan, hub):
    """Let the hub forget the run now that its metrics and events are in
    hand, so a hub that serves many runs keeps none of the finished ones.
    Once released the plan holds no run on the hub, so closing again
    (abandon, after a failure past this point) does nothing. A hub that
    cannot release (one without the route answers 405) only keeps the run,
    which is a warning, never the pair's error."""
    try:
        hub.release(plan["run_id"])
    except HubError as problem:
        plan["warnings"].append(f"releasing the run on the hub failed: {problem}")
        return
    plan["hub_run"] = False


def finish_pair(plan, driver, handles, hub):
    """Wait, record, tear down, score, scrub; returns the pair's verdict.
    When the wait is interrupted the run is still closed, so the arm frees
    what it took (Arm.teardown), the containers go, and the drivers'
    threads, which write the agents' stream files, are waited for; the
    scrub runs last whatever happened before it, since the credential may
    be in any file and a pair's files may be published however the pair
    ended."""
    try:
        try:
            outcomes = wait_agents(plan, driver, handles)
            ended = now()
            metrics, events, artifacts = close_run(plan, hub)
            write_outputs(plan, outcomes, metrics, events, ended, artifacts)
        except BaseException:
            abandon(plan, hub)
            settle(handles)
            raise
        teardown(plan)
        evaluation = score(plan)
        conclude(plan)
    finally:
        scrub_pair(plan)
    return verdict(outcomes, evaluation)


def settle(handles, timeout=30):
    """Wait, up to timeout seconds each, for the drivers' threads to end:
    the claude-cli driver's thread writes the agent's stream file until
    its docker exec ends, which abandon's docker rm makes it do, and a
    file rewritten while a thread still writes it would keep its tail."""
    for handle in handles.values():
        thread = getattr(handle, "thread", None)
        if thread is not None:
            thread.join(timeout)


def conclude(plan):
    """The pair is over: its timings, complete now that the teardown and
    the score are in, go into result.json, which was written before them
    because the scorer reads the patches written beside it; and one line
    sums them up for whoever is watching."""
    path = Path(plan["run_dir"]) / "result.json"
    result = read_json(path)
    if result is not None:
        result["timings"] = plan.get("timings") or []
        write_json(path, result)
        export_pair(plan)
    say(f"{plan['run_id']}: {timings_summary(plan)}")


def export_pair(plan):
    """The pair's trajectory.json.gz for CooperBench's viewer and
    coordbench's (export.py's write_pair), made once eval.json and the
    timings are in, since the trajectory carries the outcome. It is a
    derived file: a failure is said and costs the pair nothing."""
    module = sibling_module("export")
    if module is None:
        return
    try:
        module.write_pair(Path(plan["run_dir"]), plan.get("cooperbench_dir"))
    except Exception as problem:  # a derived file must not cost the pair its score
        say(f"{plan['run_id']}: exporting the trajectory failed: {type(problem).__name__}: {problem}")


def abandon(plan, hub):
    """Close the run and remove its containers when a pair stops half way;
    closing twice is harmless, the hub tears an arm down once."""
    try:
        close_run(plan, hub)
    finally:
        teardown(plan)


# The failures that come from the machine, not the pair: Docker, the hub,
# the disk and the processes around them. A pair that hit one did not get
# an answer to its question, so its record says so apart from a setup error.
INFRASTRUCTURE_FAILURES = (drivers.DockerError, HubError, OSError, subprocess.SubprocessError)


def record_failure(plan, hub, problem):
    plan["error"] = f"{type(problem).__name__}: {problem}"
    plan["error_kind"] = "infrastructure" if isinstance(problem, INFRASTRUCTURE_FAILURES) else "setup"
    say(f"{plan['run_id']}: {plan['error']}")
    outcomes = {a["id"]: unmeasured_error(plan["error"]) for a in plan["agents"]}
    try:
        metrics, events, artifacts = close_run(plan, hub)
        write_outputs(plan, outcomes, metrics, events, now(), artifacts)
        teardown(plan)
        conclude(plan)
        save_plan(plan)
    finally:
        scrub_pair(plan)


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


def measured_fields(outcome, driver_measures):
    """The usage fields an agent's record carries as numbers: those the
    outcome says it measured, else all or none by the driver's word."""
    if outcome.measured is not None:
        return set(outcome.measured)
    return set(USAGE_FIELDS) if driver_measures else set()


def model_differs(requested, observed):
    """Whether the agent ran another model than the one asked for; None
    when the run could not see which it ran. A dated id of the model
    asked for (claude-x-20250929 for claude-x) is the same model, which
    Claude Code names by its full id."""
    if not observed or not requested:
        return None
    asked = drivers.bare_model(requested)
    return not (observed == asked or re.fullmatch(re.escape(asked) + r"-\d{8}", observed))


def cooperbench_agent_fields(outcome, measured):
    """The fields coop.py, team.py and solo.py write per agent, in their
    order (feature_id and team_role go before, where they apply). A field
    the driver did not measure is null, never CooperBench's default 0."""
    usage = {name: getattr(outcome, name) if name in measured else None for name in USAGE_FIELDS}
    return {"status": outcome.status, **usage, "patch_lines": len(outcome.patch.splitlines()),
            "error": outcome.error}


def coordbench_agent_fields(plan, agent, outcome, measured):
    """What coordbench adds to an agent's record: its role, Claude Code's
    own clock, how a run that did not submit ended, the model asked for
    and the one run, and what the stream's init event or the transcript
    said the agent ran with."""
    requested = agent_model(plan, agent)
    return {"role": agent["role"], "duration_ms": outcome.duration_ms, "api_duration_ms": outcome.api_duration_ms,
            "end_reason": outcome.end_reason, "model_requested": requested, "model": outcome.model,
            "model_differs": model_differs(requested, outcome.model),
            "claude_code_version": outcome.claude_code_version, "tools": outcome.tools,
            "mcp_servers": outcome.mcp_servers,
            "cost_source": outcome.cost_source if "cost" in measured else None,
            "output_tokens_lower_bound": getattr(outcome, "output_tokens_lower_bound", None),
            "cost_lower_bound": getattr(outcome, "cost_lower_bound", None)}


def agent_record(plan, agent, outcome):
    """One agent's block of result.json: CooperBench's fields, then
    coordbench's. A coop or team agent leads with its feature_id (team
    adds team_role, as team.py does); the solo agent, which solo.py
    writes without one, carries coordbench's id and features among the
    additions instead. duration_ms and api_duration_ms are Claude Code's
    own wall and API time, null when the driver saw no result event."""
    measured = measured_fields(outcome, plan.get("measures_usage", False))
    cooperbench = cooperbench_agent_fields(outcome, measured)
    coordbench = coordbench_agent_fields(plan, agent, outcome, measured)
    features = agent["features"]
    if plan["setting"] == "solo":
        return {**cooperbench, "id": agent["id"], "features": list(features), **coordbench}
    head = {"feature_id": features[0] if len(features) == 1 else features}
    if plan["setting"] == "team":
        head["team_role"] = agent["role"]
    return {**head, **cooperbench, **coordbench}


def usage_totals(agents):
    """total_cost and total_steps as coop.py sums them, each over the
    agents whose figure was measured; null when none was."""
    costs = [record["cost"] for record in agents.values() if record["cost"] is not None]
    steps = [record["steps"] for record in agents.values() if record["steps"] is not None]
    return {"total_cost": sum(costs) if costs else None, "total_steps": sum(steps) if steps else None}


def lead_agent(plan):
    """team.py's lead_agent, the first agent; the team arm names it lead."""
    leads = [agent["id"] for agent in plan["agents"] if agent["role"] == "lead"]
    return leads[0] if leads else (plan["agents"][0]["id"] if plan["agents"] else None)


def agents_seconds(plan):
    """The agents' phase as one span, the first start to the last end:
    the stretch CooperBench's duration_seconds is mostly made of."""
    records = phase_records(plan, "agent")
    return span_seconds(records) if records else None


def cooperbench_result_fields(plan, records, metrics, started, ended):
    """The keys CooperBench's coop.py, team.py and solo.py write, in their
    order: the agents under "agents" (solo's one under "agent"), team's
    lead_agent and team_features, messages_sent for every setting with
    partners (the hub counts one per delivery, as coop_msg.py logs one
    line per recipient) and log_dir."""
    record = {
        "repo": plan["repo"], "task_id": plan["task_id"], "features": plan["features"],
        "setting": plan["setting"], "run_id": plan["run_id"], "run_name": plan["run_name"],
        "agent_framework": plan["agent_framework"], "model": plan["model"], "started_at": plan["started_at"],
        "ended_at": ended.isoformat(), "duration_seconds": (ended - started).total_seconds(),
    }
    if plan["setting"] == "team":
        record["lead_agent"] = lead_agent(plan)
    if plan["setting"] == "solo":
        record["agent"] = next(iter(records.values()), {})
    else:
        record["agents"] = records
    record.update(usage_totals(records))
    if plan["setting"] != "solo":
        record["messages_sent"] = metrics.get("messages_sent")
    if plan["setting"] == "team" and metrics.get("team_features") is not None:
        record["team_features"] = metrics["team_features"]
    record["log_dir"] = plan["run_dir"]
    return record


def error_kind(plan, outcomes):
    """What stopped the pair, or null when nothing did: the runner's
    failure as record_failure classed it, else an agent that ended in Error
    with no failure of the runner's own."""
    if plan.get("error_kind"):
        return plan["error_kind"]
    return "agent" if any(o.status == "Error" for o in outcomes.values()) else None


def result_record(plan, outcomes, metrics, ended):
    started = datetime.fromisoformat(plan["started_at"])
    records = {a["id"]: agent_record(plan, a, outcomes[a["id"]]) for a in plan["agents"]}
    record = cooperbench_result_fields(plan, records, metrics, started, ended)
    record.update({
        "error_kind": error_kind(plan, outcomes),
        "arm": plan["arm_name"], "driver": plan["driver"], "agents_seconds": agents_seconds(plan),
        "isolation": plan.get("isolation"), "options": plan.get("options") or {},
        "agent_models": plan.get("agent_models") or {}, "max_turns": plan["max_turns"],
        "timeout_s": plan["timeout_s"], "disallowed_tools": plan.get("disallowed_tools") or [],
        "provenance": plan.get("provenance"), "image": plan.get("image_facts") or {"image": plan["image"]},
        "metrics": metrics, "timings": plan.get("timings") or [],
        "utc_offset_seconds": plan.get("utc_offset_seconds", utc_offset_seconds()),
    })
    if plan["error"]:
        record["error"] = plan["error"]
    return record


def write_outputs(plan, outcomes, metrics, events, ended, artifacts=None):
    run_dir = Path(plan["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, text in patch_files(plan, outcomes).items():
        write_text(run_dir / name, text)
    result = result_record(plan, outcomes, metrics, ended)
    write_json(run_dir / "result.json", result)
    write_json(run_dir / "metrics.json", metrics)
    write_text(run_dir / "events.jsonl", "".join(json.dumps(e, default=str) + "\n" for e in events))
    write_trajectories(plan, result)
    write_artifacts(plan, artifacts or {})
    try:
        write_conversation(plan, events)
    except Exception as problem:  # a derived file must not cost the pair its score
        plan["warnings"].append(f"writing the conversation files failed: {type(problem).__name__}: {problem}")
    for warning in plan["warnings"]:
        say(f"{plan['run_id']}: {warning}")


# The files CooperBench's runner writes beside result.json, from what the
# run left: the trajectories, the conversation, the arm's artifacts.

def trajectory_names(plan, agent):
    """agent<feature>_traj.json per feature, as coop.py and team.py name
    them, or solo_traj.json; an agent with no feature (a reviewer) by id."""
    if plan["setting"] == "solo":
        return {"solo_traj.json": None}
    if not agent["features"]:
        return {f"{safe_name(agent['id'])}_traj.json": None}
    return {f"agent{feature}_traj.json": feature for feature in agent["features"]}


def trajectory_record(plan, agent, record, feature, messages):
    """The trajectory coop.py, team.py or solo.py writes: the messages are
    CooperBench's parse_session_jsonl of the agent's session transcript."""
    head = {"repo": plan["repo"], "task_id": plan["task_id"]}
    if plan["setting"] == "solo":
        head.update(features=plan["features"], agent_id="solo")
    else:
        head.update(feature_id=feature, agent_id=agent["id"])
        if plan["setting"] == "team":
            head["team_role"] = agent["role"]
    return {**head, "model": agent_model(plan, agent), "status": record.get("status"), "cost": record.get("cost"),
            "steps": record.get("steps"), "messages": messages}


def session_messages(plan, agent):
    path = Path(plan["run_dir"]) / f"{agent_file_id(plan, agent['id'])}_session.jsonl"
    if not path.is_file():
        return []
    return drivers.parse_session_jsonl(path.read_text(encoding="utf-8", errors="replace"))


def write_trajectories(plan, result):
    records = result_agents(result)
    for agent in plan["agents"]:
        record = records.get("solo") if plan["setting"] == "solo" else records.get(agent["id"])
        messages = session_messages(plan, agent)
        for name, feature in trajectory_names(plan, agent).items():
            write_json(Path(plan["run_dir"]) / name, trajectory_record(plan, agent, record or {}, feature, messages))


# The files the runner writes itself, and those the cascade, the export
# and the judge write into the pair directory after it, which an arm's
# artifact may not replace or pre-empt: an artifact named like one is
# refused with a warning.
RUNNER_FILES = {"result.json", "eval.json", "metrics.json", "events.jsonl", "plan.json", "trace.jsonl",
                "conversation.json", "conversation_store.json", "config.json", "summary.json",
                "merge_naive.diff", "merge_union.diff", "merge_llm.diff", "trajectory.json.gz", "judge.json",
                SHARED_COPY, *REMADE_DIRECTORIES}
RUNNER_SUFFIXES = (".patch", "_stream.jsonl", "_session.jsonl", "_prompt.md", "_traj.json",
                   "_session_outcome.json", "_sent.jsonl", ".session-prompt.md")
ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def artifact_refused(name):
    """Why an artifact's name cannot be written into the pair directory,
    or None: a plain file name, and none of the runner's own."""
    if not isinstance(name, str) or not ARTIFACT_NAME.fullmatch(name) or ".." in name:
        return "it is not a plain file name"
    if name in RUNNER_FILES or name.endswith(RUNNER_SUFFIXES):
        return "the runner writes a file of that name itself"
    return None


def write_artifacts(plan, artifacts):
    """What the arm and the hub produced at close, each under its own name
    in the pair directory: text as it is, anything else as JSON."""
    run_dir = Path(plan["run_dir"])
    for name, content in artifacts.items():
        refused = artifact_refused(name)
        if refused:
            plan["warnings"].append(f"the artifact {name!r} was not written: {refused}")
        elif isinstance(content, str):
            write_text(run_dir / name, content)
        else:
            write_json(run_dir / name, content)


def sibling_module(name):
    """A module of coordbench's own that a copy of run.py may run without
    (one deployed alone); None when it is not there, so the files it makes
    are left out with a warning. A module that is there but fails to
    import raises, as any other bug would."""
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded if beside_run(getattr(loaded, "__file__", None)) else None
    spec = importlib.util.find_spec(name)
    if spec is None or not beside_run(spec.origin):
        return None
    return importlib.import_module(name)


def beside_run(origin):
    """Whether a module's file is coordbench's own, beside run.py, and not
    one of the same name from elsewhere on the path; a module with no file
    (one a caller put in sys.modules itself, as the tests do) is taken as
    given."""
    return origin is None or Path(origin).resolve().parent == HERE


def store_document(rows):
    """conversation_store.json in the paper-era store's shape, {messages,
    export_timestamp, total_messages}, whether conversation.store gives the
    rows or the whole document."""
    if isinstance(rows, dict) and "messages" in rows:
        return rows
    rows = list(rows or [])
    return {"messages": rows, "export_timestamp": now().strftime("%Y-%m-%d %H:%M:%S"), "total_messages": len(rows)}


def write_conversation(plan, events):
    """conversation.json (coop.py's shape), one <agent>_sent.jsonl per
    sender that sent anything (the adapter's copy of its send log) and
    conversation_store.json (the paper's message store), derived from the
    run's message events by conversation.py. Solo has no partner, and
    solo.py writes none of them."""
    if plan["setting"] == "solo":
        return
    module = sibling_module("conversation")
    if module is None:
        plan["warnings"].append("conversation.py is not beside run.py, so the conversation files are not written")
        return
    run_dir = Path(plan["run_dir"])
    write_json(run_dir / "conversation.json", module.conversation(events))
    for agent, rows in (module.sent_by(events) or {}).items():
        if rows:
            write_text(run_dir / f"{safe_name(agent)}_sent.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
    write_json(run_dir / "conversation_store.json", store_document(module.store(events)))


def copy_shared_volume(plan):
    """The team scratchpad's last state into <pair_dir>/shared/, copied out
    of an agent's container while the containers still mount it: what the
    agents wrote there (the lead's PLAN.md, the members' handoff patches)
    is in no transcript, and the volume goes with the teardown. It is a
    directory of its own so an agent's <agent>.patch there never meets the
    runner's. A copy that fails is said and costs the pair nothing."""
    resources = plan["resources"]
    if not resources["volume"] or not resources["containers"]:
        return
    destination = Path(plan["run_dir"]) / SHARED_COPY
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    try:
        done = drivers.docker("cp", f"{resources['containers'][0]}:{SHARED_DIR}/.", str(destination), timeout=300)
        problem = None if done.returncode == 0 else (done.stderr or done.stdout or "").strip()[:300]
    except (OSError, subprocess.SubprocessError) as error:
        problem = f"{type(error).__name__}: {error}"
    if problem:
        say(f"{plan['run_id']}: copying {SHARED_DIR} before the teardown failed: {problem}")


def teardown(plan):
    """Remove the run's containers, volume, repository and workspaces; the
    network and the git server are shared, as in CooperBench, and stay.
    The shared volume is copied into the pair directory first, kept or not."""
    resources = plan["resources"]
    copy_shared_volume(plan)
    if plan["keep"]:
        say(f"kept for inspection: {json.dumps(resources)}")
        return
    with timed(plan, "teardown"):
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
              "dataset_dir": plan.get("dataset_dir") or str(Path(plan["cooperbench_dir"]) / "dataset")}
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
    """eval.json with the fields _evaluate_single writes, in its order;
    its setting is solo or coop, as _evaluate_single writes "coop" for
    every merged evaluation, a team pair's included."""
    record = {"repo": plan["repo"], "task_id": plan["task_id"], "features": plan["features"],
              "setting": "solo" if plan["setting"] == "solo" else "coop"}
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
    """CooperBench's evaluation into eval.json, then the merge cascade
    beside it. eval.json is written before the cascade runs, so a pair
    whose cascade is cut short keeps CooperBench's verdict."""
    if plan["error"]:
        return {"error": plan["error"]}
    with timed(plan, "score"):
        record = evaluation_record(plan, run_cooperbench_tests(plan))
    path = Path(plan["run_dir"]) / "eval.json"
    write_json(path, record)
    cascade = cascade_block(plan, record)
    if cascade is not None:
        record["cascade"] = cascade
        write_json(path, record)
    return record


def cascade_settings(plan):
    """What cascade.run is handed beside the plan and the evaluation:
    where CooperBench and its dataset are, the pair's directory and the
    scorer's time limit."""
    return {"cooperbench_dir": plan["cooperbench_dir"],
            "dataset_dir": plan.get("dataset_dir") or str(Path(plan["cooperbench_dir"]) / "dataset"),
            "run_dir": plan["run_dir"], "scoring_timeout_s": SCORING_TIMEOUT_SECONDS}


def cascade_block(plan, evaluation):
    """The cascade (cascade.py's run: the paper's naive, union and learned
    merges, each agent alone, both verdict rules) for a merged pair that
    CooperBench could evaluate; None when there is nothing to add, so
    eval.json is exactly CooperBench's. A cascade that fails says why in
    its block and leaves CooperBench's keys as they are."""
    if plan["setting"] == "solo" or evaluation.get("error"):
        return None
    module = sibling_module("cascade")
    if module is None:
        return None
    try:
        with timed(plan, "cascade"):
            return module.run(plan, evaluation, cascade_settings(plan))
    except Exception as problem:
        return {"error": f"{type(problem).__name__}: {problem}"}


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


def parse_agent_model(text):
    agent, equals, model = text.partition("=")
    if not equals or not agent or not model:
        raise argparse.ArgumentTypeError(f"expected AGENT=MODEL, as agent2=claude-opus-4-6, got {text!r}")
    return agent, model


def parse_tools(text):
    return [word for word in text.replace(" ", "").split(",") if word]


def settings_from(args, default_driver):
    """The command line as settings. The directories are made absolute
    here, once: a relative workspace path in `docker run -v` is read as a
    volume name, and the scorer runs with CooperBench's checkout as its
    working directory while being handed paths under it."""
    cooperbench_dir = Path(args.cooperbench_dir).resolve() if args.cooperbench_dir else cooperbench_home()
    return {
        "arm": args.arm, "driver": args.driver or default_driver, "model": args.model,
        "run_name": args.name or f"coordbench-{arm_name(find_arm(args.arm))}",
        "log_dir": Path(args.log_dir).resolve() if args.log_dir else cooperbench_dir / "logs",
        "cooperbench_dir": cooperbench_dir,
        "dataset_dir": Path(args.dataset_dir).resolve() if args.dataset_dir else cooperbench_dir / "dataset",
        "hub": args.hub, "timeout_s": args.timeout_min * 60,
        "max_turns": args.max_turns, "keep": args.keep, "force": args.force, "options": dict(args.option),
        "arm_dirs": [Path(directory).resolve() for directory in args.arm_dir],
        "agent_models": dict(args.agent_model), "disallowed_tools": args.disallowed_tools or [],
        "concurrency": getattr(args, "concurrency", 1), "scoring_timeout_s": SCORING_TIMEOUT_SECONDS,
    }


def hub_dir(settings):
    return Path(settings["log_dir"]) / settings["run_name"] / "hub"


def run_root(settings):
    """<log_dir>/<run_name>, where config.json and summary.json go."""
    return Path(settings["log_dir"]) / settings["run_name"]


def plan_summary(plan, handles):
    """What run and prepare print for the orchestrating session: where
    the pair is, each agent's prompt file and the model to run it with,
    never the agents' tokens, which the session has no use for and a
    transcript would keep."""
    agents = []
    for agent in plan["agents"]:
        handle = handles.get(agent["id"])
        prompt = handle.get("prompt_file") if isinstance(handle, dict) else None
        agents.append({"id": agent["id"], "prompt_file": prompt or agent.get("prompt_file"),
                       "model": agent_model(plan, agent)})
    return {"run_dir": plan["run_dir"], "agents": agents}


def run_one(settings, hub, driver, repo, task_id, features, task=None):
    """One pair, unless it is finished: its verdict, and its report for
    summary.json (runconfig.pair_report) under the task label CooperBench
    would give it."""
    plan = new_plan(settings, driver, repo, task_id, features)
    task = task or runconfig.task_label(repo, task_id, features)
    finished = finished_run_dir(plan)
    if finished is not None and not settings["force"]:
        plan["run_dir"] = str(finished)
        if (finished / "eval.json").exists():
            return "skipped", runconfig.pair_report(finished, task, "skip", eval_skipped=True)
        return verdict({}, score(plan)), runconfig.pair_report(finished, task, "skip")
    try:
        outcome = run_pair(settings, hub, driver, repo, task_id, features)
    finally:
        close_debug_sinks()
    return outcome, runconfig.pair_report(plan["run_dir"], task, "done")


def run_pair(settings, hub, driver, repo, task_id, features):
    """Provision a pair, run its agents and finish it; returns its verdict."""
    try:
        plan, handles = prepare_pair(settings, hub, driver, repo, task_id, features)
    except PairFailed:
        return "error"
    if getattr(driver, "resumable", False):
        with STDOUT_LOCK:
            print(json.dumps(plan_summary(plan, handles)), flush=True)
    return finish_pair(plan, driver, handles, hub)


def run_listed(settings, hub, driver, pair):
    """One pair of the run's list. A pair that raises is the pair's error,
    reported as CooperBench's runner reports a task whose thread raised,
    and the run goes on to the next."""
    repo, task_id, features = pair
    task = runconfig.task_label(repo, task_id, features)
    try:
        outcome, report = run_one(settings, hub, driver, repo, task_id, sorted(features), task)
    except Exception as problem:
        text = f"{type(problem).__name__}: {problem}"
        say(f"{task}: {text}")
        outcome = "error"
        report = {"entry": {"task": task, "status": "error", "cost": None, "error": text, "eval": None},
                  "eval_skipped": False, "run_dir": None}
    say(f"{repo}/{task_id} {feature_dir_name(features)}: {outcome}")
    return outcome, report


def run_all(settings, hub, driver, pairs):
    """Every pair, one after another, or --concurrency at once on a thread
    pool as CooperBench's _run_with_progress runs its tasks, reported in
    the order they end."""
    if settings["concurrency"] <= 1:
        return [run_listed(settings, hub, driver, pair) for pair in pairs]
    with ThreadPoolExecutor(max_workers=settings["concurrency"]) as pool:
        futures = [pool.submit(run_listed, settings, hub, driver, pair) for pair in pairs]
        return [future.result() for future in as_completed(futures)]


def requested_pairs(args, settings):
    """The pairs the command line names: one --features pair of --repo and
    --task, or every pair of --subset, narrowed by either when given, and
    by --reference-merge when that is given."""
    if args.features:
        if not args.repo or args.task is None:
            raise ValueError("--features needs --repo and --task")
        pairs = [(args.repo, args.task, args.features)]
    else:
        pairs = subset_tasks(settings["dataset_dir"], args.subset, args.repo, args.task)
        if not pairs:
            raise ValueError(f"the {args.subset} subset names no pair" + (" of that task" if args.repo else ""))
    if getattr(args, "reference_merge", None):
        pairs = by_reference_merge(pairs, settings["dataset_dir"], args.reference_merge)
    return pairs


def by_reference_merge(pairs, dataset_dir, merge):
    """The pairs whose reference patches merge as merge asks, clean or
    conflicting, by CooperBench's gold_conflict_report.json. The choice is
    made on what the gold patches do, never on what an agent did."""
    report = read_json(Path(dataset_dir) / "gold_conflict_report.json")
    if not isinstance(report, dict) or not isinstance(report.get("all_results"), list):
        raise ValueError(f"--reference-merge needs gold_conflict_report.json in {dataset_dir}")
    conflicts = {(row["repo"], int(row["task_id"]), tuple(sorted((row["f1"], row["f2"])))): row["has_conflict"]
                 for row in report["all_results"]}
    kept = []
    for repo, task_id, features in pairs:
        key = (repo, int(task_id), tuple(sorted(features)))
        if key not in conflicts:
            raise ValueError(f"the reference report has no result for {repo}/{task_id} {feature_dir_name(features)}")
        if conflicts[key] == (merge == "conflicting"):
            kept.append((repo, task_id, features))
    if not kept:
        raise ValueError(f"no pair of the request merges {merge} with the reference patches")
    return kept


def check_docker():
    """The Docker daemon must answer before a run makes any directory: with
    it down every pair fails at network set-up and leaves an error record
    to clear by hand, as the pilot's first batch did."""
    try:
        answer = drivers.docker("info", timeout=60)
    except (OSError, subprocess.SubprocessError):
        answer = None
    if answer is None or answer.returncode != 0:
        raise ValueError("the Docker daemon does not answer `docker info`; bash bench/cooperbench/setup.sh starts it")


def check_agent_models(settings, pairs):
    """Every --agent-model names an agent the arm lays out, so a typo is
    refused before anything runs instead of running the run's model."""
    arm = find_arm(settings["arm"])
    known = [spec.id for spec in arm.layout(sorted(pairs[0][2]))]
    unknown = sorted(set(settings.get("agent_models") or {}) - set(known))
    if unknown:
        raise ValueError(f"--agent-model names {', '.join(unknown)}, which the arm does not lay out "
                         f"({', '.join(known)})")


def check_concurrency(settings):
    if settings["concurrency"] < 1:
        raise ValueError("--concurrency takes a number of pairs, 1 or more")
    if settings["concurrency"] > 1 and trace.level == "debug":
        raise ValueError("--debug traces one pair at a time: trace.py has one stream per process, so with "
                         "--concurrency above 1 every pair's trace.jsonl would hold every pair's events")


def run_setting(settings, pairs):
    """The run's setting, from the arm's layout of its first pair."""
    arm = find_arm(settings["arm"])
    return pair_setting(arm, arm.layout(sorted(pairs[0][2])))


def write_run_config(settings, driver, pairs, replace=True):
    """config.json for the run, with each task image as docker holds it."""
    images = {}
    for repo, task_id, _ in pairs:
        images.setdefault(image_name(repo, task_id), None)
    images = {image: runconfig.image_facts(image) for image in images}
    record = runconfig.config_record(settings, driver, arm_name(find_arm(settings["arm"])),
                                     run_setting(settings, pairs), pairs, images)
    runconfig.write_config(run_root(settings), record, replace=replace)


def summary_extras(arm, driver, model, setting, run_dirs, time_source):
    return {"arm": arm, "driver": driver, "model": model, "setting": setting, "total_time_source": time_source,
            "claude_code": runconfig.claude_code_digest(run_dirs)}


def summary_line(record):
    """CooperBench's closing lines in one: the runs, the evals with its
    console's rate (passed over everything evaluated), the cost."""
    tally = record["eval"]
    cost = "unknown" if record["total_cost"] is None else f"${record['total_cost']:.2f}"
    infrastructure = record.get("infrastructure_errors", 0)
    failed = f"{record['failed']} failed" + (f" ({infrastructure} infrastructure)" if infrastructure else "")
    return (f"summary: {record['completed']} completed, {record['skipped']} skipped, {failed}; "
            f"evals: {tally['total_evaluated']} evaluated, {tally['passed']} passed, {tally['failed']} failed, "
            f"{tally['errors']} errors ({100 * record['pass_rate_over_evaluated']:.1f}%); cost: {cost}")


def write_run_summary(settings, setting, reports, seconds):
    """summary.json for this session's pairs, CooperBench's _save_summary."""
    run_dirs = [report["run_dir"] for report in reports if report["run_dir"]]
    totals = runconfig.run_totals(run_root(settings) / setting)
    extras = summary_extras(arm_name(find_arm(settings["arm"])), settings["driver"], settings["model"], setting,
                            run_dirs, "session")
    record = runconfig.summary_record(settings["run_name"], reports, seconds, totals, extras)
    runconfig.write_summary(run_root(settings), record)
    say(summary_line(record))


def refresh_summary(plan):
    """summary.json once finish has scored a pair: the session driver runs
    a run pair by pair, so the summary is rebuilt from every pair of the
    setting on disk, and its time is get_run_totals' wall time, the span
    CooperBench prints (marked approximate) when a session skipped pairs."""
    setting_dir = Path(plan["run_dir"]).parents[2]
    reports = runconfig.reports_from_disk(setting_dir)
    totals = runconfig.run_totals(setting_dir)
    extras = summary_extras(plan["arm_name"], plan["driver"], plan["model"], plan["setting"],
                            [report["run_dir"] for report in reports], "run_totals wall_time")
    record = runconfig.summary_record(plan["run_name"], reports, totals["wall_time"], totals, extras)
    runconfig.write_summary(setting_dir.parent, record)
    say(summary_line(record))


def warn_about_credentials(driver):
    if isinstance(driver, drivers.ClaudeCliDriver) and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        say("neither CLAUDE_CODE_OAUTH_TOKEN nor COORDBENCH_CLAUDE_TOKEN is set, so the agents cannot sign in")


def command_run(args):
    settings = settings_from(args, "claude-cli")
    pairs = requested_pairs(args, settings)
    check_docker()
    check_concurrency(settings)
    check_agent_models(settings, pairs)
    driver = drivers.load_driver(settings["driver"])
    warn_about_credentials(driver)
    began = time.time()
    with timed(settings, "hub"):
        hub, process = connect_hub(settings)
    try:
        write_run_config(settings, driver, pairs)
        results = run_all(settings, hub, driver, pairs)
    finally:
        if process is not None:
            stop_hub(process)
            scrub_hub(hub_dir(settings))
    write_run_summary(settings, run_setting(settings, pairs), [report for _, report in results], time.time() - began)
    return 1 if any(outcome == "error" for outcome, _ in results) else 0


def command_prepare(args):
    settings = settings_from(args, "session")
    driver = drivers.load_driver(settings["driver"])
    if not getattr(driver, "resumable", False):
        raise SystemExit(f"prepare needs a driver whose wait can run in another process, "
                         f"and {settings['driver']} is not one")
    check_docker()
    check_agent_models(settings, [(args.repo, args.task, args.features)])
    with timed(settings, "hub"):
        hub, process = connect_hub(settings)
    try:
        # One pair of a run the session drives pair by pair: the run's
        # config.json is the first prepare's, kept by the rest.
        write_run_config(settings, driver, [(args.repo, args.task, args.features)], replace=False)
        plan, handles = prepare_pair(settings, hub, driver, args.repo, args.task, args.features)
    except PairFailed as failure:
        if process is not None:
            stop_hub(process)
        say(f"prepare failed: {failure}")
        return 1
    finally:
        close_debug_sinks()
    if process is not None:
        plan["hub_pid"] = process.pid
        save_plan(plan)
    print(json.dumps(plan_summary(plan, handles)), flush=True)
    return 0


def command_finish(args):
    plan = load_plan(args.run_dir)
    driver = drivers.load_driver(plan["driver"])
    handles = {a["id"]: a["handle"] for a in plan["agents"] if a.get("handle") is not None}
    open_debug_sink(plan["run_dir"])
    try:
        outcome = finish_pair(plan, driver, handles, finish_client(plan))
    finally:
        if plan.get("hub_pid"):
            stop_hub_pid(plan["hub_pid"], plan["hub_url"])
            scrub_hub(pair_hub_dir(plan))
    save_plan(plan)
    close_debug_sinks()
    refresh_summary(plan)
    print(json.dumps({"run_dir": plan["run_dir"], "verdict": outcome}), flush=True)
    return 0 if outcome != "error" else 1


def rescore_dirs(path):
    """The coordbench pair directories at or below path: those with a
    result.json and the plan.json the runner keeps beside it."""
    path = Path(path)
    found = [path] if (path / "result.json").is_file() else sorted(p.parent for p in path.rglob("result.json"))
    return [found_dir for found_dir in found if (found_dir / "plan.json").is_file()]


def needs_scoring(run_dir, plan, force):
    """Whether rescore scores the pair: always with --force, else when it
    has no eval.json, as `cooperbench eval` decides, or when a merged
    pair's eval.json has no cascade block yet."""
    evaluation = read_json(Path(run_dir) / "eval.json")
    if force or not isinstance(evaluation, dict):
        return True
    return plan["setting"] != "solo" and "cascade" not in evaluation


def rescore_pair(run_dir, overrides, force):
    """One pair scored again from its patches; the outcome, or None when
    it needs none. The plan comes from the pair's plan.json, with its
    run_dir where the pair is now (logs move between hosts) and any
    --cooperbench-dir or --dataset-dir given; no hub, container or agent
    is started, and only eval.json and the trajectory export change."""
    plan = load_plan(run_dir)
    plan.update(run_dir=str(run_dir), **overrides)
    if not needs_scoring(run_dir, plan, force):
        return None
    evaluation = score(plan)
    export_pair(plan)
    return verdict({}, evaluation)


def command_rescore(args):
    """CooperBench's `cooperbench eval` for coordbench's logs: score the
    finished pairs under --run-dir again, eval.json and its cascade, and
    start no agent, whatever state a pair is in. config.json, result.json
    and summary.json stay the run's own."""
    overrides = {key: str(value) for key, value in (("cooperbench_dir", args.cooperbench_dir),
                                                     ("dataset_dir", args.dataset_dir)) if value}
    run_dirs = rescore_dirs(args.run_dir)
    if not run_dirs:
        say(f"no coordbench pair (a result.json with its plan.json) at or below {args.run_dir}")
        return 1
    outcomes = {}
    for run_dir in run_dirs:
        outcome = rescore_pair(run_dir, overrides, args.force) or "skipped"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        say(f"{run_dir}: {outcome}")
    say("rescored: " + ", ".join(f"{count} {outcome}" for outcome, count in sorted(outcomes.items())))
    return 1 if "error" in outcomes else 0


def add_pair_arguments(parser, subset):
    """--repo and --task name the pair's task; with --subset (run only)
    they narrow the subset instead, and either may be left out."""
    parser.add_argument("--arm", required=True, help="a built-in arm's name or an arm's directory")
    parser.add_argument("--repo", required=not subset, help="the dataset's repository, as pallets_click_task")
    parser.add_argument("--task", required=not subset, type=int, help="the task's number, as 2068")
    pairs = parser.add_mutually_exclusive_group(required=True)
    pairs.add_argument("--features", type=parse_features, help="the features, as 1,6")
    if subset:
        pairs.add_argument("--subset", help="every pair the subset names (as flash), of every task or of the "
                                            "--repo and --task given")
        parser.add_argument("--reference-merge", choices=("clean", "conflicting"),
                            help="only the pairs whose reference patches merge clean, or conflict, by "
                                 "CooperBench's gold_conflict_report.json")
        parser.add_argument("--concurrency", type=int, default=1,
                            help="how many pairs run at once (default 1; CooperBench's runner runs 20)")
    parser.add_argument("--driver", help="claude-cli, session, or path/to/driver.py:Class")
    parser.add_argument("--model", default=os.environ.get("MODEL") or "claude-sonnet-5-5",
                        help="the agents' model (default $MODEL or claude-sonnet-5-5, as run_arms.sh)")
    parser.add_argument("--agent-model", action="append", type=parse_agent_model, default=[],
                        metavar="AGENT=MODEL", help="one agent's own model, as agent2=claude-opus-4-6, for "
                                                    "cross-play pairs; repeatable")
    parser.add_argument("--name", help="the run's name in the logs (default coordbench-<arm>)")
    parser.add_argument("--log-dir", help="the logs' root (default <cooperbench>/logs)")
    parser.add_argument("--cooperbench-dir",
                        help="CooperBench's checkout (default $COOPERBENCH_DIR or ~/cooperbench)")
    parser.add_argument("--dataset-dir", help="the dataset the specs and the scorer read (default "
                                              "<cooperbench>/dataset), as a 59bbf65 worktree's for paper-era runs")
    parser.add_argument("--hub", help="a running hub's URL as this host reaches it, sent $COORD_ADMIN_TOKEN "
                                      "when set; else one is started, with a token of its own")
    parser.add_argument("--arm-dir", action="append", default=[], metavar="DIR",
                        help="a directory whose arms a hub started here may load, beside the built-in "
                             "ones and the --arm directory; repeatable")
    parser.add_argument("--timeout-min", type=float, default=120, help="each agent's time limit")
    parser.add_argument("--max-turns", type=int,
                        help="each agent's turn limit (default none, as 63b9d44; 100 approximates the paper's "
                             "action cap)")
    parser.add_argument("--disallowed-tools", type=parse_tools, metavar="TOOL,TOOL",
                        help="Claude Code tools the agents may not use, as WebFetch,WebSearch for the paper's "
                             "local operations only")
    parser.add_argument("--option", action="append", type=parse_option, default=[], metavar="KEY=VALUE",
                        help="a run option for the arm, the value read as JSON when it parses; "
                             "git_remote or shared_dir asks for that need, which the runner then "
                             "provisions and sets the options of itself")
    parser.add_argument("--keep", action="store_true", help="leave containers, volume and repository in place")
    parser.add_argument("--force", action="store_true", help="run a pair again even when it is finished")
    add_debug_argument(parser)


def add_debug_argument(parser):
    parser.add_argument("--debug", action="store_true",
                        help="trace every docker command, hub call, file written and marker poll besides the "
                             "phases, to <run_dir>/trace.jsonl and the listeners (trace.py's debug level)")


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
    add_debug_argument(finishing)
    finishing.set_defaults(func=command_finish)
    rescoring = commands.add_parser("rescore", help="score finished pairs again from their patches, starting no agent")
    rescoring.add_argument("--run-dir", required=True, help="a pair directory, or a run or setting directory above pairs")
    rescoring.add_argument("--force", action="store_true",
                           help="score pairs that already have an eval.json with a cascade (else only those without)")
    rescoring.add_argument("--cooperbench-dir", help="the CooperBench checkout to score with, instead of the plan's")
    rescoring.add_argument("--dataset-dir", help="the dataset to take the tests from, instead of the plan's")
    add_debug_argument(rescoring)
    rescoring.set_defaults(func=command_rescore)
    scrubbing = commands.add_parser("scrub", help="scrub a run directory once more and say whether it may be published")
    scrubbing.add_argument("--run-dir", required=True, help="the run directory a job uploads")
    add_debug_argument(scrubbing)
    scrubbing.set_defaults(func=command_scrub)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    drivers.adopt_token_alias()
    if args.debug:
        trace.set_level("debug")
    try:
        return args.func(args)
    except (HubError, CredentialKept, FileNotFoundError, ValueError) as problem:
        say(str(problem))
        return 2


if __name__ == "__main__":
    sys.exit(main())
