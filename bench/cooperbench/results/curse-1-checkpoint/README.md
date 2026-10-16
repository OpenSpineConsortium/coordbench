# curse-1: the runs of 2026-10-16, kept through the pause

At 08:58 UTC on 2026-10-16 the owner paused the bench until the AIDaS
grant is submitted (due 03:59 UTC on 2026-10-17), so that the account's
five-hour window goes to the grant; the session resumes it at 04:05 UTC
on 2026-10-17. The logs live only on the container that made them, so
this directory keeps the ten runs made that day, bundled by
session/bundle.py as the other bundles are, for a new container to
restore with ../curse-of-coordination-notes/run/restore-bundles.py. The
full run's own bundle will take these runs in and replace this
directory.

Every bench agent ran as claude-sonnet-5-5 at effort xhigh, the effort
the pilot's and the step's agents ran at (lane.js's args.effort, #22;
each run's audit.json lists its agents' efforts). coordbench was at
c433d48, main with #21, for the first two outlines runs and at 2b93164,
#22's head, for the other eight; the two differ only in lane.js's
effort knob and documentation.

| pair | arm | passed | why it ran |
|---|---|---|---|
| dottxt_ai_outlines 1655 f6_f7 | solo | no | the step's run met a full disk |
| dottxt_ai_outlines 1655 f6_f7 | coop | yes | the step's run met a full disk |
| dottxt_ai_outlines 1655 f6_f7 | sdlc | no | the step's run met a full disk |
| dottxt_ai_outlines 1655 f6_f7 | coop-git | yes | the step's run met a full disk |
| dottxt_ai_outlines 1655 f6_f7 | team | yes | the step's run met a full disk |
| pallets_click 2068 f5_f11 | sdlc | yes | the step's agents were spawned twice |
| pallets_click 2068 f6_f7 | solo | yes | the pilot's run lost its agent; the pair is in the full run |
| pallets_click 2068 f6_f7 | coop | yes | the full run |
| pallets_click 2068 f6_f7 | sdlc | yes | the full run |
| pallets_click 2068 f6_f7 | team | yes | the full run |

Every one is clean by its audit. Two more runs started that day and the
account's session limit cut them at 09:10 UTC, coop-git on click 2068
f6_f7 and coop on jinja 1559 f4_f10; their audits call them harness
errors (the harness lost both agents), so they are not here and the
queue holds them again. The queue at the pause is
../curse-of-coordination-notes/run/curse-1-queue.jsonl.
