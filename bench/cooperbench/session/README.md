# The session route: a run driven by one Claude Code session

bench.yml runs the benchmark on GitHub's runners with the owner's Claude
tokens in the environment bench. Where no token can be used, a Claude
Code session can run the same benchmark itself: coordbench's session
driver (coordbench/README.md, "The session driver") prepares each pair
and writes one prompt file per bench agent, the session spawns one
subagent per prompt, and the driver scores the pair when the subagents
have left their markers. The three files here are the scaffolding that
keeps that going over a whole list without a hand on it, and summary.py
reads what came out.

- queue.py: the work list, one JSON line per (pair, arm) item, under a
  file lock, so several lanes take items from one queue and no item is
  taken twice. Items are ordered task by task, pair by pair, arm by arm,
  so every arm advances together and a run cut short still holds the
  same pairs under every arm, which the paired tests need.
- lane.py: one lane's two halves. `prepare` takes the next item, waits
  for its task image (prepared by a separate loop, since a pull takes
  minutes and a lane should not spend its agents' deadline on one), runs
  `run.py prepare` with the session driver and prints the plan: the run
  directory and each agent's prompt file and run token. `finish` is run
  once the subagents have returned: a subagent that returned without its
  marker is given one (harness.json records it), one the harness lost
  has its deadline brought forward so the driver records an Error at
  once, then `run.py finish` scores and tears down, judge.py's free
  heuristics run, and the item takes the verdict. `finish --detach` runs
  that in a session of its own and `wait` polls for its verdict, since a
  scoring with the merge cascade can outlast one tool call.
- lane.js: the lane as a Claude Code Workflow script: prepare, spawn the
  bench agents in parallel, finish, until the queue is empty. The
  prepare and finish steps are small agents that run the two commands
  above and relay their JSON; the bench agents are the model the plan
  names. A lane runs one pair at a time (a workflow runs two agents at
  once on a four-CPU host); the run's width is the number of lanes
  started on the queue, `Workflow({script, args: {queue, worker}})` once
  per lane. `args.resume` takes a plan whose agents were never spawned.
- summary.py: the cost side beside report.py's statistics: per arm the
  pass rate, the wall time, the agents' seconds, the tokens by kind and
  an estimated cost at list prices, and one CSV row per pair.

What differs from a run on the runners is in coordbench/README.md's
session-driver section and in each pair's result.json (driver: session,
isolation: advisory): the agents are subagents of the orchestrating
session, so they carry that session's project instructions and run on
its account, their steps and durations are not streamed (their wall
time is measured by the runner, their tokens read from their
transcripts, the output count a lower bound), and they work on the host
with the container reached through docker exec, an isolation the prompt
asks for rather than enforces.

A run of this route:

    python3 bench/cooperbench/session/queue.py add LOGS/TAG-queue.jsonl --tag TAG \
        --arms solo,coop,sdlc,coop-git,team --pairs @bench/cooperbench/pairs.txt \
        --timeout-min 45 --model claude-sonnet-5-5
    # from the session: Workflow on lane.js, args {queue, worker: lane-N}, once per lane
    python3 bench/cooperbench/report.py --logs LOGS --run solo=TAG-solo --run coop=TAG-coop \
        --run sdlc=TAG-sdlc --run coop-git=TAG-coop-git --run team=TAG-team --cooperbench-dir ~/cooperbench
    python3 bench/cooperbench/session/summary.py --logs LOGS --tag TAG --out summary.json --csv pairs.csv --md summary.md
