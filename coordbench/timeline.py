"""Where one coordbench run's time went: the harness or the agents.

    python3 coordbench/timeline.py <run_dir> [--json]

The run directory is the one that holds result.json (the pair's directory
under the logs); events.jsonl and eval.json beside it are read when they
are there. The breakdown reads in four parts. Infrastructure is the
harness's own cost: the phases around the agents (starting or reaching
the hub, creating the run, the git remote, the shared volume, each
agent's container with its wrappers, closing the run, CooperBench's
scoring, the teardown), summed as recorded, so two containers provisioned
at once count twice, plus the hub's handling time, which is what the hub
spent computing answers over every tool call (each call's duration less
the time it waited for another agent): the harness's overhead per call.
Blocked time is coordination cost: a call that waited on the run's lock
for a peer (a long poll for a message, a request's wait for its answer),
which is what the arm's design asks of the agent, not a slow hub. What is
left of an agent's wall time once blocked, hub and, when Claude Code
reported it, model time are taken out is the agent itself: its shell
commands, its edits and its thinking. The idle stretches say where that
time went: a long gap between one call's end and the agent's next call,
with no coordination in between, is the agent working or thinking alone,
where a long blocked time is the agent waiting on a peer. A run that
predates a field (the pilot runs, which have no timings and no tool_done
events) prints "not recorded" in its place and never fails; a run with no
events.jsonl still prints what result.json holds. The module reads JSON
only and imports nothing of coordbench, so it can be copied alone beside
a run."""

import argparse
import json
import sys
from pathlib import Path

# The run's own phases, in the order the breakdown lists them; "agent" is
# the agents' phase and is read into their table instead.
PHASES = ("hub", "run_create", "git_remote", "shared_volume", "provision", "close", "score", "teardown")
LONGEST_GAPS = 3
NOT_RECORDED = "not recorded"
NOT_APPLICABLE = "-"


def warn(text):
    print(f"timeline: {text}", file=sys.stderr)


# Reading the run directory.

def read_json(path):
    """The file's JSON, or None when it is missing or unreadable; an
    unreadable file is said on stderr, since a missing one is the normal
    case for a run that was never scored."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        warn(f"{path}: cannot read: {error}")
        return None


def read_events(path):
    """events.jsonl as a list of dicts in file order, or None when there
    is no file. A line that is not a JSON object is skipped and counted on
    stderr: a disk that filled mid-run leaves one cut line, not a run
    worth refusing."""
    if not path.is_file():
        return None
    events, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict):
            events.append(event)
        else:
            skipped += 1
    if skipped:
        warn(f"{path}: skipped {skipped} line(s) that are not events")
    return events


def load_run(run_dir):
    """The run directory's three files: result.json, which must be there,
    the events (None without events.jsonl) and the evaluation (None
    without eval.json)."""
    run_dir = Path(run_dir)
    result = read_json(run_dir / "result.json")
    if not isinstance(result, dict):
        raise FileNotFoundError(f"{run_dir / 'result.json'} is not there to read")
    return {"result": result, "events": read_events(run_dir / "events.jsonl"),
            "evaluation": read_json(run_dir / "eval.json")}


# Reading the fields, each absent when the run did not record it.

def number(value):
    """value as a float, or None when it is not a number: null, a missing
    field, or a field a hand-edited file holds as text."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def seconds_of_ms(value):
    milliseconds = number(value)
    return None if milliseconds is None else milliseconds / 1000.0


def agent_records(result):
    """The agents by id: the "agents" block, or the solo setting's
    singular "agent" (CooperBench's solo.py, and run.py for solo), under
    the id run.py records in it, which the timings and events name."""
    agents = result.get("agents")
    if isinstance(agents, dict):
        return agents
    agent = result.get("agent")
    return {agent.get("id") or "solo": agent} if isinstance(agent, dict) else {}


def timing_records(result):
    """The run's phase timings, or None when result.json predates them."""
    timings = result.get("timings")
    if not isinstance(timings, list):
        return None
    return [t for t in timings if isinstance(t, dict) and isinstance(t.get("phase"), str)]


def events_of_kind(events, kind):
    return None if events is None else [e for e in events if e.get("kind") == kind]


def verdict(result, evaluation):
    """The verdict as run.py gives it: error when an agent ended in Error
    or the scorer failed, else pass or fail by both_passed; None when the
    run was never scored."""
    if not isinstance(evaluation, dict):
        return None
    errored = any(isinstance(a, dict) and a.get("status") == "Error" for a in agent_records(result).values())
    if errored or evaluation.get("error"):
        return "error"
    return "pass" if evaluation.get("both_passed") else "fail"


# The breakdown.

def run_header(result, evaluation):
    return {"repo": result.get("repo"), "task_id": result.get("task_id"), "features": result.get("features"),
            "arm": result.get("arm"), "driver": result.get("driver"), "verdict": verdict(result, evaluation),
            "duration_seconds": number(result.get("duration_seconds")),
            "started_at": result.get("started_at"), "ended_at": result.get("ended_at")}


def handling_seconds(done):
    """The hub's own time on one call: its duration less what it waited
    for a peer."""
    duration = seconds_of_ms(done.get("duration_ms")) or 0.0
    blocked = seconds_of_ms(done.get("blocked_ms")) or 0.0
    return duration - blocked


def phase_order(record):
    phase = record["phase"]
    return PHASES.index(phase) if phase in PHASES else len(PHASES)


def infrastructure(timings, dones):
    """Every phase that is not an agent's, in PHASES order and then as
    recorded, their sum, and the hub's handling time over every call."""
    phases = None
    if timings is not None:
        phases = sorted(({"phase": t["phase"], "agent": t.get("agent"), "seconds": number(t.get("seconds"))}
                         for t in timings if t["phase"] != "agent"), key=phase_order)
    return {"phases": phases,
            "total_seconds": None if phases is None else sum(p["seconds"] or 0.0 for p in phases),
            "hub_seconds": None if dones is None else sum(handling_seconds(d) for d in dones)}


def agent_names(result, timings, tool_events):
    """Every agent the run names: result.json's first, then any the
    timings or the tool events add, in the order seen."""
    names = list(agent_records(result))
    for record in timings or []:
        if record["phase"] == "agent":
            add_name(names, record.get("agent"))
    for event in tool_events or []:
        add_name(names, event.get("agent"))
    return names


def add_name(names, name):
    if isinstance(name, str) and name not in names:
        names.append(name)


def wall_seconds(name, record, timings):
    """The agent's wall time and where it came from: its "agent" phase in
    the timings, else Claude Code's duration_ms in its record, else
    nothing."""
    for timing in timings or []:
        if timing["phase"] == "agent" and timing.get("agent") == name and number(timing.get("seconds")) is not None:
            return number(timing["seconds"]), "phase"
    driver = seconds_of_ms(record.get("duration_ms"))
    return (driver, "driver") if driver is not None else (None, None)


def agent_row(name, result, timings, tool_events, dones):
    """One agent's numbers. Blocked, hub, errors and cancelled come from
    its tool_done events and are None when the run recorded none; the
    agent's own time is its wall less blocked, hub and, when known, model
    time, and None when any of the first three is unknown."""
    record = agent_records(result).get(name)
    record = record if isinstance(record, dict) else {}
    wall, source = wall_seconds(name, record, timings)
    mine = None if dones is None else [d for d in dones if d.get("agent") == name]
    blocked = None if mine is None else sum(seconds_of_ms(d.get("blocked_ms")) or 0.0 for d in mine)
    hub = None if mine is None else sum(handling_seconds(d) for d in mine)
    api = seconds_of_ms(record.get("api_duration_ms"))
    own = None if None in (wall, blocked, hub) else wall - blocked - hub - (api or 0.0)
    return {"agent": name, "wall_seconds": wall, "wall_source": source,
            "blocked_seconds": blocked, "hub_seconds": hub,
            "tool_calls": None if tool_events is None else sum(1 for e in tool_events if e.get("agent") == name),
            "errors": None if mine is None else sum(1 for d in mine if d.get("is_error")),
            "cancelled": None if mine is None else sum(1 for d in mine if d.get("cancelled")),
            "api_seconds": api, "own_seconds": own}


def gaps(name, events, basis):
    """The agent's gaps between consecutive calls, in time order. With
    tool_done recorded a gap runs from a call's end to the next call's
    start; without it, from one call's start to the next's, so the gap
    holds the earlier call's own duration too."""
    found, last = [], None
    for event in events:
        if event.get("agent") != name or number(event.get("t")) is None:
            continue
        t, tool = number(event["t"]), event.get("tool")
        if event.get("kind") == "tool":
            if last is not None:
                found.append({"seconds": t - last[0], "start": last[0], "end": t, "before": last[1], "after": tool})
            last = (t, tool) if basis == "tool" else None
        elif event.get("kind") == "tool_done" and basis == "tool_done":
            last = (t, tool)
    return found


def longest_gaps(name, events, basis):
    return sorted(gaps(name, events, basis), key=lambda g: (-g["seconds"], g["start"]))[:LONGEST_GAPS]


def analyse(run):
    """The breakdown as plain data: the header, the infrastructure, one
    row per agent and the idle stretches, None where the run has no
    events.jsonl."""
    result, events, evaluation = run["result"], run["events"], run["evaluation"]
    timings = timing_records(result)
    tool_events = events_of_kind(events, "tool")
    dones = events_of_kind(events, "tool_done") or None
    names = agent_names(result, timings, tool_events)
    basis = None if events is None else ("tool_done" if dones else "tool")
    return {"run": run_header(result, evaluation),
            "infrastructure": infrastructure(timings, dones),
            "agents": [agent_row(n, result, timings, tool_events, dones) for n in names],
            "idle": None if events is None else {"basis": basis,
                                                 "agents": {n: longest_gaps(n, events, basis) for n in names}}}


# Printing it.

def seconds_text(seconds):
    """Seconds for a cell: milliseconds under a second, since the hub's
    handling of one call is that small; "not recorded" for None."""
    if seconds is None:
        return NOT_RECORDED
    if abs(seconds) < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f} s"


def text(value):
    return NOT_RECORDED if value is None else str(value)


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def render_header(run):
    features = run["features"]
    features_text = ", ".join(map(str, features)) if isinstance(features, list) else text(features)
    return [f"# {text(run['repo'])} task {text(run['task_id'])}, features {features_text}: "
            f"arm {text(run['arm'])}, driver {text(run['driver'])}, verdict {text(run['verdict'])}, "
            f"wall {seconds_text(run['duration_seconds'])}",
            "",
            f'Cells read "{NOT_RECORDED}" where the run did not record the field '
            f'and "{NOT_APPLICABLE}" where the field does not apply.']


def render_infrastructure(infra):
    lines = ["", "## Infrastructure", ""]
    if infra["phases"] is None:
        lines.append(f"Phase timings: {NOT_RECORDED} (result.json has no timings).")
    else:
        rows = [[p["phase"], NOT_APPLICABLE if p["agent"] is None else text(p["agent"]), seconds_text(p["seconds"])]
                for p in infra["phases"]]
        rows.append(["sum of phases", NOT_APPLICABLE, seconds_text(infra["total_seconds"])])
        lines += table(["phase", "agent", "seconds"], rows)
    lines += ["", "Hub handling time over every tool call (duration less blocked): "
                  f"{seconds_text(infra['hub_seconds'])}" + ("" if infra["hub_seconds"] is not None
                                                            else " (no tool_done events)") + "."]
    return lines


def wall_text(agent):
    suffix = " (from Claude Code)" if agent["wall_source"] == "driver" else ""
    return seconds_text(agent["wall_seconds"]) + suffix


def render_agents(agents):
    """The agents' table. With model time known for any agent, the
    agent's own time is split into model and local columns; without it,
    one own column holds the model time too."""
    lines = ["", "## Agents", ""]
    if not agents:
        return lines + ["No agents recorded."]
    api_known = any(a["api_seconds"] is not None for a in agents)
    headers = ["agent", "wall", "blocked on peers", "in hub calls", "tool calls", "errors", "cancelled"]
    headers += ["model (api)", "local (shell, edits)"] if api_known else ["own (tools, edits, thinking)"]
    rows = []
    for agent in agents:
        row = [agent["agent"], wall_text(agent), seconds_text(agent["blocked_seconds"]),
               seconds_text(agent["hub_seconds"]), text(agent["tool_calls"]), text(agent["errors"]),
               text(agent["cancelled"])]
        if api_known:
            local = agent["own_seconds"] if agent["api_seconds"] is not None else None
            row += [seconds_text(agent["api_seconds"]), seconds_text(local)]
        else:
            row.append(seconds_text(agent["own_seconds"]))
        rows.append(row)
    return lines + table(headers, rows)


def render_idle(idle):
    lines = ["", "## Idle stretches", ""]
    if idle is None:
        return lines + [f"{NOT_RECORDED} (no events.jsonl)."]
    if idle["basis"] == "tool_done":
        lines.append("The longest gaps from one call's end (tool_done) to the agent's next call: a long gap "
                     "with no coordination call is the agent working or thinking alone, where blocked time "
                     "above is the agent waiting on a peer.")
    else:
        lines.append("No tool_done events: each gap runs from one call's start to the next call's start, "
                     "so it holds the earlier call's own duration too.")
    rows = []
    for name, found in idle["agents"].items():
        if not found:
            rows.append([name] + [NOT_APPLICABLE] * 5)
        for gap in found:
            rows.append([name, seconds_text(gap["seconds"]), f"{gap['start']:.1f}", f"{gap['end']:.1f}",
                         text(gap["before"]), text(gap["after"])])
    return lines + [""] + table(["agent", "gap", "from t", "to t", "tool before", "tool after"], rows)


def render(report):
    lines = (render_header(report["run"]) + render_infrastructure(report["infrastructure"])
             + render_agents(report["agents"]) + render_idle(report["idle"]))
    return "\n".join(lines)


def tidy(value):
    """The report with its floats rounded to the millisecond, for JSON."""
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tidy(v) for v in value]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Where one coordbench run's time went: the harness or the agents.")
    parser.add_argument("run_dir", help="the directory that holds result.json")
    parser.add_argument("--json", action="store_true", help="print the breakdown as JSON rather than Markdown")
    args = parser.parse_args(argv)
    try:
        run = load_run(args.run_dir)
    except FileNotFoundError as error:
        warn(str(error))
        return 2
    report = analyse(run)
    print(json.dumps(tidy(report), indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
