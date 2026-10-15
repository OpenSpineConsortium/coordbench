# Arms side by side

Passed is eval.json's both_passed, CooperBench 63b9d44's verdict. A pair whose merge conflicts passes only when agent1's own patch passes both features, since a conflicting merge is scored as placeholder results. A pair whose merge is clean passes on the merged patch. The paper-era rule (naive merge, union, resolver) is counted in the merge cascade's table.

## Runs

| Label | Run | Models | Arm | Driver | Setting | Pairs | Of the benchmark | CooperBench commit | Dataset | Isolation | Agents on another model | summary.json pass_rate | Run wall (min) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | curse-1-solo | claude-sonnet-5-5 | solo | session | solo | 8 | 8/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 62.5% | 564.9 |
| coop | curse-1-coop | claude-sonnet-5-5 | coop | session | coop | 8 | 8/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 75.0% | 302.2 |
| sdlc | curse-1-sdlc | claude-sonnet-5-5 | sdlc | session | coop | 8 | 8/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 75.0% | 323.5 |
| coop-git | curse-1-coop-git | claude-sonnet-5-5 | coop-git | session | coop | 8 | 8/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 75.0% | 342.1 |
| team | curse-1-team | claude-sonnet-5-5 | team | session | team | 8 | 8/652 | 63b9d44d9f | 9c9e5802e0 | advisory | 0 | 75.0% | 362.0 |

Of the benchmark counts the run's pairs among subsets/all.json's; the commit and the dataset (git tree, else content hash) come from config.json, which runs from before it have not. Isolation is advisory under the session driver, whose subagent works on the host. Models is result.json's model, the one asked for; the agents that ran another (the session driver reads it from the transcript) are counted beside it.

## Pass rates

| Arm | Model | Pairs | Passed | Rate, errors as failures | 95% Wilson | 95% bootstrap over task sets | Finished pairs passed | Rate on finished pairs | 95% Wilson (finished) | Errors | Authors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 8 | 5 | 62.5% | 30.6-86.3% | 25.0-87.5% | 5/8 | 62.5% | 30.6-86.3% | 0 | 24/50 |
| coop | claude-sonnet-5-5 | 8 | 6 | 75.0% | 40.9-92.9% | 37.5-100.0% | 6/8 | 75.0% | 40.9-92.9% | 0 | 13/50 |
| sdlc | claude-sonnet-5-5 | 8 | 6 | 75.0% | 40.9-92.9% | 37.5-100.0% | 6/8 | 75.0% | 40.9-92.9% | 0 | - |
| coop-git | claude-sonnet-5-5 | 8 | 6 | 75.0% | 40.9-92.9% | 37.5-100.0% | 6/8 | 75.0% | 40.9-92.9% | 0 | 28/50 |
| team | claude-sonnet-5-5 | 8 | 6 | 75.0% | 40.9-92.9% | 37.5-100.0% | 6/8 | 75.0% | 40.9-92.9% | 0 | 31/50 |

The first rate is the paper's k/n: eval.json both_passed over every attempted pair, a pair without an evaluation or with an eval error failing and the agents' status ignored, as CooperBench scores an errored agent's patch. The rate on finished pairs is coordbench's: a pair an agent left in Error, or with no usable evaluation, is set apart (Errors) rather than failed. The bootstrap resamples task sets.

Paper, Fig 4 Left and Table 5 (k/652): Solo claude 47.1% (307), gpt-5 48.3% (315), minimax 36.2% (236), qwen coder 21.6% (141), qwen 6.3% (41); Coop claude 25.9% (169 on the website and in Table 6, 168 in Table 5), gpt-5 27.9% (182 / 183), minimax 14.0% (91), qwen coder 13.3% (87), qwen 4.6% (30).

## CooperBench's three rates

| Arm | Model | summary.json: passed / (passed + failed) | Console and ablation page: passed / evaluated | Website and paper: passed / attempted |
| --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 62.5% | 62.5% | 62.5% |
| coop | claude-sonnet-5-5 | 75.0% | 75.0% | 75.0% |
| sdlc | claude-sonnet-5-5 | 75.0% | 75.0% | 75.0% |
| coop-git | claude-sonnet-5-5 | 75.0% | 75.0% | 75.0% |
| team | claude-sonnet-5-5 | 75.0% | 75.0% | 75.0% |

runner/core.py's summary.json leaves eval errors out; its console line and scripts/gen_ablation_report.py count them in; the website and the paper divide by every pair.

## Cost and time

| Arm | Model | Pairs | Tokens per pair | Partly counted pairs, tokens per pair | Cost (USD, sum) | Cost source | Minutes per pair (mean) | Minutes per pair (median) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 8 | - | 8, >= 1,737,924 | - | - | 5.1 | 3.7 |
| coop | claude-sonnet-5-5 | 8 | - | 8, >= 3,371,448 | - | - | 6.1 | 5.6 |
| sdlc | claude-sonnet-5-5 | 8 | - | 8, >= 3,789,386 | - | - | 19.6 | 8.8 |
| coop-git | claude-sonnet-5-5 | 8 | - | 8, >= 4,437,464 | - | - | 6.0 | 5.1 |
| team | claude-sonnet-5-5 | 8 | - | 8, >= 3,982,167 | - | - | 5.8 | 4.9 |

Tokens per pair is over the pairs whose four counts are all known for every agent. A pair with some counts null (the session driver's output count when the transcript lacks the final one) is no total: it is counted apart, with the known counts and output_tokens_lower_bound for the missing output as a lower bound, so drivers are never compared on different bases. A pair whose counts are all null (a driver that could not measure usage) is in neither, and '-' means no pair. Cost is result.json's total_cost summed over the pairs that have one; the median is CooperBench's ablation page's.

## Paired comparisons

| Arms | Pairs both scored | Only the first passed | Only the second passed | Exact McNemar p |
| --- | --- | --- | --- | --- |
| solo (claude-sonnet-5-5) vs coop (claude-sonnet-5-5) | 8 | 0 | 1 | 1.000 |
| solo (claude-sonnet-5-5) vs sdlc (claude-sonnet-5-5) | 8 | 0 | 1 | 1.000 |
| solo (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 8 | 0 | 1 | 1.000 |
| solo (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 8 | 0 | 1 | 1.000 |
| coop (claude-sonnet-5-5) vs sdlc (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |
| coop (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |
| coop (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |
| sdlc (claude-sonnet-5-5) vs coop-git (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |
| sdlc (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |
| coop-git (claude-sonnet-5-5) vs team (claude-sonnet-5-5) | 8 | 0 | 0 | 1.000 |

On the pairs both arms scored (an eval.json without an error), the first attempt of each.

## Coordination gap

| Arm | Model | Solo | Pairs both scored | Solo rate | Arm rate | Gap (points) | Relative gap | Only Solo passed | Only the arm passed | Exact McNemar p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | solo | 8 | 62.5% | 75.0% | -12.5 | -20% | 0 | 1 | 1.000 |
| sdlc | claude-sonnet-5-5 | solo | 8 | 62.5% | 75.0% | -12.5 | -20% | 0 | 1 | 1.000 |
| coop-git | claude-sonnet-5-5 | solo | 8 | 62.5% | 75.0% | -12.5 | -20% | 0 | 1 | 1.000 |
| team | claude-sonnet-5-5 | solo | 8 | 62.5% | 75.0% | -12.5 | -20% | 0 | 1 | 1.000 |

Solo minus the arm on the pairs both scored (Fig 4's 'coordination gap'); relative is the gap over the Solo rate; pooled rows sum the counts across models.

Paper, Fig 4 Left (derived): claude 21.2 points (45% relative), gpt-5 20.4 (42%), minimax 22.2 (61%), qwen coder 8.3 (38%), qwen 1.7 (27%); pooled 46% relative. The abstract's '30% lower' follows from none of the paper's numbers.

## Git ablation

| Model | Coop | Coop-git | Solo | Gap with git | Gap without | Git improvement | Pairs both scored | Exact McNemar p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-sonnet-5-5 | 75.0% | 75.0% | 62.5% | +12.5 | +12.5 | +0.0 | 8 | 1.000 |

The blog post's columns: each rate over the arm's attempted pairs, the gaps signed as arm minus Solo, the McNemar test on the pairs both scored.

Blog (the-curious-case-of-miscoordination.mdx): Gemini 3 Flash, OpenHands SDK: git 27.76%, no git 26.23%, Solo 48.60%, gaps -20.84 / -22.37, +1.53; Gemini 3 Flash, Mini-SWE: 15.18 / 12.27, Solo 25.20, +2.91; Gemini 3 Pro, Mini-SWE: 21.78 / 20.40, Solo 36.80, +1.38; 'not statistically significant'.

## Pass route

| Arm | Model | Passes | Naive merge (N) | Identical patches (N) | Lead alone (L) | Other | Cascade routes, all pairs | Features passed on clean or identical merges |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 6 | 5 | 1 | 0 | 0 | naive 5, none 2 | 7/8, 6/8 |
| sdlc | claude-sonnet-5-5 | 6 | 1 | 5 | 0 | 0 | naive 6, none 2 | 7/8, 6/8 |
| coop-git | claude-sonnet-5-5 | 6 | 0 | 5 | 1 | 0 | naive 5, none 2, union 1 | 6/7, 5/7 |
| team | claude-sonnet-5-5 | 6 | 3 | 0 | 3 | 0 | naive 3, none 3, union 2 | 3/4, 3/4 |

eval.json merge.strategy of each pass (naive, skip-merge-identical, solo-agent1), and cascade.py's route over every pair that has a cascade. Per-feature results are counted only on clean and identical merges: on a conflict 63b9d44 writes placeholders unless the lead's patch passes both.

## Naive-merge conflicts

| Arm | Model | Pairs with a known merge | Conflicts | Conflict rate | 95% Wilson | Clean merges | Clean but tests fail | Semantic failure rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 8 | 0 | 0.0% | 0.0-32.4% | 8 | 2 | 25.0% |
| sdlc | claude-sonnet-5-5 | 8 | 0 | 0.0% | 0.0-32.4% | 8 | 2 | 25.0% |
| coop-git | claude-sonnet-5-5 | 8 | 1 | 12.5% | 2.2-47.1% | 7 | 2 | 28.6% |
| team | claude-sonnet-5-5 | 8 | 4 | 50.0% | 21.5-78.5% | 4 | 1 | 25.0% |

The denominator is the scored pairs whose naive-merge result is known (a finished cascade's has_naive_merge_conflict, else eval.json merge.status; a patch that failed to apply, or missing_input, is unknown with or without a cascade), as the website's rates use the rows with a merge report. The semantic failure rate, the share of clean merges whose tests fail, is coordbench's own proxy for Sec 5's semantic coordination gap; the paper gives no rate.

Website coop/index.json hasConflict over the rows with results (with comm, exact): claude 122/607 = 20.1%, gpt-5 156/571 = 27.3%, minimax 54/340 = 15.9%, qwen 257/607 = 42.3%, qwen coder 376/536 = 70.1%. No comm, read off Fig 5b only: claude ~42%, gpt-5 ~66.5%, minimax ~28%, qwen ~52.5%, qwen coder ~71%.

Conflicts compared on the pairs both arms merged (coop against coop-nocomm reproduces Fig 5b):

| Arms | Model | Pairs both known | Only the first conflicted | Only the second conflicted | Exact McNemar p |
| --- | --- | --- | --- | --- | --- |
| coop vs sdlc | claude-sonnet-5-5 | 8 | 0 | 0 | 1.000 |
| coop vs coop-git | claude-sonnet-5-5 | 8 | 0 | 1 | 1.000 |
| coop vs team | claude-sonnet-5-5 | 8 | 0 | 4 | 0.125 |
| sdlc vs coop-git | claude-sonnet-5-5 | 8 | 0 | 1 | 1.000 |
| sdlc vs team | claude-sonnet-5-5 | 8 | 0 | 4 | 0.125 |
| coop-git vs team | claude-sonnet-5-5 | 8 | 0 | 3 | 0.250 |

## Merge cascade (Table 6)

| Arm | Model | Pairs with a finished cascade | Cascade errors | Cascades not run | Naive % | +Union % (cumulative) | +LLM % (cumulative) | Paper-era verdict % | 63b9d44 verdict % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 7 | 1 | 0 | 71.43 | 71.43 (+0.0) | not run | 71.43 | 71.43 |
| sdlc | claude-sonnet-5-5 | 8 | 0 | 0 | 75.00 | 75.00 (+0.0) | not run | 75.00 | 75.00 |
| coop-git | claude-sonnet-5-5 | 8 | 0 | 0 | 62.50 | 75.00 (+12.5) | not run | 75.00 | 75.00 |
| team | claude-sonnet-5-5 | 8 | 0 | 0 | 37.50 | 62.50 (+25.0) | not run | 62.50 | 75.00 |

naive_ok, union_ok and llm_ok are exclusive and the percentages cumulative, as the legacy aggregation computes them, over the pairs whose cascade finished (the paper's are over all 652). A cascade that errored (its own sandbox failed, or it was cut short) or did not run (the pair or CooperBench's evaluation ended in error) is counted apart and in no percentage: it says nothing about the merge. The two verdicts are the paper era's rule (naive, union, resolver; no lead-alone fallback) and 63b9d44's, eval.json's both_passed, never mixed.
The learned resolver did not run: no resolver endpoint: set COORDBENCH_RESOLVER_URL to an OpenAI-compatible API base serving the paper's learned resolver (Qwen2.5-Coder-0.5B-Merge-Resolver); coordbench runs no model server; not needed: the naive merge was clean; no resolver endpoint: set COORDBENCH_RESOLVER_URL to an OpenAI-compatible API base serving the paper's learned resolver (Qwen2.5-Coder-0.5B-Merge-Resolver); coordbench runs no model server; not needed: the naive merge was clean; not needed: the union merge passed both suites; not needed: the naive merge was clean.

Paper, Table 6 (Naive / +Union / +LLM, % of 652): Claude 4.5 no comm 12.27 / 26.84 / 27.30, with comm 16.72 / 24.85 / 25.92 (delta -1.4); GPT-5 13.88 / 26.69 / 27.91 and 20.42 / 26.64 / 27.90; average delta -0.5.

Each agent's patch alone against both suites (agents may redivide the features, footnote 1; both patches passing the same suite is a free sign of duplicated work):

| Arm | Model | Pairs | First agent alone passes both | Second agent alone passes both | Both pass the same suite |
| --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 7 | 1 | 1 | 1 |
| sdlc | claude-sonnet-5-5 | 8 | 6 | 5 | 7 |
| coop-git | claude-sonnet-5-5 | 8 | 6 | 6 | 7 |
| team | claude-sonnet-5-5 | 8 | 6 | 0 | 6 |

## Communication

| Arm | Model | Conversations | With text | Turns (mean) | Speaker runs (mean) | Overhead: sends / tool calls | Sends and receives / tool calls | Sends and their results / calls and results | Pairs with a transcript |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 8 | 8 | 6.1 | 3.9 | 17.6% | 41.2% | 17.5% | 8 |
| sdlc | claude-sonnet-5-5 | 8 | 8 | 3.8 | 3.1 | 8.8% | 22.1% | 8.9% | 8 |
| coop-git | claude-sonnet-5-5 | 8 | 8 | 5.6 | 4.6 | 12.5% | 29.4% | 12.5% | 8 |
| team | claude-sonnet-5-5 | 8 | 8 | 4.1 | 3.9 | 10.0% | 19.9% | 10.0% | 8 |

A turn is one sent message, one per send call (a broadcast is one turn; a team's request and its response are one each, as the overhead counts them), from the conversation the judge labels (events.jsonl, else conversation_store.json, else CooperBench's conversation.json); speaker runs count the changes of speaker instead. The overhead pools the pairs: the sending calls (send_message, broadcast, and the team's request and respond) and the receiving ones (receive_messages, wait_for_message) as the hub counted them, each alias of a chained shell command apart, or as the transcripts show them, over every tool call of both agents' own turns (subagents left out). The paper counts OpenHands events (actions and observations), so neither basis is its unit.

Paper, Fig 6a (average turns): claude 14.5, gpt-5 15.5, minimax 10.8, qwen 3.2, qwen coder 1.1. Fig 5c (comm events, % of all events): claude 20.0%, gpt-5 16.3%, minimax 13.6%, qwen 6.2%, qwen coder 3.3%.

## Speech acts

Each act's count, and its share of the agents' tool calls over the conversations that have a transcript (Fig 5c splits the overhead so):

| Arm | Model | Labels | Conversations | With a transcript | Plan (% of tool calls) | Question (% of tool calls) | Answer (% of tool calls) | Update (% of tool calls) | Ack (% of tool calls) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 8 | 8 | 9 (3.2%) | 11 (3.9%) | 5 (1.8%) | 11 (3.9%) | 2 (0.7%) |
| sdlc | claude-sonnet-5-5 | keyword baseline | 8 | 8 | 16 (4.7%) | 0 (0.0%) | 1 (0.3%) | 0 (0.0%) | 1 (0.3%) |
| coop-git | claude-sonnet-5-5 | keyword baseline | 8 | 8 | 7 (1.9%) | 9 (2.5%) | 4 (1.1%) | 8 (2.2%) | 1 (0.3%) |
| team | claude-sonnet-5-5 | keyword baseline | 8 | 8 | 15 (4.5%) | 0 (0.0%) | 4 (1.2%) | 9 (2.7%) | 5 (1.5%) |

Plan:Question, by naive-merge conflict, as the ratio of totals and the mean of per-trajectory ratios (a trajectory with no Question left out of the mean):

| Arm | Model | Labels | Pairs without conflict | Ratio of totals | Mean of ratios | Pairs with conflict | Ratio of totals | Mean of ratios |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 8 | 0.82 | 0.83 | 0 | - | - |
| sdlc | claude-sonnet-5-5 | keyword baseline | 8 | - | - | 0 | - | - |
| coop-git | claude-sonnet-5-5 | keyword baseline | 7 | 1.00 | 0.75 | 1 | 0.00 | 0.00 |
| team | claude-sonnet-5-5 | keyword baseline | 4 | - | - | 4 | - | - |

First-turn planning: the conflict rate when the conversation's first message is a Plan and when it is not, and, as a variant, when every agent's first message is a Plan (Fisher's exact test):

| Arm | Model | Labels | Pairs | First message a Plan | Otherwise | Fisher p | Every agent opens with a Plan | Otherwise | Fisher p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | keyword baseline | 8 | 0/4 (0.0%) | 0/4 (0.0%) | 1.000 | 0/3 (0.0%) | 0/5 (0.0%) | 1.000 |
| sdlc | claude-sonnet-5-5 | keyword baseline | 8 | 0/8 (0.0%) | 0/0 (-) | 1.000 | 0/8 (0.0%) | 0/0 (-) | 1.000 |
| coop-git | claude-sonnet-5-5 | keyword baseline | 8 | 0/1 (0.0%) | 1/7 (14.3%) | 1.000 | 0/1 (0.0%) | 1/7 (14.3%) | 1.000 |
| team | claude-sonnet-5-5 | keyword baseline | 8 | 4/8 (50.0%) | 0/0 (-) | 1.000 | 3/7 (42.9%) | 1/1 (100.0%) | 1.000 |

By difficulty bucket (App C's ten; the paper's eight are undefined), every arm pooled:

| Labels | Bucket midpoint | First message a Plan | Otherwise | Fisher p |
| --- | --- | --- | --- | --- |
| keyword baseline | 0.05 | 3/12 (25.0%) | 1/8 (12.5%) | 0.619 |
| keyword baseline | 0.95 | 1/9 (11.1%) | 0/3 (0.0%) | 1.000 |

Paper, Sec 5: Plan:Question 2.04 without conflict vs 1.31 with; a Plan in the first turn 29.4% conflict rate vs 51.5% without.

## Concrete references

| Arm | Model | Split | Pairs | Line references, mean | Mann-Whitney p | File paths, mean | Mann-Whitney p |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | passed / failed | 6 / 2 | 1.0 / 1.5 | 0.571 | 9.0 / 7.0 | 0.929 |
| coop | claude-sonnet-5-5 | no conflict / conflict | 8 / 0 | 1.1 / - | - | 8.5 / - | - |
| sdlc | claude-sonnet-5-5 | passed / failed | 6 / 2 | 0.2 / 0.0 | 1.000 | 7.2 / 5.5 | 0.571 |
| sdlc | claude-sonnet-5-5 | no conflict / conflict | 8 / 0 | 0.1 / - | - | 6.8 / - | - |
| coop-git | claude-sonnet-5-5 | passed / failed | 6 / 2 | 0.5 / 0.5 | 1.000 | 7.3 / 7.0 | 0.571 |
| coop-git | claude-sonnet-5-5 | no conflict / conflict | 7 / 1 | 0.4 / 1.0 | 0.375 | 7.6 / 5.0 | 0.625 |
| team | claude-sonnet-5-5 | passed / failed | 6 / 2 | 0.0 / 0.0 | 1.000 | 8.2 / 8.5 | 0.607 |
| team | claude-sonnet-5-5 | no conflict / conflict | 4 / 4 | 0.0 / 0.0 | 1.000 | 9.2 / 7.2 | 0.971 |

Regex counts over the message text, per trajectory: line references ('line N', 'lines N-M', 'L<N>', '<path>:<N>') and file paths (a token ending in a source extension). Both splits are given, since the paper does not say whether 'successful' means passing or avoiding conflict.

Paper, Sec 5: successful trajectories 32.6 vs 22.5 line-number mentions and 13.1 vs 10.0 file-path mentions ('successful' and the extraction unstated).

## Coordination

The claim protocol's counters, summed over the pairs that recorded them (the sdlc arm):

| Arm | Model | Pairs | Claims | Pull requests opened | Merge attempts | Merges | Merge conflicts | Conflicts / attempts | Pairs where every agent merged | Pairs where one did not | Seconds to first merge (median) | Pairs with a merge |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sdlc | claude-sonnet-5-5 | 8 | 16 | 16 | 16 | 16 | 0 | 0.0% | 8 | 0 | 105.9 | 8 |

The team harness's board, summed over the pairs that recorded it (the team arm):

| Arm | Model | Pairs | Tasks total | Tasks done | Unowned at the end | Done / total | Seconds to first claim (median) | Pairs with a claim | Claims per pair | Updates per pair |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| team | claude-sonnet-5-5 | 8 | 16 | 16 | 0 | 100.0% | 31.3 | 8 | 1.62 | 3.50 |

Each count is a pair's arm metrics (result.json's metrics block, else metrics.json) summed over the arm's pairs whose agents all finished and that hold every column's key, scored or not; a pair an agent left in Error, or that failed before its agents ran, is left out, as the rates set them apart. A merge attempt ends in a merge, a merge conflict (the default branch moved under the gate and the branch conflicts with it) or a refusal: a main that moved without a conflict, a gate failure or an attempt that crashed count with the refusals, which are the rest. A pair where every agent merged is one where each agent landed a merge, by the hub's merge events (with an open queue one agent may merge every issue). Seconds to the first merge count from the hub's start of the pair, before its agents were provisioned; seconds to the first claim from the first task's creation. The team's claims and updates per pair sum its claims_per_agent and updates_per_agent. These are the protocol's own counts, which the paper has no figure for; the emergent rates stand in the sections above (PARITY row 97).

## Failure modes

Judge (backend / model / prompt variant): none called, the free heuristics only.

Free cross-checks, pairs where each found something (beside the labels, never replacing them):

| Arm | Model | Pairs | clean_merge_failed | contradictions | git_broken | git_destructive | git_disasters | git_impatience | ignored_questions | insertion_markers | missing_final_status | near_duplicates | negotiation_candidates | overlapping_hunks | resource_division_candidates | unanswered_questions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 8 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 5 | 1 | 1 |
| sdlc | claude-sonnet-5-5 | 8 | 0 | 1 | 0 | 0 | 0 | 8 | 0 | 0 | 5 | 0 | 0 | 8 | 0 | 0 |
| coop-git | claude-sonnet-5-5 | 8 | 0 | 1 | 2 | 0 | 8 | 8 | 0 | 0 | 4 | 0 | 0 | 8 | 1 | 0 |
| team | claude-sonnet-5-5 | 8 | 1 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 8 | 0 | 0 |

## Difficulty (App C)

1 Solo model(s): claude-sonnet-5-5; d takes 2 distinct value(s) over 8 pair(s).
With one or two models d is coarse ({0, 1} or {0, 0.5, 1} with one run each), so at most three buckets fill: the curves, areas and retention are degenerate and not comparable with Table 5's five models.
d~ is rescaled over the pairs scored here, not the paper's 652; the paper's per-pair Solo outcomes are unpublished, so these pairs cannot be placed on its scale.

| Arm | Model | Pairs | Filled buckets | Limits | AUC Solo | AUC arm | Delta AUC | Retention |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 8 | 2 | 0.05-0.95 | 0.450 | 0.600 | -0.150 | 1.33 |
| sdlc | claude-sonnet-5-5 | 8 | 2 | 0.05-0.95 | 0.450 | 0.600 | -0.150 | 1.33 |
| coop-git | claude-sonnet-5-5 | 8 | 2 | 0.05-0.95 | 0.450 | 0.600 | -0.150 | 1.33 |
| team | claude-sonnet-5-5 | 8 | 2 | 0.05-0.95 | 0.450 | 0.600 | -0.150 | 1.33 |

Filled buckets, Solo and arm passes with their 95% Wilson intervals:

| Arm | Model | Midpoint | Pairs | Solo | Wilson | Arm | Wilson |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 0.05 | 5 | 5/5 | 56.6-100.0% | 5/5 | 56.6-100.0% |
| coop | claude-sonnet-5-5 | 0.95 | 3 | 0/3 | 0.0-56.2% | 1/3 | 6.1-79.2% |
| sdlc | claude-sonnet-5-5 | 0.05 | 5 | 5/5 | 56.6-100.0% | 5/5 | 56.6-100.0% |
| sdlc | claude-sonnet-5-5 | 0.95 | 3 | 0/3 | 0.0-56.2% | 1/3 | 6.1-79.2% |
| coop-git | claude-sonnet-5-5 | 0.05 | 5 | 5/5 | 56.6-100.0% | 5/5 | 56.6-100.0% |
| coop-git | claude-sonnet-5-5 | 0.95 | 3 | 0/3 | 0.0-56.2% | 1/3 | 6.1-79.2% |
| team | claude-sonnet-5-5 | 0.05 | 5 | 5/5 | 56.6-100.0% | 5/5 | 56.6-100.0% |
| team | claude-sonnet-5-5 | 0.95 | 3 | 0/3 | 0.0-56.2% | 1/3 | 6.1-79.2% |

Paper, Table 5 (AUC Solo / Coop, Delta, retention): gpt-5 0.506 / 0.325, 0.181, 0.64; claude 0.469 / 0.283, 0.186, 0.60; minimax 0.374 / 0.171, 0.203, 0.46; qwen coder 0.236 / 0.148, 0.088, 0.63; qwen 0.106 / 0.072, 0.034, 0.68; pooled 0.338 / 0.200, 0.138, 0.59.

## Dataset

Conflicting gold solutions: 499 of 652 (76.5%) in CooperBench's dataset/gold_conflict_report.json, against the paper's 77.3% on the paper-era dataset; 6 of the 8 pairs these runs cover.

Pass rate, errors as failures:

| Arm | Model | All pairs | Features unamended | Features and task unamended | Audit both OK | Audit any SPEC | Audit any TEST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| solo | claude-sonnet-5-5 | 5/8 (62.5%) | 4/7 (57.1%) | 2/3 (66.7%) | 4/7 (57.1%) | 1/1 (100.0%) | 0/0 (-) |
| coop | claude-sonnet-5-5 | 6/8 (75.0%) | 5/7 (71.4%) | 2/3 (66.7%) | 5/7 (71.4%) | 1/1 (100.0%) | 0/0 (-) |
| sdlc | claude-sonnet-5-5 | 6/8 (75.0%) | 5/7 (71.4%) | 2/3 (66.7%) | 5/7 (71.4%) | 1/1 (100.0%) | 0/0 (-) |
| coop-git | claude-sonnet-5-5 | 6/8 (75.0%) | 5/7 (71.4%) | 2/3 (66.7%) | 5/7 (71.4%) | 1/1 (100.0%) | 0/0 (-) |
| team | claude-sonnet-5-5 | 6/8 (75.0%) | 5/7 (71.4%) | 2/3 (66.7%) | 5/7 (71.4%) | 1/1 (100.0%) | 0/0 (-) |

Naive-merge conflict rate:

| Arm | Model | All pairs | Features unamended | Features and task unamended | Audit both OK | Audit any SPEC | Audit any TEST |
| --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | 0/8 (0.0%) | 0/7 (0.0%) | 0/3 (0.0%) | 0/7 (0.0%) | 0/1 (0.0%) | 0/0 (-) |
| sdlc | claude-sonnet-5-5 | 0/8 (0.0%) | 0/7 (0.0%) | 0/3 (0.0%) | 0/7 (0.0%) | 0/1 (0.0%) | 0/0 (-) |
| coop-git | claude-sonnet-5-5 | 1/8 (12.5%) | 0/7 (0.0%) | 0/3 (0.0%) | 0/7 (0.0%) | 1/1 (100.0%) | 0/0 (-) |
| team | claude-sonnet-5-5 | 4/8 (50.0%) | 3/7 (42.9%) | 1/3 (33.3%) | 3/7 (42.9%) | 1/1 (100.0%) | 0/0 (-) |

Each headline rate again over the pairs whose two features' feature.md, feature.patch and tests.patch are as in the paper's dataset (59bbf65), then with the task's runner.sh, Dockerfile and other files unchanged too, and by the spec audit's verdicts (OK; SPEC: feature.md amended; TEST: tests.patch amended).

## Against CooperBench's published runs

| Arm | Model | Published model | Pairs in both | Our rate | Published rate | Only ours passed | Only theirs passed | Exact McNemar p | Our conflict rate | Published conflict rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| coop | claude-sonnet-5-5 | claude | 7 | 71.4% | 28.6% | 3 | 0 | 0.250 | 0.0% | 14.3% |
| coop | claude-sonnet-5-5 | gemini_flash_miniswe | 8 | 75.0% | 25.0% | 5 | 1 | 0.219 | 0.0% | 37.5% |
| coop | claude-sonnet-5-5 | gemini_flash_sdk | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 0.0% | 50.0% |
| coop | claude-sonnet-5-5 | gemini_pro_miniswe | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 0.0% | 25.0% |
| coop | claude-sonnet-5-5 | gpt5 | 6 | 66.7% | 50.0% | 2 | 1 | 1.000 | 0.0% | 16.7% |
| coop | claude-sonnet-5-5 | minimax | 5 | 60.0% | 20.0% | 2 | 0 | 0.500 | 0.0% | 0.0% |
| coop | claude-sonnet-5-5 | qwen | 7 | 71.4% | 14.3% | 4 | 0 | 0.125 | 0.0% | 28.6% |
| coop | claude-sonnet-5-5 | qwen_coder | 6 | 66.7% | 16.7% | 3 | 0 | 0.250 | 0.0% | 83.3% |
| sdlc | claude-sonnet-5-5 | claude | 7 | 71.4% | 28.6% | 3 | 0 | 0.250 | 0.0% | 14.3% |
| sdlc | claude-sonnet-5-5 | gemini_flash_miniswe | 8 | 75.0% | 25.0% | 5 | 1 | 0.219 | 0.0% | 37.5% |
| sdlc | claude-sonnet-5-5 | gemini_flash_sdk | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 0.0% | 50.0% |
| sdlc | claude-sonnet-5-5 | gemini_pro_miniswe | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 0.0% | 25.0% |
| sdlc | claude-sonnet-5-5 | gpt5 | 6 | 66.7% | 50.0% | 2 | 1 | 1.000 | 0.0% | 16.7% |
| sdlc | claude-sonnet-5-5 | minimax | 5 | 60.0% | 20.0% | 2 | 0 | 0.500 | 0.0% | 0.0% |
| sdlc | claude-sonnet-5-5 | qwen | 7 | 71.4% | 14.3% | 4 | 0 | 0.125 | 0.0% | 28.6% |
| sdlc | claude-sonnet-5-5 | qwen_coder | 6 | 66.7% | 16.7% | 3 | 0 | 0.250 | 0.0% | 83.3% |
| coop-git | claude-sonnet-5-5 | claude | 7 | 71.4% | 28.6% | 3 | 0 | 0.250 | 0.0% | 14.3% |
| coop-git | claude-sonnet-5-5 | gemini_flash_miniswe | 8 | 75.0% | 25.0% | 5 | 1 | 0.219 | 12.5% | 37.5% |
| coop-git | claude-sonnet-5-5 | gemini_flash_sdk | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 12.5% | 50.0% |
| coop-git | claude-sonnet-5-5 | gemini_pro_miniswe | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 12.5% | 25.0% |
| coop-git | claude-sonnet-5-5 | gpt5 | 6 | 66.7% | 50.0% | 2 | 1 | 1.000 | 0.0% | 16.7% |
| coop-git | claude-sonnet-5-5 | minimax | 5 | 60.0% | 20.0% | 2 | 0 | 0.500 | 20.0% | 0.0% |
| coop-git | claude-sonnet-5-5 | qwen | 7 | 71.4% | 14.3% | 4 | 0 | 0.125 | 0.0% | 28.6% |
| coop-git | claude-sonnet-5-5 | qwen_coder | 6 | 66.7% | 16.7% | 3 | 0 | 0.250 | 0.0% | 83.3% |
| team | claude-sonnet-5-5 | claude | 7 | 71.4% | 28.6% | 3 | 0 | 0.250 | 42.9% | 14.3% |
| team | claude-sonnet-5-5 | gemini_flash_miniswe | 8 | 75.0% | 25.0% | 5 | 1 | 0.219 | 50.0% | 37.5% |
| team | claude-sonnet-5-5 | gemini_flash_sdk | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 50.0% | 50.0% |
| team | claude-sonnet-5-5 | gemini_pro_miniswe | 8 | 75.0% | 50.0% | 3 | 1 | 0.625 | 50.0% | 25.0% |
| team | claude-sonnet-5-5 | gpt5 | 6 | 66.7% | 50.0% | 2 | 1 | 1.000 | 50.0% | 16.7% |
| team | claude-sonnet-5-5 | minimax | 5 | 60.0% | 20.0% | 2 | 0 | 0.500 | 60.0% | 0.0% |
| team | claude-sonnet-5-5 | qwen | 7 | 71.4% | 14.3% | 4 | 0 | 0.125 | 42.9% | 28.6% |
| team | claude-sonnet-5-5 | qwen_coder | 6 | 66.7% | 16.7% | 3 | 0 | 0.250 | 50.0% | 83.3% |

The website's coop/index.json, rows with results only. Its paper models were scored by the legacy cascade (naive, union, resolver) and its Gemini rows by 63b9d44's both_passed, so the two published rules differ, and its stats cannot be recomputed from its rows.

## Every pair

| Pair | solo (claude-sonnet-5-5) | coop (claude-sonnet-5-5) | sdlc (claude-sonnet-5-5) | coop-git (claude-sonnet-5-5) | team (claude-sonnet-5-5) |
| --- | --- | --- | --- | --- | --- |
| dottxt_ai_outlines_task/1655/f6_f7 | fail | fail | fail | fail | fail |
| go_chi_task/56/f1_f5 | fail | pass N | pass N | pass N | pass N |
| huggingface_datasets_task/6252/f4_f6 | pass | pass N | pass N | pass N | pass L |
| pallets_click_task/2068/f5_f11 | pass | pass N | pass N | pass N | pass L |
| pallets_jinja_task/1559/f4_f10 | pass | pass N | pass N | pass N | pass N |
| pallets_jinja_task/1621/f2_f9 | pass | pass N | pass N | pass N | pass N |
| pillow_task/68/f1_f5 | fail | fail | fail | fail | fail |
| typst_task/6554/f5_f9 | pass | pass N | pass N | pass L | pass L |

Each cell is the pair's first attempt: pass or fail by eval.json, N when it passed on the naive merge or identical patches, L on the lead's patch alone, error when it has no eval.json, '-' when the arm did not run it.
