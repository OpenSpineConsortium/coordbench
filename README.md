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
  skip the install when claude is already there. Neither touches a
  prompt, a tool or the scoring.
- The scorer passes the gold patches here: every feature's tests fail on
  the base commit and pass with its gold patch, 12 of 12 on
  pallets_click/2068 and 10 of 10 on pallets_jinja/1621
  (prepare_task.sh runs this check for every task it prepares).
- A team run with a shared remote on pallets_click/2068, given a
  placeholder token, brought every piece up (the agents' containers on the
  team network, the git server, the task list seeded in Redis, the team's
  MCP server connected inside the container) and stopped only at the
  API's 401 for the placeholder.

## What it needs from the owner

A subscription token for the agents. Run `claude setup-token` on your own
machine, signed in to the Pro or Max account whose usage the run should
draw on, and add the token to the cloud environment's settings as the
environment variable CLAUDE_CODE_OAUTH_TOKEN; never put it in the
repository or in a chat. A session sees only the variables that existed
when it started, so start a new session on this repository afterwards.
Keep ANTHROPIC_API_KEY unset: the adapter prefers it, and it bills the API.

## Steps, in the new session

1. `bash experiment/setup.sh`: Docker, CooperBench, Redis, the git server.
2. For each task of dataset/subsets/flash.json, one at a time:
   `bash experiment/prepare_task.sh <repo> <task>`, then
   `bash experiment/run_arms.sh <repo> <task>`, then remove the task's
   image (`docker rmi akhatua/cooperbench-...`) to free the disk. A
   session's background command stops after two hours, so the session
   drives one task per command.
3. Each arm's results are under `~/cooperbench/logs/<run name>/`;
   summary.json has the pass rate, and `cooperbench eval -n <run name>
   --backend docker -c 1` scores a run again.

Start with a pilot on pallets_click/2068: six pairs, so 6 solo sessions
and 12 each for the other three arms, 42 sessions in all.

## Cost and time

The authors' own claude_code team run on the 10-pair core subset cost
about $8.50 at API prices, about $0.43 per agent session with Sonnet 4.5.
The flash subset over four arms is 50 solo sessions and 100 for each of
the other three arms, 350 sessions in all, about $150 at API prices. On
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
- pallets_jinja/1465 is missing from the mirror, so build_task_image.sh
  builds it from its Dockerfile, on the full Python image because apt is
  blocked. The mirror may also answer 429 for the pillow and
  dirty_equals images; try again later.

## Files

- setup.sh: brings up Docker, CooperBench at the pinned commit with the
  patch, Redis and the git server.
- prepare_task.sh: pulls or builds one task's image, adds the overlay, and
  checks that its features are gradeable.
- run_arms.sh: runs the four arms on one task.
- overlay_task_image.sh, build_task_image.sh: the image steps on their own.
- cooperbench-claude-code-setup.patch: the adapter's installer skips the
  install when claude is already in the image.
