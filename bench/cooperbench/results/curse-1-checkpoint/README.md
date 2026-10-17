# curse-1: the full run's finished runs, kept through the checkpoint of 2026-10-16

The full run finished on 2026-10-17 and is recorded in ../curse-1-full/;
this directory keeps the interim figures read from transcripts.

At 18:54 UTC on 2026-10-16 the owner asked every thread to checkpoint,
and at 18:58 UTC to put everything in git so the work can go on under
another account. At 18:57 UTC the queue was paused (`queue.py pause`:
the 36 items still pending became paused), the three runs then in flight
finished by 19:02 UTC, every one clean, and no lane ran until the
next session resumed the run on 2026-10-17. No run was cut, so that
session reran nothing: it resumed the 36 paused items (../curse-of-coordination-notes/README.md, "The next steps, in
order").

The logs live only on the container that made them, so this directory
kept, until 2026-10-17, every finished run of the full run, for a new
container to restore with ../curse-of-coordination-notes/run/restore-bundles.py together with
../curse-1-pilot/ and ../curse-1-step-8/, which hold the pilot's runs and
the step's.

- raw/ (moved on 2026-10-17 into ../curse-1-full/raw/, which holds
  every file it held, unchanged, beside the 36 runs made since): the
  139 finished runs of the full run's 175 items, the 35 pairs
  of the order after the step's 8 under the five arms, each run with the
  files session/bundle.py keeps of a run (no transcript, prompt or run
  token), and each arm its config.json, the pilot's, as in
  ../curse-1-step-8/raw. ../curse-of-coordination-notes/run/checkpoint-bundle.sh
  made it, and the arms' summary.json were then deleted, since
  restore-bundles.py never copies one. 124 of the runs were made on
  2026-10-16; the other 15 are the pilot's runs of 2026-10-15 on click
  2068 f1_f6, f2_f10 and f5_f7, which the full run counts and
  ../curse-1-pilot/raw holds too, the same runs with the same starts.
  Six files that are not plain text were left out, as bundle.py leaves
  them out: agent9.patch of coop-git on typst 6554 f4_f9, and
  agent7.patch, agent8.patch, merge_union.diff, shared/FEATURE8.txt and
  shared/agent2.patch of team on typst 6554 f7_f8.
- interim/: report.py's and summary.py's output over all 179 finished
  runs of the tag, the step's 40 and these 139, made at the checkpoint
  by ../curse-of-coordination-notes/run/record-full.sh on the container
  that held every transcript: report.md, summary.md, summary.json,
  pairs.csv (its paths name that container's logs) and difficulty.json.
  They are interim, since 36 runs of the full run are left, and the
  owner's rule of 2026-10-15 (18:01 UTC) keeps every figure of the
  experiment out of the grant until the full run is done and xblossom
  has been tried. They are kept because a restored run has no
  transcript, so the figures read from transcripts (tool calls and
  messages) can be had for these runs only from here.
- No queue.jsonl and no arm summary.json: the queue at the checkpoint
  was ../curse-of-coordination-notes/run/curse-1-queue.jsonl, 179 items
  done, 36 paused and none running (the file now holds the end of the
  run, 215 done, and the repository's history keeps the checkpoint's),
  and the runner rebuilds a summary from its runs.

Every run here is clean by its audit, and none has a suite that timed
out, so none was scored a second time (the tag's one second scoring is
the step's sdlc run on outlines 1655 f6_f7, in ../curse-1-step-8/).

Passes by batch, of the runs done (each batch is 8 pairs, 33-43 is 11):

| batch | runs done | solo | coop | sdlc | coop-git | team |
|---|---|---|---|---|---|---|
| 9-16 | 40 of 40 | 6 of 8 | 4 of 8 | 4 of 8 | 4 of 8 | 4 of 8 |
| 17-24 | 40 of 40 | 8 of 8 | 8 of 8 | 7 of 8 | 8 of 8 | 8 of 8 |
| 25-32 | 40 of 40 | 5 of 8 | 4 of 8 | 5 of 8 | 5 of 8 | 5 of 8 |
| 33-43 | 19 of 55 | 3 of 4 | 2 of 4 | 3 of 4 | 3 of 4 | 2 of 3 |

Over the 35 pairs that every arm has finished, the step's 8 among them,
solo and team passed 26 and coop, sdlc and coop-git 25 each. What the
batches showed on the way:

- The pairs that need the disk to themselves ran alone on one lane once
  their batch's other pairs were done: typst 6554 f4_f9 in 9-16, and
  outlines 1655 f7_f10 and then typst 6554 f1_f9 and f7_f8 in 17-24.
- In 9-16 solo alone passed two pairs, pillow 25 f1_f5 and dspy 8394
  f3_f5, which all four two-agent arms failed.
- sdlc's one miss in 17-24, on typst 6554 f1_f9: the naive merge of the
  two patches conflicted, and agent1, whose patch CooperBench's rule
  then scores alone, submitted its own feature only, after waiting about
  90 seconds for its peer; agent2's patch passes both features alone.
  The protocol's agents failed to coordinate there; the harness did not
  fail.
- coop's miss on jinja 1465 f2_f3 in 25-32 is a naive-merge conflict
  (three sections), where the other four arms passed.

The pause of 08:58 UTC that this directory first kept is in the
repository's history: its ten runs were the step's six made again
(outlines 1655 f6_f7 under the five arms and sdlc on click 2068 f5_f11),
which ../curse-1-step-8/raw holds, and the four first runs of click 2068
f6_f7, which are among the 139 here.
