# The curse of coordination: where the experiment stands, 2026-10-15

This folder is the handoff from the session that ran the experiment on
2026-10-15 to the session that runs the next step. The question is the
one in bench/cooperbench/PLAN.md: does Symphony's claim protocol (the
sdlc arm) let two Claude Code agents finish CooperBench's task pairs as
often as one agent does, and at what cost in time and tokens.

## 2026-10-16: the reruns, paused for the grant

- The effort. Every agent of the pilot, the step and the screen ran at
  effort xhigh, inherited from the orchestrating session; this day's
  session ran at max. lane.js now takes args.effort (#22), every
  curse-1 lane names "xhigh" (step 3 below), and the one solo run made
  at max was made again.
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
  the pause, 54 items done and 161 paused. The session resumes at
  04:05 UTC on 2026-10-17 with the rest of the reruns, then the step's
  figures with the harness errors left out, then the four batches back
  to back, as the owner chose at 00:56 UTC ("All batches").

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
    and of the full run.
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
  - ../xblossom-screen/

## Where things stand

- The pilot (../curse-1-pilot/) and the step of 8 pairs
  (../curse-1-step-8/, #19). The step ran 40 runs and reported:
  - solo passed 5 of 8;
  - every two-agent arm passed 6 of 8;
  - nothing was significant.
- The audit that #21 adds found 11 of the step's 40 runs to be harness
  errors, not the arms' results:
  - A full disk:
    - outlines 1655 f6_f7 under all five arms;
    - coop-git and sdlc on go_chi 56 f1_f5;
    - coop and sdlc on jinja 1559 f4_f10;
    - solo on jinja 1621 f2_f9.
  - A respawned pair of agents: sdlc on click 2068 f5_f11.
  - The step's figures count these 11 as results. They are to be
    regenerated once the reruns are done.
  - The clean runs alone give these counts, which are too uneven to
    compare:
    - solo 4 of 6;
    - coop 5 of 6;
    - sdlc 3 of 4;
    - coop-git 5 of 6;
    - team 6 of 7.
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
2. Copy run/curse-1-queue.jsonl to ~/cooperbench/logs/.
3. Before a report that spans the old and the new runs, put the
   bundled runs back into the logs directory:
   `python3 run/restore-bundles.py .. ~/cooperbench/logs --bundles curse-1-pilot,curse-1-step-8,curse-1-checkpoint`.
   It copies each bundled run whose audit is clean and leaves alone a
   run the logs already hold. Where two bundles hold a run at one path,
   only the later start counts: the pilot's click 2068 f5_f11 under
   coop, sdlc, coop-git and team was cut by a restart and run again in
   the step, and both bundles keep a copy. A bundle holds no
   transcripts, so the figures read from them (tool calls, the
   communication overhead) cover only the runs made on the container
   at hand.

## The next steps, in order

At the pause of 2026-10-16, steps 1 to 3 and 5 are done and step 4 is
part done; the section at the top lists what is left of it.

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
   - After each batch, `queue.py requeue Q --harness-only` puts back any
     new harness error.
7. Report with the commands in bench/cooperbench/session/README.md,
   "How much to run":
   - subset.py, then report.py and summary.py on the sample;
   - bundle.py into results/;
   - the step of 8's figures again, with the harness errors left out.
8. After the full run, the user decides what comes next. The candidates
   are steps 4 and 5 of trial-order.md, which they have not chosen.
