# Arms side by side

Passed is eval.json's both_passed, CooperBench 63b9d44's verdict. A pair whose merge conflicts passes only when agent1's own patch passes both features, since a conflicting merge is scored as placeholder results. A pair whose merge is clean passes on the merged patch. The paper-era rule (naive merge, union, resolver) is counted in the merge cascade's table.

## Runs

| Label | Run | Models | Arm | Driver | Setting | Pairs | Of the benchmark | CooperBench commit | Dataset | Isolation | Agents on another model | summary.json pass_rate | Run wall (min) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | curse-1-solo | claude-sonnet-5-5 | solo | session | solo | 36 | 36/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 75.0% | 2235.5 |
| coop | curse-1-coop | claude-sonnet-5-5 | coop | session | coop | 36 | 36/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 69.4% | 2231.2 |
| sdlc | curse-1-sdlc | claude-sonnet-5-5 | sdlc | session | coop | 36 | 36/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 72.2% | 2229.9 |
| coop-git | curse-1-coop-git | claude-sonnet-5-5 | coop-git | session | coop | 36 | 36/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 72.2% | 2228.7 |
| team | curse-1-team | claude-sonnet-5-5 | team | session | team | 35 | 35/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 74.3% | 2216.2 |

Of the benchmark counts the run's pairs among subsets/all.json's; the commit and the dataset (git tree, else content hash) come from config.json, which runs from before it have not. Isolation is advisory under the session driver, whose subagent works on the host. Models is result.json's model, the one asked for; the agents that ran another (the session driver reads it from the transcript) are counted beside it.

## Pass rates

| Arm | Model | Pairs | Passed | Rate, errors as failures | 95% Wilson | 95% bootstrap over task sets | Finished pairs passed | Rate on finished pairs | 95% Wilson (finished) | Errors | Authors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 36 | 27 | 75.0% | 58.9-86.2% | 55.6-90.0% | 27/36 | 75.0% | 58.9-86.2% | 0 | 24/50 |
| coop | claude-sonnet-5-5 | 36 | 25 | 69.4% | 53.1-82.0% | 50.0-85.0% | 25/36 | 69.4% | 53.1-82.0% | 0 | 13/50 |
| sdlc | claude-sonnet-5-5 | 36 | 26 | 72.2% | 56.0-84.2% | 53.1-86.8% | 26/36 | 72.2% | 56.0-84.2% | 0 | - |
| coop-git | claude-sonnet-5-5 | 36 | 26 | 72.2% | 56.0-84.2% | 51.4-87.5% | 26/36 | 72.2% | 56.0-84.2% | 0 | 28/50 |
| team | claude-sonnet-5-5 | 35 | 26 | 74.3% | 57.9-85.8% | 54.5-89.7% | 26/35 | 74.3% | 57.9-85.8% | 0 | 31/50 |

The first rate is the paper's k/n: eval.json both_passed over every attempted pair, a pair without an evaluation or with an eval error failing and the agents' status ignored, as CooperBench scores an errored agent's patch; a run the session route's audit found a harness error is left out, and listed apart when there is one. The rate on finished pairs is coordbench's: a pair an agent left in Error, or with no usable evaluation, is set apart (Errors) rather than failed. The bootstrap resamples task sets.

Paper, Fig 4 Left and Table 5 (k/652): Solo claude 47.1% (307), gpt-5 48.3% (315), minimax 36.2% (236), qwen coder 21.6% (141), qwen 6.3% (41); Coop claude 25.9% (169 on the website and in Table 6, 168 in Table 5), gpt-5 27.9% (182 / 183), minimax 14.0% (91), qwen coder 13.3% (87), qwen 4.6% (30).

## CooperBench's three rates

| Arm | Model | summary.json: passed / (passed + failed) | Console and ablation page: passed / evaluated | Website and paper: passed / attempted |
| --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 75.0% | 75.0% | 75.0% |
| coop | claude-sonnet-5-5 | 69.4% | 69.4% | 69.4% |
| sdlc | claude-sonnet-5-5 | 72.2% | 72.2% | 72.2% |
| coop-git | claude-sonnet-5-5 | 72.2% | 72.2% | 72.2% |
| team | claude-sonnet-5-5 | 74.3% | 74.3% | 74.3% |

runner/core.py's summary.json leaves eval errors out; its console line and scripts/gen_ablation_report.py count them in; the website and the paper divide by every pair.

## Cost and time

| Arm | Model | Pairs | Tokens per pair | Partly counted pairs, tokens per pair | Cost (USD, sum) | Cost source | Minutes per pair (mean) | Minutes per pair (median) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 36 | 1,061,079 | 31, >= 1,529,994 | - | - | 4.3 | 3.2 |
| coop | claude-sonnet-5-5 | 36 | - | 36, >= 3,114,175 | - | - | 4.8 | 4.2 |
| sdlc | claude-sonnet-5-5 | 36 | - | 36, >= 3,825,066 | - | - | 6.9 | 6.5 |
| coop-git | claude-sonnet-5-5 | 36 | - | 36, >= 3,795,126 | - | - | 4.9 | 4.4 |
| team | claude-sonnet-5-5 | 35 | - | 35, >= 3,658,283 | - | - | 5.1 | 4.3 |

Tokens per pair is over the pairs whose four counts are all known for every agent. A pair with some counts null (the session driver's output count when the transcript lacks the final one) is no total: it is counted apart, with the known counts and output_tokens_lower_bound for the missing output as a lower bound, so drivers are never compared on different bases. A pair whose counts are all null (a driver that could not measure usage) is in neither, and '-' means no pair. Cost is result.json's total_cost summed over the pairs that have one; the median is CooperBench's ablation page's.

## Paired comparisons

| Arms | Pairs both scored | Only the first passed | Only the second passed | Exact McNemar p |
| --- | --- | --- | --- | --- |
| solo (claude-sonnet-5-5) vs coop (claude-sonnet-5-5) | 36 | 4 | 2 | 0.688 |
| solo (claude-sonnet-5-5) vs sdlc (claude-sonnet-5-5) | 36 | 3 | 2 | 1.000 |
| solo (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 36 | 2 | 1 | 1.000 |
| solo (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 35 | 2 | 2 | 1.000 |
| coop (claude-sonnet-5-5) vs sdlc (claude-sonnet-5-5) | 36 | 1 | 2 | 1.000 |
| coop (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 36 | 1 | 2 | 1.000 |
| coop (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 35 | 0 | 1 | 1.000 |
| sdlc (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 36 | 1 | 1 | 1.000 |
| sdlc (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 35 | 0 | 1 | 1.000 |
| coop-git (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 35 | 0 | 1 | 1.000 |

On the pairs both arms scored (an eval.json without an error), the first attempt of each.

## Coordination gap

| Arm | Model | Solo | Pairs both scored | Solo rate | Arm rate | Gap (points) | Relative gap | Only Solo passed | Only the arm passed | Exact McNemar p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | solo | 36 | 75.0% | 69.4% | 5.6 | 7% | 4 | 2 | 0.688 |
| sdlc | claude-sonnet-5-5 | solo | 36 | 75.0% | 72.2% | 2.8 | 4% | 3 | 2 | 1.000 |
| coop-git | claude-sonnet-5-5 | solo | 36 | 75.0% | 72.2% | 2.8 | 4% | 2 | 1 | 1.000 |
| team | claude-sonnet-5-5 | solo | 35 | 74.3% | 74.3% | 0.0 | 0% | 2 | 2 | 1.000 |

Solo minus the arm on the pairs both scored (Fig 4's 'coordination gap'); relative is the gap over the Solo rate; pooled rows sum the counts across models.

Paper, Fig 4 Left (derived): claude 21.2 points (45% relative), gpt-5 20.4 (42%), minimax 22.2 (61%), qwen coder 8.3 (38%), qwen 1.7 (27%); pooled 46% relative. The abstract's '30% lower' follows from none of the paper's numbers.

## Git ablation

| Model | Coop | Coop-git | Solo | Gap with git | Gap without | Git improvement | Pairs both scored | Exact McNemar p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-sonnet-5-5 | 69.4% | 72.2% | 75.0% | -2.8 | -5.6 | +2.8 | 36 | 1.000 |

The blog post's columns: each rate over the arm's attempted pairs, the gaps signed as arm minus Solo, the McNemar test on the pairs both scored.

Blog (the-curious-case-of-miscoordination.mdx): Gemini 3 Flash, OpenHands SDK: git 27.76%, no git 26.23%, Solo 48.60%, gaps -20.84 / -22.37, +1.53; Gemini 3 Flash, Mini-SWE: 15.18 / 12.27, Solo 25.20, +2.91; Gemini 3 Pro, Mini-SWE: 21.78 / 20.40, Solo 36.80, +1.38; 'not statistically significant'.

## Pass route

| Arm | Model | Passes | Naive merge (N) | Identical patches (N) | Lead alone (L) | Other | Cascade routes, all pairs | Features passed on clean or identical merges |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 25 | 19 | 6 | 0 | 0 | naive 24, none 10, union 1 | 29/34, 29/34 |
| sdlc | claude-sonnet-5-5 | 26 | 1 | 25 | 0 | 0 | naive 26, none 10 | 31/35, 29/35 |
| coop-git | claude-sonnet-5-5 | 26 | 0 | 22 | 4 | 0 | naive 22, none 11, union 3 | 27/32, 26/32 |
| team | claude-sonnet-5-5 | 26 | 11 | 0 | 15 | 0 | naive 11, none 16, union 8 | 12/14, 13/14 |

eval.json merge.strategy of each pass (naive, skip-merge-identical, solo-agent1), and cascade.py's route over every pair that has a cascade. Per-feature results are counted only on clean and identical merges: on a conflict 63b9d44 writes placeholders unless the lead's patch passes both.

## Naive-merge conflicts

| Arm | Model | Pairs with a known merge | Conflicts | Conflict rate | 95% Wilson | Clean merges | Clean but tests fail | Semantic failure rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 36 | 2 | 5.6% | 1.5-18.1% | 34 | 9 | 26.5% |
| sdlc | claude-sonnet-5-5 | 36 | 1 | 2.8% | 0.5-14.2% | 35 | 9 | 25.7% |
| coop-git | claude-sonnet-5-5 | 36 | 4 | 11.1% | 4.4-25.3% | 32 | 10 | 31.2% |
| team | claude-sonnet-5-5 | 35 | 21 | 60.0% | 43.6-74.4% | 14 | 3 | 21.4% |

The denominator is the scored pairs whose naive-merge result is known (a finished cascade's has_naive_merge_conflict, else eval.json merge.status; a patch that failed to apply, or missing_input, is unknown with or without a cascade), as the website's rates use the rows with a merge report. The semantic failure rate, the share of clean merges whose tests fail, is coordbench's own proxy for Sec 5's semantic coordination gap; the paper gives no rate.

Website coop/index.json hasConflict over the rows with results (with comm, exact): claude 122/607 = 20.1%, gpt-5 156/571 = 27.3%, minimax 54/340 = 15.9%, qwen 257/607 = 42.3%, qwen coder 376/536 = 70.1%. No comm, read off Fig 5b only: claude ~42%, gpt-5 ~66.5%, minimax ~28%, qwen ~52.5%, qwen coder ~71%.

Conflicts compared on the pairs both arms merged (coop against coop-nocomm reproduces Fig 5b):

| Arms | Model | Pairs both known | Only the first conflicted | Only the second conflicted | Exact McNemar p |
| --- | --- | --- | --- | --- | --- |
| coop vs sdlc | claude-sonnet-5-5 | 36 | 2 | 1 | 1.000 |
| coop vs coop-git | claude-sonnet-5-5 | 36 | 2 | 4 | 0.688 |
| coop vs team | claude-sonnet-5-5 | 35 | 0 | 19 | 0.000 |
| sdlc vs coop-git | claude-sonnet-5-5 | 36 | 1 | 4 | 0.375 |
| sdlc vs team | claude-sonnet-5-5 | 35 | 0 | 20 | 0.000 |
| coop-git vs team | claude-sonnet-5-5 | 35 | 0 | 18 | 0.000 |

## Merge cascade (Table 6)

| Arm | Model | Pairs with a finished cascade | Cascade errors | Cascades not run | Naive % | +Union % (cumulative) | +LLM % (cumulative) | Paper-era verdict % | 63b9d44 verdict % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 35 | 1 | 0 | 68.57 | 71.43 (+2.9) | not run | 71.43 | 68.57 |
| sdlc | claude-sonnet-5-5 | 36 | 0 | 0 | 72.22 | 72.22 (+0.0) | not run | 72.22 | 72.22 |
| coop-git | claude-sonnet-5-5 | 36 | 0 | 0 | 61.11 | 69.44 (+8.3) | not run | 69.44 | 72.22 |
| team | claude-sonnet-5-5 | 35 | 0 | 0 | 31.43 | 54.29 (+22.9) | not run | 54.29 | 74.29 |

naive_ok, union_ok and llm_ok are exclusive and the percentages cumulative, as the legacy aggregation computes them, over the pairs whose cascade finished (the paper's are over all 652). A cascade that errored (its own sandbox failed, or it was cut short) or did not run (the pair or CooperBench's evaluation ended in error) is counted apart and in no percentage: it says nothing about the merge. The two verdicts are the paper era's rule (naive, union, resolver; no lead-alone fallback) and 63b9d44's, eval.json's both_passed, never mixed.
The learned resolver did not run: no resolver endpoint: set COORDBENCH_RESOLVER_URL to an OpenAI-compatible API base serving the paper's learned resolver (Qwen2.5-Coder-0.5B-Merge-Resolver); coordbench runs no model server; not needed: the naive merge was clean; no resolver endpoint: set COORDBENCH_RESOLVER_URL to an OpenAI-compatible API base serving the paper's learned resolver (Qwen2.5-Coder-0.5B-Merge-Resolver); coordbench runs no model server; not needed: the naive merge was clean; not needed: the union merge passed both suites.

Paper, Table 6 (Naive / +Union / +LLM, % of 652): Claude 4.5 no comm 12.27 / 26.84 / 27.30, with comm 16.72 / 24.85 / 25.92 (delta -1.4); GPT-5 13.88 / 26.69 / 27.91 and 20.42 / 26.64 / 27.90; average delta -0.5.

Each agent's patch alone against both suites (agents may redivide the features, footnote 1; both patches passing the same suite is a free sign of duplicated work):

| Arm | Model | Pairs | First agent alone passes both | Second agent alone passes both | Both pass the same suite |
| --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 35 | 6 | 6 | 6 |
| sdlc | claude-sonnet-5-5 | 36 | 25 | 27 | 35 |
| coop-git | claude-sonnet-5-5 | 36 | 26 | 26 | 35 |
| team | claude-sonnet-5-5 | 35 | 26 | 0 | 29 |

## Communication

| Arm | Model | Conversations | With text | Turns (mean) | Speaker runs (mean) | Overhead: sends / tool calls | Sends and receives / tool calls | Sends and their results / calls and results | Pairs with a transcript |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 36 | 36 | 6.2 | 4.9 | 15.9% | 36.3% | 15.9% | 27 |
| sdlc | claude-sonnet-5-5 | 36 | 36 | 3.8 | 3.1 | 8.1% | 18.0% | 8.1% | 29 |
| coop-git | claude-sonnet-5-5 | 36 | 36 | 6.2 | 5.0 | 14.9% | 33.5% | 14.9% | 27 |
| team | claude-sonnet-5-5 | 35 | 35 | 4.2 | 3.6 | 9.8% | 20.7% | 9.8% | 25 |

A turn is one sent message, one per send call (a broadcast is one turn; a team's request and its response are one each, as the overhead counts them), from the conversation the judge labels (events.jsonl, else conversation_store.json, else CooperBench's conversation.json); speaker runs count the changes of speaker instead. The overhead pools the pairs: the sending calls (send_message, broadcast, and the team's request and respond) and the receiving ones (receive_messages, wait_for_message) as the hub counted them, each alias of a chained shell command apart, or as the transcripts show them, over every tool call of both agents' own turns (subagents left out). The paper counts OpenHands events (actions and observations), so neither basis is its unit.

Paper, Fig 6a (average turns): claude 14.5, gpt-5 15.5, minimax 10.8, qwen 3.2, qwen coder 1.1. Fig 5c (comm events, % of all events): claude 20.0%, gpt-5 16.3%, minimax 13.6%, qwen 6.2%, qwen coder 3.3%.

## Speech acts

Each act's count, and its share of the agents' tool calls over the conversations that have a transcript (Fig 5c splits the overhead so):

| Arm | Model | Labels | Conversations | With a transcript | Plan (% of tool calls) | Question (% of tool calls) | Answer (% of tool calls) | Update (% of tool calls) | Ack (% of tool calls) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 36 | 27 | 51 (4.0%) | 31 (1.9%) | 23 (1.8%) | 55 (4.2%) | 14 (0.6%) |
| sdlc | claude-sonnet-5-5 | keyword baseline | 36 | 29 | 57 (3.3%) | 1 (0.1%) | 4 (0.2%) | 5 (0.4%) | 4 (0.2%) |
| coop-git | claude-sonnet-5-5 | keyword baseline | 36 | 27 | 33 (2.4%) | 36 (2.2%) | 24 (1.7%) | 32 (2.1%) | 5 (0.4%) |
| team | claude-sonnet-5-5 | keyword baseline | 35 | 25 | 48 (3.0%) | 12 (0.9%) | 18 (1.1%) | 49 (3.5%) | 10 (0.5%) |

Plan:Question, by naive-merge conflict, as the ratio of totals and the mean of per-trajectory ratios (a trajectory with no Question left out of the mean):

| Arm | Model | Labels | Pairs without conflict | Ratio of totals | Mean of ratios | Pairs with conflict | Ratio of totals | Mean of ratios |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 34 | 1.55 | 0.81 | 2 | - | - |
| sdlc | claude-sonnet-5-5 | keyword baseline | 35 | 55.00 | 0.00 | 1 | - | - |
| coop-git | claude-sonnet-5-5 | keyword baseline | 32 | 0.85 | 0.69 | 4 | 1.67 | 0.50 |
| team | claude-sonnet-5-5 | keyword baseline | 14 | 7.00 | 1.00 | 21 | 3.00 | 0.94 |

First-turn planning: the conflict rate when the conversation's first message is a Plan and when it is not, and, as a variant, when every agent's first message is a Plan (Fisher's exact test):

| Arm | Model | Labels | Pairs | First message a Plan | Otherwise | Fisher p | Every agent opens with a Plan | Otherwise | Fisher p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 36 | 1/23 (4.3%) | 1/13 (7.7%) | 1.000 | 1/19 (5.3%) | 1/17 (5.9%) | 1.000 |
| sdlc | claude-sonnet-5-5 | keyword baseline | 36 | 1/31 (3.2%) | 0/5 (0.0%) | 1.000 | 1/24 (4.2%) | 0/12 (0.0%) | 1.000 |
| coop-git | claude-sonnet-5-5 | keyword baseline | 36 | 2/9 (22.2%) | 2/27 (7.4%) | 0.255 | 2/6 (33.3%) | 2/30 (6.7%) | 0.121 |
| team | claude-sonnet-5-5 | keyword baseline | 35 | 12/25 (48.0%) | 9/10 (90.0%) | 0.028 | 9/17 (52.9%) | 12/18 (66.7%) | 0.500 |

By difficulty bucket (App C's ten; the paper's eight are undefined), every arm pooled:

| Labels | Bucket midpoint | First message a Plan | Otherwise | Fisher p |
| --- | --- | --- | --- | --- |
| keyword baseline | 0.05 | 13/65 (20.0%) | 8/42 (19.0%) | 1.000 |
| keyword baseline | 0.95 | 3/23 (13.0%) | 4/13 (30.8%) | 0.225 |

Paper, Sec 5: Plan:Question 2.04 without conflict vs 1.31 with; a Plan in the first turn 29.4% conflict rate vs 51.5% without.

## Concrete references

| Arm | Model | Split | Pairs | Line references, mean | Mann-Whitney p | File paths, mean | Mann-Whitney p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | passed / failed | 25 / 11 | 0.8 / 0.6 | 0.992 | 11.4 / 9.3 | 0.640 |
| coop | claude-sonnet-5-5 | no conflict / conflict | 34 / 2 | 0.6 / 2.0 | 0.057 | 11.1 / 4.0 | 0.057 |
| sdlc | claude-sonnet-5-5 | passed / failed | 26 / 10 | 0.1 / 0.1 | 1.000 | 5.3 / 4.7 | 0.651 |
| sdlc | claude-sonnet-5-5 | no conflict / conflict | 35 / 1 | 0.1 / 0.0 | 1.000 | 5.2 / 2.0 | 0.222 |
| coop-git | claude-sonnet-5-5 | passed / failed | 26 / 10 | 0.5 / 0.2 | 0.479 | 7.9 / 7.8 | 0.719 |
| coop-git | claude-sonnet-5-5 | no conflict / conflict | 32 / 4 | 0.4 / 0.2 | 0.752 | 7.8 / 8.0 | 0.638 |
| team | claude-sonnet-5-5 | passed / failed | 26 / 9 | 0.0 / 0.2 | 0.803 | 7.8 / 7.1 | 0.889 |
| team | claude-sonnet-5-5 | no conflict / conflict | 14 / 21 | 0.1 / 0.0 | 0.753 | 9.0 / 6.7 | 0.530 |

Regex counts over the message text, per trajectory: line references ('line N', 'lines N-M', 'L<N>', '<path>:<N>') and file paths (a token ending in a source extension). Both splits are given, since the paper does not say whether 'successful' means passing or avoiding conflict.

Paper, Sec 5: successful trajectories 32.6 vs 22.5 line-number mentions and 13.1 vs 10.0 file-path mentions ('successful' and the extraction unstated).

## Coordination

The claim protocol's counters, summed over the pairs that recorded them (the sdlc arm):

| Arm | Model | Pairs | Claims | Pull requests opened | Merge attempts | Merges | Merge conflicts | Conflicts / attempts | Pairs where every agent merged | Pairs where one did not | Seconds to first merge (median) | Pairs with a merge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sdlc | claude-sonnet-5-5 | 36 | 72 | 72 | 74 | 72 | 2 | 2.7% | 36 | 0 | 124.6 | 36 |

The team harness's board, summed over the pairs that recorded it (the team arm):

| Arm | Model | Pairs | Tasks total | Tasks done | Unowned at the end | Done / total | Seconds to first claim (median) | Pairs with a claim | Claims per pair | Updates per pair |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| team | claude-sonnet-5-5 | 35 | 70 | 70 | 0 | 100.0% | 34.0 | 35 | 1.74 | 3.40 |

Each count is a pair's arm metrics (result.json's metrics block, else metrics.json) summed over the arm's pairs whose agents all finished and that hold every column's key, scored or not; a pair an agent left in Error, or that failed before its agents ran, is left out, as the rates set them apart. A merge attempt ends in a merge, a merge conflict (the default branch moved under the gate and the branch conflicts with it) or a refusal: a main that moved without a conflict, a gate failure or an attempt that crashed count with the refusals, which are the rest. A pair where every agent merged is one where each agent landed a merge, by the hub's merge events (with an open queue one agent may merge every issue). Seconds to the first merge count from the hub's start of the pair, before its agents were provisioned; seconds to the first claim from the first task's creation. The team's claims and updates per pair sum its claims_per_agent and updates_per_agent. These are the protocol's own counts, which the paper has no figure for; the emergent rates stand in the sections above (PARITY row 97).

## Failure modes

Judge (backend / model / prompt variant): none called, the free heuristics only.

Free cross-checks, pairs where each found something (beside the labels, never replacing them):

| Arm | Model | Pairs | clean_merge_failed | contradictions | git_broken | git_destructive | git_disasters | git_impatience | ignored_questions | insertion_markers | missing_final_status | near_duplicates | negotiation_candidates | overlapping_hunks | resource_division_candidates | unanswered_questions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 36 | 9 | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 1 | 1 | 25 | 5 | 1 |
| sdlc | claude-sonnet-5-5 | 36 | 0 | 2 | 6 | 0 | 0 | 36 | 0 | 0 | 14 | 0 | 0 | 36 | 0 | 0 |
| coop-git | claude-sonnet-5-5 | 36 | 0 | 4 | 10 | 0 | 36 | 35 | 0 | 0 | 13 | 0 | 0 | 36 | 3 | 1 |
| team | claude-sonnet-5-5 | 35 | 3 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 28 | 0 | 1 | 35 | 0 | 0 |

## Difficulty (App C)

1 Solo model(s): claude-sonnet-5-5; d takes 2 distinct value(s) over 36 pair(s).
With one or two models d is coarse ({0, 1} or {0, 0.5, 1} with one run each), so at most three buckets fill: the curves, areas and retention are degenerate and not comparable with Table 5's five models.
d~ is rescaled over the pairs scored here, not the paper's 652; the paper's per-pair Solo outcomes are unpublished, so these pairs cannot be placed on its scale.

| Arm | Model | Pairs | Filled buckets | Limits | AUC Solo | AUC arm | Delta AUC | Retention |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 36 | 2 | 0.05-0.95 | 0.450 | 0.483 | -0.033 | 1.07 |
| sdlc | claude-sonnet-5-5 | 36 | 2 | 0.05-0.95 | 0.450 | 0.500 | -0.050 | 1.11 |
| coop-git | claude-sonnet-5-5 | 36 | 2 | 0.05-0.95 | 0.450 | 0.467 | -0.017 | 1.04 |
| team | claude-sonnet-5-5 | 35 | 2 | 0.05-0.95 | 0.450 | 0.515 | -0.065 | 1.15 |

Filled buckets, Solo and arm passes with their 95% Wilson intervals:

| Arm | Model | Midpoint | Pairs | Solo | Wilson | Arm | Wilson |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 0.05 | 27 | 27/27 | 87.5-100.0% | 23/27 | 67.5-94.1% |
| coop | claude-sonnet-5-5 | 0.95 | 9 | 0/9 | 0.0-29.9% | 2/9 | 6.3-54.7% |
| sdlc | claude-sonnet-5-5 | 0.05 | 27 | 27/27 | 87.5-100.0% | 24/27 | 71.9-96.1% |
| sdlc | claude-sonnet-5-5 | 0.95 | 9 | 0/9 | 0.0-29.9% | 2/9 | 6.3-54.7% |
| coop-git | claude-sonnet-5-5 | 0.05 | 27 | 27/27 | 87.5-100.0% | 25/27 | 76.6-97.9% |
| coop-git | claude-sonnet-5-5 | 0.95 | 9 | 0/9 | 0.0-29.9% | 1/9 | 2.0-43.5% |
| team | claude-sonnet-5-5 | 0.05 | 26 | 26/26 | 87.1-100.0% | 24/26 | 75.9-97.9% |
| team | claude-sonnet-5-5 | 0.95 | 9 | 0/9 | 0.0-29.9% | 2/9 | 6.3-54.7% |

Paper, Table 5 (AUC Solo / Coop, Delta, retention): gpt-5 0.506 / 0.325, 0.181, 0.64; claude 0.469 / 0.283, 0.186, 0.60; minimax 0.374 / 0.171, 0.203, 0.46; qwen coder 0.236 / 0.148, 0.088, 0.63; qwen 0.106 / 0.072, 0.034, 0.68; pooled 0.338 / 0.200, 0.138, 0.59.

## Dataset

Conflicting gold solutions: 499 of 652 (76.5%) in CooperBench's dataset/gold_conflict_report.json, against the paper's 77.3% on the paper-era dataset; 26 of the 36 pairs these runs cover.

Pass rate, errors as failures:

| Arm | Model | All pairs | Features unamended | Features and task unamended | Audit both OK | Audit any SPEC | Audit any TEST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 27/36 (75.0%) | 21/26 (80.8%) | 13/15 (86.7%) | 21/26 (80.8%) | 5/8 (62.5%) | 1/2 (50.0%) |
| coop | claude-sonnet-5-5 | 25/36 (69.4%) | 19/26 (73.1%) | 13/15 (86.7%) | 19/26 (73.1%) | 5/8 (62.5%) | 1/2 (50.0%) |
| sdlc | claude-sonnet-5-5 | 26/36 (72.2%) | 20/26 (76.9%) | 14/15 (93.3%) | 20/26 (76.9%) | 5/8 (62.5%) | 1/2 (50.0%) |
| coop-git | claude-sonnet-5-5 | 26/36 (72.2%) | 20/26 (76.9%) | 14/15 (93.3%) | 20/26 (76.9%) | 5/8 (62.5%) | 1/2 (50.0%) |
| team | claude-sonnet-5-5 | 26/35 (74.3%) | 20/25 (80.0%) | 13/14 (92.9%) | 20/25 (80.0%) | 5/8 (62.5%) | 1/2 (50.0%) |

Naive-merge conflict rate:

| Arm | Model | All pairs | Features unamended | Features and task unamended | Audit both OK | Audit any SPEC | Audit any TEST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 2/36 (5.6%) | 1/26 (3.8%) | 0/15 (0.0%) | 1/26 (3.8%) | 1/8 (12.5%) | 0/2 (0.0%) |
| sdlc | claude-sonnet-5-5 | 1/36 (2.8%) | 1/26 (3.8%) | 0/15 (0.0%) | 1/26 (3.8%) | 0/8 (0.0%) | 0/2 (0.0%) |
| coop-git | claude-sonnet-5-5 | 4/36 (11.1%) | 3/26 (11.5%) | 3/15 (20.0%) | 3/26 (11.5%) | 1/8 (12.5%) | 0/2 (0.0%) |
| team | claude-sonnet-5-5 | 21/35 (60.0%) | 15/25 (60.0%) | 8/14 (57.1%) | 15/25 (60.0%) | 6/8 (75.0%) | 0/2 (0.0%) |

Each headline rate again over the pairs whose two features' feature.md, feature.patch and tests.patch are as in the paper's dataset (59bbf65), then with the task's runner.sh, Dockerfile and other files unchanged too, and by the spec audit's verdicts (OK; SPEC: feature.md amended; TEST: tests.patch amended).

## Against CooperBench's published runs

| Arm | Model | Published model | Pairs in both | Our rate | Published rate | Only ours passed | Only theirs passed | Exact McNemar p | Our conflict rate | Published conflict rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | claude | 32 | 65.6% | 34.4% | 12 | 2 | 0.013 | 6.2% | 25.0% |
| coop | claude-sonnet-5-5 | gemini_flash_miniswe | 36 | 69.4% | 8.3% | 22 | 0 | 0.000 | 5.6% | 41.7% |
| coop | claude-sonnet-5-5 | gemini_flash_sdk | 36 | 69.4% | 30.6% | 16 | 2 | 0.001 | 5.6% | 61.1% |
| coop | claude-sonnet-5-5 | gemini_pro_miniswe | 36 | 69.4% | 25.0% | 16 | 0 | 0.000 | 5.6% | 36.1% |
| coop | claude-sonnet-5-5 | gpt5 | 30 | 63.3% | 36.7% | 10 | 2 | 0.039 | 6.7% | 26.7% |
| coop | claude-sonnet-5-5 | minimax | 20 | 60.0% | 10.0% | 11 | 1 | 0.006 | 0.0% | 15.0% |
| coop | claude-sonnet-5-5 | qwen | 32 | 65.6% | 3.1% | 20 | 0 | 0.000 | 6.2% | 34.4% |
| coop | claude-sonnet-5-5 | qwen_coder | 28 | 71.4% | 10.7% | 17 | 0 | 0.000 | 7.1% | 57.1% |
| sdlc | claude-sonnet-5-5 | claude | 32 | 71.9% | 34.4% | 13 | 1 | 0.002 | 0.0% | 25.0% |
| sdlc | claude-sonnet-5-5 | gemini_flash_miniswe | 36 | 72.2% | 8.3% | 23 | 0 | 0.000 | 2.8% | 41.7% |
| sdlc | claude-sonnet-5-5 | gemini_flash_sdk | 36 | 72.2% | 30.6% | 16 | 1 | 0.000 | 2.8% | 61.1% |
| sdlc | claude-sonnet-5-5 | gemini_pro_miniswe | 36 | 72.2% | 25.0% | 18 | 1 | 0.000 | 2.8% | 36.1% |
| sdlc | claude-sonnet-5-5 | gpt5 | 30 | 70.0% | 36.7% | 11 | 1 | 0.006 | 0.0% | 26.7% |
| sdlc | claude-sonnet-5-5 | minimax | 20 | 60.0% | 10.0% | 10 | 0 | 0.002 | 5.0% | 15.0% |
| sdlc | claude-sonnet-5-5 | qwen | 32 | 71.9% | 3.1% | 22 | 0 | 0.000 | 0.0% | 34.4% |
| sdlc | claude-sonnet-5-5 | qwen_coder | 28 | 78.6% | 10.7% | 19 | 0 | 0.000 | 0.0% | 57.1% |
| coop-git | claude-sonnet-5-5 | claude | 32 | 68.8% | 34.4% | 12 | 1 | 0.003 | 9.4% | 25.0% |
| coop-git | claude-sonnet-5-5 | gemini_flash_miniswe | 36 | 72.2% | 8.3% | 23 | 0 | 0.000 | 11.1% | 41.7% |
| coop-git | claude-sonnet-5-5 | gemini_flash_sdk | 36 | 72.2% | 30.6% | 16 | 1 | 0.000 | 11.1% | 61.1% |
| coop-git | claude-sonnet-5-5 | gemini_pro_miniswe | 36 | 72.2% | 25.0% | 17 | 0 | 0.000 | 11.1% | 36.1% |
| coop-git | claude-sonnet-5-5 | gpt5 | 30 | 70.0% | 36.7% | 11 | 1 | 0.006 | 10.0% | 26.7% |
| coop-git | claude-sonnet-5-5 | minimax | 20 | 60.0% | 10.0% | 10 | 0 | 0.002 | 10.0% | 15.0% |
| coop-git | claude-sonnet-5-5 | qwen | 32 | 68.8% | 3.1% | 21 | 0 | 0.000 | 9.4% | 34.4% |
| coop-git | claude-sonnet-5-5 | qwen_coder | 28 | 78.6% | 10.7% | 19 | 0 | 0.000 | 10.7% | 57.1% |
| team | claude-sonnet-5-5 | claude | 31 | 71.0% | 32.3% | 13 | 1 | 0.002 | 54.8% | 25.8% |
| team | claude-sonnet-5-5 | gemini_flash_miniswe | 35 | 74.3% | 8.6% | 23 | 0 | 0.000 | 60.0% | 42.9% |
| team | claude-sonnet-5-5 | gemini_flash_sdk | 35 | 74.3% | 31.4% | 16 | 1 | 0.000 | 60.0% | 60.0% |
| team | claude-sonnet-5-5 | gemini_pro_miniswe | 35 | 74.3% | 25.7% | 17 | 0 | 0.000 | 60.0% | 37.1% |
| team | claude-sonnet-5-5 | gpt5 | 29 | 69.0% | 37.9% | 10 | 1 | 0.012 | 58.6% | 27.6% |
| team | claude-sonnet-5-5 | minimax | 19 | 63.2% | 5.3% | 11 | 0 | 0.001 | 68.4% | 10.5% |
| team | claude-sonnet-5-5 | qwen | 31 | 71.0% | 3.2% | 21 | 0 | 0.000 | 54.8% | 35.5% |
| team | claude-sonnet-5-5 | qwen_coder | 27 | 77.8% | 11.1% | 18 | 0 | 0.000 | 55.6% | 55.6% |

The website's coop/index.json, rows with results only. Its paper models were scored by the legacy cascade (naive, union, resolver) and its Gemini rows by 63b9d44's both_passed, so the two published rules differ, and its stats cannot be recomputed from its rows.

## Every pair

| Pair | solo (claude-sonnet-5-5) | coop (claude-sonnet-5-5) | sdlc (claude-sonnet-5-5) | coop-git (claude-sonnet-5-5) | team (claude-sonnet-5-5) |
| --- | --- | --- | --- | --- | --- |
| dottxt_ai_outlines_task/1655/f6_f7 | fail | pass N | pass N | pass N | pass L |
| dottxt_ai_outlines_task/1655/f7_f10 | pass | pass N | pass N | pass N | pass N |
| dspy_task/8394/f3_f5 | pass | fail | fail | fail | fail |
| go_chi_task/26/f1_f2 | pass | pass N | pass N | pass N | pass N |
| go_chi_task/56/f1_f5 | fail | pass N | pass N | fail | pass N |
| huggingface_datasets_task/3997/f2_f4 | fail | fail | fail | fail | fail |
| huggingface_datasets_task/6252/f4_f6 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2068/f1_f6 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2068/f2_f10 | fail | fail | fail | fail | fail |
| pallets_click_task/2068/f5_f11 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2068/f5_f7 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2068/f6_f7 | pass | pass N | pass N | pass L | pass L |
| pallets_click_task/2068/f7_f10 | pass | pass N | pass N | pass L | pass L |
| pallets_click_task/2800/f1_f3 | pass | pass N | pass N | pass N | pass N |
| pallets_click_task/2800/f1_f7 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2800/f2_f7 | pass | fail | pass N | pass L | - |
| pallets_click_task/2956/f1_f8 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1465/f2_f3 | pass | fail | pass N | pass N | pass L |
| pallets_jinja_task/1559/f4_f10 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1559/f4_f8 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1559/f6_f9 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1621/f2_f9 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1621/f3_f5 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1621/f4_f10 | fail | fail | fail | fail | fail |
| pallets_jinja_task/1621/f6_f7 | pass | pass N | pass N | pass N | pass N |
| pillow_task/25/f1_f5 | pass | fail | fail | fail | fail |
| pillow_task/25/f2_f3 | pass | pass N | pass N | pass N | pass L |
| pillow_task/290/f2_f3 | fail | fail | fail | fail | fail |
| pillow_task/290/f3_f5 | fail | fail | fail | fail | fail |
| pillow_task/290/f4_f5 | fail | fail | fail | fail | fail |
| pillow_task/68/f1_f5 | fail | fail | fail | fail | fail |
| samuelcolvin_dirty_equals_task/43/f3_f7 | pass | pass N | pass N | pass N | pass L |
| typst_task/6554/f1_f9 | pass | pass N | fail | pass N | pass L |
| typst_task/6554/f4_f9 | pass | pass N | pass N | pass N | pass L |
| typst_task/6554/f5_f9 | pass | pass N | pass N | pass L | pass L |
| typst_task/6554/f7_f8 | pass | pass N | pass N | pass N | pass L |

Each cell is the pair's first attempt: pass or fail by eval.json, N when it passed on the naive merge or identical patches, L on the lead's patch alone, error when it has no eval.json, '-' when the arm did not run it.
