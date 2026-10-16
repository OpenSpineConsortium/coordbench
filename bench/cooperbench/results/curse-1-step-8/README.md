# The curse of coordination, and the claim protocol against it: the step of 8 pairs, 2026-10-15

Regenerated on 2026-10-16. The audit that #21 adds found 11 of the
step's first 40 runs to be harness errors, ten of them a full disk and
one a respawned pair of agents. Each was made again on 2026-10-16 and is
clean by its audit, and one of them, sdlc's on the outlines pair, was
scored again from its own patches, since its first scoring's run of one
test suite timed out with no test counted (What happened on the way,
below). Every figure below is over the 40 clean runs, the 29 first runs
the audit passed and the 11 made again, unless it names another set.
The first version of this directory, whose figures counted the 11 as the
arms' results, is in the repository's history (#19, with #21's audits).
../curse-of-coordination-notes/README.md says how the runs were made
again.

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
  harness). Every agent claude-sonnet-5-5 at effort xhigh (each run's
  audit.json lists its agents' efforts), through Claude Code 2.1.300,
  and 2.1.301 for the runs made again (each run's result.json records
  it), on CooperBench 63b9d44 with its one patch (dataset
  tree 9c9e5802e0); WebFetch and WebSearch off; 45 minutes per agent;
  one seed. The sdlc arm's protocol is symphony-protocol main at
  c7f0d84, the pilot's, for every run, kept although F18 (0536cc3) and
  F19 (1a79df9) merged during the day, so the step's sdlc runs compare
  with the pilot's.
- The harness: coordbench at 43c2e3d, the pilot's pull request (#18)
  as merged, for 28 of the 29 first runs, since it was held still while
  the step ran and this directory's own pull request came after. The
  29th, solo on click 2068 f5_f11, is the pilot's own run (06:01 UTC,
  coordbench d53e312): the queue held it as done, so the step reused it,
  and it is in the pilot's directory too. The other four arms of that
  pair were pilot runs its restart had cut, run again at 10:38 UTC;
  sdlc's, whose agents were spawned twice, is one of the 11 made again.
  Those 11 ran from the commits each result.json's provenance names:
  c433d48, main with #21, for solo and coop on the outlines pair, with
  #22's effort knob in lane.js not yet committed (and, for coop,
  session/README.md too); 2b93164, #22's head, which merged as a491803
  with the same tree, for four; and 219645d (#23) for five, coop-git on
  go_chi 56 with #25's relay prompts not yet committed. Between 43c2e3d
  and these the
  harness changed how a run is finished, audited and given disk room;
  the bench agents' prompt is the same.
- The route is the session route (bench/cooperbench/session/README.md),
  as in the pilot: one cloud session with no Claude token in its
  environment, each bench agent a subagent of that session. The first
  runs went on four lanes from 10:38 UTC, so four runs, often the four
  two-agent arms of one pair, ran at once on one four-CPU host, and on
  one lane for the typst pair at the end, one run at a time. The 11
  made again were orchestrated by another session on 2026-10-16: the
  outlines pair's five one at a time on one lane, and the other six on
  up to three lanes. The pilot's README lists the four ways this route
  differs from the runners', each the same for every arm.
- The queue was the pilot's (tag curse-1), which already held every
  pair of the list, paused: the 8 were released with `queue.py resume
  --pairs` and the sample record written by the same hand, since `add
  --sample` adds only pairs the queue lacks. The 11 harness errors went
  back into the same queue with `queue.py requeue --harness-only`. The
  tag also holds the pilot's other click 2068 runs and the full run, so
  the report here is rendered over a copy of the step's 40 runs alone
  (session/subset.py), which takes the clean run of each item.

## What happened on the way

Every run counted below is a complete attempt whose agents returned on
their own and whose audit is clean. On 2026-10-15 six attempts were set
aside and run again, five spoiled by the host and one by the session;
two runs were finished or judged again by hand; and six agents' token
counts were filled in after the run. The audit then found 11 of the runs
that day kept to be harness errors too, and they were made again on
2026-10-16 (the last item below).

- At 10:48 UTC the disk filled while the datasets 6252 image (13.3 GB)
  was built beside the others. coop-git jinja 1559's agents stopped on
  no space: the attempt was set aside and the pair rerun. sdlc click
  2068 f5_f11's finish died after its agents had returned (896 and 824
  seconds): the finish was run again by hand at 12:15 UTC, so its
  result.json wall time, 5816 seconds, held 82 minutes with the harness
  dead. The audit later found its agents spawned twice, and it is one
  of the 11 made again.
- solo jinja 1621 f2_f9's first attempt ended with an empty patch: the
  session's permission classifier blocked the agent's final `git diff >
  patch.txt`, a fail that is not the model's. The attempt was set aside
  and the pair rerun, and passed; the audit later found that rerun cut
  by a full disk, and its run of 2026-10-16 passed too.
- At 12:36 UTC the disk filled again while the typst image was built
  beside the pillow one. team go_chi's prepare failed and the pair was
  rerun; solo pillow had three stranded attempts before the one that
  counts; coop go_chi's finish scored the pair (both_passed true) and
  died before the judge and the verdict, which were made by hand at
  12:44 UTC with the same commands, though its merge cascade was not
  run again, so report.md's cascade rows count 7 coop pairs; and a
  finish run again after one that died raised FileNotFoundError on a
  workspace the teardown had taken. The step's pull request (#19) made
  lane.py note such a workspace (gone_workspaces in harness.json) and,
  where an earlier finish wrote result.json, stop before run.py could
  write empty patches and a fresh score over the only copy that finish
  left.
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
  1559, team on jinja 1621): each opened its prompt file with a tool
  call, five as their very first act, and none wrote the run token in a
  text reply, the only place finish looked for it (sdlc's jinja 1559
  agent echoed it only through a shell command), so result.json had no
  token counts for it and summary.py counted them as zero, halving
  those pairs. The step's pull request made the driver also find an
  agent by the prompt file its own prompt names, ranked after the token
  and refused to a transcript that carries any other run's token, since
  every attempt of a pair shares that path; each of the six had exactly
  one such transcript, and at 17:14 UTC each was copied and counted with
  the driver's own code into its result.json (the session outcome
  beside it, outside the repository, keeps the earlier record).
  summary.py leaves a pair with an unmeasured agent out of the token and
  cost figures, and says so, where it used to count the agent as zero.
  Three of the six runs (coop and coop-git on outlines 1655, sdlc on
  jinja 1559) are among the 11 made again.
- The set-aside attempts are outside the repository (previous-*.tar.gz
  beside each run), as the transcripts are.
- On 2026-10-16 the 11 harness errors were made again, each clean by
  its audit: the outlines pair under all five arms, one run at a time,
  since it had filled the disk under all five; coop and sdlc on jinja
  1559 f4_f10, solo on jinja 1621 f2_f9, and sdlc and coop-git on go_chi
  56 f1_f5, all five cut by a full disk; and sdlc on click 2068 f5_f11,
  whose agents were spawned twice. Three attempts were set aside on the
  way. Two were on the outlines pair at effort max, the orchestrating
  session's, before lane.js could name the bench agents' effort (#22):
  solo's, begun at 23:55 UTC on 2026-10-15, which passed, and coop's,
  whose agents the harness lost. Both were made again at xhigh, the
  effort of every other run, and solo's run at xhigh failed, so solo's
  figures below count that pair as a fail. The third, coop's on jinja
  1559 f4_f10 at 08:57 UTC, at xhigh, lost both agents when the
  account's session limit cut it, and was made again from 09:40.
  ../curse-1-checkpoint/README.md lists that day's runs up to the
  08:58 UTC pause.
- sdlc's run on the outlines pair, one of the 11, was scored a second
  time. Its first scoring, at 04:52 UTC, failed it without a test
  result: test_merged's run of feature 1's suite hit the sandbox's
  600-second limit and counted no test (exit code -1, 0 passed, 0
  failed), while feature 2's 84 tests passed. The merge cascade's
  fresh runs of each agent's patch alone passed both suites, 100 and 84
  tests, and the two agents had submitted identical patches, so the
  patch the verdict is read from had passed in a fresh sandbox twice;
  the other four arms' runs of the same suite took 4 to 7 seconds by
  pytest's count. A suite cut off by the clock measured the sandbox, not
  the patch, as a scoring on a full disk does, so at 11:43 UTC the run
  was scored again from its own patches with `run.py rescore --force`,
  which starts no agent: both suites passed, feature 1's 100 tests in
  5.7 seconds, and the run counts as a pass. A second
  timeout would have kept the fail. CooperBench counts a timed-out suite
  as failed; PARITY.md (row 23) states the difference. The first
  scoring is kept beside the run as eval.first-scoring.json, the
  rescore's output as rescore.txt, and the queue's item records it. No
  other suite has timed out in any curse-1 run made so far.

## The result

report.md is the CooperBench-parity report (bench/cooperbench/METRICS.md
maps each section to the paper's figure); summary.md and summary.json
the latency, token and cost side; pairs.csv one row per (pair, arm).

### The curse: solo against coop, and the arm under test against both

Pass is eval.json's both_passed under CooperBench 63b9d44: the merged
patch passes both features' hidden tests or, where the merge conflicts,
the lead's (agent1's) patch alone does. The lead-alone case decided 5
of the 27 two-agent passes (team on outlines 1655, datasets 6252, click
2068 and typst; coop-git on typst).

| arm | pairs | passed | rate | 95% Wilson |
|---|---|---|---|---|
| solo | 8 | 5 | 62.5% | 31-86% |
| coop | 8 | 7 | 87.5% | 53-98% |
| sdlc | 8 | 7 | 87.5% | 53-98% |
| coop-git | 8 | 6 | 75.0% | 41-93% |
| team | 8 | 7 | 87.5% | 53-98% |

Every pair under every arm (report.md, Every pair): pillow 68 fails
under all five arms; datasets 6252, click 2068 f5_f11, jinja 1559,
jinja 1621 and typst 6554 pass under all five; two pairs separate them.
outlines 1655 failed under solo and passed under the four two-agent
arms (team's on the lead's patch alone); go_chi 56 failed under solo
and coop-git and passed under coop, sdlc and team. No pair that solo
passed failed under a two-agent arm, so the coordination gap of the
paper (solo minus the arm) is -25 points for coop, sdlc and team and
-12.5 for coop-git, where the paper's Claude had +21: on these pairs two
agents did not pass less often than one. Under the paper era's rule,
which scored its Claude rows (naive merge, union, resolver, with no
lead-alone fallback), team passes 6 of 8 and the other arms are
unchanged (report.md, Merge cascade). The paired tests cannot tell any
two arms apart at this size (no exact McNemar p on passes is below
0.5). On the seven of these pairs the paper's published Claude coop
runs cover, they passed 2 (datasets 6252 and jinja 1559); ours passed 6
under coop and 6 under sdlc, p = 0.125 each (report.md, Against
CooperBench's published runs).

### What the protocol did

The claim protocol's counters over the eight sdlc pairs (report.md,
Coordination): 16 claims, 16 pull requests opened, 16 merge attempts,
16 merges, 0 merge conflicts at the gate, every pull request merged at
its first attempt, 102 seconds from the hub's start to the first merge
(median). In 7 of the 8 pairs one agent merged the shared main into its
claim branch before its pull request merged (git_log.txt), so any
overlap was settled in its workspace, where the gate's counter does not
see it; the gold patches conflict in 6 of the 8 pairs. The team
harness's board: 16 tasks, 16 done, 32 seconds to the first claim.

The naive merge of the two agents' final patches conflicted in 0 of 8
pairs under coop, 0 under sdlc, 1 under coop-git and 5 under team
(report.md, Naive-merge conflicts; pairs.csv's merge_conflict column,
which summary.py reads from the merge status that CooperBench's
eval.json records). sdlc's and coop-git's zeros and ones are not
comparable with coop's: sdlc tells each agent to submit the integrated
main once its pull request has merged, and coop-git tells each agent to
merge its peers' branches into its own before it writes its patch, so
all 8 sdlc pairs and 7 of the 8 coop-git pairs submitted identical
patches, which cannot conflict. For the same reason each agent's patch
alone passed both features' tests in many pairs (sdlc: the first
agent's in 7, the second's in 7; coop-git: 6 and 6; coop: 1 and 1 of
7): the counts measure that submission rule, not duplicated work. The
agents' own commits show each sdlc agent writing only its own feature;
on go_chi 56 both edited tree.go, and the second agent's last commit
made its CORS handling use the 405 Allow header the first had added.
team's five conflicts are between the lead's and the member's
submissions, four of them passed on the lead's patch alone.

The cost of talking (report.md, Communication): coop's agents sent 5.8
messages per pair, coop-git's 6.2, sdlc's 3.9 and team's 3.9, over all
eight pairs. The share of an arm's tool calls spent sending needs the
agents' transcripts, which stayed on the container that made the first
runs, so this version gives it only over the runs made again (report.md,
Pairs with a transcript): coop 14.8% over 2 pairs, coop-git 16.9% over
2, sdlc 7.3% over 4 and team 6.7% over 1, too few to compare. Over the
32 two-agent first attempts, 9 of the 11 harness errors among them, the
first version gave coop 17.6%, coop-git 12.5%, team 10.0% and sdlc
8.8%. These count message sends only: sdlc's agents also made about
13.5 protocol calls per pair (board, claim, pull request, status,
ready) and team's about 11
task-board calls, which the percentages do not count as coordination.

### Latency, tokens and cost

From summary.md, over the eight pairs, every agent of every run
measured; tokens are the sum over the pair's agents of input, cache
read, cache write and output (the output a lower bound); cost at list
prices (Sonnet 5.5: 2 dollars per million input, 10 output, 0.20 cache
read, 2.50 cache write), an estimate on the same scale for every arm,
since nothing was billed:

| arm | wall seconds per pair (median) | agent seconds, summed (median) | tokens per pair (mean) | cost per pair (mean) | cost per passing pair | messages per pair |
|---|---|---|---|---|---|---|
| solo | 219 | 194 | 1.59 M | $0.67 | $1.07 | 0 |
| coop | 266 | 356 | 3.11 M | $1.26 | $1.44 | 5.8 |
| sdlc | 407 | 662 | 4.23 M | $1.49 | $1.70 | 3.9 |
| coop-git | 308 | 525 | 4.25 M | $1.49 | $1.98 | 6.2 |
| team | 263 | 442 | 3.85 M | $1.40 | $1.60 | 3.9 |

By the ratio of the arms' medians over the same eight pairs
(summary.md's paired table), every two-agent arm took 1.7 to 2.6 times
solo's tokens and 1.8 to 2.2 times its estimated cost, for 1.2 to 1.9
times its wall time; the ratios of the means say the same (tokens 2.0
to 2.7, cost 1.9 to 2.2). Against coop, sdlc took 1.53 times the wall
time (slower on 7 of the 8 pairs) and 1.86 times the summed agent
seconds, for 1.4 times the tokens (1.37 by medians, 1.36 by means; per
pair from 0.99 to 1.8, more on 7 of 8; 1.57 net of the route's fixed
prompt, below) and 1.2 times the cost (1.17 by medians, 1.18 by means).
coop-git and team took 1.16 and 0.99 of coop's wall time for 1.50 and
1.24 times its tokens.

Where sdlc's extra time went: not into the protocol's calls, 108 in
all, about 13.5 per pair, 101 of them back within a second (the
slowest, a board call, took 7.8 seconds). Nearly a third of sdlc's
agent time, 1,951 of 6,374 agent-seconds, was spent blocked in
wait_for_message, which the coop and coop-git arms do not offer (team's
agents spent 426 of 4,094 there), much of it after both pull requests
had merged: on go_chi 56, by the hub's clock, the second merge came at
627 seconds, and one agent then waited in wait_for_message from 672 to
972 seconds and ended at 1,019, its peer having ended at 644. sdlc's
agents wait on each other under the arm's rule to submit the integrated
main.

The first runs' wall times were taken with four runs sharing four
CPUs, image builds and two disk fills, while the typst runs ran alone;
the runs made again ran on one to three lanes on another day. The 11
made again also started from a shorter fixed prompt (below), 6,000
to 11,000 tokens less on every call, and sdlc holds four of them,
solo, coop and coop-git two each and team one, which lowers sdlc's
token count a little against the others'; the net tokens in summary.md
take the fixed prompt out.

### What this step says

- The curse of coordination did not appear on these 8 pairs: the four
  two-agent arms passed 6 or 7 of 8 to solo's 5 of 8 (team 6 under the
  paper era's rule), and no pair that solo passed failed under any of
  them. These runs differ from the paper's Claude rows in the model
  (Sonnet 5.5 against the paper's 2025 Claude), the agent harness
  (Claude Code subagents under coordbench's session driver against
  OpenHands) and the scoring rule all at once, and the paper's per-pair
  solo results are unpublished (report.md, Difficulty), so this step
  cannot say which of these, if any, accounts for the paper's gap. With
  no curse to remove, it cannot show the protocol removing one.
- The protocol arm passed 7 of 8, as coop and team did, failing only
  pillow 68, which every arm failed; its outlines run counts as a pass
  once scored again (above). Every pull
  request merged at its first attempt, and its agents sent the fewest
  messages, with team's. Its cost was latency, 1.5 times coop's wall
  time, mostly its agents waiting for each other, and 1.4 times coop's
  tokens for 1.2 times its cost. Whether the gate wins passes where two
  agents' patches would conflict cannot be read here: coop's naive
  merges never conflicted on these pairs, and sdlc's agents submitted
  the integrated main. A larger sample may hold pairs where coop
  conflicts.
- Two agents cost 1.7 to 2.7 times one agent's tokens under every
  coordination setting, because each agent re-reads its whole context
  on every turn: 95 to 96 percent of every arm's input is a prompt-cache
  read. In this route that context opens with the orchestrating
  session's own instructions (the pilot's README), so a pair's tokens
  track its agents' turns, and the absolute figures are this route's,
  not a standalone agent's: the 52 agents of the first runs read 78,492
  to 81,683 tokens before any work, and the 20 of the runs made again,
  orchestrated by another session, 71,028 to 72,203; that much on every
  call, the first included, is 74 to 79 percent of each arm's tokens
  and, at cache-read prices, 36 to 44 percent of its dollars. The two
  agents of a pair print only 6 to 25 percent of the same lines, by
  overlap.py over 34 two-agent runs of the step's tasks before the
  audit, the pilot's among them
  (../curse-of-coordination-notes/xblossom-README.md); it reads the
  transcripts, so it is not run again here.
- The same queue grows the run, on the harness of the runs made again
  (219645d and later): the owner chose the
  full run on 2026-10-15, the other 35 gradable pairs under the five
  arms, and it goes on in four batches of the same nested stratified
  order (../curse-of-coordination-notes/run/batch-*.txt), each released
  with `queue.py resume --pairs`. PLAN.md's power note puts the
  detectable gap at about 25 points over 50 pairs at the paper's rates,
  about a third of pairs discordant; at this step's one or two
  discordant pairs in eight, even the 43 gradable pairs would detect
  less. A later step may move the sdlc arm to symphony-protocol's newer
  main (F18 0536cc3, F19 1a79df9 and F2 a3a5b4d merged after c7f0d84,
  all unreleased at 0.4.1), at the cost of comparability with this step
  and the pilot.

## Files

- report.md: report.py's output over the step's 40 clean runs, every
  section.
- summary.md, summary.json, pairs.csv: summary.py's latency, token and
  cost side, per arm and per pair; their tool-call figures need the
  transcripts, so they cover only the runs made again.
- strata.md: strata.py's table of the 50 flash pairs with their strata
  and the published results, and the step's draw.
- difficulty.json: the difficulty curves' data.
- queue.jsonl: the step's 40 items as the queue held them on 2026-10-16,
  all done, the 11 made again among them, sdlc's outlines item with its
  second scoring recorded (subset.py writes the sample's lines; the
  tag's queue also holds the pilot's items and the full run).
- raw/: for each counted run, result.json, eval.json, metrics.json,
  judge.json, harness.json, events.jsonl, audit.json, the agents'
  messages and patches, for the runs made again scoring.log, and for
  sdlc's outlines run eval.first-scoring.json and rescore.txt, as
  bundle.py copies them, held to the repository's plain-text rule (two
  patches of sdlc's typst run, agent5.patch and agent9.patch, are not
  plain text and were left out); raw/*/config.json are the run
  configurations the tag's first runs wrote, the pilot's, unchanged.
  The 11 harness errors' files are in the first version (above). The
  transcripts, the session outcomes and the set-aside attempts stay
  outside the repository.
