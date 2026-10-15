# The curse of coordination, and the claim protocol against it: pilot of 2026-10-15

The question (bench/cooperbench/PLAN.md): CooperBench found that two
agents coordinating on a pair of features pass far less often than one
agent given both, the curse of coordination; does Symphony's claim
protocol, coordbench's sdlc arm (an issue per feature, a claim, a pull
request each, a merge gate), let two Claude Code agents finish the pair
more often than CooperBench's own coordination settings, and at what
cost in time and tokens? This directory is a pilot of that benchmark
and the preview of its report: the same report.py and summary.py that
a full run renders, over the pairs the pilot scored, so the shape of
the result is visible before the usage a full run takes is spent.

## How the pilot ran

- Five arms on every pair: solo (one agent, both features, the
  ceiling), coop (CooperBench's default, two agents with messages),
  sdlc (the arm under test), coop-git (two agents with a shared git
  remote), team (the authors' lead, task list and scratchpad harness).
- Every agent claude-sonnet-5-5 through Claude Code 2.1.300, on
  CooperBench 63b9d44 with its one patch (dataset tree 9c9e5802e0);
  coordbench at the commit of this directory's pull request; the sdlc
  arm's protocol symphony-protocol main at c7f0d84, which carries the
  fixes after 0.4.1 that are merged and unreleased, as the owner asked;
  WebFetch and WebSearch off; 45 minutes per agent; one seed.
- The route is the session route (bench/cooperbench/session/README.md):
  the benchmark ran from one cloud session with no Claude token in its
  environment, each bench agent a subagent of that session, five lanes
  so five pairs ran at once on one four-CPU host. It differs from the
  runners' route of PLAN.md in four ways, each the same for every arm:
  the agents are subagents and carry the session's project
  instructions in their context (about 78 thousand cached tokens per
  agent, equal across arms); their steps are not streamed, so their
  wall time is the runner's measure and their output tokens a lower
  bound; the timeout is 45 minutes, not 120; and five pairs shared one
  host rather than one runner each.
- The list was the 43 pairs of 17 tasks the cloud session's network can
  grade (the three tasks that download at test time, openai_tiktoken 0,
  dottxt_ai_outlines 1706 and llama_index 18813, left out), ordered
  task by task with every arm of a pair together, queue tag curse-1.
  The run started at 05:46 UTC; at 05:31 and 05:47 UTC the owner asked
  for the full run to be skipped and the number of experiments to be a
  choice, which reached the session at 06:06 UTC, when the queue was
  paused to the pairs of click 2068 already under way; at about 06:10
  UTC the session hit its account's usage limit (reset 09:10 UTC) and
  its worker restarted, which killed the five runs then in flight.
- So the pilot is one task, pallets_click 2068, with three pairs
  complete under all five arms (f1_f6, f2_f10, f5_f7), one more under
  solo alone (f5_f11), and five runs cut by the restart (f5_f11 under
  the four two-agent arms, f6_f7 under solo), whose agents are
  recorded as Error. CooperBench scores an errored agent's patch as it
  stands, so report.md counts those five as attempts (two of them pass
  on the patch one agent had merged before the cut); summary.md sets
  them apart, since a cut run's clock ran to its deadline and its
  agents' work is partial. A full run reruns such pairs (PLAN.md,
  Resume) and they are not counted.

## What the report shows, with the pilot's numbers

report.md is the CooperBench-parity report (bench/cooperbench/METRICS.md
maps each section to the paper's figure); summary.md and summary.json
are the latency, token and cost side; pairs.csv has one row per
(pair, arm) with every figure, for a later pass to reuse. On three
complete pairs no difference between arms can be read; the tables
below are the shape of the result, not the result.

### The curse: solo against coop, and the arm under test against both

Pass is CooperBench's own verdict, eval.json's both_passed: the merged
patch passes both features' hidden tests. Over the complete pairs:

| arm | complete pairs | passed | rate | 95% Wilson |
|---|---|---|---|---|
| solo | 4 | 3 | 75% | 30-95% |
| coop | 3 | 2 | 67% | 21-94% |
| sdlc | 3 | 2 | 67% | 21-94% |
| coop-git | 3 | 2 | 67% | 21-94% |
| team | 3 | 2 | 67% | 21-94% |

Every arm passed f1_f6 and f5_f7 and every arm, solo included, failed
f2_f10: its feature 10 hidden test mocks Popen with a wait() that takes
no timeout argument, and feature 2 adds a timeout to that very call, so
a patch that implements both as specified fails feature 10's test. That
is a conflict built into the pair, not a coordination failure, and the
paper's eight coop runs failed it too (strata.md). The report's
Coordination gap section takes solo minus each arm on the pairs both
scored and the Paired comparisons section gives the exact McNemar test
over them; at this size every p is 1. PLAN.md says what the full list
can show: with the paper's rates, a gap of about 25 points between two
arms reaches significance over 50 pairs, a gap of 10 does not and is
reported as an estimate with its interval.

### What the protocol did

The report's Coordination section sums the claim protocol's counters
over the sdlc pairs: 6 claims, 6 pull requests opened, 6 merge
attempts, 6 merges, 0 merge conflicts, every agent merged in every
pair, 84 seconds from the hub's start to the first merge (median). The
team harness's board beside it: 6 tasks, 6 done, 25 seconds to the
first claim. The Communication section is the cost of talking: coop's
agents spent 17.3% of their tool calls sending messages (6.5 turns per
pair), coop-git 12.9%, sdlc 8.4% (3.2 turns), team 8.6%; the paper's
Claude spent 20% of its events on messages. In the pilot's coop pairs
the two agents converged on byte-identical patches by mirroring each
other's hunks (the Pass route column reads N, identical patches), the
paper's expectation failure in miniature.

### Latency, tokens and cost

From summary.md, over the complete pairs, medians; tokens are the sum
over the pair's agents of input, cache read, cache write and output
(the output a lower bound); cost at list prices (Sonnet 5.5: 2 dollars
per million input, 10 output, 0.20 cache read, 2.50 cache write), an
estimate on the same scale for every arm, since nothing was billed:

| arm | wall seconds per pair | agent seconds, summed | tokens per pair (mean) | cost per pair | cost per passing pair | messages per pair |
|---|---|---|---|---|---|---|
| solo | 127 | 113 | 1.26 M | $0.42 | $0.56 | 0 |
| coop | 261 | 491 | 3.43 M | $1.05 | $1.58 | 8.7 |
| sdlc | 213 | 361 | 3.49 M | $1.05 | $1.57 | 4.3 |
| coop-git | 239 | 431 | 3.71 M | $1.11 | $1.67 | 6.3 |
| team | 211 | 335 | 3.33 M | $1.06 | $1.59 | 4.0 |

Paired on the same three pairs, every two-agent arm took about twice
solo's wall time and three times its tokens; sdlc took 0.81 of coop's
wall time and 0.74 of its summed agent seconds for the same tokens
(summary.md's paired table). The full report's Cost and time section
gives the same per pair in CooperBench's own form; its token column
reads as a lower bound under the session driver, which is why
summary.md exists.

### The rest of the report

report.md also renders, for a full run: CooperBench's three ways of
counting a rate; the git ablation (coop against coop-git); the pass
route (naive merge, identical patches, lead alone); the naive-merge
conflict rate and the semantic failures; the merge cascade of the
paper's Table 6; speech acts and failure modes by the free heuristics
(the model-judged ones run only on purpose); concrete references;
difficulty curves (difficulty.json); the dataset's gradeability; our
runs against the paper's published runs on the same pairs (the paper's
Claude failed all four of these pairs under coop; ours passed two); and
every pair under every arm.

## Scaling up, by strata

The benchmark's natural strata, from the dataset and the paper's
published runs (strata.md, by bench/cooperbench/session/strata.py):

- language, by repository: python 35 pairs, python+c (pillow) 6, rust
  (typst) 4, go (go_chi) 2, python+rust (tiktoken) 3;
- whether the two gold patches conflict when merged, the paper's
  Table 4: 37 conflict, 13 merge clean;
- size, the two gold patches' added lines in terciles: small (under 51
  lines), medium, large (133 and over);
- published difficulty, the share of the paper's published coop runs
  (eight per pair, three for typst) that passed the pair: none (19
  pairs), few, under half (26), many, half or more (5); and whether the
  paper's Claude passed it (15 pairs; it did not run on typst).

The pilot's pairs are all python, all gold-conflict, published none or
few: one cell. A run grows along a nested order: `queue.py add
--sample N --stratify language,conflict,published` queues the first N
pairs of an order a seed fixes, one stratum at a time, under every arm;
adding again with a larger N queues only the new pairs, so a run of 8
grown to 16 keeps its 8. The commands are in
bench/cooperbench/session/README.md.

What a step costs, from the pilot's rates (every arm of a pair reads
about 15 million tokens, nearly all cache reads, and takes about 17
lane-minutes): the session's account hit its usage limit after about 21
pair-runs, four pairs under five arms, in one five-hour window at five
lanes. So on this account a step of 8 pairs (40 runs, 1 per stratum
where the strata allow) is about two windows, 16 pairs four, the 43
pairs of the list about eleven windows, two and a half days of one
account; the runners' route (bench.yml) with the owner's several
accounts in the environment bench, as PLAN.md's Accounts paragraph
describes, runs the same list in a day. A step is chosen by its pair
count, and the paused queue of this pilot, with its remaining click
2068 pairs, resumes with `queue.py resume`.

## Files

- report.md: report.py's output over the pilot's runs, every section.
- summary.md, summary.json, pairs.csv: summary.py's latency, token and
  cost side, per arm and per pair.
- strata.md: strata.py's table of the 50 flash pairs with their strata
  and the paper's published results.
- difficulty.json: the difficulty curves' data.
- queue.jsonl: the pilot's queue as it was left, every item's status.
- raw/: for each run, result.json, eval.json, metrics.json, judge.json,
  harness.json, events.jsonl, the agents' messages and patches, as
  bundle.py copies them, held to the repository's plain-text rule (a
  JSON file with its non-ASCII escaped, a final newline added where
  one was missing); the transcripts stay outside the repository.
