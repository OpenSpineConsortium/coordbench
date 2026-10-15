# Parity with CooperBench

The rule this file answers to: coordbench measures every metric
CooperBench measures, as close to CooperBench's way as it can, and
differs only for a stated reason, either because the difference is better
or because the two architectures cannot be made the same. Everything a
run can only capture while it is live is captured by the run itself;
everything computed afterwards from the logs is a separate command, and
the only ones that call a model are the ones the owner runs on purpose.

Three things are "CooperBench" here, and each row says which one it
means:

- the paper, "CooperBench: Why Coding Agents Cannot be Your Teammates Yet"
  (arXiv 2601.13295, dated 2026-01-27), whose runs came from CooperBench
  commit 59bbf65, the paper era. "L" numbers are lines of the paper's
  text, as the module docstrings cite them;
- CooperBench's code at 63b9d44, the code the owner runs and the one
  bench/cooperbench/run_arms.sh drives. Module paths are under its
  src/cooperbench/ unless they start with dataset/, scripts/ or docs/; a
  path marked 59bbf65 is at that commit;
- cooperbench.com's source and data: its viewer (src/pages/viewer.astro),
  its scripts/ and its published data under public/static/data.

"coordbench" in each row is the code on this branch, read, not the
reports of the people who wrote it: a file and the function that does it.

Status:

- same: computed or written as CooperBench does, often by calling
  CooperBench's own code, with anything coordbench adds kept beside it;
- equivalent: the same quantity reached another way or in another shape,
  where the row says what that changes;
- different: deliberately not as CooperBench does, for the reason given;
- not measured: not computed yet, for the reason given.

Rows 0 to 135 are the rows of the gap matrix this work was planned from,
in its order. A review of the matrix made 22 corrections, applied here in
23 rows (one correction covers both rows 110 and 121), each marked
"(corrected)" after its title, and added 13 rows, numbered M1 to M13. Row
71's "(corrected)" is no such mark: "C3b Incorrect claim (corrected)" is
App F's own name for the category. Each row sits in the matrix's
category; the review's additions are placed where they belong.

## Contents

- Summary
- Outcome
- Merge
- Statistics
- Time
- Cost
- Communication
- Failure modes
- Emergent behaviours
- Trajectory files
- Viewer
- Setup, dataset and agents
- Decisions
- Left undone
- Open questions only the authors can answer
- Producing each measure

## Summary

| Category | Rows | same | equivalent | different | not measured |
| --- | --- | --- | --- | --- | --- |
| Outcome | 12 | 9 | 1 | 1 | 1 |
| Merge | 13 | 9 | 2 | 1 | 1 |
| Statistics | 11 | 10 | 0 | 1 | 0 |
| Time | 7 | 3 | 1 | 3 | 0 |
| Cost | 5 | 3 | 1 | 1 | 0 |
| Communication | 18 | 5 | 8 | 4 | 1 |
| Failure modes | 27 | 4 | 7 | 16 | 0 |
| Emergent behaviours | 5 | 0 | 0 | 4 | 1 |
| Trajectory files | 16 | 12 | 1 | 3 | 0 |
| Viewer | 14 | 8 | 1 | 4 | 1 |
| Setup, dataset and agents | 21 | 5 | 2 | 11 | 3 |
| All | 149 | 68 | 24 | 49 | 8 |

Rows 77 to 86, the ten symptoms, are counted one by one; a row with two
statuses (same under one driver, different under another) is counted by
the first it names.

## Outcome

**0. Pair success under 63b9d44's rule (both_passed).** Status: same.
- CooperBench: eval/sandbox.py test_merged (93-302): identical patches
  short-circuit; otherwise a naive git merge, authoritative when clean; on
  a conflict or a patch that did not apply, agent1's patch (the lower
  feature, the lead in team mode) alone against both suites (strategy
  solo-agent1); both_passed = feature1.passed and feature2.passed. Solo:
  test_solo.
- coordbench: coordbench/run.py run_cooperbench_tests calls CooperBench's
  own test_merged or test_solo, in CooperBench's virtualenv
  (cooperbench_test, EVALUATION_SCRIPT); evaluation_record writes eval.json
  with _evaluate_single's keys in its order; score().

**1. Pair success under the paper's rule (naive, then union, then the
learned resolver; no lead-alone fallback).** Status: different.
- CooperBench: paper Sec 2.2 (L247-278) and App B (L1383-1384); 59bbf65
  evaluation/evaluate.py run_merge_evaluation (naive, union, LLM) and
  _run_multi_aggregation (passed = naive_ok or union_ok or llm_ok). The
  website's generate_coop_index.py applies the same cascade.
- coordbench: coordbench/cascade.py run(), called by run.py score() through
  cascade_block, adds a "cascade" block to eval.json and never touches
  CooperBench's keys: verdict_63b9d44 (eval.json's both_passed),
  verdict_paper_era and verdict_paper_era_regex, route (naive, union, llm
  or none), lower_bound, basis (naming the dataset's commit) and
  deciding_artifact per verdict. It runs in a sandbox of its own through
  CooperBench's own backend and helpers (_setup_branches, _merge_naive,
  _merge_union, _run_tests), since test_merged ends its sandbox before it
  returns.
- Why: three parts are not the paper era's. The learned resolver needs a
  model server coordbench does not run (row 58), so a pair the resolver
  might have saved fails and lower_bound says so. The test-file filter
  and the pass rule are 63b9d44's, so both verdicts judge the same merged
  artifacts; 59bbf65's regex rule is recorded beside (the _regex keys) and
  each filter's drops are listed (row 62). The dataset is the checkout's
  unless --dataset-dir names a 59bbf65 one (M1). Two verdicts are kept,
  never mixed: 63b9d44's is what the owner's baseline measures, the
  cascade's is what produced every published number.

**2. Feature passed (featureN.passed) (corrected).** Status: same as
63b9d44; the paper era's rule is recorded beside it.
- CooperBench: eval/sandbox.py _run_tests (585-616): passed = exit code 0
  and a parsed passed count above 0. On a conflict or missing_input,
  featureN is a placeholder (passed False, 0 tests) unless patch1 alone
  passes both suites (255-256, 264-269), so per-feature rates from eval.json
  mean something only on clean or identical merges. The paper era's rule
  (59bbf65 test_runner.py _parse_test_output, 103-124) ignores the exit
  code and reads pass and failure words.
- coordbench: the same through test_merged. bench/cooperbench/report.py
  route_section counts per-feature passes over clean and identical merges
  only. coordbench/cascade.py test_record keeps, for every test run of
  every step, passed (63b9d44's rule) and passed_regex (legacy_passed,
  59bbf65's regex).

**3. tests_passed, tests_failed, exit_code, test_output per feature.**
Status: same.
- CooperBench: eval/sandbox.py _parse_results (619-669); the identical-patch
  path keeps _run_tests' raw keys ("output", no feature_id). 59bbf65 kept
  one output per merge step.
- coordbench: eval.json through test_merged; coordbench/cascade.py keeps a
  test_output per step (naive, union, llm, each agent alone) in each
  step's tests block.

**4. apply_status {agent1, agent2}.** Status: same.
- CooperBench: eval/sandbox.py _setup_branches (439-512): applied (git
  apply, else --3way), skipped (empty patch), failed.
- coordbench: eval.json from test_merged (run.py evaluation_record); the
  cascade records its own apply_status from the same helper.

**5. Solo success.** Status: same.
- CooperBench: paper Sec 4 (L470-484), Fig 4 Left, Table 5; eval/sandbox.py
  test_solo; eval.json setting solo, merge null.
- coordbench: coordbench/arms/solo/arm.py (CooperBench's prompt) and
  test_solo through run.py run_cooperbench_tests; evaluation_record writes
  merge null.

**7. Pass route (naive, identical, lead-alone).** Status: same.
- CooperBench: docs/BENCHMARK_RESULTS.md's N (naive merge or identical) and
  L (lead alone) per task, from eval.json merge.strategy.
- coordbench: bench/cooperbench/report.py route_section and route_rows
  count passes by strategy (naive, skip-merge-identical, solo-agent1) and
  the cascade's routes; route_letter puts N or L on each pair of the
  "Every pair" table.

**8. Coordination gap (Solo minus Coop).** Status: equivalent.
- CooperBench: paper Sec 4 (L476-484) and the Fig 4 caption (L459); not
  computed in any CooperBench code. The abstract's "30% lower" follows from
  none of the paper's numbers.
- coordbench: bench/cooperbench/report.py gap_section and gap_row: per
  multi-agent arm and model, the absolute gap (solo rate minus arm rate)
  and the relative gap (over the solo rate), with the exact McNemar p,
  beside the gaps derived from Fig 4 (REFERENCES["gap"]).
- What differs: the gap is taken on the pairs both arms scored, which
  pairs the comparison; the paper's rates are over all 652 per model.

**15. Scaling the number of agents (2, 3, 4) (corrected).** Status: not
measured.
- CooperBench: paper Sec 4 (L529-536): 46 tasks from 3 task sets, 68.6%,
  46.5%, 30.0%, model unstated, the set unpublished. 63b9d44 publishes
  3-agent sets: dataset/subsets/team3_250.json (250 triplets under
  "pairs") and team3_all.json (every triplet under "groups"); its
  eval/evaluate.py scores features[0] and features[1] only.
- coordbench: coordbench/run.py subset_entries reads both "pairs" and
  "groups", and the hub takes up to 16 features, so a triplet runs; but
  run_cooperbench_tests and cascade.skip_reason refuse anything but a
  pair ("CooperBench scores pairs of features only"), so it ends with an
  evaluation error.
- Left undone: an n-feature scorer built from test_merged's steps (one
  branch per patch, merged in feature order, every suite on the merged
  tree). It would be coordbench's own, since 63b9d44 has none, and it can
  be run afterwards on the patches a run keeps. Not built in this round.

**21. Per-agent status (Submitted / LimitsExceeded / Error) (corrected).**
Status: same.
- CooperBench: agents/claude_code/parsers.py (57-64): Submitted when the
  result event has is_error false and subtype success, LimitsExceeded when
  the subtype names max_turns, a limit or a budget, else Error, and Error
  without a result event. Its "error" is only an exception's message in
  the adapter (adapter.py 548-549, 588), null for an Error read from the
  result event.
- coordbench: coordbench/drivers.py status_of (a port), summarize_stream and
  CliRun.outcome. AgentOutcome.error is CooperBench's (the exception: a
  timeout, a container gone); coordbench's own account of how an agent
  that did not submit ended is end_reason (the result text, "the stream
  ended without a result event" and its last line, the timeout).
- The session driver reports Submitted when the agent's marker appears and
  Error when it does not; a subagent has no result event, so
  LimitsExceeded cannot be told apart there, an architectural limit of
  that driver.

**22. patch_lines.** Status: same.
- CooperBench: len(patch.splitlines()) before test-file filtering (coop.py
  232, solo.py 127, team.py 323).
- coordbench: coordbench/run.py cooperbench_agent_fields, the same formula.

**M5. Git-access ablation table (coop-git against coop, with Solo and the
gaps).** Status: same.
- CooperBench: the "Marginal Improvement" table of the blog post
  (src/content/blog/the-curious-case-of-miscoordination.mdx): per model
  and framework the git and no-git rates, Solo, both gaps and the
  improvement, called "not statistically significant".
- coordbench: bench/cooperbench/report.py git_section and git_rows: per
  model the coop, coop-git and solo rates, both gaps, the improvement and
  an exact McNemar p on the pairs both arms scored, beside the blog's
  numbers (REFERENCES["git"]). The test is coordbench's; the blog names
  none.

## Merge

**18. Share of tasks with conflicting gold solutions (corrected).**
Status: same.
- CooperBench: paper Sec 2.1 (L231-232): 77.3% (504 of 652). The code
  measures it itself: scripts/check_gold_conflicts.py writes
  dataset/gold_conflict_report.json, whose summary at 63b9d44 is 499
  conflicts of 652, "76.5%". That script applies with git apply
  --ignore-whitespace, then --3way, and an older test-file filter
  (check_gold_conflicts.py 58-73, 154-168), so it is not exactly the
  scorer's merge.
- coordbench: bench/cooperbench/dataset_stats.py gold_conflicts reads the
  report as it is, per pair and per repository; report.py gold_line sets
  76.5% beside the paper's 77.3%. The gap is put down to the dataset's
  changes since 59bbf65 (M1) until it is checked.
- Left undone: recomputing the share with the scorer's own _setup_branches
  and _merge_naive, which would show what the scorer itself sees. Free
  compute; not built.

**53. Naive merge command.** Status: equivalent.
- CooperBench: the paper's text says git merge-file (Sec 2.2 L252); both
  code generations run `git merge --no-commit --no-ff` (59bbf65
  core/merge.py merge(); 63b9d44 eval/sandbox.py _merge_naive).
- coordbench: 63b9d44's through test_merged, and cascade.py's naive_step
  calls the same _merge_naive.
- What differs: only against the paper's text; coordbench follows the code
  that produced the numbers. Which one made Table 6 is an open question.

**54. Naive merge conflict flag (corrected).** Status: equivalent.
- CooperBench: 59bbf65 aggregated_results has_naive_merge_conflict; the
  website's index.json hasConflict; 63b9d44 merge.status "conflicts", with
  missing_input taking precedence (eval/sandbox.py 225-232).
- coordbench: eval.json merge.status as 63b9d44 writes it; cascade.py fill
  sets has_naive_merge_conflict from the merge itself, null when a patch
  did not apply, since that merge is not a merge of the two agents' work;
  export.py has_conflict gives the index's hasConflict, null for an error
  or missing_input.
- What differs: nothing in eval.json; the cascade's own flag is null
  where a patch did not apply, since that merge is not the agents' work.

**55. Conflict rate (corrected).** Status: same.
- CooperBench: paper Fig 5(b). The with-communication values reproduce
  exactly from the website's coop/index.json hasConflict over the rows
  that have a merge report: claude 122/607 = 20.1%, gpt-5 156/571 =
  27.3%, minimax 54/340 = 15.9%, qwen 257/607 = 42.3%, qwen coder 376/536
  = 70.1%. The paper's denominator is therefore the pairs with a merge
  report, not 652. The no-communication values can only be read off the
  figure.
- coordbench: bench/cooperbench/report.py conflict_section and
  conflict_rows: per arm and model, the share of scored pairs whose
  naive-merge result is known (a finished cascade's
  has_naive_merge_conflict, else eval.json merge.status; a patch that did
  not apply, or missing_input, is unknown with or without a cascade, as
  export.py naive_conflict reads it too), with Wilson
  intervals, and conflict_comparison_rows: exact McNemar between arms of
  one model (coop against coop-nocomm is Fig 5(b)). The exact references
  are REFERENCES["conflict"].

**56. Conflict size and severity.** Status: same.
- CooperBench: 59bbf65 core/merge.py _analyze_diff_content (296-334) and
  _calculate_conflict_score (20 per section, 2 per line), reporter.py
  _get_severity_level (CRITICAL above 100, HIGH above 50, MEDIUM above 20,
  LOW above 0, else NONE). 63b9d44 dropped them.
- coordbench: coordbench/cascade.py conflict_details, conflict_score and
  severity, line for line, over conflicted_diff: the merge redone and left
  conflicted long enough for `git diff`, as 59bbf65's merge() read it.

**57. Union merge step.** Status: same.
- CooperBench: 59bbf65 core/merge.py merge_union ('* merge=union' in
  .gitattributes), run only when naive conflicts. 63b9d44 keeps
  _merge_union (eval/sandbox.py 547-582) but no longer calls it.
- coordbench: coordbench/cascade.py union_step calls CooperBench's own
  _merge_union when the naive merge conflicted and tests both suites on
  its result.

**58. Learned LLM merge resolver step.** Status: not measured.
- CooperBench: paper App B (L1376-1406): Qwen2.5-Coder-0.5B-Merge-Resolver
  (Sec 2.2 L256 says Qwen 3 Coder 1.5B), five lines of context,
  temperature 0; 59bbf65 evaluation/llm_merge.py (SYSTEM_PROMPT, an
  OpenAI-compatible server at localhost:8457). Absent at 63b9d44.
- coordbench: coordbench/cascade.py llm_step is the plug point. With
  COORDBENCH_RESOLVER_URL naming an OpenAI-compatible API base
  (COORDBENCH_RESOLVER_MODEL, COORDBENCH_RESOLVER_KEY) it re-merges,
  resolves each region with 59bbf65's SYSTEM_PROMPT word for word, five
  lines of context, temperature 0, three retries and its splice
  (conflict_regions, apply_resolutions), commits and tests. Without one
  the block says llm {ran: false, needed, reason} and lower_bound is set.
- Left undone: running it, by decision 7: there is no model server here and
  the Hugging Face host is blocked.

**59. Merge success by strategy, cumulative (Naive, +Union, +LLM), with
and without communication.** Status: same, with the +LLM column a lower
bound until a resolver runs.
- CooperBench: paper App E Table 6; 59bbf65 _run_multi_aggregation (naive,
  union and LLM exclusive, percentages cumulative).
- coordbench: bench/cooperbench/report.py cascade_section and cascade_rows:
  per arm and model the cumulative Naive, +Union and +LLM shares, both
  verdict rules apart, coop beside coop-nocomm (comm_effect_lines), and
  the reason the LLM step did not run (llm_reason). The shares are over
  the pairs whose cascade finished (cascade_finished: ran, no error, its
  naive step there); a cascade that errored or did not run is counted in
  its own columns and in no share, since 59bbf65's aggregation never
  counted a harness failure as a merge failure, and the 63b9d44 column is
  eval.json's both_passed over the same pairs.

**60. Lead-alone fallback (strategy solo-agent1).** Status: same.
- CooperBench: 63b9d44 only (eval/sandbox.py 248-269); the paper fails both
  features when no conflict-free merge exists (L272-273).
- coordbench: inherited in both_passed; verdict_paper_era never uses it;
  cascade.py agent_alone_step tests agent1's patch alone on every pair, so
  the fallback's own result is there whether or not 63b9d44 used it.

**61. Identical-patch short-circuit.** Status: same.
- CooperBench: eval/sandbox.py 182-218 (status identical, strategy
  skip-merge-identical).
- coordbench: inherited through test_merged.

**62. Test files stripped from agent patches (corrected).** Status: same
as 63b9d44; the paper era's filter is recorded, not applied.
- CooperBench: 63b9d44 eval/sandbox.py _filter_test_files (676-714), whose
  comment (672-675) says the rule before it "only knew Python
  (_test.py)". 59bbf65 kept only a patch's code files (core/patch.py
  _filter_patch); check_gold_conflicts.py keeps another, older list.
- coordbench: test_merged's filter. coordbench/cascade.py uses 63b9d44's
  filter for both verdicts and records, per agent, what each filter drops
  (filter_comparison, legacy_dropped over 59bbf65's patterns) in
  dropped_by_filter, with filters_agree.
- Why: both verdicts then judge the same merged artifacts, so a difference
  between them is the cascade's and not the filter's; the pairs where the
  filters disagree are named.

**63. Merged artifacts kept on disk.** Status: same.
- CooperBench: 63b9d44 keeps merge.diff, its first 5000 characters, in
  eval.json; 59bbf65 saved the naive, union and LLM diffs.
- coordbench: eval.json merge.diff as 63b9d44; coordbench/cascade.py
  write_artifacts writes merge_naive.diff (the conflicted diff with its
  markers when it conflicted), merge_union.diff and merge_llm.diff whole,
  beside eval.json, and removes one a later scoring did not make.

**M11. Redivision of features: each agent's patch against both suites.**
Status: different.
- CooperBench: paper footnote 1 (L257-259) lets agents redivide the two
  features; 63b9d44 tests patch1 alone only after a conflict or
  missing_input, and drops what it finds unless both suites pass.
- coordbench: coordbench/cascade.py agent_alone_step and alone: each
  agent's patch alone against both suites, four runs, in every pair;
  report.py alone_rows and alone_lines say when the agents redivided and
  when both did the same work (both patches pass one suite).
- Why: better; it changes no verdict and shows redivision, duplicated work
  (a free signal for Work overlap) and lead against member alone.

## Statistics

**6. Success-rate denominator (corrected).** Status: same.
- CooperBench: the paper's rate is k/652 per model, every task counted; the
  website counts a missing run as a failure (generate_coop_index.py
  155-162). 63b9d44's summary.json pass_rate is passed / max(passed +
  failed, 1), leaving eval errors out (runner/core.py 610, 617), while its
  console line (core.py 652-664) and scripts/gen_ablation_report.py (100)
  divide by passed + failed + errors. An errored agent's patch is still
  evaluated and counted by eval.json (core.py 473-480, 333-338).
- coordbench: bench/cooperbench/report.py rates_section and rate_rows: the
  first rate is eval.json both_passed over every attempted pair, a pair
  with no eval.json or an eval error failing and the agents' status
  ignored, but for a run whose audit.json (the session route's audit)
  names a harness error, left out of every rate and listed apart, with a Wilson interval and a bootstrap over task sets;
  cooperbench_rates_section prints CooperBench's three rates apart; the
  finished-pairs rate beside them is coordbench's, labelled, which sets a
  pair an agent left in Error apart, since a harness error is not a
  coordination outcome. coordbench/runconfig.py summary_record writes
  pass_rate by _save_summary's formula and pass_rate_over_evaluated by the
  console's.

**9. Relative difficulty d(t) and the rescaled d~(t).** Status: same
(method); not comparable with Table 5.
- CooperBench: paper Sec 4 (L489-491, L522-523), App C (L1409-1416): d(t) =
  1 - (1/|M|) sum over m of Solo_m(t), min-max rescaled over the
  benchmark. No code at any commit.
- coordbench: bench/cooperbench/difficulty.py difficulty() and
  solo_success(): Solo_m(t) is the share of model m's scored Solo runs of t
  that passed (binary with one run, a fraction with repeated runs; a run
  the session route's audit.json names a harness error is not scored); d
  over the models that scored the pair; d~ over the pairs scored here; written
  to difficulty.json with the method (METHOD) and notes().
- Not comparable: with the Claude models of one subscription |M| is 1 or 2,
  so d takes {0, 1} or {0, 0.5, 1}, at most three buckets fill, and the
  output says so (decision 8). The paper's per-pair Solo outcomes are
  unpublished, so no pair can be placed on its scale.

**10. Difficulty-stratified success curves (10 buckets, midpoints, Wilson
per bucket).** Status: same (method).
- CooperBench: App C Algorithm 1 (L1435-1464), Fig 4 Right, Fig 7.
- coordbench: bench/cooperbench/difficulty.py curve, curve_counts and
  with_rates: per arm and model, against that model's Solo on the pairs
  both scored, rows {bucket, midpoint, n, k, rate, Wilson} with empty
  buckets kept at n = 0; report.py difficulty_section and bucket_rows.

**11. AUC_Solo and AUC_Coop (trapezoidal).** Status: same (method).
- CooperBench: App C (L1419); Table 5. The limits and the handling of empty
  buckets are unstated.
- coordbench: bench/cooperbench/difficulty.py area and summary: the
  trapezoid over the filled midpoints, first to last, none with fewer than
  two; the limits and the number of filled buckets are written beside
  every area.

**12. Delta AUC.** Status: same.
- CooperBench: App C (L1420); Table 5.
- coordbench: bench/cooperbench/difficulty.py summary (delta_auc).

**13. Retention (AUC_Coop / AUC_Solo).** Status: same.
- CooperBench: App C (L1421-1422); Table 5; "41% of Solo capability is
  lost" (L1496-1497).
- coordbench: bench/cooperbench/difficulty.py summary (retention), per
  multi-agent arm against Solo.

**14. Pooling across models by summing counts.** Status: same.
- CooperBench: App C (L1423-1424), Algorithm 1 lines 13-17.
- coordbench: bench/cooperbench/report.py make_groups groups by arm label
  and result.json model, and pooled_groups adds a pooled row that sums the
  pairs; bench/cooperbench/difficulty.py pooled sums the counts per
  bucket.

**89. Annotator validation against humans (48/50, Wilson [86%, 99%]).**
Status: same (procedure); it needs human time.
- CooperBench: App G (L1715-1718): 50 trajectories sampled, human labels
  matched the judge on 48; the "matched" basis is unstated.
- coordbench: coordbench/annotate.py command_validate (a seeded draw of 50
  judged pairs, blind packets, the rubric the judge used, sheet.json) and
  command_agreement (agreement on the level and on the primary symptom
  with Wilson intervals, both since the basis is unstated, and Cohen's
  kappa when a second human's sheet is given); VALIDATION_REFERENCE.

**124. Wilson 95% interval for rates (corrected).** Status: same.
- CooperBench: paper Sec 4 (L484, "computed over task sets") and App C
  (L1416-1418). The leaderboard's typed-in intervals are approximately
  Wilson over 652: claude [22.66, 29.37] against 169/652's [22.70, 29.42],
  minimax [11.5, 16.8] against [11.51, 16.83], solo gpt-5 [44.5, 52.2]
  against [44.50, 52.15]. Only GPT-5 Coop (27.95 [24.48, 31.65] against
  182/652's [24.61, 31.48]) and gemini_flash_sdk are off by more than 0.1.
- coordbench: bench/cooperbench/report.py wilson (difficulty.wilson, z
  1.96) beside bootstrap_cell and bootstrap_rate, a cluster bootstrap over
  task sets (2000 resamples, seeded), for the paper's "over task sets".

**125. Significance tests.** Status: different.
- CooperBench: the paper names no test for "not statistically
  significant" (L547-548), "significantly reduce" (L560-561), first-turn
  planning (L577-578) or the reference counts (L580-581).
- coordbench: bench/cooperbench/report.py mcnemar_exact (paired arms, on
  success and on conflict, the git ablation and the published runs),
  fisher_exact (first-turn planning) and mann_whitney (reference counts,
  exact below 40 pairs), each p printed with its test.
- Why: the paper names none, so coordbench names one.

**126. Repeated runs (k).** Status: same.
- CooperBench: the legacy and website file names end in _k1, one run; no
  seeds stated.
- coordbench: bench/cooperbench/report.py takes a --run label more than
  once and pools its pairs; Group.first_attempts pairs the first attempt
  of each pair for the paired tests; difficulty.py solo_success turns
  repeated Solo runs into a fraction. The run-name suffix -k<n> is a
  convention, not enforced.

## Time

**30. duration_seconds (pair).** Status: different.
- CooperBench: coop.py (68, 163-164): from before the agents spawn to after
  every adapter thread returns, so each agent's container start, CLI
  install, run, harvest and cleanup and the git server; the evaluation is
  out.
- coordbench: coordbench/run.py cooperbench_result_fields: from the pair's
  plan (new_plan's started_at) to the agents' end, so the run's creation
  on the hub and the provisioning are in, close, teardown and scoring out;
  agents_seconds() adds the agents' phase alone, first start to last end.
- Why: architectural; coordbench provisions and tears down outside the
  agent threads, so CooperBench's stretch cannot be cut the same way. The
  phase timings (row 34) give each part on its own.

**31. Session wall time and get_run_totals.** Status: same.
- CooperBench: runner/core.py (224): summary.json total_time_seconds,
  inline evaluations included; utils.py get_run_totals (wall time, run
  time, task count).
- coordbench: coordbench/run.py command_run times the session as
  CooperBench does and write_run_summary passes it to
  coordbench/runconfig.py summary_record; run_totals is get_run_totals
  over every result.json of the run's setting. Under the session driver,
  refresh_summary uses run_totals' wall time and says so in
  total_time_source.

**32. Median task time and run wall (ablation report).** Status: same.
- CooperBench: scripts/gen_ablation_report.py (51-77): the median of
  duration_seconds; run wall from summary.json.
- coordbench: bench/cooperbench/report.py cost_section (mean and median
  minutes per pair) and runs_section ("Run wall", from summary.json
  total_time_seconds).

**33. Claude Code duration_ms and duration_api_ms per agent.** Status:
different.
- CooperBench: in the result event, kept only inside the parser's raw copy.
- coordbench: coordbench/drivers.py summarize_stream into result.json
  agents.<id>.duration_ms and api_duration_ms; null under the session
  driver, which sees no stream.
- Why: better; it separates model time from local time at no cost.

**34. Phase timings and the timeline breakdown.** Status: different.
- CooperBench: none.
- coordbench: coordbench/run.py PHASES, record_timing and timed (hub,
  run_create, git_remote, shared_volume, provision, agent, close, score,
  cascade, teardown) into result.json timings; coordbench/timeline.py.
  result.json's started_at and ended_at are naive local times, as
  CooperBench's runner writes them, and so are the timings; the host's
  offset from UTC goes beside them as utc_offset_seconds, and export.py
  (host_zone) reads every naive time in that zone, so a run exported or
  compared on a host in another zone reads the same. A run from before the
  field has its offset recovered from run_started's epoch against the
  run_create phase's naive times; one with neither (the session-driver
  pilots, CooperBench's own runs) is read in the reader's zone, and
  metadata.coordbench.time_zone says which it was.
- Why: coordbench's own diagnostics of what the harness costs; the offset,
  because a naive time read on another host is hours off the transcripts'
  UTC times.

**35. Absolute timestamps for events and steps.** Status: equivalent.
- CooperBench: the claude_code parser sorts the session by timestamp and
  drops it (parsers.py 146-159); conversation.json timestamps are float
  epochs; the website's SDK files interpolate times, mini-swe's are real.
- coordbench: coordbench/arm.py Run.emit_started, emitted by hub.py
  Hub.build before the arm's setup, makes run_started seq 0 with the epoch
  and its UTC ISO time, so epoch + t places every event; export.py
  hub_anchor and absolute give each hub event abs_ts. Steps carry the
  transcript's UTC times ("session-jsonl"), else times spread over the
  agent's phase ("interpolated", the website's SDK approach), else the
  hub's own record of the calls ("hub-events").
- What differs: coordbench keeps more than CooperBench's files do; nothing
  CooperBench keeps is lost.

**M13. Agent and scorer time limits.** Status: same.
- CooperBench: the claude_code container and exec timeouts are 7200 s
  (_coop/runtime.py build_environment; adapter.py 540); test_merged and
  test_solo use sandbox timeout 600 (eval/sandbox.py 100, 311).
- coordbench: --timeout-min 120 (claude under coreutils timeout,
  drivers.py claude_script); test_merged's 600 is left as it is; the
  scorer's subprocess is held to SCORING_TIMEOUT_SECONDS (3 h);
  coordbench/runconfig.py config_record writes timeout_s,
  scoring_timeout_s and cooperbench_sandbox_timeout_s into config.json.

## Cost

**24. Per-agent cost (USD).** Status: same under claude-cli; different
under the session driver.
- CooperBench: claude_code takes the result event's total_cost_usd
  (parsers.py 67); agents/pricing.py prices a model that reports none
  through litellm (prompt and completion tokens only), then a table of its
  own that holds MiniMax alone.
- coordbench: claude-cli as CooperBench, cost_source "claude code"
  (drivers.py summarize_stream). The session driver estimates
  (drivers.py fill_from_transcript and estimated_cost) from CLAUDE_PRICES,
  the Claude rows of the litellm 1.81.12 table CooperBench's environment
  ships, with the four token fields priced apart as Claude Code prices
  them, cost_source "estimated from tokens". The cost stays null, with
  output_tokens_lower_bound and cost_lower_bound, when the transcript's
  output count is not final, and null for a model the table lacks.
  coordbench/export.py passes cost_source and cost_lower_bound on with
  each agent, and compare.html shows a cost lower bound as ">=", with why
  in the value's title; a run's cost that leaves out an agent's (none
  measured, or only a lower bound) is marked the same way, and every cost
  says where it came from in its title: Claude Code's own total, the
  estimate from tokens, OpenHands' accumulated_cost or the website file's
  metadata.
- Why: decision 6. A subagent reports no cost; litellm is a dependency
  coordbench refuses, so its table is vendored as data. Note that no
  Claude 5 model is in that table, so the default claude-sonnet-5-5 gets
  no estimate until a row is added from a published price.

**25. Token counts (input, output, cache_read, cache_write).** Status:
different.
- CooperBench: parsers.py (69-72) from the result event's usage, written
  with 0 defaults (coop.py 226-231).
- coordbench: claude-cli the same four fields (drivers.py
  summarize_stream). The session driver copies the subagent's transcript
  and sums each API message's usage once (drivers.py transcript_usage: the
  largest value of each field over a message's lines, by message.id); the
  output count counts only when every message's final count is in the
  transcript (its stop_reason line), else it is recorded as a lower bound.
  A field nobody measured is null, never 0. coordbench/export.py
  transcript_usage sums a transcript or a stream the same way and says
  whether the output count is final, so each exported agent carries
  tokens_source (transcript, stream or result.json) and
  tokens_output_final.
- Why: better; a 0 would read as a measurement.

**26. total_cost and total_steps per pair.** Status: equivalent.
- CooperBench: sums over every agent with 0 defaults (coop.py 166-167).
- coordbench: coordbench/run.py usage_totals sums over the measured agents
  and is null when none was.
- What differs: null rather than 0 when no agent was measured (row 25).

**28. modelUsage, session_id, permission_denials from the result event.**
Status: same.
- CooperBench: kept only inside <agent>_stream.jsonl.
- coordbench: the same raw stream; coordbench/export.py collect_agent also
  copies modelUsage into metadata.coordbench.agents[].model_usage.

**29. Cost and tokens per run in reports.** Status: same.
- CooperBench: docs/BENCHMARK_RESULTS.md's cost per framework; the ablation
  CSV's total_cost.
- coordbench: bench/cooperbench/report.py cost_section: tokens per pair
  over the pairs whose every count is known, the pairs with some count
  null (the session driver's output count without its final figure) apart
  as a lower bound with output_tokens_lower_bound, the cost summed over the
  pairs that have one, and the cost sources. compare.html marks a token
  total as a lower bound (">=", its reason in the cell's title) when it
  leaves a count out, and when an agent's output counts were summed from
  a transcript or a stream whose messages lack the stop_reason line that
  holds the final count (tokens_output_final false, row 25).

## Communication

**16. Communication ablation (with against without communication).**
Status: same as 63b9d44.
- CooperBench: paper Sec 5 (L542-550), Fig 5(a), App E Table 6. 63b9d44 runs
  it as `cooperbench run --no-messaging`: the claude_code adapter installs
  no coop-* command and builds the prompt with build_instruction given no
  agents (_coop/prompt.py 134-160), so the agents are not told a partner
  exists.
- coordbench: coordbench/arms/coop-nocomm/arm.py CoopNoCommArm: coop's
  layout, no tools, the task and the submission block only, tested against
  CooperBench's own function when its source is present;
  bench/cooperbench/run_arms.sh coop-nocomm runs CooperBench's own;
  report.py sets coop beside coop-nocomm in the conflict and cascade
  sections.
- What the paper's own no-communication prompt was is an open question.

**36. messages_sent (result.json top level).** Status: equivalent.
- CooperBench: coop.py (239) len(sent_msgs), one per recipient of a
  broadcast; team.py (330) writes 0 for claude_code and codex because
  _spawn_team_agent drops the field.
- coordbench: coordbench/run.py cooperbench_result_fields writes the top
  level messages_sent from coordbench/kit.py message_metrics, one per
  delivery as coop_msg.py logs.
- What differs: a team run carries its real count where CooperBench writes
  0, a bug in CooperBench rather than a definition.

**37. conversation.json.** Status: same.
- CooperBench: coop.py (172-182) and team.py (251-255) through
  _extract_conversation: sent messages only, [{from, to, message,
  timestamp, feature_id}], one per delivery, sorted by the float timestamp.
- coordbench: coordbench/kit.py deliver records each delivery as a
  "message" event with content, timestamp, timestamp_iso, message_id (its
  own seq) and send_id (the seq of the call's "tool" event);
  coordbench/conversation.py conversation() derives the file and run.py
  write_conversation writes it.

**38. <agent>_sent.jsonl.** Status: same.
- CooperBench: claude_code adapter.py (572-573) copies coop_msg.py's send
  log ({from, to, content, timestamp, timestamp_iso}) when it is not empty.
- coordbench: coordbench/conversation.py sent_by, written by run.py
  write_conversation for each sender that sent anything.

**39. Message store with read receipts.** Status: equivalent.
- CooperBench: paper Sec 3 (L372-382): messages pass through an SQL store;
  the website's causes/<cause>/<id>_conversation.json shows its export
  ({messages: [{id, sender_id, recipient_id, content, message_type,
  timestamp, is_read}], export_timestamp, total_messages}). 63b9d44 logs no
  reads.
- coordbench: coordbench/conversation.py store() writes
  conversation_store.json in that shape, one row per send call (a
  broadcast is one row to "broadcast"), with coordbench's ids after the
  authors' keys (from, to, recipients, message_ids, send_id, sent_at,
  read_at). is_read comes from the message_ids that coordbench/kit.py's
  receive, wait_for_message and put_back (receive_lost) name.
- What differs: it is derived from the run's events, not exported from a
  database; the fields are the export's, export_timestamp included in its
  format (59bbf65 openhands_coop.py's time.strftime("%Y-%m-%d %H:%M:%S"),
  local time to the second with a space, as all 37 of the website's
  exports have it), while the rows' timestamps are ISO as theirs are.

**40. Message delivery model (push into the next step, or pull)
(corrected).** Status: different from the paper, same as 63b9d44.
- CooperBench: paper Sec 3 (L377-378): a message is included "in the prompt
  of the next step"; App D's prompt shows "[Inter-agent message]". 63b9d44's
  claude_code adapter pulls (coop-recv). CooperBench's own Cooperator
  Plugin pushes into Claude Code through MCP channels (website
  src/pages/cooperator-plugin.astro: `claude
  --dangerously-load-development-channels server:claude-coop`).
- coordbench: pull, as 63b9d44 (coordbench/kit.py receive and
  wait_for_message). No push option.
- Why: decision 2. The default matches the code the owner runs. A push
  option, through a channel as the Cooperator Plugin does or a PostToolUse
  hook as a fallback, is out of scope for now; the plugin is its reference
  design.

**41. Communication overhead (communication events as a share of all
events).** Status: different.
- CooperBench: paper Fig 5(c) (claude 20.0% ... qwen coder 3.3%), L563-567,
  over OpenHands events (actions and observations). No code.
- coordbench: bench/cooperbench/report.py pair_overhead and overhead_cells:
  the sending calls over all tool calls of both agents' own turns, sends
  and receives over the calls, and sends and their results over the calls
  and their results. The hub's count of coordination calls is used when
  events.jsonl is there (each alias of a chained shell command counted),
  else the transcripts; subagent turns are left out. The split by speech
  act is in speech_act_section once the acts are labelled.
- Why: architectural; Claude Code has no OpenHands events, so neither
  basis is the paper's unit and both are given.

**42. Speech-act type per message (Plan, Question, Answer, Update, Ack).**
Status: different.
- CooperBench: paper Fig 5(c) and L564-567; the method is not described.
- coordbench: coordbench/judge.py speechacts, one judge call per
  conversation with prompts/speechacts-v1.txt (coordbench's definitions,
  recorded with the output), and keyword_speech_acts, the free keyword
  baseline beside it.
- Why: the paper gives no method, so the method is coordbench's, named as
  such.

**43. Plan:Question ratio.** Status: same.
- CooperBench: paper L572-573: 2.04 without conflict, 1.31 with; ratio of
  totals or mean of ratios unstated.
- coordbench: bench/cooperbench/report.py plan_question_row: both readings,
  split by naive-merge conflict, from the judge's labels and from the
  keyword baseline.

**44. First-turn planning and conflicts.** Status: equivalent.
- CooperBench: paper L576-579: conflict rate 29.4% with a Plan in the first
  turn against 51.5% without, lower in 7 of 8 difficulty buckets.
- coordbench: bench/cooperbench/report.py first_turn_rows (whether the
  conversation's first message is a Plan, and as a variant whether every
  agent's first one is, all_open_with_plan) with Fisher's exact test, and
  first_turn_bucket_rows over App C's ten buckets.
- What differs: the paper's eight buckets are not defined anywhere, so the
  ten of App C are used.

**45. Concrete reference counts (line numbers, file paths).** Status:
equivalent.
- CooperBench: paper L580-582: 32.6 against 22.5 line-number mentions, 13.1
  against 10.0 file paths; the extraction and "successful" are unstated.
- coordbench: bench/cooperbench/report.py references, pair_references and
  reference_rows: regular expressions for line references (lines N, N-M,
  L<N>, path:N) and file paths with a source extension, means per
  trajectory split by both_passed and by conflict, Mann-Whitney U.
- What differs: the extraction is coordbench's, since the paper's is not
  given.

**46. Conversation turns.** Status: equivalent.
- CooperBench: paper Fig 6(a) (claude 14.5 ... qwen coder 1.1); "turn"
  undefined.
- coordbench: bench/cooperbench/report.py conversation_rows: one turn per
  send call (a broadcast is one, a team's request and its response one
  each), and speaker runs as a variant. The turns are the messages of the
  conversation coordbench/judge.py labels (pair_sends through
  judge.conversation_of), each under the id the judge keys its labels by
  (the delivery's message_id, a team request's or response's seq, a
  paper-era store's row id, a position in CooperBench's conversation.json),
  so every speech-act label of rows 42 to 44 finds its message.
- What differs: the paper does not define a turn, so both readings are
  given, one turn per send call first.

**47. Message volume (characters, words).** Status: equivalent.
- CooperBench: App I.2 (L1895) counts words.
- coordbench: coordbench/kit.py message_metrics: message_chars and
  message_words (text split on whitespace).
- What differs: CooperBench's code counts neither; the paper counts
  words, and both counts are kept.

**49. Typed request and response bodies.** Status: same.
- CooperBench: the task-log keeps request_id, request_kind, by and to, no
  body (team_harness/protocol.py 107, 174).
- coordbench: task_log.json body-free in CooperBench's shape; the team
  arm's Board.record (coordbench/arms/team/arm.py) also puts each body as
  content on its request and response events, so unresponsiveness to a
  request can be judged.

**50. Team metrics.** Status: equivalent.
- CooperBench: team_harness/metrics.py (29-54) compute_metrics, the only
  thing team.py puts under metrics.
- coordbench: coordbench/arms/team/arm.py compute_metrics, a verbatim port;
  TeamArm.metrics adds coordbench's counts without changing the six.
- What differs: coordbench's keys sit beside the six and change none of
  them.

**52. Tool calls per tool, events per kind, tool_done duration and
blocked time.** Status: different.
- CooperBench: none.
- coordbench: coordbench/arm.py Arm.metrics and each arm's tool_call_counts;
  the tool and tool_done events that frame every call (coordbench/mcp.py).
- Why: coordbench's own coordination telemetry.

**M4. CooperBench's Cooperator Plugin: push messaging for Claude Code
through MCP channels.** Status: not measured.
- CooperBench: website src/pages/cooperator-plugin.astro (`npm i -g
  @cooperbench/claude-coop`, `claude-coop install`, the channel flag above).
- coordbench: nothing yet.
- Left undone: decision 2; it is the reference design for a later
  coop-push arm, once it is known whether channel notifications reach a
  headless `claude --print` agent.

**M12. Agent naming (feature-numbered in the paper era, ordinal at
63b9d44).** Status: equivalent.
- CooperBench: the website's cause conversations name agents agent_<feature>
  (sender_id "agent_3"); App D's prompt says "You are {{ agent_id }}";
  63b9d44 uses agent1 and agent2 (coop.py 66).
- coordbench: agent1 and agent2, as 63b9d44 (arms/coop/arm.py layout);
  coordbench/conversation.py paper_id writes sender_id and recipient_id as
  agent_<feature> in conversation_store.json, with coordbench's ids (from,
  to) beside, so the store lines up with the authors' exports. A paper-era
  agent learned its partner's feature from the partner's name; a 63b9d44
  agent does not.
- What differs: the store carries both names; the agents themselves
  are named as 63b9d44 names them.

## Failure modes

**66. Communication errors in three groups (Repetition,
Unresponsiveness, Hallucination).** Status: equivalent.
- CooperBench: paper Sec 5 (L596-605), Fig 6(b-d), App F (L1638-1682): an LLM
  judge, model unnamed, precision first, at most one category per
  conversation, at least two exact quotes. No code at any commit.
- coordbench: coordbench/judge.py commerrors: App F's text word for word as
  the system prompt (prompts/commerrors-appF-7.txt), one call per
  conversation with a message (a broadcast is one message; a run whose
  audit.json names a harness error has none), commerrors_section
  accepts a label only when its code is in the variant and two quotes
  verify (verify_quotes: inside one message's content, whitespace and
  curly quotes folded), else keeps it as rejected with the reason;
  ERROR_GROUPS maps the codes; bench/cooperbench/report.py error_rows
  gives the groups over the judged conversations and over every scored
  pair.
- What differs: the judge model (decision 4): under claude-cli it is a
  Claude model, the one unavoidable deviation; openai-compatible can run
  GPT-5. Every judge.json section records backend, model and prompt
  variant.

**67. C1a Unanswered direct question (no reply).** Status: equivalent.
- CooperBench: App F; Fig 6(c) "No reply to direct question".
- coordbench: the judge's label (row 66); free cross-check judge.py
  question_checks (unanswered_questions: a question whose addressee sends
  nothing later).
- What differs: the judge model, as row 66.

**68. C1b Unanswered direct question (ignored).** Status: equivalent.
- CooperBench: App F; Fig 6(c) "Reply but ignores question".
- coordbench: the judge's label; cross-check question_checks
  (ignored_questions: a reply sharing no content word with the question).
- What differs: the judge model, as row 66.

**69. C2 Non-answer or vague answer.** Status: equivalent.
- CooperBench: App F; Fig 6(c) "Vague / non-answer".
- coordbench: the judge's label only.
- What differs: the judge model, as row 66.

**70. C3a Incorrect claim (uncorrected).** Status: different.
- CooperBench: App F prints this bullet as "C4a" (PDF p.28), clashing with
  the repetition C4a; its evidence rule and taxonomy note call it C3a.
- coordbench: the judge's label, coded C3a in prompts/commerrors-appF-7.txt;
  cross-check judge.py contradictions (a done, added or implemented claim
  naming an identifier or file the sender's patch lacks).
- Why: kept as printed, C3a and C4a could not be told apart in the output.

**71. C3b "Incorrect claim (corrected)", App F's name.** Status: equivalent.
- CooperBench: App F; Fig 6(d) "Hallucination (corrected)".
- coordbench: the judge's label only.
- What differs: the judge model, as row 66.

**72. Plan drift / unilateral deviation (corrected).** Status: different.
- CooperBench: in Fig 6(d)'s legend only, probably the eighth of "eight
  categories" (L1677), with no code or definition. Two website images
  place it under Incorrectness, the Hallucination group, beside "Incorrect
  claim with correction": conversation_errors_v5_buckets.png (the source of
  Fig 6) and coordination_failure_taxonomy_tree.png.
- coordbench: only in prompt variant appF-8 (prompts/commerrors-appF-8.txt,
  `--variant appF-8`), as coordbench's code C5, counted under
  Hallucination; the default appF-7 cannot produce it.
- Why: the code and the definition are coordbench's because the paper gives
  neither; the placement follows the two images.

**73. C4a Spammy repetition (repeats same information).** Status:
equivalent.
- CooperBench: App F; Fig 6(b) "Repeats same information".
- coordbench: the judge's label; cross-check judge.py near_duplicates
  (same-sender pairs with difflib's ratio of at least 0.9).
- What differs: the judge model, as row 66.

**74. C4b Spammy repetition (near-duplicate status blocks).** Status:
equivalent.
- CooperBench: App F; Fig 6(b) "Near-duplicate status blocks".
- coordbench: the judge's label; near_duplicates marks a pair status-like.
- What differs: the judge model, as row 66.

**75. Any communication error.** Status: same.
- CooperBench: one label per conversation, so the Fig 6 panels are disjoint
  (claude 53.9% ... qwen coder 0%).
- coordbench: bench/cooperbench/report.py error_rows "Any error", the sum of
  the accepted labels, over both denominators.

**76. Failure symptom taxonomy (Table 1).** Status: different.
- CooperBench: paper Sec 6.1 (L647-653: a GPT-5 annotator over every failed
  Coop trajectory) and App G (L1685-1719: three evidence kinds, a primary
  and secondary symptoms, a coordination label only with conversation
  evidence). The rubric and prompt are unpublished.
- coordbench: coordbench/judge.py symptoms: prompts/symptoms-r1.txt, a
  reconstruction written around everything the paper prints verbatim
  (Table 1's caption and ten meanings, App H's examples, App G's decision
  rule and its instruction to emit labels) with a tie-break for the
  confusion App G names; symptom_scope takes every scored multi-agent pair
  failed under 63b9d44's verdict or the cascade's, harness errors apart
  (the session route's audit.json among them);
  one call per such pair; symptoms_section verifies every quote against
  its source. bench/cooperbench/report.py symptom_lines: the primary
  distribution over coordination-level failures and over every judged
  failure (Table 1's basis is unstated), secondary symptoms apart, the
  implementation share, Table 1's shares beside.
- Why: the rubric is a reconstruction, labelled so in every output
  (SYMPTOM_RUBRIC), because the authors' is unpublished; the judge model
  is the owner's choice (decision 4).

**77-86. The ten symptoms.** Status: different, each as row 76; each is a
label of the symptom judge, and some have a free supporting signal among
judge.py's heuristics, reported beside the label and never in its place.
- 77 Work overlap (Table 1, 33.2%; App H L1724-1733): overlapping_hunks
  (both patches change overlapping lines of one file), and the cascade's
  agent_alone when both patches pass one suite.
- 78 Divergent architecture (29.7%; App H L1736-1745): clean_merge_failed
  (a clean naive merge whose tests fail).
- 79 Repetition (14.7%): near_duplicates; the symptom is kept apart from
  App F's C4a and C4b.
- 80 Unresponsiveness (8.7%): unanswered_requests (team requests with no
  response) and unanswered_questions.
- 81 Unverifiable claims (4.3%): no free signal.
- 82 Broken commitment (3.7%): contradictions (a completion claim naming
  what the sender's patch lacks).
- 83 Dependency access (1.7%): no free signal.
- 84 Placeholder misuse (1.5%): insertion_markers (row 92) beside it.
- 85 Parameter flow (1.3%): no free signal.
- 86 Timing dependency (1.1%): no free signal.

**87. Implementation failure against coordination failure.** Status:
same.
- CooperBench: App G (L1695-1700): a coordination label needs explicit
  conversation evidence, else "we default to implementation-level
  failure". No rate reported.
- coordbench: coordbench/judge.py coerced_level enforces the rule: without
  a verified conversation quote the level becomes implementation and the
  primary symptom null; report.py symptom_lines gives the implementation
  share.

**88. Failure causes (Expectation, Commitment, Communication)
(corrected).** Status: same.
- CooperBench: paper Sec 6.2 (L656-663) and Table 2: 50 failed Coop traces
  reviewed by hand, 42% / 32% / 26%. The website's causes/index.json holds
  23 hand-written entries: expectation 8, commitment 9, communication 6.
  The blog (src/content/blog/curse-of-coordination.mdx) says expectation
  failure "happens in 63% of failures", against Table 2's 42%.
- coordbench: coordbench/annotate.py command_causes (a seeded uniform draw
  of 50 failed multi-agent pairs, one review packet each, labels.json with
  Table 2's definitions word for word) and command_tally (counts with
  Wilson intervals; --apply writes each label into the pair's judge.json);
  --draft stores a judge's draft apart and never counts it;
  CAUSE_REFERENCES and CAUSE_CONFLICT print both published sets and the
  blog's contradiction. coordbench/export.py --causes writes the stock
  viewer's causes/ files for the labelled pairs (row 112).

**90. Spatial against semantic coordination.** Status: different.
- CooperBench: paper Sec 5 (L585-594) and App I: conflicts are spatial,
  success needs semantic coordination; no rate.
- coordbench: bench/cooperbench/report.py conflict_section's "Semantic
  failure rate", the share of clean naive merges whose tests fail.
- Why: the paper gives no rate; this proxy is coordbench's, labelled so.

**91. Git-setting failure categories (corrected).** Status: different.
- CooperBench: the website's public/static/data/git-failures.json, 13
  hand-curated examples from gemini *_git runs, with ids impatience,
  destructive, broken, git_disasters, blame and sagas, no rates and no
  procedure; not in the paper. The blog
  (the-curious-case-of-miscoordination.mdx) names five patterns: The
  Real-Time Interaction Problem, Uncooperative Behaviors, The Commitment
  Problem, Misuse of Cooperation Tools, Hallucination in Communication.
- coordbench: captured at close: coordbench/hub.py collect_artifacts writes
  git_log.txt (kit.py git_log: `git log --all --reflog --stat` less the
  history below base_ref), read in place from git_remote_dir with its
  reflog (run.py init_repository_script sets core.logAllRefUpdates
  always, and repository_on_host names the directory), else fetched from
  git_remote_host without one. Detectors in coordbench/judge.py git_checks:
  git_disasters (force pushes, hard resets, amend and force in Bash),
  git_impatience (fetching a partner's branch before its first push),
  git_broken (a failed test run after a push), git_destructive (a commit
  deleting 100 lines or more of a file the partner changed); blame and
  sagas are judge tags (emergent). GIT_BLOG_NAMES files each id under the
  blog's name.
- Why: decision 10; neither the paper nor the website gives a method, so
  these are coordbench-labelled and never reported as CooperBench rates.

**92. Prompt-design failure categories (App D) (corrected).** Status:
different.
- CooperBench: App D (L1532-1534, L1589-1590) names failure kinds
  qualitatively and two checkable targets with claimed effects: "Never use
  insertion markers or comments like // [handleSubmit:onFinally] other
  agent inserts" and "you must send a final status update message"
  (L1565-1571).
- coordbench: coordbench/judge.py final_status (an agent whose last
  message comes before its last edit) and insertion_markers, among the
  free heuristics, beside Placeholder misuse: an added comment, outside
  the test files, that marks where another agent inserts code, as App D's
  example does. is_insertion_marker takes a party other than the writer
  (another agent, a numbered agent such as agent2 or agent_2 but never an
  identifier such as agent_config, a teammate or partner, and for a
  reserved place, an insertion point or code that goes here also a
  feature by number) and asks for the insertion itself: the party
  inserts (or plugs, hooks, fills in); it adds, puts, places or appends
  with a modal before the verb ("will add") or a place after it ("here",
  "below"), since the bare present tense describes code ("# Our partner
  adds a fee"); code goes here with the party named anywhere ("# Feature
  2 code goes here (other agent)"); a place reserved for the party; an
  insertion point named with the party ("# insertion point for agent2")
  or inside brackets (App H's "[Conditional filters overlay insertion
  point]"); or App D's bracketed anchor with a party or an insertion
  beside it. A comment that only names an agent ("# agent1 was here",
  "# Added by agent2", "# Agent 1 put this in"), says what someone's code
  does ("# teammate puts the files on disk") or is a placeholder of the
  code's own is not one. The three pilots' real patches hold none.
- Why: App D gives targets and effects, no procedure or rates, so the two
  detectors are coordbench's.

**M6. Failure families (the website's taxonomy tree).** Status: same.
- CooperBench: public/static/images/coordination_failure_taxonomy_tree.png,
  referenced by no page: Information (Information asymmetry, Unverifiable
  claims), Integration (Dependency access, Parameter flow, Placeholder
  misuse), Structural (Divergent architecture, Work overlap, Timing
  dependency), Communication (Repetition, Unresponsiveness, Incorrectness).
  Information asymmetry is not in Table 1, and Broken commitment is not in
  the tree.
- coordbench: coordbench/judge.py FAMILIES fills each symptom's family;
  bench/cooperbench/report.py symptom_lines gives family-level rates.
  Information asymmetry is offered only in the labelled variant
  symptoms-r1-tree (prompts/symptoms-r1-tree.txt), never as one of Table
  1's ten.

## Emergent behaviours

**93. Role division.** Status: different.
- CooperBench: paper Sec 6.4 (L834-862), "mutual confirmation"; no rate
  ("rare in our traces", L916); hand-curated website examples.
- coordbench: coordbench/judge.py emergent (prompts/emergent-v1.txt, the
  paper's own definitions), present only with a verified quote from each
  of two agents (EMERGENT_RULES); report.py emergent_rows.
- Why: decision 10; the paper gives no method or rate, so the tag is
  coordbench's.

**94. Resource division.** Status: different.
- CooperBench: Sec 6.4 (L864-892), "specificity"; website examples.
- coordbench: the judge's tag (a quote from each agent, one naming a file
  or a line range) and the free candidate
  resource_division_candidates.
- Why: as row 93.

**95. Negotiation.** Status: different.
- CooperBench: Sec 6.4 (L894-915); a website example.
- coordbench: the judge's tag (the options and the choice, from two agents)
  and the free candidate negotiation_candidates.
- Why: as row 93.

**96. Other curated behaviours (accountability, backtracking,
scaffolding, leadership, ownership).** Status: not measured.
- CooperBench: website file-name prefixes of hand-cut excerpts, no
  definitions.
- coordbench: none; judge.py's tags are the three above and the git
  arms' blame and sagas.
- Left undone: there is nothing to reproduce without definitions.

**97. Vague intentions become checkable commitments.** Status: different.
- CooperBench: Sec 6.4 (L829-832), Sec 6.3 (L807-823), Sec 8 (L1001-1004).
- coordbench: the sdlc arm makes commitments checkable by construction
  (coordbench/arms/sdlc/arm.py: claims, pull requests, the merge gate), and
  its metrics count claims, prs_opened, merge_attempts, merges,
  merge_conflicts and time_to_first_merge_seconds in metrics.json and in
  result.json's metrics block, and report.py's Coordination section sums
  them per arm beside the emergent rates, with the team arm's board
  (tasks, time to first claim, claims and updates) in a second table.
- Left undone: nothing; the paper has no figure for either count.

## Trajectory files

**27. Run-level summary.json.** Status: same, with two stated
differences.
- CooperBench: runner/core.py _save_summary (582-624): completed, skipped,
  failed, total_cost, total_time_seconds, results [{task, status, cost,
  eval}], eval {total_evaluated, passed, failed, errors, skipped,
  pass_rate}, pass_rate; a single-task run leaves results empty.
- coordbench: coordbench/runconfig.py summary_record and write_summary,
  from run.py write_run_summary at the end of a run and refresh_summary
  after each session-driver finish; coordbench's keys after CooperBench's
  (pass_rate_over_evaluated, run_totals, arm, driver, model, setting,
  claude_code).
- Why: results lists single-pair runs too, which CooperBench leaves empty,
  a gap in its code rather than a design; a cost nobody measured is null
  rather than 0.

**48. Team task log and final tasks (task_log.json, tasks.json).**
Status: same.
- CooperBench: team.py (285-294): the Redis task-log and the final list in
  id order.
- coordbench: coordbench/arms/team/arm.py TeamArm.artifacts, taken by
  hub.py collect_artifacts when the run closes, before teardown, and
  written by run.py write_artifacts in CooperBench's shapes.

**64. Legacy merge report per pair.** Status: equivalent.
- CooperBench: 59bbf65 core/paths.py's merge report (features with naive,
  union and LLM test results and outputs; merge_analysis with status,
  conflict_score, severity and the diff file).
- coordbench: the eval.json cascade block holds the same facts under its
  own keys (naive, union and llm with f1, f2 and their tests,
  conflict_sections, conflict_lines, conflict_score, severity, diff_file);
  coordbench/export.py legacy_detail turns them into the legacy names
  where a legacy file is written (row 65). No second file.
- What differs: one block in eval.json rather than a file per pair; the
  legacy names appear where the legacy file is written.

**65. aggregated_results_<model>_k<k>.json per task.** Status: same.
- CooperBench: 59bbf65 evaluate.py (590-616) and core/paths.py (295):
  detailed_results with has_naive_merge_conflict and
  featureN_{naive,union,llm}_merge_test_passed; read by the website's
  generate_coop_index.py (27-72).
- coordbench: coordbench/export.py write_legacy, merge_legacy, legacy_detail
  and cascade_totals: with --site, legacy/<coop|coop_git>/<repo>/task<N>/
  aggregated_results_<model_key>_k1.json, with the 59bbf65 regex rule
  beside under _regex keys; tests/test_coordbench_export.py reads it with
  a port of generate_coop_index.py's parse rule.

**98. agent<feature>_traj.json and solo_traj.json.** Status: same.
- CooperBench: coop.py (191-208), team.py (262-279, adds team_role), solo.py
  (88-105); messages from parsers.py parse_session_jsonl and
  _content_blocks_to_text.
- coordbench: coordbench/run.py write_trajectories, trajectory_names and
  trajectory_record, with coordbench/drivers.py parse_session_jsonl and
  content_blocks_to_text vendored verbatim (MIT); export.py
  runner_trajectory_files fills them in when they are missing.

**99. <agent>_stream.jsonl.** Status: same.
- CooperBench: adapter.py (566-575): the raw stream-json with stderr mixed
  in, always written.
- coordbench: claude-cli the same (drivers.py claude_script, 2>&1). The
  session driver, which has no stream, writes none: how its agent ended
  goes into <agent>_session_outcome.json (drivers.py
  record_session_outcome), since a line in a file named like a stream
  would be read by CooperBench's parser as an Error.

**100. <agent>_session.jsonl.** Status: same.
- CooperBench: adapter.py (571): every *.jsonl under the agent's Claude
  config projects concatenated, always written.
- coordbench: claude-cli drivers.py save_session_transcript, the same
  command, written even when empty. The session driver's preface makes the
  subagent open its first reply with a run token (run_token,
  SESSION_PREFACE); SessionDriver.take_transcript finds the transcript that
  carries it under Claude Code's projects directory (find_transcripts,
  carries_token), waits for it to settle and copies it. The token counts in
  a reply's text or in the prompt (every user line before the first reply,
  since a Workflow subagent gets the harness's request before its task),
  never in a tool call's input, and a transcript that names another agent's
  token of the same run is an orchestrator's and nobody's; a transcript
  whose reply carries the token is preferred over a newer one whose prompt
  alone does. Every match but the last needs a prompt that names the
  agent's own prompt file (its run directory and name, shared by every
  attempt of the pair), which keeps out a review agent whose prompt quoted
  the token. Next comes
  a transcript whose prompt names that file and which carries no other run
  token in its prompt or in a tool result, for an agent that opened the
  file with a tool call and never wrote the token in a text reply (six of
  72 agents in the curse-1 step of 8 pairs); another token it typed in a
  reply bars it too, unless a tool result holds its own, since what an
  agent read decides over what it typed (one solo agent of the X-Blossom
  screen opened its reply with its token short of the run id's suffix).
  Last comes a transcript that lost its prompt (its first line is not a
  user line with no parent, as a disk full at the agent's spawn left one
  relay of the curse-1 jinja 1559 run), by the exact token in a reply.
  The audit (bench/cooperbench/session/audit.py) counts a run whose
  agent's transcript was not found, or lost its first lines, as a harness
  error, since its tokens and tool results are then not all read, as it
  does a run where another transcript that lost its prompt carries the
  agent's token, which may be a second spawn.

**101. Solo result.json shape and file names.** Status: same.
- CooperBench: solo.py (119-129): one "agent" block, agent_id "solo", so
  solo_stream.jsonl and solo_session.jsonl.
- coordbench: coordbench/run.py cooperbench_result_fields writes the
  singular "agent" block with CooperBench's keys first; agent_file_id names
  the files solo_*, whatever the arm calls its agent on the hub (agent1).
  Every reader takes both shapes: report.py pair_agents, timeline.py
  agent_records, runconfig.py result_agents, run.py result_agents; and the
  readers of the agent's files take the solo_ stem in the solo setting
  while keeping agent1 as the id the timings and events use (export.py
  file_stem, judge.py agent_tool_uses).

**102. setting value and directory for team runs.** Status: same.
- CooperBench: team.py (91, 303): setting team under team/; coop and coop
  --git both coop; eval.json says coop for any merged evaluation.
- coordbench: coordbench/run.py pair_setting (team for the team arm, coop
  for coop, coop-git, coop-nocomm and sdlc, or an arm.json's "setting");
  evaluation_record writes coop for merged evaluations; finished_run_dir
  still finds a team pair written under coop/ before; report.py read_run
  walks every setting. result.json's "arm" keeps what CooperBench's files
  cannot tell apart.

**103. result.json log_dir.** Status: same.
- CooperBench: coop.py 240, solo.py 132, team.py 342.
- coordbench: coordbench/run.py cooperbench_result_fields.

**104. run_id format.** Status: different.
- CooperBench: uuid4().hex[:8].
- coordbench: coordbench/run.py new_run_id: <arm>-<task>-f<a>_f<b>-<hex6>.
- Why: readable, it names the pair, and no reader parses it.

**105. config.json (run level).** Status: same, with coordbench's keys
after CooperBench's.
- CooperBench: runner/core.py _save_config (278-292): run_name,
  agent_framework, model, setting, concurrency, total_tasks, started_at; no
  git, messaging or team flags.
- coordbench: coordbench/runconfig.py config_record, written by run.py
  write_run_config when the run starts: CooperBench's keys in its order,
  then arm, driver, options, agent_models, max_turns, timeout_s,
  disallowed_tools, the time limits, isolation,
  differences_from_cooperbench, provenance, images and pairs.
- Why: the extra keys record what CooperBench's file cannot tell apart.

**106. eval_summary.json.** Status: same.
- CooperBench: eval/evaluate.py _save_summary, written by `cooperbench
  eval` only, never by the inline evaluation.
- coordbench: none; summary.json's eval block covers it, as for
  CooperBench's inline evaluation.

**107. Scratchpad volume contents (PLAN.md, <agent>.patch, tasks/).**
Status: different.
- CooperBench: the volume cb-team-<run_id> is never harvested into the logs
  and never removed, so what the agents wrote there stays in docker.
- coordbench: coordbench/run.py copy_shared_volume copies the volume's last
  state into <pair_dir>/shared/ (docker cp from an agent's container,
  which mounts it) at the start of teardown, before the containers and the
  volume are removed, and with --keep too; then teardown removes the
  volume. The task list also survives as tasks.json (row 48).
- Why: better; the agents' plan and handoff patches are in no transcript,
  and the logs keep them without docker. They sit in a directory of their
  own so an agent's <agent>.patch never meets the runner's. What the
  volume held between the copy's moments (a handoff overwritten later) is
  not kept, as in CooperBench.

**108. Paper-era files (execution and planning trajectories, plans).**
Status: different.
- CooperBench: 59bbf65 core/paths.py (248-275), OpenHands v0.54's event
  stream.
- coordbench: none; the export bridges the formats (row 109).
- Why: architectural; the agents are Claude Code, not OpenHands.

**109. Website viewer trajectory file (.json.gz, {metadata, steps}).**
Status: same.
- CooperBench: the website's scripts/prepare_oh_sdk_trajectories.py
  (440-465) and prepare_miniswe_trajectories.py (464-489); read by
  viewer.astro (740-755).
- coordbench: coordbench/export.py write_pair (trajectory.json.gz in the
  pair directory, which run.py export_pair calls once a pair is scored)
  and write_site; build_trajectory gives {metadata, steps}, the stock
  viewer's keys first and everything else under metadata.coordbench
  (format coordbench-trajectory/1). Steps come from the session transcript
  split into threads by sessionId and, for sidechains, agentId
  (split_threads, main_thread), joined to the hub's record by agent, tool
  and time (join_hub), with what is left unmatched in join_warnings.

## Viewer

**110. Website index.json rows and stats (corrected).** Status: same.
- CooperBench: public/static/data/{coop,coop_git}/index.json, written by
  the prepare scripts' write_index. It mixes two scoring rules: the paper
  models' rows come from legacy aggregated_results through the cascade
  (generate_coop_index.py 38-65), the Gemini rows from the newer runner's
  both_passed. coop/index.json has 697 rows: 45 paper-model typst rows keyed
  "typst", the name before the rename, and 45 Gemini rows keyed
  "typst_task". Rows marked missing carry hasConflict null (81 for gpt5, 45
  for claude, 312 for minimax, 116 for qwen_coder, 45 for qwen), and
  stats.rate for gpt5, 27.95, is not 182/652 (27.91).
- coordbench: coordbench/export.py merge_index, index_row and model_stats:
  rows merged by (repo, taskId, features) with the alias {typst:
  typst_task} (REPO_ALIASES), stats total over every attempted pair,
  passed, rate rounded to one decimal as write_index rounds it, merging
  twice changing nothing. A typst pair has two rows on the website, so its
  result joins one of them (row_for: the row under the pair's own repo
  name, else the first), never both, which would count it twice, and the
  viewer URL names that row (row_repo); a lookup of a model's result
  searches both rows for the one that holds it (export.py index_result,
  compare.py, compare.html indexResult), so neither a Gemini result in the
  typst_task row nor coordbench's own reads "not published".
  report.py published_outcomes and the comparison viewer use only the rows
  with results; a missing row reads "not published", never a fail.

**111. Website tasks.json.** Status: equivalent.
- CooperBench: the website's scripts/generate_tasks_json.py, from the
  dataset.
- coordbench: not written. coordbench/viewer/compare.html reads tasks.json
  for the feature titles when it is given a site root;
  coordbench/export.py features_info takes the titles from the dataset by
  extract_title's rule when a CooperBench checkout is named.
- What differs: coordbench reads the file rather than writing it; the
  titles come from the same feature.md by the same rule.

**112. Cause example files (causes/index.json and
<cause>/<id>_trajectory.json).** Status: same.
- CooperBench: the website's scripts/index_cause_trajectories.py
  (285-404): entries {classification, task_id, model, features, summary,
  failure, project, id, trajectory_url, step_count}; 7-key steps.
- coordbench: coordbench/export.py --causes: load_causes, cause_entry,
  merge_causes, write_cause and seven_key_steps, for the pairs a human
  labelled, with ordinal agent ids. A site tree without a causes/index.json
  gets an empty one (ensure_cause_index), since viewer.astro's
  initCauseIndex (717-738) fetches it before it reads its URL and loads no
  trajectory when that fails; so --site works on a fresh directory served
  as the viewer's public root as well as on the website's public/ tree.

**113. Agent lanes.** Status: different.
- CooperBench: viewer.astro (597-611): agent_1 is lane A and every other step
  lane B, so a third agent folds into B; its run panel reads agent_1 and
  agent_2 only (625-630, 661-680).
- coordbench: coordbench/viewer/compare.html draws one lane per agent.
  export.py gives ordinal lanes (agent_k is the k-th agent in result.json
  order, never a feature number) and, for more than two agents,
  stock_viewer_note in metadata and every agent's cost in total_cost.
- Why: better; team and sdlc runs can have more than two agents.

**114. URL parameters and data path.** Status: same.
- CooperBench: viewer.astro (722-755): model, repo, task, features;
  setting=coopGit selects coop_git; /static/data/<coop|coop_git>/
  trajectories/<model>/<repo>_<task>_<features>.json.gz.
- coordbench: coordbench/export.py write_site, setting_dir (coop_git for
  coop-git and sdlc, coop otherwise), model_key ('<model>_<arm>_<driver>'
  slugged, or --model-key), trajectory_file_name and viewer_url. A second
  run of the same key over a pair in one export call goes under the key
  with its run name added, with a warning (unclaimed_key), so neither run
  silently replaces the other.

**115. Tool-type classification.** Status: same.
- CooperBench: viewer.astro getToolType (854-888); a precomputed toolType
  wins. Its legacy "read" has no args.command and is typed edit.
- coordbench: coordbench/export.py get_tool_type, a port tested against the
  precomputed types, and bash_type (find_grep by the viewer's rule); Read
  is typed view, which fixes the legacy mistyping; compare.html uses the
  same types and palette.

**116. Hidden receive steps.** Status: same.
- CooperBench: viewer.astro (1244-1253, 1328-1331) hides any step whose tool
  name holds comm_get or receive.
- coordbench: coordbench/export.py names reads openhands_comm_get
  (receive_steps), so the stock viewer hides them as its own;
  compare.html shows them with "Show receipts", since unresponsiveness
  needs them.

**117. System-prompt filter.** Status: same.
- CooperBench: viewer.astro (513-522) drops a step whose first non-empty of
  content, text and message holds "You are OpenHands agent", "<ROLE>",
  "You are agent_", "**FEATURE DESCRIPTION**" or "Added workspace context".
- coordbench: coordbench/export.py STOCK_VIEWER_FILTERS (all five, on that
  field order) and stock_viewer_drops: prompts are never steps, and any
  other step the filter would drop is listed in filtered_by_stock_viewer.

**118. Run info panel and stats bar.** Status: same in the counts;
different in the clock.
- CooperBench: viewer.astro (623-700): the model from the first
  model_response.model, else the last llm_metrics; tokens and cost from the
  last llm_metrics; a cost of 0 printed N/A. The stats bar (1079-1081)
  counts each agent's steps as every entry of the file the system-prompt
  filter keeps (splitStepsByAgent 597-611, so an OpenHands event list's
  observations are steps too), and its duration runs from the first
  step's time to the last (1073-1075).
- coordbench: coordbench/export.py add_llm_metrics (each agent's last step,
  the cost left out when null) and mark_models (model_response.model on
  each agent's first step). compare.html's "steps" is the stats bar's
  count (stockCounts), in its status line, its steps per agent and its
  speed table alike; its one other count, its own step cards without the
  message receipts (an OpenHands observation sits in its action's card),
  is always labelled "steps without observations and receipts". A run
  with no transcript (the session-driver pilots) has only the hub's
  record of its coordination calls for steps, so its counts are marked as
  floors (">=", "hub calls only"), and its steps per minute, time per
  step, communication share, longest idle gap and times to the first edit
  and test run are left out as not measured, each with why in its title.
  Its clock starts at the first agent's own start (its phase in the
  timings, or its transcript's first line) when one is known, else, for
  a coordbench run with no run_started event (the session-driver pilots,
  from before per-agent timings), at result.json's started_at, where each
  agent's span then starts too, else at the file's first event. That
  last is not the stats bar's start: the page counts from the earliest
  event in the file, the system prompt included, and the stats bar from
  the first entry its prompt filter keeps, so a published run's wall
  time is longer by the prompt's lead (gpt5 dottxt_ai_outlines 1371
  feature1_feature2: 8m 21s against the stats bar's 8m 12s; claude's:
  10m 11s against 10m 3s, as the stats bar prints whole seconds rounded
  down). The runs table and each timeline say which
  clock a run uses ("clock: agents' start", "pair start", "first
  event"), and the wall time's title gives the stats bar's duration where
  the two differ.
- Why: an agent has begun when its system prompt is logged, so the
  page's wall time starts there, and the stats bar's figure stays in
  view beside it. A pilot's first step is its first hub call, 68 s after
  the pair's start in the coop pilot, so a clock from it made the wall
  time short and the time to the first message near zero; the pair's
  start, though it holds the setup, is the nearest start such a run
  recorded, and the agents' spans start there with it so the active span
  and the wall time agree. A run with per-agent timings but no
  run_started event (the demo) keeps its agents' start, as every later
  run does, so the two read alike. Step counts and rates over hub calls
  alone would set a coordination log beside full trajectories as if it
  were one.

**119. Step cards, filters, timeline, time markers, highlight, show more.**
Status: different.
- CooperBench: viewer.astro (1237-1625), with four bugs: a click lands on
  the wrong step after hidden steps (originalIndex), max-h-32 boxes never
  expand, the status bar is always hidden, a bad URL silently loads a
  random cause example.
- coordbench: coordbench/viewer/compare.html reproduces the cards and the
  filters (Communication, Edits, File View, Terminal, Other, and Show
  receipts), escapes all text, expands every collapsed box, links timeline
  blocks to steps by a stable uid and shows load errors instead of a
  random example. Its timelines draw one unit to a pixel, never under
  560 px wide, so their lane letters and axis labels keep their 12 and
  11 px at a phone's width and the timeline scrolls in its own box; a
  timeline wider than its box fades at the edge it scrolls toward and
  says which stretch of its axis is in view ("In view: 0.0 s to 8m 55s
  of 17m 50s"), since a phone hides its scroll bars. A run with
  no transcript draws each hub call only as long as it took, over a
  dashed band marking the agent's span whose own calls were not
  recorded, where a full log's step lasts until the next. Beside the
  times it flags agent logs that do not overlap, and an agent whose own
  log covers under a tenth of the run's wall time (of the longest
  agent's log when the logs do not overlap) and does not end with its
  finish step as possibly truncated, as CooperBench's gpt5 pallets_click
  2068 feature1_feature6 file is: 6 events over 7 s of a 20-minute run.
  A log that ends with its finish is complete however short: over the
  website's coop files that rule leaves 20 of gpt5's 548 flagged (21
  before), 3 of claude's 589 (8) and 1 of gemini_flash_miniswe's 652
  (27), none ending with a finish. Only real step times are checked,
  since interpolated ones fill each agent's phase and the hub's record
  holds only the coordination calls.
- Why: better; the same views without the bugs.

**120. Outcome display (pass or fail, conflict, tests).** Status:
different.
- CooperBench: not in viewer.astro; the leaderboard panel shows pass or
  fail (leaderboard.astro 792-858).
- coordbench: compare.html's runs table and failure-mode presence matrix
  show the outcome and the failure modes per run. The outcome keeps each
  scoring rule under its own name (decision 1): "63b9d44 pass" or "fail"
  with "63b9d44 strategy" (eval.json merge.strategy: naive merge,
  identical patches, lead's patch alone, or, where 63b9d44's sandbox.py
  leaves "naive" after a conflict or a patch that did not apply, "none
  (naive conflicted, lead's patch alone failed)", since no merge was
  tested), "paper-era pass" or "fail" with "paper-era route"
  (cascade.route: naive, union, learned resolver, none passed), "paper-era
  undecided" with "paper-era route: learned resolver not run" where naive
  and union failed and the resolver was needed but did not run
  (cascade.llm, its lower_bound; decision 7), or "paper-era not scored"
  where eval.json has no cascade;
  "naive conflict", which both rules share; and for a published run
  "index pass" or "fail", the website index's own mix of rules (row
  110). Each matrix cell is marked by its source (judge, rule, human,
  heuristic, data): a symptom level judge.json says App G's rule decided
  (decided_by "rule", a failed pair with no message, where no model was
  called) or set over the judge's (coerced) reads "rule", and the judge
  caption shows only when judge.json names a judge. A failure present is
  red and absent green; role division, resource division and negotiation
  are coordination behaviours (Sec 6.4), not failures, so their columns
  stand under their own heading, present in purple and absent plain.
  timeline.py's header prints the verdict.
- Why: better; the stock viewer cannot show why a run failed.

**121. Leaderboard and its 95% intervals (corrected).** Status:
different.
- CooperBench: leaderboard.astro (396-591) types in the scores and the
  intervals (GPT-5 coop 27.95 [24.48, 31.65]), mixing the two scoring rules
  of row 110; its panel reads index.json (744-858).
- coordbench: bench/cooperbench/report.py computes every rate's Wilson
  interval and a bootstrap over task sets, and names the rule behind each
  rate. export.py merges coordbench's rows into index.json in the
  website's shape (row 110), but the leaderboard does not show them: it
  draws its rows from its typed-in leaderboardData and opens the panel
  only for those fixed model keys (renderLeaderboard 677-741, its
  listener 735-739, openModelPanel 744), never walking index.json's
  models, so a coordbench key gets no row until leaderboard.astro lists
  it. (--model-key with a paper model's key would fold coordbench's
  results into that model's rows and restate its stats over the mix,
  which is no comparison.) The way in is the viewer URL export.py prints
  for each pair (/viewer?model=...).
- Why: computed intervals rather than typed ones, and one rule per column.

**122. Front-page emergent mini-viewer and git-failure components.**
Status: not measured.
- CooperBench: index.astro (993-1192); FailureExample.astro and
  FailureViewer.astro, unused.
- coordbench: none.
- Left undone by choice: compare.html's presence matrix shows the same
  behaviours per run.

**123. Gzip handling.** Status: same.
- CooperBench: viewer.astro (703-714): pako.ungzip when the body starts 0x1f
  0x8b, else plain JSON; files at gzip level 9.
- coordbench: coordbench/export.py write_gzip_json (level 9, compact JSON);
  compare.html undoes gzip with the browser's DecompressionStream on the
  same magic number, with the plain-JSON fallback; compare.py
  read_bundle_file does the same in Python.

## Setup, dataset and agents

**17. Dataset size and composition (corrected).** Status: same as
63b9d44; different from the paper era (M1).
- CooperBench: at 63b9d44, 30 task sets, 199 features and 652 pairs over 12
  repositories (subsets/all.json). The paper's PR count does not agree
  with itself: Table 3's #PRs column sums to 32, not its printed 34 (typst
  is listed with 3 PRs, but only typst_task/task6554 exists), and App A.2
  says 52 task sets. Features and pairs reconcile per repository.
- coordbench: bench/cooperbench/dataset_stats.py composition beside
  TABLE_3 and PAPER_TOTALS; report.py runs_section counts how many of the
  652 pairs each run covers.

**19. Feature complexity (Table 4) (corrected).** Status: equivalent.
- CooperBench: App A.2 Table 4 (52.3 lines, 4.4 functions, 1.4 files);
  generation/prompt.py _extract_patch_info (8-35) counts added and removed
  lines and hunks per file with unidiff.
- coordbench: bench/cooperbench/dataset_stats.py patch_counts,
  feature_complexity and complexity_table: lines, files and hunks from the
  diff text; functions approximated by the hunk headers' enclosing
  definitions, beside the hunks, since Table 4's "Avg. Functions" equals
  the mean hunk count to one decimal in most repositories.
- What differs: the standard library instead of unidiff, a dependency
  coordbench refuses.

**20. Feature difficulty class (SWE-Rater-32B Easy, Medium, Hard).**
Status: not measured.
- CooperBench: App A.2 Table 4 (Easy 15, Medium 99, Hard 85); the per-feature
  labels are not published.
- coordbench: none.
- Left undone: re-rating with another model would not give the same label;
  it waits for the authors' labels.

**23. Rerun rule (corrected).** Status: different.
- CooperBench: rerun a pair any of whose agents ended in Error (coop.py
  75-81; solo reads the singular agent, solo.py 44-49), an agent killed
  at the 7200 s timeout among them (claude_code/parsers.py: no result
  event is Error).
- coordbench: coordbench/run.py pair_finished, the same rule over both
  shapes, which also reruns a pair whose result.json has a top-level error
  (a harness failure) and keeps a pair whose agent ran out its time
  (agent_ran_out_of_time: drivers.CliRun's end_reason starts "timed out
  after"), since that attempt's patch is scored as CooperBench scores it
  and a rerun would most likely run out again, spending the account's
  usage to replace a sample, the attempt count then set by how often the
  owner dispatched; the Actions kit's artifacts.py reads the same rule
  (bench/cooperbench/PLAN.md, Resume). run_one scores again, without its
  agents, a finished pair that lacks eval.json or whose eval.json holds
  an error (run.py scored), as artifacts.py calls such a pair unscored.
  A pair run again in the same
  directory has the earlier attempt's files packed into
  previous-<time>.tar.gz first (run.py set_aside_earlier_attempt), and
  run.py rescore scores pairs from their patches without ever starting
  their agents, as `cooperbench eval` does: a pair with no verdict, as
  run_one decides, or a merged pair with no cascade block yet
  (needs_scoring).
- What differs: the time-limit keep, deliberately, for the reason above:
  CooperBench would run such a pair again; the harness-error rerun, a
  coordbench addition; the scoring again of a pair whose eval.json holds
  an error, by run and rescore alike, where CooperBench's runner reads
  such an eval.json back unless forced (eval/evaluate.py
  _evaluate_single, 320-322) and counts it as an evaluation error
  (runner/core.py _process_eval_result, 333-334), and `cooperbench eval`
  skips it as already evaluated (eval/evaluate.py 57-60), so a sandbox
  that failed once does not cost the pair its sample; and the packing,
  since CooperBench's runner reuses the
  directory with mkdir(exist_ok=True) and leaves what the new attempt
  does not rewrite, which for coordbench (a judge.json, an agent's
  _sent.jsonl, a session transcript) the post-hoc tools would read as
  the new attempt's.

**51. team_features switches, lead_agent, team_role and the ablation
sweep.** Status: different.
- CooperBench: team.py (335-341) writes team_features {task_list,
  scratchpad, mcp, auto_refresh, protocol}, lead_agent and each agent's
  team_role; scripts/run_team_ablation.py sweeps the --team-no-<feature>
  flags (docs/team_harness_ablation_report.html).
- coordbench: coordbench/run.py writes lead_agent (lead_agent()),
  team_features (TEAM_FEATURES in coordbench/arms/team/arm.py, every switch
  on) and team_role per agent (agent_record). The team arm has no
  switches.
- Left undone: run options team.<feature>=off honoured by the arm's tools
  and prompt, as CooperBench's flags are, and so the sweep. Free to build;
  the sweep needs runs.

**127. Agent framework (corrected).** Status: equivalent.
- CooperBench: paper: OpenHands v0.54 CodeActAgent with an MCP tool
  openhands_comm_send (Sec 3 L372-382). 63b9d44: claude_code, codex,
  mini_swe_agent_v2 and openhands_sdk adapters.
- coordbench: Claude Code only: claude-cli builds the claude_code adapter's
  command line (coordbench/drivers.py claude_flags, claude_script,
  claude_environment), session subagents, or a driver of one's own. The
  additions are listed in config.json's differences_from_cooperbench
  (drivers.py CLI_DIFFERENCES): --plugin-dir and --mcp-config on the
  default tool surface; MCP_TOOL_TIMEOUT=330000; coreutils timeout inside
  the container; no install step (the overlay pins the host's Claude Code
  version, where CooperBench's agents/claude_code/setup.sh installs @latest
  at run time); no ANTHROPIC_BASE_URL or ANTHROPIC_AUTH_TOKEN forwarding, no
  _MODEL_PROFILES budgets, no settings.json; --disallowedTools only on
  request.
- Why: architectural; coordbench varies the coordination with the agent
  fixed.

**128. Models (corrected).** Status: different.
- CooperBench: paper: GPT-5, Claude 4.5 Sonnet, MiniMax-M2,
  Qwen3-Coder-30B-A3B, Qwen3-30B-A3B, in self-play (Sec 3 L391, L465-467).
- coordbench: Claude models on the owner's subscription. result.json model
  is the one asked for; each agent records model_requested, the model it
  ran (claude-cli: the stream's init event; session driver: the
  transcript's message.model) and model_differs (run.py model_differs). A
  session subagent's model is the orchestrating session's choice, which
  nothing else enforces, so a mismatch is flagged rather than prevented;
  report.py runs_section counts the agents that ran another model.
- Why: architectural and cost; the open models could run through an
  Anthropic-compatible endpoint (M8).

**129. Action cap (100 actions per agent).** Status: same as 63b9d44.
- CooperBench: paper Sec 2.1 (L243-244, footnote 2). 63b9d44's claude_code
  sets no cap unless max_turns is configured (adapter.py 432-435).
- coordbench: --max-turns, default none (decision 3), passed to claude as
  --max-turns (drivers.py claude_flags) and recorded in config.json and
  result.json. For paper-faithful runs use --max-turns 100; a Claude Code
  turn may hold several parallel tool calls, so it approximates actions.
- Left undone: report.py does not give the LimitsExceeded share. Free.

**130. Cooperative planning phase and {{ plan }} in the prompt.** Status:
different.
- CooperBench: paper App D (L1552-1553: "Implementation Plan: {{ plan }}",
  L1585); 59bbf65 planning/ (coop.py, templates/coop.j2); the website's
  legacy trajectories embed the plans. 63b9d44 has no planning phase.
- coordbench: none, as 63b9d44.
- Left undone: an optional coop-planned arm running the legacy planning loop
  (coop.j2 word for word). It needs model calls, an extra phase per pair,
  and whether the paper's numbers included planning is an open question.

**131. Collaborative execution prompt (corrected).** Status: different by
default; same with `--option tool_surface=bash`.
- CooperBench: paper App D (L1544-1610), with openhands_comm_send. 63b9d44:
  _coop/prompt.py build_instruction (134-160), and its claude_code agents
  had only the Bash coop-* commands.
- coordbench: the coop and team arms add one sentence naming the MCP tool
  behind each command (arms/coop/arm.py mcp_sentence; the team arm's
  role_block) and load as a Claude Code plugin with an MCP server and a
  skill. `--option tool_surface=bash` reproduces 63b9d44's surface exactly
  for coop, coop-git and team: no plugin (Arm.claude_surface, AgentJob
  arm_plugin false, drivers.py claude_flags), so no skill and no
  coordbench server; no added sentence, so the authors' text byte for
  byte; the coop-* commands in Bash; and for team the one server
  CooperBench registers, cooperbench-team, serving wait_for_message alone
  (Arm.mcp_tools_for). run_started and result.json's metrics record the
  surface.
- Why: the MCP surface is coordbench's default, a better fit for Claude
  Code; the bash surface is there for the comparison with CooperBench's own
  runs. On the bash surface one difference remains: cli.py install still
  writes coord-<tool> wrappers beside the coop-* commands in
  /usr/local/bin, which no prompt names; left for a change to cli.py,
  which every arm shares. App D's paper-era prompt is not reproduced (a
  coop-paper variant would have to replace openhands_comm_send, so it
  would not be verbatim).

**132. Workspace isolation, hidden tests and gold, Docker task images
(corrected).** Status: different.
- CooperBench: paper Sec 2.1 (L215, L225-244); images
  akhatua/cooperbench-<repo>:task<id>; one container per agent.
- coordbench: claude-cli: one container per agent, tests and gold never
  mounted, as CooperBench. The session driver's subagent runs on the host
  with host file tools, so isolation from the dataset's tests.patch and
  feature.patch and from the partner's spec holds by instruction only;
  result.json and config.json say isolation "advisory"
  (runconfig.driver_isolation, drivers.py SESSION_DIFFERENCES). The images
  are not CooperBench's as published: bench/cooperbench/prepare_task.sh
  pulls them from mirror.gcr.io or rebuilds them (build_task_image.sh),
  and overlay_task_image.sh rewrites each under the same tag (the proxy's
  CA, the claude binary, the redis client); the scorer runs in the same
  overlaid image. config.json records each image's id, registry digests
  and cooperbench.overlay label (runconfig.image_facts).
- Why: architectural; the cloud session's network needs the overlay, and the
  session driver works through the host. Left undone: restricting the
  subagent's Read and Bash to its workspace through permissions.

**133. coop-git setting.** Status: same.
- CooperBench: 63b9d44 --git (_coop/prompt.py _git_block); the website's
  coop_git index.
- coordbench: coordbench/arms/coop-git/arm.py with CooperBench's git block;
  export.py files it under coop_git, so setting=coopGit opens it.

**134. team setting.** Status: different.
- CooperBench: 63b9d44 team mode (runner/team.py, team_harness/). Its
  wait_for_message BLPOPs <agent>:inbox while coop_msg.py pushes to
  run:<id>:<agent>:inbox, so every wait times out.
- coordbench: coordbench/arms/team/arm.py with CooperBench's prompt and
  four differences listed at its top: atomic claims under the run's lock,
  a request's wait capped at 300 s, a wait_for_message that hands over a
  message, and responses put back when a reply reaches nobody; the task
  list's mirror is written from the hub's host.
- Why: better; a long poll that never answers measures nothing, and the
  claim race is fixed. Each difference is listed with its reason.

**135. sdlc arm.** Status: different.
- CooperBench: none.
- coordbench: coordbench/arms/sdlc/arm.py, deciding through the vendored
  sdlc_protocol gate.
- Why: coordbench's own arm.

**M1. Dataset version: the paper-era dataset against 63b9d44's.** Status:
different.
- CooperBench: `git diff --name-status 59bbf65 63b9d44 -- dataset` touches
  108 files (feature.md, tests.patch, one gold feature.patch, runner.sh,
  Dockerfile, combined.patch), and dataset/typst became dataset/typst_task;
  dataset/SPEC_AUDIT.md: OK 164, SPEC 23, TEST 12. CooperBench records no
  dataset commit.
- coordbench: coordbench/runconfig.py provenance (the CooperBench commit and
  modified files, the dataset's git tree, its last commit and a content
  hash, coordbench's own commit) in config.json and every result.json;
  bench/cooperbench/dataset_stats.py dataset_changes (a feature is amended
  when its feature.md, feature.patch or tests.patch changed in content; a
  pure rename is not); report.py dataset_section gives every headline rate
  again over the unamended pairs; `run.py --dataset-dir` points the specs,
  the scorer and the cascade at a 59bbf65 worktree while 63b9d44's code
  scores, and the cascade's basis names the dataset's commit.
- Why: better; CooperBench's files cannot tell which dataset a run used.

**M2. Spec-sufficiency audit verdict per feature (OK, SPEC, TEST).**
Status: same.
- CooperBench: dataset/.spec_audit_verdicts.json, 199 keys
  '<repo>/<task>|<feature>' to [verdict, note] (scripts/spec_audit_survey.py,
  spec_audit_status.py).
- coordbench: bench/cooperbench/dataset_stats.py spec_audit; report.py
  dataset_bases splits the rates by audit class; export.py features_info
  copies each feature's verdict and note into metadata.coordbench.

**M3. Gradeability and combined-patch checks.** Status: different.
- CooperBench: scripts/check_gradeable.py (tests alone fail, tests and gold
  pass; dataset/gradeable_report_docker.json); scripts/check_combined.py
  (tests and combined.patch pass), whose report is not shipped.
- coordbench: bench/cooperbench/prepare_task.sh runs check_gradeable.py per
  task and survey.sh keeps each report; dataset_stats.py gradeability reads
  them; report.py ungradeable_lines lists the pairs with a feature that
  does not grade here.
- Left undone: check_combined.py is not run, and config.json does not record
  which verdicts held for the image a run used. Free compute.

**M7. Mixed-model (cross-play) pairs.** Status: not measured.
- CooperBench: paper Sec 2.1 (L213-218) defines them; 63b9d44 passes one
  model to every agent.
- coordbench: run.py --agent-model AGENT=MODEL gives an agent its own model
  (check_agent_models refuses a name the arm does not lay out), and each
  agent's model is recorded; report.py groups by result.json's model, the
  run's, so ordered cross-play pairs are not reported apart.
- Left undone: the report side. Free; the runs use plan limits.

**M8. Open models through Claude Code (Anthropic-compatible endpoints).**
Status: not measured.
- CooperBench: the claude_code adapter forwards ANTHROPIC_BASE_URL and
  ANTHROPIC_AUTH_TOKEN (adapter.py 134-150), keeps the model name, and has a
  qwen budget profile with SMALL_CONTEXT_DISALLOWED_TOOLS; docs/QWEN_LOCAL.md
  runs Qwen through vLLM.
- coordbench: the drivers pass only CLAUDE_CODE_OAUTH_TOKEN by name
  (drivers.py PASSED_BY_NAME); run_arms.sh unsets ANTHROPIC_BASE_URL for
  CooperBench's own runner; --disallowed-tools exists.
- Left undone: forwarding the two variables by name and porting the budget
  profile. Not built in this round; it is what would let the paper's open
  models run locally and make d(t) finer (row 9).

**M9. Claude Code version and tool surface per agent.** Status:
different.
- CooperBench: agents/claude_code/setup.sh installs @latest at run time and
  records no version; no tool is disallowed except under the qwen profile,
  while the paper says the tools were limited to local operations
  (L237-238).
- coordbench: coordbench/drivers.py stream_init records each agent's
  claude_code_version, tools and mcp_servers from the stream's init event
  (the session driver: the transcript's version) in result.json;
  runconfig.py claude_code_digest sums them in summary.json;
  `--disallowed-tools WebFetch,WebSearch` keeps the agents to local tools,
  passed as --disallowedTools.
- Why: better; a run says what it ran with.

**M10. Run scope and concurrency.** Status: same.
- CooperBench: `cooperbench run` takes a whole subset across repositories
  (runner/core.py discover_tasks) with concurrency 20 and evaluation
  concurrency 10.
- coordbench: coordbench/run.py requested_pairs and subset_tasks (--subset
  alone runs every pair of the subset in discover_tasks' order; --repo and
  --task narrow it) and run_all (--concurrency N pairs at once on one hub,
  default 1, each scored as it ends; --debug is refused above 1).

## Decisions

### The decisions this work was built to

1. Reproduce CooperBench at 63b9d44 by default and record the paper era
   beside it, never mixing the two scoring rules. eval.json's both_passed
   is 63b9d44's own test_merged; the paper era's verdict is a separate
   block under its own names (verdict_paper_era, route), every report
   table names the rule it uses, and so does the comparison viewer's
   outcome cell (row 120). Why: 63b9d44 is the code the owner runs
   and the baseline run_arms.sh measures, while every published number
   came from the paper era's cascade.
2. Message delivery stays pull, as 63b9d44's claude_code adapter delivers.
   Push changes the arm being measured; the Cooperator Plugin
   (cooperator-plugin.astro) is the reference design for a later push
   option.
3. No turn limit for Claude Code, as 63b9d44; --max-turns 100 is
   documented for paper-faithful runs.
4. The model-judged measures (communication errors, symptoms, speech acts,
   emergent tags) are coordbench/judge.py, a separate command the owner runs
   on purpose; no run spends a token on judging. Backend claude-cli by
   default, on the owner's subscription (CLAUDE_CODE_OAUTH_TOKEN or
   COORDBENCH_CLAUDE_TOKEN, never ANTHROPIC_API_KEY), model by --model or
   COORDBENCH_JUDGE_MODEL (default claude-sonnet-5-5); backend
   openai-compatible (the standard library's urllib, --base-url or
   COORDBENCH_JUDGE_URL, COORDBENCH_JUDGE_KEY) so the paper's GPT-5 can be
   matched. Every section records its backend, model, prompt variant and,
   for claude-cli, the CLI's version. A judge model other than the
   paper's is the one deviation that cannot be avoided under claude-cli.
5. Free deterministic cross-checks sit beside every judge label
   (judge.py heuristics_of), and agreement between the two is a cheap check
   on the judge's precision.
6. Session-driver cost: tokens summed from the copied transcript, each API
   message once; cost estimated from a price table vendored as data, the
   Claude rows of the litellm table that CooperBench's agents/pricing.py
   consults first, marked cost_source "estimated from tokens"; claude-cli
   keeps Claude Code's own total_cost_usd, cost_source "claude code".
   Added while building: Claude Code's transcripts hold a message's final
   output count only on its stop_reason line, so when any message lacks it
   the output count and the cost stay null and their lower bounds are
   recorded instead.
7. The learned merge resolver cannot run here (no model server, the
   Hugging Face host blocked): the cascade runs naive and union, records
   the LLM step as not run with the reason, and takes an OpenAI-compatible
   endpoint through COORDBENCH_RESOLVER_URL when one exists. A pair that
   failed naive and union and needed the resolver is a fail of those two
   only (the cascade's lower_bound): export.py passes the step on, and the
   comparison viewer shows it as "paper-era undecided", never as the full
   cascade's fail beside a published verdict that ran it.
8. Difficulty, buckets and AUC are App C's exactly, with their degeneracy
   under one or two models stated in the output and no claim of
   comparability with Table 5.
9. Message text is logged in the run's own events.jsonl, as it is sent, so
   a crash keeps it, and the conversation files are derived from it;
   trace.py's stream carries none of it (arm.py PRIVATE_FIELDS: a field
   named content never reaches a listener, the hub's --debug file
   included).
10. Emergent behaviours and git-setting failures have no paper method, so
    their detectors are coordbench-labelled and optional, never reported
    as CooperBench rates.
11. The comparison viewer is one self-contained page,
    coordbench/viewer/compare.html, with no CDN and no build, and
    coordbench/compare.py embeds chosen runs into a copy of it, so a
    report is one file that opens offline; the page also loads files by
    drag and drop.

### Decisions taken while building

- The cascade runs in a sandbox of its own, since test_merged ends its own
  before it returns and run.py calls it in a subprocess that hands back
  only JSON. On a clean merge it reuses test_merged's test results for the
  same merge rather than run the suites again and risk a flaky
  disagreement with both_passed.
- Patches apply as 63b9d44's _setup_branches applies them (git apply, then
  --3way), where 59bbf65 used --whitespace=fix and stopped the whole
  evaluation on a failure; any failed naive merge is a conflict, as
  63b9d44 has it; every merged diff is taken from the base commit; the
  resolver's merge is tested from its own diff; the resolver endpoint gets
  no /health probe.
- 59bbf65's regex pass rule is kept as a record beside 63b9d44's, not as a
  verdict to trust alone: it takes any "failed" for a failure, so cargo's
  "0 failed" fails every Rust suite of the current dataset.
- The verdicts are named for what they are (verdict_63b9d44,
  verdict_paper_era, verdict_paper_era_regex, with basis naming the
  dataset's commit) rather than "paper_both_passed", and deciding_artifact
  names, per verdict, the file the verdict was read from: agent<f1>.patch
  when 63b9d44 used the lead's patch alone.
- A message event carries send_id beside message_id, since one broadcast
  is several deliveries (kit.py deliver runs once per recipient), and the
  store groups by it.
- The session driver's preface makes the subagent open its first reply
  with a run token naming the run and the agent; nothing else would put
  the prompt's path into the transcript reliably.
- The export splits a concatenated transcript by sessionId and, for
  sidechains, agentId, because subagent records carry the parent's
  sessionId; joins the hub's record by agent, tool and time with the order
  only breaking ties, because refused calls, chained aliases and
  broadcasts make a join by ordinal drift; and adds a third timestamp kind,
  "hub-events", for runs with neither a transcript nor a stream, as the
  session-driver pilots are.
- judge.py's claude-cli backend replaces Claude Code's system prompt with
  the prompt's own text (--system-prompt), turns every tool, MCP server,
  setting source, slash command and saved session off, and runs from an
  empty temporary directory with a throwaway CLAUDE_CONFIG_DIR and HOME, so
  nothing but the prompt reaches the model. For a reasoning model the
  openai-compatible backend sends no temperature, which such a model
  refuses, and records reasoning_effort. A reply that is not JSON gets
  one retry, never more.
- Team requests and responses are part of the judged conversation, though
  CooperBench's conversation.json leaves them out, because unanswered
  requests are Unresponsiveness. The symptom judge takes a pair failed
  under either verdict, and judges nothing where the message text was not
  recorded (the pilots), since a missing text would read as silence.
- On the bash tool surface the hub stands in for CooperBench's
  cooperbench-team server under that name. Claude Code (2.1.296, checked
  against a local fake API and a fake MCP server, no tokens spent) expands
  ${COORD_MCP_URL} and ${COORD_AGENT_TOKEN:-} inside an --mcp-config file
  as it does in a plugin's .mcp.json.
- A pair's git_log.txt is read in place from the git server's volume when
  this host owns it, so the reflog shows force-pushes; a fetch over git://
  carries no reflog.
- An arm's artifact may not take a name the runner, the cascade, the export
  or the judge writes (run.py RUNNER_FILES, RUNNER_SUFFIXES).

### Where the review and the matrix disagreed

The review's 22 corrections are applied in 23 rows, 2, 6, 15, 17, 18, 19,
21, 23, 40, 54, 55, 62, 72, 88, 91, 92, 110, 121, 124, 127, 128, 131 and
132 (one correction is rows 110 and 121 together), and
its additions are rows M1 to M13. Each was checked against the source where
it could be: the gold-conflict summary (499 of 652, 76.5%), the two 3-agent
subsets and their keys, the missing_input precedence in eval/sandbox.py
227-232, generation/prompt.py's _extract_patch_info, causes/index.json's
8, 9 and 6, the conflict counts of coop/index.json, its 45 and 45 typst
rows, and the Wilson intervals over 652. All held.

One correction says less than the source. The review read the published
index's passing rows as 179 for gpt5 and 35 for minimax against
stats.passed's 182 and 91, and concluded the rows cannot reproduce the
headline counts. Every model has 652 rows; those without a merge report
are marked "missing": true with hasConflict null, and some of them still
carry passed true (3 for gpt5, 56 for minimax). Counting them, stats.passed
is reproduced for every model; leaving them out gives the review's
numbers. What does not reproduce is gpt5's stats.rate, 27.95 against
182/652 = 27.91. coordbench uses only the rows with a merge report, as the
paper's conflict rates do, and shows the missing ones as "not published";
what a passing row without a merge report means is an open question.

The review's spec problems are each met in the rows they touch: the
cascade's own sandbox, verdict naming and deciding artifact (row 1), the
difficulty module's degeneracy (row 9), the hub join, send_id, the typst
alias, rounding, the five filters, more than two agents and the sidechain
split (rows 37, 109, 110, 113, 117), the judge's isolation, temperature and
quote verification (row 66), the run token (row 100) and the overhead's two
denominators (row 41). Its last point, the protocol sync automation,
concerns other repositories.

## Left undone

Each item is in its row with its reason; here they are in one place, the
ones a live run would lose first.

- The n-feature scorer for the 3-agent sets (row 15): free, and possible
  afterwards from the patches a run keeps.
- The learned resolver's step (row 58): needs a model server.
- The team arm's switches and the ablation sweep (row 51).
- A push delivery option (rows 40, M4).
- Open models through ANTHROPIC_BASE_URL (M8), cross-play reporting (M7),
  the coop-planned arm (row 130) and a paper-era prompt variant (row 131).
- check_combined.py and per-image gradeability in config.json (M3); the
  scorer-side gold-conflict recompute (row 18).
- In the report: the LimitsExceeded share (row 129).
- The coord-<tool> wrappers on the bash surface (row 131), and restricting
  a session subagent's tools to its workspace (row 132).
- Exported trajectories are keyed by model, arm and driver. Within one
  export call a second run of the same key over a pair goes under the key
  with its run name added, with a warning; across calls, a pair exported
  again under the same key replaces the earlier export, which is how a
  re-export updates a site, so two runs meant to stand side by side are
  exported with their own --model-key.

What has not been exercised for real, since each would spend tokens or
needs a service that is not here: a real Claude Code agent under the new
capture (the end-to-end check replaced the claude binary in the real task
image with a fake that edits, messages and submits); the session driver's
transcript copy and cost estimate on a real subagent's transcript; the
judge against a real model; and the resolver step. The end-to-end pairs
all failed by design, but rescoring copies of the three real
session-driver pilots with run.py rescore ran the cascade on their real
patches: the naive route passed the demo coop pair and the pilot coop
pair (whose identical patches 63b9d44 skips merging), and the union
route passed the pilot team pair, whose naive merge conflicted (2
sections, 24 lines) and which 63b9d44 passed on the lead's patch alone.
The end-to-end pairs took the route none; the learned resolver's route is
covered by the unit tests only, since it needs a model server.

## Open questions only the authors can answer

- App F: which model was the communication-error judge? Did the prompt
  used carry the printed duplicate "C4a Incorrect claim (uncorrected)"?
  What are the code and the definition of Fig 6(d)'s "Plan drift /
  unilateral deviation", the eighth of "eight categories"?
- Fig 6: is the denominator every Coop run or only the runs with a
  message? Is a "turn" in Fig 6(a) a message or a change of speaker?
- Fig 5(c): how were messages typed Plan, Question, Answer, Update and Ack
  (a model, rules or people), and were planning-phase messages counted?
  What unit is an "event" there?
- Table 1: primary labels only? Are implementation-level failures left
  out? May the full rubric App G mentions (verification requirements,
  common confusions) and the annotator's prompt be published? What did
  "matched" mean in App G's 48 of 50?
- Table 2: who labelled the 50 traces and how were they sampled? The blog
  says expectation failure is 63% of failures, Table 2 says 42%, and the
  website's 23 examples split 8, 9, 6: which is the measured share?
- Difficulty: is Solo_m(t) binary or a fraction? Are the AUC's limits 0.05
  to 0.95 or 0 to 1? How are empty buckets handled? What are Sec 5's "8
  difficulty buckets"? Will the per-task Solo results be published, so
  other runs can be placed on the paper's scale?
- Sec 5 (L580-582): are "successful trajectories" those that passed or
  those without a conflict?
- The resolver: Sec 2.2 says Qwen 3 Coder 1.5B and App B
  Qwen2.5-Coder-0.5B-Merge-Resolver. Which one served at localhost:8457 in
  59bbf65's llm_merge.py, whose server module (evaluation.run_server) is
  not in the commit? Does the fine-tune expect llm_merge.py's system
  prompt?
- Table 6's naive merge: git merge-file, as the text says, or git merge
  --no-commit --no-ff, as both code generations run?
- Did the Coop and Solo numbers include the cooperative planning phase
  (59bbf65 planning/, the {{ plan }} of App D)?
- What was the paper's no-communication prompt: App D's scenario with the
  tool removed, or no word of a partner, as 63b9d44's --no-messaging?
- Message delivery: the paper says a message is included "in the prompt of
  the next step". Through which mechanism, for OpenHands, and does the
  Cooperator Plugin reproduce it for Claude Code?
- Which counts are authoritative: Table 5 (gpt-5 183, claude 168, pooled
  sums off by one) or Table 6 and the website (182, 169)? How were the
  leaderboard's intervals computed (GPT-5's matches neither Wilson nor
  Clopper-Pearson over 652), why is gpt5's stats.rate 27.95 rather than
  182/652, and what is a passing index row marked missing?
- Table 3 prints 34 PRs, its column sums to 32 and App A.2 says 52 task
  sets: which is right? Will Table 4's per-feature SWE-Rater-32B labels be
  published?
- The scaling experiment: which 46 tasks, from which three task sets, with
  which model?
- git-failures.json: how were the 13 examples chosen, and is there a
  procedure or a rate behind them?

## Producing each measure

A run captures everything that can only be captured live: the message
text and its read receipts (events.jsonl and the conversation files), the
transcripts and streams, the team's task log and final list and its
scratchpad volume's last state, the shared remote's history with its
reflog, each agent's model, Claude Code version and tools, the provenance
and the image, the timings with the host's UTC offset and the patches.
Everything else is computed afterwards from those files, and only the
judge's model-judged sections, a cause draft and the resolver call a model.

| Measure | Command | Model calls |
| --- | --- | --- |
| A run of an arm, scored by CooperBench (eval.json), with the merge cascade, config.json, summary.json, the conversation files, the trajectories and trajectory.json.gz | `python3 coordbench/run.py run --arm ARM --subset flash [--repo R --task T] --model M --name RUN` | the agents' own, on the owner's subscription; none for scoring |
| 63b9d44's own tool surface | add `--option tool_surface=bash` (coop, coop-git, team) | as a run |
| The communication ablation (Fig 5a and 5b, Table 6) | the same with `--arm coop-nocomm` beside `--arm coop` | as a run |
| Paper-faithful settings | add `--max-turns 100 --disallowed-tools WebFetch,WebSearch`, and `--dataset-dir` a 59bbf65 worktree's dataset/ for the paper era's specs and tests | as a run |
| Cross-play or per-agent models | add `--agent-model agent2=MODEL` | as a run |
| A session-driver run | `python3 coordbench/run.py prepare --arm ARM --repo R --task T --features A,B` (the session driver is prepare's default), one subagent per printed prompt, then `python3 coordbench/run.py finish --run-dir DIR` | the subagents', on the orchestrating session |
| CooperBench's own runner on the same pairs | `bash bench/cooperbench/run_arms.sh REPO TASK [solo coop coop-git team coop-nocomm]` | the agents' own |
| Rescoring old pairs (adds the cascade) | `python3 coordbench/run.py rescore --run-dir RUN_DIR [--force] [--cooperbench-dir DIR] [--dataset-dir DIR]`, coordbench's `cooperbench eval`: every pair below RUN_DIR without eval.json, or whose merged eval.json has no cascade (every pair with --force), is scored from its patches and plan.json; it starts no hub, container or agent whatever state a pair is in (`run.py run` would run an unfinished pair's agents again), and leaves config.json, result.json and summary.json as the run wrote them | none, but the resolver's when its endpoint is set |
| The learned resolver's step | set COORDBENCH_RESOLVER_URL (and COORDBENCH_RESOLVER_MODEL, COORDBENCH_RESOLVER_KEY) before the run or the rescoring | up to four per conflict region (three retries, as 59bbf65's litellm num_retries=3), on that endpoint |
| Rates, gaps, git ablation, pass routes, conflicts, cascade table, agent alone, communication, references, speech-act ratios, the protocol's own counters (sdlc and team), failure-mode rates, difficulty, dataset splits, against the published runs | `python3 bench/cooperbench/report.py --run LABEL=RUN ... [--published WEBSITE/public/static/data/coop/index.json] [--difficulty-out difficulty.json]` | none |
| Difficulty alone | `python3 bench/cooperbench/difficulty.py --solo RUN [--solo RUN2] --arm coop=RUN ... --out difficulty.json` | none |
| Dataset composition, complexity, gold conflicts, version, audit, gradeability | `python3 bench/cooperbench/dataset_stats.py [--cooperbench-dir DIR] [--json out.json]`; gradeability reports from `bash bench/cooperbench/survey.sh` or `prepare_task.sh` | none (Docker compute for gradeability) |
| Free cross-checks (heuristics, git-setting detectors, App D checks) | `python3 coordbench/judge.py heuristics RUN_DIR` | none |
| Communication errors (Fig 6) | `python3 coordbench/judge.py commerrors RUN_DIR [--variant appF-8] [--model M]`, or with `--backend openai-compatible --model gpt-5` and COORDBENCH_JUDGE_URL and COORDBENCH_JUDGE_KEY set, for the paper's annotator model | one per conversation with a message, two when a reply is not JSON; none for a run whose audit.json names a harness error |
| Symptoms (Table 1) | `python3 coordbench/judge.py symptoms RUN_DIR [--variant symptoms-r1-tree]` | one per failed multi-agent pair, two at most; none for a run whose audit.json names a harness error |
| Speech acts (Fig 5c) | `python3 coordbench/judge.py speechacts RUN_DIR` | one per conversation with a message, two when a reply is not JSON; none for a run whose audit.json names a harness error |
| Emergent tags, blame and sagas | `python3 coordbench/judge.py emergent RUN_DIR` | one per conversation with a message, two when a reply is not JSON; none for a run whose audit.json names a harness error |
| What a judge would be sent | add `--dry-run` to any of the four, or `judge.py render SECTION PAIR_DIR` | none |
| Causes (Table 2) | `python3 coordbench/annotate.py causes RUN_DIR --out DIR --sample 50 --seed N`, a person fills DIR/labels.json, then `annotate.py tally DIR/labels.json --apply` | none; `--draft` calls one per drawn pair, two when a reply is not JSON |
| The symptom judge against people (App G) | `python3 coordbench/annotate.py validate RUN_DIR --out DIR`, a person fills DIR/sheet.json, then `annotate.py agreement DIR/sheet.json [OTHER/sheet.json]` | none |
| CooperBench's viewer over coordbench runs | `python3 coordbench/export.py RUN_DIR --site OUT [--causes DIR/labels.json] [--model-key KEY] [--cooperbench-dir DIR]` | none |
| Runs side by side in one offline page | `python3 coordbench/compare.py PAIR_DIR_OR_JSON_GZ ... -o report.html [--label L ...] [--baseline N]` | none |
| One pair's time breakdown | `python3 coordbench/timeline.py PAIR_DIR` | none |
