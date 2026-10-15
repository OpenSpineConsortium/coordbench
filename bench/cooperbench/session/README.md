# The session route: a run driven by one Claude Code session

bench.yml runs the benchmark on GitHub's runners with the owner's Claude
tokens in the environment bench. Where no token can be used, a Claude
Code session can run the same benchmark itself: coordbench's session
driver (coordbench/README.md, "The session driver") prepares each pair
and writes one prompt file per bench agent, the session spawns one
subagent per prompt, and the driver scores the pair when the subagents
have left their markers. The files here are the scaffolding that keeps
that going over a list without a hand on it, the knobs that set how
much of the benchmark a run takes, and the readers of what came out.

- strata.py: the natural strata of the pairs and a nested stratified
  order over them. From the dataset alone each pair has its language
  (by repository: python, python+c, rust, go, python+rust), whether its
  two gold patches conflict when merged (gold_conflict_report.json, the
  paper's Table 4) and the size of the two gold patches (terciles over
  the list); with the website's coop/index.json it also has the
  published difficulty, how many of the paper's eight coop runs passed
  it (none, few, many) and whether the paper's Claude did. `table`
  prints the strata and the pairs; `sample N` prints the first N pairs
  of the order a seed fixes, one stratum at a time (equal allocation)
  or in the strata's proportions. The order is nested: the sample of
  size N is the first N of the sample of size N+1, so a run grows by
  taking more of the same order.
- queue.py: the work list, one JSON line per (pair, arm) item, under a
  file lock, so several lanes take items from one queue and no item is
  taken twice. `add` takes the pairs (a list, or `--sample N` with the
  strata keys) and the arms; adding again with a larger N queues only
  the new items, which is how a run scales up. Items are ordered task
  by task, pair by pair, arm by arm, so every arm advances together and
  a run cut short still holds the same pairs under every arm, which the
  paired tests need. `add --sample` writes the draw's arguments (keys,
  allocation, seed, list, skips, the published file's hash) on each
  item and refuses a later draw with other arguments, which would not
  nest (`--force` adds it anyway). `pause` and `resume` hold and
  release the pending items, all or the pairs named; `requeue` puts
  failed ones back, with what the run left on them cleared, so they
  are prepared afresh, and `requeue --harness-only` only those whose
  run measured the harness (below), since an agent that ran out of time
  is its arm's result. `add --order-seed N`, or `shuffle` on a queue
  already filled, puts each pair's arms in an order drawn from the seed
  and the pair, so no arm always meets a lane's first minutes or the
  end of a usage window. `add --option KEY=VALUE` gives every item a
  run option that prepare passes to `run.py prepare`.
- lane.py: one lane's two halves. `prepare` takes the next item, waits
  for its task image (prepared by a separate loop, since a pull takes
  minutes and a lane should not spend its agents' deadline on one) and
  for `--min-free-gb` (8) free on the docker root, the log directory and
  the temporary directory, runs `run.py prepare` with the session driver
  and prints the plan: the run directory and each agent's prompt file
  and run token. An arm whose arm.json names plugins or MCP servers is
  refused by name, since a subagent loads neither. `finish` is run
  once the subagents have returned: a subagent that returned without its
  marker is given one (harness.json records it, and the agents that
  returned saying they had not finished), one the harness lost has its
  deadline brought forward and is marked lost so the driver records an
  Error at once and says why, then `run.py finish` scores and tears
  down, judge.py's free heuristics run, audit.py reads the run, and the
  item takes the verdict. `finish --detach` runs
  that in a session of its own and `wait` polls for its verdict, since a
  scoring with the merge cascade can outlast one tool call. A lane that
  lost the plan it was handed (a workflow that stopped before its
  agents ran) runs `prepare` again and is handed the same plan, read
  back from the run directory; but one whose agents ran (a marker or a
  patch in a workspace, or a transcript spawned as an agent) is finished
  instead, its agents with no marker counted as lost, since agents
  spawned again on that workspace would be scored with the first ones
  as one run. An image still missing, or a disk still short, after six
  waits fails the item rather than holding the lane.
- audit.py: what the session route did to a finished run, in its
  audit.json: per agent the API calls and the context the first one
  sent, which every call sends again before any work (the session's own
  prompt, about 80,000 tokens, which a bench agent inherits), the
  effort its calls ran at (the session's, inherited too), the tool calls
  the session refused (a permission decision other than accept, or
  Claude Code's own guard, such as "Blocked: sleep"), the tool results
  that say the disk was full, and how many subagents were spawned as
  it. A run with an agent the harness lost, a full disk or an agent
  spawned twice measured the harness, not the arm: finish gives it the
  verdict error whatever it scored (scored_verdict keeps the score) and
  marks the item's harness_error, so `requeue --harness-only` runs it
  again. `python3 audit.py RUN_DIR ...` audits runs finished before it,
  while the session's transcripts are still on disk.
- lane.js: the lane as a Claude Code Workflow script: prepare, spawn the
  bench agents in parallel, finish, until the queue is empty. `args.root`
  is the checkout whose lane.py runs, and is required: a default checkout
  can hold older code than the arm being run. The
  prepare and finish steps are small agents that run the two commands
  above and relay their JSON (the lane trusts the plan's agents, not the
  relayed done flag, which one relay flipped); the bench agents are the
  model the plan names. A lane runs one pair at a time (a workflow runs
  two agents at once on a four-CPU host); the run's width is the number
  of lanes started on the queue, `Workflow({script, args: {queue, root,
  worker}})` once per lane. `args.resume` takes a plan whose agents were
  never spawned. `args.bench_model` is the model the bench agents run
  as (the workflow picks a subagent's model; the default, sonnet, is
  claude-sonnet-5-5) and must be the model the queue's items name,
  since the driver records the queue's.
- summary.py: the cost side beside report.py's statistics: per arm the
  pass rate, the wall time, the agents' seconds, the tokens by kind and
  an estimated cost at list prices, and one CSV row per pair. From each
  run's audit.json it leaves the harness errors out of every figure and
  lists them, and gives the API calls, the fixed prompt's share of the
  tokens, the tokens net of it (a paired ratio too), the pairs with a
  refusal and the efforts; `--bases` names the arms every other is
  paired against.
- bundle.py: the files of a tag worth keeping in the repository (every
  pair's result, evaluation, metrics, messages and patches; not the
  transcripts, trajectories, prompts, plans or wrappers, which hold
  the run tokens), for a results directory, held to the repository's
  plain-text rule: a JSON file is written again with its non-ASCII
  escaped, a text file gets its missing final newline, and a file that
  cannot be made plain text is left out and named.
- subset.py: one sample's runs copied out of a tag's logs into logs of
  their own, with each arm's config.json and a summary.json rebuilt from
  the copied pairs, so the three tools above read the stratified sample
  alone when the tag also holds pairs outside it, as a tag grown from a
  pilot does; the sample is the queue's items that carry a sample
  record. Only finished runs are copied, transcripts included, and the
  sample's lines of the queue go beside them, where bundle.py looks;
  runs not finished are named, so a sample still running reads as
  incomplete, and OUT must be new or empty.

What differs from a run on the runners is in coordbench/README.md's
session-driver section and in each pair's result.json (driver: session,
isolation: advisory): the agents are subagents of the orchestrating
session, so they carry that session's project instructions and run on
its account, their steps and durations are not streamed (their wall
time is measured by the runner, their tokens read from their
transcripts, the output count a lower bound), and they work on the host
with the container reached through docker exec, an isolation the prompt
asks for rather than enforces.

## How much to run

The agents run on the session's own Claude subscription, whose five-hour
window is the budget. The pilot of 2026-10-15 (bench/cooperbench/results/
curse-1-pilot/) ran five lanes, up to ten Sonnet subagents at once, and
reached the account's session limit after about seventy minutes and
twenty-one pair-runs; one pair-run of a two-agent arm read about 3 to 5
million tokens (nearly all cache reads) and took 3 to 7 minutes, a solo
one about 1 million and 2 minutes. So a run is sized in pairs: the
first N of the stratified order, every arm, N chosen for the window,
and grown by adding again with a larger N once the window has reset.

    LOGS=~/cooperbench/logs; Q=$LOGS/TAG-queue.jsonl
    python3 bench/cooperbench/session/strata.py --published coop-index.json \
        --by language,conflict,published table            # what there is
    python3 bench/cooperbench/session/strata.py --published coop-index.json --by language,conflict,published \
        --skip openai_tiktoken_task/0,dottxt_ai_outlines_task/1706,llama_index_task/18813 \
        sample 8                                          # the order the add below queues, same skip and seed
    python3 bench/cooperbench/session/queue.py add $Q --tag TAG \
        --arms solo,coop,sdlc,coop-git,team --sample 8 \
        --stratify language,conflict,published --published coop-index.json \
        --skip openai_tiktoken_task/0 dottxt_ai_outlines_task/1706 llama_index_task/18813 \
        --timeout-min 45 --model claude-sonnet-5-5          # 8 pairs, 40 runs
    # from the session: Workflow on lane.js, args {queue: Q, worker: lane-N, root: <this checkout>}, one per lane
    python3 bench/cooperbench/session/queue.py requeue $Q --status failed --harness-only   # run harness errors again
    python3 bench/cooperbench/session/queue.py add $Q --tag TAG --sample 16 ...   # grow: 8 more pairs
    python3 bench/cooperbench/report.py --logs $LOGS --run solo=TAG-solo --run coop=TAG-coop \
        --run sdlc=TAG-sdlc --run coop-git=TAG-coop-git --run team=TAG-team --cooperbench-dir ~/cooperbench
    python3 bench/cooperbench/session/summary.py --logs $LOGS --tag TAG --out summary.json --csv pairs.csv --md summary.md
    python3 bench/cooperbench/session/bundle.py --logs $LOGS --tag TAG --out bench/cooperbench/results/TAG
    python3 bench/cooperbench/session/subset.py $Q $LOGS $LOGS-sample    # then the three above with --logs $LOGS-sample;
        # it copies the sample's finished runs and its lines of the queue, and names what is not finished

The three tasks skipped above download at test time, which the cloud
session's network refuses; a runner can take them. coop-index.json is
cooperbench/website's public/static/data/coop/index.json; without it
the published stratum reads unknown and the other keys still work.
