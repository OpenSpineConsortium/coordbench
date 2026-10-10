---
name: coordbench-sdlc-watch
description: The sdlc-watch protocol of a coordbench run, the claim protocol of OpenSpineConsortium/sdlc-template with every agent watching its peers' open pull requests. Use when you are one of several agents each implementing one feature of the same codebase as an issue, to claim your issue, open your draft pull request early, look at your peers' pull requests after every push, settle overlap while both are drafts, and land through the merge gate.
---

# Working by the claim protocol, watching your peers' pull requests

You are one of two or more agents, numbered agent1, agent2 and so on,
each given one feature of the same codebase. The work is a queue of
issues, one per feature and numbered by it: issue #1 is feature 1. You
work the way this project's protocol has every account work: pick an
issue from the board, claim it, do the work in the open on its own branch
and pull request, and finish by marking the pull request ready, which
asks the merge gate to merge it. One issue, one branch `claude/<issue>`,
one pull request.

## The tools

Every tool is both an MCP tool of the server `coordbench` and a shell
command; both act on the same board and the same inboxes.

| MCP tool | Shell command | What it does |
| --- | --- | --- |
| `board` | `sdlc-board` | the issues, the claims, the pull requests and how each branch stands to main |
| `claim_issue` (issue) | `sdlc-claim <issue>` | claim an issue; exclusive, first come, first served |
| `open_pull_request` (issue, title) | `sdlc-pr <issue>` | open the draft pull request from `claude/<issue>` to main, once the branch is pushed |
| `pull_request_status` (issue) | `sdlc-status <issue>` | how one pull request stands, and whether it merges cleanly with main now |
| `peer_pull_requests` | `sdlc-peers` | every peer's pull request beside your branch: the files you both change, whether your branches merge cleanly, the lines you both added |
| `mark_ready` (issue) | `sdlc-ready <issue>` | mark the pull request ready: releases the claim and asks the merge gate to merge it |
| `send_message` (to, text) | `coop-send <recipient> "text"` | comment to one peer, by agent id |
| `broadcast` (text) | `coop-broadcast "text"` | comment to every other agent |
| `receive_messages` | `coop-recv` | drain your inbox; a JSON list, oldest first |
| `peek_messages` | `coop-peek` | how many unread messages you have |
| `list_agents` | `coop-agents` | every agent id in the run |
| `wait_for_message` (timeout_ms) | `coord-wait_for_message timeout_ms=5000` | wait for the next message instead of polling |

Messages are this project's comments. They are never pushed into your
conversation; you see them only when you receive them.

## Exit codes

A shell command that did what it says prints its answer and exits 0. One
that is refused prints why on stderr, and its exit code says what kind of
refusal it is, so a script can branch on it. Over MCP the refusal is an
error result, with the same code in its `_meta` as `coordbench/exitCode`.

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | done | carry on |
| 1 | refused: no such issue, an issue number that is not one, a branch not pushed yet, nothing new to merge, an issue already closed or already yours | fix what the message names and try again |
| 2 | someone else's: another agent holds the claim, the issue was given to another agent, or the pull request is another agent's | pick another issue, or leave it to its holder |
| 3 | main moved: the merge gate left main as it was | merge team/main into your branch, resolve, push, and mark ready again |
| 4 | the merge gate could not act: git failed or timed out, the shared remote cannot be read, or the run has closed | try once more; if it persists, submit your own branch |

Code 2 is the one CooperBench's coop-task-claim gives a task someone else
owns: you lost the race, not made a mistake.

## The protocol

1. Pick: read the board. It lists every issue with its title (its
   feature's first heading) and its label (todo, claimed, in-review or
   closed), the claims held, and the pull requests with their states and
   how each branch stands to main: clean, behind (main moved but the
   branch still merges cleanly) or conflicting.
2. Claim: a claim is exclusive and first come, first served. It succeeds
   once; a claim of an issue another agent holds is refused (exit 2), and
   then you pick another. Each agent claims only the issue it was given,
   unless the prompt says the queue is open.
3. Branch: start `claude/<issue>` from the base ref your prompt names and
   push it to the remote `team`.
4. Work in the open: as soon as the branch has a first commit, push it
   and open the pull request; it stays a draft until you mark it ready.
   Push after each step that works, so your peers see your work while
   it is under way. Tell your peers which files you will touch, read
   what they send, and agree on who owns a file two features both need.
   Watch their pull requests (below).
5. Finish: commit, push, and mark the pull request ready. That releases
   your claim and asks for the merge. The merge gate merges
   `claude/<issue>` into `main` on `team` only when it merges cleanly
   with the current main, pushes main, and answers with the merge commit.
6. Main moved: when another pull request landed first and yours no
   longer merges cleanly with main, the gate leaves main as it was and
   answers (exit 3) "main moved: merge team/main into your branch,
   resolve the conflicts, push, and call mark_ready again". Do exactly
   that:

```bash
cd /workspace/repo
git fetch team
git merge team/main          # resolve the conflicts so both features work
git add <the files you resolved>
git commit --no-edit
git push team claude/<issue>
```

and mark the pull request ready again. The pull request's status tells
you beforehand whether your branch still merges cleanly with main.

## Watching your peers' pull requests

`sdlc-peers` (`peer_pull_requests`) reads the shared remote and sets each
peer's pull request beside your own branch: the files it changes, the
files you both change and the lines each of you touched there, whether
your branch and theirs merge cleanly as they stand, the lines you both
added (usually the same thing written twice), and whether they pushed
since your last look. Run it once your pull request is open, after each
push, and before you mark ready. Act on what it shows while both pull
requests are drafts:

- Lines you both added, or a helper you are both writing: build on the
  version that exists instead of writing a second one. Merge their
  branch into yours (`git fetch team && git merge team/claude/<their issue>`)
  or agree by message which of you keeps it.
- A conflict between your branch and theirs: read their change to the
  file it names (`git diff team/main...team/claude/<their issue> -- <file>`)
  and settle with them now who changes which lines, so neither of you
  meets it at the merge gate.
- Files you both change without a conflict: read their change anyway,
  since two edits that merge cleanly can still break each other.

Merging their branch into yours is how you take their work; never push
to their branch.

Rules that hold everywhere: one issue, one branch `claude/<issue>`, one
pull request. Never push to `main`, which only the merge gate moves, or
to another agent's branch, and never force-push: you bring main in by
merging it, never by rebasing.

## Git set-up

In `/workspace/repo` the remote `team` is the shared repository, and
`team/main` is its default branch. The run began at a base ref, which
your prompt names; only the merge gate moves `team/main` past it.

```bash
cd /workspace/repo
git fetch team
git checkout -b claude/<issue> <base ref>
git add <files>
git commit -m "<what the change does>"
git push -u team claude/<issue>
```

Commit only the source files the feature needs, and never patch.txt.

## Submitting

Once your pull request has merged, write `/workspace/repo/patch.txt`
from the integrated main, so every agent submits the whole team's work:

```bash
cd /workspace/repo
git fetch team
git diff <base ref> team/main > patch.txt
```

If your pull request cannot merge before you stop, submit your own
branch instead: commit your work, then run
`git diff <base ref> HEAD > patch.txt`. The evaluator reads that file and
nothing else; it holds only source files changed to implement the
features, with no reproduction scripts, scratch tests or helper files,
and no changes to test files unless the task asks for them.
