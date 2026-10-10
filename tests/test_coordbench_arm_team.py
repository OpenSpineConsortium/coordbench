"""The team arm is CooperBench's team mode re-made as a plug-in, and the
best arm of the authors' ablation, so a run through coordbench is worth
setting beside theirs only if the arm is theirs. These tests pin what
makes it so: the lead is chosen and the list seeded as runner/team.py
does, a claim is atomic and refuses another agent's task as coop_task.py
does, updates, listing and typed requests behave, print and exit as the
in-container commands do (checked against coop_task.py itself when
CooperBench's source is at hand), a request's wait is booked as time
spent on another agent for the hub's tool_done event, each tool answers
to CooperBench's command and options, the prompt is
build_team_instruction's text (checked against CooperBench's own
function likewise), the metrics are metrics.py's, and the directory is a
well-formed Claude Code plugin as well as an arm."""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
TEAM = COORDBENCH / "arms" / "team"
sys.path.insert(0, str(COORDBENCH))

import cli  # noqa: E402
import hub as hub_module  # noqa: E402
import mcp  # noqa: E402
from arm import SUBMISSION_BLOCK, Call, Run, find_arm  # noqa: E402

RULE = "\n\n---\n\n"
DASH = "\u2014"
# Unstripped, with a trailing newline, as CooperBench reads feature.md.
SPECS = {3: "# Verbose flag\n\nAdd a --verbose flag.\n", 7: "# TOML\n\nRead TOML configs.\n",
         9: "# Colours\n\nColour the help.\n"}
# What the runner gives a run of an arm that needs the shared volume; the
# hub refuses a team run without it.
SHARED = {"shared_dir": "/workspace/shared"}

ALIASES = {"task_create": "coop-task-create", "task_claim": "coop-task-claim",
           "task_update": "coop-task-update", "task_list": "coop-task-list",
           "request": "coop-request", "respond": "coop-respond", "pending": "coop-pending",
           "send_message": "coop-send", "broadcast": "coop-broadcast",
           "receive_messages": "coop-recv", "peek_messages": "coop-peek",
           "list_agents": "coop-agents", "wait_for_message": ""}
# coop_task.py's positionals and options, by the tool that stands in for
# each of its subcommands: one word each, none taking the rest of the
# line, so a title of several words wants its quotes as it does there.
ARGV = {"task_create": ["title"], "task_claim": ["task_id"], "task_update": ["task_id", "status"],
        "task_list": [], "request": ["recipient", "kind", "body"], "respond": ["request_id", "body"],
        "pending": []}
FLAGS = {"task_create": {"--assign": "assign"}, "task_claim": {},
         "task_update": {"-n": "note", "--note": "note"}, "task_list": {"--mine": "mine", "--open": "open"},
         "request": {"--wait": "wait"}, "respond": {}, "pending": {}}
TASK_FIELDS = ["id", "title", "owner", "status", "created_by", "created_at", "last_note", "metadata"]

# team_harness/prompt.py's _TEAM_LIST_USAGE, copied here so the test does
# not take the arm's word for it.
TEAM_LIST_USAGE = """Available shell commands (Redis-backed, atomic):

```bash
coop-task-create "<title>"                 # creates an open, unassigned task; prints task_id
coop-task-create --assign <agent> "<title>" # creates and pre-assigns; agent must still claim
coop-task-claim <task_id>                   # exit 0 if you got it, 2 if someone else owns it
coop-task-update <task_id> <status> [-n "<note>"]
                                            # statuses: open | in_progress | blocked | done
coop-task-list                              # JSON list of every task in the team
coop-task-list --mine                       # tasks you own
coop-task-list --open                       # open, unassigned tasks
```

Messaging (`coop-send` / `coop-recv` / `coop-broadcast` / `coop-peek`)
also works and is the right tool for short questions that don't
warrant a new task."""

MCP_SENTENCE = (
    "Every coop-* command is also a tool of the MCP server `coordbench`, with the same task list "
    "and inbox behind it: `coop-task-create` is `task_create`, `coop-task-claim` is `task_claim`, "
    "`coop-task-update` is `task_update`, `coop-task-list` is `task_list`, `coop-request` is "
    "`request`, `coop-respond` is `respond`, `coop-pending` is `pending`, `coop-send` is "
    "`send_message`, `coop-broadcast` is `broadcast`, `coop-recv` is `receive_messages`, "
    "`coop-peek` is `peek_messages`, and `coop-agents` is `list_agents`.")

LEAD_EXCERPTS = [
    "## You are the team-lead\n\nYou are **agent1** \u2014 the team-lead.  Members reporting to you:\n"
    "`agent2`, `agent3`.",
    "### The integration step is the WHOLE point of your job \u2014 DO NOT SKIP IT",
    "2. Break the spec into 2-4 concrete tasks and assign one per member\n"
    "   with `coop-task-create --assign <agent> \"...\"`.",
    "- [ ] `git diff` now shows BOTH features \u2014 your own and every\n"
    "      member's \u2014 present in the working tree.",
    "A shared scratchpad volume is mounted at `/workspace/shared/` for\n"
    "coordination \u2014 files there are NOT evaluated.  Drop design notes,\n"
    "interface contracts, or member-produced patches there so the whole\n"
    "team can see them with `ls /workspace/shared/`.",
]
MEMBER_EXCERPTS = [
    "## You are a team member\n\nYou are **agent2**.  The team-lead is **agent1**, who will\n"
    "organize work into tasks for you to claim.",
    "2. `coop-task-claim <task_id>`.  If you lose the race (exit code 2),\n   pick another.",
    "   `coop-send agent1 \"blocked on task <id>: ...\"`.",
    "   cd /workspace/repo && git diff > /workspace/shared/agent2.patch",
    "### Final submission \u2014 REQUIRED",
    "A shared scratchpad volume is mounted at `/workspace/shared/` for\n"
    "coordination \u2014 files there are NOT evaluated.  Use it for anything\n"
    "your peers might need to see \u2014 partial diffs, interface sketches,\n"
    "error logs from your reproduction script.",
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEAM_MODULE = load_module("coordbench_arm_team_under_test", TEAM / "arm.py")


def cooperbench_source():
    """CooperBench's package directory, from a checkout named by
    COOPERBENCH_DIR (the variable bench/cooperbench/setup.sh uses) or at
    ~/cooperbench, or None when the source is not here."""
    for root in (os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")):
        source = Path(root or "/nonexistent") / "src" / "cooperbench"
        if (source / "team_harness").is_dir():
            return source
    return None


def cooperbench_team_prompt():
    """CooperBench's team_harness/prompt.py, with the one module it imports
    (_coop/prompt.py) put in place of CooperBench's package, whose own
    imports need redis and modal. Returns the module and the stand-in
    modules its git branch imports at call time, or None."""
    source = cooperbench_source()
    coop_file = source and source / "agents" / "_coop" / "prompt.py"
    team_file = source and source / "team_harness" / "prompt.py"
    if not (source and coop_file.is_file() and team_file.is_file()):
        return None
    packages = ("cooperbench", "cooperbench.agents", "cooperbench.agents._coop")
    stand_ins = {name: types.ModuleType(name) for name in packages}
    stand_ins["cooperbench.agents._coop.prompt"] = load_module("cooperbench_coop_prompt", coop_file)
    with mock.patch.dict(sys.modules, stand_ins):
        return load_module("cooperbench_team_prompt", team_file), stand_ins


def cooperbench_coop_task():
    """CooperBench's in-container command, team_harness/coop_task.py, loaded
    with an empty stand-in for the redis package it imports; each test
    hands it a FakeRedis in place of a server. None when the source is
    not here."""
    source = cooperbench_source()
    path = source and source / "team_harness" / "coop_task.py"
    if not (path and path.is_file()):
        return None
    with mock.patch.dict(sys.modules, {"redis": types.ModuleType("redis")}):
        return load_module("cooperbench_coop_task", path)


COOPERBENCH = cooperbench_team_prompt()
COOP_TASK = cooperbench_coop_task()
NO_SOURCE = "CooperBench's source is not here; set COOPERBENCH_DIR to its checkout"


class FakeRedis:
    """The Redis commands coop_task.py uses, over dicts, so CooperBench's own
    command runs here without a server. Values are kept as the strings
    Redis would hold; blpop answers at once, as a wait that ran out."""

    def __init__(self):
        self.hashes, self.sets, self.lists = {}, {}, {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({field: str(value) for field, value in mapping.items()})

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def exists(self, key):
        return int(key in self.hashes)

    def delete(self, key):
        self.hashes.pop(key, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, ()))

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key, start, end):
        return list(self.lists.get(key, ()))

    def blpop(self, keys, timeout=0):
        for key in keys:
            if self.lists.get(key):
                return key, self.lists[key].pop(0)
        return None


def new_run(features=(3, 7, 9), options=None, run_id="r1"):
    arm = find_arm(str(TEAM))
    run = Run(run_id, arm, arm.layout(list(features)), options)
    arm.setup(run)
    return arm, run


def call(run, agent, tool, **arguments):
    """A tool call as the hub makes it, answered as /rpc answers it."""
    return hub_module.Hub().call(run, agent, tool, arguments)


def refusal(text, exit_code):
    return {"text": text, "isError": True, "exitCode": exit_code}


def exit_code(answer):
    """What the tool's shell command exits with, given its /rpc answer."""
    return answer["exitCode"] if answer["isError"] else 0


def text_of(run, agent, tool, **arguments):
    answer = call(run, agent, tool, **arguments)
    if answer["isError"]:
        raise AssertionError(f"{tool} refused: {answer['text']}")
    return answer["text"]


def shell_arguments(description, words, stdin=None):
    """A command's words as the arguments of its tool, read as arm.py's
    contract says a shell command reads them: a word naming one of the
    tool's flags sets that flag's argument, as a switch when the schema
    makes it a boolean and from the next word otherwise, and the other
    words go onto argv as cli.py maps them, the text on stdin standing in
    for a trailing argument no word was left for."""
    flags = description.get("flags") or {}
    properties = description["inputSchema"].get("properties", {})
    arguments, rest, queue = {}, [], list(words)
    while queue:
        word = queue.pop(0)
        name = flags.get(word)
        if name is None:
            rest.append(word)
        elif properties.get(name, {}).get("type") == "boolean":
            arguments[name] = True
        elif queue:
            arguments[name] = cli.coerce(description, name, queue.pop(0), label=word)
        else:
            raise cli.Failure(f"{word} takes a value")
    with mock.patch.object(cli, "stdin_has_text", return_value=stdin is not None), \
            mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
        return {**cli.words_to_arguments(description, rest), **arguments}


def shell(run, agent, tool, *words, stdin=None):
    """A shell command: its words read against the tool as the hub
    describes it to cli.py, then the same /rpc call."""
    hub = hub_module.Hub()
    description = next(t for t in hub.describe_tools(run, agent) if t["name"] == tool)
    return hub.call(run, agent, tool, shell_arguments(description, words, stdin))


def tasks_of(run, agent="agent1", **filters):
    return json.loads(text_of(run, agent, "task_list", **filters))


def task_titled(run, title):
    return next(task for task in tasks_of(run) if task["title"] == title)


def board(run):
    return run.state["team"]


class LayoutAndSeeding(unittest.TestCase):
    def test_the_agent_with_the_lowest_feature_is_the_lead(self):
        arm = find_arm(str(TEAM))
        layout = [(a.id, a.role, a.features) for a in arm.layout([9, 3, 7])]
        self.assertEqual(layout, [("agent1", "lead", (3,)), ("agent2", "member", (7,)),
                                  ("agent3", "member", (9,))])

    def test_the_hub_creates_a_team_run_by_its_name(self):
        answer = hub_module.Hub().create_run("team", [7, 3], {3: "a", 7: "b"}, SHARED)
        self.assertEqual(answer["arm"], "team")
        self.assertEqual([(a["id"], a["role"], a["features"]) for a in answer["agents"]],
                         [("agent1", "lead", [3]), ("agent2", "member", [7])])

    def test_the_hub_refuses_a_team_run_without_the_shared_directory(self):
        for options in (None, {}, {"shared_dir_host": "/tmp/shared"}):
            with self.subTest(options=options), self.assertRaises(hub_module.HubError) as refused:
                hub_module.Hub().create_run("team", [7, 3], {3: "a", 7: "b"}, options)
            self.assertEqual(refused.exception.status, 400)
            self.assertIn("shared_volume", refused.exception.message)

    def test_each_member_gets_its_feature_assigned_and_the_lead_its_integration_task(self):
        _, run = new_run()
        self.assertEqual([(t["title"], t["owner"]) for t in board(run).tasks.values()], [
            ("Implement feature 7", "agent2"), ("Implement feature 9", "agent3"),
            ("Lead-only: integrate and submit feature 3", "agent1")])
        for task in tasks_of(run):
            self.assertEqual(list(task), TASK_FIELDS)
            self.assertEqual((task["status"], task["created_by"], task["last_note"]),
                             ("open", "bench-runner", ""))
            self.assertIsInstance(task["created_at"], float)
        self.assertEqual(task_titled(run, "Implement feature 7")["metadata"],
                         {"feature_id": 7, "assigned_to": "agent2"})
        self.assertEqual(task_titled(run, "Lead-only: integrate and submit feature 3")["metadata"],
                         {"feature_id": 3, "assigned_to": "agent1", "lead_task": True})

    def test_seeding_is_in_the_audit_log_and_the_events(self):
        _, run = new_run()
        self.assertEqual([(e["kind"], e["by"]) for e in board(run).log], [("create", "bench-runner")] * 3)
        self.assertEqual([e["kind"] for e in run.events], ["task_create"] * 3)

    def test_a_team_of_one_has_only_the_leads_task(self):
        _, run = new_run(features=(4,))
        self.assertEqual([t["title"] for t in tasks_of(run)], ["Lead-only: integrate and submit feature 4"])


class Claims(unittest.TestCase):
    def test_a_claim_makes_the_caller_owner_and_moves_the_task_on(self):
        _, run = new_run()
        task_id = text_of(run, "agent1", "task_create", title="Write the parser")
        self.assertEqual(text_of(run, "agent3", "task_claim", task_id=task_id), "")
        task = task_titled(run, "Write the parser")
        self.assertEqual((task["owner"], task["status"]), ("agent3", "in_progress"))

    def test_exactly_one_of_many_racing_agents_wins_and_the_rest_exit_2(self):
        _, run = new_run(features=tuple(range(1, 13)))
        agents = list(run.agents)
        for _ in range(10):
            task_id = text_of(run, "agent1", "task_create", title="contested")
            start = threading.Barrier(len(agents))
            answers = {}

            def race(agent):
                start.wait()
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "task_claim", "arguments": {"task_id": task_id}}}
                answers[agent] = mcp.handle(request, run, agent)["result"]

            threads = [threading.Thread(target=race, args=(agent,)) for agent in agents]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            winners = [agent for agent, answer in answers.items() if not answer["isError"]]
            self.assertEqual(len(answers), len(agents))
            self.assertEqual(len(winners), 1)
            self.assertEqual(board(run).tasks[task_id]["owner"], winners[0])
            losses = [a for a in answers.values() if a["isError"]]
            self.assertEqual({a["content"][0]["text"] for a in losses}, {f"task {task_id} owned by {winners[0]}"})
            self.assertEqual({a["_meta"]["coordbench/exitCode"] for a in losses}, {2})
            claims = [e for e in board(run).log if e["kind"] == "claim" and e["task_id"] == task_id]
            self.assertEqual([e["by"] for e in claims], winners)

    def test_a_task_assigned_to_another_agent_cannot_be_claimed(self):
        _, run = new_run()
        task = task_titled(run, "Implement feature 7")
        for intruder in ("agent3", "agent1"):
            answer = call(run, intruder, "task_claim", task_id=task["id"])
            self.assertEqual(answer, refusal(f"task {task['id']} owned by agent2", 2))
        after = task_titled(run, "Implement feature 7")
        self.assertEqual((after["owner"], after["status"]), ("agent2", "open"))
        self.assertNotIn("claim", [e["kind"] for e in board(run).log])

    def test_the_assignee_claims_its_task_and_may_claim_it_again(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        text_of(run, "agent2", "task_claim", task_id=task_id)
        text_of(run, "agent2", "task_claim", task_id=task_id)
        self.assertEqual(task_titled(run, "Implement feature 7")["status"], "in_progress")
        self.assertEqual(run.arm.metrics(run)["claims_per_agent"], {"agent2": 2})

    def test_an_unknown_task_cannot_be_claimed_and_exits_1(self):
        _, run = new_run()
        self.assertEqual(call(run, "agent2", "task_claim", task_id="nope"),
                         refusal("task nope does not exist", 1))


class UpdatesAndListing(unittest.TestCase):
    def setUp(self):
        _, self.run = new_run()
        self.task_id = task_titled(self.run, "Implement feature 7")["id"]
        text_of(self.run, "agent2", "task_claim", task_id=self.task_id)

    def test_the_owner_sets_status_and_note(self):
        text_of(self.run, "agent2", "task_update", task_id=self.task_id, status="done",
                note="patch at /workspace/shared/agent2.patch")
        task = task_titled(self.run, "Implement feature 7")
        self.assertEqual((task["status"], task["last_note"]), ("done", "patch at /workspace/shared/agent2.patch"))
        self.assertEqual(board(self.run).log[-1]["note"], "patch at /workspace/shared/agent2.patch")

    def test_a_status_without_a_note_keeps_the_last_note(self):
        text_of(self.run, "agent2", "task_update", task_id=self.task_id, status="blocked", note="need the API")
        text_of(self.run, "agent2", "task_update", task_id=self.task_id, status="in_progress")
        self.assertEqual(task_titled(self.run, "Implement feature 7")["last_note"], "need the API")
        self.assertNotIn("note", board(self.run).log[-1])

    def test_only_the_owner_updates_and_anyone_else_exits_3(self):
        answer = call(self.run, "agent1", "task_update", task_id=self.task_id, status="done")
        self.assertEqual(answer, refusal(f"task {self.task_id} owned by 'agent2', not you", 3))

    def test_an_unknown_status_exits_4_and_an_unknown_task_1(self):
        answer = call(self.run, "agent2", "task_update", task_id=self.task_id, status="finished")
        self.assertEqual(answer, refusal(
            "invalid status 'finished'; expected one of ['blocked', 'done', 'in_progress', 'open']", 4))
        answer = call(self.run, "agent2", "task_update", task_id="nope", status="done")
        self.assertEqual(answer, refusal("task nope does not exist", 1))

    def test_the_owner_check_comes_before_the_status_check(self):
        # coop_task.py checks the owner first, so a stranger with a bad
        # status hears that the task is not theirs.
        answer = call(self.run, "agent3", "task_update", task_id=self.task_id, status="finished")
        self.assertEqual(exit_code(answer), 3)

    def test_list_holds_every_task_in_id_order(self):
        ids = [task["id"] for task in tasks_of(self.run)]
        self.assertEqual(ids, sorted(board(self.run).tasks))
        self.assertEqual(len(ids), 3)

    def test_list_filters_mine_and_open(self):
        mine = tasks_of(self.run, "agent2", mine=True)
        self.assertEqual([t["id"] for t in mine], [self.task_id])
        still_open = {t["title"] for t in tasks_of(self.run, "agent2", open=True)}
        self.assertEqual(still_open, {"Implement feature 9", "Lead-only: integrate and submit feature 3"})
        self.assertEqual(tasks_of(self.run, "agent2", mine=True, open=True), [])
        self.assertEqual(len(tasks_of(self.run, "agent2", mine=False, open="false")), 3)


class MissingArguments(unittest.TestCase):
    def test_a_missing_argument_exits_2_with_argparses_words(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        for tool, arguments, missing in (
                ("task_create", {"assign": "agent2"}, "title"),
                ("task_claim", {}, "task_id"),
                ("task_update", {"status": "done"}, "task_id"),
                ("task_update", {"task_id": task_id}, "status"),
                ("request", {"kind": "review"}, "recipient"),
                ("request", {"recipient": "agent1"}, "kind"),
                ("respond", {"body": "yes"}, "request_id")):
            with self.subTest(tool=tool, arguments=arguments):
                self.assertEqual(call(run, "agent2", tool, **arguments),
                                 refusal(f"the following arguments are required: {missing}", 2))
        self.assertEqual(len(board(run).tasks), 3)
        self.assertEqual(board(run).requests, {})

    def test_an_empty_title_or_body_is_a_value_as_it_is_to_argparse(self):
        # coop_task.py's parser takes "" for a positional and stores it;
        # only an absent one is refused, so the arm refuses only None.
        _, run = new_run()
        task_id = text_of(run, "agent1", "task_create", title="")
        self.assertEqual(board(run).tasks[task_id]["title"], "")
        request_id = text_of(run, "agent2", "request", recipient="agent1", kind="review", body="")
        self.assertEqual(board(run).requests[request_id]["body"], "")
        self.assertEqual(text_of(run, "agent1", "respond", request_id=request_id, body=""), "")
        self.assertEqual(board(run).responses[request_id][0]["body"], "")


class ShellCommands(unittest.TestCase):
    def test_each_tool_answers_to_cooperbenchs_command_and_options(self):
        _, run = new_run()
        for agent in run.agents:
            described = hub_module.Hub().describe_tools(run, agent)
            self.assertEqual({t["name"]: t["alias"] for t in described}, ALIASES)
            self.assertEqual({t["name"]: t["argv"] for t in described if t["name"] in ARGV}, ARGV)
            self.assertEqual({t["name"]: t["flags"] for t in described if t["name"] in FLAGS}, FLAGS)

    def test_every_flag_sets_a_field_of_the_schema_that_no_position_fills(self):
        _, run = new_run()
        switches = set()
        for tool in hub_module.Hub().describe_tools(run, "agent1"):
            properties = tool["inputSchema"]["properties"]
            positions = {name.removesuffix("...") for name in tool["argv"]}
            for flag, name in tool["flags"].items():
                with self.subTest(tool=tool["name"], flag=flag):
                    self.assertIn(name, properties)
                    self.assertNotIn(name, positions)
                    if properties[name].get("type") == "boolean":
                        switches.add(flag)
        self.assertEqual(switches, {"--mine", "--open"})

    def test_create_with_and_without_assign(self):
        _, run = new_run()
        for words, owner in ((["Write the parser"], ""),
                             (["--assign", "agent2", "Write the parser"], "agent2"),
                             (["Write the parser", "--assign", "agent3"], "agent3")):
            answer = shell(run, "agent1", "task_create", *words)
            self.assertFalse(answer["isError"], answer)
            task = board(run).tasks[answer["text"]]
            self.assertEqual((task["title"], task["owner"], task["created_by"]),
                             ("Write the parser", owner, "agent1"))

    def test_create_needs_a_title(self):
        _, run = new_run()
        self.assertEqual(shell(run, "agent1", "task_create", "--assign", "agent2"),
                         refusal("the following arguments are required: title", 2))

    def test_an_empty_title_from_the_shell_makes_a_task(self):
        _, run = new_run()
        answer = shell(run, "agent1", "task_create", "")
        self.assertFalse(answer["isError"], answer)
        self.assertEqual(board(run).tasks[answer["text"]]["title"], "")

    def test_unquoted_words_are_refused_as_argparse_refuses_them(self):
        # coop_task.py takes one word per positional and argparse exits 2
        # on "unrecognized arguments"; the arm no longer joins the words,
        # so an agent that forgot its quotes is told, as in CooperBench.
        _, run = new_run()
        request_id = board(run).request("agent2", "agent1", "review", "x")
        for tool, words in (("task_create", ("Write", "the", "parser")),
                            ("task_create", ("--assign", "agent2", "Write", "the", "parser")),
                            ("request", ("agent1", "review", "may I", "take cli.py?")),
                            ("respond", (request_id, "yes,", "take it"))):
            with self.subTest(tool=tool), self.assertRaises(cli.Failure) as refused:
                shell(run, "agent2", tool, *words)
            self.assertIn("too many words", str(refused.exception))
        self.assertEqual(len(board(run).tasks), 3)
        self.assertEqual(list(board(run).requests), [request_id])
        self.assertEqual(board(run).responses, {})

    def test_update_takes_its_note_anywhere_and_by_either_name(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        shell(run, "agent2", "task_claim", task_id)
        answer = shell(run, "agent2", "task_update", task_id, "done", "-n", "patch at /workspace/shared/agent2.patch")
        self.assertFalse(answer["isError"], answer)
        self.assertEqual(board(run).tasks[task_id]["last_note"], "patch at /workspace/shared/agent2.patch")
        answer = shell(run, "agent2", "task_update", "--note", "waiting on agent1", task_id, "blocked")
        self.assertFalse(answer["isError"], answer)
        task = board(run).tasks[task_id]
        self.assertEqual((task["status"], task["last_note"]), ("blocked", "waiting on agent1"))

    def test_update_by_a_stranger_exits_3_from_the_shell_too(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        answer = shell(run, "agent3", "task_update", task_id, "done", "-n", "mine now")
        self.assertEqual(answer, refusal(f"task {task_id} owned by 'agent2', not you", 3))

    def test_words_the_command_cannot_place_never_reach_the_list(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        for tool, words in (("task_update", (task_id, "done", "a note")), ("task_list", ("--all",))):
            with self.subTest(tool=tool), self.assertRaises(cli.Failure):
                shell(run, "agent2", tool, *words)
        self.assertEqual(board(run).tasks[task_id]["status"], "open")

    def test_an_mcp_note_is_never_read_as_flags(self):
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        text_of(run, "agent2", "task_claim", task_id=task_id)
        text_of(run, "agent2", "task_update", task_id=task_id, status="blocked", note="run pytest -n 4 --open")
        self.assertEqual(board(run).tasks[task_id]["last_note"], "run pytest -n 4 --open")

    def test_list_flags(self):
        _, run = new_run()
        mine = json.loads(shell(run, "agent2", "task_list", "--mine")["text"])
        self.assertEqual([t["title"] for t in mine], ["Implement feature 7"])
        both = json.loads(shell(run, "agent2", "task_list", "--open", "--mine")["text"])
        self.assertEqual([t["title"] for t in both], ["Implement feature 7"])
        self.assertEqual(len(json.loads(shell(run, "agent2", "task_list")["text"])), 3)

    def test_request_with_a_quoted_body_and_a_wait(self):
        _, run = new_run()
        answers = {}
        waiting = threading.Thread(target=lambda: answers.update(shell(
            run, "agent2", "request", "agent1", "plan_approval", "may I take cli.py?", "--wait", "10")))
        waiting.start()
        self.assertTrue(run.wait_until(lambda: bool(board(run).requests), 5))
        pending = json.loads(shell(run, "agent1", "pending")["text"])
        self.assertEqual([(p["from"], p["kind"], p["body"]) for p in pending],
                         [("agent2", "plan_approval", "may I take cli.py?")])
        self.assertEqual(shell(run, "agent1", "respond", pending[0]["id"], "yes, take it"),
                         {"text": "", "isError": False})
        waiting.join(10)
        response = json.loads(answers["text"])
        self.assertEqual((response["from"], response["to"], response["body"]), ("agent1", "agent2", "yes, take it"))

    def test_a_body_left_off_the_words_is_read_from_stdin(self):
        # coop_task.py reads the body of a request or a response from stdin
        # when no word gives it, which is how a long body is piped in.
        _, run = new_run()
        request_id = shell(run, "agent2", "request", "agent1", "review", stdin="the plan:\n- one\n")["text"]
        self.assertEqual(board(run).requests[request_id]["body"], "the plan:\n- one\n")
        self.assertFalse(shell(run, "agent1", "respond", request_id, stdin="approved\n")["isError"])
        self.assertEqual(board(run).responses[request_id][0]["body"], "approved\n")

    def test_a_wait_that_runs_out_exits_5_and_a_wait_that_is_no_number_2(self):
        _, run = new_run()
        answer = shell(run, "agent2", "request", "agent1", "review", "look", "--wait", "0.05")
        request_id = next(iter(board(run).requests))
        self.assertEqual(answer, refusal(f"timed out waiting for response to {request_id}", 5))
        answer = shell(run, "agent2", "request", "agent1", "review", "look", "--wait", "soon")
        self.assertEqual(answer, refusal("argument --wait: invalid float value: 'soon'", 2))
        self.assertEqual(len(board(run).requests), 1)


class Requests(unittest.TestCase):
    def setUp(self):
        _, self.run = new_run()

    def test_a_request_waits_in_the_recipients_pending_list(self):
        request_id = text_of(self.run, "agent2", "request", recipient="agent1", kind="review", body="look at x")
        self.assertRegex(request_id, r"^[0-9a-f]{10}$")
        pending = json.loads(text_of(self.run, "agent1", "pending"))
        self.assertEqual([(p["id"], p["from"], p["to"], p["kind"], p["body"]) for p in pending],
                         [(request_id, "agent2", "agent1", "review", "look at x")])
        self.assertIsInstance(pending[0]["ts"], float)
        self.assertEqual(json.loads(text_of(self.run, "agent3", "pending")), [])

    def test_only_the_addressee_responds_and_only_once(self):
        request_id = text_of(self.run, "agent2", "request", recipient="agent1", kind="review", body="x")
        self.assertEqual(call(self.run, "agent3", "respond", request_id=request_id, body="no"),
                         refusal(f"request {request_id} not addressed to agent3", 7))
        self.assertEqual(text_of(self.run, "agent1", "respond", request_id=request_id, body="fine"), "")
        self.assertEqual(json.loads(text_of(self.run, "agent1", "pending")), [])
        self.assertEqual(call(self.run, "agent1", "respond", request_id=request_id, body="again"),
                         refusal(f"unknown request {request_id}", 6))

    def test_a_waiting_request_gets_its_response(self):
        answers = {}
        waiting = threading.Thread(target=lambda: answers.update(call(
            self.run, "agent2", "request", recipient="agent1", kind="plan_approval", body="plan", wait=10)))
        waiting.start()
        self.assertTrue(self.run.wait_until(lambda: bool(board(self.run).requests), 5))
        request_id = next(iter(board(self.run).requests))
        text_of(self.run, "agent1", "respond", request_id=request_id, body="approved")
        waiting.join(10)
        self.assertFalse(answers["isError"], answers)
        response = json.loads(answers["text"])
        self.assertEqual(list(response), ["id", "from", "to", "body", "ts"])
        self.assertEqual((response["id"], response["body"]), (request_id, "approved"))

    def test_a_wait_times_out_with_cooperbenchs_message_and_code(self):
        answer = call(self.run, "agent2", "request", recipient="agent1", kind="k", body="b", wait=0.1)
        request_id = next(iter(board(self.run).requests))
        self.assertEqual(answer, refusal(f"timed out waiting for response to {request_id}", 5))

    def test_closing_the_run_ends_a_wait(self):
        hub = hub_module.Hub()
        hub.create_run("team", [1, 2], {1: "a", 2: "b"}, SHARED, run_id="closing")
        run = hub.hosted("closing").run
        answers = {}
        waiting = threading.Thread(target=lambda: answers.update(hub.call(
            run, "agent2", "request", {"recipient": "agent1", "kind": "k", "wait": 60})))
        waiting.start()
        run.wait_until(lambda: bool(board(run).requests), 5)
        hub.close_run("closing")
        waiting.join(10)
        self.assertFalse(waiting.is_alive())
        self.assertEqual((answers["isError"], answers["exitCode"]), (True, 5))
        self.assertTrue(answers["text"].startswith("timed out waiting for response to "))

    def test_a_cancelled_wait_exits_5_and_takes_no_response(self):
        # Claude Code gives up on a request's wait with a
        # notifications/cancelled; the hub finds the call among those in
        # flight on the session, and the wait ends as coop-request's does
        # when it runs out, taking nothing: a response that came with the
        # cancellation stays on the board.
        in_flight, answers = {}, {}
        request = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "request", "arguments": {"recipient": "agent1", "kind": "k", "wait": 30}}}
        waiting = threading.Thread(target=lambda: answers.update(
            mcp.handle(request, self.run, "agent2", in_flight=in_flight)["result"]))
        waiting.start()
        self.assertTrue(self.run.wait_until(lambda: bool(board(self.run).requests), 5))
        self.assertEqual(list(in_flight), [4])
        request_id = next(iter(board(self.run).requests))
        cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                  "params": {"requestId": 4, "reason": "the agent moved on"}}
        with self.run.lock:
            text_of(self.run, "agent1", "respond", request_id=request_id, body="with the cancellation")
            self.assertIsNone(mcp.handle(cancel, self.run, "agent2", in_flight=in_flight))
        waiting.join(5)
        self.assertFalse(waiting.is_alive(), "the wait slept on past the cancellation")
        self.assertEqual(answers["content"][0]["text"], f"timed out waiting for response to {request_id}")
        self.assertEqual(answers["_meta"]["coordbench/exitCode"], 5)
        self.assertEqual(in_flight, {})
        self.assertEqual([r["body"] for r in board(self.run).responses[request_id]], ["with the cancellation"])

    def test_a_response_taken_for_a_reply_never_written_goes_back_on_the_board(self):
        # The hub could not write the answer that carried the response,
        # so it runs the call's undo: the response is back at the head of
        # its request's queue, as it was before the wait took it.
        calls, answers = [], {}
        request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                   "params": {"name": "request", "arguments": {"recipient": "agent1", "kind": "k", "wait": 10}}}
        waiting = threading.Thread(target=lambda: answers.update(
            mcp.handle(request, self.run, "agent2", sink=calls)["result"]))
        waiting.start()
        self.assertTrue(self.run.wait_until(lambda: bool(board(self.run).requests), 5))
        request_id = next(iter(board(self.run).requests))
        text_of(self.run, "agent1", "respond", request_id=request_id, body="approved")
        waiting.join(5)
        self.assertEqual(json.loads(answers["content"][0]["text"])["body"], "approved")
        self.assertEqual(board(self.run).responses[request_id], [])
        self.assertEqual(len(calls), 1)
        hub_module.undo_lost(calls)
        self.assertEqual([r["body"] for r in board(self.run).responses[request_id]], ["approved"])

    def test_a_request_to_a_name_not_in_the_run_is_queued_as_cooperbench_queues_it(self):
        # protocol.py's request checks the recipient against nobody: the
        # request is stored under the name given and a wait on it BLPOPs a
        # list nobody pushes to, so it runs out. The arm used to refuse the
        # name, a difference the agent could see.
        request_id = text_of(self.run, "agent2", "request", recipient="agent9", kind="k", body="b")
        self.assertEqual(board(self.run).requests[request_id]["to"], "agent9")
        for agent in self.run.agents:
            self.assertEqual(json.loads(text_of(self.run, agent, "pending")), [])
        answer = call(self.run, "agent2", "request", recipient="agent9", kind="k", body="b", wait=0.1)
        self.assertEqual(exit_code(answer), 5)
        self.assertTrue(answer["text"].startswith("timed out waiting for response to "))
        self.assertEqual(len(board(self.run).requests), 2)

    def test_a_bad_wait_is_refused_before_anything_is_queued(self):
        answer = call(self.run, "agent2", "request", recipient="agent1", kind="k", wait="soon")
        self.assertEqual(answer, refusal("argument --wait: invalid float value: 'soon'", 2))
        self.assertEqual(board(self.run).requests, {})

    def test_requests_are_audited_but_do_not_count_as_task_activity(self):
        request_id = text_of(self.run, "agent2", "request", recipient="agent1", kind="k", body="b")
        text_of(self.run, "agent1", "respond", request_id=request_id, body="r")
        log = board(self.run).log[-2:]
        self.assertEqual([(e["kind"], e["by"], e["to"]) for e in log], [("request", "agent2", "agent1"),
                                                                       ("response", "agent1", "agent2")])
        self.assertEqual(log[0]["request_kind"], "k")
        metrics = self.run.arm.metrics(self.run)
        self.assertEqual((metrics["claims_per_agent"], metrics["updates_per_agent"]), ({}, {}))


# One session of the commands, run through this arm and through
# coop_task.py over the same list: each step's exit code and output must
# agree. <feature 7> and <request> stand for ids both sides share.
PARITY_SESSION = [
    ("agent3", ("claim", "<feature 7>")),
    ("agent2", ("claim", "<feature 7>")),
    ("agent2", ("claim", "<feature 7>")),
    ("agent2", ("claim", "nope")),
    ("agent2", ("claim",)),
    ("agent1", ("update", "<feature 7>", "done")),
    ("agent3", ("update", "<feature 7>", "finished")),
    ("agent2", ("update", "<feature 7>", "finished")),
    ("agent2", ("update", "nope", "done")),
    ("agent2", ("update", "<feature 7>")),
    ("agent2", ("update", "<feature 7>", "blocked", "-n", "need the API")),
    ("agent2", ("update", "<feature 7>", "in_progress")),
    ("agent2", ("list", "--mine")),
    ("agent3", ("list", "--open")),
    ("agent1", ("list", "--open", "--mine")),
    ("agent2", ("update", "<feature 7>", "done", "--note", "patch at /workspace/shared/agent2.patch")),
    ("agent1", ("list",)),
    ("agent1", ("pending",)),
    ("agent3", ("respond", "<request>", "no")),
    ("agent1", ("respond", "<request>", "approved")),
    ("agent1", ("respond", "<request>", "again")),
    ("agent1", ("pending",)),
    ("agent1", ("create",)),
    ("agent1", ("create", "--assign", "agent2")),
    ("agent2", ("request", "agent1", "review", "look", "--wait", "soon")),
    ("agent2", ("request", "agent1", "review")),
    ("agent2", ("request", "agent1", "review", "look", "--wait", "0.05")),
    ("agent1", ("create", "--assign", "agent2", "Write the parser")),
    ("agent1", ("create", "")),
    ("agent2", ("request", "agent9", "review", "nobody reads this")),
]

TEN_HEX = re.compile(r"\b[0-9a-f]{10}\b")


def seeded_redis(run):
    """A FakeRedis holding the run's tasks and open requests as coop_task.py
    stores them, under the same ids."""
    fake, space = FakeRedis(), f"cb:{run.id}"
    for task in board(run).tasks.values():
        fake.hset(f"{space}:task:{task['id']}", mapping={**task, "metadata": json.dumps(task["metadata"])})
        fake.sadd(f"{space}:tasks:all", task["id"])
    for request in board(run).requests.values():
        fake.hset(f"{space}:requests:open:{request['id']}", mapping=request)
        fake.sadd(f"{space}:requests:by_recipient:{request['to']}", request["id"])
    return fake


def outcome(code, out, err):
    """(exit code, what the command said): stdout on success, else the
    message on stderr, argparse's without its usage line. Ids a step
    makes afresh differ between the two sides, so they read as <id>."""
    said = out if code == 0 else err.strip().splitlines()[-1].split(" error: ", 1)[-1]
    return code, TEN_HEX.sub("<id>", said.strip())


def run_coop_task(fake, run_id, agent, words):
    out, err = io.StringIO(), io.StringIO()
    environment = {"CB_TEAM_REDIS_URL": "redis://localhost:6379/0", "CB_TEAM_RUN_ID": run_id,
                   "CB_TEAM_AGENT_ID": agent}
    # A request or a response with no body reads stdin, which here is
    # empty, as the shell side's is.
    with mock.patch.object(COOP_TASK, "redis", types.SimpleNamespace(from_url=lambda url: fake)), \
            mock.patch.dict(os.environ, environment), mock.patch.object(sys, "stdin", io.StringIO("")), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        os.environ.pop("CB_TEAM_TASKS_DIR", None)
        try:
            code = COOP_TASK.main(list(words))
        except SystemExit as stop:
            code = stop.code
    return outcome(code, out.getvalue(), err.getvalue())


def run_ours(run, agent, words):
    subcommand, *rest = words
    tool = next(name for name, alias in ALIASES.items() if alias in (f"coop-task-{subcommand}", f"coop-{subcommand}"))
    answer = shell(run, agent, tool, *rest)
    return outcome(exit_code(answer), "" if answer["isError"] else answer["text"], answer["text"])


@unittest.skipIf(COOP_TASK is None, NO_SOURCE)
class CooperBenchsOwnCommands(unittest.TestCase):
    def test_a_session_of_commands_exits_and_prints_as_coop_task_py_does(self):
        _, run = new_run()
        request_id = board(run).request("agent2", "agent1", "plan_approval", "may I take cli.py?")
        names = {"<feature 7>": task_titled(run, "Implement feature 7")["id"], "<request>": request_id}
        fake = seeded_redis(run)
        codes = set()
        for agent, words in PARITY_SESSION:
            words = tuple(names.get(word, word) for word in words)
            with self.subTest(agent=agent, words=words):
                theirs = run_coop_task(fake, run.id, agent, words)
                self.assertEqual(run_ours(run, agent, words), theirs)
                codes.add(theirs[0])
        self.assertEqual(codes, {0, 1, 2, 3, 4, 5, 6, 7})


def exchange(url, body=None):
    """GET the url, or POST body to it as JSON: (status, the JSON answer)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as problem:
        with problem:
            return problem.code, json.loads(problem.read())


class OverHttp(unittest.TestCase):
    def setUp(self):
        self.server = hub_module.serve_in_background(hub_module.Hub())
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = self.server.hub.base_url

    def create(self, options):
        return exchange(f"{self.base}/api/runs", {"arm": "team", "features": [1, 2],
                                                  "specs": {"1": "a", "2": "b"}, "options": options})

    def test_rpc_lists_the_flags_and_answers_a_lost_claim_with_exit_code_2(self):
        status, created = self.create(SHARED)
        self.assertEqual(status, 201)
        rpc = created["agents"][0]["rpc_url"]
        status, described = exchange(rpc)
        self.assertEqual({t["name"]: t["flags"] for t in described["tools"] if t["name"] in FLAGS}, FLAGS)
        status, listed = exchange(rpc, {"tool": "task_list", "arguments": {"open": True}})
        task_id = next(t["id"] for t in json.loads(listed["text"]) if t["owner"] == "agent2")
        status, answer = exchange(rpc, {"tool": "task_claim", "arguments": {"task_id": task_id}})
        self.assertEqual((status, answer), (200, refusal(f"task {task_id} owned by agent2", 2)))

    def test_a_run_without_the_shared_directory_is_400(self):
        status, answer = self.create({})
        self.assertEqual(status, 400)
        self.assertIn("shared_volume", answer["error"])


class Prompts(unittest.TestCase):
    def prompts(self, features=(3, 7, 9), options=None):
        arm, run = new_run(features, options)
        return {agent: arm.prompt(run, agent, SPECS) for agent in run.agents}

    def test_the_lead_gets_the_task_the_submission_block_and_the_lead_block(self):
        lead = self.prompts()["agent1"]
        self.assertTrue(lead.startswith(SPECS[3] + RULE + SUBMISSION_BLOCK + RULE + "## You are the team-lead"))
        for excerpt in LEAD_EXCERPTS:
            self.assertIn(excerpt, lead)
        self.assertIn(TEAM_LIST_USAGE + "\n\n" + MCP_SENTENCE + "\n\nA shared scratchpad volume", lead)
        self.assertNotIn("## You are a team member", lead)

    def test_a_member_gets_the_member_block_naming_the_lead(self):
        member = self.prompts()["agent2"]
        self.assertTrue(member.startswith(SPECS[7] + RULE + SUBMISSION_BLOCK + RULE + "## You are a team member"))
        for excerpt in MEMBER_EXCERPTS:
            self.assertIn(excerpt, member)
        self.assertIn(TEAM_LIST_USAGE + "\n\n" + MCP_SENTENCE + "\n\nA shared scratchpad volume", member)

    def test_no_git_block_without_a_shared_remote(self):
        for prompt in self.prompts().values():
            self.assertNotIn("## Git collaboration", prompt)
            self.assertEqual(prompt.count(RULE), 2)

    def test_a_shared_remote_adds_cooperbenchs_git_block_last(self):
        lead = self.prompts(options={"git_remote": "git://host/team.git"})["agent1"]
        git = lead.split(RULE)[-1]
        self.assertTrue(git.startswith(f"## Git collaboration {DASH} MERGE IS REQUIRED BEFORE SUBMITTING"))
        self.assertIn("- Partner branches: `team/agent2`, `team/agent3`", git)
        self.assertIn("git push team agent1 --force || true", git)
        self.assertIn("  git fetch team && git merge --no-edit team/agent3 || true   # pull in agent3's work", git)

    def test_a_team_of_one_gets_the_solo_prompt(self):
        self.assertEqual(self.prompts(features=(3,))["agent1"], SPECS[3] + RULE + SUBMISSION_BLOCK)

    @unittest.skipIf(COOPERBENCH is None, NO_SOURCE)
    def test_prompts_are_cooperbenchs_but_for_the_mcp_sentence(self):
        team, stand_ins = COOPERBENCH
        for git in (False, True):
            arm, run = new_run(options={"git_remote": "git://host/team.git"} if git else {})
            for agent, spec in run.agents.items():
                with self.subTest(agent=agent, git=git), mock.patch.dict(sys.modules, stand_ins):
                    theirs = team.build_team_instruction(
                        SPECS[spec.features[0]], agents=list(run.agents), agent_id=agent,
                        team_role=spec.role, git_enabled=git)
                    ours = arm.prompt(run, agent, SPECS)
                    self.assertEqual(ours.replace("\n\n" + MCP_SENTENCE, "", 1), theirs)


class Metrics(unittest.TestCase):
    def test_metrics_follow_cooperbenchs_definitions(self):
        _, run = new_run(features=(3, 7))
        member_task = task_titled(run, "Implement feature 7")["id"]
        lead_task = task_titled(run, "Lead-only: integrate and submit feature 3")["id"]
        text_of(run, "agent1", "task_create", title="nobody's yet")
        text_of(run, "agent2", "task_claim", task_id=member_task)
        text_of(run, "agent2", "task_update", task_id=member_task, status="done", note="patch")
        text_of(run, "agent1", "task_claim", task_id=lead_task)
        text_of(run, "agent1", "task_update", task_id=lead_task, status="in_progress", note="merging")
        text_of(run, "agent2", "send_message", to="agent1", text="patch is up")
        metrics = run.arm.metrics(run)
        self.assertEqual({key: metrics[key] for key in ("tasks_total", "tasks_done", "unowned_at_end",
                                                         "claims_per_agent", "updates_per_agent")},
                         {"tasks_total": 3, "tasks_done": 1, "unowned_at_end": 1,
                          "claims_per_agent": {"agent2": 1, "agent1": 1},
                          "updates_per_agent": {"agent2": 1, "agent1": 1}})
        self.assertGreaterEqual(metrics["time_to_first_claim_seconds"], 0)
        self.assertEqual((metrics["messages_sent"], metrics["messages_by_agent"]), (1, {"agent2": 1}))
        self.assertEqual(metrics["tool_calls"]["task_claim"], 2)

    def test_time_to_first_claim_runs_from_the_first_create_to_the_first_claim(self):
        events = [{"kind": "create", "ts": 100.0, "by": "bench-runner"},
                  {"kind": "create", "ts": 101.0, "by": "bench-runner"},
                  {"kind": "claim", "ts": 104.5, "by": "agent2"},
                  {"kind": "claim", "ts": 103.25, "by": "agent1"}]
        metrics = TEAM_MODULE.compute_metrics(events, [{"owner": "agent1", "status": "done"}, {"owner": ""}])
        self.assertEqual(metrics, {"tasks_total": 2, "tasks_done": 1, "unowned_at_end": 1,
                                   "time_to_first_claim_seconds": 3.25,
                                   "claims_per_agent": {"agent2": 1, "agent1": 1}, "updates_per_agent": {}})

    def test_no_claim_means_no_time_to_first_claim(self):
        _, run = new_run()
        self.assertIsNone(run.arm.metrics(run)["time_to_first_claim_seconds"])

    def test_closing_through_the_hub_answers_the_metrics(self):
        hub = hub_module.Hub()
        hub.create_run("team", [1, 2], {1: "a", 2: "b"}, SHARED, run_id="m")
        self.assertEqual(hub.close_run("m")["tasks_total"], 2)


class Mirror(unittest.TestCase):
    def test_listing_mirrors_the_list_into_the_shared_directory(self):
        with tempfile.TemporaryDirectory() as shared:
            _, run = new_run(options={"shared_dir_host": shared})
            (Path(shared) / "tasks").mkdir()
            (Path(shared) / "tasks" / "gone.json").write_text("{}", encoding="utf-8")
            listed = tasks_of(run, "agent2")
            mirror = Path(shared) / "tasks"
            self.assertEqual(sorted(p.name for p in mirror.iterdir()),
                             sorted([f"{t['id']}.json" for t in listed] + ["_index.json", "_log.jsonl"]))
            self.assertEqual(json.loads((mirror / f"{listed[0]['id']}.json").read_text()), listed[0])
            self.assertEqual(json.loads((mirror / "_index.json").read_text())["ids"], [t["id"] for t in listed])
            self.assertEqual(len((mirror / "_log.jsonl").read_text().splitlines()), 3)

    def test_a_stale_task_file_is_removed_through_the_directory_not_by_its_path(self):
        with tempfile.TemporaryDirectory() as shared:
            _, run = new_run(options={"shared_dir_host": shared})
            (Path(shared) / "tasks").mkdir()
            (Path(shared) / "tasks" / "stale.json").write_text("{}", encoding="utf-8")
            tasks_of(run, "agent2")
            self.assertFalse((Path(shared) / "tasks" / "stale.json").exists())

    def test_a_symlink_planted_as_tasks_is_not_followed(self):
        # The hub writes the mirror on its host, as root when the volume's
        # path asks for it, and the agents own the volume's contents: a
        # symlink named tasks would have it write into, and delete *.json
        # from, whatever directory an agent chose. The kernel refuses the
        # link as the directory is opened (O_NOFOLLOW), so the mirror is
        # skipped and the target is left as it was.
        with tempfile.TemporaryDirectory() as shared, tempfile.TemporaryDirectory() as elsewhere:
            victim = Path(elsewhere) / "precious.json"
            victim.write_text("keep me", encoding="utf-8")
            (Path(shared) / "tasks").symlink_to(elsewhere, target_is_directory=True)
            _, run = new_run(options={"shared_dir_host": shared})
            listed = tasks_of(run, "agent2")
            self.assertEqual(len(listed), 3)
            self.assertTrue((Path(shared) / "tasks").is_symlink())
            self.assertEqual(sorted(p.name for p in Path(elsewhere).iterdir()), ["precious.json"])
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(sorted(p.name for p in Path(shared).iterdir()), ["tasks"])

    def test_a_file_in_the_place_of_tasks_skips_the_mirror(self):
        with tempfile.TemporaryDirectory() as shared:
            (Path(shared) / "tasks").write_text("not a directory", encoding="utf-8")
            _, run = new_run(options={"shared_dir_host": shared})
            self.assertEqual(len(tasks_of(run, "agent2")), 3)
            self.assertEqual((Path(shared) / "tasks").read_text(encoding="utf-8"), "not a directory")
            self.assertEqual(sorted(p.name for p in Path(shared).iterdir()), ["tasks"])

    def test_a_closed_run_mirrors_nothing(self):
        # The runner removes the volume after closing the run, while the
        # hub keeps answering tools; a late listing must not put a tasks
        # directory back where the volume was.
        with tempfile.TemporaryDirectory() as shared:
            _, run = new_run(options={"shared_dir_host": shared})
            with run.lock:
                run.closed = True
            self.assertEqual(len(tasks_of(run, "agent2")), 3)
            self.assertEqual(list(Path(shared).iterdir()), [])

    def test_a_removed_volume_root_is_not_made_again(self):
        # Only the tasks leaf is made, never its parents, so a volume that
        # docker has removed stays removed instead of leaving a root-owned
        # directory under docker's volume root.
        with tempfile.TemporaryDirectory() as parent:
            gone = Path(parent) / "volumes" / "coordbench-shared-r1" / "_data"
            _, run = new_run(options={"shared_dir_host": str(gone)})
            self.assertEqual(len(tasks_of(run, "agent2")), 3)
            self.assertEqual(list(Path(parent).iterdir()), [])

    def test_without_a_shared_directory_nothing_is_written(self):
        _, run = new_run()
        mirror = mock.Mock()
        # The module find_arm loaded, which is not TEAM_MODULE: each load
        # of an arm is a module of its own.
        with mock.patch.dict(type(run.arm).setup.__globals__, {"mirror_task_list": mirror}):
            tasks_of(run)
        mirror.assert_not_called()

    def test_a_close_that_lands_after_the_read_still_gets_one_mirror_and_no_more(self):
        # The run is read as open under the lock and the writes happen
        # outside it: a close that lands between the two gets this one
        # mirror, into a volume the runner removes only once the close has
        # answered, and the next listing writes nothing.
        with tempfile.TemporaryDirectory() as shared:
            _, run = new_run(options={"shared_dir_host": shared})
            module_globals = type(run.arm).setup.__globals__
            original = module_globals["open_tasks_directory"]

            def close_then_open(path):
                with run.lock:
                    run.closed = True
                return original(path)

            with mock.patch.dict(module_globals, {"open_tasks_directory": close_then_open}):
                listed = tasks_of(run, "agent2")
            mirror = Path(shared) / "tasks"
            self.assertEqual(sorted(p.name for p in mirror.iterdir()),
                             sorted([f"{t['id']}.json" for t in listed] + ["_index.json", "_log.jsonl"]))
            shutil.rmtree(mirror)
            self.assertEqual(len(tasks_of(run, "agent2")), 3)
            self.assertEqual(list(Path(shared).iterdir()), [])

    def test_a_close_and_the_volumes_removal_in_the_window_leave_nothing_behind(self):
        # The same window, with the runner's removal of the volume already
        # done when the writes come: only the tasks leaf is ever made, so
        # the mirror lands nowhere and the volume's root stays gone.
        with tempfile.TemporaryDirectory() as parent:
            shared = Path(parent) / "volume"
            shared.mkdir()
            _, run = new_run(options={"shared_dir_host": str(shared)})
            module_globals = type(run.arm).setup.__globals__
            original = module_globals["open_tasks_directory"]

            def close_remove_then_open(path):
                with run.lock:
                    run.closed = True
                shutil.rmtree(shared)
                return original(path)

            with mock.patch.dict(module_globals, {"open_tasks_directory": close_remove_then_open}):
                self.assertEqual(len(tasks_of(run, "agent2")), 3)
            self.assertEqual(list(Path(parent).iterdir()), [])


class BlockedTime(unittest.TestCase):
    """A request's wait is time spent on another agent, not on the hub:
    await_response books it on the Call, and the hub's tool_done event
    carries it as blocked_ms beside the handler's wall time, so a reader
    of events.jsonl can tell a slow hub from an agent that chose to wait."""

    def setUp(self):
        _, self.run = new_run()

    def tool_done(self, agent, tool):
        events = [e for e in self.run.events
                  if e["kind"] == "tool_done" and e["agent"] == agent and e["tool"] == tool]
        self.assertEqual(len(events), 1, events)
        return events[0]

    def test_await_response_books_its_wait_on_the_call(self):
        team = board(self.run)
        request_id = team.request("agent2", "agent1", "k", "b")
        waiting = Call(self.run, "agent2", {})
        self.assertIsNone(team.await_response(request_id, 0.2, waiting))
        self.assertGreaterEqual(waiting.blocked_s, 0.15)
        self.assertLess(waiting.blocked_s, 5)

    def test_a_wait_that_runs_out_is_reported_as_blocked_time(self):
        calls = []
        request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                   "params": {"name": "request", "arguments": {"recipient": "agent1", "kind": "k", "wait": 0.2}}}
        answer = mcp.handle(request, self.run, "agent2", sink=calls)["result"]
        self.assertEqual(answer["_meta"]["coordbench/exitCode"], 5)
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0].blocked_s, 0.15)
        done = self.tool_done("agent2", "request")
        self.assertEqual((done["is_error"], done["cancelled"]), (True, False))
        self.assertGreaterEqual(done["blocked_ms"], 150)
        self.assertLessEqual(done["blocked_ms"], done["duration_ms"])

    def test_a_wait_answered_by_a_peer_is_blocked_until_the_response(self):
        answers = {}
        waiting = threading.Thread(target=lambda: answers.update(call(
            self.run, "agent2", "request", recipient="agent1", kind="k", body="b", wait=10)))
        waiting.start()
        self.assertTrue(self.run.wait_until(lambda: bool(board(self.run).requests), 5))
        time.sleep(0.15)
        request_id = next(iter(board(self.run).requests))
        text_of(self.run, "agent1", "respond", request_id=request_id, body="approved")
        waiting.join(5)
        self.assertFalse(answers["isError"], answers)
        done = self.tool_done("agent2", "request")
        self.assertGreaterEqual(done["blocked_ms"], 100)
        self.assertLessEqual(done["blocked_ms"], done["duration_ms"])
        self.assertLess(done["duration_ms"], 5000)

    def test_a_request_without_a_wait_blocks_for_nothing(self):
        text_of(self.run, "agent2", "request", recipient="agent1", kind="k", body="b")
        self.assertEqual(self.tool_done("agent2", "request")["blocked_ms"], 0)


class LateCancel(unittest.TestCase):
    def test_a_wait_cancelled_after_the_take_puts_the_response_back(self):
        # The cancel lands after await_response popped the response and
        # before the reply is written: the hub sees it as the handler
        # returns (mcp.settle) and runs the undo send_request registered,
        # so the response is back on the board for the next wait, while
        # the answer, which the client discards, still carries it.
        _, run = new_run()
        in_flight, answers = {}, {}
        request = {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                   "params": {"name": "request", "arguments": {"recipient": "agent1", "kind": "k", "wait": 10}}}
        cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 8}}
        board_class = type(board(run))
        original = board_class.await_response

        def take_then_cancel(team, request_id, seconds, call=None):
            response = original(team, request_id, seconds, call)
            mcp.handle(cancel, run, "agent2", in_flight=in_flight)
            return response

        with mock.patch.object(board_class, "await_response", take_then_cancel):
            waiting = threading.Thread(target=lambda: answers.update(
                mcp.handle(request, run, "agent2", in_flight=in_flight)["result"]))
            waiting.start()
            self.assertTrue(run.wait_until(lambda: bool(board(run).requests), 5))
            request_id = next(iter(board(run).requests))
            text_of(run, "agent1", "respond", request_id=request_id, body="approved")
            waiting.join(5)
        self.assertFalse(waiting.is_alive(), "the wait slept on")
        self.assertEqual(json.loads(answers["content"][0]["text"])["body"], "approved")
        self.assertEqual([r["body"] for r in board(run).responses[request_id]], ["approved"])
        self.assertEqual(in_flight, {})
        done = [e for e in run.events if e["kind"] == "tool_done" and e["tool"] == "request"]
        self.assertEqual([e["cancelled"] for e in done], [True])


class ShellRefusals(unittest.TestCase):
    """What the arm's docstring says exits 2 as argparse makes coop_task.py
    exit: a required positional with no word, which the shell command
    leaves missing rather than reading from a stdin at /dev/null, and a
    --wait of true, false or null, which the command refuses itself since
    the handler would read them as numbers."""

    def test_a_required_positional_with_no_word_is_refused_not_read_from_stdin(self):
        # drivers.py hands docker a stdin at /dev/null: no text, yet not a
        # terminal, so a command that read its last word from it would
        # make a task titled "" where coop_task.py exits 2.
        _, run = new_run()
        task_id = task_titled(run, "Implement feature 7")["id"]
        for tool, words, missing in (("task_create", (), "title"),
                                     ("task_create", ("--assign", "agent2"), "title"),
                                     ("task_claim", (), "task_id"),
                                     ("task_update", (task_id,), "status")):
            with self.subTest(tool=tool, words=words):
                self.assertEqual(shell(run, "agent2", tool, *words, stdin=""),
                                 refusal(f"the following arguments are required: {missing}", 2))
        self.assertEqual(len(board(run).tasks), 3)
        self.assertEqual(board(run).tasks[task_id]["status"], "open")

    def test_a_wait_of_true_false_or_null_is_refused_by_the_command_as_no_number(self):
        _, run = new_run()
        for word in ("true", "false", "null"):
            with self.subTest(word=word):
                with self.assertRaises(cli.Failure) as refused:
                    shell(run, "agent2", "request", "agent1", "review", "look", "--wait", word)
                self.assertEqual(str(refused.exception), f"argument --wait: invalid float value: {word!r}")
        self.assertEqual(cli.FAILURE, 2)
        self.assertEqual(board(run).requests, {})


class PluginFiles(unittest.TestCase):
    def read_json(self, relative):
        return json.loads((TEAM / relative).read_text(encoding="utf-8"))

    def test_plugin_json(self):
        plugin = self.read_json(".claude-plugin/plugin.json")
        self.assertEqual((plugin["name"], plugin["version"]), ("coordbench-team", "0.1.0"))
        self.assertTrue(plugin["description"])

    def test_mcp_json_points_at_this_agents_hub_endpoint_with_a_timeout_above_the_longest_wait(self):
        # Claude Code aborts an HTTP MCP call after 60 s unless the server's
        # config sets "timeout" (milliseconds); the request tool waits up
        # to LONGEST_WAIT_SECONDS, so the config must outlast it. The
        # header carries the agent's own hub token, which a run the runner
        # creates wants on every request.
        config = self.read_json(".mcp.json")
        self.assertEqual(config, {"mcpServers": {"coordbench": {
            "type": "http", "url": "${COORD_MCP_URL}", "timeout": 330000,
            "headers": {"Authorization": "Bearer ${COORD_AGENT_TOKEN:-}"}}}})
        self.assertGreater(config["mcpServers"]["coordbench"]["timeout"],
                           TEAM_MODULE.LONGEST_WAIT_SECONDS * 1000)

    def test_arm_json(self):
        config = self.read_json("arm.json")
        self.assertEqual(set(config), {"entry", "name", "description", "needs", "plugins", "mcp_servers"})
        self.assertEqual((config["entry"], config["name"], config["needs"], config["plugins"], config["mcp_servers"]),
                         ("arm.py:TeamArm", "team", ["shared_volume"], [], {}))
        arm = find_arm("team")
        self.assertEqual((type(arm).__name__, tuple(arm.needs)), ("TeamArm", ("shared_volume",)))

    def test_skill_has_frontmatter_and_names_every_tool_and_command(self):
        text = (TEAM / "skills" / "coordbench-team" / "SKILL.md").read_text(encoding="utf-8")
        lines = text.split("\n")
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        fields = dict(line.split(": ", 1) for line in lines[1:end])
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "coordbench-team")
        self.assertNotIn(": ", fields["description"])
        body = "\n".join(lines[end + 1:])
        for name, alias in ALIASES.items():
            self.assertIn(f"`{name}`", body)
            if alias:
                self.assertIn(f"`{alias}", body)

    def test_every_file_is_ascii_with_lf(self):
        for path in [p for p in TEAM.rglob("*") if p.is_file() and "__pycache__" not in p.parts]:
            raw = path.read_bytes()
            with self.subTest(path=path.name):
                raw.decode("ascii")
                self.assertNotIn(b"\r", raw)
                self.assertTrue(raw.endswith(b"\n"))

    def test_every_agent_lists_every_tool_over_mcp(self):
        _, run = new_run()
        for agent in run.agents:
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            names = [t["name"] for t in mcp.handle(request, run, agent)["result"]["tools"]]
            self.assertEqual(names, list(ALIASES))


if __name__ == "__main__":
    unittest.main()
