# RAG over your code or your papers

## The short answer

**For your papers, RAG fits.** Literature is prose. Questions about it are conceptual, so grep often lacks the right words. Citations also have to be real. The open-source tools for this run locally and need no key.

**For code in coordbench, RAG is a weaker fit than the X-Blossom idea suggests.** It is still cheap to test.

How it lines up with X-Blossom:

- **Where it matches.** An index is a stored intermediate state that can be reused. It is built once, and each later query takes a short path into it instead of scanning again.
- **Where it doesn't.** The index stores the corpus. It does not store what the agents have already worked out.

How it lines up with our measured overlap:

- **The agents duplicate finding, not reading.** Each agent opens about half the files the other opens, but only 6-25% of printed lines overlap. So a shared map of where things are could save both agents the search. A shared cache of what they read could save at most the overlapping share, which is small.
- **RAG doesn't address the main multiplier.** Most of the 1.6-2.3x cost of two-agent arms comes from each agent carrying its whole transcript on every turn. Retrieval mainly helps each agent read less, and that helps solo just as much.
  - My guess (an inference, not measured) is that absolute tokens may fall while the coop/solo ratio barely moves.
  - Context management attacks the ratio more directly. Examples are search run in a subagent, so its output stays out of the main transcript, or hiding old tool output.
- **A cut made early compounds.** About 95% of each agent's input is prompt-cache reads of its own transcript. So anything kept out of the transcript early is not re-read on every later turn. The Complexity Trap paper measured tool observations at about 84% of an average SWE-agent turn's tokens.
- **The baseline inside Claude Code is strong.** For small and mid-size repositories the evidence leans against embedding RAG. Positive results come from large repositories and from structural indexes (symbol graphs, LSP, repo maps).

**Recommendation:**
- **Code:** test one light local index (Semble) as an extra arm. Keep a repo map built once at the base commit as the cheaper control that matches X-Blossom most closely.
- **Literature:** use mcp-local-rag on a PDF folder, or zotero-mcp if the papers live in Zotero.

Links to the three to try first, each checked on 2026-10-15: Semble, github.com/MinishLab/semble; mcp-local-rag, github.com/shinpr/mcp-local-rag; zotero-mcp, github.com/54yyyu/zotero-mcp.

Researched 2026-10-15 by a sweep of code-RAG tools, literature-RAG tools and the published evidence, each list checked again against its primary sources. Companion note: off-the-shelf.md in this folder (the X-Blossom moves without RAG).

## Code RAG: plug-and-play options that meet the constraints

All of these need no API key and run locally.

| Tool | Install (Claude Code) | Embeddings and store | Incremental re-index | Shared across agents | Licence | Activity |
|---|---|---|---|---|---|---|
| Built-in Grep/Glob/Read (the control) | default | none | no index, so nothing goes stale | no; each agent explores again | n/a | current |
| Claude Code LSP plugins (pyright-lsp, gopls-lsp, rust-analyzer-lsp) | `/plugin install pyright-lsp@claude-plugins-official`, with the language server on PATH | none (language server) | live | no, one per agent | Apache-2.0 at repository root; per-plugin licences not checked | first-party, current docs |
| Serena | `uv tool install -p 3.13 serena-agent`; `serena setup claude-code` | none (LSP, 40+ languages) | live | one server per agent; its memories are files, so sharing them is possible (inference) | GPL-3.0-or-later (its SolidLSP part MIT) | 2.0.0, 2026-10-13; 30.3k stars |
| Semble | `uv tool install semble`; `semble install --agent claude --type mcp --yes` | static Model2Vec potion-code-16M on CPU, plus BM25; `~/.cache/semble` | yes, by mtime; automatic in MCP mode | one shared cache directory is plausible; concurrent writers undocumented | MIT | 0.6.2, 2026-10-05; 6.2k stars |
| ck | `cargo install ck-search`; `claude mcp add ck-search -s user -- ck --serve` | FastEmbed bge-small (default) and others; `.ck/` or `$CK_INDEX_DIR` | yes, chunk-level delta (blake3) | read-only sharing plausible; index keyed by absolute path | MIT OR Apache-2.0 | 0.8.1, 2026-10-11; 1.75k stars |
| Codanna | install script, brew or cargo; `codanna serve --watch` in `.mcp.json` | local model of about 150 MB (download host not stated); `.codanna/` | yes, `serve --watch` | has an HTTP transport, so one server for both agents (inference) | Apache-2.0 | 0.16.0, 2026-08-29; 755 stars |
| claude-context, local mode | Milvus standalone in Docker Compose, plus Ollama `nomic-embed-text`; `claude mcp add claude-context -e EMBEDDING_PROVIDER=Ollama ... -- npx @zilliz/claude-context-mcp@<pinned>` | Ollama embeddings; Milvus | yes; a Merkle DAG of SHA-256 file hashes, synced every 5 minutes or on a trigger file | only if both agents index the same absolute path against one Milvus | MIT | 0.1.15, 2026-06-22; 12.6k stars |
| grepai | install script or brew; Ollama `nomic-embed-text`; `grepai watch` | Ollama or LM Studio; store not named | yes, file watcher | not documented | MIT | 1.9k stars; updated 2026-10-15 |
| Aider repo map, via RepoMapper | `pip install -r requirements.txt`; `python repomap.py . --map-tokens 2048`, or its MCP server | none; tree-sitter plus PageRank; diskcache keyed by mtime | yes, per file by mtime | a text artifact: compute once, give both agents the same copy (inference) | Aider Apache-2.0; RepoMapper MIT | Aider 0.86.2, 2026-02-12, 49.6k stars; RepoMapper 2025-12-08, 217 stars |
| Probe | `npx -y @probelabs/probe@latest mcp` (raw `mcp` mode only) | none; BM25/TF-IDF over the AST | no index | stateless; dedup only within one session | Apache-2.0 file (npm metadata says ISC) | 0.6.0-rc341, 2026-10-01; 727 stars |
| code-index-mcp | `uvx code-index-mcp` | none; tree-sitter, plus ripgrep or similar | file list watched; deep symbol index rebuilt by hand | one per agent | MIT | 2.17.1, 2026-07-27; about 1.0k stars |
| CodeNib | `pip install "codenib[graph,mcp]==0.2.4"`; `codenib codegraph init /abs/path` | none by default; BM25 plus a SCIP/LSP symbol graph | no; rerun init, which reuses unchanged views | one index per checkout | Apache-2.0 | beta 0.2.4; last commit 2026-10-12 |

**Caveats that apply to the whole table:**
- **Model downloads.** Every embedding tool fetches its model once. Semble's comes from Hugging Face; for ck and Codanna I assumed Hugging Face but did not verify it. Hugging Face, ollama.com and download.pytorch.org are blocked from this cloud. The LSP, tree-sitter, BM25 and repo-map tools work as they are.
- **What the task images carry.** I read `bench/cooperbench/overlay_task_image.sh`. It adds only claude, Node on Alpine images, and the redis client. So none of these servers, and none of the language servers, is in the task images.
- **LSP plugin limits.** The Claude Code docs say plugin language servers are not started in cloud sessions. They also say a server starts the first time Claude edits a matching file, so navigation before that first edit may not be available.
  - Whether an LSP call alone starts the server is unverified.
  - How this behaves under headless `claude -p` in coordbench's containers is unverified.
- **Long results.** Claude Code warns when an MCP result passes 10,000 tokens and caps it at 25,000 by default. A retriever that returns long passages adds to the transcript that each agent then carries on every turn.

## Literature RAG: options that meet the constraints

| Tool | Install | Embeddings and store | Incremental re-index | Shared across agents | Licence | Activity |
|---|---|---|---|---|---|---|
| mcp-local-rag | `claude mcp add local-rag --scope user --env BASE_DIR=/abs/papers -- npx -y mcp-local-rag` (Node 22+) | all-MiniLM-L6-v2 via Transformers.js (about 90 MB) with a keyword boost; LanceDB | yes, by hand: sync skips unchanged files and drops deleted ones | one writer; read-only queries can run alongside, so a pre-built index serves both agents | MIT | 0.22.1, 2026-10-14; 415 stars |
| QMD | `npm install -g @tobilu/qmd`; `qmd collection add`; `qmd embed`; then its plugin, or `qmd mcp --http --daemon` | embeddinggemma-300M, qwen3-reranker-0.6b and a 1.7B query-expansion model (about 2 GB of GGUF in all); SQLite FTS5 plus vectors | `qmd update`, then `qmd embed` | yes, one HTTP server (documented) | MIT | 2.8.3, 2026-08-16; 30.4k stars |
| Zotero MCP | `uv tool install "zotero-mcp-server[semantic]"`; `zotero-mcp setup`; `update-db --fulltext`; `install-skill` | all-MiniLM-L6-v2 or Ollama; ChromaDB | yes, from a sync watermark and each item's dateModified | one updater at a time (lock file); two readers unverified | MIT for the server; the in-Zotero plugin is AGPL-3.0 | 0.14.1, 2026-10-08; 5.4k stars |
| arxiv-mcp-server with the `[pro]` extra | `uv tool install "arxiv-mcp-server[pro]"` | sentence-transformers plus NumPy over the papers it downloaded | `reindex` tool | search is stateless; a shared paper store is unverified | Apache-2.0 | 0.8.2, 2026-10-08; 3.2k stars |
| Docling MCP (converter, used in front of the tools above) | `uvx --from 'docling-mcp[local]' docling-mcp-server` with `DOCLING_MCP_CONVERSION_MODE=local` | none; does layout, OCR and tables | n/a; keeps 10 documents in memory | n/a | MIT | 3.3.0, 2026-10-06; 772 stars |
| PaperQA2, local | `pip install paper-qa[local]`; `pqa ask` from Bash, with every model pointed at Ollama | sentence-transformers or Ollama embeddings; tantivy plus NumPy under `~/.pqa` | yes | unverified | Apache-2.0 | 2026.8.12; 9.4k stars |
| Qdrant MCP or Chroma MCP (building blocks only) | `uvx mcp-server-qdrant` with `QDRANT_LOCAL_PATH`; `uvx chroma-mcp --client-type persistent` | FastEmbed MiniLM; or Chroma's ONNX MiniLM, which downloads from S3 (reachable here) | none; documents are added explicitly | Qdrant's local mode locks out a second process, so share through one HTTP server; Chroma has an http mode | Apache-2.0 | Qdrant 0.8.1, 2025-12-10; Chroma 0.2.6, 2025-08-14 (stale) |

**Notes on the literature tools:**
- **Codanna and Semble can index text too** (Semble through `--content docs`), but only after the PDFs are converted.
- **mcp-local-rag:**
  - It reads PDFs directly and returns page numbers.
  - A stronger biomedical embedding must support mean pooling. Which ONNX models qualify is unverified.
- **QMD** is the closest local match to Anthropic's measured recipe (embeddings plus BM25 plus reranking). It reads Markdown only, so the PDFs must be converted first.
- **PaperQA2** has its own LLM inside. Under our rules that has to be a local model, so its published numbers would not carry over.

**Search servers for the published literature** (live search, not RAG over your own corpus):
- **pubmed-mcp-server**
  - Install: `claude mcp add pubmed -- npx -y @cyanheads/pubmed-mcp-server`. Needs Node 24+.
  - MeSH-aware, with PMC full text. An NCBI key is optional.
  - Apache-2.0; 2.10.22, released 2026-10-12.
- **paper-search-mcp**
  - Install: `uvx paper-search-mcp`.
  - Covers about two dozen sources; keys are optional. Keep its Sci-Hub step off.
  - MIT; 0.1.4; 2.8k stars.
- **Claude's PubMed connector (claude.ai)**
  - It works in your own Claude Code sessions logged in through /login.
  - It cannot back a coordbench arm. The Claude Code authentication docs say a `claude setup-token` credential "can't ... fetch claude.ai connectors", and coordbench runs on that token.
- **Network note:** NCBI, OpenAlex and Semantic Scholar are blocked from this cloud, so these search servers work from your machine, not from here.

## Ruled out, and why

**Code:**
- **semantic-code-mcp:** no Go support, needs Python 3.14 or later and torch, has no licence, and has 3 stars.
- **claude-context-local:**
  - Its model probably needs a Hugging Face login token, which is a credential.
  - It installs with curl piped to bash.
  - It is GPL, with no LICENSE file.
- **Sourcebot:**
  - Its MCP server moved to the enterprise licence in v5.0.0.
  - From v6.0.0, every deployment must be registered and activated.
  - It needs Postgres and Redis.
- **Sourcegraph MCP:** Enterprise only, with OAuth or access tokens.
- **DeepWiki:** hosted and limited to public repositories. It describes the current default branch, so it can leak the very feature a task asks for. Unverified (search snippets only).
- **Context7:** its setup creates an API key. It covers public library docs, not your code.
- **Greptile:** needs an API key and is hosted. Unverified.
- **SuperGrep:** its semantic tools need an account and upload your code; its keyword-only mode is close to grep.
- **Continue's @Codebase:** deprecated, and it only runs inside the IDE.
- **Augment Context Engine, Cursor, Cognition SWE-grep:** hosted, and need accounts.
- **Agentless and Agentless Lite:** need OpenAI and Voyage keys.
- **SweRank:** CC BY-NC licence, needs a GPU, and is not plug-and-play.

**Literature:**
- **paperqa-mcp-server:** requires `OPENAI_API_KEY`. It never exposes `agent_llm`, so it cannot be made fully local.
- **OpenScholar:** needs a Semantic Scholar key and heavy infrastructure, so it counts here as evidence, not a tool.
- **NotebookLM, and notebooklm-py:** hosted, and the unofficial route stores a Google session.
- **Claude Projects' RAG mode:** works in claude.ai only and cannot be reached from Claude Code.
- **Open WebUI and AnythingLLM:** heavy, and their bridges to Claude Code need a key issued by the instance. Both bridges are unverified. AnythingLLM's telemetry is on by default.
- **RAGFlow:** a heavy Docker stack, and each client needs a key from the instance.
- **Kotaemon and Khoj:** no interface Claude can call.
- **lancedb-mcp-server:** no licence. lance-mcp has been idle since February 2025.
- **openalex-mcp:** a 0-star, single-commit project, and OpenAlex's key policy is contested.
- **Mem0:** needs an LLM (OpenAI by default), which a Claude subscription cannot supply.

## What the evidence says

### Against embedding RAG for code inside an agent loop

- **Anthropic: no index, by design.**
  - In "Effective context engineering for AI agents" (anthropic.com/engineering), Claude Code drops CLAUDE.md in up front and finds code with glob and grep "just-in-time, effectively bypassing the issues of stale indexing".
  - The Agent SDK article (claude.com/resources/articles/building-agents-with-the-claude-agent-sdk) says semantic search is "usually faster than agentic search, but less accurate, more difficult to maintain, and less transparent".
  - Neither publishes numbers.
- **Boris Cherny's account (unverified, secondary only):** early Claude Code used RAG with a local vector database, and agentic search worked better. The original post could not be read.
- **Two other vendors dropped embeddings for code:**
  - Sourcegraph's Cody FAQ: "Cody does not support embeddings on Cody Enterprise because we have replaced them with Sourcegraph Search."
  - Continue deprecated @Codebase in favour of agent mode, rules files and MCP.
- **CodeGrep (arXiv 2608.05886, SWE-bench Verified, from the abstract):**
  - Handing retrieved files to a coding agent helps only above a precision threshold.
  - A BM25 seed (precision 0.375) made things worse, and a Jina seed (0.445) made no difference.
  - Only the RL-trained retriever (0.677) helped: 25.8% to 27.0% resolved, about 15% fewer rounds and 19% fewer tokens on resolved instances.
- **ETH, "Evaluating AGENTS.md" (arXiv 2602.11988, abstract):**
  - Repository context files do not generally improve task success and raise cost by over 20%.
  - Repository overviews do not help. This counts against putting a pre-built summary in every prompt.
  - Lulla et al. (arXiv 2601.20404) found the opposite on efficiency: median runtime -28.64% and output tokens -16.58%, with correctness not measured.
- **SWE-agent (NeurIPS 2024):** the agent resolved 18.00% of SWE-bench Lite. A BM25 RAG pipeline used instead of an agent scored 2.67% to 4.33%. The RAG figures are from secondary sources and unverified.
- **SWE-bench (original paper):** Claude 2 resolved 1.96% with BM25 retrieval against 4.80% with the correct files supplied.
- **Repoformer (ICML 2024):**
  - Many retrieved contexts are unhelpful or harmful.
  - Retrieving only when needed was up to 70% faster with no loss.
- **GrepRAG (ISSTA 2026):** grep commands written by the LLM did as well as graph-based retrieval for code completion. Its improved version gained 7.04-15.58% relative exact match. This is completion, not agents.
- **"Is Grep All You Need" (arXiv 2605.15184):**
  - The harness mattered more than the retrieval algorithm.
  - Grep won when results came back inline; with file-based results, vector search won in 5 of 10 pairings.
  - The corpus was conversation memory, not code.
- **Agent Retrieval Bench (arXiv 2607.24882):**
  - No retriever family dominates, and plain BM25 came last.
  - Recall@20: Qwen3-Embedding-8B 0.703, RepoMap 0.633, BM25 0.445.
  - RepoMap gave the most useful context per token at an 8K budget.
  - Agents' own logged searches missed every relevant file on 27-35% of samples.
- **ContextBench (arXiv 2602.05892):** agents explore far more context than they use, and better scaffolding brings only marginal gains in retrieval.

### For an index, mostly structural

- **SuperCoder, "Code Isn't Memory" (arXiv 2606.22417; supercoder-eval README).** Claude Opus 4.7, 3 seeds, 91 multi-file issues in Go, Java and Python:
  - Index on versus off: +7.9 points resolved (paired, n=80, p=0.003).
  - Turns: 28.3 versus 36.2.
  - Cost per solved task: $2.30 versus $2.84.
  - Cost per run was about the same: 1.15 versus 1.19 (p=0.73).
  - Against OpenCode (agentic grep): +6.0 points, p=0.087, not significant.
  - Caveats: the authors built the harness they test, and the index was called only about 1.4 times per run.
- **claude-context's own evaluation** (30 SWE-bench Verified instances, GPT-4o-mini, OpenAI embeddings):
  - Tokens fell from 73,373 to 44,449 (-39.4%), and tool calls from 8.3 to 5.3.
  - F1 stayed at 0.40, and only finding the files was measured, not fixing anything.
- **Cursor's semantic search study (Nov 2025; search snippets only, unverified):**
  - +12.5% average accuracy offline.
  - +0.3% code retention overall, rising to +2.6% in codebases of 1,000+ files.
  - This is the best large-sample evidence for embeddings, and it says size matters.
- **BLAgent (arXiv 2605.17965), same model:**
  - Retrieve-then-rerank was over 18x cheaper than LocAgent: about 26,180 prompt tokens, or $0.09, per bug with Claude.
  - LocAgent exhausted a $300 budget after 182 of 300 instances.
- **Semble's benchmark:**
  - NDCG@10: Semble 0.854, ck 0.642, grepai 0.561, probe 0.387, ripgrep 0.126.
  - Its relevance labels were written by Claude Sonnet 4.6.
  - Its "99% fewer tokens" is against a simulated grep-and-read-whole-files baseline, not an agent run.

### Shared memory between parallel agents (the X-Blossom question)

- **DeLM (arXiv 2606.10662, README tables):** 2 or 4 Claude Code agents share an append-only context and a task queue.
  - Terminal-Bench subset: 81.67 (single agent) versus 93.33 (two agents), with latency down from 130.75 to 64.77 minutes.
  - DeepSWE subset: 71.67 versus 88.33.
  - Token cost is not reported.
  - The agents all attack the same task rather than splitting features as CooperBench does.
  - This is the closest published setting to coordbench, and what it shares is findings, not a corpus index.
- **Anthropic, "How we built our multi-agent research system":**
  - Subagents "performed the exact same searches as other agents".
  - The fix was clearer task division plus work stored outside and passed back by reference.
  - It also says "most coding tasks involve fewer truly parallelizable tasks than research".
- **SWE Context Bench (arXiv 2602.08316):** a correctly selected, summarized prior context improves resolution and cuts cost. Unfiltered or wrongly chosen experience gives little or negative benefit.
- **Agentic Plan Caching (NeurIPS 2025):** reusing plan templates cut cost 50.31% and latency 27.28%.
- **Sleep-time compute (unverified):** precomputing over a context cut cost per query 2.5x when amortized over related queries.
- **KVCOMM:** shares KV caches across agents, but only on your own serving stack. It cannot apply under a Claude subscription.
- **The Complexity Trap (arXiv 2508.21433):** hiding old tool observations halves cost against the raw agent with no loss in solve rate. This targets the cost of each agent carrying its own transcript more directly than retrieval does.

### For literature

- **Anthropic, "Contextual Retrieval":**
  - Retrieval failure in the top 20 chunks fell from 5.7% to 3.7% with contextual embeddings (-35%), to 2.9% when contextual BM25 was added (-49%), and to 1.9% with reranking (-67%).
  - It also advises that a knowledge base under about 200,000 tokens (about 500 pages) can go whole into the prompt.
  - My inference: in Claude Code that budget is shared with the system prompt and transcript, and each agent carries it every turn, so the practical threshold is lower there.
- **OpenScholar (Nature 2026, as quoted by secondary sources):**
  - GPT-4o fabricated citations 78-90% of the time.
  - OpenScholar-8B beat PaperQA2 by 5.5% in correctness.
  - This is the main risk RAG addresses for the spine literature.
- **PaperQA2:** on the LitQA2 benchmark, 85.2% precision and 66.0% accuracy. These come from search snippets of the paper, and which LLM it used is unverified.
- **BM25 Wins at Scale (arXiv 2607.26497):**
  - At small corpus sizes, an agent browsing raw files matched BM25 (77.4 versus 74.7) while spending 39x more query tokens.
  - The curves cross near 10M corpus tokens.
  - An agent with BM25 as a tool scored best: 69.4.
  - The corpus is enterprise documents, so carrying this over to papers is an inference.
- **scholar-mcp's own small LitSearch test:** Hit@5 of 0.62 against 0.46 for BM25, using a hosted reranker.

### For measuring any of this

- **Bai et al. (arXiv 2604.22750; abstract only, unverified):**
  - Repeated runs of the same task differ by up to 30x in tokens.
  - Input tokens dominate cost.
  - So a small token effect needs several repeats per pair.

## The smallest test in coordbench

**How arms load extra servers.** I read this at coordbench commit 6de2157.
- **An arm is a plugin directory.** Its `arm.json` takes:
  - `"plugins"`: directories, git URLs or .zip URLs, loaded with `--plugin-dir` or `--plugin-url`.
  - `"mcp_servers"`: configs in `.mcp.json` form, handed to each agent with `--mcp-config`.
- **The servers run inside the agent's container.** The claude-cli driver writes that config to `/tmp/coord-mcp.json` inside each agent's container, so a stdio server starts there.
- **The solo arm is minimal.** Its directory holds only `plugin.json`, `arm.json` and `arm.py`.

**Steps:**

1. **Arms (no code).**
   - Copy `coordbench/arms/solo` to `solo-semble` and `coordbench/arms/coop` to `coop-semble`.
   - Change the name in `arm.json` and add one `"mcp_servers"` entry. Semble's README gives the command as `uvx --from "semble[mcp]" semble`.
   - I did not check whether the copied class's own `name` attribute also needs changing.
2. **Image (one script line, not arm code).**
   - Add one RUN line to `bench/cooperbench/overlay_task_image.sh`, as it already does for redis, to install `semble[mcp]`.
   - Copy the model into the image and set `SEMBLE_MODEL_NAME` to that local path, since Hugging Face is blocked here.
   - The same applies to any tool in the tables. The LSP route would also need pyright, gopls and rust-analyzer in the images.
3. **Runs.**
   - Run the 8-pair step with solo against solo-semble, and coop against coop-semble.
   - Do at least 3 repeats each, because of the token variance above.
4. **Readout (no new code).** Compare:
   - total tokens and uncached input
   - turns and Read calls
   - files opened by both agents and printed-line overlap
   - pass rate and the coop/solo ratio
   - Write the predictions down before running:
     - absolute tokens fall, if anything
     - the ratio stays about the same
     - the clearest gains are on the largest repository (typst)
   - Whether coordbench's existing metrics count calls to a non-hub MCP server separately was not checked. Counting them may need a few lines.

**The shared-index variant (the X-Blossom version proper).**
- **Setup.** Build one index at the base commit on the host and serve it to both agents over HTTP, the same way the hub is reached: a `"type": "http"` entry in `"mcp_servers"`.
- **Which tools can serve it.** QMD and Codanna document an HTTP mode. Semble's HTTP mode is unverified.
- **Limitation.** The index describes the base commit, not each agent's edits. That is fine for locating code, since the features being added are new.

**The session route.**
- **How it works.** `run.py prepare` provisions the pair, the session runs the agents as its own subagents, and `run.py finish` scores. Add the server to the session once with `claude mcp add`. The server then runs on the host and needs no image change.
- **Assumption.** That subagents inherit the session's MCP servers is from memory and unverified.
- **Costs:**
  - isolation is "advisory"
  - output tokens are recorded only as a lower bound
  - in a cloud session, Hugging Face is still blocked and plugin language servers do not start
- **Better to run it from your own machine.**

**The cheapest X-Blossom control.** A repo map computed once at the base commit and put into both agents' prompts.
- It is deterministic and needs no model.
- It needs a short prompt override in `arm.py`, so it is a small amount of code.

**A literature test, outside coordbench.**
- Write 30-50 questions on the spine corpus the LitQA2 way. Each answer should be in a paper's main text, with a key passage and distractors.
- Compare grep over pdftotext output against mcp-local-rag and QMD.
- Score precision, accuracy, abstention and whether each citation is correct.
- Nothing needs coding beyond the question file.

## Not verified

**About the tools and products:**
- Boris Cherny's original words.
- Cursor's numbers.
- The DeepWiki and Greptile details.
- grepai's 80% and 97% token claims.
- The model download hosts for ck and Codanna.
- Which biomedical ONNX models fit mcp-local-rag.

**About how the setup would behave:**
- Whether two agents on one account share a prompt-cache prefix.
- Whether an LSP call starts a plugin language server before the first edit, and how that behaves under headless `claude -p` in a container.
- That subagents inherit the session's MCP servers.
- That Semble can serve over HTTP.
- Whether two processes can read one Chroma persistent directory, one zotero-mcp store, or one QMD SQLite index at once.

**About the evidence:**
- The SWE-agent RAG figures.
- The sleep-time compute and Bai et al. figures.
- The PaperQA2 paper's own text.
- OpenScholar's numbers, which come from secondary quotes of the Nature paper.
