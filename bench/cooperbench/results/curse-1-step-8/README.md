# The curse of coordination, and the claim protocol against it: the step of 8 pairs, 2026-10-15

The question (bench/cooperbench/PLAN.md, and the pilot in
results/curse-1-pilot/): CooperBench found that two agents coordinating
on a pair of features pass far less often than one agent given both,
the curse of coordination; does Symphony's claim protocol, coordbench's
sdlc arm (an issue per feature, a claim, a pull request each, a merge
gate), let two Claude Code agents finish the pair more often than
CooperBench's own coordination settings, and at what cost in time and
tokens? This directory is the first step after the pilot: the first 8
pairs of the nested stratified order, one pair from each of 8 of the
benchmark's 14 strata, under all five arms, 40 runs, the step the owner
chose on 2026-10-15 at 10:32 UTC from the sizes the pilot's README
offered (8, 16 or all 43 pairs).

## How the step ran

- The sample: `queue.py add --sample 8 --stratify
  language,conflict,published` draws the first 8 pairs of the nested
  stratified order over the 43 pairs of the list the cloud session's
  network can grade (the three tasks that download at test time left
  out), equal allocation across the strata, seed 0; strata.md records
  the strata and the draw, which `strata.py --skip ... sample 8`
  reproduces. The pairs, with their stratum (language, whether the two
  gold patches conflict, the share of the paper's published coop runs
  that passed):
  - pallets_jinja 1621 f2_f9: python, conflict, few (2 of 8 published runs passed)
  - pallets_jinja 1559 f4_f10: python, clean, few (3 of 8)
  - pallets_click 2068 f5_f11: python, conflict, none (0 of 8)
  - pillow 68 f1_f5: python+c, conflict, none (0 of 8)
  - huggingface_datasets 6252 f4_f6: python, clean, many (8 of 8)
  - go_chi 56 f1_f5: go, conflict, none (0 of 8)
  - dottxt_ai_outlines 1655 f6_f7: python, conflict, many (4 of 8)
  - typst 6554 f5_f9: rust, conflict, few (1 of 3)
- Five arms on every pair, as in the pilot: solo (one agent, both
  features, the ceiling), coop (CooperBench's default, two agents with
  messages), sdlc (the arm under test), coop-git (two agents with a
  shared git remote), team (the authors' lead, task list and scratchpad
  harness). Every agent claude-sonnet-5-5 through Claude Code 2.1.300,
  on CooperBench 63b9d44 with its one patch (dataset tree 9c9e5802e0);
  coordbench at 43c2e3d, the pilot's pull request (#18) as merged,
  since the harness was held still while the step ran and this
  directory's own pull request came after; the sdlc arm's protocol
  symphony-protocol main at c7f0d84, the pilot's, kept although F18
  (0536cc3) and F19 (1a79df9) merged during the day, so the step's sdlc
  runs compare with the pilot's; WebFetch and WebSearch off; 45 minutes
  per agent; one seed.
- The route is the session route (bench/cooperbench/session/README.md),
  as in the pilot: one cloud session with no Claude token in its
  environment, each bench agent a subagent of that session, four lanes
  from 10:39 UTC so four pairs ran at once on one four-CPU host, and
  one lane for the typst pair at the end, one run at a time. The
  pilot's README lists the four ways this differs from the runners'
  route, each the same for every arm.
- The queue was the pilot's (tag curse-1), which already held every
  pair of the list, paused: the 8 were released with `queue.py resume
  --pairs` and the sample record written by the same hand, since `add
  --sample` adds only pairs the queue lacks; the pilot's click 2068
  pairs stay in the tag, so the report here is rendered over a copy of
  the step's 40 runs alone (session/subset.py), and the pilot's pairs
  are in the pilot's directory.

## What happened on the way

Five runs were spoiled by the host and run again; every run counted
below is a complete attempt whose agents returned on their own.

- At 10:48 UTC the disk filled while the datasets 6252 image (13.3 GB)
  was built beside the others. coop-git jinja 1559's agents stopped on
  no space: the attempt was set aside and the pair rerun. sdlc click
  2068 f5_f11's finish died after its agents had returned (896 and 824
  seconds): the finish was run again by hand at 12:15 UTC, so its
  result.json wall time, 5816 seconds, holds 82 minutes with the
  harness dead; its agents' own times are in its timings, and
  summary.md's medians are little moved by it.
- solo jinja 1621 f2_f9's first attempt ended with an empty patch: the
  session's permission classifier blocked the agent's final `git diff >
  patch.txt`, a fail that is not the model's. The attempt was set aside
  and the pair rerun, and passed.
- At 12:36 UTC the disk filled again while the typst image was built
  beside the pillow one. team go_chi's prepare failed and the pair was
  rerun; solo pillow had three stranded attempts before the one that
  counts; coop go_chi's finish scored the pair (both_passed true) and
  died before the judge and the verdict, which were made by hand at
  12:44 UTC with the same commands; and a finish run again after one
  that died raised FileNotFoundError on a workspace the teardown had
  taken. This pull request's lane.py notes such a workspace
  (gone_workspaces in harness.json) and, where an earlier finish wrote
  result.json, stops before run.py could write empty patches and a
  fresh score over the only copy that finish left.
- The typst image needs the disk to itself: 10.1 GB, with Docker's
  build cache released first (a restart of the daemon freed the 17 GB
  that `docker builder prune` reported as in use after two builds had
  been killed). It was built last, alone, in four minutes, and its five
  runs went on one lane, one at a time, since a two-agent typst run's
  workspaces, each a copy of the repository with its cargo build, take
  about 11 GB; the disk's free space fell to 2.6 GB during the sdlc
  run and recovered at its teardown. The five ran from 14:40 to 16:49
  UTC, after the account's usage window had reset at 14:10.
- The set-aside attempts are outside the repository (previous-*.tar.gz
  beside each run), as the transcripts are.

## The result

report.md is the CooperBench-parity report (bench/cooperbench/METRICS.md
maps each section to the paper's figure); summary.md and summary.json
the latency, token and cost side; pairs.csv one row per (pair, arm).

### The curse: solo against coop, and the arm under test against both

Pass is CooperBench's own verdict, eval.json's both_passed: the merged
patch passes both features' hidden tests.

| arm | pairs | passed | rate | 95% Wilson |
|---|---|---|---|---|
| solo | 8 | 5 | 62.5% | 31-86% |
| coop | 8 | 6 | 75.0% | 41-93% |
| sdlc | 8 | 6 | 75.0% | 41-93% |
| coop-git | 8 | 6 | 75.0% | 41-93% |
| team | 8 | 6 | 75.0% | 41-93% |

Every pair under every arm (report.md, Every pair): outlines 1655 and
pillow 68 fail under all five arms; datasets 6252, click 2068 f5_f11,
jinja 1559, jinja 1621 and typst 6554 pass under all five; go_chi 56 is
the one pair that separates them, failed by solo and passed by every
two-agent arm. So the coordination gap of the paper (solo minus the
arm) is -12.5 points for every two-agent arm here, where the paper's
Claude had +21: on these pairs two agents did not pass less often than
one. The paired tests cannot tell any two arms apart at this size
(every exact McNemar p is 1; PLAN.md's power note: a gap of 25 points
needs about 50 pairs). On the seven of these pairs the paper's
published Claude coop runs cover, they passed 2 (datasets 6252 and
jinja 1559); ours passed 5 under coop and under sdlc, p = 0.25
(report.md, Against CooperBench's published runs).

### What the protocol did

The claim protocol's counters over the eight sdlc pairs (report.md,
Coordination): 16 claims, 16 pull requests opened, 16 merge attempts,
16 merges, 0 merge conflicts, every agent merged in every pair, 106
seconds from the hub's start to the first merge (median). The team
harness's board: 16 tasks, 16 done, 31 seconds to the first claim. The
naive merge of the two agents' final patches conflicted in 0 of 8 pairs
under coop and sdlc, 1 of 8 under coop-git and 4 of 8 under team
(report.md, Naive-merge conflicts); team's six passes came three on the
naive merge and three on the lead's patch alone. Of sdlc's six passes,
five passed on identical patches, and under sdlc and coop-git each
agent's patch alone passed both features' tests in 6 or 7 of the 8
pairs: there each agent's final patch holds the other's merged work,
since both pull the shared main before they finish, so identical
patches here are the protocol's synchronisation and not, by themselves,
duplicated work (under coop, where nothing is shared but messages, the
same count is 1). The cost of talking (report.md, Communication): coop
6.1 messages per pair and 19.6% of its tool calls spent sending,
coop-git 5.6 and 13.5%, team 4.1 and 10.6%, sdlc 3.8 and 9.3%.

### Latency, tokens and cost

From summary.md, over the eight pairs; tokens are the sum over the
pair's agents of input, cache read, cache write and output (the output
a lower bound); cost at list prices (Sonnet 5.5: 2 dollars per million
input, 10 output, 0.20 cache read, 2.50 cache write), an estimate on the
same scale for every arm, since nothing was billed:

| arm | wall seconds per pair (median) | agent seconds, summed (median) | tokens per pair (mean) | cost per pair | cost per passing pair | messages per pair |
|---|---|---|---|---|---|---|
| solo | 219 | 201 | 1.74 M | $0.72 | $1.15 | 0 |
| coop | 335 | 405 | 3.02 M | $1.17 | $1.56 | 6.1 |
| sdlc | 529 | 737 | 3.58 M | $1.23 | $1.63 | 3.8 |
| coop-git | 308 | 525 | 4.12 M | $1.41 | $1.87 | 5.6 |
| team | 296 | 443 | 3.74 M | $1.35 | $1.80 | 4.1 |

Paired on the same pairs (summary.md's paired table), every two-agent
arm took 1.9 to 2.5 times solo's tokens and 1.8 to 2.3 times its
estimated cost, for 1.35 to 2.4 times its wall time. Against coop, sdlc
took 1.58 times the wall time and 1.82 times the summed agent seconds,
for 1.14 times the tokens and 1.09 times the cost: the protocol's steps
(a claim, a pull request, the gate) are the two agents' time, and its
agents sent the fewest messages. coop-git and team took about coop's
wall time (0.92 and 0.88 of it) for 1.30 and 1.06 times its tokens.
sdlc's mean minutes per pair in report.md (19.6) holds click 2068
f5_f11's 82 minutes with the harness dead; the medians above do not
move with it.

### What this step says

- The curse of coordination did not appear on these pairs: the four
  two-agent arms passed 6 of 8 to solo's 5 of 8, and the one pair that
  separated them went the two-agent way. The paper's gap is a property
  of its agents and harness (OpenHands, 2025 models, the legacy merge
  cascade on its Claude rows); Claude Code's Sonnet 5.5 under
  CooperBench's own coop setting passed 5 of the 7 published pairs here
  where the paper's Claude passed 2. With no curse to remove, this step
  cannot show the protocol removing it.
- The protocol arm matched every two-agent arm's pass rate with the
  cleanest record (every pull request merged, no conflict, the fewest
  messages) and cost the most time of the four. What it costs is
  latency, not tokens: 1.6 times coop's wall time for 1.14 times its
  tokens. Whether that buys passes where coop conflicts cannot be read
  from 8 pairs in which coop's naive merges never conflicted; team's 4
  conflicts of 8 are where such pairs would show it.
- Two agents cost about twice one agent's tokens under every
  coordination setting, mostly because each agent carries its own
  transcript through every turn (about 95 percent of every agent's
  input is a prompt-cache read; the two agents of a pair print only 6
  to 25 percent of the same lines:
  /mnt/project-files/experiments/curse-of-coordination/xblossom/ in
  the project, outside this repository).
- The same code and the same queue grow the run: `queue.py add --sample
  16 ...` adds the next 8 pairs of the order (about four of this
  account's five-hour windows at four lanes, by this step's rates:
  each two-agent pair-run read 3 to 5 million tokens and took 3 to 12
  minutes, typst's 9 to 12), `--sample 43` the rest. The pilot's README
  has the sizes and the runners' route for the owner's several
  accounts. A later step may move the sdlc arm to symphony-protocol's
  newer main (F18 0536cc3, F19 1a79df9 and F2 a3a5b4d merged after
  c7f0d84, all unreleased at 0.4.1), at the cost of comparability with
  this step and the pilot.

## Files

- report.md: report.py's output over the step's runs, every section.
- summary.md, summary.json, pairs.csv: summary.py's latency, token and
  cost side, per arm and per pair.
- strata.md: strata.py's table of the 50 flash pairs with their strata
  and the paper's published results, and the step's draw.
- difficulty.json: the difficulty curves' data.
- queue.jsonl: the step's 40 items as the queue left them, all done
  (subset.py writes the sample's lines; the tag's queue also holds the
  pilot's items and the rest of the list, paused).
- raw/: for each run, result.json, eval.json, metrics.json, judge.json,
  harness.json, events.jsonl, the agents' messages and patches, as
  bundle.py copies them, held to the repository's plain-text rule (two
  patches of sdlc's typst run, agent5.patch and agent9.patch, are not
  plain text and are left out); the transcripts and the set-aside
  attempts stay outside the repository.
