"""Run-level records: config.json when a run starts, summary.json when it ends.

CooperBench's runner (src/cooperbench/runner/core.py at 63b9d44, MIT
licence) writes both under <logs>/<run_name>/: _save_config the run's
name, agent framework, model, setting, concurrency, task count and start,
and _save_summary the session's counts, cost, time and per-task results
with the evaluation's tally. run.py writes the same files with the same
keys and formulas, so a reader of CooperBench's runs reads coordbench's,
and adds what CooperBench's files cannot tell apart: the arm, the driver,
the options and limits, which CooperBench and which dataset were used
(the checkout's commit and a hash of the dataset tree, since the dataset
changed after the paper), which image each task ran in, the driver's
departures from CooperBench's claude_code adapter, and, at the end, what
Claude Code versions, models, tools and MCP servers the agents reported.

Three of CooperBench's rules are kept on purpose and stated here. The
summary's pass_rate is passed / max(passed + failed, 1), leaving eval
errors out of the denominator, as _save_summary computes it (its console
line divides by passed + failed + errors instead; both counts are in the
eval block). The eval tally comes from eval.json alone, whatever the
agents' status, as _process_eval_result counts it. And a pair counts as
"skip" when its result.json was already there, its evaluation as skipped
when its eval.json was. One rule differs: "results" lists single-pair
runs too, which CooperBench leaves empty (a gap in its code, not a
design), and a cost nobody measured stays null rather than 0.

Nothing here starts a container: git runs on this host's checkouts and
the one docker command (image_facts) goes through drivers.docker, which
the tests replace.
"""

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import drivers

CONFIG_FILE = "config.json"
SUMMARY_FILE = "summary.json"
# CooperBench's test_merged and test_solo default (eval/sandbox.py at
# 63b9d44), which run.py leaves as it is.
COOPERBENCH_SANDBOX_TIMEOUT_SECONDS = 600
GIT_TIMEOUT_SECONDS = 30
# How many changed paths a record lists before it only counts them.
MODIFIED_LISTED = 50
# The label overlay_task_image.sh puts on a task image it has rewritten.
OVERLAY_LABEL = "cooperbench.overlay"


# Provenance: which CooperBench, which dataset, which coordbench.

def git(directory, *arguments):
    """git's output in the directory without its trailing newline, or
    None when the directory is no git checkout, git is missing or the
    command fails. Only the end is trimmed: a status line starts with a
    space that says the file is unstaged."""
    try:
        done = subprocess.run(["git", "-C", str(directory), *arguments], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, env=git_environment())
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip() if done.returncode == 0 else None


def git_environment():
    """git needs none of the runner's secrets, so it gets none of them."""
    held = {*drivers.PASSED_BY_NAME, *drivers.HELD_BACK}
    return {name: value for name, value in os.environ.items() if name not in held}


def modified_paths(directory, *paths):
    """The tracked files that differ from the commit, or None outside git."""
    status = git(directory, "status", "--porcelain", "--untracked-files=no", "--", *paths)
    if status is None:
        return None
    return [line[3:] for line in status.splitlines() if line.strip()]


def checkout_facts(directory):
    """A checkout's commit and the tracked files it changes; CooperBench's
    is changed on purpose (bench/cooperbench/cooperbench-claude-code-setup.patch),
    so the list says what a run ran beyond the commit."""
    modified = modified_paths(directory)
    return {"dir": str(directory), "commit": git(directory, "rev-parse", "HEAD"),
            "modified": None if modified is None else modified[:MODIFIED_LISTED],
            "modified_count": None if modified is None else len(modified)}


def content_digest(directory):
    """A hash of every file under the directory, by relative path and
    content, for a dataset that is not a clean git tree: the sha256 of the
    lines "<path> <sha256 of the file>", sorted by path."""
    root, lines = Path(directory), []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts):
        lines.append(f"{path.relative_to(root).as_posix()} {hashlib.sha256(path.read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(), len(lines)


def dataset_facts(dataset_dir):
    """Which dataset the specs and the scorer read: git's tree hash for it
    and the last commit that touched it when it sits in a checkout, whether
    it differs from that commit, and a content hash that holds for any
    directory. The paper's dataset is 59bbf65's and the code's 63b9d44's;
    these tell the two apart (108 files changed between them)."""
    dataset_dir = Path(dataset_dir)
    sha256, files = content_digest(dataset_dir) if dataset_dir.is_dir() else (None, 0)
    facts = {"dir": str(dataset_dir), "git_tree": None, "last_commit": None, "modified": None,
             "sha256": sha256, "files": files}
    tree = dataset_tree_name(dataset_dir)
    if tree is not None:
        modified = modified_paths(dataset_dir, ".")
        facts.update(git_tree=git(dataset_dir, "rev-parse", tree),
                     last_commit=git(dataset_dir, "log", "-1", "--format=%H", "--", "."),
                     modified=None if modified is None else bool(modified))
    return facts


def dataset_tree_name(dataset_dir):
    """The revision naming the dataset's tree in its checkout, HEAD:<path>,
    or None when the directory is in no checkout."""
    top = git(dataset_dir, "rev-parse", "--show-toplevel")
    if not top:
        return None
    try:
        relative = dataset_dir.resolve().relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return None
    return "HEAD^{tree}" if relative == "." else f"HEAD:{relative}"


PROVENANCE = {}


def provenance(cooperbench_dir, dataset_dir):
    """The CooperBench checkout, its dataset and coordbench's own checkout,
    worked out once per pair of directories in a process: a long subset
    run asks for it once per pair."""
    key = (str(cooperbench_dir), str(dataset_dir))
    if key not in PROVENANCE:
        PROVENANCE[key] = {"cooperbench": checkout_facts(cooperbench_dir), "dataset": dataset_facts(dataset_dir),
                           "coordbench": checkout_facts(Path(__file__).resolve().parent)}
    return PROVENANCE[key]


IMAGE_FORMAT = "{{.Id}}|{{json .RepoDigests}}|{{index .Config.Labels \"" + OVERLAY_LABEL + "\"}}"


def image_facts(image):
    """The task image as docker holds it here: its id, the registry
    digests it was pulled as (none once overlay_task_image.sh has rebuilt
    it) and the overlay label, so a run says which image its agents and
    its scorer used. Nones when docker does not know the image."""
    try:
        found = drivers.docker("image", "inspect", "-f", IMAGE_FORMAT, image, timeout=60)
    except (OSError, subprocess.SubprocessError):
        found = None  # no docker client here, or one that hangs: the image is unknown
    words = (found.stdout or "").strip().split("|", 2) if found is not None and found.returncode == 0 else []
    if len(words) != 3 or not words[0]:
        return {"image": image, "id": None, "repo_digests": None, "overlay": None}
    try:
        digests = json.loads(words[1] or "[]") or []
    except ValueError:
        digests = None
    return {"image": image, "id": words[0], "repo_digests": digests,
            "overlay": words[2] if words[2] not in ("", "<no value>") else None}


# config.json

def config_record(settings, driver, arm, setting, pairs, images):
    """CooperBench's _save_config keys, in its order, then coordbench's."""
    return {
        "run_name": settings["run_name"], "agent_framework": getattr(driver, "framework", settings["driver"]),
        "model": settings["model"], "setting": setting, "concurrency": settings.get("concurrency", 1),
        "total_tasks": len(pairs), "started_at": datetime.now().isoformat(),
        "arm": arm, "driver": settings["driver"], "options": settings["options"],
        "agent_models": settings.get("agent_models") or {}, "max_turns": settings["max_turns"],
        "timeout_s": settings["timeout_s"], "disallowed_tools": settings.get("disallowed_tools") or [],
        "scoring_timeout_s": settings.get("scoring_timeout_s"),
        "cooperbench_sandbox_timeout_s": COOPERBENCH_SANDBOX_TIMEOUT_SECONDS,
        "isolation": driver_isolation(driver),
        "differences_from_cooperbench": list(getattr(driver, "differences_from_cooperbench", ()) or ()),
        "provenance": provenance(settings["cooperbench_dir"], settings["dataset_dir"]),
        "images": images,
        "pairs": [{"repo": repo, "task_id": task_id, "features": list(features)} for repo, task_id, features in pairs],
    }


def driver_isolation(driver):
    """How far the agent is kept to its own container: the driver says,
    or a driver whose agent works on host files is taken as advisory."""
    return getattr(driver, "isolation", None) or ("advisory" if getattr(driver, "host_workspace", False)
                                                    else "container")


def write_config(run_root, record, replace=True):
    """config.json, written whole as CooperBench writes it; with replace
    False an existing one is kept (prepare, one pair of a run the
    orchestrating session drives pair by pair)."""
    path = Path(run_root) / CONFIG_FILE
    if path.exists() and not replace:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, record)
    return path


def write_json(path, value):
    drivers.write_text(path, json.dumps(value, indent=2, default=str) + "\n")


# summary.json

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def eval_status(evaluation):
    """A pair's eval entry as _run_with_progress records it: error, pass
    or fail by eval.json alone; None when there is no evaluation."""
    if not isinstance(evaluation, dict):
        return None
    if evaluation.get("error"):
        return "error"
    return "pass" if evaluation.get("both_passed") else "fail"


def task_label(repo, task_id, features):
    """CooperBench's results key: repo/task/features, the features joined
    by commas in the order the task names them."""
    return f"{repo}/{task_id}/{','.join(str(f) for f in features)}"


def pair_report(run_dir, task, status, eval_skipped=False):
    """One pair of the session, from what it left on disk: the entry
    "results" lists ({task, status, cost, eval}, plus error for a pair the
    runner could not run) and whether its evaluation was already there.
    status is CooperBench's: done, skip, or error for a pair that failed
    before its agents ran (its result.json carries a top-level error) or
    whose audit.json names a harness error."""
    result = read_json(Path(run_dir) / "result.json") or {}
    harness_error = (read_json(Path(run_dir) / "audit.json") or {}).get("harness_error")
    if status == "done" and (result.get("error") or harness_error):
        status = "error"
    entry = {"task": task, "status": status, "cost": result.get("total_cost")}
    if status == "error":
        entry["error"] = result.get("error") or f"harness error: {harness_error}"
    if harness_error:
        # The session route's audit (bench/cooperbench/session/audit.py)
        # found the run measured the harness, not the arm: an error, as
        # its summary and report count it, not a pass or a fail.
        entry["harness_error"] = harness_error
    if result.get("error_kind"):
        entry["error_kind"] = result["error_kind"]
    entry["eval"] = None if status == "error" else eval_status(read_json(Path(run_dir) / "eval.json"))
    # _process_eval_result checks an evaluation's error before its skipped
    # flag, so an errored evaluation found on disk is an error, never a skip.
    skipped = eval_skipped and entry["eval"] in ("pass", "fail")
    return {"entry": entry, "eval_skipped": skipped, "run_dir": str(run_dir)}


def eval_tally(reports):
    """passed, failed, errors and skipped, as _process_eval_result adds
    them up: a skipped evaluation still counts as its pass or fail."""
    statuses = [r["entry"]["eval"] for r in reports]
    return {"passed": statuses.count("pass"), "failed": statuses.count("fail"), "errors": statuses.count("error"),
            "skipped": sum(1 for r in reports if r["eval_skipped"])}


def total_cost(reports):
    """The session's cost as _run_with_progress sums it, over the pairs
    whose cost was measured; null when none was."""
    costs = [r["entry"]["cost"] for r in reports if r["entry"]["cost"] is not None]
    return sum(costs) if costs else None


def summary_record(run_name, reports, total_time_seconds, totals, extras):
    """_save_summary's keys and formulas, then coordbench's (extras)."""
    tally = eval_tally(reports)
    statuses = [r["entry"]["status"] for r in reports]
    rate = tally["passed"] / max(tally["passed"] + tally["failed"], 1)
    return {
        "run_name": run_name, "completed_at": datetime.now().isoformat(), "pass_rate": rate,
        "total_tasks": len(reports), "completed": statuses.count("done"), "skipped": statuses.count("skip"),
        "failed": statuses.count("error"), "total_cost": total_cost(reports),
        "total_time_seconds": total_time_seconds, "results": [r["entry"] for r in reports],
        # The errors that were the machine's (Docker, the hub), counted
        # apart from setup and agent errors so a rerun can tell them apart.
        "infrastructure_errors": sum(1 for r in reports if r["entry"].get("error_kind") == "infrastructure"),
        "eval": {"total_evaluated": tally["passed"] + tally["failed"] + tally["errors"], **tally, "pass_rate": rate},
        # CooperBench's console rate, beside _save_summary's above.
        "pass_rate_over_evaluated": tally["passed"] / max(tally["passed"] + tally["failed"] + tally["errors"], 1),
        "run_totals": totals, **extras,
    }


def run_totals(setting_dir):
    """get_run_totals (utils.py at 63b9d44) over every result.json of the
    run's setting, earlier sessions included: the wall time from the
    earliest start to the latest end, the run time summed over the pairs,
    the cost and the pair count."""
    cost, run_time, count, starts, ends = 0.0, 0.0, 0, [], []
    for path in sorted(Path(setting_dir).rglob("result.json")):
        result = read_json(path)
        if not isinstance(result, dict):
            continue
        cost += result.get("total_cost") or 0
        run_time += result.get("duration_seconds") or 0
        count += 1
        starts += [datetime.fromisoformat(result["started_at"])] if result.get("started_at") else []
        ends += [datetime.fromisoformat(result["ended_at"])] if result.get("ended_at") else []
    wall = (max(ends) - min(starts)).total_seconds() if starts and ends else 0.0
    return {"wall_time": wall, "run_time": run_time, "total_cost": cost, "task_count": count}


def result_agents(result):
    """The agent records of one result.json: the "agents" block, or the
    solo setting's singular "agent"."""
    agents = result.get("agents")
    if isinstance(agents, dict) and agents:
        return list(agents.values())
    return [result["agent"]] if isinstance(result.get("agent"), dict) else []


def claude_code_digest(run_dirs):
    """What the agents of these pairs ran with, as each one's result.json
    records it from its stream's init event or its transcript: the
    distinct Claude Code versions, models, tools, MCP servers (name and
    status) and cost sources, and how many agents ran another model than
    the one asked for."""
    found = {"claude_code_versions": set(), "models": set(), "tools": set(), "mcp_servers": set(),
             "cost_sources": set()}
    mismatches = 0
    for run_dir in run_dirs:
        for agent in result_agents(read_json(Path(run_dir) / "result.json") or {}):
            for key, field in (("claude_code_versions", "claude_code_version"), ("models", "model"),
                               ("cost_sources", "cost_source")):
                if agent.get(field):
                    found[key].add(agent[field])
            found["tools"].update(agent.get("tools") or [])
            found["mcp_servers"].update(server_text(s) for s in agent.get("mcp_servers") or [])
            mismatches += bool(agent.get("model_differs"))
    return {**{key: sorted(values) for key, values in found.items()}, "model_mismatches": mismatches}


def server_text(server):
    if isinstance(server, dict):
        return f"{server.get('name')} ({server.get('status')})" if server.get("status") else str(server.get("name"))
    return str(server)


def reports_from_disk(setting_dir):
    """Every pair of the run's setting as a report, for finish, which runs
    one pair of a run the orchestrating session drives pair by pair and so
    refreshes the summary from what is on disk: each pair counts as done,
    its evaluation as found."""
    reports = []
    for path in sorted(Path(setting_dir).glob("*/*/*/result.json")):
        result = read_json(path) or {}
        task = task_label(result.get("repo"), result.get("task_id"), result.get("features") or [])
        reports.append(pair_report(path.parent, task, "done"))
    return reports


def write_summary(run_root, record):
    path = Path(run_root) / SUMMARY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, record)
    return path
