# The collaboration experiment in one Claude Code cloud session

This kit runs the four arms of CooperBench's own later ablation in a single
Claude Code cloud session: solo, coop with messages only, coop with a
shared git remote, and a team with a lead and a shared task list (the
authors had 48%, 26%, 56% and 62% on the 50-pair flash subset with codex
and gpt-5.5, one seed, unreviewed). The agents here are Claude Code on the
owner's subscription, so the run tests whether the ordering holds for
Claude; it does not check the authors' numbers.

## How it runs

The session's container runs Docker, and CooperBench's own runner and
scorer run on it unchanged (cooperbench/CooperBench at 63b9d44, with the
one patch below). The runner starts each agent as a headless Claude Code
in its own task container, in parallel; in the coop and team arms the
agents send messages through Redis, push to and merge from a local git
server, and share a task list and a scratchpad volume, exactly as in the
authors' runs. CooperBench's evaluation then merges the two patches and
runs each feature's hidden tests in a fresh task container. The session
itself only drives shell scripts, so it spends almost none of the usage.

## What was checked here, with no Claude usage (2026-10-09)

- dockerd starts in the cloud container. Docker Hub answers 429 to this
  host, so every image comes from mirror.gcr.io, which serves the task
  images CooperBench publishes (akhatua/cooperbench-*).
- The environment's network policy blocks deb.debian.org,
  deb.nodesource.com, the Alpine mirrors and huggingface.co, so the
  adapter cannot install Claude Code in a task container its own way.
  overlay_task_image.sh adds the session's own claude binary, the redis
  client and the egress proxy's CA to each task image under its own name,
  and cooperbench-claude-code-setup.patch makes the adapter's installer
  skip the install when claude is already there. On the Alpine images
  (go_chi), where the session's glibc binary cannot run, the overlay
  copies Node from the official Alpine image and installs the same Claude
  Code version from npm. Neither touches a prompt, a tool or the scoring.
- Every task of the flash subset was prepared here and its gold patches
  checked (each feature's tests must fail on the base commit and pass with
  its gold patch). 42 of the 50 pairs grade here as they stand:

  | Task | Pairs | Grade here | Why not |
  | --- | --- | --- | --- |
  | pallets_click 2068, 2800, 2956 | 6, 4, 1 | all | |
  | pallets_jinja 1465, 1559, 1621 | 2, 4, 6 | all | |
  | pillow 25, 68, 290 | 2, 1, 3 | all | |
  | typst 6554 | 4 | all | |
  | dottxt_ai_outlines 1655 | 2 | all | |
  | dspy 8394 | 2 | all | |
  | huggingface_datasets 3997, 6252 | 1, 1 | all | |
  | samuelcolvin_dirty_equals 43 | 2 | all | |
  | go_chi 26 | 1 | all | |
  | go_chi 56 | 1 | flaky | feature 1 passed by hand and in one of three checks; the other two failed with no output, the sandbox's fault rather than the code's |
  | openai_tiktoken 0 | 3 | none | its tests download encodings from openaipublic.blob.core.windows.net |
  | dottxt_ai_outlines 1706 | 2 | none | its tests download models from huggingface.co |
  | llama_index 18813 | 2 | none | its test run installs from pypi.nvidia.com |

  The three hosts are outside this environment's network policy. Allowing
  them is the owner's call (the environment's settings, Network access);
  the task containers also resolve only the hosts the environment reaches
  directly, so a test that downloads at run time would then still need its
  files baked into the overlay image rather than fetched. Until then the
  run covers the 42 pairs, and the report says which pairs each arm ran.
- A team run with a shared remote on pallets_click/2068, given a
  placeholder token, brought every piece up (the agents' containers on the
  team network, the git server, the task list seeded in Redis, the team's
  MCP server connected inside the container) and stopped only at the
  API's 401 for the placeholder; a coop run with a shared remote on
  go_chi/26 did the same on the Alpine overlay.

## What it needs from the owner

A subscription token for the agents. Run `claude setup-token` on your own
machine, signed in to the Pro or Max account whose usage the run should
draw on, and add the token to the cloud environment's settings as the
environment variable COORDBENCH_CLAUDE_TOKEN (a line
COORDBENCH_CLAUDE_TOKEN=<token> under Environment variables, not under
Network secrets); never put it in the repository or in a chat. The cloud
environment withholds a variable named CLAUDE_CODE_OAUTH_TOKEN from the
session, since it runs the session's own Claude Code; run_arms.sh and
run.py copy the alias to that name for the agents. A session sees only the variables that existed
when it started, so start a new session on this repository afterwards.
Keep ANTHROPIC_API_KEY unset: the adapter prefers it, and it bills the API.

## Steps, in the new session

1. `bash bench/cooperbench/setup.sh`: Docker, CooperBench, Redis, the git server.
2. For each task of dataset/subsets/flash.json, one at a time:
   `bash bench/cooperbench/prepare_task.sh <repo> <task>`, then
   `bash bench/cooperbench/run_arms.sh <repo> <task>`, then remove the task's
   image (`docker rmi akhatua/cooperbench-...`) to free the disk. A
   session's background command stops after two hours, so the session
   drives one task per command.
3. `python3 bench/cooperbench/report.py` sets the four arms side by side,
   per arm and model with a pooled row, each figure beside the paper's or
   CooperBench's own. The first rate is the paper's: eval.json both_passed
   over every attempted pair, as CooperBench scores an errored agent's
   patch; beside it are CooperBench's three rates (summary.json, the
   console and ablation page, the website) and coordbench's rate on the
   pairs the agents finished, where a pair an agent left in Error is set
   apart rather than failed. Every rate has a Wilson interval and a
   bootstrap over task sets. Then the paired comparisons (exact McNemar),
   the coordination gap against Solo, the git ablation, the pass route (N
   and L as in CooperBench's BENCHMARK_RESULTS.md), the naive-merge
   conflict rate, the merge cascade's Naive, +Union and +LLM success when
   eval.json has it, turns, the overhead of messaging, concrete line and
   file references, the speech acts and failure modes once judge.json
   holds them, the difficulty curves of App C, the dataset split (the
   pairs unamended since the paper's dataset, the spec audit's classes,
   the gold-conflict share) and one row per pair. A measure whose input a
   run did not record says "not recorded". A pair whose token counts are
   null, from a coordbench driver that could not measure usage, is left
   out of "Tokens per pair", which reads '-' when no pair of the arm was
   measured. `--published <website>/static/data/coop/index.json` also
   compares each arm with CooperBench's published runs on the same
   pairs. The raw results are under `~/cooperbench/logs/<run name>/`, and
   `cooperbench eval -n <run name> --backend docker -c 1` scores a run
   again.

Start with a pilot on pallets_click/2068: six pairs, so 6 solo sessions
and 12 each for the other three arms, 42 sessions in all.

## Cost and time

The authors' own claude_code team run on the 10-pair core subset cost
about $8.50 at API prices, about $0.43 per agent session with Sonnet 4.5.
The flash subset over four arms is 50 solo sessions and 100 for each of
the other three arms, 350 sessions in all, about $150 at API prices
(294 sessions, about $125, for the 42 pairs that grade here). On
a subscription that is usage drawn from the plan's limits, not a bill,
and it will likely span several usage windows. A pair whose agents stop
on a usage limit is marked Error, and running the same command again
later runs it again; finished pairs are kept. The pilot is about 42
sessions, near $18 at API prices. With CONCURRENCY=1 a pair takes 5 to 20
minutes per arm.

The container has 4 CPUs, 15 GB of memory and about 26 GB of free disk.
The task images are 0.1 to 4.2 GB compressed and about twice that on disk,
which is why the steps take one task at a time.

## How this run differs from the authors'

- Agent and model: Claude Code with MODEL (claude-sonnet-5-5 unless set)
  instead of codex with gpt-5.5.
- Claude Code is the session's own binary (2.1.295) rather than the latest
  npm release; it carries Claude Code's own subagent tools.
- coop-git keeps messages on, CooperBench's default; the authors did not
  record their choice. The team arm runs without --git, as theirs did.
- No turn limit and a two-hour cap per agent, as in their runs; one seed,
  as theirs. A second seed on the pairs where the arms disagree is the
  cheapest way to see how much of a gap is noise.
- When the mirror lacks a task image, prepare_task.sh builds it from the
  task's Dockerfile with build_task_image.sh, on the full Python image
  because apt is blocked (tried on outlines/1371, which built). The mirror
  answers 429 now and then for images it has not cached, the pillow and
  dirty_equals ones among them; trying again later is the cure.

## Files

- setup.sh: brings up Docker, CooperBench at the pinned commit with the
  patch, Redis and the git server.
- prepare_task.sh: pulls or builds one task's image, adds the overlay, and
  checks that its features are gradeable.
- survey.sh: prepares every flash task in turn and checks its gold
  patches grade here; it is what made the table above.
- run_arms.sh: runs the four arms on one task.
- report.py: the arms side by side, from CooperBench's logs; tested in
  tests/test_experiment_report.py.
- difficulty.py: App C's relative difficulty, the ten-bucket curves, AUC
  and retention, which report.py prints and which also writes
  difficulty.json on its own; tested in tests/test_experiment_difficulty.py.
- dataset_stats.py: the dataset beside the paper's Tables 3 and 4, the
  gold-conflict share, what changed since the paper's dataset, the spec
  audit and the gradeability checks; tested in
  tests/test_experiment_dataset_stats.py.
- overlay_task_image.sh, build_task_image.sh: the image steps on their own.
- cooperbench-claude-code-setup.patch: the adapter's installer skips the
  install when claude is already in the image.
