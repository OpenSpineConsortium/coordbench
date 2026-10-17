# curse-1: the full run, all 215 runs, 2026-10-15 to 2026-10-17

The curse-of-coordination experiment's full run: the 43 gradable pairs of
the nested stratified order (../curse-of-coordination-notes/run/order-43.txt)
under the five arms solo, coop, sdlc, coop-git and team, every agent on
claude-sonnet-5-5 at effort xhigh, run from the session on its own
subscription with no API key. The step of 8 pairs (../curse-1-step-8/)
is its first 40 runs; of the other 175, the pilot's 15 on click 2068
ran on 2026-10-15, 124 ran in batches 9-16, 17-24, 25-32 and 33-43 on
2026-10-16, and the last 36 of batch 33-43 on 2026-10-17, from 13:47 to
15:10 UTC, after the checkpoint of 2026-10-16 moved the
work to a new Project (../curse-1-checkpoint/README.md).

Every run is clean by its audit (no harness error left in), no suite hit
the sandbox's 600-second limit in the runs of 2026-10-17, and the tag's
one second scoring stays the step's sdlc run on outlines 1655 f6_f7.

## What it found

Passes over the 43 pairs (report.md, "Pass rates" and "Paired
comparisons"):

| arm | passed | rate | 95% Wilson | wall s per pair (median) | cost per pair (mean, list price) | naive-merge conflict rate |
|---|---|---|---|---|---|---|
| solo | 33 | 76.7% | 62-87% | 184 | $0.61 | - |
| coop | 30 | 69.8% | 55-81% | 246 | $1.18 | 7.0% |
| sdlc | 32 | 74.4% | 60-85% | 391 | $1.33 | 2.3% |
| coop-git | 32 | 74.4% | 60-85% | 267 | $1.32 | 11.6% |
| team | 33 | 76.7% | 62-87% | 262 | $1.32 | 60.5% |

- The curse is there in coop, and small: 7.0 points below solo (5 pairs
  only solo passed, 2 only coop, exact McNemar p 0.45), against the
  paper's 21.2 points for Claude. sdlc, coop-git and team close most or
  all of it (2.3, 2.3 and 0 points), and none of the differences is
  significant at 43 pairs.
- sdlc against coop: 3 pairs only sdlc passed, 1 only coop (p 0.63), at
  1.6 times coop's wall time, 1.5 times its agent seconds, 1.2 times its
  tokens and 1.2 times its cost (summary.md, the paired table). Its
  naive merge conflicted on 1 pair of 43, the fewest of the two-agent
  arms.
- Every two-agent arm costs about twice solo per pair for the same or
  fewer passes. On these pairs, with this model, one agent remains the
  cheapest way to a pass.
- The costs are list-price estimates from the token counts (summary.md
  names the prices); output tokens are a lower bound where the session
  driver's transcript lacked the final count.

Passes by batch (8 pairs a batch, 33-43 is 11):

| batch | solo | coop | sdlc | coop-git | team |
|---|---|---|---|---|---|
| 1-8 (the step) | 5 | 7 | 7 | 6 | 7 |
| 9-16 | 6 | 4 | 4 | 4 | 4 |
| 17-24 | 8 | 8 | 7 | 8 | 8 |
| 25-32 | 5 | 4 | 5 | 5 | 5 |
| 33-43 | 9 | 7 | 9 | 9 | 9 |

These figures are the owner's to read first, and stay out of the grant
by the owner's rule of 2026-10-15 (18:01 UTC) until xblossom has been
tried.

## What is here

- report.md, summary.md, summary.json, pairs.csv and difficulty.json:
  report.py's and summary.py's output over all 215 runs, made by
  ../curse-of-coordination-notes/run/record-full.sh on 2026-10-17.
  pairs.csv names the runs under /root/cooperbench/logs-all, the
  container's copy of the sample.
- The figures that divide by tool calls read from transcripts (the
  overhead columns of report.md's Communication section and the shares
  in Speech acts) cover only the runs made on the container that wrote
  this record, the 36 of 2026-10-17, which those tables count as pairs
  with a transcript, since a restored run keeps none;
  ../curse-1-checkpoint/interim/ has them for the 179 runs finished by
  the checkpoint. The pass rates, wall times, tokens and costs come from
  each run's own files and cover all 215.
- raw/: the full run's 175 runs, each with the files session/bundle.py
  keeps of a run (no transcript, prompt or run token) and its
  audit.json, each arm with the pilot's config.json; the step's 40 are
  in ../curse-1-step-8/raw/. ../curse-of-coordination-notes/run/checkpoint-bundle.sh
  made it, and the arms' summary.json were then deleted, since
  restore-bundles.py never copies one. It holds every file the
  checkpoint's raw/ held, unchanged, and replaces it. The pilot's 15 runs
  on click 2068 f1_f6, f2_f10 and f5_f7, which the full run counts, are
  here and in ../curse-1-pilot/raw/, the same runs with the same starts.
- The queue at the end of the run, 215 items done, is
  ../curse-of-coordination-notes/run/curse-1-queue.jsonl.

restore-bundles.py's default bundles are now curse-1-pilot,
curse-1-step-8 and curse-1-full, which together hold all 215 runs.
