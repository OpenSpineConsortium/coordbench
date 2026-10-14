# Every statistic, and where the report gives it

What CooperBench's paper, its blog posts and its later ablation report
measure, and the section of bench/cooperbench/report.py (the report job's
report.md) that gives the same figure for a run here. PARITY.md is the
row-by-row account of how each is computed; this file is the short map
for a reader with a report in one hand and a paper in the other. "Judge"
marks a measure that needs the model-judged pass (coordbench/judge.py),
which no workflow run makes.

## The paper (arXiv 2601.13295)

| The paper's figure | Where the paper gives it | Report section |
| --- | --- | --- |
| Pass rate per setting and model, k/652 | Fig 4 left, Table 5 | Pass rates (with the paper's rows printed under the table) |
| The three ways CooperBench itself counts a rate | summary.json, console, website | CooperBench's three rates |
| Coordination gap, solo minus coop, absolute and relative | Fig 4, the abstract's "30%" | Coordination gap |
| Paired significance between settings | none in the paper | Paired comparisons (exact McNemar) |
| Naive-merge conflict rate; semantic failures (clean merge, tests fail) | Fig 5b, Sec 5 | Naive-merge conflicts |
| Merge cascade: naive, union, learned resolver | Table 6 | Merge cascade (Table 6); the resolver's step needs a model server and says so |
| Turns (Fig 6a) and the share of events spent on messages (Fig 5c) | Fig 6a, Fig 5c, Sec 5 | Communication (turns, speaker runs, overhead: sends / tool calls) |
| Speech acts: Plan, Question, Answer, Update, Ack | Fig 5c | Speech acts, judge; a keyword baseline in the heuristics |
| Concrete references, line numbers and file paths | Sec 5 | Concrete references (Mann-Whitney between conflict and no-conflict pairs) |
| Communication errors: unresponsiveness, hallucination, repetition | Sec 5, Fig 6, App F | Failure modes, judge |
| Failure symptoms by level and family | Sec 6.1, Table 1, App G | Failure modes, judge, with the free cross-checks beside |
| Emergent behaviours: role and resource division, negotiation | Sec 6.4 | Failure modes, judge (coordbench's labels, kept apart) |
| Relative difficulty curves, AUC, retention | App C | Difficulty (App C) |
| Dataset tables, amendments since the paper, gold conflicts | Tables 3 and 4 | Dataset (with the survey's gradeability) |
| Cost per pair and wall time | Sec 4 | Cost and time |

## The blog posts

| Figure | Post | Report section |
| --- | --- | --- |
| Solo against coop, and 2, 3 and 4 agents (68.6, 46.5, 30.0%) | The Curse of Coordination | Pass rates and Coordination gap; the 3- and 4-agent sets are not run (PARITY row 15) |
| Up to 20% of steps spent on messages | The Curse of Coordination | Communication |
| Expectation, commitment and communication failures (63% expectation) | The Curse of Coordination | Not a report section: `annotate.py causes` draws failed pairs and writes review packets for a person to label, and `annotate.py tally` counts the labels with Wilson intervals |
| Coop with git against coop without, and solo (27.76 / 26.23 / 48.60 for Gemini 3 Flash) | The Curious Case of Miscoordination | Git ablation (its columns, the blog's rows printed under the table) |
| The five git failure patterns (blame, sagas, and the rest) | The Curious Case of Miscoordination | Failure modes, judge (emergent: the git-setting labels) |

## The team-harness ablation report

| Figure | Report section |
| --- | --- |
| Pass rate of solo, coop, coop+git, team and the team minus each switch | Pass rates, one row per arm; the switches (minus scratchpad, task list, MCP, auto-refresh, protocol) are not run (PARITY row 51) |
| Median task time | Cost and time (minutes per pair, median) |
| Evaluation policy: identical patches, naive merge, lead alone | Pass route (N, identical, L) |
| The team's metrics: tasks total and done, unowned at the end, time to first claim, claims and updates per agent | Recorded in each team pair's metrics.json; not yet a report section |

## Claim Plane (arXiv 2607.21909 and 2608.00947)

| Figure | Report section |
| --- | --- |
| Pair pass rate with static admission against none, with a bootstrap interval | Pass rates (Wilson and the task-set bootstrap) and Paired comparisons |
| Integration success | Naive-merge conflicts (clean merges) and Pass route |
| Dynamic admission's serialized and failed-closed executions | Not an arm here; the sdlc arm is a static protocol with a merge gate |

## Symphony's own protocol

The sdlc arm records, in each pair's metrics.json: claims, prs_opened,
merge_attempts, merges, merge_conflicts and time_to_first_merge_seconds,
beside the message and tool-call counts every arm has. They are not yet a
section of the report (PARITY row 97); a reader takes them from the
artifacts, and the next change to report.py is a Coordination section
with these and the team's metrics above.

## What a fake run shows

A run in fake mode fails every pair by design, since its agent implements
nothing; its report is read for the Pass rates table (every pair
attempted, 0 in its Errors column), the Communication table (the fake agent's one message to its
peer) and, in the sdlc arm, the claim, pull request and merge counts in
metrics.json, which say the protocol's path was walked.
