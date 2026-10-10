"""The arm archetype: a generator that writes a complete, loadable arm.

    python3 new_arm.py <name> <parent directory> [--description TEXT]
    python3 new_arm.py --check <arm directory>

The first form writes <parent>/<name>/, a Claude Code plugin and a
coordbench arm in one directory, laid out as README.md's "An arm is a
Claude Code plugin" shows: .claude-plugin/plugin.json, .mcp.json (the
same as every built-in arm's), a skill naming every tool and shell
command, arm.json, and arm.py, a documented subclass of Arm that gives
every agent CooperBench's messaging and the README's notice board
(post_notice and read_board, board-post and board-read in the shell),
adds one paragraph about the board to the prompt, counts the notices in
its metrics, and carries a commented stub for each override it does not
make, with the README's sentence on what that override is for. A
newcomer starts from a working directory, changes what the new arm does
differently, and deletes the rest.

The second form is the newcomer's smoke test: it loads the arm the way
the hub does, builds a run of two agents in-process, lists the tools,
pins a notice as one agent and reads it as the other, sends a message,
closes the run and prints the metrics. Any step that fails ends the
check with exit 1 and the exception's one line.

coordbench/examples/board/ is the generator's own output for
the name "board", kept in the tree as the worked example; a test
regenerates it and compares every byte, so the archetype and the example
cannot drift apart.

The generator refuses, with exit 2 and nothing written, a name that is
not a plugin name (lowercase letters, digits and dashes, starting with a
letter: the class it names must be a Python identifier), a parent that
is not a directory, a target directory that already exists, and a
description that is not one line of ASCII, since every file it writes is
ASCII with LF endings. Python 3.12's standard library and nothing else.
"""

import argparse
import json
import os
import re
import string
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hub as hub_module  # noqa: E402
from arm import find_arm  # noqa: E402

# A plugin name, held to what makes a Python class name of it: a segment
# may hold digits, the first must start with a letter.
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Exit codes: what argparse exits with for words it cannot read, and what
# a failed check exits with.
USAGE = 2
CHECK_FAILED = 1

# Every built-in arm's .mcp.json, byte for byte: the timeout is past the
# longest wait any built-in tool allows (300 seconds), since Claude Code
# gives up on an HTTP MCP call after 60 without it, and the header carries
# the agent's own hub token, or nothing for a run without tokens.
MCP_JSON = """{
  "mcpServers": {
    "coordbench": {
      "type": "http",
      "url": "${COORD_MCP_URL}",
      "timeout": 330000,
      "headers": {"Authorization": "Bearer ${COORD_AGENT_TOKEN:-}"}
    }
  }
}
"""

SKILL = string.Template("""---
name: coordbench-$name
description: The $name protocol of a coordbench run, CooperBench's messaging plus a shared notice board. Use when you are one of several agents each implementing a separate feature of the same codebase, to say on the board which files you take, read what your peers took, or message them.
---

# Coordinating through a shared board

You are one of two or more agents, numbered agent1, agent2 and so on.
Each was assigned a separate feature from the same codebase, and your
features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes. The board is where you say what you
take; messages are for everything else. Your peers know only what you
tell them.

## The tools

Every tool is both an MCP tool of the server `coordbench` and a shell
command; both reach the same board and the same inboxes, one per agent.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `post_notice` (text) | `board-post <text>` | pin one line on the board, signed with your agent id |
| `read_board` | `board-read` | every line on the board, oldest first |
| `send_message` (to, text) | `coop-send <recipient> "text"` | send to one peer, by agent id |
| `broadcast` (text) | `coop-broadcast "text"` | send to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |
| `wait_for_message` (timeout_ms) | `coord-wait_for_message` | block until a message arrives, instead of polling |

## The protocol

1. At the start, read the board, then pin one line naming your feature
   and the files you intend to touch.
2. Read the board again before you edit a file you did not name, and
   receive your messages after major edits and before you submit.
3. When two agents need to change the same file, coordinate explicitly
   by message: split the file, agree on one owner, or merge the changes.
4. Pin a last line when you are done, so your peers know what to expect
   from your patch.

## Submitting

Before you exit, write your final unified diff to
`/workspace/repo/patch.txt`; the evaluator reads that file and nothing
else.

```bash
cd /workspace/repo
git diff > patch.txt
cat patch.txt
```

The diff holds only the source files you changed to implement your
feature: no reproduction scripts, no scratch tests, no helper files, and
no changes to test files unless the task asks for them.
""")

ARM_PY = string.Template('''"""The $name arm: CooperBench's messaging plus a shared notice board.

One agent per feature, numbered agent1, agent2 and so on in the order of
the features, each in its own copy of the repository. Every agent has
CooperBench's messaging (coop-send, coop-broadcast, coop-recv, coop-peek,
coop-agents and the long poll wait_for_message, all from kit.py) and two
tools of this arm's own: post_notice pins a line signed with the caller's
id on a board every agent can read, and read_board reads it. The board
is a list in run.state, changed under the run's lock, so a notice is
there for the next reader whoever posts first.

The prompt is the base prompt (the agent's feature spec and CooperBench's
submission block) with one paragraph about the board before the
submission block. The metrics count the notices and the messages.

coordbench/new_arm.py wrote this file, and it is yours to
change: the commented lines in the class below say where each override
goes and what coordbench/README.md ("Writing an arm") says it
is for. An arm overrides only what it changes, so delete what this one
does not need.
"""

import kit
from arm import SUBMISSION_BLOCK, Arm, Tool, ToolError

# The board lives under this key of run.state; a run starts without one
# and the first notice makes it.
BOARD = "board"

EMPTY_BOARD = "(the board is empty)"


def board_of(run):
    """The board's lines, oldest first; the caller holds run.lock."""
    return run.state.setdefault(BOARD, [])


def post_notice(call):
    """Pin one line, signed with the caller's id, and record a "notice"
    event so metrics() can count it. The text is required and must be a
    string: a number or a list is refused rather than pinned as its repr."""
    text = call.arg("text", required=True)
    if not isinstance(text, str):
        raise ToolError(f"text must be a string, not {type(text).__name__}")
    with call.run.lock:
        board_of(call.run).append(f"{call.agent}: {text}")
    call.run.emit("notice", call.agent, chars=len(text))
    return "posted"


def read_board(call):
    """Every line on the board, oldest first, or a marker for none."""
    with call.run.lock:
        lines = list(board_of(call.run))
    return "\\n".join(lines) or EMPTY_BOARD


def board_paragraph(run, agent):
    """The one paragraph this arm adds to the prompt: who the agent is, who
    its peers are, and how the board and the messages divide the work."""
    peers = ", ".join(run.peers(agent)) or "no other agent"
    return ("## The shared board\\n\\n"
            f"You are {agent}, working alongside {peers}. Each of you implements a "
            "separate feature of the same codebase, and your features may touch the "
            "same files. Before you edit, read the board (read_board, or board-read in "
            "the shell) to see which files your peers have taken; then pin one line "
            "naming the files you take (post_notice, or board-post <text>), and another "
            "when you are done. Messages (send_message and the other coop- commands) "
            "are for anything the board does not say; your peers know only what you "
            "tell them.")


def notice_metrics(run):
    """How many notices were pinned, in all and by agent, from the "notice"
    events post_notice emits."""
    notices = [event for event in run.events if event["kind"] == "notice"]
    by_agent = {}
    for event in notices:
        by_agent[event["agent"]] = by_agent.get(event["agent"], 0) + 1
    return {"notices_posted": len(notices), "notices_by_agent": by_agent}


class $class_name(Arm):
    """Messaging and a shared notice board, one agent per feature."""

    name = "$name"
    description = $description_literal
    # needs = ("git_remote", "shared_volume"): "git_remote" (a bare repository, remote
    # `team` in every agent's repository; run.options git_remote, git_remote_host and
    # base_ref say where) and "shared_volume" (/workspace/shared in every container;
    # run.options shared_dir and shared_dir_host). The hub refuses a run whose options
    # do not provide what its arm needs. arm.json's "needs" lists the same.
    needs = ()

    # def layout(self, features): the agents, their roles and features; one agent per
    # feature by default (agent1, agent2 in the features' order), all "peer".

    # def setup(self, run): seed run.state (a task list, a queue); run.specs holds each
    # feature's spec.

    def tools(self, run, agent):
        return kit.messaging_tools() + [
            Tool("post_notice", "Pin a line on the shared board, signed with your agent id.",
                 post_notice, kit.schema(["text"], text=kit.TEXT), alias="board-post", argv=("text...",)),
            Tool("read_board", "Every line on the board, oldest first.", read_board, alias="board-read"),
        ]

    def prompt(self, run, agent, specs):
        base = super().prompt(run, agent, specs)
        return base.replace(SUBMISSION_BLOCK, board_paragraph(run, agent) + "\\n\\n" + SUBMISSION_BLOCK, 1)

    # def instructions(self, run, agent): text sent as the MCP server's instructions,
    # which Claude Code puts into the agent's system prompt; "" by default, and every
    # built-in arm leaves it so, because CooperBench's agents got none.

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run), **notice_metrics(run)}

    # def teardown(self, run): free what setup took, once, when the run closes.
''')

# The run --check builds: two agents, two one-line specs.
CHECK_RUN_ID = "new-arm-check"
CHECK_FEATURES = [1, 2]
CHECK_SPECS = {1: "Add a --verbose flag to the command line.\n",
               2: "Teach the config loader to read TOML.\n"}


class Refused(Exception):
    """A request the generator refuses before it writes anything."""


def class_name_of(name):
    """BoardArm for board, CoopGitArm for coop-git: each dash-separated
    segment capitalized, then Arm."""
    return "".join(segment.capitalize() for segment in name.split("-")) + "Arm"


def default_description(name):
    return (f"coordbench's {name} arm: CooperBench's messaging plus a shared notice board "
            "every agent can pin a line on.")


def check_name(name):
    if not NAME.fullmatch(name or ""):
        raise Refused(f"{name!r} is not a plugin name: lowercase letters, digits and dashes, "
                      "starting with a letter, such as my-arm")


def check_description(description):
    """One line of printable ASCII, so the files stay ASCII with LF and the
    Python literal the description becomes stays one line."""
    if not description.strip():
        raise Refused("the description must say something")
    if not all(" " <= char <= "~" for char in description):
        raise Refused("the description must be one line of ASCII; the files written are ASCII with LF")


def check_target(parent, target):
    if not parent.is_dir():
        raise Refused(f"{parent} is not a directory")
    if target.exists():
        raise Refused(f"{target} already exists; this generator never writes over a directory")


def files_of(name, description):
    """Each file of the arm by its path within the directory, as text."""
    plugin = {"name": f"coordbench-{name}", "version": "0.1.0", "description": description}
    arm_json = {"entry": f"arm.py:{class_name_of(name)}", "name": name, "description": description,
                "needs": [], "plugins": [], "mcp_servers": {}}
    return {
        ".claude-plugin/plugin.json": json.dumps(plugin, indent=2) + "\n",
        ".mcp.json": MCP_JSON,
        f"skills/coordbench-{name}/SKILL.md": SKILL.substitute(name=name),
        "arm.json": json.dumps(arm_json, indent=2) + "\n",
        "arm.py": ARM_PY.substitute(name=name, class_name=class_name_of(name),
                                    description_literal=json.dumps(description)),
    }


def write_arm(target, files):
    """The files, LF whatever the platform; the directory is made here, so
    nothing stands until the checks before this call have passed."""
    for relative, text in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii", newline="\n")


def shown(path):
    """A path as the person would type it: relative to the working
    directory when it is below it, absolute otherwise."""
    relative = os.path.relpath(path)
    return relative if not relative.startswith("..") else str(path)


def how_to_try(target):
    parent = target.parent
    return (f"To try it: python3 {shown(HERE / 'new_arm.py')} --check {shown(target)}; then start the hub "
            f"with python3 {shown(HERE / 'hub.py')} --arm-dir {shown(parent)} and POST /api/runs with "
            f'{{"arm": "{target}", ...}} (python3 {shown(HERE / "run.py")} run --arm {shown(target)} does '
            f"both); then read \"Writing an arm\" in {shown(HERE / 'README.md')} for what to override next.")


def generate(name, parent, description=None):
    """Write the arm and say what was written; Refused when it cannot."""
    check_name(name)
    description = default_description(name) if description is None else description
    check_description(description)
    parent = Path(parent).resolve()
    target = parent / name
    check_target(parent, target)
    files = files_of(name, description)
    write_arm(target, files)
    print(f"wrote {shown(target)}/")
    for relative in files:
        print(f"  {relative}")
    print(how_to_try(target))


def one_line(problem):
    """The first line of an exception's message, or its type for an
    exception that says nothing."""
    lines = str(problem).strip().splitlines()
    return lines[0] if lines else type(problem).__name__


def answer_of(hub, run, agent, tool, arguments):
    """One tool call through the hub, as /rpc makes it; a refusal is a
    failed step, not a result to print."""
    answer = hub.call(run, agent, tool, arguments)
    if answer["isError"]:
        raise RuntimeError(f"{agent}'s {tool} was refused: {answer['text']}")
    print(f"{agent} {tool} -> {answer['text']}")
    return answer["text"]


def check(directory):
    """The smoke test, step by step; any exception ends it."""
    arm = find_arm(directory)
    print(f"loaded {type(arm).__name__} from {shown(arm.root)}")
    hub = hub_module.Hub()
    created = hub.create_run(arm, CHECK_FEATURES, CHECK_SPECS, None, CHECK_RUN_ID)
    agents = ", ".join(f"{a['id']} ({a['role']}, feature {', '.join(map(str, a['features']))})"
                       for a in created["agents"])
    print(f"run {CHECK_RUN_ID}: {agents}")
    run = hub.hosted(CHECK_RUN_ID).run
    tools = hub.describe_tools(run, "agent1")
    print("tools for agent1: " + ", ".join(
        f"{t['name']} ({t['alias']})" if t["alias"] else t["name"] for t in tools))
    answer_of(hub, run, "agent1", "post_notice", {"text": "agent1 takes cli.py"})
    board = answer_of(hub, run, "agent2", "read_board", {})
    if "agent1 takes cli.py" not in board:
        raise RuntimeError(f"agent2 did not see agent1's notice on the board: {board!r}")
    answer_of(hub, run, "agent2", "send_message", {"to": "agent1", "text": "noted, I take config.py"})
    metrics = hub.close_run(CHECK_RUN_ID)
    print("metrics: " + json.dumps(metrics, indent=2))
    print("check ok")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write a complete, loadable coordbench arm from a name, or smoke-test an arm.")
    parser.add_argument("name", nargs="?", help="the arm's name: lowercase letters, digits and dashes")
    parser.add_argument("parent", nargs="?", help="the directory to write <name>/ into; it must exist")
    parser.add_argument("--description", help="one line on what the arm is; plugin.json and arm.json carry it")
    parser.add_argument("--check", metavar="ARM_DIR",
                        help="load this arm, run it in-process with two agents and print its metrics")
    args = parser.parse_args(argv)
    if args.check is not None:
        if args.name is not None or args.parent is not None or args.description is not None:
            parser.error("--check takes the arm's directory and nothing else")
        try:
            check(args.check)
        except Exception as problem:
            print(f"check failed: {one_line(problem)}", file=sys.stderr)
            return CHECK_FAILED
        return 0
    if args.name is None or args.parent is None:
        parser.error("a name and a parent directory are required (or --check ARM_DIR)")
    try:
        generate(args.name, args.parent, args.description)
    except Refused as problem:
        print(f"refused: {problem}", file=sys.stderr)
        return USAGE
    return 0


if __name__ == "__main__":
    sys.exit(main())
