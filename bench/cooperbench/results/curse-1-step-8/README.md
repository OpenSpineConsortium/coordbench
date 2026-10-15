# The curse of coordination, and the claim protocol against it: the step of 8 pairs, 2026-10-15

The question (bench/cooperbench/PLAN.md, and the pilot in
results/curse-1-pilot/): CooperBench found that two agents coordinating
on a pair of features pass far less often than one agent given both,
the curse of coordination; does Symphony's claim protocol, coordbench's
sdlc arm (an issue per feature, a claim, a pull request each, a merge
gate), let two Claude Code agents finish the pair more often than
CooperBench's own coordination settings, and at what cost in time and
tokens? This directory is the first step after the pilot: the first 8
pairs of the nested stratified order under all five arms, 40 runs, the
step the owner chose on 2026-10-15 at 10:32 UTC from the sizes the
pilot's README offered (8, 16 or all 43 pairs).

## How the step ran

- The sample: `queue.py add --sample 8 --stratify
  language,conflict,published` draws the first 8 pairs of the nested
  stratified order over the 43 pairs of the flash list that the cloud
  session's network can grade (the three tasks that download at test
  time left out), equal allocation across the strata, seed 0; strata.md
  records the strata and the draw, which `strata.py --skip ... sample 8`
  reproduces. The 50 flash pairs fall in 14 strata; the 43 gradable
  ones fill 12 of them (tiktoken, left out, was both python+rust
  strata), and the step takes one pair from each of 8. The pairs, with
  their stratum (language, whether the two gold patches conflict, and
  how many of the website's eight published coop runs of the pair
  passed: the paper's five models and the blog's three Gemini runs):
  - pallets_jinja 1621 f2_f9: python, conflict, few (2 of 8 published runs passed)
  - pallets_jinja 1559 f4_f10: python, clean, few (3 of 8)
  - pallets_click 2068 f5_f11: python, conflict, none (0 of 8)
  - pillow 68 f1_f5: python+c, conflict, none (0 of 8)
  - huggingface_datasets 6252 f4_f6: python, clean, many (8 of 8)
  - go_chi 56 f1_f5: go, conflict, none (0 of 8)
  - dottxt_ai_outlines 1655 f6_f7: python, conflict, many (4 of 8)
  - typst 6554 f5_f9: rust, conflict, few (1 of 3)

  One pair per stratum weights the small strata up: the rates below
  describe these 8 pairs and estimate no rate for the list or the
  benchmark.
- Five arms on every pair, as in the pilot: solo (one agent, both
  features, the ceiling), coop (CooperBench's default, two agents with
  messages), sdlc (the arm under test), coop-git (two agents with a
  shared git remote), team (the authors' lead, task list and scratchpad
  harness). Every agent claude-sonnet-5-5 through Claude Code 2.1.300,
  on CooperBench 63b9d44 with its one patch (dataset tree 9c9e5802e0);
  WebFetch and WebSearch off; 45 minutes per agent; one seed. The sdlc
  arm's protocol is symphony-protocol main at c7f0d84, the pilot's,
  kept although F18 (0536cc3) and F19 (1a79df9) merged during the day,
  so the step's sdlc runs compare with the pilot's.
- The harness: coordbench at 43c2e3d, the pilot's pull request (#18)
  as merged, for 39 of the 40 runs, since it was held still while the
  step ran and this directory's own pull request came after. The 40th,
  solo on click 2068 f5_f11, is the pilot's own run (06:01 UTC,
  coordbench d53e312): the queue held it as done, so the step reused it,
  and it is in the pilot's directory too. The other four arms of that
  pair were pilot runs its restart had cut, run again at 10:38 UTC.
- The route is the session route (bench/cooperbench/session/README.md),
  as in the pilot: one cloud session with no Claude token in its
  environment, each bench agent a subagent of that session, four lanes
  from 10:38 UTC so four runs, often the four two-agent arms of one
  pair, ran at once on one four-CPU host, and one lane for the typst
  pair at the end, one run at a time. The pilot's README lists the four
  ways this differs from the runners' route, each the same for every
  arm.
- The queue was the pilot's (tag curse-1), which already held every
  pair of the list, paused: the 8 were released with `queue.py resume
  --pairs` and the sample record written by the same hand, since `add
  --sample` adds only pairs the queue lacks. The tag also holds the
  pilot's other click 2068 runs and the paused rest of the list, so the
  report here is rendered over a copy of the step's 40 runs alone
  (session/subset.py).

## What happened on the way

Every run counted below is a complete attempt whose agents returned on
their own. Six attempts were set aside and run again, five spoiled by
the host and one by the session; two runs were finished or judged again
by hand; and six agents' token counts were filled in after the run.

- At 10:48 UTC the disk filled while the datasets 6252 image (13.3 GB)
  was built beside the others. coop-git jinja 1559's agents stopped on
  no space: the attempt was set aside and the pair rerun. sdlc click
  2068 f5_f11's finish died after its agents had returned (896 and 824
  seconds): the finish was run again by hand at 12:15 UTC, so its
  result.json wall time, 5816 seconds, holds 82 minutes with the
  harness dead; its agents' own times are in its timings, and the
  medians below are little moved by it.
- solo jinja 1621 f2_f9's first attempt ended with an empty patch: the
  session's permission classifier blocked the agent's final `git diff >
  patch.txt`, a fail that is not the model's. The attempt was set aside
  and the pair rerun, and passed.
- At 12:36 UTC the disk filled again while the typst image was built
  beside the pillow one. team go_chi's prepare failed and the pair was
  rerun; solo pillow had three stranded attempts before the one that
  counts; coop go_chi's finish scored the pair (both_passed true) and
  died before the judge and the verdict, which were made by hand at
  12:44 UTC with the same commands, though its merge cascade was not
  run again, so report.md's cascade rows count 7 coop pairs; and a
  finish run again after one that died raised FileNotFoundError on a
  workspace the teardown had taken. This pull request's lane.py notes
  such a workspace (gone_workspaces in harness.json) and, where an
  earlier finish wrote result.json, stops before run.py could write
  empty patches and a fresh score over the only copy that finish left.
- The typst image needs the disk to itself: 10.1 GB, with Docker's
  build cache released first (a restart of the daemon freed the 17 GB
  that `docker builder prune` reported as in use after two builds had
  been killed). It was built alone, and its five runs went on one lane,
  one at a time, since a two-agent typst run's workspaces, each a copy
  of the repository with its cargo build, take about 11 GB; the disk's
  free space fell to 2.6 GB during the sdlc run and recovered at its
  teardown. The five ran from 15:15 to 16:49 UTC, after the account's
  usage window had reset at 14:10.
- Six agents' transcripts were not found when their runs finished
  (coop and coop-git on outlines 1655 and datasets 6252, sdlc on jinja
  1559, team on jinja 1621): each read its prompt file only after its
  first reply and never echoed the run token by which finish finds an
  agent's transcript, so result.json had no token counts for it and
  summary.py counted them as zero, halving those pairs. This pull
  request's driver also finds an agent by the prompt file its own
  prompt names, ranked after the token; each of the six had exactly one
  such transcript, and at 17:14 UTC each was copied and counted with
  the driver's own code into its result.json (the session outcome
  beside it, outside the repository, keeps the earlier record). summary.py
  now leaves a pair with an unmeasured agent out of the token and cost
  figures, and says so, where it used to count the agent as zero.
- The set-aside attempts are outside the repository (previous-*.tar.gz
  beside each run), as the transcripts are.

## The result

report.md is the CooperBench-parity report (bench/cooperbench/METRICS.md
maps each section to the paper's figure); summary.md and summary.json
the latency, token and cost side; pairs.csv one row per (pair, arm).

### The curse: solo against coop, and the arm under test against both

Pass is eval.json's both_passed under CooperBench 63b9d44: the merged
patch passes both features' hidden tests or, where the merge conflicts,
the lead's (agent1's) patch alone does. The lead-alone case decided 4
of the 24 two-agent passes (team on datasets 6252, click 2068 and
typst; coop-git on typst).

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
one. Under the paper era's rule, which scored its Claude rows (naive
merge, union, resolver, with no lead-alone fallback), team passes 5 of
8, solo's count, and the other arms are unchanged (report.md, Merge
cascade). The paired tests cannot tell any two arms apart at this size
(every exact McNemar p is 1). On the seven of these pairs the paper's
published Claude coop runs cover, they passed 2 (datasets 6252 and
jinja 1559); ours passed 5 under coop and under sdlc, p = 0.25
(report.md, Against CooperBench's published runs).

### What the protocol did

The claim protocol's counters over the eight sdlc pairs (report.md,
Coordination): 16 claims, 16 pull requests opened, 16 merge attempts,
16 merges, 0 merge conflicts at the gate, every pull request merged at
its first attempt, 106 seconds from the hub's start to the first merge
(median). In 6 of the 8 pairs the second agent merged the shared main
into its claim branch before marking it ready (git_log.txt), so any
overlap was settled in its workspace, where the gate's counter does not
see it; the gold patches conflict in 6 of the 8 pairs. The team
harness's board: 16 tasks, 16 done, 31 seconds to the first claim.

The naive merge of the two agents' final patches conflicted in 0 of 8
pairs under coop, 0 under sdlc, 1 under coop-git and 4 under team
(report.md, Naive-merge conflicts). sdlc's and coop-git's zeros and ones
are not comparable with coop's: both arms tell each agent to submit the
integrated main as its patch once its work has merged, so 7 of their 8
pairs submitted identical patches, which cannot conflict. For the same
reason each agent's patch alone passed both features' tests in many
pairs (sdlc: the first agent's in 6, the second's in 5; coop-git: 6 and
6; coop: 1 and 1 of 7): the counts measure that submission rule, not
duplicated work. The agents' own commits show each sdlc agent writing
only its own feature, except on go_chi 56, where both changed the 405
handling. team's four conflicts are between the lead's and the member's
submissions, three of them passed on the lead's patch alone.

The cost of talking (report.md, Communication): coop 6.1 messages per
pair and 17.6% of its tool calls spent sending, coop-git 5.6 and 12.5%,
team 4.1 and 10.0%, sdlc 3.8 and 8.8%. These count message sends only:
sdlc's agents also made about 13 protocol calls per pair (board, claim,
pull request, status, ready) and team's about 10 task-board calls, which
the percentages do not count as coordination.

### Latency, tokens and cost

From summary.md, over the eight pairs, every agent of every run
measured; tokens are the sum over the pair's agents of input, cache
read, cache write and output (the output a lower bound); cost at list
prices (Sonnet 5.5: 2 dollars per million input, 10 output, 0.20 cache
read, 2.50 cache write), an estimate on the same scale for every arm,
since nothing was billed:

| arm | wall seconds per pair (median) | agent seconds, summed (median) | tokens per pair (mean) | cost per pair (mean) | cost per passing pair | messages per pair |
|---|---|---|---|---|---|---|
| solo | 219 | 201 | 1.74 M | $0.72 | $1.15 | 0 |
| coop | 335 | 405 | 3.37 M | $1.30 | $1.73 | 6.1 |
| sdlc | 529 | 737 | 3.79 M | $1.30 | $1.73 | 3.8 |
| coop-git | 308 | 525 | 4.44 M | $1.52 | $2.02 | 5.6 |
| team | 296 | 443 | 3.98 M | $1.43 | $1.91 | 4.1 |

By the ratio of the arms' medians over the same eight pairs
(summary.md's paired table), every two-agent arm took 1.9 to 2.5 times
solo's tokens and 1.8 to 2.1 times its estimated cost, for 1.35 to 2.4
times its wall time; the ratios of the means say the same (tokens 1.9
to 2.6, cost 1.8 to 2.1). Against coop, sdlc took 1.58 times the wall
time (slower on 6 of the 8 pairs) and 1.82 times the summed agent
seconds, for 1.1 times the tokens (1.14 by medians, 1.12 by means; per
pair from 0.48 to 1.8, more on 5 of 8) and the same cost (1.07 by
medians, 1.00 by means). coop-git and team took about coop's wall time
(0.92 and 0.88 of it) for 1.30 and 1.11 times its tokens.

Where sdlc's extra time went: not into the protocol's calls, about 13
per pair, each back within about a second (one board call took 7.8
seconds). A third of sdlc's agent time, 2,306 of 7,375 agent-seconds,
was spent blocked in wait_for_message, which the coop and coop-git arms
do not offer (team's agents spent 426 of 4,262 there), much of it after
both pull requests had merged: in click 2068 the second merge came at
341 seconds and both agents still called the hub at 800. sdlc's agents
wait on each other under the arm's rule to submit the integrated main.

The wall times were taken with four runs sharing four CPUs, image
builds and two disk fills (sdlc go_chi ran through the second), while
the typst runs ran alone; sdlc's mean minutes per pair in report.md
(19.6) holds click 2068 f5_f11's 82 minutes with the harness dead,
which the medians above do not move with.

### What this step says

- The curse of coordination did not appear on these 8 pairs: the four
  two-agent arms passed 6 of 8 to solo's 5 of 8 (team 5 under the
  paper era's rule), and the one pair that separated them went the
  two-agent way. These runs differ from the paper's Claude rows in the
  model (Sonnet 5.5 against the paper's 2025 Claude), the agent
  harness (Claude Code subagents under coordbench's session driver
  against OpenHands) and the scoring rule all at once, and the paper's
  per-pair solo results are unpublished (report.md, Difficulty), so
  this step cannot say which of these, if any, accounts for the
  paper's gap. With no curse to remove, it cannot show the protocol
  removing one.
- The protocol arm matched every two-agent arm's pass rate, every pull
  request merged at its first attempt, and its agents sent the fewest
  messages. Its cost was latency: 1.6 times coop's wall time, mostly
  its agents waiting for each other, for about coop's tokens and cost.
  Whether the gate wins passes where two agents' patches would conflict
  cannot be read here: coop's naive merges never conflicted on these
  pairs, and sdlc's agents submitted the integrated main. A larger
  sample may hold pairs where coop conflicts.
- Two agents cost about twice one agent's tokens under every
  coordination setting, because each agent re-reads its whole context
  on every turn: about 95 percent of an agent's input is a prompt-cache
  read. In this route that context opens with about 78 thousand tokens
  of the orchestrating session's instructions (the pilot's README), so
  a pair's tokens track its agents' turns, and the absolute figures are
  this route's, not a standalone agent's. The two agents of a pair
  print only 6 to 25 percent of the same lines, by an analysis outside
  this repository over 34 two-agent runs that predates the last typst
  runs (/mnt/project-files/experiments/curse-of-coordination/xblossom/
  in the project).
- The same code and the same queue grow the run: `queue.py add --sample
  16 ...` adds the next 8 pairs of the order, `--sample 43` the rest.
  By this step's record the next 8 would take about two of this
  account's five-hour windows at four lanes: the 34 runs of its first
  seven pairs (the 35th is the pilot's) went from 10:38 to 12:59 UTC in
  one window and the typst pair's five in the next, each
  two-agent run reading 1.9 to 7.6 million tokens (the arms' means 3.4
  to 4.4 million) and taking 3 to 14 minutes of wall time (typst's 10
  to 14). PLAN.md's power note puts the detectable gap at about 25
  points over 50 pairs at the paper's rates, about a third of pairs
  discordant; at this step's one discordant pair in eight, even the 43
  gradable pairs would detect less. The pilot's README has the sizes
  and the runners' route for the owner's several accounts. A later step
  may move the sdlc arm to symphony-protocol's newer main (F18 0536cc3,
  F19 1a79df9 and F2 a3a5b4d merged after c7f0d84, all unreleased at
  0.4.1), at the cost of comparability with this step and the pilot.

## Files

- report.md: report.py's output over the step's runs, every section.
- summary.md, summary.json, pairs.csv: summary.py's latency, token and
  cost side, per arm and per pair.
- strata.md: strata.py's table of the 50 flash pairs with their strata
  and the published results, and the step's draw.
- difficulty.json: the difficulty curves' data.
- queue.jsonl: the step's 40 items as the queue left them, all done
  (subset.py writes the sample's lines; the tag's queue also holds the
  pilot's items and the rest of the list, paused).
- raw/: for each run, result.json, eval.json, metrics.json, judge.json,
  harness.json, events.jsonl, the agents' messages and patches, as
  bundle.py copies them, held to the repository's plain-text rule (two
  patches of sdlc's typst run, agent5.patch and agent9.patch, are not
  plain text and are left out); raw/*/config.json are the run
  configurations the tag's first runs wrote, the pilot's, unchanged.
  The transcripts, the session outcomes and the set-aside attempts stay
  outside the repository.
