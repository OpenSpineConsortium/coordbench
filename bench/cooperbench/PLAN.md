# The benchmark, pre-registered

What .github/workflows/bench.yml runs in real mode, written down before
any agent runs, so the pairs, the arms, the settings and the analysis are
fixed first and the numbers read afterwards. A change to this file after
the first real dispatch is a new benchmark under a new tag, not an
amendment of the one that ran.

## Question

Does Symphony's claim protocol (coordbench's sdlc arm: issues, a claim per
issue, a pull request each, a merge gate) let two Claude Code agents
finish CooperBench's feature pairs more often than CooperBench's own
coordination settings do, and how far does it close the gap to one agent
doing both features?

## Pairs

bench/cooperbench/pairs.txt: the flash subset whole, 20 tasks and 50
pairs, fixed on 2026-10-14 from the survey on GitHub's runners (bench.yml
in survey mode, tag survey-1, run 38549172370), which graded every
feature's gold patch of every task on a runner's disk, the three tasks
that download at test time included (openai_tiktoken 0, dottxt_ai_outlines
1706, llama_index 18813), which the cloud session's network policy had
kept out of the first list of 17 tasks and 43 pairs. The dispatch reads it
as `@bench/cooperbench/pairs.txt`. Every arm runs every pair of the list,
so the arms are compared on the same pairs.

## Arms

Five, in this order of interest: sdlc, the arm under test; solo, one
agent given both features, the ceiling; coop, CooperBench's default, two
agents with messages; coop-git, the two with a shared git remote as well
(the authors' "Curious case of miscoordination" setting); team, the
authors' later harness with a lead, a task list and a scratchpad. The
no-communication baseline coop-nocomm is left out to save usage, and can
be added as a sixth arm by the dispatch input; six arms over 50 pairs are
300 jobs, more than one matrix's 256, so a sixth arm takes two dispatches
of the tag.

## Settings

- Agents: Claude Code, the version setup.sh pins, through coordbench's
  claude-cli driver on CooperBench 63b9d44 with its one patch; model
  claude-sonnet-5-5 for every agent; the MCP tool surface; WebFetch and
  WebSearch off; no turn cap; 120 minutes per agent; one seed. Each
  (pair, arm) is one job on ubuntu-24.04, so no pair sees another's
  containers.
- Accounts: the owner's, one job at a time per account, every arm of a
  pair on the same account (plan.py), so an account's usage window is
  spent on a pair's arms together rather than on one arm; the order the
  jobs run in is GitHub's queue's, first in first out as far as it keeps
  it, so the arm rotation is a tendency, not a guarantee. Three or more
  accounts hold the whole list in one dispatch (250 jobs, under the 100
  an account's queue takes); on one or two, the same tag is dispatched
  again once the first dispatch has drained, and the resume carries on.
- Resume: a pair whose artifact holds a scored pair with no agent in
  Error is not run again; a pair that ended in Error (a usage limit among
  the causes) runs again on the next dispatch of the same tag, on a fresh
  runner, and its new artifact is the one the report reads, the earlier
  attempt's staying on GitHub under the same name until it expires.
- Judging: the free heuristics only, in the run; the model-judged
  measures (communication errors, symptoms, speech acts, emergent
  behaviours) are run afterwards by hand, on purpose, if at all.

## Primary outcome

eval.json's both_passed, CooperBench's own verdict: the pair passes when
the merged patch passes both features' hidden tests (a conflicting merge
passes only on agent1's patch, as 63b9d44 scores it). The rate is k/n over
every attempted pair, an error counting as a failure, the paper's
definition; the rate on finished pairs is reported beside it.

## Analysis, in report.py as it stands

1. The pass rate of each arm with a 95% Wilson interval and a bootstrap
   over task sets (2,000 resamples, report.py's BOOTSTRAP_ITERATIONS, seed 0), since pairs of one task are
   not independent.
2. sdlc against each other arm on the pairs both scored: the discordant
   counts and an exact McNemar test; the coordination gap against solo
   in points and relative.
3. The git ablation's columns for coop and coop-git, the pass route (naive
   merge, identical patches, lead alone), the naive-merge conflict rate
   and the semantic failure rate, the merge cascade's table.
4. Cost and time per pair, turns, the overhead of messaging, concrete
   references, difficulty curves, and one row per pair.

What 50 pairs can show: with the paper's rates (solo near 47%, coop near
26%) the discordant pairs of a solo-versus-coop comparison are about a
third of the list, and an exact McNemar test at that size detects a gap
of roughly 25 points or more; a gap of 10 points between sdlc and coop
will not reach significance here and is reported as an estimate with its
interval. A second seed on the pairs where the arms disagree is the
planned follow-up, not part of this run.

## What is not claimed

The authors' numbers are not reproduced: their agents were codex with
gpt-5.5 (the ablation) or several models through OpenHands and Mini-SWE
(the paper); ours are Claude Code on a subscription. The comparison is
between arms on the same agent, pairs and day; the paper's figures sit
beside ours in the report for orientation only (METRICS.md).
