---
name: coordbench-team
description: The team protocol of a coordbench run, CooperBench's team mode. Use when you are the lead or a member of a team of agents each owning one feature of the same codebase, to find or claim your task on the shared list, report progress or a blocker, ask a teammate for a decision, or hand your patch to the lead.
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
too few words, or a --wait that is not a number, exits 2.

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
