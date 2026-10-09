"""The coop-git arm: CooperBench's coop setting with a shared git remote.

Everything the coop arm does, plus a git remote named `team` that every
agent can push to and fetch from, and CooperBench's git block in the
prompt, which asks each agent to merge its peers' branches before it
writes its patch. Messages stay on, as they do in CooperBench when it runs
with --git and without --no-messaging; the authors' later ablation did
not record whether theirs were on, and their default is the one choice
that needs no guess.

The git block is CooperBench's _git_block (src/cooperbench/agents/_coop/
prompt.py at 63b9d44, MIT licence), word for word. It makes promises the
runner keeps, as CooperBench's build_git_setup_command (_coop/runtime.py)
keeps them there: in /workspace/repo the remote `team` points at the
shared repository (the run option "git_remote"), a branch named after the
agent's id is checked out and pushed, and `team/main` holds the commit the
agents started from. That is why this arm needs "git_remote". The skill
restates the prompt and adds nothing, as coop's does.

The coop arm's class is loaded from the built-in coop directory rather
than copied, so the two arms share one copy of CooperBench's coop text
and coop-git stays coop plus one block.
"""

import importlib.util

from arm import builtin_arms_dir


def load_coop_module():
    """The built-in coop arm's module. It is found through coordbench's own
    arms directory, so a copy of this directory elsewhere still finds it."""
    path = builtin_arms_dir() / "coop" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_coop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coop = load_coop_module()
DASH = coop.DASH

GIT_BLOCK = f"""## Git collaboration {DASH} MERGE IS REQUIRED BEFORE SUBMITTING

A shared git remote named `team` is already configured in this repo.
- Your branch: `{{agent_id}}` (already created and pushed)
- Partner branches: {{partner_branches}}
- Base reference: `team/main` (pristine starting state)

### The submission rule the bench actually enforces

The bench evaluates each agent's `patch.txt` against EVERY feature's
test suite.  If your `patch.txt` only contains your own work, the
peer feature's tests will fail with `ImportError` because the
symbols they introduced aren't in your tree.  **You MUST pull in
your peers' branches before generating your final `patch.txt`** {DASH} or
your submission will be incomplete by construction.

### Required final sequence {DASH} run this verbatim before exiting

```bash
# 1. Commit your own work so it's on your branch tip.
cd /workspace/repo
git add -A
git commit -m 'wip: my work' || true   # ok if nothing to commit

# 2. Push so peers can fetch you (optional but recommended).
git push team {{agent_id}} --force || true

# 3. Pull in every peer's branch.  Use --no-edit to take the default
#    merge commit message.  || true so a clean-no-op doesn't abort.
{{partner_merge_lines}}

# 4. Rebuild patch.txt from the MERGED tree.  This is the artifact the
#    bench evaluates {DASH} it must contain both your work and your peers'.
git diff team/main..HEAD > patch.txt

# 5. Sanity-check: the diff should mention symbols you didn't write
#    yourself (your peers' contributions).
wc -l patch.txt
head -30 patch.txt
```

### During the run

```bash
git fetch team                                   # see what peers published
git branch -r                                    # list every team branch
git log team/{{first_partner}} --oneline -10       # inspect a peer's history
git show team/{{first_partner}} -- path/to/file    # inspect a peer's change
```

If you skip the merge step, you will lose points the bench would
otherwise have awarded.  The team-mode metric `tasks_done` only
measures coordination, not correctness {DASH} correctness comes from
`patch.txt` containing the union of the team's work."""


def merge_line(partner):
    return f"  git fetch team && git merge --no-edit team/{partner} || true   # pull in {partner}'s work"


def git_block(agent_id, partners):
    """CooperBench's git block for this agent and its partners, in order."""
    return GIT_BLOCK.format(
        agent_id=agent_id,
        partner_branches=", ".join(f"`team/{partner}`" for partner in partners),
        partner_merge_lines="\n".join(merge_line(partner) for partner in partners),
        first_partner=partners[0],
    )


class CoopGitArm(coop.CoopArm):
    name = "coop-git"
    description = ("CooperBench's coop setting with a shared git remote: one agent per "
                   "feature, messages on, each merging its peers' branches before it submits.")
    needs = ("git_remote",)

    def sections(self, run, agent, specs):
        # build_instruction adds the git block after the cooperation block,
        # and only when the agent has partners to merge.
        sections = super().sections(run, agent, specs)
        partners = run.peers(agent)
        if partners:
            sections.append(git_block(agent, partners))
        return sections
