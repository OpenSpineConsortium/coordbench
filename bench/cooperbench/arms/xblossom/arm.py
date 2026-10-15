"""The xblossom arm: the team arm plus the four moves of the X-Blossom plan.

Everything the team arm does stays as it is: the lead and its members,
the runner's seeding of the task list, the tools (the task list with its
atomic claim, typed requests, messages and wait_for_message), the metrics
and artifacts, the two tool surfaces and the shared volume at
/workspace/shared. The prompt is the team arm's, which is CooperBench's
word for word, with one section added after it, under a rule of its own,
asking for four moves:

1. Facts keyed to file content. Before opening a file an agent reads
   /workspace/shared/facts/<git hash-object of the file>; after checking a
   fact about it, the agent appends one line there: the path, the fact
   and the command that checked it. An unchanged file has the same hash
   in every agent's copy, so a peer's fact is found, and an edited one has
   a new hash, so a fact about old content is never read. The line is
   appended through a here-document with a quoted delimiter, so the shell
   neither runs nor expands what the fact quotes, and it starts with the
   path because two files of the same content share one hash.
2. A disjoint plan. Each agent's prompt holds only its own feature, so
   every agent first declares its feature's files in FILES.md on the
   volume, and the lead's PLAN.md gives each task a set of files from
   those declarations: a file two features change is split by region, a
   region both must change is marked shared and joined at integration,
   and nothing else is in two sets. A member asks before it touches
   anything outside its task by a line in REQUESTS.md, which the lead
   reads when it polls the task list; the request goes through the
   volume and not through messages, so the lead is given no habit of
   reading its inbox that the team arm's lead lacks.
3. Claims. The lead creates its tasks open, without --assign, and every
   agent takes work with coop-task-claim, which is a check-and-set under
   the run's lock; an agent whose claim exits 2 takes another open task
   that no other agent owns rather than wait, since --open lists open
   tasks whoever owns them.
4. Certificates. A done note carries where the work is, the lines git
   diff --stat printed, the test command and its exit code, not a
   narrative. The stat is run where the agent runs git and pasted into
   the note, never substituted into the coordination command, which on
   the session route runs on the host and not in the agent's container.

The moves use the team arm's own commands and the shared volume, so the
agents get no new tool and a difference against the team arm is the
prompt's alone. The section goes last, after any git block the run adds,
so the team arm's prompt is always a prefix of this one, and a team of
one gets no section, as it gets no role block. arm.json declares the
setting team, so the runner files and records a pair as it does the team
arm's.

The facts live outside /workspace/repo, so they never reach a scored
patch, and the runner's copy of the volume keeps them after the run
(<pair dir>/shared/facts/). metrics() adds how many fact files the volume
holds when asked and their size, from the volume's host path; it opens
none of them, since the volume is the agents' to write. A read of a fact
leaves no trace on the volume, so reads are counted off the transcripts.

The team arm's class is loaded from the built-in team directory, the way
coop-git loads coop's, so the two arms share one copy of CooperBench's
text and this one stays the team arm plus one section.
"""

import importlib.util
import os
import re
import stat
from pathlib import Path

from arm import builtin_arms_dir


def load_team_module():
    """The built-in team arm's module, found through coordbench's own arms
    directory, so a copy of this directory elsewhere still finds it."""
    path = builtin_arms_dir() / "team" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_team", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


team = load_team_module()

# git hash-object prints 40 hex digits in a SHA-1 repository and 64 in a
# SHA-256 one; any other name under facts/ is not a fact file.
BLOB_NAME = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

XBLOSSOM_SECTION = """## Four working rules for this team

These rules come on top of everything above; where they differ from it,
they win. The lead is **{lead}**. Run each shell block below where you
run git.

### 1. Facts are filed under the file's content hash

`/workspace/shared/facts/` holds checked facts about the repository's
files, one file per content hash. Before you open a file, read its facts:

```bash
cd /workspace/repo && cat /workspace/shared/facts/$(git hash-object <path>) 2>/dev/null
```

After you check a fact about a file (what a function does, where a feature
hooks in, which tests cover it), append one line to that file's facts:
its path, the fact, then the command that checked it. The here-document
keeps the line exactly as you type it, quotes, `$` and backticks included:

```bash
cd /workspace/repo && mkdir -p /workspace/shared/facts &&
  cat >> /workspace/shared/facts/$(git hash-object <path>) <<\\FACT
<path>: <fact> -- checked by: <command>
FACT
```

Files with the same content share one hash, which is why each line starts
with its path. A file whose content changes gets a new hash, so facts
about its old content are never read again; hash the file as it stands
when you check. Facts live only on the shared volume, never in the
repository.

### 2. A disjoint plan

Each agent's prompt holds only its own feature, so the plan is built from
what every agent declares. Before you edit anything, append to
`/workspace/shared/FILES.md` one line per file your feature changes, as
`{agent}: <path>`: the files your spec lists as modified or, where it
lists none, the files you expect to change.

The lead's `/workspace/shared/PLAN.md` gives each task on the list a set
of files (paths under `/workspace/repo`), from FILES.md and the lead's own
spec. A file only one feature changes goes to that feature's task. A file
two features change is split into regions (a function, a class or a
block the plan names), each going to the task of the feature that changes
it; a region both features must change, such as one function's
signature, is marked shared, both agents make only their own change
there, and the lead joins the two at integration. No other file or
region is in two sets.

Edit only the files and regions of the task you own; until PLAN.md gives
your task its set, edit only files that FILES.md shows no other agent
declaring.

{plan_by_role}

Applying a finished member's patch at integration (`git apply`, and
fixing what it breaks), or merging the lead's work when the lead asks,
is not an edit of another task's files.

### 3. Claim open tasks; never wait on a lost claim

The lead creates its tasks open, with `coop-task-create "<title>"` and no
`--assign`; this replaces the `--assign` in the lead's step 2. Everyone
takes work by claiming it: `coop-task-claim <task_id>` exits 0 when the
task is yours and 2 when another agent owns it. On exit 2, do not wait
and do not ask: run `coop-task-list --open` and claim another task whose
owner is empty (`""`) or is you; `--open` also lists open tasks that
other agents own, and those are not yours to claim. The tasks the runner
made at the start keep the owners it gave them.

### 4. A done note is a certificate

When you set a task to done, the note carries evidence, not a narrative:
where the work is, what `git diff --stat` printed, the test command you
ran and the exit code it really returned.

```bash
{diff_command}
```

Then set the task to done with this note, the stat lines pasted as git
printed them:

```text
{patch_line}
<the lines git diff --stat printed>
test: <command> -> exit <code>
```

With the shell command, put the note in single quotes,
`coop-task-update <task_id> done -n '<note>'`, so the shell passes it as
written. Write `test: none` when you ran no test. A message saying you
are done carries the same lines."""


# Rule 2's last paragraph, by role. A member asks before it touches
# anything outside its task, on a file of the volume the lead reads when
# it polls; the lead keeps the plan and answers there.
MEMBER_PLAN = """Before you touch a file or region outside your task, append a
request to `/workspace/shared/REQUESTS.md`, as
`{agent}: may I edit <path or region>? <why>`, and leave it alone until
the lead says yes on a line below yours or moves it to your task in
PLAN.md; re-read both files while you wait, and go on with what is yours."""

LEAD_PLAN = """Write PLAN.md once every member has declared its files, planning your
own feature's tasks meanwhile. Each time you poll the task list, re-read
FILES.md and `/workspace/shared/REQUESTS.md`, where a member asks before
it touches anything outside its task: answer each request on a line below
it, as `{lead}: yes` or `{lead}: no, <why>`, and move the file or region
in PLAN.md when you agree. Before you touch a file or region of another
agent's task, move it to yours in PLAN.md and say so on a line of
REQUESTS.md."""

# Rule 4's command and the certificate's first line, by role. A member's
# work goes to the lead as a patch on the volume; the lead's stays in its
# own tree, which it submits, so it writes no patch there that its
# checklist would list among the members' and try to apply to itself.
MEMBER_DIFF = "cd /workspace/repo && git diff > /workspace/shared/{agent}.patch && git diff --stat"
MEMBER_PATCH_LINE = "patch: /workspace/shared/{agent}.patch"
LEAD_DIFF = "cd /workspace/repo && git diff --stat"
LEAD_PATCH_LINE = "patch: the lead's working tree"


def xblossom_section(agent, lead):
    is_lead = agent == lead
    names = {"agent": agent, "lead": lead}
    plan_by_role = (LEAD_PLAN if is_lead else MEMBER_PLAN).format(**names)
    diff_command = (LEAD_DIFF if is_lead else MEMBER_DIFF).format(**names)
    patch_line = (LEAD_PATCH_LINE if is_lead else MEMBER_PATCH_LINE).format(**names)
    return XBLOSSOM_SECTION.format(plan_by_role=plan_by_role, diff_command=diff_command,
                                   patch_line=patch_line, **names)


def regular_size(directory, name):
    """The size of a regular file in the open directory, or None for
    anything else; a symlink is looked at, never followed."""
    try:
        status = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        return None
    return status.st_size if stat.S_ISREG(status.st_mode) else None


def fact_metrics(run):
    """The fact files on the volume and their bytes, from its host path (the
    run option shared_dir_host); None for both when the hub cannot see the
    volume, and zero when nobody wrote a fact."""
    shared = run.options.get("shared_dir_host")
    if not shared or not Path(shared).is_dir():
        return {"fact_files": None, "fact_bytes": None}
    try:
        directory = os.open(Path(shared) / "facts", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {"fact_files": 0, "fact_bytes": 0}
    except OSError:
        return {"fact_files": None, "fact_bytes": None}
    try:
        sizes = [regular_size(directory, name) for name in os.listdir(directory) if BLOB_NAME.fullmatch(name)]
    finally:
        os.close(directory)
    sizes = [size for size in sizes if size is not None]
    return {"fact_files": len(sizes), "fact_bytes": sum(sizes)}


class XblossomArm(team.TeamArm):
    """The team arm, with the four moves added to every agent's prompt."""

    name = "xblossom"
    description = ("CooperBench's team mode plus four moves: facts filed under a file's content "
                   "hash on the shared volume, a disjoint file plan, open tasks taken by claim, "
                   "and done notes that carry evidence.")

    def sections(self, run, agent, specs):
        sections = super().sections(run, agent, specs)
        if run.peers(agent):
            sections.append(xblossom_section(agent, team.lead_of(run)))
        return sections

    def metrics(self, run):
        return {**super().metrics(run), **fact_metrics(run)}
