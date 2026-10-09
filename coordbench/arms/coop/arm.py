"""The coop arm: CooperBench's coop setting, messages only, as a plug-in.

One agent per feature, numbered agent1, agent2 and so on; each works in
its own copy of the repository and can only talk to the others, through
CooperBench's messaging commands (coop-send, coop-broadcast, coop-recv,
coop-peek, coop-agents), which the hub also serves as MCP tools. There is
no wait_for_message here: CooperBench gives its coop agents none, and an
arm that waited would no longer be the arm the authors measured.

The prompt is CooperBench's own build_instruction for a coop agent
(src/cooperbench/agents/_coop/prompt.py at 63b9d44, MIT licence), ported
word for word: the feature spec, the submission block and the cooperation
block, in that order, each separated by a rule. The one addition is a
sentence in the cooperation block naming the MCP tool behind each
command, since a Claude Code that loaded this arm as a plugin sees the
tools before it sees any shell command. With that sentence taken out the
prompt is the authors' text exactly, which the tests check against
CooperBench's own function when its source is at hand. The skill
(skills/coordbench-coop/SKILL.md) restates the prompt and adds nothing
to it, so an agent that loads the plugin knows no more than CooperBench's
agents did.

coop-git (../coop-git) is this arm plus CooperBench's git block; it
subclasses CoopArm through sections(), so both share one copy of the text.
"""

import kit
from arm import AgentSpec, Arm, SUBMISSION_BLOCK

# build_instruction joins its sections with this rule, and solo.py joins
# the features of a solo task with the same one.
SECTION_RULE = "\n\n---\n\n"

# CooperBench's text uses the em dash; the escape keeps this file ASCII
# while the prompt keeps the authors' character.
DASH = "\u2014"

# _coop_block from prompt.py, split where the MCP sentence goes: after the
# list of commands, before the recommended workflow.
COOP_COMMANDS = """## Cooperation protocol

You are **{agent_id}**, working alongside: **{partner_str}**.
Each agent has been assigned a separate feature from the same codebase;
your features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes.

Available shell commands for cross-agent messaging (Redis-backed inbox,
one inbox per agent):

```bash
coop-send <recipient> "message text here"   # send to a specific peer
coop-broadcast "message text here"          # send to every other peer
coop-recv                                    # drain your inbox (prints JSON list)
coop-peek                                    # number of unread messages
coop-agents                                  # list every agent id
```"""

COOP_WORKFLOW = f"""Recommended workflow:

1. At the start, `coop-broadcast` a short summary of your feature and
   which files you intend to touch.
2. Periodically `coop-recv` to read what your peers have sent {DASH} at
   minimum after major edits and before submitting.
3. If two agents need to modify the same file, coordinate explicitly
   (split the file, agree on one owner, or merge changes).
4. Keep messages short and focused: file names, function names, and
   one-sentence intents are usually enough.

Messages are not magic {DASH} your peers only know what you tell them.
"""


def coop_block(agent_id, partners, mcp_note=""):
    """CooperBench's cooperation block for this agent; with an MCP note, the
    note stands as its own paragraph between the commands and the workflow."""
    commands = COOP_COMMANDS.format(agent_id=agent_id, partner_str=", ".join(partners))
    middle = [mcp_note] if mcp_note else []
    return "\n\n".join([commands, *middle, COOP_WORKFLOW])


def joined_names(pairs):
    """`a` is `b`, `c` is `d`, and `e` is `f`: the pairs as one clause."""
    phrases = [f"`{alias}` is `{name}`" for alias, name in pairs]
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + ", and " + phrases[-1]


def mcp_sentence(tools):
    """The one sentence this arm adds to CooperBench's prompt, built from the
    tools themselves so it names whatever the hub really serves."""
    pairs = [(tool.alias, tool.name) for tool in tools if tool.alias.startswith("coop-")]
    if not pairs:
        return ""
    return ("Each of these commands is also available as a tool of the MCP server "
            f"`coordbench`, with the same inbox behind it: {joined_names(pairs)}.")


def task_text(run, agent, specs):
    """The agent's spec as CooperBench passes it: the feature file's text,
    unstripped, since coop.py hands read_text() straight to the prompt."""
    return SECTION_RULE.join(specs[number] for number in run.agents[agent].features)


def tool_call_counts(run):
    """How often each tool was called, from the hub's "tool" events."""
    counts = {}
    for event in run.events:
        if event["kind"] == "tool":
            counts[event["tool"]] = counts.get(event["tool"], 0) + 1
    return counts


class CoopArm(Arm):
    name = "coop"
    description = ("CooperBench's coop setting: one agent per feature, coordinating by "
                   "messages only.")
    needs = ()

    def layout(self, features):
        # coop.py sorts the features and gives agent1 the lowest, agent2 the
        # next, so the same pair gets the same numbering in both harnesses.
        return [AgentSpec(f"agent{i}", "peer", (number,))
                for i, number in enumerate(sorted(features), 1)]

    def tools(self, run, agent):
        return kit.messaging_tools(wait=False)

    def prompt(self, run, agent, specs):
        return SECTION_RULE.join(self.sections(run, agent, specs))

    def sections(self, run, agent, specs):
        """build_instruction's sections in its order. A run of one agent has
        no partners, and CooperBench then leaves the cooperation block out."""
        sections = [task_text(run, agent, specs), SUBMISSION_BLOCK]
        partners = run.peers(agent)
        if partners:
            sections.append(coop_block(agent, partners, self.mcp_note(run, agent)))
        return sections

    def mcp_note(self, run, agent):
        return mcp_sentence(self.tools_for(run, agent).values())

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run),
                "tool_calls": tool_call_counts(run)}
