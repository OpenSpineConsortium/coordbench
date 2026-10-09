---
name: coordbench-coop-git
description: The coop-git protocol of a coordbench run, CooperBench's setting with messages and a shared git remote named team. Use when you are one of several agents each implementing a separate feature of the same codebase, to message your peers, publish your branch, and merge theirs before you submit.
---

# Coordinating by messages and a shared remote

You are one of two or more agents, numbered agent1, agent2 and so on.
Each was assigned a separate feature from the same codebase, and your
features may overlap (touch the same files), so coordinate to avoid
clobbering each other's changes. You reach your peers two ways:
messages, and a git remote named `team`.

The bench evaluates each agent's `patch.txt` against every feature's
test suite, so your patch must hold your peers' work as well as yours:
you must merge their branches before you write it.

## The tools

Every messaging tool is both an MCP tool of the server `coordbench` and
a shell command; both reach the same inbox, one per agent.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `send_message` (to, text) | `coop-send <recipient> "text"` | send to one peer, by agent id |
| `broadcast` (text) | `coop-broadcast "text"` | send to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |

## The shared remote

In `/workspace/repo` the remote `team` is already configured. Your
branch is named after your agent id and is already created and pushed;
each peer's work is on `team/<their id>`, and `team/main` is the
pristine starting state. During the run, `git fetch team` shows what
your peers published, `git branch -r` lists every team branch, and
`git log team/<peer> --oneline -10` or `git show team/<peer> -- <path>`
shows what a peer changed.

## The protocol

1. At the start, broadcast a short summary of your feature and the files
   you intend to touch.
2. Receive your messages periodically, at the least after major edits
   and before you submit.
3. When two agents need to change the same file, coordinate explicitly:
   split the file, agree on one owner, or merge the changes.
4. Before you exit, commit your work, push your branch, merge every
   peer's branch, and build your patch from the merged tree. If you skip
   the merge step, you lose points the bench would otherwise award.

## Submitting

Run this before you exit, with one fetch-and-merge line per peer:

```bash
cd /workspace/repo
git add -A
git commit -m 'wip: my work' || true
git push team <your id> --force || true
git fetch team && git merge --no-edit team/<peer> || true
git diff team/main..HEAD > patch.txt
wc -l patch.txt
head -30 patch.txt
```

`/workspace/repo/patch.txt` is the one file the evaluator reads. It
holds only source files changed to implement the features, yours and
your peers': no reproduction scripts, no scratch tests, no helper files,
and no changes to test files unless the task asks for them.
