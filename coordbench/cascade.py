"""The paper's merge cascade, scored beside CooperBench 63b9d44's verdict.

CooperBench's runner at 63b9d44 scores a pair with test_merged
(src/cooperbench/eval/sandbox.py): a naive git merge whose tests decide
when it is clean, and agent1's patch alone against both suites when it
conflicts or a patch did not apply. The paper's numbers came from the
evaluator before it (commit 59bbf65, evaluation/evaluate.py
run_merge_evaluation and _run_multi_aggregation): a naive merge, a union
merge when the naive one conflicts, a learned resolver when the union's
result fails a suite, and no agent1-alone fallback. eval.json keeps
CooperBench's own keys and verdict as test_merged gives them; run() adds
this module's block beside them, so one run answers both "what does
CooperBench's runner say today" and "what would the paper have said".

How it runs, and why:

- In a sandbox of its own. test_merged makes its sandbox and ends it
  before it returns, and coordbench calls it in a subprocess that hands
  back only JSON, so nothing of it is left to merge again. This module
  makes a new sandbox with CooperBench's own backend and drives it with
  CooperBench's own helpers (_load_patch, _filter_test_files,
  _write_patch, _setup_branches, _merge_naive, _merge_union, _run_tests),
  each run in CooperBench's virtualenv by SANDBOX_SCRIPT, a loop that
  answers one request at a time; RemoteSandbox is its other end. The
  helpers are private, so the tests pin them against 63b9d44 on a real
  git repository whenever CooperBench's checkout is present.
- The naive merge's tests are test_merged's own when it merged cleanly
  (the same helpers on the same input), so the cascade agrees with
  both_passed on every clean pair instead of running the suites again
  and risking a flaky disagreement.
- Two pass rules for every test run: 63b9d44's (exit code 0 and a parsed
  passed count above 0, _run_tests), which is the default, and 59bbf65's
  regex (evaluation/test_runner.py _parse_test_output, exit code ignored)
  beside it, under the keys ending in _regex. The regex takes any
  "failed" for a failure, case aside, so cargo's "0 failed" fails every
  Rust suite of the current dataset: it is the paper era's record, not a
  verdict to trust alone.
- Conflict size by 59bbf65's own line scan (core/merge.py
  _analyze_diff_content and _calculate_conflict_score, reporter.py
  _get_severity_level) over the conflicted merge's `git diff`, as
  59bbf65's merge() captured it; 63b9d44 dropped these numbers.
- Each agent's patch alone against both suites, four runs, shows when the
  agents redivided the features and when both did the same work; 63b9d44
  tests agent1 alone only after a conflict, and drops what it finds
  unless both suites pass.
- The learned resolver (59bbf65 evaluation/llm_merge.py, the paper's
  Qwen2.5-Coder-0.5B-Merge-Resolver) needs a model server coordbench does
  not run. It runs only when COORDBENCH_RESOLVER_URL (or the settings key
  resolver_url) names an OpenAI-compatible API base, with that file's
  system prompt, five lines of context, temperature 0, three retries and
  its splice; otherwise the block says it did not run and why, and
  lower_bound says when the paper-era verdict could have been higher.

What differs from 59bbf65, each kept for a reason:

- The test-file filter is 63b9d44's for both verdicts, so both judge the
  same merged artifacts; 59bbf65 kept only a patch's code files
  (core/patch.py _filter_patch). dropped_by_filter lists, per agent, what
  each filter drops, so the pairs where the difference matters are known.
- Patches apply as _setup_branches applies them (git apply, then --3way);
  59bbf65 used `git apply --whitespace=fix` and stopped the whole
  evaluation on a failure. A patch that does not apply leaves its branch
  empty here, and has_naive_merge_conflict is null, since that merge is
  not a merge of the two agents' work.
- Any failed naive merge is a conflict, as 63b9d44's _merge_naive has
  it; 59bbf65 raised on a failure without a content conflict.
- Every merged diff is taken from the base commit (git diff BASE HEAD), as
  63b9d44's are; 59bbf65 diffed against agent 2's branch.
- The resolver's merge is tested from its own diff. 59bbf65's call
  passed the workspace to run_tests, which tests <workspace>/feature.patch
  or else the gold feature.patch (test_runner.py run_tests); a file
  nothing writes, so not reproduced.
- The resolver's endpoint gets no /health probe; 59bbf65 refused to run
  without one on localhost:8457.
"""

import base64
import contextlib
import io
import json
import os
import re
import select
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import drivers  # noqa: E402
import trace  # noqa: E402

CASCADE_VERSION = 1
# test_merged's own default, which run.py leaves as it is; DockerSandbox
# holds each command in the sandbox to it.
SANDBOX_TIMEOUT = 600
# One helper call (a test run is the longest), and the whole cascade, which
# gets as long as run.py gives test_merged (SCORING_TIMEOUT_SECONDS).
CALL_TIMEOUT = 3600
CASCADE_TIMEOUT = 3 * 3600
CLOSE_TIMEOUT = 120
BACKEND = "docker"

NAIVE_DIFF = "merge_naive.diff"
UNION_DIFF = "merge_union.diff"
LLM_DIFF = "merge_llm.diff"
DIFF_FILES = (NAIVE_DIFF, UNION_DIFF, LLM_DIFF)
AGENTS = ("agent1", "agent2")
TESTS_PATCHES = ("tests1.patch", "tests2.patch")
REPO = "/workspace/repo"
STAGING = "/patches/coordbench_file.b64"
# A shell argument holds at most 128 KiB (MAX_ARG_STRLEN), so a file
# goes into the sandbox in pieces well under it.
CHUNK = 65536
ERRORS_KEPT = 20

RESOLVER_URL = "COORDBENCH_RESOLVER_URL"
RESOLVER_MODEL = "COORDBENCH_RESOLVER_MODEL"
RESOLVER_KEY = "COORDBENCH_RESOLVER_KEY"
# The model name 59bbf65's litellm call sent the server ("openai/" is
# litellm's routing prefix, not part of the name).
DEFAULT_RESOLVER_MODEL = "gpt-3.5-turbo"
RESOLVER_TIMEOUT = 310.0
RESOLVER_RETRIES = 3
CONTEXT_LINES = 5
NO_RESOLVER = ("no resolver endpoint: set COORDBENCH_RESOLVER_URL to an OpenAI-compatible API base serving "
               "the paper's learned resolver (Qwen2.5-Coder-0.5B-Merge-Resolver); coordbench runs no model server")
# 59bbf65 evaluation/llm_merge.py SYSTEM_PROMPT, word for word.
RESOLVER_PROMPT = """You are an expert software engineer resolving git merge conflicts.

RULES:
- Remove ALL conflict markers (<<<<<<< HEAD, =======, >>>>>>> branch_name)
- Merge BOTH features with MINIMAL changes
- Keep EXACT existing logic and structure from both branches
- Do NOT add any new code that wasn't in either branch
- Do NOT restructure, reorganize, or refactor
- Output ONLY the resolved code without conflict markers

Resolve this conflict:"""

RULES = {
    "verdict_63b9d44": "eval.json both_passed, from CooperBench's test_merged: the naive merge's tests when it is "
                       "clean, else agent1's patch alone against both suites",
    "verdict_paper_era": "naive merge, then union when naive conflicts, then the learned resolver when union fails "
                         "a suite (59bbf65 run_merge_evaluation); no agent1-alone fallback; 63b9d44's pass rule",
    "verdict_paper_era_regex": "the same cascade under 59bbf65's pass rule",
    "pass_rule": "exit code 0 and a parsed passed count above 0 (63b9d44 eval/sandbox.py _run_tests)",
    "pass_rule_regex": "59bbf65 evaluation/test_runner.py _parse_test_output: a pass word and no failure word, "
                       "exit code ignored",
    "patch_apply": "63b9d44 _setup_branches: git apply, then git apply --3way; 59bbf65 used git apply "
                   "--whitespace=fix and stopped the evaluation when a patch failed",
    "conflict_size": "59bbf65 core/merge.py _analyze_diff_content over the conflicted merge's git diff; "
                     "conflict_score 20 per section and 2 per line; severity by reporter.py _get_severity_level",
    "llm": "59bbf65 evaluation/llm_merge.py: its system prompt, 5 lines of context, temperature 0, 3 retries, "
           "its splice; the merge then tested from its own diff",
}
TEST_FILE_FILTER = {
    "verdict_63b9d44": "63b9d44 eval/sandbox.py _filter_test_files",
    "verdict_paper_era": "63b9d44 eval/sandbox.py _filter_test_files, so both verdicts judge the same merges; "
                         "59bbf65 kept only code files (core/patch.py _filter_patch), see dropped_by_filter",
}

# 59bbf65 core/patch.py: the patterns its filter sorted a patch's files by.
LEGACY_TEST_PATTERNS = (
    r"test[s]?/", r"__test[s]?__/", r"spec[s]?/", r"\.test\.", r"\.spec\.", r"_test\.", r"_spec\.",
    r"test_.*\.py$", r".*_test\.py$", r".*Test\.(java|kt|scala)$", r".*Tests\.(java|kt|scala)$",
)
LEGACY_DOC_PATTERNS = (
    r"readme", r"\.md$", r"\.rst$", r"\.txt$", r"docs?/", r"documentation/", r"\.wiki", r"changelog",
    r"contributing", r"license", r"authors", r"install", r"usage",
)
LEGACY_DEPENDENCY_PATTERNS = (
    r"pyproject\.toml$", r"uv\.lock$", r"requirements.*\.txt$", r"Pipfile$", r"Pipfile\.lock$", r"poetry\.lock$",
    r"package\.json$", r"package-lock\.json$", r"yarn\.lock$", r"pnpm-lock\.yaml$", r"composer\.json$",
    r"composer\.lock$", r"Gemfile$", r"Gemfile\.lock$", r"go\.mod$", r"go\.sum$", r"Cargo\.toml$", r"Cargo\.lock$",
    r"setup\.py$", r"setup\.cfg$", r"environment\.ya?ml$", r"conda-environment\.ya?ml$", r"\.conda$",
    r"requirements\.in$", r"dev-requirements\.txt$", r"test-requirements\.txt$",
)
LEGACY_PATH_PATTERNS = (r"^diff --git a/(.+) b/(.+)$", r"^--- a/(.+)$", r"^\+\+\+ b/(.+)$")
LEGACY_CATEGORIES = ("code", "docs", "tests", "dependencies")


# The entry point.

def run(plan, evaluation, settings=None):
    """The cascade block for eval.json, after test_merged scored the pair
    (evaluation is that eval.json record; plan is run.py's, of which this
    reads repo, task_id, features, setting, error, run_dir and
    cooperbench_dir). The merged diffs go into the pair's directory
    beside eval.json. A failure in the sandbox or a step is the block's
    error, never an exception: scoring must not end a run."""
    settings, evaluation = settings or {}, evaluation or {}
    reason = skip_reason(plan, evaluation, settings)
    if reason:
        return {"cascade_version": CASCADE_VERSION, "ran": False, "reason": reason}
    block, artifacts = new_block(plan, evaluation, settings), {}
    with trace.span("cascade", repo=plan["repo"], task_id=plan["task_id"]):
        try:
            inputs = pair_inputs(plan, settings)
            with opened_sandbox(plan) as sandbox:
                fill(block, artifacts, sandbox, inputs, evaluation, resolver_config(settings))
        except Exception as problem:
            block["error"] = f"{type(problem).__name__}: {problem}"
    write_artifacts(plan["run_dir"], artifacts)
    return block


def skip_reason(plan, evaluation, settings):
    if settings.get("cascade") is False:
        return "turned off for this run"
    if plan.get("setting") == "solo":
        return "the solo setting has one patch, so nothing to merge"
    if len(plan.get("features") or []) != 2:
        return "CooperBench scores pairs of features only"
    if plan.get("error"):
        return f"the pair ended in error: {plan['error']}"
    if evaluation.get("error"):
        return f"CooperBench's evaluation failed, so the cascade did not run: {evaluation['error']}"
    return None


def new_block(plan, evaluation, settings=None):
    """The block's keys in reading order, the verdicts first; the steps
    fill the rest, and a step that never ran leaves its keys null. The
    basis names the dataset's commit, which differs from the code's when
    run.py was given another dataset (a 59bbf65 worktree)."""
    commit = cooperbench_commit(plan["cooperbench_dir"])
    data = cooperbench_commit(dataset_dir(plan, settings or {}))
    return {
        "cascade_version": CASCADE_VERSION, "ran": True,
        "basis": f"the paper's cascade on the CooperBench {data[:7] if data else '(unknown commit)'} dataset",
        "cooperbench_commit": commit,
        "verdict_63b9d44": bool(evaluation.get("both_passed")),
        "verdict_paper_era": None, "verdict_paper_era_regex": None,
        "route": None, "route_regex": None, "lower_bound": None, "verdict_63b9d44_rerun": None,
        "deciding_artifact": {"verdict_63b9d44": artifact_63b9d44(evaluation, plan["features"]),
                              "verdict_paper_era": None},
        "apply_status": None, "has_naive_merge_conflict": None,
        "naive": None, "union": None, "llm": None, "agent_alone": None,
        "test_file_filter": TEST_FILE_FILTER, "dropped_by_filter": None, "filters_agree": None,
        "rules": RULES, "error": None,
    }


def pair_inputs(plan, settings=None):
    """The two agents' patches as run.py hands them to test_merged (agent1
    is the lower feature), and the two features' hidden tests, from the
    dataset test_merged was handed (run.py's --dataset-dir, else the
    checkout's own), so both verdicts judge the same tests."""
    run_dir, features = Path(plan["run_dir"]), list(plan["features"])
    task_dir = dataset_dir(plan, settings or {}) / plan["repo"] / f"task{plan['task_id']}"
    return {"features": features,
            "patches": [read_text(run_dir / f"agent{feature}.patch") for feature in features],
            "tests": [(task_dir / f"feature{feature}" / "tests.patch").read_text(encoding="utf-8")
                      for feature in features]}


def dataset_dir(plan, settings):
    given = settings.get("dataset_dir") or plan.get("dataset_dir")
    return Path(given) if given else Path(plan["cooperbench_dir"]) / "dataset"


def read_text(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_artifacts(run_dir, artifacts):
    """Each merged diff whole; a diff an earlier scoring left that this one
    did not make is removed, so no file outlives the step it came from."""
    for name in DIFF_FILES:
        path = Path(run_dir) / name
        if name in artifacts:
            drivers.write_text(path, artifacts[name])
        elif path.exists():
            path.unlink()


def cooperbench_commit(cooperbench_dir):
    try:
        done = subprocess.run(["git", "-C", str(cooperbench_dir), "rev-parse", "HEAD"], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


# The cascade, over any sandbox with RemoteSandbox's methods.

def fill(block, artifacts, sandbox, inputs, evaluation, resolver):
    """Every step in 59bbf65's order, then each agent alone, then the
    verdicts. Each step writes its key as it ends, so a failure part way
    leaves what came before it in the block."""
    base, agent2_head = prepare(block, sandbox, inputs)
    status = block["apply_status"]
    block["naive"] = naive_step(sandbox, base, agent2_head, status, evaluation, artifacts)
    block["has_naive_merge_conflict"] = None if "failed" in status.values() else block["naive"]["conflict"]
    block["union"] = union_step(sandbox, base, block["naive"], artifacts)
    block["llm"] = llm_step(sandbox, base, agent2_head, block["union"], resolver, inputs["features"], artifacts)
    block["agent_alone"] = agent_alone_step(sandbox, base, status, inputs["features"])
    block.update(verdicts(block))


def prepare(block, sandbox, inputs):
    """test_merged's first steps: filter, write, branch. Returns the base
    commit and agent 2's branch head, which later steps reset to, since
    _merge_naive and _merge_union commit their merge onto that branch."""
    filtered = [sandbox.filter_patch(patch) for patch in inputs["patches"]]
    block["dropped_by_filter"] = filter_comparison(inputs["patches"], filtered)
    block["filters_agree"] = all(set(d["63b9d44"]) == set(d["59bbf65"]) for d in block["dropped_by_filter"].values())
    for name, content in [("patch1.patch", filtered[0]), ("patch2.patch", filtered[1]),
                          (TESTS_PATCHES[0], inputs["tests"][0]), (TESTS_PATCHES[1], inputs["tests"][1])]:
        sandbox.write_patch(name, content)
    setup = sandbox.setup_branches()
    if setup.get("error") or not setup.get("base_sha"):
        raise SandboxError(setup.get("error") or "Failed to get base commit SHA")
    block["apply_status"] = setup.get("apply_status") or {"agent1": "unknown", "agent2": "unknown"}
    return setup["base_sha"], branch_head(sandbox, "agent2")


def branch_head(sandbox, branch):
    done = sandbox.exec("bash", "-c", f"cd {REPO} && git rev-parse {branch}")
    head = done["output"].strip().splitlines()[-1:] if done["returncode"] == 0 else []
    if not head:
        raise SandboxError(f"no head for branch {branch}: {done['output'][-500:]}")
    return head[0]


def naive_step(sandbox, base, agent2_head, status, evaluation, artifacts):
    with step("naive"):
        merged = sandbox.merge_naive(base)
        conflict = bool(merged.get("conflict"))
        diff = conflicted_diff(sandbox, agent2_head) if conflict else merged.get("diff") or ""
        artifacts[NAIVE_DIFF] = diff
        result = {"conflict": conflict, **conflict_size(diff), "diff_file": NAIVE_DIFF}
        if conflict:
            return {**result, **judged([None, None]), "tests_from": None}
        reused = tests_from_evaluation(evaluation, status)
        records = reused or run_both(sandbox, base, "naive_diff.patch")
        return {**result, **judged(records), "tests_from": "test_merged" if reused else "cascade"}


CONFLICT_CAPTURE = """
cd /workspace/repo
git checkout --force agent2 >/dev/null 2>&1
git reset --hard {head} >/dev/null 2>&1
git clean -fd >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
git diff > /patches/naive_conflict.diff
git merge --abort >/dev/null 2>&1 || git reset --hard {head} >/dev/null 2>&1
"""


def conflicted_diff(sandbox, agent2_head):
    """The merge again, left conflicted long enough for `git diff`, as
    59bbf65's merge() read it; 63b9d44's _merge_naive aborts at once and
    keeps nothing. (59bbf65's fallback for an empty diff read stage 0 of
    each path, which a conflicted path does not have, so it is left out.)"""
    sandbox.exec("bash", "-c", CONFLICT_CAPTURE.format(head=agent2_head))
    return read_file(sandbox, "/patches/naive_conflict.diff")


def tests_from_evaluation(evaluation, status):
    """test_merged's results for the merge this step also made, when its
    merge was clean (or the patches identical) and both patches applied;
    None when the step must run the suites itself."""
    merge = evaluation.get("merge") or {}
    records = [evaluation.get("feature1"), evaluation.get("feature2")]
    if merge.get("status") not in ("clean", "identical") or "failed" in status.values():
        return None
    if not all(isinstance(record, dict) for record in records):
        return None
    return [test_record(record) for record in records]


def union_step(sandbox, base, naive, artifacts):
    if not naive["conflict"]:
        return {"ran": False, "clean": None, **judged([None, None]), "diff_file": None,
                "reason": "the naive merge was clean"}
    with step("union"):
        merged = sandbox.merge_union(base)
        if merged.get("error"):
            return {"ran": True, "clean": False, **judged([None, None]), "diff_file": None}
        artifacts[UNION_DIFF] = merged.get("diff") or ""
        return {"ran": True, "clean": True, **judged(run_both(sandbox, base, "union_diff.patch")),
                "diff_file": UNION_DIFF}


def llm_step(sandbox, base, agent2_head, union, resolver, features, artifacts):
    """59bbf65 resolves only when union's merge fails a suite; that is
    asked under both pass rules, so either verdict can use the step."""
    if not union["ran"] or (passes(union) and passes(union, "_regex")):
        reason = "the naive merge was clean" if not union["ran"] else "the union merge passed both suites"
        return {"ran": False, "needed": False, "reason": f"not needed: {reason}"}
    if resolver is None:
        return {"ran": False, "needed": True, "reason": NO_RESOLVER}
    with step("llm"):
        result = {"ran": True, "needed": True, "endpoint": shown_url(resolver["url"]), "model": resolver["model"],
                  "prompt": "59bbf65 evaluation/llm_merge.py SYSTEM_PROMPT", "context_lines": CONTEXT_LINES,
                  "temperature": 0}
        files = remerge_conflicts(sandbox, agent2_head)
        total, resolved, errors = resolve_files(sandbox, files, endpoint_resolver(resolver))
        result.update(total_conflicts=total, resolved=resolved, errors=errors[:ERRORS_KEPT])
        if not total:
            return {**result, **judged([None, None]), "diff_file": None,
                    "note": "the merge did not conflict again, so nothing was resolved"}
        artifacts[LLM_DIFF] = commit_resolution(sandbox, base, features)
        return {**result, **judged(run_both(sandbox, base, "llm_diff.patch")), "diff_file": LLM_DIFF}


REMERGE = """
cd /workspace/repo
rm -f .git/index.lock
git checkout --force agent2 >/dev/null 2>&1
git reset --hard {head} >/dev/null 2>&1
git clean -fd >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
git -c core.quotePath=false diff --name-only --diff-filter=U > /patches/llm_unmerged.txt
"""
COMMIT_RESOLUTION = """
cd /workspace/repo
git add -A >/dev/null 2>&1
git commit -m {message} >/dev/null 2>&1
git diff {base} HEAD > /patches/llm_diff.patch
"""


def remerge_conflicts(sandbox, agent2_head):
    """The conflicted merge once more, from agent 2's branch as it was
    before the union step committed onto it; the paths left unmerged."""
    sandbox.exec("bash", "-c", REMERGE.format(head=agent2_head))
    listing = read_file(sandbox, "/patches/llm_unmerged.txt")
    return [line.strip() for line in listing.splitlines() if line.strip()]


def resolve_files(sandbox, paths, resolve):
    """apply_llm_resolutions over the conflicted files: a file that is not
    there (the deleting side won) or holds no markers is passed over."""
    total = resolved = 0
    errors = []
    for path in paths:
        text = read_repo_file(sandbox, path)
        if text is None:
            continue
        new_text, found, done, problems = apply_resolutions(text, resolve)
        if not found:
            continue
        total, resolved = total + found, resolved + done
        errors += [f"{path}: {problem}" for problem in problems]
        write_repo_file(sandbox, path, new_text)
    return total, resolved, errors


def commit_resolution(sandbox, base, features):
    message = shlex.quote(f"LLM merge resolution for features {features[0]} and {features[1]}")
    sandbox.exec("bash", "-c", COMMIT_RESOLUTION.format(message=message, base=base))
    return read_file(sandbox, "/patches/llm_diff.patch")


def agent_alone_step(sandbox, base, status, features):
    with step("agent_alone"):
        return {agent: alone(sandbox, base, status.get(agent), number, feature)
                for number, (agent, feature) in enumerate(zip(AGENTS, features), 1)}


def alone(sandbox, base, applied, number, feature):
    """One agent's patch against both suites, as 63b9d44 tests agent1's
    after a conflict; a patch that is empty or did not apply is not run."""
    result = {"feature": feature, "patch_file": f"agent{feature}.patch"}
    if applied != "applied":
        why = "the patch was empty" if applied == "skipped" else "the patch did not apply"
        return {**result, **judged([None, None]), "reason": why}
    return {**result, **judged(run_both(sandbox, base, f"patch{number}.patch"))}


def step(name):
    return trace.span("cascade_step", step=name)


# Test runs, under both pass rules.

def run_both(sandbox, base, feature_patch):
    """One merged artifact against both features' suites, as _run_tests
    runs it: reset to base, then runner.sh with the two patches."""
    return [test_record(sandbox.run_tests(tests, feature_patch, base)) for tests in TESTS_PATCHES]


def test_record(result):
    """A test run under both rules. _run_tests names the output "output";
    test_merged's clean path renames it "test_output"."""
    output = result.get("output")
    if output is None:
        output = result.get("test_output") or ""
    return {"passed": bool(result.get("passed")), "passed_regex": legacy_passed(output),
            "exit_code": result.get("exit_code"), "tests_passed": result.get("tests_passed", 0),
            "tests_failed": result.get("tests_failed", 0), "test_output": output}


def judged(records):
    """f1 and f2 for a step: each feature's suite passed under 63b9d44's
    rule, the same under 59bbf65's regex, and the runs; null when the
    step ran no tests."""
    first, second = records
    return {"f1": first["passed"] if first else None, "f2": second["passed"] if second else None,
            "f1_regex": first["passed_regex"] if first else None,
            "f2_regex": second["passed_regex"] if second else None,
            "tests": {"f1": first, "f2": second}}


def passes(result, rule=""):
    return result.get(f"f1{rule}") is True and result.get(f"f2{rule}") is True


TESTS_RAN = re.compile(r"(passed|PASSED|Test execution completed|\bok\b)")
TEST_FAILURE = re.compile(r"(FAIL:|FAILED|failed.*tests?.*failed)", re.IGNORECASE)
TEST_SUCCESS = re.compile(r"(\bok\b|passed|PASSED)")


def legacy_passed(output):
    """59bbf65 evaluation/test_runner.py _parse_test_output."""
    if not TESTS_RAN.search(output):
        return False
    return bool(TEST_SUCCESS.search(output)) and not TEST_FAILURE.search(output)


# The verdicts.

def verdicts(block):
    route, route_regex = cascade_route(block), cascade_route(block, "_regex")
    return {"route": route, "verdict_paper_era": route != "none",
            "route_regex": route_regex, "verdict_paper_era_regex": route_regex != "none",
            "lower_bound": block["llm"]["needed"] and not block["llm"]["ran"],
            "verdict_63b9d44_rerun": rerun_63b9d44(block),
            "deciding_artifact": {**block["deciding_artifact"], "verdict_paper_era": artifact_paper_era(block, route)}}


def cascade_route(block, rule=""):
    """59bbf65 _run_multi_aggregation: naive_ok, union_ok and llm_ok are
    exclusive, in that order; "none" fails the pair."""
    if not block["naive"]["conflict"] and passes(block["naive"], rule):
        return "naive"
    if block["union"]["ran"] and passes(block["union"], rule):
        return "union"
    if block["llm"]["ran"] and passes(block["llm"], rule):
        return "llm"
    return "none"


def rerun_63b9d44(block):
    """test_merged's rule over this sandbox's runs, a check on the verdict
    eval.json holds: clean and both applied, the naive merge's tests;
    otherwise agent1's patch alone."""
    if "failed" not in block["apply_status"].values() and not block["naive"]["conflict"]:
        return passes(block["naive"])
    return passes(block["agent_alone"]["agent1"])


def artifact_63b9d44(evaluation, features):
    """What test_merged's verdict was read from: the clean merge, or
    agent1's patch (identical patches, or the agent1-alone fallback);
    null when agent1's patch never applied, so nothing was tested."""
    merge = evaluation.get("merge") or {}
    if merge.get("status") == "clean":
        return NAIVE_DIFF
    if merge.get("status") == "identical" or (evaluation.get("apply_status") or {}).get("agent1") == "applied":
        return f"agent{features[0]}.patch"
    return None


def artifact_paper_era(block, route):
    """The step that passed, or when none did, the last merge the cascade
    made (the conflicted naive diff when that was all there was)."""
    by_route = {"naive": NAIVE_DIFF, "union": UNION_DIFF, "llm": LLM_DIFF}
    if route in by_route:
        return by_route[route]
    return next(block[name]["diff_file"] for name in ("llm", "union", "naive") if block[name].get("diff_file"))


# Conflict size: 59bbf65's line scan and its two summaries.

def conflict_size(diff):
    details = conflict_details(diff)
    score = conflict_score(details)
    return {**details, "conflict_score": score, "severity": severity(score)}


def conflict_details(diff):
    """59bbf65 core/merge.py _analyze_diff_content, line for line."""
    sections = lines = 0
    inside = False
    for line in (diff or "").splitlines():
        line = line.strip()
        if "<<<<<<< " in line:
            sections += 1
            inside = True
        elif "=======" in line:
            continue
        elif ">>>>>>>" in line:
            inside = False
        elif inside:
            lines += 1
    average = lines / sections if sections > 0 else 0
    return {"conflict_sections": sections, "conflict_lines": lines, "avg_lines_per_conflict": round(average, 2)}


def conflict_score(details):
    """59bbf65 core/merge.py _calculate_conflict_score."""
    return details["conflict_sections"] * 20 + details["conflict_lines"] * 2


def severity(score):
    """59bbf65 evaluation/reporter.py _get_severity_level."""
    for level, floor in (("CRITICAL", 100), ("HIGH", 50), ("MEDIUM", 20), ("LOW", 0)):
        if score > floor:
            return level
    return "NONE"


# The two test-file filters, compared.

def filter_comparison(raw_patches, filtered):
    """Per agent, the files each filter drops: 63b9d44's (the one used) by
    comparing the patch with what _filter_test_files left of it, 59bbf65's
    by its own sorting."""
    return {agent: {"63b9d44": sorted(set(patch_files(raw)) - set(patch_files(kept))),
                    "59bbf65": legacy_dropped(raw)}
            for agent, raw, kept in zip(AGENTS, raw_patches, filtered)}


def patch_files(patch):
    return [match.group(2) for match in re.finditer(r"^diff --git a/(.+) b/(.+)$", patch or "", re.MULTILINE)]


def legacy_dropped(patch):
    """The files of a patch 59bbf65's _filter_patch dropped: every file
    section split_patch_by_type did not put under "code"."""
    sections = [s for s in re.split(r"(?m)^(?=diff --git)", patch or "") if s.startswith("diff --git")]
    return sorted(path for section in sections if legacy_section_category(section) != "code"
                  for path in patch_files(section)[:1])


def legacy_section_category(section):
    """split_patch_by_type: the first of code, docs, tests, dependencies
    that any of the section's paths falls under (parse_patch_file_paths)."""
    paths = set()
    for line in section.split("\n"):
        for pattern in LEGACY_PATH_PATTERNS:
            match = re.match(pattern, line)
            if match:
                paths.add(match.group(2) if pattern.startswith("^diff") else match.group(1))
    found = {legacy_category(path) for path in paths if path and path != "/dev/null"}
    return next((category for category in LEGACY_CATEGORIES if category in found), None)


def legacy_category(path):
    """categorize_files, which matches its patterns against the lowered
    path (so its two capitalised Java patterns never match)."""
    lowered = path.lower()
    for category, patterns in (("tests", LEGACY_TEST_PATTERNS), ("docs", LEGACY_DOC_PATTERNS),
                               ("dependencies", LEGACY_DEPENDENCY_PATTERNS)):
        if any(re.search(pattern, lowered) for pattern in patterns):
            return category
    return "code"


# The learned resolver: 59bbf65's llm_merge.py, with its server a plug point.

def resolver_config(settings):
    """The endpoint, from the settings or the environment; the key from
    the environment only, so it is never written into a run's files."""
    url = settings.get("resolver_url") or os.environ.get(RESOLVER_URL)
    if not url:
        return None
    return {"url": url.rstrip("/"), "key": os.environ.get(RESOLVER_KEY),
            "model": settings.get("resolver_model") or os.environ.get(RESOLVER_MODEL) or DEFAULT_RESOLVER_MODEL}


def endpoint_resolver(config):
    """resolve_conflict_with_llm: the prompt, then the answer without its
    markdown fence."""
    def resolve(context):
        return strip_markdown_blocks(ask_endpoint(config, f"{RESOLVER_PROMPT}\n\n{context}"))
    return resolve


def ask_endpoint(config, prompt):
    """One chat completion, as litellm sent it: the prompt as the one user
    message, temperature 0, 310 s, three retries."""
    body = json.dumps({"model": config["model"], "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.get("key"):
        headers["Authorization"] = f"Bearer {config['key']}"
    problem = None
    for _ in range(1 + RESOLVER_RETRIES):
        request = urllib.request.Request(f"{config['url']}/chat/completions", data=body, headers=headers)
        try:
            with opener_for(config["url"]).open(request, timeout=RESOLVER_TIMEOUT) as response:
                return json.load(response)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as error:
            error.close()
            problem = error
        except (OSError, ValueError, LookupError, TypeError) as error:
            problem = error
    raise problem


def opener_for(url):
    """A server on this host is reached directly, whatever proxy is set
    for outbound traffic; any other through the environment's proxy."""
    if urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def shown_url(url):
    """The endpoint as the block records it, without any user or password."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def strip_markdown_blocks(text):
    """59bbf65 llm_merge.py strip_markdown_blocks."""
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[0].strip().startswith("```"):
        lines.pop(0)
    if lines and lines[-1].strip() == "```":
        lines.pop()
    return "\n".join(lines)


def conflict_regions(text, context_lines=CONTEXT_LINES):
    """59bbf65 llm_merge.py extract_conflict_regions: each region with
    up to five lines either side, cut back where a neighbour's own context
    begins; kept as written, overlaps included, since its numbers came
    from it."""
    lines = text.splitlines(keepends=True)
    total, regions, start, inside = len(lines), [], -1, False
    for index, line in enumerate(lines):
        if line.startswith("<<<<<<<"):
            inside, start = True, index
        elif line.startswith(">>>>>>>") and inside:
            inside = False
            regions.append({"conflict_start": start, "conflict_end": index})
    for index, region in enumerate(regions):
        before = max(0, region["conflict_start"] - context_lines)
        after = min(total, region["conflict_end"] + 1 + context_lines)
        if index > 0:
            before = max(before, min(total, regions[index - 1]["conflict_end"] + 1 + context_lines))
        if index < len(regions) - 1:
            after = min(after, max(0, regions[index + 1]["conflict_start"] - context_lines))
        region.update(context_start=before, context_end=after, context_string="".join(lines[before:after]))
    return regions


def apply_resolutions(text, resolve):
    """apply_llm_resolutions for one file: the file read in text mode (so
    CRLF becomes LF), regions found by splitlines, spliced into the
    file's readlines from the last region back. Its splice is kept as
    written: the resolution's last line gets no newline, so it joins the
    line after the context. Returns the new text, the regions, how many
    were resolved, and the errors of the others."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    regions = conflict_regions(text)
    if not regions:
        return text, 0, 0, []
    lines, resolved, problems = io.StringIO(text).readlines(), 0, []
    for region in reversed(regions):
        try:
            pieces = resolve(region["context_string"]).split("\n")
        except Exception as problem:
            problems.append(f"{type(problem).__name__}: {problem}")
            continue
        pieces = [piece + "\n" if index < len(pieces) - 1 and not piece.endswith("\n") else piece
                  for index, piece in enumerate(pieces)]
        lines = lines[:region["context_start"]] + pieces + lines[region["context_end"]:]
        resolved += 1
    return "".join(lines), len(regions), resolved, problems


# Files in the sandbox, through its exec.

def read_file(sandbox, path):
    done = sandbox.exec("cat", path)
    if done["returncode"] != 0:
        raise SandboxError(f"cannot read {path}: {done['output'][-500:]}")
    return done["output"]


def read_repo_file(sandbox, path):
    done = sandbox.exec("cat", f"{REPO}/{path}")
    return done["output"] if done["returncode"] == 0 else None


def write_repo_file(sandbox, path, text):
    """As _write_patch writes, through base64, but in pieces, since a file
    can be longer than one shell argument may be."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sandbox.exec("bash", "-c", f": > {STAGING}")
    for start in range(0, len(encoded), CHUNK):
        sandbox.exec("bash", "-c", f"printf %s {encoded[start:start + CHUNK]} >> {STAGING}")
    done = sandbox.exec("bash", "-c", f"base64 -d {STAGING} > {shlex.quote(f'{REPO}/{path}')}")
    if done["returncode"] != 0:
        raise SandboxError(f"cannot write {path}: {done['output'][-500:]}")


# The sandbox: CooperBench's helpers in its virtualenv, one call at a time.

SANDBOX_SCRIPT = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
# Answers go out on the process's own stdout; anything else printed there,
# by CooperBench or a library, goes to stderr instead.
channel = os.fdopen(os.dup(1), "w", encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr
from cooperbench.eval import sandbox as cb
from cooperbench.utils import get_image_name

state = {"sandbox": None}


def opened():
    if state["sandbox"] is None:
        raise RuntimeError("no sandbox is open")
    return state["sandbox"]


def open_sandbox(repo, task_id, backend, timeout):
    image = get_image_name(repo, task_id)
    state["sandbox"] = cb.get_backend(backend).create_sandbox(image, timeout)
    container = getattr(state["sandbox"], "_container", None)
    return {"image": image, "container": getattr(container, "id", None)}


def execute(args):
    result = opened().exec(*args)
    return {"returncode": result.returncode, "output": result.stdout_read() + result.stderr_read()}


def close():
    sandbox, state["sandbox"] = state["sandbox"], None
    if sandbox is not None:
        sandbox.terminate()
    return {}


OPERATIONS = {
    "open": open_sandbox,
    "filter_patch": lambda content: cb._filter_test_files(cb._load_patch(content) or ""),
    "write_patch": lambda name, content: cb._write_patch(opened(), name, content),
    "setup_branches": lambda: cb._setup_branches(opened()),
    "merge_naive": lambda base_sha: cb._merge_naive(opened(), base_sha),
    "merge_union": lambda base_sha: cb._merge_union(opened(), base_sha),
    "run_tests": lambda tests_patch, feature_patch, base_sha: cb._run_tests(
        opened(), tests_patch, feature_patch, base_sha),
    "exec": execute,
    "close": close,
}

try:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        request = json.loads(line)
        operation = request.pop("op")
        try:
            answer = {"ok": OPERATIONS[operation](**request)}
        except Exception as problem:
            answer = {"error": type(problem).__name__ + ": " + str(problem)}
        channel.write(json.dumps(answer, default=str) + "\n")
        channel.flush()
        if operation == "close":
            break
finally:
    close()
'''


class SandboxError(RuntimeError):
    """A helper failed in CooperBench's sandbox, or the sandbox stopped answering."""


class RemoteSandbox:
    """SANDBOX_SCRIPT's other end. Each method is one of CooperBench's
    helpers run on the sandbox that process holds, with the helper's own
    arguments and answer (exec answers {returncode, output}). A call that
    outlives its time, or the cascade's, leaves the channel out of step,
    so closing then kills the process and removes the container."""

    def __init__(self, process, log, deadline):
        self.process, self.log, self.deadline = process, log, deadline
        self.pending, self.container, self.broken = b"", None, False

    @classmethod
    def start(cls, cooperbench_dir, budget=CASCADE_TIMEOUT):
        """In CooperBench's virtualenv when it has one, as run.py runs
        test_merged, in isolated mode, without the agents' credentials."""
        cooperbench_dir = Path(cooperbench_dir)
        python = cooperbench_dir / ".venv" / "bin" / "python"
        log = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(
                [str(python if python.exists() else sys.executable), "-I", "-c", SANDBOX_SCRIPT,
                 str(cooperbench_dir / "src")],
                cwd=cooperbench_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0,
                env=scorer_environment())
        except BaseException:
            log.close()
            raise
        return cls(process, log, time.monotonic() + budget)

    def call(self, operation, wait=None, **arguments):
        """One helper, its arguments by name; wait is how long to wait for
        its answer, by default what is left of the cascade's time."""
        if self.broken:
            raise SandboxError("the sandbox channel is out of step after an earlier failure")
        if wait is None:
            wait = max(0.0, min(CALL_TIMEOUT, self.deadline - time.monotonic()))
        self.send({"op": operation, **arguments})
        answer = self.receive(operation, wait)
        if "error" in answer:
            raise SandboxError(f"{operation}: {answer['error']}")
        return answer.get("ok")

    def send(self, request):
        try:
            self.process.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
            self.process.stdin.flush()
        except OSError as problem:
            self.broken = True
            raise SandboxError(f"the sandbox helper is gone: {self.log_tail()}") from problem

    def receive(self, operation, wait):
        deadline = time.monotonic() + wait
        while b"\n" not in self.pending:
            left = deadline - time.monotonic()
            if left <= 0:
                self.broken = True
                raise SandboxError(f"{operation}: no answer within {wait:.0f} s")
            ready, _, _ = select.select([self.process.stdout], [], [], left)
            if not ready:
                continue
            chunk = os.read(self.process.stdout.fileno(), 1 << 16)
            if not chunk:
                self.broken = True
                raise SandboxError(f"{operation}: the sandbox helper exited: {self.log_tail()}")
            self.pending += chunk
        line, _, self.pending = self.pending.partition(b"\n")
        return json.loads(line)

    def log_tail(self, size=2000):
        self.log.seek(0)
        return self.log.read().decode("utf-8", errors="replace")[-size:].strip()

    def open(self, repo, task_id, backend=BACKEND, timeout=SANDBOX_TIMEOUT):
        answer = self.call("open", repo=repo, task_id=task_id, backend=backend, timeout=timeout)
        self.container = answer.get("container")
        return answer

    def filter_patch(self, content):
        return self.call("filter_patch", content=content)

    def write_patch(self, name, content):
        self.call("write_patch", name=name, content=content)

    def setup_branches(self):
        return self.call("setup_branches")

    def merge_naive(self, base_sha):
        return self.call("merge_naive", base_sha=base_sha)

    def merge_union(self, base_sha):
        return self.call("merge_union", base_sha=base_sha)

    def run_tests(self, tests_patch, feature_patch, base_sha):
        return self.call("run_tests", tests_patch=tests_patch, feature_patch=feature_patch, base_sha=base_sha)

    def exec(self, *args):
        return self.call("exec", args=[str(arg) for arg in args])

    def close(self):
        """Ask the helper to end the sandbox; failing that, end both here."""
        try:
            if not self.broken and self.process.poll() is None:
                self.call("close", wait=CLOSE_TIMEOUT)
                self.process.wait(timeout=CLOSE_TIMEOUT)
        except (SandboxError, subprocess.TimeoutExpired):
            self.broken = True
        finally:
            if self.broken or self.process.poll() is None:
                self.kill()
            for stream in (self.process.stdin, self.process.stdout, self.log):
                stream.close()

    def kill(self):
        """The process, and the container it can no longer end: a killed
        helper runs no finally, so the sandbox would stay up."""
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if self.container:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                drivers.docker("rm", "-f", self.container, timeout=60)


def scorer_environment():
    """run.py's environment_without_secrets, and the resolver's key too:
    the sandbox's process needs none of them."""
    withheld = {*drivers.PASSED_BY_NAME, *drivers.HELD_BACK, RESOLVER_KEY}
    return {name: value for name, value in os.environ.items() if name not in withheld}


@contextlib.contextmanager
def opened_sandbox(plan):
    """A fresh sandbox of the pair's image (CooperBench's get_image_name,
    as test_merged takes it), ended however the cascade ends. Tests
    replace this one function."""
    sandbox = RemoteSandbox.start(plan["cooperbench_dir"])
    try:
        sandbox.open(plan["repo"], plan["task_id"])
        yield sandbox
    finally:
        sandbox.close()
