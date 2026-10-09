---
name: coordbench-board
description: The board protocol of a coordbench run, CooperBench's messaging plus a shared notice board. Use when you are one of several agents each implementing a separate feature of the same codebase, to say on the board which files you take, read what your peers took, or message them.
---

# Coordinating through a shared board

You are one of two or more agents, numbered agent1, agent2 and so on.
Each was assigned a separate feature from the same codebase, and your
features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes. The board is where you say what you
take; messages are for everything else. Your peers know only what you
tell them.

## The tools

Every tool is both an MCP tool of the server `coordbench` and a shell
command; both reach the same board and the same inboxes, one per agent.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `post_notice` (text) | `board-post <text>` | pin one line on the board, signed with your agent id |
| `read_board` | `board-read` | every line on the board, oldest first |
| `send_message` (to, text) | `coop-send <recipient> "text"` | send to one peer, by agent id |
| `broadcast` (text) | `coop-broadcast "text"` | send to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |
| `wait_for_message` (timeout_ms) | `coord-wait_for_message` | block until a message arrives, instead of polling |

## The protocol

1. At the start, read the board, then pin one line naming your feature
   and the files you intend to touch.
2. Read the board again before you edit a file you did not name, and
   receive your messages after major edits and before you submit.
3. When two agents need to change the same file, coordinate explicitly
   by message: split the file, agree on one owner, or merge the changes.
4. Pin a last line when you are done, so your peers know what to expect
   from your patch.

## Submitting

Before you exit, write your final unified diff to
`/workspace/repo/patch.txt`; the evaluator reads that file and nothing
else.

```bash
cd /workspace/repo
git diff > patch.txt
cat patch.txt
```

The diff holds only the source files you changed to implement your
feature: no reproduction scripts, no scratch tests, no helper files, and
no changes to test files unless the task asks for them.
