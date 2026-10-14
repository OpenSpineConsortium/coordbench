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

## On another host

The scripts run the same way on any Linux host with Docker, a GitHub-hosted
runner among them, and read these settings, each with a default for the
cloud session:

- COOPERBENCH_DIR: CooperBench's checkout (default ~/cooperbench).
- CLAUDE_CODE_VERSION: the Claude Code version setup.sh installs with
  Claude Code's native installer when the host has no claude (default the
  version pinned in setup.sh); the overlay copies that one binary into
  every task image, so every agent runs the same build, and labels the
  image with the version (cooperbench.claude), so prepare_task.sh overlays
  it again when the version changes.
- COORDBENCH_CLAUDE_BIN: a stand-in for claude, a script that answers in
  Claude Code's stream-json, copied into every image in claude's place
  (the Alpine ones too, so it is POSIX sh) and never installed over, for a
  run that spends no usage.
- COORDBENCH_PROXY_CA: the egress proxy's CA (default
  /root/.ccr/ca-bundle.crt, the cloud session's); where the file is absent
  the overlay adds no trust lines and build_task_image.sh builds a task's
  Dockerfile as the dataset wrote it, with apt and Docker Hub reachable.
- COORDBENCH_SKIP_GRADEABLE: set, prepare_task.sh skips CooperBench's
  gradeability check, minutes a task, where the task was checked already
  (a survey ran) and a pair's eval would show a broken task anyway.
- COORDBENCH_REGISTRY: where the redis, git-server and node images come
  from (default mirror.gcr.io/library); COORDBENCH_MIRROR: the mirror a
  task image is pulled from first (default mirror.gcr.io), with Docker
  Hub itself tried next and the task's Dockerfile built last.

After each pair, however it ended, run.py replaces the exact value of the
agents' credential (CLAUDE_CODE_OAUTH_TOKEN, or COORDBENCH_CLAUDE_TOKEN)
wherever a file of the pair, or of the hub it started, holds it, the gzip
trajectory included, with "[credential scrubbed by coordbench]": an agent
that prints its environment writes the token into its stream, its
transcript and what it told its peer, and a pair's files may be published
as a workflow's artifact. The run says on stderr when it found one, and a
pair with a file that holds the token and could not be rewritten is an
error, not a result. The scrub rewrites the token's exact bytes in plain
and gzip files only: an archive an agent packed (.tar.gz, .zip), a git
object under the team arm's shared/ copy or a workspace kept with
--keep, or the token encoded some other way, is outside it, as is the
log of a hub started elsewhere (--hub), which is that hub's to keep.
`run.py scrub --run-dir DIR` runs the scrub once more over a run
directory and reads every file back, archives' members and git objects
(loose ones and packfiles, inflated) included, and exits 2 when any
still holds the token or could not be read; the workflow's jobs run it
before an upload, after stopping any agent container still up, and
publish nothing it refused. The team arm's shared/ is copied into every
pair and published with it.

## In GitHub Actions

.github/workflows/bench.yml runs the same kit on GitHub's runners, one job
per pair and arm, with the report job setting the arms side by side from
the pairs' artifacts. It has three modes, chosen when it is dispatched:

- survey: CooperBench's gradeability check on each task of the pair list,
  with no agent, whose table (survey_report.py) says which pairs grade on
  a runner and whether the largest images fit; PLAN.md's pair list is
  fixed from it.
- fake: every pair with fake_claude as the agent, so the whole path (the
  images, the containers, the hub, the git server, the scorer and the
  artifacts) runs and no usage is spent. A pull request that changes the
  kit (bench/cooperbench/), the workflow or the package coordbench/ runs
  one fake pair per arm of a smoke set as its check.
- real: Claude Code on the owner's accounts. One job, approve, waits in
  the environment bench until a reviewer approves the dispatch, once;
  every real job needs it and carries no environment of its own, since a
  job that waits in the environment behind its concurrency group asks
  for its own click when its turn comes. Each real job reads its
  account's token from the organization secret SYMPHONY_CLAUDE_<slot>,
  the slot a number from 1 to 8, and runs in the concurrency group of its
  slot, one job at a time, up to 100 waiting, first in first out as far
  as GitHub keeps it; a dispatch holds at most 256 jobs, GitHub's limit
  on one matrix. The 100 is the group's across every dispatch, so a
  second dispatch while the first still queues gets its jobs past that
  number cancelled by GitHub: plan.py keeps each dispatch under it, and
  nothing checks the queue for you, so dispatch the same tag again only
  when no run of an earlier dispatch is still queued (Actions > Bench:
  no run of the tag in progress). One dispatch holds the whole list only
  with at least ceil(pairs x arms / 100) accounts: three for the 50
  pairs under five arms; with fewer, the plan says how many items it
  left out and the next dispatch of the tag takes them.
  The workflow names the eight slots' secrets one by one rather than
  looking one up by a computed name, which would hand the runner every
  secret the repository can see. The approval gates this workflow's use
  of the accounts and not the secrets themselves, which any workflow of
  this repository, on any branch, may name: the secrets are shared with
  this repository alone, and only people with write access run workflows
  here.

The owner sets up, once and in this order: the environment bench in this
repository with the owner as a required reviewer, first, since a real
dispatch before it exists would make the environment with no reviewer
(GitHub makes an environment a workflow names, so bench may already be
there, with no rule, and then only needs its reviewer); the plan job
reads the environment and refuses a real run until it has one who may
approve this dispatch (actions/environment.py; a rule against
self-review with the owner as its only reviewer refuses too), so nothing
runs unapproved, and the run is dispatched again once the reviewer is
set (on the environment's page: Required reviewers ticked with the
owner added, Prevent self-review left unticked since the owner both
dispatches and approves, and Deployment branches and tags limited to
main); then an organization secret SYMPHONY_CLAUDE_<slot> per account,
the slot a number from 1 up (so SYMPHONY_CLAUDE_1 for the first account,
SYMPHONY_CLAUDE_2 for the second, up to 8), its value from `claude
setup-token` on that account, with Repository access set to Selected
repositories and this one ticked (this repository is public, so Private
repositories leaves it out, and All repositories would hand the token
to every workflow of the organization), which the plan job checks slot
by slot before any job runs; and the repository variable
SYMPHONY_CLAUDE_ACCOUNTS with the slots in use, separated by commas
(`1,2,3` for three accounts). A real dispatch is approved on its run's
page: Review deployments, tick bench, Approve and deploy. The token's value is never in a log:
run.py passes it to the agents by name, after each pair scrubs it out of
every file the pair left (above), and before anything is uploaded the
job scrubs the pair's directory once more and reads every file back,
archives included, publishing nothing that still holds it or could not
be read.

A dispatch names a tag; its artifacts are `<tag>__<arm>__<repo>__<task>__f1_f6`,
kept 90 days, with a marker `<name>__done` beside each finished one
(scored, no agent failed: in Error for a cause outside the benchmark, a
usage limit or a refused sign-in among them; one that ran out its time
is kept with its scored patch, PLAN.md's Resume). A dispatch of the same
tag plans no job for a pair whose marker is there and skips one whose
artifact holds a finished pair, scores from its files a pair whose
agents did not fail but that was never scored, and runs again a pair an
agent of which failed, so a run that stopped on a usage limit is
finished by dispatching it again, at most three dispatches in all;
GitHub's "Re-run failed jobs" on the run does the same for its failed
jobs, each re-run attempt replacing the attempt's artifact. A fake
dispatch's artifacts carry the tag `fake-<tag>`, and a real dispatch
refuses a tag that starts with `fake-` or `smoke-`, so a rehearsal never
stands in for the benchmark. The pairs come from PLAN.md's list
(`@bench/cooperbench/pairs.txt`), from `flash`, or from entries such as
`pallets_click_task/2068`; an agent's minutes are the `timeout_min`
input, 120 at most in real mode and 30 in fake. The report is the
artifact `report-<tag>-<run id>`, the job summary and the report job's
log; METRICS.md says where each of the paper's statistics is in it, and
its Dataset section carries the runner survey's gradeability when the
dispatch names the survey's tag (`survey_tag`), since the survey runs
under a tag of its own.

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
The flash subset over the four arms of that run is 50 solo sessions and
100 for each of the other three arms, 350 sessions in all, about $150 at
API prices. The dispatch in Actions runs five arms, so a pair is nine
sessions (one solo and two for each other arm): pairs.txt's 50 pairs are
450 sessions, about $195 at the authors' rate, and the pilot on
pallets_click 2068 (six pairs) 54 sessions, about $23. On a subscription
that is usage drawn from the plan's limits, not a bill, and an account
runs one job at a time, so a run spans days and several usage windows. A pair whose agents stop
on a usage limit is marked Error, and running the same command again
later runs it again; finished pairs are kept, a pair whose agent ran
out its 120 minutes among them, since its patch is scored. The pilot is about 42
sessions, near $18 at API prices. With CONCURRENCY=1 a pair takes 5 to 20
minutes per arm.

The container has 4 CPUs, 15 GB of memory and about 26 GB of free disk.
The task images are 0.1 to 4.2 GB compressed and about twice that on disk,
which is why the steps take one task at a time.

## How this run differs from the authors'

- Agent and model: Claude Code with MODEL (claude-sonnet-5-5 unless set)
  instead of codex with gpt-5.5.
- Claude Code is the session's own binary (2.1.296) rather than the latest
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
  patch, Redis and the git server, and installs uv and Claude Code where
  the host has none.
- prepare_task.sh: pulls or builds one task's image, adds the overlay
  (once per Claude Code version), and checks that its features are
  gradeable.
- survey.sh: prepares every flash task in turn and checks its gold
  patches grade here; it is what made the table above.
- run_arms.sh: runs the four arms on one task, and coop-nocomm
  (`--no-messaging`) when it is named, the no-communication baseline
  report.py sets against coop.
- pilot.sh: the six-arm pilot on one task (solo, coop, coop-nocomm,
  coop-git, team, sdlc) with the flags the click 2068 and jinja 1559 pilots
  used, then report.py and the free judge heuristics. Usage:
  `bash bench/cooperbench/pilot.sh <repo> <task> <prefix>`; the runs are
  <prefix>-<arm> under the logs and the report is <prefix>-report.md. It
  needs COORDBENCH_CLAUDE_TOKEN set, runs every arm even when one ends in
  error, and makes no judge model calls.
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
- fake_claude: the stand-in agent of a run that spends no usage (above).
- actions/: what .github/workflows/bench.yml runs: plan.py (the matrix),
  run-pair/action.yml and pair.sh (one pair and arm), survey_task.sh,
  artifacts.py (the resume and the report's download), environment.py
  (the gate before a real run), report.sh and survey_report.py,
  free_disk.sh; tested in tests/test_experiment_actions.py.
- PLAN.md, pairs.txt, METRICS.md: the benchmark pre-registered, its pair
  list, and where each of the paper's statistics is in the report.
- cooperbench-claude-code-setup.patch: the adapter's installer skips the
  install when claude is already in the image.
