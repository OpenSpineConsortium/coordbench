# X-Blossom's parallelism applied to LLM agents

Written 2026-10-15 for the question: what would this kind of parallelism
look like for LLMs that iterate but reuse intermediate states to avoid
repeating work. Source: Fan, Lee and Zhang, "X-Blossom", PVLDB 18(10),
2025 (the attached XBloom.pdf), read against coordbench's step of 8 pairs.

## The three moves and their analogues

| X-Blossom | Inference level | Agent level |
|---|---|---|
| No recursion: a blossom is never contracted and lifted; its odd nodes become even and their even-length path back to the last even node goes into a path table, so the graph stays static | The KV cache is the path table: a shared prefix is computed once and reused by every sample or agent that starts with it (prefix caching, radix-tree caches) | Do not summarize and re-expand (a subagent's summary is a contraction, re-reading the files it summarized is the lifting). Keep the repository at a commit as the static graph and write small checked facts, keyed by the blob hash of the files they describe, into a shared table; a fact holds until one of those hashes changes |
| Many vertex-disjoint augmenting paths per search: the forest is built once and every disjoint path it holds is taken | Batched or parallel decoding of independent continuations over one prefix | Split the work into units whose file sets are disjoint, run them all in one round, land them together; CooperBench's curse is two paths sharing a vertex, two agents editing the same function |
| Lock-free claims: an atomic compare-and-swap puts each alternating tree in at most one path (Theorem 3); a loser reverts and its path is found in a later round | Not needed: a cache entry is immutable | Symphony's claim is this CAS: the first push of claude/N wins, GitHub refuses the second, the loser takes other work, and an idle claim goes back to the queue for the next round |
| Store the short path, not the tree (3.3x faster than keeping the trees, in the paper) | Cache the prefix, not the sampled continuations | Hand off a certificate (commit, diff, the tests that pass, a few lines of intent), never a transcript; record only valid resume points, as X-Blossom records only even-length paths |

## What the runs say about repeated work

overlap.py in this folder reads the agents' session transcripts. The agents
read the repository through docker exec (cat, sed -n, grep), so files are
taken from those commands and line ranges from sed -n A,Bp. Over 34
two-agent runs (the step's tasks under coop, sdlc, coop-git and team):

| Arm | Runs | Files both agents opened / files either opened (median) | Printed line ranges both read / either read | Input tokens served from the prompt cache (median) |
|---|---|---|---|---|
| coop | 8 | 0.62 | 194 / 1367 = 0.14 | 0.95 |
| sdlc | 9 | 0.50 | 461 / 1828 = 0.25 | 0.95 |
| coop-git | 8 | 0.54 | 71 / 1096 = 0.06 | 0.96 |
| team | 9 | 0.50 | 274 / 1711 = 0.16 | 0.96 |

So the inference-level reuse already works (about 95 percent of every
agent's input is a cache read), and the two agents repeat each other's
reading at the file level about half the time but at the line level much
less. Most of the extra tokens of a two-agent run (1.6 to 2.3 times solo's
per pair) come from each agent carrying its own whole context through every
turn, not from reading the repository twice. The agent-level path table and
certificate handoffs aim at that.

## How it could be tested

A sixth arm on the same pairs: sdlc plus a shared fact table (append only,
keyed by blob hash, each fact with the command that checked it) and handoffs
by certificate. Prediction: tokens per pair move toward solo's while sdlc's
zero conflicts and parallel wall time stay. Not started; it waits on the
owner's word, since every run spends the account's usage.
