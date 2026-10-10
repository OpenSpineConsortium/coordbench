"""The board arm: CooperBench's messaging plus a shared notice board.

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
    return "\n".join(lines) or EMPTY_BOARD


def board_paragraph(run, agent):
    """The one paragraph this arm adds to the prompt: who the agent is, who
    its peers are, and how the board and the messages divide the work."""
    peers = ", ".join(run.peers(agent)) or "no other agent"
    return ("## The shared board\n\n"
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


class BoardArm(Arm):
    """Messaging and a shared notice board, one agent per feature."""

    name = "board"
    description = "coordbench's board arm: CooperBench's messaging plus a shared notice board every agent can pin a line on."
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
        return base.replace(SUBMISSION_BLOCK, board_paragraph(run, agent) + "\n\n" + SUBMISSION_BLOCK, 1)

    # def instructions(self, run, agent): text sent as the MCP server's instructions,
    # which Claude Code puts into the agent's system prompt; "" by default, and every
    # built-in arm leaves it so, because CooperBench's agents got none.

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run), **notice_metrics(run)}

    # def teardown(self, run): free what setup took, once, when the run closes.
