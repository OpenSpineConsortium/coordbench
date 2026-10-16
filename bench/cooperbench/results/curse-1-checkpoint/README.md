# curse-1: the runs of 2026-10-16, kept through the pause

At 08:58 UTC on 2026-10-16 the owner paused the bench until the AIDaS
grant is submitted (due 03:59 UTC on 2026-10-17), so that the account's
five-hour window goes to the grant, and at 09:37 UTC wrote "Just
continue", so the bench ran again from about 09:41 UTC. The logs live
only on the container that made them, so this directory keeps the ten
runs made that day before the pause, for a new container to restore with
../curse-of-coordination-notes/run/restore-bundles.py. Each run keeps
the files session/bundle.py keeps of a run (no transcript, prompt or run
token), and each arm its config.json; unlike the other bundles it has no
queue.jsonl and no arm summary.json, since the queue at the pause is
../curse-of-coordination-notes/run/curse-1-queue.jsonl and the runner
rebuilds a summary from its runs. The full run's own bundle will take
these runs in and replace this directory.

Every bench agent ran as claude-sonnet-5-5 at effort xhigh, the effort
the pilot's and the step's agents ran at (lane.js's args.effort, #22;
each run's audit.json lists its agents' efforts). The first two outlines
runs, solo and coop, ran from c433d48, main with #21, with #22's effort
knob in lane.js not yet committed (their result.json provenance lists
lane.js as modified, and coop's session/README.md too); the other eight
ran from 2b93164, #22's head, unmodified. The two differ only in
lane.js's effort knob and documentation.

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

Every one is clean by its audit. One wall time is not the agents': solo
on click 2068 f6_f7's result.json gives 1006 seconds (duration_seconds),
of which its agent took 185 (agents_seconds), since its finish waited
out the account's session limit from 08:58 to 09:12 UTC; the other nine
runs' wall times exceed their agents' by 12 to 44 seconds. A wall-time
figure takes that run's agents' own time, as the step's README does for
click 2068 f5_f11.

Three more runs started that day and are not here, since their audits
call them harness errors (the harness lost both agents) and the queue
put them back: coop on the outlines pair at 00:28 UTC, at the session's
effort max, which ran again at xhigh at 04:17 UTC (the table's row); and
coop-git on click 2068 f6_f7 and coop on jinja 1559 f4_f10, which the
account's session limit cut at about 08:59 UTC. The queue at the pause
is ../curse-of-coordination-notes/run/curse-1-queue.jsonl.
