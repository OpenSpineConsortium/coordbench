# Deep research: native git plus DeLM for agents that reuse verified results

Question (the owner's, 2026-10-15): How to combine native git (worktrees, notes on blobs, compare-and-swap refs, merge-tree) with DeLM's harness (or other pieces in the above artefact about XBloom Applied to this problem) and plugin for parallel Claude Code agents that reuse verified results, measured on CooperBench in coordbench, with a subscription only and no stored credential.

Run 2026-10-15 16:50-17:55 UTC: 5 search angles, 23 sources fetched, 94 claims extracted, 25 verified by three adversarial votes each (19 confirmed, 6 refuted), merged into 13 findings. arXiv and kernel.org were unreachable from this container; papers were checked through their authors' repositories.

## Summary

Git itself provides every primitive this design needs, and each one has a working precedent. (1) Verified results can be stored as git notes keyed by content: git-test keys on the tree, not the commit, so a result survives rewording, squashing and tree-preserving rebases. (2) A result can be published, or work claimed, without a server: git update-ref does a compare-and-swap on a ref, and its --stdin mode applies several ref updates all or none. grite and Git AI already coordinate agents through custom refs and notes namespaces that sync with plain fetch and push. (3) git merge-tree --write-tree can test two agents' branches for a three-way merge without a worktree or an index. That fills a gap in DeLM: its research harness rejects a conflicting batch of files and leaves the agent to reconcile it, and its board is an HTTP and SQLite service, not git. The part of DeLM worth taking is the native plugin's check receipt: a command outcome bound to its scope, snapshot, worker and request revision, invalidated when any file in the scope changes, with stated limits. A note on a blob covers one file with no mode, so the key should be a tree of the receipt's scope, plus the command and the environment as Bazel's action key does. On credentials, DeLM's subscription mode needs a stored setup-token file, and coordbench's drivers today read CLAUDE_CODE_OAUTH_TOKEN as well. A run with no stored credential therefore has to run its agents inside the user's own logged-in Claude Code, as the DeLM plugin does. None of the verified sources measured any of this on CooperBench, so the coordbench arm and its metrics are design proposals that have not been tested.

## Findings

### 1. (high confidence, vote 3-0 (both claims [0] and [1]))

A verified result can be stored as a git note keyed by content rather than by commit. git-test writes each result with `git notes --ref=tests/<name> add -f <rev>^{tree} -m good|bad`, one notes ref per test, under refs/notes/tests/<name>. A result therefore stays valid across rewording, squashing, splitting, some reorderings, and merges and rebases that leave the tree unchanged, and a rerun tests only trees it has not seen.

Evidence: The verifier read bin/git-test on master. write_status and read_status run git notes add and show against <rev>^{tree}, and the run loop skips any tree already marked good or bad. The README says results are recorded by the tree, not the commit, and lists a commit-dependent mode as future work. Caveats from the verifiers: the key is the test name plus the tree, not the command plus the tree, so changing a test's command keeps its stale results with only a warning. Results from a dirty working tree are not recorded. --retest, --force and --forget override the skip. For agents this means the key has to include the command and the environment, and the result is valid only for a test that depends on nothing but the tree.

Sources: https://github.com/mhagger/git-test

### 2. (high confidence, vote 3-0 (claims [8] and [18]))

A reusable result needs a key that covers more than file content. DeLM's native plugin binds each check receipt to a native command outcome, its scope (1 to 2048 explicit input files, each recorded as sha256, byte count and executable bit, with absence recorded too), the snapshot, the worker and the request revision. The integrating worker reuses receipts that still apply, any change to a scoped file invalidates them, and there is no second full-suite pass and no verifier gate. Bazel's remote cache splits the same idea into an action cache and a store of outputs: the action cache maps a hash of an action's declared inputs, output names, command line and environment to result metadata (output names plus content hashes), and a content-addressable store (CAS) holds the output files. For git this means the note should be keyed on a tree built from the receipt's scope files and their modes, not on single blobs, with the command and an environment digest in the key.

Evidence: DeLM: docs/architecture.md line 73 at 3bd42f9 (2026-10-07), quoted verbatim, and confirmed in src/board/checks.rs. finish_check_record binds snapshot_id, worker and request_revision. A receipt is reusable only when its inputs are unchanged and it passed, and shared_checks refuses one whose scope differs from the current files. FileVersion in src/board/files.rs is {sha256, bytes, executable}. Bazel: docs/remote/caching.mdx on master, lines 17-23 and 33-35, with HTTP paths /ac/<sha256> and /cas/<sha256> written by PUT and read by GET. The verifier's warning: a note on one blob covers one file and no mode, so a multi-file receipt needs a note on a tree or on some other composite object.

Sources: https://github.com/Decentralized-LM/delm-agent-plugin, https://bazel.googlesource.com/bazel/+/HEAD/docs/remote/caching.mdx

### 3. (high confidence, vote 3-0 (claim [9]))

Reusing a result proves less than it seems, and a key built on content alone proves even less. DeLM states that a receipt establishes only the observed outcome for its recorded inputs. It does not prove coverage, undeclared dependencies, the ambient environment, or that nothing changed between observations. Background, interrupted, timed-out and unobserved results never count as completed checks.

Evidence: docs/architecture.md line 75 (verbatim) and line 73. src/board/checks.rs line 266 says the check proves scope equality and observed execution, not complete coverage. docs/support.md line 49 and docs/claude-integration.md line 17 repeat the rule on interrupted and background results. The verifier adds that a key of file content alone, such as notes on blobs, is weaker than DeLM's key, because it also leaves out the command and the request revision.

Sources: https://github.com/Decentralized-LM/delm-agent-plugin

### 4. (medium confidence, vote 2-1 (claim [17]))

A cache of results helps only if its record outlives the job that made it, and Git's own CI no longer keeps one. Under GitHub Actions, skip_good_tree in ci/lib.sh returns at once. The GitHub Actions and GitLab CI branches both set cache_dir=$HOME/none, so the good-trees file is lost between jobs, and the skip message still links to Azure Pipelines. For parallel agents, verified results should therefore live in refs pushed to the shared remote, not in a runner's local cache.

Evidence: The verifier read git/git next and master through the GitHub mirror (the googlesource URL itself was not fetched). In lib.sh, lines 124-128 return early under GitHub Actions, lines 227 and 281 set cache_dir=$HOME/none, and nothing in .gitlab-ci.yml caches that directory. save_good_tree still runs but writes to a directory that is thrown away. A related claim, that Git's CI actively skips a commit whose tree already passed, was refuted 0-3, which matches this.

Sources: https://code.googlesource.com/git/+/next/ci/lib.sh

### 5. (high confidence, vote 3-0 (claim [2]))

git merge-tree --write-tree performs a full merge without making a commit and without reading or writing the working tree or the index. That includes the three-way content merge, rename detection, directory/file conflicts, and a virtual merge base when there are several bases. A coordinator can use it to test whether two agents' branches merge, without a worktree.

Evidence: git's Documentation/git-merge-tree.adoc on master was quoted verbatim; kernel.org itself returned 403. A verifier tested it on git 2.43 in a bare clone. A rename plus edit merged cleanly with another branch's edit, and no commit was added. Edits to the same line produced CONFLICT (content). A criss-cross history with two merge bases merged, and HEAD and the status were unchanged. Limits: it needs Git 2.38 or later, it still writes blob and tree objects, and a clean merge is only textual and says nothing about whether the combined code works. A stronger claim was refuted 1-2: that the exit status (0 clean, 1 conflict) and the tree OID printed alone form a contract that can be fed straight into commit-tree and update-ref. A harness should not rely on that contract without testing it on its own git version.

Sources: https://www.kernel.org/pub/software/scm/git/docs/git-merge-tree.html

### 6. (medium confidence, vote 2-1 (claims [12] and [14], merged))

DeLM's research harness does not merge. When an agent imports a peer's files, the engine first checks local edits and file authorship. Any conflict rejects the whole selected batch and leaves reconciliation to the agent, by comparing whole-file sha256 hashes with no three-way merge. Its shared board is a SQLite event log served over HTTP, not git. merge-tree could resolve the edits that do not overlap, because DeLM keeps the hash of the original task file, which can serve as a merge base. Edits that truly conflict would still go back to the agent, and refs updated by compare-and-swap would make publishing atomic without merging anything.

Evidence: README.md lines 275-278 at 66617616 (2026-10-08) are quoted verbatim. ProtectedBoard.apply in board/protected_board.py sets applied = not conflicts and returns no files when there is a conflict. A grep finds no merge-tree, merge-file or diff3 code. board/shared_context.py uses sqlite3, and runtime/runner.py starts a ThreadingHTTPServer. Two of three votes on each claim. The dissent: exactly the gap overstates it, because DeLM rejects on purpose (a later publication from another author is not proof of a merged file), and the HTTP server code lives in runtime/, not board/.

Sources: https://github.com/yuzhenmao/DeLM, https://arxiv.org/abs/2606.10662

### 7. (high confidence, vote 3-0 (claims [3] and [4]))

git update-ref is a compare-and-swap on one ref, and it needs no server. With three arguments it writes <new-oid> only if the ref still holds <old-oid>, and an all-zero or empty old value means create only if absent. In --stdin mode a set of updates and verifies is all or none for writers, so an agent can record a result ref only if an input ref is still at X. Readers can still see a partial state while the transaction runs. Agents working in worktrees of one repository share refs/*, so they get this lock locally. Agents in separate clones need the remote's push rules to get the same guarantee: refusal of an existing ref, --force-with-lease, or --atomic. Symphony's claim works the same way: it is the first push of claude/<N>, and GitHub refuses a second push of the same name.

Evidence: git master's Documentation/git-update-ref.adoc is quoted verbatim. Verifiers ran it on git 2.43. A second create of the same ref failed with exit 128 (reference already exists), and so did an update with a stale old value (is at X but expected Y). A --stdin transaction that paired a create with a failing verify created neither ref. Caveats: the two-argument form overwrites without any check. Forty zeros is the SHA-1 null id, while the empty string works in SHA-256 repositories too. Since Git 2.50, --batch-updates lets each update fail on its own, which drops the all-or-none rule, so a harness must not pass it.

Sources: https://git-scm.com/docs/git-update-ref

### 8. (high confidence, vote 3-0 (claims [5] and [6]))

Agents already coordinate through git refs and notes with no server. grite keeps an issue tracker for several agents entirely in custom refs outside the working tree: an append-only log at refs/grite/wal with one commit per append and CBOR chunks under events/YYYY/MM/DD/, snapshots at refs/grite/snapshots/<unix_ms>, and lease locks at refs/grite/locks/<hash>. All of it syncs with plain fetch and push of refs/grite/*. Git AI's standard requires agent authorship notes to live in their own ref, refs/notes/ai, and never in the default refs/notes/commits. That is precedent for giving verified results a namespace of their own, such as refs/notes/verified.

Evidence: grite was checked against its code at 33afee09 (2026-07-02): WAL_REF in wal.rs, SNAPSHOT_REF_PREFIX in snapshot.rs, the lock ref in lock_manager.rs, and GRITE_REFSPEC refs/grite/*:refs/grite/* in sync.rs. A lock is an atomic create on the local ref, so across clones it is only a best-effort lease until it is pushed. Git AI was checked against specs/git_ai_standard_v3.0.0.md at 0670e7e, lines 23-24, which use RFC 2119 MUST and MUST NOT. Its notes attach to commits, not blobs. The usegitai.com page itself was never fetched. Two further grite claims were refuted 0-3: that it re-parents a refused chunk and pushes again, and the details of its advisory lock policy.

Sources: https://github.com/neul-labs/grite, https://usegitai.com/docs/get-started/how-git-ai-works

### 9. (medium confidence, vote 2-1 (claim [7]))

Git AI shares notes without a server through a fetch, merge and push loop. It fetches the remote notes ref into a tracking ref, merges with `git notes merge -s ours` so local notes win, and pushes without force, so the push must be a fast-forward. When the push is rejected as non-fast-forward, it reruns the whole cycle. That is the pattern a single shared refs/notes/verified would need.

Evidence: Checked against src/git/sync_authorship.rs and src/git/refs.rs at 0670e7ef (v1.7.6). PUSH_NOTES_MAX_ATTEMPTS = 3 counts total attempts, so there are at most two retries. A fast-forward-only push checks ancestry, not the exact old value, so it resembles a compare-and-swap without being a strict one. The default backend is git notes; an optional HTTP backend skips this path. For verified results, a set-union strategy such as cat_sort_uniq fits better than -s ours (see the next finding).

Sources: https://usegitai.com/docs/get-started/how-git-ai-works

### 10. (medium confidence, vote 3-0 (claim [16]))

The CALM theorem, the basis of the Bloom language, sorts the design into what needs coordination and what does not. Programs have consistent implementations without coordination exactly when they can be written in monotonic logic. Adding verified results to a store that only grows, merged by set union, needs no lock. Claiming, releasing, retracting or declaring a result final is not monotonic and needs a compare-and-swap on a ref. A verifier showed that in git this holds only with one ref per writer: two agents pushing one shared notes ref collide as non-fast-forward, while per-agent refs merged with `git notes merge -s cat_sort_uniq` converge.

Evidence: The quote matches the abstract in several copies (arXiv v2, ar5iv, and CACM 63(9):72-81, 2020), but the body was not read because the proxy blocked it. The verifier tested in git 2.43. Notes attach to blobs. cat_sort_uniq took the union of two agents' notes. Pushes to one shared ref were refused for the second writer, and per-agent refs succeeded. Later work widens 'exactly': handing out tasks by a rule every agent knows, such as a hash of the issue number, needs no claim at all. A related claim was refuted 0-3: that the paper presents a metric such as coordination events per CooperBench pair. That metric would be this project's own, not the paper's.

Sources: https://arxiv.org/abs/1901.01930

### 11. (medium confidence, vote 3-0 (claim [10]); 2-1 (claim [11]))

DeLM's native plugin, the piece closest to a Claude Code plugin, runs exactly two agents. Each works in a private copy-on-write copy of the project with its own Git administration, not in a git worktree: the plugin refuses linked worktrees. The agents coordinate through a shared board of tasks, contributions and check receipts. On delivery the plugin keeps the original Git index, merges compatible text edits the user made during the run, and reports conflicting edits without overwriting them. Its research harness, by contrast, uses an append-only shared context and a common task queue. Findings, failed approaches and completed subtasks are new entries with files attached by reference, a defect is a FAIL entry fixed by a later entry, and no verification step is named.

Evidence: Plugin at 3bd42f9: README.md:74 and the types [PathBuf; 2] and [Worker; 2]. src/workspace/git.rs uses --git-dir and --work-tree, and src/workspace/prepare.rs:215 rejects linked worktrees. Copies use macOS copy-on-write cloning only, with no fallback to a full copy. That claim was voted 3-0. Harness at 66617616: board/shared_context.py notes are only of types FACT and FAIL, and the prompts forbid separate verification roles. That claim was voted 2-1. A claim that DeLM's context maps directly onto notes and append-only refs was refuted 1-2. Re-expressing it in git is a design choice, not something either source shows. Git worktrees plus refs shared by compare-and-swap would replace the macOS-only copies and the HTTP board.

Sources: https://github.com/Decentralized-LM/delm-agent-plugin, https://github.com/yuzhenmao/DeLM

### 12. (high confidence, vote 3-0 (claims [13] and [15]))

No stored credential rules out DeLM's harness as it stands, and coordbench's drivers as they stand. DeLM's subscription (coding-plan) mode works only with the raw OAuth token from `claude setup-token`. The token is saved in a file only its owner can read, mounted read-only into each worker, and checked for the sk-ant-oat01- prefix. An expired token fails the run rather than falling back to API billing. DeLM's native plugin, by contrast, runs inside the host's own Claude Code session and leaves the account's login in its normal store. That is the model for a run with no stored credential: the agents are the user's own logged-in Claude Code, with sub-agents or worktree sessions, never claude -p fed a stored token.

Evidence: DeLM README lines 123-142, plus the code. delm.py requires --auth-file in coding-plan mode. runtime_support.validate_key_file requires st_mode & 0o077 == 0. worker_mounts and containers.py:199 mount the file read-only. claude_api.prepare_auth sets CLAUDE_CODE_OAUTH_TOKEN, and require_subscription demands authMethod=oauth_token. Claims [13] and [15] were merged. My own reading of /home/user/coordbench, not adversarially verified: coordbench/drivers.py:110-134 passes CLAUDE_CODE_OAUTH_TOKEN by name, with a COORDBENCH_CLAUDE_TOKEN alias for cloud sessions, and coordbench/README.md:462 says it comes from claude setup-token. coordbench's present arms therefore rely on a stored token too.

Sources: https://github.com/yuzhenmao/DeLM, https://arxiv.org/abs/2606.10662, https://github.com/Decentralized-LM/delm-agent-plugin

### 13. (low confidence, vote n/a (synthesis))

Proposed combination, a synthesis not tested by any source. Build a coordbench arm, for example git-native beside the existing coop, coop-git, sdlc, sdlc-watch, solo and team arms in coordbench/arms. It would have five parts. (a) Each agent works in a git worktree of one clone, so refs are shared locally. (b) Claims and the final state of a result are made with update-ref, using a create-if-absent old value and a --stdin transaction. (c) Each worker appends verified results to its own refs/notes/verified/<worker>. The note is keyed on a tree of the receipt's scope and carries the command and an environment digest, as DeLM's receipt does, and readers take the union. (d) Before landing, the coordinator runs merge-tree --write-tree on the pair. A receipt whose scope files are byte-identical in the merged tree is reused; any other is run again. (e) Everything runs in the user's logged-in Claude Code with no token. Score it as PARITY.md requires, then add reuse rate, reruns avoided, conflicts merge-tree caught before landing, and compare-and-swap retries.

Evidence: Assembled from the confirmed findings above and from the coordbench layout listed under /home/user/coordbench/coordbench (arms/, new_arm.py, drivers.py). Each piece is verified on its own, but no source measured the combination, and no verified claim reports a CooperBench result for DeLM or for git-native reuse. Rule (d) is an inference from DeLM's invalidation rule, which drops a receipt when a scoped file changes. A clean textual merge does not establish that the code is correct, so the merged tree still needs at least the checks whose scopes the merge touched.

Sources: https://github.com/mhagger/git-test, https://git-scm.com/docs/git-update-ref, https://www.kernel.org/pub/software/scm/git/docs/git-merge-tree.html, https://github.com/Decentralized-LM/delm-agent-plugin, https://arxiv.org/abs/1901.01930

## Caveats

Evidence quality. The proxy blocked arXiv and every mirror, so the DeLM paper (2606.10662) and the CALM paper were checked only through the authors' repositories or at the level of the abstract. usegitai.com and kernel.org were not fetched; Git AI was checked against its GitHub source, and merge-tree against git's own docs in the git/git repository. Git's CI was read through the GitHub mirror, not googlesource.

Split votes. Four of the claims used here passed 2-1, not unanimously: Git AI's sync loop, DeLM's no-merge behaviour (two claims), and DeLM's append-only context. Six claims were refuted and left out. They include the exit-status contract of merge-tree, the details of grite's push retry and lock policy, a direct mapping from DeLM to git, and the idea that the CALM paper defines a coordination metric.

Time sensitivity. Both DeLM repositories changed within the past two weeks (commits of 2026-10-07 and 2026-10-08). The plugin runs only two agents and only on macOS. Both could change soon. The git details depend on version:
- merge-tree --write-tree needs Git 2.38 or later.
- --batch-updates, added in Git 2.50, removes the all-or-none rule of update-ref --stdin.
- The all-zero null id has a different length in SHA-256 repositories.
- Git 2.43, which the verifiers used, lacks merge-tree --quiet.

Scope of reuse. A key built on content alone inherits the blind spots DeLM itself lists: coverage, undeclared dependencies, the environment, and changes between observations. A clean textual merge says nothing about whether the merged code works.

Unverified local reading. The statement that coordbench's drivers pass CLAUDE_CODE_OAUTH_TOKEN, from claude setup-token or the COORDBENCH_CLAUDE_TOKEN alias, comes from my own reading of /home/user/coordbench/coordbench/drivers.py lines 110-134 and coordbench/README.md line 462. It did not go through adversarial verification.

No measurement. No verified source reports a CooperBench measurement of any design here, so whether it helps on CooperBench is unknown.

Interpretation. The 'XBloom' artefact the question refers to was not available. I took it to mean the Bloom and CALM line of work, which only claim [16] covers.

## Open questions

- How often does reuse actually happen on CooperBench pairs? The two agents build different features from one base, so it is unclear how many receipt scopes stay byte-identical after merge-tree, and whether reuse saves enough time or tokens to show up next to CooperBench's own pass and merge measures.
- Can an arm with no stored credential run in coordbench's GitHub Actions at all? Actions has no interactive login, so such runs may be limited to a person's own machine or a cloud session using its own login. That would conflict with the project's plan to run CooperBench from a pool of organization secrets in the bench environment.
- What exactly should the verified-result key be? Options include a tree of the declared scope, plus the command, plus an environment digest. Undeclared dependencies could be caught by tracing the files a test reads or by the test runner's coverage, but nobody has tested either.
- Should results go into one ref per worker, which merges without coordination as union, or into one shared notes ref with fetch-merge-push retries? And how should either fit with Symphony's existing claim, the first push of claude/<N>, which is already a create-if-absent compare-and-swap on the remote?

## Refuted claims (left out above)

- (1-2) The exit status is a machine-checkable merge verdict: 0 for a clean merge, 1 for a conflicted one, anything else for an error. On a clean merge the only output is the OID of the resulting toplevel tree, which can be fed into commit-tree and update-ref to land the merge as pure plumbing.
- (0-3) Concurrent appends rely on the remote refusing non-fast-forward pushes, which works as compare-and-swap on the ref. A writer whose push is refused fetches the new head, re-parents the same chunk onto it and pushes again as a fast-forward only. History is never rewritten and no merge commits are made. The docs never name update-ref or an explicit old-value CAS.
- (0-3) grite's locks are advisory TTL leases, not mutual exclusion. A lock is a JSON payload (owner, nonce, expires_unix_ms, resource) committed to the lock ref. It is acquired when the ref is missing or expired, renewed by the owner with a new commit, and released with expiry=0. The default policy 'warn' carries on past a conflicting lock; only 'require' blocks, and only in the CLI or an optional pre-pu
- (1-2) [PROVENANCE: this session's proxy refused arxiv.org, its mirrors and the project site. Quotes are verbatim from the authors' own repository README (raw.githubusercontent.com/yuzhenmao/DeLM, main branch, which cites arXiv 2606.10662), fetched and checked byte for byte. A web-search summary of the abstract agrees.] DeLM coordinates through a task queue that agents claim from and an append-only share
- (0-3) The paper presents CALM as the exact answer to when coordination is needed. It treats avoiding coordination's cost while keeping consistent semantics as the main design aim. A harness that reuses verified results should therefore coordinate only the steps that are not monotonic, and it can be measured on whether it does (for example, coordination events per CooperBench pair).
- (0-3) Git's own CI decides whether a verified result can be reused by looking up the tree object ID, not the commit ID. A commit whose tree has already passed build and test is skipped, so a rebase that changes only commit messages does not trigger a rebuild. The lookup is a grep for the tree hash at the start of a line in the record file.

## Sources

- https://github.com/mhagger/git-test (primary)
- https://www.kernel.org/pub/software/scm/git/docs/git-merge-tree.html (primary)
- https://git-scm.com/docs/git-update-ref (primary)
- https://github.com/neul-labs/grite (primary)
- https://codex.danielvaughan.com/2026/04/25/codex-cli-merge-conflict-prevention-resolution-worktrees-clash/ (unreliable)
- https://usegitai.com/docs/get-started/how-git-ai-works (primary)
- https://github.com/Decentralized-LM/delm-agent-plugin (primary)
- https://github.com/yuzhenmao/DeLM (primary)
- https://arxiv.org/abs/2606.10662 (primary)
- https://pypi.org/project/decmas/ (blog)
- https://venturebeat.com/orchestration/stanfords-delm-cuts-multi-agent-task-costs-50-without-a-central-orchestrator (unreliable)
- https://arxiv.org/abs/1901.01930 (primary)
- https://code.googlesource.com/git/+/next/ci/lib.sh (primary)
- https://bazel.googlesource.com/bazel/+/HEAD/docs/remote/caching.mdx (primary)
- https://manpages.opensuse.org/Tumbleweed/git-test/git-test.1.en.html (primary)
- https://arxiv.org/html/2601.13295v1 (primary)
- https://www.mintlify.com/cooperbench/CooperBench/guides/evaluation (unreliable)
- https://www.mintlify.com/cooperbench/CooperBench/results/findings (unreliable)
- https://code.claude.com/docs/en/legal-and-compliance (primary)
- https://code.claude.com/docs/en/authentication (primary)
- https://code.claude.com/docs/en/worktrees.md (primary)
- https://code.claude.com/docs/en/costs (primary)
- https://arxiv.org/abs/2601.13295 (primary)
