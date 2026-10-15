---
name: coordbench-xblossom
description: The xblossom protocol of a coordbench run, CooperBench's team mode plus four rules (facts filed under a file's content hash, a disjoint file plan, open tasks taken by claim, done notes that carry evidence). Use when you are the lead or a member of a team of agents each owning one feature of the same codebase, to read or record a checked fact about a file, find or claim a task, keep to your task's files, report progress or a blocker, or hand your work to the lead.
---

# Working as a team

You are one agent of a team, numbered agent1, agent2 and so on, each
assigned one feature of the same codebase. agent1, the agent with the
lowest feature, is the team-lead; the others are members. The benchmark
scores the merged work of the team, so the lead's integration of every
member's patch is the point of the whole arrangement.

## The tools

Every tool is both an MCP tool of the server `coordbench` and a shell
command; both reach the same task list, requests and inboxes.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `task_create` (title, assign) | `coop-task-create [--assign <agent>] "<title>"` | create an open task, optionally pre-assigned; prints its id |
| `task_claim` (task_id) | `coop-task-claim <task_id>` | become the task's owner; it moves to in_progress |
| `task_update` (task_id, status, note) | `coop-task-update <task_id> <status> [-n "<note>"]` | set the status of a task you own, with a note |
| `task_list` (mine, open) | `coop-task-list [--mine] [--open]` | every task as JSON; --mine yours, --open those still open |
| `request` (recipient, kind, body, wait) | `coop-request <recipient> <kind> [body] [--wait N]` | a typed request to one agent; prints its id, or with a wait the response |
| `respond` (request_id, body) | `coop-respond <request_id> [body]` | answer an open request addressed to you |
| `pending` | `coop-pending` | the open requests addressed to you, as JSON |
| `send_message` (to, text) | `coop-send <recipient> "text"` | send a message to one agent |
| `broadcast` (text) | `coop-broadcast "text"` | send a message to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |
| `wait_for_message` (timeout_ms) | `coord-wait_for_message` | block until a message arrives, instead of polling |

## The task list

A task has an id, a title, an owner, a status, who created it, when, a
last_note and metadata. The statuses are open, in_progress, blocked and
done. A new task is open even when it is assigned: assigning names the
owner the lead expects, and that owner still claims it.

A claim succeeds for exactly one agent. A task that another agent owns,
whether it claimed the task or was assigned it, cannot be claimed; the
claim is refused with the owner's name, and you pick another task.
Claiming a task you already own is fine. Only a task's owner can update
it.

The shell commands exit as CooperBench's do, so a script can branch on
the code: coop-task-claim exits 2 when another agent owns the task and 1
when there is no such task; coop-task-update exits 3 for a task you do
not own and 4 for an unknown status; coop-request with --wait exits 5
when no response came in time; coop-respond exits 6 for an unknown or
closed request and 7 for one addressed to another agent. A command given
too many words, or too few for the arguments it cannot do without (a
title or a body of several words wants its quotes; a body left off is
read from stdin), or a --wait that is not a number (true, false and null
included), exits 2.

At the start the list holds one task per member, "Implement feature N",
assigned to that member, and the lead's "Lead-only: integrate and submit
feature N".

## The lead

1. Plan to avoid conflicts: if features touch the same file or function,
   divide the work into disjoint regions, and write a one-paragraph plan
   to `/workspace/shared/PLAN.md`.
2. Break the spec into 2 to 4 concrete tasks and assign one per member.
3. You may implement a task yourself in parallel.
4. List the tasks now and then; when a member's task is blocked, read its
   last_note and reassign it or unblock the member with a message.
5. Submit only when every task is done, every member's patch is at
   `/workspace/shared/<agent_id>.patch`, you have read and applied each
   one (`git apply`), the merged tree builds, and `git diff` shows every
   feature. Then write `/workspace/repo/patch.txt` from `git diff`.

## A member

1. List the open tasks; the one whose owner is your id is the one the lead
   expects you to take. `/workspace/shared/tasks/` may also hold the list
   as files.
2. Claim it; if the claim is refused, pick another.
3. Read `/workspace/shared/PLAN.md` if it exists and edit only the regions
   your task owns. When blocked, set the task to blocked with a note
   saying what you need, and message the lead.
4. When done, export your diff with
   `git diff > /workspace/shared/<your id>.patch`, then set the task to
   done with a note naming that file. The lead integrates.
5. Write your own `/workspace/repo/patch.txt` from your final working tree
   before you exit.

## Requests and messages

A message is for a short question that does not need a task. A request
is for a question that needs an answer by id, such as a plan approval:
the requester can block on the response with a wait, the addressee finds
it among its pending requests and responds, and responding closes it.

## The scratchpad

`/workspace/shared/` is mounted in every agent's container and is not
evaluated: use it for the plan, interface notes, partial diffs and the
members' patches.

## Submitting

Before you exit, write your final unified diff to
`/workspace/repo/patch.txt`; the evaluator reads that file and nothing
else. It holds only the source files you changed to implement the
feature: no reproduction scripts, no scratch tests, no helper files, and
no changes to test files unless the task asks for them.

## The four rules of this arm

These come on top of everything above; where they differ from it, they
win. Run each shell command below where you run git.

### 1. Facts are filed under the file's content hash

Before you open a file, read its facts:

```bash
cd /workspace/repo && cat /workspace/shared/facts/$(git hash-object <path>) 2>/dev/null
```

After you check a fact about a file (what a function does, where a
feature hooks in, which tests cover it), append one line to its facts:
the path, the fact, then the command that checked it. The here-document
with a quoted delimiter keeps the line exactly as typed, quotes, `$` and
backticks included:

```bash
cd /workspace/repo && mkdir -p /workspace/shared/facts &&
  cat >> /workspace/shared/facts/$(git hash-object <path>) <<\FACT
<path>: <fact> -- checked by: <command>
FACT
```

Files with the same content share one hash, hence the path on each line.
An edited file has a new hash, so facts about its old content are never
read again. Facts live only on the shared volume, never in the
repository.

### 2. A disjoint plan

Each agent sees only its own feature, so before editing anything every
agent, the lead too, appends to `/workspace/shared/FILES.md` one line per
file its feature changes, `<your id>: <path>`, from its spec's list of
modified files. The lead's `/workspace/shared/PLAN.md` gives each task a
set of files from those lines: a file one feature changes goes to its
task; a file two change is split into regions (a function, a class, a
named block), each going to the feature that changes it; a region both
must change is marked shared, each agent making only its own change
there for the lead to join at integration; nothing else is in two sets.
Edit only your task's files and regions, and until the plan gives your
task its set, only files no other agent declared.

- A member, before touching a file or region outside its task, appends
  `<your id>: may I edit <path or region>? <why>` to
  `/workspace/shared/REQUESTS.md` and leaves it alone until the lead says
  yes below that line or moves it to the member's task in PLAN.md, going
  on with its own work meanwhile.
- The lead writes PLAN.md once every member has declared, re-reads
  FILES.md and REQUESTS.md each time it polls the task list, answers each
  request on a line below it (`<lead id>: yes` or `<lead id>: no, <why>`),
  and moves the file or region in PLAN.md when it agrees. Before it
  touches a file or region of another agent's task, it moves it to its
  own in PLAN.md and says so on a line of REQUESTS.md.

Applying a finished member's patch at integration, or merging the lead's
work when the lead asks, is not an edit of another task's files.

### 3. Claim open tasks; never wait on a lost claim

The lead creates its tasks open, with no `--assign`. Everyone takes work
with `coop-task-claim`; on exit 2 another agent owns the task, so list
the open tasks and claim another whose owner is empty (`""`) or is you,
instead of waiting. `--open` also lists open tasks that other agents
own, such as the lead's integration task, and those are not yours to
claim.

### 4. A done note is a certificate

The note carries where the work is, the lines `git diff --stat` printed,
the test command you ran and the exit code it really returned, or
`test: none`.

- A member runs
  `cd /workspace/repo && git diff > /workspace/shared/<your id>.patch && git diff --stat`
  and starts the note with `patch: /workspace/shared/<your id>.patch`.
- The lead runs `cd /workspace/repo && git diff --stat` and starts the
  note with `patch: the lead's working tree`; it writes no patch under
  `/workspace/shared/`, which holds the members' patches.

Paste the stat lines into the note as git printed them, with no `$(...)`
in the coordination command, and with the shell command put the note in
single quotes: `coop-task-update <task_id> done -n '<note>'`. A message
saying you are done carries the same lines.
