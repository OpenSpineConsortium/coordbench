"""The plug-in contract: what a coordination arm is, and the run it acts on.

A coordination arm is one way of wiring agents together: solo, messages
only, a shared git remote, a team with a lead, the claim protocol of this
repository, or anything new. It is a directory that is at once a Claude
Code plugin and a coordbench plug-in:

    my-arm/
      .claude-plugin/plugin.json   the plugin's name and description
      .mcp.json                    the hub's MCP endpoint, so any Claude
                                   Code that loads the plugin gets the tools
      skills/<name>/SKILL.md       the protocol, in words, for the agents
      arm.json                     coordbench's part: the entry point and
                                   what the arm needs from the runner
      arm.py                       a subclass of Arm, below

The hub (hub.py) loads the arm, creates a Run per task pair, and serves the
arm's tools to every agent over MCP, over a stdio bridge for agents that
only speak stdio, and as shell commands. The runner (run.py) gives each
agent the arm's prompt, provisions what the arm needs (a shared git remote,
a shared scratchpad), starts the agents with a driver, and scores what
they leave with CooperBench's own evaluation. An arm therefore says only
how agents coordinate; tasks, agents and scoring stay the same across arms,
which is what makes two arms comparable.

Everything here is the Python standard library, so the hub runs anywhere
Python 3.11 does.
"""

import importlib.util
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SUBMISSION_BLOCK = """## Submission protocol

When you are done editing the codebase, write your final unified diff to
`/workspace/repo/patch.txt` BEFORE exiting.  The bench evaluator reads
that file; nothing else is inspected.  A typical pattern:

```bash
cd /workspace/repo
git diff > patch.txt
cat patch.txt   # sanity-check that the diff is what you intend to submit
```

Constraints on `patch.txt`:

- Must be a unified diff (`git diff` output is fine).
- Should contain ONLY source files you intentionally modified to implement
  the feature.  Exclude reproduction scripts, scratch tests, or
  helper files you wrote during development.
- Do not include changes to test files unless the task explicitly asks
  you to modify tests.

You are free to read files, run shell commands, and run tests as needed."""
# The block above is CooperBench's own (src/cooperbench/agents/_coop/
# prompt.py at 63b9d44, MIT licence), kept word for word so every arm asks
# for its result the same way the authors' runs did.


class ToolError(Exception):
    """A tool refused its call; the agent sees the message as an error result.
    exit_code is what the tool's shell command exits with, so a port of a
    command whose callers branch on its code (CooperBench's coop-task-claim
    exits 2 when someone else holds the task) can keep the code."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


# What the runner provides for each need, as the run options that say
# where it is; the hub refuses a run whose arm needs something the options
# do not provide, rather than let a prompt promise what is not there.
NEED_OPTIONS = {"git_remote": ("git_remote",), "shared_volume": ("shared_dir",)}


def unmet_needs(arm, options):
    options = options or {}
    return [need for need in arm.needs
            if not all(options.get(name) for name in NEED_OPTIONS.get(need, ()))]


@dataclass(frozen=True)
class AgentSpec:
    """One agent of a run: its id, its role in the arm, and the features
    (by number) it is asked to implement."""

    id: str
    role: str
    features: tuple


@dataclass
class Call:
    """One tool call: the run, the calling agent and its arguments."""

    run: "Run"
    agent: str
    arguments: dict

    def arg(self, name, default=None, required=False):
        value = self.arguments.get(name, default)
        if required and value in (None, ""):
            raise ToolError(f"{name} is required")
        return value


@dataclass
class Tool:
    """A coordination tool. The handler returns text (or a dict, sent as
    JSON text) or raises ToolError. `argv` maps a shell command's
    positional words to arguments, the last name ending in "..." taking
    the rest of the line, so `alias` can stand in for CooperBench's
    commands (coop-send <to> <text...>). `flags` maps a shell flag to the
    argument it sets (--note to "note"); a flag whose argument is a boolean
    in the schema is a switch, any other takes the next word."""

    name: str
    description: str
    handler: Callable[[Call], object]
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    roles: tuple = ()
    alias: str = ""
    argv: tuple = ()
    flags: dict = field(default_factory=dict)

    def describe(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


class Run:
    """The shared state of one task pair under one arm. The hub serves it
    from many threads at once, so every change goes through `lock`, a
    condition that blocking tools wait on and every change wakes."""

    def __init__(self, run_id, arm, agents, options=None, events_path=None, specs=None):
        self.id = run_id
        self.arm = arm
        self.agents = {a.id: a for a in agents}
        self.options = dict(options or {})
        self.specs = dict(specs or {})
        self.state = {}
        self.lock = threading.Condition()
        self.events = []
        self.events_path = Path(events_path) if events_path else None
        self.started = time.time()
        self.closed = False

    def emit(self, kind, agent=None, **data):
        """Record one coordination event; the metrics come from these."""
        with self.lock:
            event = {"seq": len(self.events), "t": round(time.time() - self.started, 3),
                     "kind": kind, "agent": agent, **data}
            self.events.append(event)
            if self.events_path:
                with self.events_path.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(event) + "\n")
            self.lock.notify_all()
        return event

    def wait_until(self, ready, timeout):
        """Block until ready() holds or the timeout (seconds) passes;
        ready() runs under the lock."""
        with self.lock:
            return self.lock.wait_for(ready, timeout=max(0.0, timeout))

    def peers(self, agent):
        return [a for a in self.agents if a != agent]

    def role_of(self, agent):
        return self.agents[agent].role


class Arm:
    """Subclass this to make an arm. Override what the arm changes:

    - layout(features): the agents and their roles; one per feature by
      default, agent1, agent2 and so on in the order of the features,
      every agent a "peer";
    - setup(run): seed the run's state, such as a task list;
    - tools(run, agent): the tools this agent gets;
    - prompt(run, agent, specs): what the agent is told, given each
      feature's spec by number;
    - metrics(run): numbers worth keeping, from run.events and run.state;
    - teardown(run): free what setup or the tools took (a scratch clone, a
      thread), called once when the run closes, outside run.lock.

    run.specs holds each feature's spec by number from setup() on.

    `needs` tells the runner what to provision: "git_remote" (a bare
    repository every agent can push to and fetch from, as the remote
    `team`) and "shared_volume" (one directory mounted in every agent's
    container at /workspace/shared).
    """

    name = "arm"
    description = ""
    needs = ()

    def __init__(self, root=None, config=None):
        self.root = Path(root) if root else None
        self.config = dict(config or {})

    def layout(self, features):
        # Numbered by position, as CooperBench numbers its agents; the
        # patch files are named by feature, which the runner takes care of.
        return [AgentSpec(f"agent{i}", "peer", (n,)) for i, n in enumerate(features, 1)]

    def setup(self, run):
        pass

    def tools(self, run, agent):
        return []

    def prompt(self, run, agent, specs):
        mine = run.agents[agent].features
        parts = [specs[n].strip() for n in mine]
        return "\n\n".join(parts + [SUBMISSION_BLOCK])

    def teardown(self, run):
        pass

    def metrics(self, run):
        kinds = {}
        for event in run.events:
            kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
        return {"events": kinds}

    def tools_for(self, run, agent):
        """The tools of this agent's role, by name."""
        role = run.role_of(agent)
        return {t.name: t for t in self.tools(run, agent) if not t.roles or role in t.roles}


def load_arm(path):
    """Load the arm in a directory from its arm.json: {"entry": "arm.py:ClassName", ...}.
    The rest of arm.json is handed to the arm as its config."""
    root = Path(path).resolve()
    config = json.loads((root / "arm.json").read_text(encoding="utf-8"))
    module_file, _, class_name = config["entry"].partition(":")
    spec = importlib.util.spec_from_file_location(f"coordbench_arm_{root.name.replace('-', '_')}",
                                                  root / module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arm = getattr(module, class_name)(root, config)
    if not isinstance(arm, Arm):
        raise TypeError(f"{class_name} in {root} is not an Arm")
    return arm


def builtin_arms_dir():
    return Path(__file__).resolve().parent / "arms"


def find_arm(name_or_path):
    """An arm by the name of a built-in one, or by its directory."""
    candidate = Path(name_or_path)
    if (candidate / "arm.json").exists():
        return load_arm(candidate)
    builtin = builtin_arms_dir() / name_or_path
    if (builtin / "arm.json").exists():
        return load_arm(builtin)
    raise FileNotFoundError(f"no arm named {name_or_path!r} and no arm.json under it")
