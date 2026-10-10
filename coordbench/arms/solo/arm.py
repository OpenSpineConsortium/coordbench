"""The solo arm: CooperBench's solo setting as a coordbench plug-in.

One agent implements every feature of the pair, with nobody to talk to and
no coordination tools. It is the baseline the other arms are measured
against: a pair that solo solves and coop does not is a cost of
coordinating, not of the task.

The prompt is CooperBench's own (63b9d44, MIT licence), built the way its
solo runner builds it: src/cooperbench/runner/solo.py puts each feature's
spec under a "## Feature <n>" heading and joins them with a rule, and
build_instruction in src/cooperbench/agents/_coop/prompt.py, given no
partners, adds only the submission block after another rule. The text is
kept identical so a solo run through coordbench can be set beside a solo
run through CooperBench's runner; the tests check it against CooperBench's
own function when its source is at hand.

The plugin directory holds no skill and no .mcp.json: CooperBench's solo
agent had no MCP server and no skill, so a Claude Code that loads this
plugin sees nothing beyond the prompt, as the authors' agent did. What is
here is the plugin's name (.claude-plugin/plugin.json), arm.json and this
file.
"""

import kit
from arm import TOOL_SURFACES, AgentSpec, Arm, SUBMISSION_BLOCK

# The rule CooperBench puts between the features and between sections.
SECTION_RULE = "\n\n---\n\n"


def combined_task(features, specs):
    """The specs as solo.py combines them: in the order the task names the
    features, unsorted, and each spec as read from its file, unstripped."""
    return SECTION_RULE.join(f"## Feature {number}\n\n{specs[number]}" for number in features)


def tool_call_counts(run):
    """How often each tool was called, from the hub's "tool" events; always
    empty here, kept so every arm's metrics carry the same keys."""
    counts = {}
    for event in run.events:
        if event["kind"] == "tool":
            counts[event["tool"]] = counts.get(event["tool"], 0) + 1
    return counts


class SoloArm(Arm):
    name = "solo"
    description = "CooperBench's solo setting: one agent implements every feature, alone."
    needs = ()
    # No tools, so the bash tool surface is the same as the mcp one.
    tool_surfaces = TOOL_SURFACES

    def layout(self, features):
        return [AgentSpec("agent1", "solo", tuple(features))]

    def prompt(self, run, agent, specs):
        task = combined_task(run.agents[agent].features, specs)
        return SECTION_RULE.join([task, SUBMISSION_BLOCK])

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run),
                "tool_calls": tool_call_counts(run)}
