# Off-the-shelf options for the X-Blossom approach

Checked 2026-10-15 by a sweep of Claude Code's own features, open-source tools and papers, each list checked again against its primary sources; the key repositories and the 2.1.86 changelog line were spot-checked once more before this was posted. "Verified" below means the source, or for git the behaviour, was read or tested that day. Anything marked "not verified" comes from a search summary or from memory.

## Short answer

Partly. No one ships the whole X-Blossom set: a shared table of checked facts keyed to file content, disjoint rounds, compare-and-swap claims and certificate handoffs. You can still test all four moves without bespoke code:

- **Claims and the disjoint split need no new tool.** The team arm's `coop-task-claim` is already a real check-and-set: the loser gets exit 2 and is told to pick another task. The split is a prompt change to the lead's PLAN.md.
- **No product keys facts to file content.** Nothing on the market stores facts under a file's blob hash and drops them when the file changes. The nearest plug-in is git itself: facts filed under `git hash-object <path>` go stale by construction once the file is edited. That was tested across two clones with git notes. The plain-file form of it, on the shared volume, needs only a prompt.
- **Certificates are a prompt convention.** Have each agent put the commit sha, `git diff --stat`, the test command and its exit code in its done note.

What I would try first is one prompt-only arm that subclasses the team arm and makes all four changes, run with the claude-cli driver against team and solo. It needs no install, no dependency and no credential. If you want a maintained third-party tool instead of a convention, Claim Plane's MCP server is the one to try. It is Apache-2.0, needs no API key, and is the only tool already studied on CooperBench. But it covers claims and admission, not facts, and it needs Python inside each task container.

Expect the fact table to save only a little:

- Since version 2.1.86, Claude Code skips an agent's own re-reads of unchanged files (verified from the changelog).
- About 95% of your input tokens are each agent re-reading its own transcript, and no tool here shrinks that.
- With only 6-25% of printed lines overlapping, the table can only save an agent's first read of a file its peer has already read.
- In claude-mem's own evaluation, replacing a file read with stored notes saved 32-35% on a 49 KB file but cost up to 20% more on a 19 KB file, because each blocked read adds turns (verified, self-reported).

## Plug-and-play options that meet the constraints

None of these needs an API key or a new credential, and all run locally on the subscription. "claude-cli only" means the session driver cannot load it: SessionDriver.start ignores an arm's plugins and MCP servers (coordbench/drivers.py:907-918).

### A. Nothing to install (git, coordbench, Claude Code)

| Option | Move covered | How it is turned on | Facts stale on file change? | Licence | Activity | coordbench change |
| --- | --- | --- | --- | --- | --- | --- |
| Blob-hash fact files on the shared volume (a convention) | fact table | Prompt: before opening a file, `cat /workspace/shared/facts/$(git hash-object <path>)`; after checking a fact, append one line to that file | yes, by content: an edited file has a new hash, and an unchanged file has the same hash in both agents' copies | n/a | n/a | prompt (a team subclass); the volume is already there. Not run yet. |
| git notes on blob objects, with a lease push as the claim | fact table, CAS claims, certificates | Prompt: `git notes --ref=facts add -m '<fact>' $(git hash-object <path>)`. Sync with fetch, `git notes merge -s cat_sort_uniq`, push, and retry on reject. Claim with `git push --force-with-lease=refs/claims/<unit>: team HEAD:refs/claims/<unit>` | yes, by content (tested across two clones; an edited file's new blob had no note) | GPL-2.0 (git; nothing copied) | git core; tested with git 2.43 | prompt, in a coop-git or sdlc subclass (both have the remote `team`) or with `--option git_remote=true`. A plain `git push` of a claim is not a CAS: the second agent saw "Everything up-to-date" and would think it won. Only the lease form refused it ("stale info"). |
| coordbench team arm's `coop-task-claim` | CAS claims | Prompt: the lead creates open tasks with `coop-task-create "<title>"`, without `--assign`, and members race to claim them | n/a | coordbench's own | in the tree | prompt (a team subclass; the team prompt is CooperBench's word for word, so leave it in place) |
| Certificate in the done note, in git-appraise's `ci` note format | certificate handoff | Prompt: one JSON line per result (timestamp, agent, status, plus the commit sha and test command) in the task note or in `refs/notes/devtools/ci` | partial: a result annotates one commit, so a new commit has none until it is re-tested | Apache-2.0 (format only) | git-appraise dormant since 2023-08-11; take the format, not the binary | prompt |
| Workflow prefix stagger, `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS` 5000 vs 0 | shared prompt-cache head | Host environment variable for the session driver | n/a | Claude Code (not open source) | added in 2.1.229 | none. Pin one model for both agents, since agents on different models share no prefix. |
| Claude Code hooks: a TaskCompleted test gate | certificate handoff | The arm ships `hooks/hooks.json`. A TaskCompleted command runs the tests and exits 2 on failure, which keeps the task open and feeds stderr back to the model. It fires on any TaskUpdate completion, not only in agent teams. | n/a | Claude Code | current docs | option plus a few driver lines. Claude Code's Task tools are off by default on newer models, and an arm cannot turn them on (`CLAUDE_CODE_ENABLE_TODO_TOOLS=1` or `--allowedTools TaskCreate`). claude-cli only. |
| Shared Claude Code task list, `CLAUDE_CODE_TASK_LIST_ID` | CAS claims, dependency rounds | The same id in both agents, with /tmp/claude-cfg/tasks/<id> mounted from the shared volume | no | Claude Code | current docs | new code: a few lines in drivers.py for the variables and the mount. Not verified: whether its file locking holds between two containers on a bind mount. |
| Fork one scout session into both agents: `claude -p --resume <scout.jsonl> --fork-session` | shared, already-paid prefix; aimed at the files both agents open | One scout run reads the files both features touch, then each agent resumes a fork of it | no (the scout's reads are a snapshot) | Claude Code | current docs | new code: a driver option of a few lines. Charge the scout's tokens to the arm. That the forks read the scout's cache is inferred from the documented rules, not measured. |

### B. Third-party tools

| Option | Move covered | How it is turned on | Facts stale on file change? | Licence | Activity | coordbench change |
| --- | --- | --- | --- | --- | --- | --- |
| Claim Plane MCP server (`claim-plane-mcp`) | CAS claims; disjoint rounds (admission against declared file sets); partial certificates | arm.json `mcp_servers`: `claim-plane-mcp --db /workspace/shared/plane.db`. That command is my construction; the project documents only a Codex connector. | partial: scope growth is re-admitted against the live repository; it is not a fact store | Apache-2.0 | repository 0.56.3, last commit 2026-09-17; PyPI has only 0.37.31 | option (arm.json), plus the package in each container. python3 is not guaranteed in task images (coordbench/run.py:2683-2690). claude-cli only. Leave its HMAC-signed observation tools unused, since they need a local secret. |
| bettermemory | fact table | MCP server or plugin, with `BETTERMEMORY_DIR` set to a directory on /workspace/shared | partial: flags a fact whose cited paths, symbols or literals changed, or that has commits since; not a content hash, so an unrelated commit can flag it | MIT | last commit 2026-10-15; PyPI 8.0.0 | option, plus the Python package in each container. It uses fcntl locks and describes many agents on one store, but a store shared across two containers was not tested. claude-cli only. |
| MCP reference memory server (`@modelcontextprotocol/server-memory`) | fact table, keyed only by a naming convention such as `<path>@<blob sha>` | `MEMORY_FILE_PATH=/workspace/shared/facts.jsonl` | no, except by that convention | MIT, moving to Apache-2.0 | npm 1.0.0, 2026-10-11, which added file locking between processes | option (arm.json). It needs Node, which only the Alpine overlay images carry (bench/cooperbench/overlay_task_image.sh:49-67). claude-cli only. |
| ATM agent pack for Claude Code (eaglhuang/AI-Atomic-Framework) | CAS claims (a write broker with a content-hash CAS); certificates (evidence bundles) | An npm install, then slash commands under .claude/commands that call `node atm.mjs` | partial: writes are checked against a content hash; there is no fact store | Apache-2.0 | last commit 2026-10-15; npm 0.1.20; 3 stars | heavy: Node 20+ in each container, an atom map of the repository and a governance workflow; no comparative results. Not a first try. |

Measuring aids (no coordbench change):

- **session-report** (Anthropic plugin, Apache-2.0). Run `node analyze-sessions.mjs --dir <run results> --json` on the host to get the cache-hit rate, cache breaks and tokens per subagent. How it labels coordbench's flat `<agent>_session.jsonl` layout is untested.
- **`--exclude-dynamic-system-prompt-sections`**, a control flag. Both claude-cli agents already share one config directory and one working directory (coordbench/drivers.py:100, 678), so the flag should change nothing. A difference would expose a hidden difference in the prefix.

## Ruled out, and why

Needs an API key, a hosted service or a new credential:

- **Mem0.** The Claude Code plugin needs a Mem0 Platform key, and the default model and embeddings are OpenAI's.
- **zilliztech/claude-context.** It needs OpenAI embeddings plus Milvus by default. An all-local setup with Ollama and Milvus is possible but heavy.
- **Graphiti/Zep.** It needs an LLM key and a graph database (FalkorDB, Neo4j or Neptune).
- **Letta.** It needs Letta Cloud or keys, and it is its own harness, not Claude Code.
- **STORM's code.** It needs LLM_API_KEY inside OpenHands, and its repository has no licence file.
- **Co-Coder's code.** It needs an LLM provider key, and its file graph comes from an LLM-written blueprint.
- **CodeCRDT.** It needs Amazon Bedrock.
- **MetaGPT, AutoGen/Magentic-One, LLMCompiler, Graph of Thoughts, Skeleton-of-Thought and Buffer of Thoughts.** All run on model API keys. Copy Magentic-One's ledger format instead.
- **DeLM's research harness.** It wants the output of `claude setup-token` saved as a file on disk, which is a stored credential. The DeLM plugin is macOS only, with no fallback for copy-on-write cloning.
- **claude-mem.**
  - Its default install signs in to cmem.ai for a memory key, and `--provider claude` still contacts cmem.ai once.
  - PostHog analytics are on by default.
  - The observer model's calls fall outside the agents' transcripts, so token counts would be too low.
  - Staleness is by mtime.
- **ccpm.** It needs `gh auth login` and GitHub Issues.

Licence or fit:

- **MCP Agent Mail.** Its MIT licence carries a rider that grants no rights to Anthropic or anyone acting on its behalf, and that counts benchmarking and use in an evaluation harness as use. Do not use it unless the owner reads the licence and decides otherwise.
- **Basic Memory.** It is AGPL and has no tie between a note and source content.
- **Serena.** It is GPL-3.0-or-later; running it is fine but vendoring it is not. It is a "read less" tool with no staleness check on its memories.
- **Verified Repo Memory.** It keys facts to a snippet's sha256, which is the right idea, but it cannot be shared live. Each server loads its store once and rewrites the whole file on every write, so the two agents erase each other's facts.
- **Octopus Blackboard.** Its claims are advisory: both conflicting claims are recorded, so it is not a CAS.
- **Beads.** Its embedded Dolt allows one writer. A race-free claim needs a dolt sql-server for both containers.
- **codebase-memory-mcp.** It is a "read less" index with a curl-pipe-bash installer and a daemon. Its savings are self-reported. Its preprint, which I did not read, reports a quality cost according to its README and a search summary.
- **Ruflo.** It changes routing, memory and prompts at once, so no result could be attributed to one idea.
- **claude-md-management and subagent `memory: project`.** Both write files into the repository, which then land in the scored patch. A CLAUDE.md edited mid-session does not apply until a restart.

Claude Code features that do not fit coordbench's shape:

- **Agent teams.** Claude does not spawn teammates under `-p`, which the claude-cli driver uses.
- **Cross-session messaging.** A session in a container and one on the host cannot reach each other.
- **Checkpointing.** It does not track files changed by Bash.

Open weights and your own GPU only, so nothing runs on hosted Claude: Hogwild! Inference, KVCOMM, KVFlow (not verified), LMCache, SGLang RadixAttention, Multiverse, APR and Parrot. Anthropic's prompt cache is the hosted counterpart, and coordbench already reads about 95% of input from it.

## Reading list

Each entry gives the code link and what it shows. Figures are verified from the repository's README or the post unless marked otherwise.

- **CooperBench**, arXiv 2601.13295, github.com/cooperbench/CooperBench (MIT per the README). Two cooperating agents reach about 25% success, about 50% below one agent doing both features. Communication takes up to 20% of the budget and cuts merge conflicts without raising success. Failures split 42% expectation, 26% communication and 32% commitment.
- **Claim Plane**, arXiv 2607.21909 and 2608.00947, github.com/SkeinRank/claim-plane (Apache-2.0). This is pre-write admission studied on CooperBench, with 30 pairs, 3 seeds and 4 arms.
  - Pair pass was 23.3% unconstrained, 50.0% with static admission (+26.7 pp, 95% CI 9.6 to 60.0) and 22.2% with dynamic admission. Integration success rose from 65.6% to 96.7%.
  - Static admission serialized 96.7% of executions, so the gain came mostly from not running in parallel. No cost saving is claimed.
  - Caveats: the planner had context localized from the gold patches, the calls ran in sequence, and the coders ran through OpenRouter, not Claude Code.
- **DeLM**, arXiv 2606.10662, github.com/yuzhenmao/DeLM and github.com/Decentralized-LM/delm-agent-plugin (MIT). Its append-only shared context and check receipts are the closest published version of "a fact valid until its inputs change". With Opus 5.5 in Claude Code, on 10 tasks with 3 runs:
  - DeepSWE: 71.67% to 88.33% at n=2, 1.50x faster.
  - Terminal-Bench: 81.67% to 93.33%, 2.02x faster.
  - These are latency figures; no token totals are given.
- **STORM**, arXiv 2605.20563, github.com/dreamyang-liu/STORM (no licence file). The mechanism was verified in its code: per-file version counters and md5 hashes, a write that is a CAS on the version, and push invalidation of peers' cached views. The +18.7 on Commit0-Lite comes from a search summary and is not verified.
- **Co-Coder**, arXiv 2606.00953, github.com/Flitternie/CoCoder (Apache-2.0). It partitions work by isolating hub files and detecting communities. With gpt-5-mini:
  - DevEval: 56.8% to 68.1%, 1.81x faster, 28% lower API cost.
  - CodeProjectEval: 20.1% to 34.1%, 2.10x faster, 35% lower cost.
  - It generates whole repositories, so the results transfer weakly to feature pairs.
- **CodeCRDT**, arXiv 2510.18893, github.com/spugachev/CodeCRDT (MIT). It ran lock-free claims with Claude Sonnet 4.5 over 600 trials: up to 21.1% faster on decomposable tasks, up to 39.4% slower on coupled ones, and 5-10% semantic conflicts after convergence.
- **ATM**, arXiv 2607.00041, github.com/eaglhuang/AI-Atomic-Framework (Apache-2.0). It is a CAS write broker with evidence-first handoffs. It reports no comparative results.
- **Anthropic, "How we built our multi-agent research system"** (2025-06-13). Subagents pass lightweight references, not transcripts. Multi-agent runs used about 15x the tokens of chat, and token use explained 80% of the variance. It notes that "most coding tasks involve fewer truly parallelizable tasks than research". No code.
- **Anthropic, "Building a C compiler with a team of parallel Claudes"** (Carlini, 2026-02-05); the compiler is at github.com/anthropics/claudes-c-compiler (CC0). Lock files in current_tasks/ with git as the arbiter, at about 2,000 sessions and 2 billion input tokens, API-billed. The harness exists only as a snippet in the post.
- **Anthropic, "Effective context engineering for AI agents"** (2025-09-29). It covers structured notes and just-in-time retrieval by lightweight identifiers. No figures.
- **claude-mem's read-gate evaluation**, in github.com/thedotmack/claude-mem (Apache-2.0, self-reported). It saved 35% and 32% of tokens on a 49 KB file, but saved 6% or cost 20% more on a 19 KB file.
- **zilliztech/claude-context's evaluation** (MIT). On 30 SWE-bench Verified instances with GPT-4o-mini, it used 39.4% fewer tokens and 36.3% fewer tool calls at equal F1. The tool is ruled out above; the number is a reference point.
- **Formats to copy:**
  - Magentic-One's task ledger (github.com/microsoft/autogen, MIT): a four-section fact sheet of given or verified facts, facts to look up, facts to derive and educated guesses.
  - git-appraise (github.com/google/git-appraise, Apache-2.0): one JSON line per note, merged with cat_sort_uniq, and a ci schema for test results.
- **MAST**, arXiv 2503.13657, github.com/multi-agent-systems-failure-taxonomy/MAST (no licence file). A failure taxonomy, plus more than 1,000 annotated multi-agent traces (the MAD dataset).
- **Not verified, from search summaries only:**
  - "Passes Alone, Fails Together" (arXiv 2609.25396): a message describing the peer's finished change recovered 82% of interfering pairs. No code was found.
  - "How Much Coordination Gain Is Real?" (arXiv 2606.20695): a single-seed +18 pp gain did not reproduce. Run paired seeds before claiming a gain.
  - "Towards a Science of Scaling Agent Systems" (arXiv 2512.08296): multi-agent variants lost 39-70% on sequential tasks.
  - LATTE (arXiv 2605.06320) and "The Working Set of a Coding Agent" (arXiv 2608.16630).
  - codebase-memory-mcp's preprint (arXiv 2603.27277): 10x fewer tokens, at 83% against 92% answer quality.

## Smallest test

One new arm with prompt changes only, run locally on the claude-cli driver. It needs no new dependency, so no issue first, and no pull request while it lives outside coordbench/arms/.

1. **Measure what is shared today, with no code.** Both claude-cli agents run with the same CLAUDE_CONFIG_DIR (coordbench/drivers.py:100, 678) and one token. Read the later agent's first-turn `cache_read_input_tokens` from its `<agent>_session.jsonl` to see whether the pair already shares the prompt-cache head. session-report gives the per-run cache-hit rate.
2. **Make the arm.** Run `python3 coordbench/new_arm.py xblossom <scratch dir>` (coordbench/README.md:236-264), then change its arm.py to subclass the team arm's class. Load the built-in module the way coop-git loads coop (coordbench/arms/coop-git/arm.py:30-35, 113). The loader accepts only an Arm subclass (coordbench/arm.py:493-509). About 10-15 lines, mostly the prompt text.
3. **Change four things in the prompt:**
   - **Facts:** before opening a file, read `/workspace/shared/facts/$(git hash-object <path>)`; after checking a fact, append one line there. The team arm already needs the shared volume (coordbench/arms/team/arm.py:802), mounted at /workspace/shared in every container (coordbench/run.py:2648-2649). That directory is outside /workspace/repo, so facts never reach the scored patches (coordbench/run.py:3241-3253).
   - **Disjoint rounds:** the lead's PLAN.md (coordbench/arms/team/arm.py:614-618) gives each task a file set, with no file in two sets. Do not derive the split from the gold patches.
   - **CAS claims:** the lead creates open tasks with `coop-task-create "<title>"` and no `--assign` (coordbench/arms/team/arm.py:580-581, 619-620). Members race with `coop-task-claim`, which is a check-and-set under the run's lock (coordbench/arms/team/arm.py:212-223); a loser gets exit 2 and picks another task (582, 680).
   - **Certificates:** the done note carries the commit sha, `git diff --stat`, the test command and its exit code, not only the patch path (coordbench/arms/team/arm.py:686-693).
4. **Run it locally:** `python3 coordbench/run.py run --arm <scratch dir>/xblossom --driver claude-cli ...`, with the same pairs and model as team and solo, and at least two seeds per arm. Keep it local for three reasons:
   - Actions accepts only arms under coordbench/arms/ (bench/cooperbench/actions/plan.py:205-213).
   - pair.sh passes no `--option` (bench/cooperbench/actions/pair.sh:48-51).
   - The session route passes no `--option` (bench/cooperbench/session/lane.py:91-95) and ignores arm plugins and MCP servers.
4b. **Or run it here, the way the step runs.** The session route hands run.py prepare the queue item's arm, and `--arm` takes an arm's directory (bench/cooperbench/session/lane.py:92, coordbench/run.py:4110), so a prompt-only arm runs in this cloud session with no Claude token, as the team arm did in the step. Its hub tools (coop-task-claim and the rest) reach the agents as shell commands there; only third-party MCP servers and `--option` values do not.
5. **Optional: the one piece of code.** To count fact hits and misses as events, rather than reading them off the transcripts, add `post_fact(path, blob, text)` and `read_facts(path, blob)` as hub tools on the pattern of the board example (coordbench/examples/board/arm.py:28-55). That is about 40 lines, and it works on both drivers.

Two corrections to keep in mind:

- **sdlc's claim is not a push.** In the sdlc arm the claim is an in-memory check-and-set under the run's lock, not a first push (coordbench/arms/sdlc/arm.py:11-15). Its units are whole features, and an agent claims another's only with `--option open_queue=true` (coordbench/arms/sdlc/arm.py:16, 525).
- **sdlc runs no tests at its gate** (`required_check=""`, coordbench/arms/sdlc/arm.py:99). A test-carrying certificate is therefore a prompt convention today. A gate that runs the tests needs work in the runner and in symphony-protocol.
