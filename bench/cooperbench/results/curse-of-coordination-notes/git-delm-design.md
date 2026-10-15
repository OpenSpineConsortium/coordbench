# A git-plus-DeLM arm: design, fit and review

Written 2026-10-15 from three inputs, each checked by the session that made it:

- a code reading of DeLM (the research harness, yuzhenmao/DeLM main at 6661761 and its swe-bench branch at 834b698, and the plugin, Decentralized-LM/delm-agent-plugin 0.3.1 at 3bd42f9), cited below as [delm];
- a tested reading of native git mechanisms on git 2.43.0, two or ten clones against a bare remote served by `git daemon --enable=receive-pack` over git:// as coordbench serves it (bench/cooperbench/setup.sh:66-68), cited as [git];
- a review of coordbench on the PR #19 branch (worktree /home/user/coordbench-step, commits 71feb15 and 27f64f8), each finding verified by a second agent, cited as [review].

coordbench line numbers are at those commits; the ones spot-checked here are the same in /home/user/coordbench-xb at bcb7c91. "Inference" marks anything not measured or read.

## Short answer

1. **The combination is sound as a design, if each side does what it is good at.**
   - git handles content: a blob id makes a fact go stale by itself when its file changes, and a three-way merge with rerere imports a peer's work. That fits CooperBench pairs, whose agents open about half the same files but print only 6-25% of the same lines (xblossom/README.md).
   - DeLM supplies the coordination semantics: an append-only board read incrementally, claims with a version fence and a release, and check receipts that stay valid only while their inputs' content is unchanged.
2. **Take these from DeLM, as ideas:**
   - from the research harness: the per-agent incremental read, the long-poll wait, idempotent publications, and FACT/FAIL notes with a scope and a basis;
   - from the plugin: fenced claims with release and split (src/board/mod.rs:837-1003), and receipts keyed by input content (src/board/checks.rs:47-364).
3. **Do these in native git:**
   - content identity: `git hash-object`, or `git rev-parse <commit>:<path>`;
   - import by `git fetch` and `git merge`, never by DeLM's whole-file apply;
   - conflict prediction by `git merge-tree --write-tree`;
   - reuse of a peer's hand resolution through a shared rr-cache;
   - certificates as commit trailers.
4. **Keep authority in the hub, not in git refs.**
   - A claim under coordbench's `run.lock` is a real compare-and-swap, and it is recorded as an event.
   - A claim ref in git is a compare-and-swap only when every agent pushes a unique value under an empty lease. Without a server hook, any agent can `--force` it or delete it (findings 8 and 10 below). Agents routinely answer a rejected push with `--force`.
   - On the session route, `sleep` polling is what the sleep guard blocks, and a force push is the kind of command the auto-mode classifier refuses (finding 4; the second half is the review's inference).
5. **DeLM's own code cannot run under your constraints. Only its ideas can.**
   - The research harness reads a `claude setup-token` token from a mode-600 file, owns Docker, refuses to start while any container runs, and reads only Terminal-Bench tasks.
   - The plugin is macOS-only by hard gates. It runs two forks inside one Claude Code process, its MCP server refuses any call its own module did not mint, and the session route ignores plugins anyway.
   - Its `board/` Python (684 lines, MIT, standard library only) could be vendored. Its file transport and file-identity checks would then have to be replaced by git, so porting the few hundred lines of semantics is simpler (inference).
6. **The weak spot is the instrument, not the design.** As coordbench measures today, the arm's effect would be invisible or misattributed:
   - About 74-86% of every session agent's input is a fixed prompt of about 80k tokens that this session hands each subagent, re-read on every API call. So token ratios mostly count calls.
   - The scorer cannot tell integrated work from duplicated work.
   - A respawned or re-handed agent keeps the dead attempt's hub, ref and volume state while only the newest transcript is counted.

   These need fixing before any run (section 4, tier A).
7. **A prompt-only cousin already exists.** The xblossom arm (team plus four moves, with facts as files on the shared volume) is built: branch xb-arm at bcb7c91 in /home/user/coordbench-xb, copied to /mnt/project-files/experiments/curse-of-coordination/xblossom/arm/. Its README records no run. The git-plus-DeLM arm proposed here is its git-side counterpart, and the experiment below runs both.

## 1. How DeLM works

There are two different systems under the name.

### 1.1 The research harness (DeLM main)

**Launch** [delm]
- `delm.py` requires `--auth-file` or `--api-key-file` (delm.py:43-47, 57-80) and reads only Terminal-Bench 4.0 tasks (delm.py:86; fetch_task.py:10-11).
- `runtime.runner.run` starts a `ProtectedBoard` over SQLite and serves it with a `ThreadingHTTPServer` on the Docker bridge gateway (runner.py:297, 319-322; containers.py:100-116).
- Each agent gets a random 16-byte token, used as its URL path (runner.py:298; context_client.py:41-44).
- It refuses to start while any container is running, or when /proc/meminfo is missing (runner.py:100-103). It needs a clean git checkout of itself (runner.py:54-63).

**Agents** [delm]
- Each worker gets one container from the task image, with read-only mounts for the tools and the credential (containers.py:179-204; runtime_support.py:51-60).
- The worker is started by `docker exec ... python3.12 /tools/worker.py` (runtime_support.py:90-101). That runs Humanize's `claude --print --input-format stream-json --output-format stream-json` (humanize src/hmz/agents/claude.py:227-290).
- Agent, WebSearch, WebFetch and the cron tools are denied, and the MCP configuration is empty and strict (claude_api.py:20, 30-52).
- A stream audit fails the run if an MCP server appears, the model changes or a denied tool is used (claude_api.py:150-196).
- So each agent is plain `claude -p` with no plugins, hooks, MCP or subagents, and it reaches the board only as a shell command, `sh /tools/context.sh ACTION ...` (context.sh:1-2; prompts_n2.py:83-127).

**Board data model** [delm] (board/shared_context.py)
- One append-only `events` table (113-115). File bodies sit in an attached second database, so an entry and its files are written in one transaction (116-121).
- Every write is `BEGIN IMMEDIATE` (123-133).
- Queue state is derived from the log, with no planner (148-159).
- `claim` is a compare-and-swap that succeeds only on an open item, with at most one unfinished claim per agent (378-395). `reserve` creates and claims in one step (397-428), and `publish` dedupes items by normalised subject (51-53, 355-376).
- There is no release, no expiry and no split. The prompt tells peers to work past an ended worker's claim (prompts_n2.py:174-175).
- Notes are `--type FACT|FAIL` with `--scope` and a free-text `--basis` (454-472).
- Retries are idempotent through a payload fingerprint (context_client.py:129-131; shared_context.py:495-502).

**Reads and waits** [delm]
- The first read returns the whole board. Later reads return only what changed for that agent (198-233).
- `read --wait N` long-polls up to 25 s (28-33, 240-269, 520-559). The site says the log stays under 15K tokens with four agents (index.html:594).

**Sharing work** [delm]
- Files travel whole: at most 40 files, 200 KB each and 400 KB in total (context_client.py:90-117).
- `expand` shows a publication as diffs against the task's originals, not against the reader's tree (shared_context.py:568-585).
- `apply` refuses the whole batch on any file that differs from the original, the incoming version or the author's own (protected_board.py:11-45).
- "the engine does not automatically merge conflicting edits" (README:276-278).

**Staleness** [delm]
- There is none in code. A FACT is valid by its prose `basis` and by two prompt lines (prompts_n2.py:185, 205-207).
- The swe-bench branch adds regex heuristics for "verified" and "invalidated" (src/shared_lessons.py:225-243, 401-413), off by default (136-142).

**git** [delm]
- Not used for coordination. It appears only in the harness's check of its own source tree (runner.py:54-63).
- Submissions are copied out with `docker cp` and graded one by one (benchmark/grading.py:33-97).

### 1.2 The plugin (delm-agent-plugin 0.3.1)

**Shape** [delm]
- A Claude Code plugin module intercepts `delm:run` (hosts/claude/delm.js:626-643) and starts a Rust sidecar, `bin/delm claude serve` (delm.js:162-186; src/claude/mod.rs:99-162).
- The skill makes the parent issue exactly two `Agent` calls with `subagent_type: "fork"`. The module rewrites each fork's cwd to a private copy-on-write copy of the project (skills/run/SKILL.md:10; delm.js:699-743). The board allows exactly two workers (src/board/mod.rs:98).
- So both agents are forks of one conversation inside one Claude Code process on the host. It was qualified in interactive Auto mode only (docs/claude-integration.md:9, 66).

**MCP** [delm]
- Every `mcp__delm` call needs a one-use ticket that the module mints, bound to agent, turn, call id, tool and exact arguments (delm.js:839-866).
- The sidecar refuses unbound calls (src/claude/mod.rs:307-338), so its MCP server cannot be used by itself.

**Claims** [delm]
- Each claim gets a version. Every later update, release, split or finish must pass `expected_version`, or it fails with "stale task version" (mod.rs:837-1003).

**Check receipts**, the X-Blossom-like part [delm] (src/board/checks.rs)
- `delm_check_begin` hashes each listed input file (47-116).
- `delm_check_finish` checks the native Bash evidence: the command ran after the fence and inside the worker's copy. It re-hashes the inputs, and sets `reusable = inputs_unchanged && passed` (161-251).
- A peer reuses a receipt only if its own files equal the recorded versions and the request revision is the same (268-308). Completion re-checks the union of all scopes (311-364).
- This covers command outcomes only, not prose findings.

**Import and git** [delm]
- Import is whole-file, refused unless the local copy equals the base, the incoming version or a published version (mod.rs:653-703).
- git is used only to choose files and for `git merge-file` at final delivery (src/workspace/git.rs:85-125, 340-360; delivery.rs:299-324, 494-526).

**Platform** [delm]
- macOS only, with no fallback: the installer refuses non-darwin (packages/installer/lib/installer.mjs:32-34).
- Copy-on-write uses `fclonefileat` and fails rather than copy (workspace/mod.rs:227-275; prepare.rs:203-204).
- Supervision uses `proc_pidinfo` (supervisor.rs:41-89), and `qualify` stops on non-macOS (compatibility.rs:432-435).

### 1.3 What DeLM measured, and how it differs from CooperBench

**Correctness** [delm]
- Each worker solves the whole task and is graded alone. Accuracy is the mean over submissions, "not best-of-n" (runner.py:180-182; README:165-166).
- The prompt assumes "Every worker submits a complete solution", with fixed starting roles A and B (prompts_n2.py:43-68).
- CooperBench gives each agent one feature and scores one merged result. DeLM's lever, shared findings shortening a redundant search, therefore has less to work on here (inference).

**Tokens** [delm]
- Read from `modelUsage`, cache reads included (claude_api.py:198-228).
- "CLI-reported cost fields are not a subscription bill" (README:141-142).

**Results, Claude Opus 5.5, Claude Code alone against DeLM n=2** [delm] (index.html:679-683, 701-705)

| Benchmark | Accuracy (%) | Latency (min) | Cost per task |
|---|---|---|---|
| Terminal-Bench (10 tasks, 3 runs) | 81.67 vs 93.33 | 130.75 vs 64.77 | $20.31 vs $17.91 |
| DeepSWE | 71.67 vs 88.33 | 49.58 vs 32.98 | $12.01 vs $12.15 |

- The "Claude Code (subagent)" baseline on DeepSWE reached 50.00% at $19.83 per task (index.html:702).
- Neither branch has a DeepSWE runner or task fetcher. Whether that code is unpublished, or the tasks were placed by hand in Terminal-Bench format, could not be told.
- So two DeLM agents cost about what one costs, in a setting where both attack the same task and finish sooner. That is encouraging for the hypothesis, but it is evidence from a different setting.

## 2. Native git: what works and what only looks like it works

All tested on git 2.43.0 [git]. Scripts and full outputs are under /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/native-git/ (scripts/x1_notes.sh to x6c_rerere_symlink.sh, out/, tools/addonly_merge.py).

### 2.1 Works

| Mechanism | Command | Observed |
|---|---|---|
| Fact keyed by content | `git notes --ref=facts add -m '<fact>' $(git hash-object src/util.py)` | After an edit, `show` answers "no note found". After a revert, the fact is back. |
| Fact on a blob the reader lacks | `git notes --ref=facts show <full 40-char id>` | Prints the fact, exit 0; no object is needed. |
| Multi-input fact | `printf "src/app.py %s\nsrc/util.py %s\n" ... \| git hash-object -w --stdin` | The note goes stale when either input changes. |
| CRLF checkouts | `git hash-object <path>` (path filters on) | Gives the committed blob id, so facts match. `--no-filters` does not. |
| One notes ref per agent | push `refs/notes/facts/<agent>`; fetch `+refs/notes/facts/*:refs/notes/team-facts/*` | Every push is a fast-forward, and readers saw every agent's facts. |
| One shared notes ref across clones | `git notes append`, then push; on refusal, `fetch` plus `git notes merge -s cat_sort_uniq`, up to 20 tries | 60 of 60 facts kept (A: 30 pushes; B: 49 pushes). |
| Claim with a unique value | `C=$(git commit-tree -m "claim U by AGENT" HEAD^{tree}); git push --porcelain --force-with-lease=refs/claims/U: team $C:refs/claims/U` | 1 of 10 won, both simultaneous and staggered by 0.3 s. The winner sees `*`, or `=` when re-pushing its own `$C`. |
| Strict compare-and-swap on a shared filesystem | `git update-ref refs/claims/U $X 0000000000000000000000000000000000000000` | The second claimant gets "reference already exists", exit 128. |
| All-or-nothing multi-claim | `git push --atomic` with empty leases | One taken unit refuses the whole set. |
| Server-enforced claims | update or pre-receive hook: refs/claims/* create-only | `--force` and delete are both "hook declined". |
| Conflict prediction | `git merge-tree --write-tree --name-only --no-messages A B` | Exit 0 clean; exit 1 lists the conflicting files. 20 probes took 0.071 s. Merge drivers in info/attributes are honoured; rerere is not. Needs git 2.38 or later, on the hub's host. |
| Certificate as trailers | `git commit --trailer 'Test-Command: ...' --trailer 'Test-Exit: 0' --trailer "Inputs: f=$(git hash-object f)"` | A peer read them with `%(trailers:key=...,valueonly)`. Needs git 2.32 or later. |
| Certificate keyed by tree | a git-appraise ci JSON line on `HEAD^{tree}` | A merged B and B merged A: different commits, the same tree, and A's note was readable by B. |
| Reusing a hand resolution | `rerere.enabled true`, `rerere.autoupdate true`, and a shared rr-cache by `ln -s <shared>/rr-cache .git/rr-cache` | B's swapped merge was staged from A's resolution; feature.py had an identical md5. The merge still exits 1, so `git commit --no-edit` follows. |
| Insertion-only merge driver | tools/addonly_merge.py (60 lines, `git merge-file -p --diff3`), set in .git/info/attributes | Resolves append/append and two registry entries; leaves a same-line edit as a conflict. merge-tree agrees. |

### 2.2 Looks like it works, but does not

| Form | Observed | Why it misleads |
|---|---|---|
| `git push team HEAD:refs/claims/U` when both agents are at the same commit | "Everything up-to-date", exit 0; 10 of 10 "won" when staggered | The client never asks the server, so the second agent believes it won. |
| `--force-with-lease=refs/claims/U:` pushing HEAD (the recipe in off-the-shelf.md:31) | the same false win, 10 of 10 | The lease refuses only a different value. |
| `--force-with-lease=refs/claims/U` with no value, after fetching claims | "forced update", exit 0 | It steals the claim. |
| `--force` with unique values | 10 of 10 exit 0 | The last writer holds the claim. |
| Plain push of a descendant of the holder's commit | exit 0 | It takes the claim. |
| `git push team :refs/claims/U` by anyone | "[deleted]", exit 0 | Anyone can drop a claim; coordbench's remote has no hook (finding 8). |
| `--atomic` with no lease | exit 0 | `--atomic` is not a claim. |
| Concurrent `git notes add` on one ref in one repository (two worktrees) | 160, 176 and 179 of 200 kept, with no error | Silent loss. Use one ref per agent. |
| `git notes prune` | Would delete every fact on an object the clone lacks | Never run it. Abbreviated ids fail with exit 128. |
| Built-in `merge=union` | A same-line edit becomes two `return` lines; the code parses and is wrong | Never use it. |
| `notes.rewriteRef` on amend | Copies the certificate to the amended commit | A certificate must not follow new content. |
| `merge-tree` on an unknown commit | Empty stdout, exit 1 | This is the same exit as a conflict. sdlc-watch reads it as clean (arm.py:172-173); the gate reads it as conflicting with no files (bare_git.py:293-294). |
| Worktrees for parallel agents | Fine on one machine | In the bench a worktree in /workspace/repo becomes a gitlink in patch.txt, and git refuses it under another uid (finding 11). Each agent has its own container (run.py:2638-2652). |

### 2.3 None of it reaches the score

CooperBench scores in a fresh sandbox with no rerere and no drivers, and its union step writes `* merge=union` into .gitattributes (CooperBench 63b9d44 src/cooperbench/eval/sandbox.py `_merge_naive`, `_merge_union`) [git].

Drivers, rerere, notes and trailers change only the agents' own merges and records. When both agents submit the integrated diff, the scorer's merge is of identical changes (finding 1).

## 2b. What already exists: the xblossom arm

The xblossom arm is the team arm plus one prompt section; the team prompt is an exact prefix of it (xblossom/arm/README.md). Its four moves:

- facts at `/workspace/shared/facts/<git hash-object>`, written through a quoted here-document;
- a disjoint plan built from each agent's FILES.md;
- open tasks taken with `coop-task-claim`;
- done notes carrying `git diff --stat` and the test command's real exit code.

How it is built and run:

- It adds no tool and needs only `shared_volume`, so it runs on the session route today.
- Fact reads leave no trace, so hits have to be counted from transcripts.
- It lives outside coordbench's tree. On the session route each queue item's `run_name` must therefore be set by hand (README, "In the session route").

The git-plus-DeLM arm below differs in three ways:

- It builds on sdlc rather than team, so it has a git remote, a merge gate and patches diffed from the base.
- Its facts, claims and receipts are hub tools that emit events.
- It imports work by git merge.

## 3. The arm: git-delm, sdlc plus a hub board

This is a proposal; nothing in this section is built or run.

### 3.1 Shape

- A new arm directory that subclasses the sdlc arm, the way coop-git loads coop (coordbench/arms/coop-git/arm.py:30-35, 113).
- It inherits from sdlc:
  - the remote `team` (`needs=("git_remote",)`, sdlc/arm.py:86-87);
  - the gate's in-memory claim of a feature (sdlc/arm.py:218-228) and its merge into main (bare_git.py:259-278);
  - "main moved" answered by a merge (sdlc/arm.py:379-390);
  - patches diffed from the base sha (sdlc/arm.py:427, 431, 461-474).
- It adds `shared_volume` for the shared rr-cache. `run.py` provisions an arm's needs without any `--option` (run.py:2265-2278), so both needs work on the session route.
- **Units.** A feature stays an agent's own, as in sdlc. The board's units are the shared pieces: a helper, a registry entry, a region both features must change. These are the "two paths sharing a vertex" of X-Blossom. The claim decides who writes a shared piece, and the other agent merges it in.

### 3.2 Rounds

1. **Declare.** Each agent publishes its feature's planned files and the shared pieces it expects (`board-publish`). The hub reports overlaps from those declarations.
2. **Work.** Each agent works on its own claim branch and claims shared units with `board-claim`. It files facts as it verifies them, against the base commit for unchanged files, and pushes often.
3. **Integrate.** Each agent fetches and merges its peer's branch, with rerere on and a shared rr-cache, then records a receipt for the tests it ran (`certify`). The gate lands work on main.
4. **Submit.** The patch is `git diff --binary <base sha> HEAD -- . ':(exclude)patch.txt'`, built from the integrated tree as sdlc's rule does.

### 3.3 Hub tools

These are shell commands on both routes, written on the pattern of coordbench/examples/board/arm.py, with that file's lock split fixed (finding 16). The DeLM column says where each behaviour comes from.

| Command | From DeLM | Behaviour | Events |
|---|---|---|---|
| `board-read [--wait S]` | shared_context.py:198-233, 240-269 | Keeps a per-agent cursor in `run.state` and returns only what changed. The long-poll uses `run.wait_until` (coordbench/arm.py:360-373), capped at 90 s, under Bash's 120 s default. | read |
| `board-publish SUBJECT --paths P...` | shared_context.py:51-53, 355-376 | Opens a unit, deduped by normalised subject. | publish |
| `board-claim ID` | shared_context.py:378-395; plugin mod.rs:837-875 | Compare-and-swap under `run.lock`, with at most one unfinished claim per agent. Prints the claim's version. A lost race exits 3 and names the holder, because cli.py already uses exit 2 for transport failures (finding 12). `call.on_lost` undoes a claim whose reply was lost (coordbench/arm.py:191-208). | claim, claim_refused (holder, reason) |
| `board-release ID --version V`, `board-split ID --version V SUBJECT...`, `board-done ID --version V --at COMMIT` | mod.rs:877-1003 | Refused as stale unless V is the current version. | release, split, done |
| `fact-add --at COMMIT --path P... --check "CMD -> exit N" TEXT` | notes (shared_context.py:454-472); receipt inputs (checks.rs:47-116) | The hub reads each input's blob id with `git rev-parse COMMIT:P` from the bare remote, through the world's scratch clone (bare_git.py:213-215), and stores the fact under that set of (path, blob) pairs. `--blob P=SHA` from `git hash-object` is the fallback, which the hub cannot check. | fact_add, with the text as content |
| `facts --at COMMIT --path P...` | checks.rs:268-308 | Returns the facts whose recorded blobs all equal the reader's, and counts the stale ones. | fact_hit, fact_stale |
| `certify COMMIT --cmd "CMD" --exit N`; `receipts COMMIT` | checks.rs:161-251 | Keyed by the commit's tree id, so any commit with the same tree reuses it. Always marked self-reported, because the hub cannot run the tests (finding 9). | certify, receipt_reuse |
| `peers` | (sdlc-watch) | The sdlc-watch merge-tree probe (sdlc-watch/arm.py:166-174, 223-242), with a guard for empty stdout. | peers |

Rules for the arm's code:
- no model call in any hub tool (finding 6);
- every event is emitted inside the same lock hold as its state change (finding 16);
- `artifacts()` returns the board, the facts and the receipts as JSON;
- no file on the shared volume is named result.json (finding 15).

Size: about 300-400 lines of Python plus tests, against the 684 lines of DeLM's board with its file transport (inference).

### 3.4 Prompt changes (on top of sdlc's prompt)

1. The four rounds above, with the shared-unit rule: whoever holds the claim writes the piece, and the peer merges it in.
2. Facts: before reading a file, run `facts --at <base> --path P`; after checking something, run `fact-add`. Put several paths in one call, because on this instrument each call costs about 80k cached tokens (finding 2).
3. Waiting is `board-read --wait 60`, never `sleep N; cmd` (finding 4).
4. Never `git push --force` and never delete a ref. Only the gate writes main.
5. In the agent's clone: `git config rerere.enabled true && git config rerere.autoupdate true && ln -s /workspace/shared/rr-cache .git/rr-cache`.
6. Certificates: commit trailers `Test-Command` and `Test-Exit`, plus `certify`.
7. Nothing of the arm's goes into /workspace/repo (finding 11). After touching the marker, do nothing more in the workspace (finding 21).

### 3.5 Runner changes

- **Needed before any run.** The measurement fixes in section 4, tier A. They are core changes and apply to every arm.
- **Needed only for a git-native variant** (claims or facts in refs):
  - An update hook in `init_repository_script` (run.py:2424-2432) that makes refs/claims/* create-only, refs/notes/* fast-forward only and main fast-forward only. This is about 8 lines and a stated difference in PARITY.md.
  - The setup's force-push of main stays a no-op at the base (run.py:2710).
  - An arm can install the hook itself only when `git_remote_dir` is readable on the host (run.py:2461-2470). It is not with Docker in a VM.
- **Optional.** An arm hook that runs set-up commands inside each container, so git configuration does not depend on the prompt (the Arm methods are at coordbench/arm.py:430-477).

### 3.6 Session route and claude-cli

| | Session route (this cloud session) | claude-cli (your machine, WSL) |
|---|---|---|
| How the agents reach the arm | Host wrappers `coord-<tool>` (drivers.py:845-876). Plugins, hooks, MCP servers, `--option`, `--max-turns` and `--disallowed-tools` are dropped without a word (lane.py:92-95; drivers.py:907-917). | The arm directory loads as a plugin (`--plugin-dir`, drivers.py:643-646); MCP or Bash surface. |
| Git setup in the clones | By prompt only. | By the arm's hooks/hooks.json SessionStart. A probe with Claude Code 2.1.300 under `claude --print` added the notes refspec this way (refuted finding, scratchpad/gitdelm/refute-notes-refspec/). |
| Ablations | One arm directory each. | `--option`. |
| Permissions | The session's auto-mode classifier and Bash sleep guard apply (finding 4). | `--permission-mode=bypassPermissions` (drivers.py:642). |
| Time limit | Not enforced; a late marker is scored (finding 19). | coreutils `timeout`, with the sample kept (drivers.py:669, 737-739). |
| Per-call prompt | About 80k tokens inherited from this session (finding 2). | The container agent carries none of this session's context. Its size is unmeasured (open question 1). |
| Credential | None: agents are this session's subagents. | A `claude setup-token` token held in the shell environment (`CLAUDE_CODE_OAUTH_TOKEN`; coordbench passes it by name, coordbench/README.md:462-466). It must never be written to a file or a shell profile. |
| Host git | 2.43 here, which is fine. | merge-tree needs 2.38 or later. Ubuntu 22.04 ships 2.34.1, under which the standing and overlap probes silently answer "unknown" (finding 18). |
| Remote hooks from the arm | `git_remote_dir` is readable. | Not readable with Docker Desktop's VM (run.py:2461-2470), so hooks need the runner change. |
| Outside-tree arm | Set each item's `run_name` by hand, and use no spaces in the path (xblossom README; lane.js:78). | The path is passed to `--arm` directly. |

## 4. Review findings that matter for this arm, ranked

### Tier A: fix before any run

Without these, the experiment cannot show its effect, or it misattributes it.

**1. The scorer cannot tell integrated work from duplicated work.** Blocking.
- Where: report.py:815-828; export.py:1562-1574; judge.py:1530-1541, 1916-1919; CooperBench sandbox.py:168-212 at 63b9d44, which is parity, PARITY.md item 61.
- Evidence: 7 of 8 coop-git pairs and 7 of 8 sdlc pairs scored "identical", and so as conflict-free. On every identical pair the judge's overlap equals the patch's own hunk count. The merges with "# Conflicts:" that the agents resolved during the run vanish.
- Fix: measure authorship at close from the bare remote. Use each agent's non-merge commits from the base, or each claim branch from its fork point (sdlc-watch/arm.py:92-115), and report the duplicated added lines and the overlapping hunks between authored diffs. Give "identical" pairs their own column. Count conflicts from the "# Conflicts:" records in git_log.txt; sdlc's `merge_conflicts` counts gate refusals, which were 0.
- For team and xblossom, take the members' patches on the volume (inference).

**2. A fixed per-call prompt is most of every agent's input, and the call count is never stored.** Should-fix. For this arm it is in effect must-fix, because the arm's hypothesis is about tokens.
- Measured share of input:
  - 81-86% over 54-55 agents, by first-call input times calls [review];
  - 75-79% over 76 agents [review];
  - 74-76% over 72 agents (step README:280-282 at 30f4334).
- Share of cost: 35-43% of dollars at cache-read prices, per the step README. The review's 0.68-0.70 probably prices the first call's cache write on every call (inference).
- The first call is 78.5-81.7k tokens. Each agent's prefix lists 59 tools, including this Project's MCP tools, and carries an 80,895-character attachment with this session's CLAUDE.md chain.
- Where: `fill_from_transcript` drops the message count (drivers.py:973-990); `steps` is always null; lane.js:103 passes no agentType; summary.py:177 adds cache reads into total_tokens.
- Fix:
  - Store `steps` and the first-call prompt tokens.
  - Report calls per pair and tokens net of calls x prefix.
  - Spawn bench agents with a dedicated agentType and a minimal tool list. That removes the roughly 44k of tool definitions (the review's split).
  - The roughly 36k of instructions goes only if the lanes run from a session outside this Project (inference).
  - Hold the context equal across arms.

**3. Respawned and re-handed agents.** Blocking.
- Where: only the newest transcript is counted (drivers.py:958, sorted at 1029). A lane that returns hands out a plan that already ran (queue.py:150-153; lane.py:145-147, 114-126), and a Workflow resume can too.
- Evidence: step-8 sdlc 2068 f5_f11 counted about 31% of its cache reads, and its agent time included the dead gap. Its record says nothing about the second attempt.
- Why it matters here: the arm's state lives outside the workspace (hub board, facts, the remote's refs, the shared volume), so a second attempt starts with the first attempt's facts and claims in place. That inflates exactly the reuse the arm is meant to show.
- Fix:
  - Sum every attempt's transcript and record the attempts.
  - Refuse to hand out again a run whose agents left a marker, a patch, a dirty tree or a transcript. Mark it failed so it is requeued fresh.
  - Reset `started` and `deadline` only when the agents never ran.

**4. Harness events are scored as the arm's result.** Blocking.
- Lost subagents become agent errors with a made-up "45 minutes" (lane.py:210-213; drivers.py:931-935; run.py:3367-3373). They count against the arm in the errors-as-failures column (summary.py:224).
- Auto-mode refusals are never detected. An agent that returns `{finished:false}` is still given a marker (drivers.py:930; lane.js:105; lane.py:204-209).
  - Step-8 solo jinja 1621 lost its patch to such a refusal.
  - "Blocked: sleep" hit coop and coop-git seven times.
- ENOSPC is neither prevented nor detected (lane.py:148). Two counted step-8 go_chi runs met it and recovered.
- Fix:
  - `error_kind` "harness" for a dead agent.
  - Scan each copied transcript for the refusal texts and for "no space left on device". Any hit sends the run back to the queue, pass or fail.
  - Pass `finished===false` agents to finish as unfinished.
  - Check free disk at prepare.

**5. Effort and arm order are not held.** Should-fix; both are cheap.
- Bench agents inherit this session's effort (xhigh in all 76 transcripts). lane.js:103 pins the model and not the effort, and nothing records it.
- `queue.py add` keeps the `--arms` order on every pair and appends a new arm last (queue.py:124-138). The Actions route already rotates the order (actions/plan.py:144-145).
- Fix: pass and record effort; use a seeded permutation of the arms per pair; rerun the baselines in the same queue.

**6. Model calls inside hub tools are never counted.** Latent.
- Where: drivers.py:1020-1023, 958-962.
- Fix for this arm: make it a rule that no hub tool calls a model, and add a test.

Already fixed, not on main: an agent whose transcript was not matched counted as zero tokens and $0 (summary.py:74). This is fixed on the PR #19 branch at 71feb15 and 27f64f8. The driver gained a "named" match tier (drivers.py:1034), and summary.py now leaves an unmeasured agent's pair out of the token and cost figures. It is not on main at 5028999.

### Tier B: design around in the arm

These need no core change before the first run.

7. **The session route drops plugins, MCP servers and options** (lane.py:92-95; drivers.py:907-917).
   - The arm is hub tools plus prompt only, with one directory per ablation.
   - Later, a plan warning and an `options` field on queue items.
8. **The bare remote enforces no ref rule** (run.py:2424-2432). Keep claims in the hub. Main can still be force-pushed by any agent; add the hook in section 3.5 if that matters.
9. **The gate runs no tests** (bare_git.py:150-152; sdlc/arm.py:99, documented). Receipts are self-reported. A gate that requires a check refuses every merge (gate.py:487-489), so never set `required_check` with BareGitWorld.
10. **The claim-ref recipe in off-the-shelf.md is a false win.** See 2.2. Use the hub, or the unique-value recipe in 2.1.
11. **Patch formation picks up whatever is in the tree** (coop-git/arm.py:64, 76; run.py:2630-2635, 2775-2785).
    - What goes wrong: a worktree becomes a gitlink, a binary fact store fails the whole patch, patch.txt gets committed, and a diff against `team/main` drops or reverses earlier landings.
    - Fix: diff from the base sha with `--binary` and exclusions; put the arm's files in .git/info/exclude or on the volume; refuse a mode 160000 line.
12. **A refused claim leaves no cause, and exit 2 is overloaded** (mcp.py:209-210; cli.py:93, 267-271, 719-721). The arm's claim uses exit 3 and emits `claim_refused`.
13. **Team claims cannot be released, and the volume mirror races** (team/arm.py:212-240, 395-402, 538-548).
    - Both are CooperBench parity.
    - They matter for xblossom: adding a release is a stated difference, and agents should use `coop-task-list`, not `ls` of the mirror.
    - git-delm has its own board with a release.
14. **`new_arm.py --check` cannot exercise an arm with needs** (new_arm.py:377). Use an in-process smoke check like xblossom's smoke_check.py.
15. **The copied shared volume is walked as pair directories** (runconfig.py:302; judge.py:288-293; export.py:2103-2113). Never name a file result.json on the volume.
16. **The board example appends and emits in separate lock holds and keeps no text** (examples/board/arm.py:45-47; new_arm.py:179-181). Emit inside the same hold, with the text as content, and override `artifacts()`. off-the-shelf.md:145 recommends this example as the pattern, so copy it fixed.
17. **git_log.txt hides claim and notes refs, and counts notes commits as the agents' edits** (kit.py:321, 358-366; judge.py:1803-1836). Overwriting a peer's note was flagged as a destructive edit. Record claims and facts as hub events. If notes are used, fix the decoration and the judge.
18. **merge-tree needs git 2.38 or later and degrades silently** (bare_git.py:290-295; sdlc-watch/arm.py:166-174). Check the version at hub start, and guard against empty stdout.

### Tier C: report-time fixes and nits

19. **The time limit is not enforced on the session route** (lane.js:102-104; drivers.py:926-937).
    - Rounds that can stall make this worse.
    - State the limit in the prompt; give every wait a timeout; flag overruns; later, a watchdog that snapshots the patch at the deadline.
20. **Output tokens are a lower bound priced as the count** (drivers.py:512-544; summary.py:83, 89-93).
    - Output is undercounted by about 7% for solo to about 20% for sdlc; the cost effect is a few percent.
    - Report cost as an interval, and compare arms on the exact input side.
21. **Wall time and agent time drift.**
    - Wall time includes the finish lag and provisioning (run.py:3101-3102); use the agents' span.
    - The deadline starts at prepare, a median 5.6 s early (run.py:2868).
    - The marker is advisory: patch.txt is read at finish (drivers.py:937-940).

### Refuted, for completeness

- **"An arm cannot configure the agents' clones."** False on claude-cli: a hooks.json SessionStart hook did it. On the session route, the prompt is the only way.
- **"A late response is lost in the team arm."** True, but it is CooperBench's own behaviour and no run used it. Do not carry handoffs over `coop-request`/`coop-respond`.

## 5. Experiment plan

### Phases

**Phase 0** (no usage)
- Tier A fixes with tests.
- The git-delm arm with an in-process smoke check: race two claims, file and read a fact, go stale on an edit, certify, read the events.
- Build the authored-diff measure and check it on step-8's raw sdlc and coop-git records, which need no new runs.

**Phase 1, rehearsal**
- One pair (click 2068 f5_f11) under solo, team, xblossom, sdlc and git-delm, one repeat: 5 runs.
- Pass condition: every new field is filled. That means calls, prefix, effort, attempts, authored overlap, claim, fact and receipt events, and no harness flag.

**Phase 2, the comparison**
- The 8 step-8 pairs under the same 5 arms, 2 repeats each: 80 runs.
- The order of arms is permuted per pair, with the seed recorded in the sample.
- All arms run in one queue, with the same model, effort and agent context.
- Claude Code has no seed, so "repeat" means a fresh run.
- outlines 1655: every arm's evaluation hit ENOSPC while installing torch, so its tests never ran (review note on queue.py:127). Rerun it with disk headroom, or swap the pair.
- Run typst alone on one lane, as step 8 did.

**Phase 3, only if Phase 2 is borderline**
- A third repeat.
- Ablations as separate arm directories:
  - git-delm without facts (claims and merge only);
  - git-delm with the insertion-only merge driver.

### Metrics

Per pair and repeat, compared paired against solo and against each arm's parent: team for xblossom, sdlc for git-delm.

- **Calls per pair** (`steps`), and **tokens net of calls x first-call prefix**, beside the raw totals.
- **Cost:** input-side cost (exact) plus an output interval.
- **Duplicated work from authored diffs:** added lines both agents authored, and overlapping hunks. Also "identical" submissions as their own column, and conflicts resolved in the run, from "# Conflicts:" records.
- **Pass rate:** CooperBench's merged evaluation, with harness-disturbed runs left out and requeued.
- **Time:** the agents' span, not wall time to finish.
- **The arm's own counters:**
  - claims won, lost, released and split;
  - facts added, hit and stale;
  - receipts issued and reused;
  - rerere replays.

  For xblossom, facts are counted from `fact_files` and from the transcripts.

### Expected usage

These are list-price estimates; /usage's dollars are not a bill.

- **Step-8 means per run:** solo $0.72, team $1.43, sdlc $1.30 (summary.md). Assume about $1.5 for each of the two new arms (inference).
- **Phase 2:** about $6.5 per pair-repeat, so about $100 for 80 runs.
- **Windows:**
  - Step 8 ran 34 runs in one five-hour window at four lanes (step README:294-296).
  - The pilot hit the session limit after 21 runs at five lanes.
  - So Phase 2 takes about two to three windows. Phase 1 fits in a fraction of one.
- A trimmed agent context would make every run cheaper, but it also makes step-8's numbers incomparable. Rerun the baselines either way (finding 5).

### What would count as the curse eliminated

The baseline from step 8 (summary.md at 30f4334):
- Tokens: the two-agent arms used 1.88 to 2.45 times solo's per pair.
- Calls: 32-38 per pair against solo's 16.8.
- Pass: every two-agent arm passed 6 of 8, against solo's 5 of 8. Only go_chi separates the arms.

Proposed thresholds, for you to adopt or change:

- **Eliminated.** All of these hold, over Phase 2's 16 pair-repeats:
  - git-delm's paired median calls per pair and net tokens per pair are at most 1.25 times solo's;
  - it passes on at least as many pair-repeats as sdlc;
  - authored duplicated added lines are near zero and below sdlc's;
  - its agents' span is at most solo's.
- **Reduced.** Calls and net tokens are at least a third below sdlc's on the same pairs, with pass not lower.
- **Not shown.** Anything else.

At 16 pair-repeats the pass rate is a guard, not a test: one or two pairs of difference is noise.

**Prediction, written before running** (inference):
- On the session route, git-delm lands between sdlc and solo on calls and tokens, not within 1.25x of solo. Each board or fact command is a call that re-reads about 80k tokens, and a lookup saves only when it replaces at least one read call.
- Duplicated authored lines and conflicts re-resolved should fall against sdlc.
- Pass should not change.
- If the claude-cli agent's prompt turns out much smaller, the transcript share rises there, and facts can show more.

## 6. Corrections to the earlier notes

- **xblossom/README.md and off-the-shelf.md:18** say about 95% of input is each agent re-reading its own transcript. About 95% is cache reads, but 74-86% of input is the fixed per-call prompt inherited from this session. The transcript's growth is roughly 15-25% (finding 2).
- **off-the-shelf.md:31's claim recipe** is a false win when both agents push the same commit. Use a unique `commit-tree` value and check `--porcelain` for `*` (section 2).
- **off-the-shelf.md:102-105 and rag.md:192** say DeLM gives no cost. Its site gives cost per task (section 1.3), and its code records tokens including cache reads.
- **off-the-shelf.md:145's board pattern** has the lock split of finding 16.
- **rag.md:270** says it is unverified that subagents inherit the session's MCP servers. They at least carry their tool schemas: the bench agents' prefix lists hearthbot, remote-devices and Claude_Docs tools (finding 2).
- **The DeLM reading's two unverified points** are answered by the git tests:
  - `git notes show` works when the blob object is absent;
  - git daemon with receive-pack accepted pushes to refs/notes/* and refs/claims/*.

## 7. Open questions

1. How large is a claude-cli agent's first-call prompt in a task container? Measure one stream before choosing the route. It decides how much a fact table can show.
2. Do one-container-per-agent bind mounts lock correctly for `update-ref` or a shared rr-cache across two containers? Only two processes on one filesystem were tested.
3. Which git versions are in the task images? rerere and merge need any git; trailers need 2.32 or later. bench/cooperbench/build_task_image.sh:38 prints the version in the build logs.
4. Can the roughly 36k tokens of instructions be removed while the lanes run inside this Project? Inference says only from a session outside it.
5. Does an exported setup-token count as a stored credential under your rule? This matters only for the claude-cli route.
6. Are the thresholds in section 5 the ones you want? And are 2 repeats enough? Bai et al. report up to 30x token variance across repeats (unverified).
7. Is DeLM's DeepSWE runner unpublished, or were the tasks placed by hand?

## Sources

- **DeLM.** All clones were deleted. Re-clone at the commits named at the top to check any DeLM file:line.
- **Git test scripts and outputs.** /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/native-git/
- **Review scripts.** /tmp/claude-0/-home-user/b761d652-4496-5832-92b3-e817935ce1a4/scratchpad/gitdelm/ (measure/, verify-*/, session-route/, refute-notes-refspec/)
- **Earlier notes and the xblossom arm.** /mnt/project-files/experiments/curse-of-coordination/xblossom/ (README.md, off-the-shelf.md, rag.md, arm/)
- **The readings and the bypass-permissions flag.** The workflow harness flagged the readings for a bypass-permissions pattern. The text only describes how Humanize maps its default permission (hmz config.py:164; claude.py:75-80, 251-258) and how coordbench's claude-cli driver starts claude (drivers.py:642). It carried no instruction.
=====
Short version: the combination is sound as a design, but only DeLM's ideas can come along, not its code.
Its research harness wants the setup-token saved in a file and owns Docker, and its plugin is macOS-only and never loads on the session route anyway.
So the arm I'd build is sdlc plus a small hub board: DeLM's incremental reads, version-fenced claims and check receipts as shell commands, with git doing content (blob ids for staleness, merge and a shared rerere cache for a peer's work).
Claims belong in the hub, not in git refs: a ref claim pushed at the same commit is a false win, and any --force takes it.
The bigger finding is the instrument. About three quarters of every agent's input is a fixed ~80k-token prompt this session hands each subagent, so token ratios mostly count API calls, and the scorer can't tell integrated work from duplicated work.
Those two, plus respawn and harness-loss counting, need fixing before any run, or the arm's effect is invisible.
The one decision that's yours: run it here on the session route (no credential at all, but we trim the agent context), or on your machine with claude-cli under WSL (hooks and permissions behave, but you export a setup-token in the shell and need git 2.38+ and Docker).
I lean toward here, since nothing new touches a credential.
The note has the design, the ranked findings and an 80-run plan (about two to three usage windows, roughly $100 at list price).
