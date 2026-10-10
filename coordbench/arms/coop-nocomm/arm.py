"""The coop-nocomm arm: CooperBench's coop setting with messaging banned.

The paper's communication ablation (Sec 5, Fig 5(a), App E Table 6) runs
the coop setting with the communication tool banned. CooperBench at
63b9d44 runs it as `cooperbench run --no-messaging`: its claude_code
adapter then installs no coop-* command and builds the prompt with
build_instruction given no agents (src/cooperbench/agents/_coop/
prompt.py), which is the feature spec and the submission block and
nothing more, so the agents are not told that a partner exists. This
arm is that run: coop's layout (one agent per feature, agent1 with the
lowest), no tools at all, and that prompt, which the tests check against
CooperBench's own function when its source is at hand. The scorer merges
the two patches as it merges a coop pair's, so a pair's outcome here sits
beside the same pair's under coop, which is what the ablation compares.

Like solo, the directory holds no skill and no .mcp.json, so a Claude
Code that loads the plugin sees the prompt and nothing else; there is no
MCP tool to leave out, so the bash tool surface is the same as the mcp
one. The coop arm's class is loaded from the built-in coop directory, as
coop-git loads it, so the layout and the spec's text are coop's own.
"""

import importlib.util

from arm import SUBMISSION_BLOCK, TOOL_SURFACES, builtin_arms_dir


def load_coop_module():
    """The built-in coop arm's module, found through coordbench's own arms
    directory, so a copy of this directory elsewhere still finds it."""
    path = builtin_arms_dir() / "coop" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_coop_for_nocomm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coop = load_coop_module()


class CoopNoCommArm(coop.CoopArm):
    name = "coop-nocomm"
    description = ("CooperBench's coop setting with messaging banned (--no-messaging): one agent "
                   "per feature, told nothing of a partner, with no coordination tools.")
    needs = ()
    tool_surfaces = TOOL_SURFACES

    def tools(self, run, agent):
        return []

    def sections(self, run, agent, specs):
        # build_instruction with no agents: the cooperation block, and the
        # git block that only follows it, are left out.
        return [coop.task_text(run, agent, specs), SUBMISSION_BLOCK]
