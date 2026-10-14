# coordbench

coordbench measures how the way coding agents are wired together changes
what they get done. It keeps the task, the agents and the scoring fixed
and makes the coordination a plug-in (an arm: alone, by messages, through
a shared git remote, as a team with a lead, through pull requests and a
merge gate, or anything new), so two ways of coordinating can be compared
on the same work. Today the work is CooperBench's task pairs, scored by
CooperBench's own evaluation, and the agents are Claude Code.

## Where to read

- coordbench/README.md is the contract: what an arm, a driver and the hub
  are, how to write an arm, and how to run one.
- bench/cooperbench/README.md is the CooperBench kit: the shell scripts
  that bring CooperBench up in a Claude Code cloud session, prepare a task
  image and run CooperBench's own four arms, and report.py, which sets
  the arms side by side.
- coordbench/README.md's "After a run" names what reads a run's files
  afterwards: the merge cascade in eval.json, the export for
  CooperBench's viewer and the comparison page (coordbench/viewer/), and
  the failure-mode judge, which calls a model only when the owner runs it.
- PARITY.md sets each of CooperBench's measures beside coordbench's,
  row by row: whether it is taken the same way, an equivalent one,
  differently for a stated reason, or not yet, with the decisions behind
  them, the questions only the authors can answer and the command that
  produces each measure (and whether it calls a model).
- docs/research.md is the research the design rests on, and
  docs/mcp-timeout.md why every arm's .mcp.json sets a timeout.

## Tests

```
python3 -m unittest discover -s tests
bash scripts/check.sh
```

The second is what CI runs as the check named "check". The tests need
Python 3.12 or later with nothing outside its standard library, and git;
none of them starts Docker or an agent. The few that compare an arm with
CooperBench's own source run when a CooperBench checkout is at
COOPERBENCH_DIR or ~/cooperbench (bench/cooperbench/setup.sh makes one)
and are skipped otherwise, as on CI.

## Where this repository fits

coordbench is one of the repositories around the claim protocol of
OpenSpineConsortium/symphony-protocol. That repository's ECOSYSTEM.md,
https://github.com/OpenSpineConsortium/symphony-protocol/blob/main/ECOSYSTEM.md,
draws all of them, says which modules each one takes and how a change to
the protocol reaches each. Here the sdlc arm decides through a copy of
the library at coordbench/vendor. The copy is never edited here:
symphony-protocol's sync workflow keeps it current by the pull request
symphony-protocol/sync, and coordbench/vendor/README.md says the rest.

## Where it came from

coordbench was split from OpenSpineConsortium/sdlc-template, where it was
the experiment/ directory, with its history: the commits before the split
are that directory's. Its sdlc arm benchmarks that template's claim
protocol, deciding through the protocol's merge gate from
OpenSpineConsortium/symphony-protocol, vendored at coordbench/vendor, rather
than through a copy of its own.
