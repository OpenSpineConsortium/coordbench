---
name: coordbench-coop
description: The coop protocol of a coordbench run, CooperBench's messages-only setting. Use when you are one of several agents each implementing a separate feature of the same codebase, to tell your peers which files you will touch, read what they sent, or agree on who owns a shared file.
---

# Coordinating by messages

You are one of two or more agents, numbered agent1, agent2 and so on.
Each was assigned a separate feature from the same codebase, and your
features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes. Messages are how you do it: your peers
know only what you tell them.

## The tools

Every tool is both an MCP tool of the server `coordbench` and a shell
command; both reach the same inbox, one per agent.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `send_message` (to, text) | `coop-send <recipient> "text"` | send to one peer, by agent id |
| `broadcast` (text) | `coop-broadcast "text"` | send to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |

Messages are never pushed into your conversation; you see them only when
you receive them.

## The protocol

1. At the start, broadcast a short summary of your feature and the files
   you intend to touch.
2. Receive your messages periodically, at the least after major edits
   and before you submit.
3. When two agents need to change the same file, coordinate explicitly:
   split the file, agree on one owner, or merge the changes.
4. Keep messages short and focused: file names, function names and
   one-sentence intents are usually enough.

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
