# Arms outside coordbench's package

An arm here is a directory that run.py's `--arm` takes by its path, as
coordbench/README.md describes for one's own arm; it is not a built-in
arm and is named by its directory. There is one, xblossom.

# The xblossom arm

xblossom is CooperBench's team mode with four moves added, from the
X-Blossom plan for parallel agents that reuse each other's checked work.
It is the built-in team arm plus one prompt section. On the session
route, which loads no arm's skill, a difference between the two arms
therefore comes from the prompt alone; with the claude-cli driver on the
mcp tool surface each arm also loads its own plugin's skill, and
xblossom's restates the four rules in other words. The arm is the
directory `xblossom/` beside this file, a Claude Code plugin and a
coordbench arm in one.

    xblossom/arm.json                            entry, name, needs shared_volume, setting team
    xblossom/arm.py                              XblossomArm(TeamArm): the section and the fact metrics
    xblossom/.claude-plugin/plugin.json          the plugin's name and description
    xblossom/.mcp.json                           the hub as the MCP server coordbench, as every built-in arm
    xblossom/skills/coordbench-xblossom/SKILL.md the team skill with the four rules, split by role

tests/test_xblossom_arm.py pins what makes the arm the team arm plus one
section; it starts no docker, hub process or agent.

## What it changes against team

Everything the team arm does stays the same:

- the lead and its members, and the runner's seeded tasks;
- the tools: the task list with its atomic claim, typed requests,
  messages and wait_for_message, on both tool surfaces;
- the shared volume at /workspace/shared;
- the metrics and the artifacts, which gain only two keys.

The team prompt, which is CooperBench's word for word, is an exact
prefix of every xblossom prompt. This was checked on both surfaces, with
and without a git remote, and for two and three agents. A team of one
gets the team prompt unchanged. arm.json declares `"setting": "team"`,
so the runner files a pair under `<run>/team/...` and records
`lead_agent`, `team_features` and each agent's `team_role`, as it does
for the team arm. The agents get no new tool: the moves use the team
arm's commands and plain files on the shared volume.

The added section, "Four working rules for this team", asks for four
moves:

1. **Facts keyed to file content.** Before an agent opens a file, it
   reads `/workspace/shared/facts/<git hash-object of the file>`. After
   it checks a fact about the file, it appends one line there: the
   path, the fact and the command that checked it. An unchanged file has
   the same hash in every agent's copy, so a peer's fact is found. An
   edited file gets a new hash, so a fact about old content is never
   read. The line is written through a here-document with a quoted
   delimiter (`<<\FACT`), so the shell runs and expands nothing in the
   fact. The template fits inside the session route's `bash -lc '...'`
   as long as the line holds no single quote, which would end that
   wrapper; the prompt asks for double quotes instead.
2. **A disjoint plan.** Each prompt holds only its own feature. So every
   agent first declares its feature's files in
   `/workspace/shared/FILES.md`, and the lead's `PLAN.md` gives each task
   a set of files built from those declarations:
   - A file one feature changes goes to that feature's task.
   - A file two features change is split by region.
   - A region both features must change is marked shared and joined at
     integration.
   - Nothing else is in two sets.

   A member asks before it touches anything outside its task, by a line
   in `/workspace/shared/REQUESTS.md`, and the lead reads that file when
   it polls the task list. Applying patches at integration does not
   count as editing another task's files.
3. **Claims.** The lead creates its tasks open, without `--assign`, and
   every agent takes work with `coop-task-claim`. That command is a
   check-and-set under the run's lock. An agent whose claim exits 2 does
   not wait: it claims another open task whose owner is empty or is
   itself.
4. **Certificates.** A done note carries:
   - where the work is (the member's patch on the volume, or the lead's
     working tree);
   - the lines `git diff --stat` printed, run where the agent runs git
     and pasted into the note;
   - the test command and the exit code it really returned.

Two of these moves add something the team arm lacks. Both are part of a
move rather than a general fix:

- The lead learns which files each member's feature changes (FILES.md).
  Move 2 cannot be followed without this.
- The lead reads one file of requests each time it polls. The requests
  travel through the volume, not through messages, so the xblossom lead
  gets no habit of reading its inbox that the team lead lacks.

## How to run it

In the session route (bench/cooperbench/session/README.md) a queue item's
arm is the directory, and queue.py, lane.py and bundle.py name it by the
directory's name, so its logs land under `<tag>-xblossom`:

    cd <coordbench>/bench/cooperbench/session
    ARM=<coordbench>/bench/cooperbench/arms/xblossom
    python3 queue.py add "$QUEUE" --tag xb1 --arms "solo,team,$ARM" --pairs go_chi_task/56/f1_f5

Keep the path free of spaces and shell metacharacters. Then start the
lanes as for any arm, with lane.js's `root` set to the checkout that
holds this arm (its default is /home/user/coordbench), and read the
results side by side:

    python3 summary.py --logs <log dir> --tag xb1 --arms solo,team,xblossom --bases solo,team
    python3 ../report.py --logs <log dir> --run solo=xb1-solo --run team=xb1-team --run xblossom=xb1-xblossom
    python3 bundle.py --logs <log dir> --tag xb1 --out <results dir> --arms solo,team,xblossom

With the claude-cli driver it is one run.py call per pair, signed in with
`CLAUDE_CODE_OAUTH_TOKEN` and never an API key:

    python3 <coordbench>/coordbench/run.py run --arm "$ARM" --driver claude-cli \
        --repo pallets_click_task --task 2068 --features 5,11 \
        --model claude-sonnet-5-5 --disallowed-tools WebFetch,WebSearch --name xb1-xblossom

## What it measures

The outcome is the team arm's, scored by the same code under the same
setting: CooperBench's merged evaluation of both agents' `patch.txt`,
the naive merge's tree when the two merge cleanly and the lead's patch
alone when they conflict. A region the plan marks shared and the lead
joins at integration usually sends the pair to the lead's patch alone.
The comparison that counts is paired, xblossom against team on the same
pairs, with the same model and limits (summary.py `--bases solo,team`
gives its wall, token and cost ratios). Since the prompts share their
whole team part, on the session route a difference in pass rate, wall
time or tokens is the four moves' effect.

Each move also leaves a trace that can be counted. None of these are
new instruments; they are the team arm's records and the copy of the
volume, read for the moves.

- **Facts.**
  - `metrics.json` has `fact_files` and `fact_bytes`: the regular files
    under `facts/` whose names are blob hashes, and their total size.
    The hub reads these from the volume's host path, opens none of them
    and never follows a symlink. A value is null when the hub cannot see
    the volume.
  - The runner's copy of the volume keeps the facts themselves under
    `<pair dir>/shared/facts/`, one line per fact, each starting with its
    path.
  - Reads leave no trace on the volume, so count them in the transcripts
    as `git hash-object` beside `/workspace/shared/facts`.
- **The plan.** `<pair dir>/shared/` keeps `FILES.md`, `PLAN.md` and
  `REQUESTS.md`. From them you can count the declared files, the shared
  regions and the requests with their answers. Comparing each agent's
  patch against its task's set shows whether the plan held.
- **Claims.** These are the team arm's metrics: `claims_per_agent`,
  `time_to_first_claim_seconds`, `tasks_total`, `tasks_done`,
  `unowned_at_end` and the claim events. A lost claim is a `task_claim`
  call refused with exit 2. The team arm's own runs give the baseline.
- **Certificates.** `tasks.json` and `task_log.json` keep each done
  note. A note certifies its work when it has a `patch:` line, the
  stat lines and a `test: ... -> exit N` line. Check its test claim
  against the agent's transcript, and its stat against the patch on the
  volume.

## Checks

`new_arm.py --check` cannot pass for this arm, as it cannot for the
built-in team arm: it builds its run with no options, so the hub refuses
an arm that needs the shared volume before the first tool call, and its
later steps call post_notice, a tool only the generator's board arms
have. tests/test_xblossom_arm.py does what --check means to do with the
tools the team arm has.
