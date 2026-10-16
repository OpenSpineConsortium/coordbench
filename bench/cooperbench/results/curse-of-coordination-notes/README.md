# The curse of coordination: where the experiment stands, 2026-10-15

This folder is the handoff from the session that ran the experiment on
2026-10-15 to the session that runs the next step. The question is the
one in bench/cooperbench/PLAN.md: does Symphony's claim protocol (the
sdlc arm) let two Claude Code agents finish CooperBench's task pairs as
often as one agent does, and at what cost in time and tokens.

## 2026-10-16: the reruns, and a pause for the grant

- The effort. Every agent of the pilot, the step and the screen ran at
  effort xhigh, inherited from the orchestrating session; this day's
  session ran at max. lane.js now takes args.effort (#22), every
  curse-1 lane names "xhigh" (step 3 below), and the two runs made at
  max were made again: solo on the outlines pair, which had passed, and
  coop on it, whose agents the harness lost.
- The image loop. With more than one task open it prepared the last
  open task's image over and over and never the one the lanes waited
  for; #23 fixed it.
- The arms' order. At 08:42 UTC, before the reruns resumed, each
  pair's arms were put in a seeded order (step 5 below), so the order
  covers click 2068 f6_f7 too.
- The reruns. 7 of the 12 are done: the outlines pair under all five
  arms and sdlc on click 2068 f5_f11 for the step, and solo on click
  2068 f6_f7 for the pilot. They and the first runs of the full run,
  the other three arms of click 2068 f6_f7, are in
  ../curse-1-checkpoint/. Left: coop and sdlc on jinja 1559 f4_f10,
  solo on jinja 1621 f2_f9, sdlc and coop-git on go_chi 56 f1_f5, and
  coop-git on click 2068 f6_f7, which the session limit cut.
- The pause. At 08:58 UTC the owner paused the bench until the AIDaS
  grant is submitted (03:59 UTC on 2026-10-17), to leave the account's
  five-hour window to it. The runs in flight were finished and the
  rest of the queue paused: run/curse-1-queue.jsonl is the queue at
  the pause, 54 items done and 161 paused. At 09:37 UTC the owner
  wrote "Just continue", and the bench ran again from about 09:41 UTC,
  after a restart of the container: the rest of the reruns, then the
  step's figures with the harness errors left out, then the four
  batches back to back, as the owner chose at 00:56 UTC ("All
  batches"). By 10:05 UTC the five reruns left and coop-git on click
  2068 f6_f7 had run, every one clean by its audit, and batch-9-16
  had begun; their runs go into the full run's bundle.
- The step's figures. ../curse-1-step-8/ is regenerated over its 40
  clean runs, the 11 made again among them: solo passed 5 of 8, coop
  7, sdlc 7, coop-git 6 and team 7, and no exact McNemar p on passes is
  below 0.5. sdlc's outlines run counts as a pass once scored again:
  its first scoring's run of feature 1's suite hit the sandbox's
  600-second limit with no test counted, the time going to the runner's
  download of packages, not to the tests, and `run.py rescore --force`
  scored the same patches again from 11:43 to 11:55 UTC, starting no
  agent; both suites passed (step 6 below makes that the rule, where
  the time plainly went outside the tests). Its README says
  what changed and which figures cover only the runs whose transcripts
  the container at hand holds.
- Batch 9-16. Its 35 runs other than typst 6554 f4_f9 ran on three
  lanes from about 10:00 to 11:37 UTC, every one clean by its audit
  and none with a timed-out suite; 17 passed. The typst pair was held
  back, as typst runs need the disk to themselves, and runs alone on one
  lane after them, from about 12:00 UTC.

## What is here

- Copies of the working notes kept in the project's shared folder on
  2026-10-15, so the next session reads them from the repository:
  - trial-order.md: the recommended order of trials, written at 18:15
    UTC, with the rule that decided the X-Blossom screen.
  - harness-review.md: the adversarial review of the harness. It
    confirmed 30 of 32 findings, 10 of them blocking. Pull request #19
    answered one of the blocking findings and #21 answered the others.
  - xblossom-README.md and overlap.py: X-Blossom's parallelism carried
    over to agents, and the script that measured repeated file reads in
    the step's transcripts.
  - research-git-delm.md, git-delm-design.md, off-the-shelf.md and
    rag.md: the research behind the options in trial-order.md.
  - Some file references in these notes do not resolve here. Some point
    at files in the repositories they cite (DeLM, CooperBench, and
    coordbench as it stood that day). Others point at the shared
    folder's layout. XBloom.pdf, the paper the user attached, is not
    copied.
- run/: the state of the full run on the container that ran it.
  - curse-1-queue.jsonl: the queue at the pause of 2026-10-16,
    09:15 UTC: 54 items done and 161 paused, the rest of the reruns
    and of the full run. Its item for sdlc's outlines run still holds
    the first scoring's fail; ../curse-1-step-8/queue.jsonl holds the
    second scoring's pass.
  - order-43.txt: the 43 gradable pairs in the nested stratified order.
  - batch-9-16.txt, batch-17-24.txt, batch-25-32.txt and
    batch-33-43.txt: the pairs of each batch, comma separated, in the
    form that `queue.py resume --pairs` takes.
  - images-full.sh: the loop that prepares task images one at a time
    ahead of the lanes, keeping the disk free.
  - restore-bundles.py: puts the bundled runs back into a logs
    directory on a new container (below).
  - audit-backfill.jsonl: the audit's verdict on each of the 68 runs
    that had been made, 56 of the tag curse-1 and 12 of the screen.
- The bundled results beside this folder each carry the audit.json of
  every run:
  - ../curse-1-pilot/
  - ../curse-1-step-8/
  - ../curse-1-checkpoint/, the runs of 2026-10-16 up to the pause
  - ../xblossom-screen/

## Where things stand

- The pilot (../curse-1-pilot/) and the step of 8 pairs
  (../curse-1-step-8/, #19). The step ran 40 runs and first reported
  solo 5 of 8 and every two-agent arm 6 of 8, nothing significant.
  Over its 40 clean runs (below), sdlc's outlines run scored again, it
  reports:
  - solo passed 5 of 8;
  - coop, sdlc and team passed 7 of 8, coop-git 6 of 8;
  - nothing was significant.
- The audit that #21 adds found 11 of the step's 40 runs to be harness
  errors, not the arms' results:
  - A full disk:
    - outlines 1655 f6_f7 under all five arms;
    - coop-git and sdlc on go_chi 56 f1_f5;
    - coop and sdlc on jinja 1559 f4_f10;
    - solo on jinja 1621 f2_f9.
  - A respawned pair of agents: sdlc on click 2068 f5_f11.
  - The step's first figures counted these 11 as results. All 11 were
    made again on 2026-10-16, each clean by its audit, and the step's
    figures are regenerated over the 40 clean runs.
- One run outside the step is also a harness error: solo on click 2068
  f6_f7, where the harness lost agent1.
- The X-Blossom screen (../xblossom-screen/; the arm merged as #20) ran
  12 runs.
  - xblossom passed 4 of 4, as team did.
  - It used 26% more tokens and 21% more dollars than team.
  - The audit found no harness error in it.
  - The user chose "Leave it out" at 19:36 UTC.
- The harness hardening is #21. It covers:
  - the free-disk check;
  - a lane that comes back without re-running a finished pair;
  - respawns and lost agents;
  - the audit that leaves harness errors out of every reader;
  - the transcript matcher's fix.
  - At 22:35 UTC `queue.py requeue --harness-only` put the 12 harness
    errors back as pending.

## The user's decisions that stand

- 18:36 UTC, "All six arms": the full run is the remaining pairs under
  the five arms solo, coop, sdlc, coop-git and team.
- 19:36 UTC, "Leave it out": xblossom is not in the full run.
- 18:01 UTC: no experiment data goes into the grant until the full run
  is done and xblossom has been tried.
- The size of each step is the user's choice. No bench run goes beyond
  what the user chose, and the run never uses all of the account's
  usage. The full run is chosen. It runs batch by batch and stops at a
  usage limit.
- The bench agents are subagents of the session, on its own
  subscription. No API key and no stored credential.

## What a new container has

The logs, transcripts included, live only on the container that made
them (/root/cooperbench/logs). A new container has only the bundles in
this repository and run/ above.

A bundle holds no transcripts. Never run audit.py on a bundle: it finds
no transcript and calls every run a harness error. The bundled
audit.json files are the audit's verdicts.

On a new container:

1. Run bench/cooperbench/setup.sh.
2. Make the logs directory (`mkdir -p ~/cooperbench/logs`) and copy
   run/curse-1-queue.jsonl into it.
3. Before a report that spans the old and the new runs, put the bundled
   runs back into the logs directory, from this folder:
   `python3 run/restore-bundles.py .. ~/cooperbench/logs --bundles curse-1-pilot,curse-1-step-8,curse-1-checkpoint`.
   Those three bundles are its default. It copies each bundled run whose
   audit is clean and never replaces a run the logs already hold, so
   name every bundle in the one call. Where two bundles hold a run at
   one path, only the later start counts: the pilot's click 2068 f5_f11
   under coop, sdlc, coop-git and team was cut by a restart and run
   again in the step, and both bundles keep a copy. A bundle holds no
   transcripts, so the figures read from them (tool calls, the
   communication overhead) cover only the runs made on the container at
   hand.

## The next steps, in order

By 12:00 UTC on 2026-10-16 steps 1 to 5 are done; of step 6, the full
run, batch 9-16 is done but its typst pair, which is running, and
batches 17-24, 25-32 and 33-43 are to come; and of step 7 the step of
8's figures are done.

Q below is ~/cooperbench/logs/curse-1-queue.jsonl, and CHECKOUT is a
coordbench checkout at main with #21 merged.

1. Check the disk with `df -h /`.
   - Keep 8 GB free beyond the image being pulled. images-full.sh waits
     for that by itself.
   - An outlines 1655 evaluation takes 7 to 10 GB per test container.
2. Start the image loop in the background:
   `bash run/images-full.sh CHECKOUT Q`.
3. Rerun the outlines pair alone on one lane, since it filled the disk
   under all five arms:
   - `python3 bench/cooperbench/session/queue.py pause Q`
   - `python3 bench/cooperbench/session/queue.py resume Q --pairs dottxt_ai_outlines_task/1655/f6_f7`
   - Start one lane, from the session: Workflow on
     bench/cooperbench/session/lane.js with args
     `{queue: Q, worker: "lane-1", root: CHECKOUT, bench_model: "sonnet", effort: "xhigh", log_dir: "/root/cooperbench/logs"}`.
     Every curse-1 lane names `effort: "xhigh"`, since the pilot's, the
     step's and the screen's agents all ran at xhigh (each agent's
     efforts in audit.json); left out, the bench agents think at the
     orchestrating session's effort, whatever it is.
4. Resume the other 7 reruns and run them on up to 3 lanes:
   - `queue.py resume Q --pairs pallets_jinja_task/1621/f2_f9,pallets_jinja_task/1559/f4_f10,pallets_click_task/2068/f5_f11,go_chi_task/56/f1_f5,pallets_click_task/2068/f6_f7`.
   - That resume also releases the 4 paused arms of click 2068 f6_f7,
     which are part of the full run in any case.
5. Draw each pair's order of arms, so no arm always meets a lane's first
   minutes: `queue.py shuffle Q --tag curse-1 --seed 1`.
6. Resume the full run batch by batch:
   - `queue.py resume Q --pairs "$(cat run/batch-9-16.txt)"`, then the
     later batches as each one ends, on about 3 lanes.
   - Hold each batch's typst pairs back (`queue.py set Q ID
     status=paused`, one item at a time, never `pause` while lanes run)
     until the batch's other pairs have finished, then run them on one
     lane: a two-agent typst run's workspaces take about 11 GB beside
     its 10 GB image. Batch 17-24 holds two typst pairs and outlines
     1655 f7_f10, whose image takes about 13 GB and whose evaluation 7
     to 10 GB more: hold that pair back too, and run it alone on one
     lane before the typst pairs.
   - After each batch, `queue.py requeue Q --harness-only` puts back any
     new harness error.
   - After each batch, score again from its own patches, once, starting
     no agent, a run whose scoring cut a suite off at the sandbox's 600
     seconds with no test counted, where the time plainly went outside
     the tests: its task's runner limits its own tests below 600
     seconds (`timeout 300` in runner.sh; among the full run's tasks,
     outlines 1655, dspy 8394, go_chi 26 and 56 and datasets 6252), or
     the merge cascade's fresh run of the same patch finished that
     suite. The second scoring counts and a second timeout keeps the
     fail; elsewhere CooperBench's fail stands, since a patch that
     hangs a test is cut off the same way (../curse-1-step-8/README.md
     says why, and PARITY.md row 23 states the difference). In order:
     - Find them with
       `grep -l "Command timed out after" ~/cooperbench/logs/curse-1-*/*/*/*/*/eval.json`
       and read each match's feature1 and feature2 (a match can be
       patch text or a cascade test). Skip a run that already has
       eval.first-scoring.json: its second scoring stands.
     - Stop images-full.sh, let no lane run, and put the task's image
       back if the loop removed it:
       `COORDBENCH_SKIP_GRADEABLE=1 bash bench/cooperbench/prepare_task.sh REPO TASK`
       (outlines 1655's image took about 5 minutes and 13 GB).
     - `cp -p RUN_DIR/eval.json RUN_DIR/eval.first-scoring.json`, then
       `python3 coordbench/run.py rescore --run-dir RUN_DIR --force > RUN_DIR/rescore.txt 2>&1`
       (a .txt, since audit.py reads every *.log in a run directory but
       scoring.log as an agent's log).
     - Record it on the queue's item:
       `python3 bench/cooperbench/session/queue.py set Q ID verdict=VERDICT 'rescored={"at": "...", "first_verdict": "fail", "why": "..."}'`,
       VERDICT being the second scoring's pass or fail.
     - Rebuild the arm's summary.json with
       `python3 bench/cooperbench/session/audit.py RUN_DIR`, which audits
       the run again from its transcripts and then rebuilds it.
7. Report with the commands in bench/cooperbench/session/README.md,
   "How much to run":
   - subset.py, then report.py and summary.py on the sample;
   - bundle.py into results/;
   - the step of 8's figures again, with the harness errors left out
     (done on 2026-10-16: ../curse-1-step-8/).
8. After the full run, the user decides what comes next. The candidates
   are steps 4 and 5 of trial-order.md, which they have not chosen.
