# Research for the collaboration experiment (the record for OpenSpineConsortium/sdlc-template#10)

What a research run found on 2026-10-09 before the design of the
experiment (bench/cooperbench/README.md, coordbench/README.md) was
settled. Four researchers made 179 claims; the 130 marked load-bearing
were each put to three skeptics who tried to refute them from the
source, and 207 of the 390 verdicts were in when the run was stopped to
save the main account's usage. Everything below survived that
verification; the claims that were not reached (all of GitHub's and
Claude's mechanics beyond those listed) were either checked later by the
sessions that built sdlc-template's setup pages (its docs/setup/) or
left out. The run's raw claims and its workflow script were kept there
as working material until the harness was built, and were removed then;
this page is the condensed record.

The network policy blocked arxiv.org, openreview.net, docs.github.com and
most paper hosts, so the paper was read from the authors' own copy in
cooperbench/website (commit 97dbb0a) and GitHub's docs from the
github/docs source (commit 9f65179).

## What CooperBench measured (verified)

- CooperBench, arXiv 2601.13295 (venue unconfirmed). 652 tasks from 12
  repositories in 4 languages; a task is one repository state plus two
  features, each with hidden unit tests, and each agent sees only its own.
- Arms: Solo (one agent implements both features), Coop with messaging,
  and Coop with messaging banned; a small extra run with 3 and 4 agents.
- Coop is isolation: each agent works in its own container on its own
  branch, neither sees the other's code, the only channel is free-text
  messages, each agent is capped at 100 actions (messages count), and the
  two patches are merged once, at the end. Success means the merge
  succeeds and the merged code passes both features' tests.
- Per-model results from the paper's Table 5, successes out of 652, Solo
  then Coop: GPT-5 315 and 183 (48.3% and 28.1%); Claude Sonnet 4.5 307
  and 168 (47.1% and 25.8%). The website's leaderboard differs on the
  Coop side, so cite the paper's table.
- The paper states the gap three ways: 30% lower (abstract), about half
  (introduction and conclusion), 41% lost (Appendix C).
- Messaging neither helped nor hurt success (not significant); it cut
  merge conflicts and took up to 20% of steps.
- Adding agents lowered success: 68.6% with 2, 46.5% with 3, 30.0% with
  4, on 46 tasks, model not named.
- Union merge and a small learned resolver account for much of Coop's
  success: GPT-5 with messaging went from 20.42% (naive merge) to 27.90%.

## What it did not show

- It never tested a shared workspace. Workspace isolation is fixed in
  every Coop run; the claim that the authors trace the failure to
  isolation was refuted 3 to 0.
- So the result is not "too much collaboration hurts". It is that two
  agents coordinating through messages alone, unable to see each other's
  code, do worse than one agent doing both features.
- The authors' own later ablation (codex with gpt-5.5, 50 pairs, one
  seed, not peer reviewed; numbers verified, the generalisation refuted
  2 to 1): messaging-only Coop 26%, Solo 48%, Coop with a shared git
  remote 56%, team mode with a lead and a shared task list 62%.

## Related evidence (verified)

- Anthropic, multi-agent research system: token spend alone explained 80%
  of performance variance on BrowseComp, so arms must be compared at
  matched budgets; coding is a poor fit for multi-agent systems today.
- Anthropic, C compiler with 16 agents on a shared git upstream: merge
  conflicts were frequent but the agents resolved them; speedup depended
  on how independent the tasks were, and on one tightly coupled task 16
  agents gave no benefit until the work was re-split.
- Claude Code docs: a single session or subagents beat an agent team for
  sequential work, same-file edits or many dependencies; teammates in a
  shared workspace overwrite each other's files; returns diminish with
  team size; agent teams use about 7x the tokens of a standard session.
- harness-lab (single author, pre-registered): a six-agent harness solved
  nothing a single agent did not, at 2.8x the cost; 40 tasks with 3
  repeats gives intervals too wide for small effects.
- Studies of agent pull requests on GitHub: textual conflicts in 27.67%
  of 107,026 simulated merges; per-PR tests passing while the shared
  suite fails, so success must be judged on the merged result.
- Anthropic on eval infrastructure: setup alone moved scores 6 points,
  concurrency is a confounder, and trials must start clean, since Claude
  has used earlier trials' git history.

## GitHub mechanics verified so far

- A workflow on GITHUB_TOKEN can start others through workflow_dispatch
  (with actions: write); the dispatched workflow must exist on the
  default branch. A push by GITHUB_TOKEN starts no push workflows; a
  GitHub App installation token does.
- GITHUB_TOKEN gets 1,000 REST requests an hour per repository, shared by
  every job polling the API.
- `matrix: ${{ fromJSON(vars.X || '[]') }}` is allowed (an unset variable
  reads as empty), and each matrix leg can have its own concurrency group.
- Not verified: auto-merge per plan, the artifact and timeline endpoints,
  and every Claude-side mechanic (claude-code-action's execution_file
  contents, concurrent sessions on one subscription token, agent teams).

## What this meant for the design

On GitHub, sdlc-template's claim branches are visible to every agent
through the shared remote, so the PR-gated protocol is closer to the
ablation's shared-remote arm than to CooperBench's isolated Coop arm.
The honest question for the experiment is therefore which of these wins
on the same tasks at the same budget, which is what coordbench/
was built to measure (its arms solo, coop, coop-git, team and sdlc map onto
these four):

1. Solo: one agent takes every issue in turn.
2. PR-gated with visibility: sdlc-template as it stands, N agents.
3. Isolated, messages only: N agents that may not fetch each other's
   branches and talk only in issue comments, a replica of CooperBench Coop.
4. Shared workspace: N agents committing to one shared branch with a
   shared task list, the nearest form of a shared project.

Hold equal across arms: model, effort, per-arm token or turn budget,
infrastructure and concurrency limits, the task suite and its order; start
every trial from a fresh repository; interleave arms in time. Score on the
final merged default branch with each task's acceptance tests, and record
makespan, CI failures, rebases and force pushes, textual conflicts, and
tokens. Use more tasks than harness-lab's 40, and fix the analysis before
the first run.
