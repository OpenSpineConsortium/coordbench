"""The cost side of a session-driven run, beside report.py's statistics:
for each arm of a tag, the pass rate, the latency, the tokens and the
estimated cost per pair, with one row per pair in a CSV a later pass can
reuse.

    python3 summary.py --logs LOG_DIR --tag TAG [--arms solo,coop,sdlc,coop-git,team]
        [--out summary.json] [--csv pairs.csv] [--md summary.md]

Latency is read from result.json: duration_seconds is the pair's wall
time from the hub's start to the end of scoring, agents_seconds the wall
time the agents ran (their spans overlap in a two-agent arm, so it is
their union, not their sum), and the per-agent spans come from timings.
Tokens come from each agent's transcript as the session driver summed
them: input, cache read, cache write, and output, which a subagent's
transcript holds whole only on its final reply of each turn and so is a
lower bound (drivers.transcript_usage). Cost is estimated from those
counts at the list prices below, so the arms compare on the same scale;
nothing was billed, the agents ran on a subscription.

What the session route did to each run is read from the audit.json that
lane.py finish writes (audit.py). A run that measured the harness rather
than the arm (an agent the harness lost, a full disk, an agent spawned
twice) is left out of every figure and counted as a harness error, so it
is neither a failure nor a pass of its arm. The API calls and the context
each call sends before any work give the tokens net of that fixed prompt,
which the route sets, not the arm: on the session route every bench
agent sends the orchestrating session's own prompt again on every call.
"""

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

# Dollars per million tokens, Anthropic's list prices of 2026-10 for the
# model every agent ran as. A cache write is priced as a five-minute
# cache entry, 1.25 times the input price; a one-hour entry would be
# twice the input price, which the report notes as the upper figure.
# Haiku's are the prices below the long-prompt tier. A pair whose agent
# ran a model outside this table has no cost and is left out of the
# cost sums, which say how many pairs they cover (priced_pairs).
PRICES = {
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
ARMS_DEFAULT = ("solo", "coop", "sdlc", "coop-git", "team")


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - half) / denominator, (centre + half) / denominator)


def agent_records(result):
    """The per-agent blocks of result.json: `agents`, a dict by agent
    name, in a multi-agent arm, `agent` in solo."""
    agents = result.get("agents")
    if isinstance(agents, dict):
        return [{"id": name, **block} for name, block in agents.items() if isinstance(block, dict)]
    if isinstance(agents, list):
        return [a for a in agents if isinstance(a, dict)]
    if isinstance(result.get("agent"), dict):
        return [{"id": "agent1", **result["agent"]}]
    return []


def tokens_of(agent):
    """The agent's counts, or None when it has none at all: an agent whose
    transcript was never found is unmeasured, not an agent that used no
    tokens, and counting it as 0 would halve its pair."""
    fields = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "output_tokens_lower_bound")
    if all(agent.get(field) is None for field in fields):
        return None
    out = {"input": agent.get("input_tokens") or 0, "cache_read": agent.get("cache_read_tokens") or 0,
           "cache_write": agent.get("cache_write_tokens") or 0}
    output = agent.get("output_tokens")
    out["output"] = output if output is not None else (agent.get("output_tokens_lower_bound") or 0)
    out["output_is_lower_bound"] = output is None
    return out


def cost_of(tokens, model):
    prices = PRICES.get(model)
    if not prices:
        return None
    return sum(tokens[kind] * prices[kind] for kind in ("input", "output", "cache_read", "cache_write")) / 1e6


def count_tool_calls(transcript_path):
    """Tool calls in a subagent's transcript: every tool_use block of an
    assistant line, as CooperBench counts an agent's steps by its calls."""
    calls = 0
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") != "assistant":
                    continue
                for block in (event.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        calls += 1
    except OSError:
        return None
    return calls


def pair_row(tag, arm, pair_dir):
    result = read_json(pair_dir / "result.json")
    if not result:
        return None
    evaluation = read_json(pair_dir / "eval.json") or {}
    metrics = result.get("metrics") or read_json(pair_dir / "metrics.json") or {}
    agents = agent_records(result)
    spans = [t for t in result.get("timings") or [] if t.get("phase") == "agent"]
    tokens = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    lower_bound, cost, tool_calls, model, unmeasured = False, 0.0, 0, result.get("model"), 0
    for agent in agents:
        own = tokens_of(agent)
        if own is None:
            unmeasured += 1
        else:
            for kind in tokens:
                tokens[kind] += own[kind]
            lower_bound = lower_bound or own["output_is_lower_bound"]
        own_cost = cost_of(own, agent.get("model") or model) if own else None
        cost = cost + own_cost if own_cost is not None and cost is not None else None
        transcript = pair_dir / f"{agent.get('id', 'agent')}_session.jsonl"
        if not transcript.exists():
            transcript = pair_dir / "solo_session.jsonl"
        calls = count_tool_calls(transcript) if transcript.exists() else None
        tool_calls = tool_calls + calls if calls is not None and tool_calls is not None else None
    errors = [a.get("error") for a in agents if a.get("error")]
    merge = evaluation.get("merge") if isinstance(evaluation.get("merge"), dict) else {}
    pair = pair_tokens(tokens, lower_bound, unmeasured or not agents)
    route = route_fields(read_json(pair_dir / "audit.json"), pair["total_tokens"])
    return {
        "tag": tag, "arm": arm, "repo": result.get("repo"), "task": result.get("task_id"),
        "features": "_".join(f"f{f}" for f in result.get("features") or []),
        "passed": bool(evaluation.get("both_passed")), "evaluated": bool(evaluation),
        "feature1_passed": bool((evaluation.get("feature1") or {}).get("passed")),
        "feature2_passed": bool((evaluation.get("feature2") or {}).get("passed")),
        "merge_conflict": merge.get("status") == "conflicts" if merge else None,
        "agent_errors": len(errors), "error": "; ".join(errors)[:200] or None,
        "agents": len(agents),
        "wall_seconds": result.get("duration_seconds"),
        "agents_wall_seconds": result.get("agents_seconds"),
        "agent_seconds_sum": sum(t.get("seconds") or 0 for t in spans) or None,
        "agent_seconds_max": max((t.get("seconds") or 0 for t in spans), default=None),
        # A pair with an unmeasured agent has no token count or cost: its
        # sums would read as the pair's and halve it.
        "unmeasured_agents": unmeasured,
        **pair,
        **route,
        "cost_usd": round(cost, 4) if cost is not None else None,
        "tool_calls": tool_calls,
        "messages_sent": metrics.get("messages_sent"), "message_words": metrics.get("message_words"),
        "claims": metrics.get("claims"), "prs_opened": metrics.get("prs_opened"),
        "merge_attempts": metrics.get("merge_attempts"), "merges": metrics.get("merges"),
        "protocol_merge_conflicts": metrics.get("merge_conflicts"),
        "time_to_first_merge_seconds": metrics.get("time_to_first_merge_seconds"),
        "run_id": result.get("run_id"), "log_dir": str(pair_dir),
    }


def route_fields(found, total_tokens):
    """What audit.json says the session route did to the run: why it
    measured the harness, if it did; the API calls; the tokens every call
    sent before any work (calls times the first call's context, per
    agent) and the total net of them; the refusals and the efforts. All
    None when the run has no audit."""
    agents = list((found or {}).get("agents", {}).values())
    calls = [a.get("calls") for a in agents]
    prefix = [a["calls"] * a["first_context"] for a in agents
              if a.get("calls") is not None and a.get("first_context") is not None]
    known = bool(agents) and len(prefix) == len(agents)
    prefix_tokens = sum(prefix) if known else None
    return {
        "audited": found is not None, "harness_error": (found or {}).get("harness_error"),
        "api_calls": sum(calls) if known else None,
        "prefix_tokens": prefix_tokens,
        "net_tokens": total_tokens - prefix_tokens if total_tokens is not None and prefix_tokens is not None else None,
        "refusals": sum(a.get("refusals") or 0 for a in agents) if found else None,
        "efforts": ",".join(sorted({e for a in agents for e in a.get("efforts") or []})) or None,
    }


def pair_tokens(tokens, lower_bound, unknown):
    kinds = {"input_tokens": "input", "cache_read_tokens": "cache_read", "cache_write_tokens": "cache_write",
             "output_tokens": "output"}
    if unknown:
        return {**dict.fromkeys(kinds), "output_is_lower_bound": None, "total_tokens": None}
    return {**{field: tokens[kind] for field, kind in kinds.items()}, "output_is_lower_bound": lower_bound,
            "total_tokens": sum(tokens.values())}


def collect(logs, tag, arms):
    rows = []
    for arm in arms:
        # <logs>/<run name>/<CooperBench setting>/<repo>/<task>/f<a>_f<b>: the
        # setting is coop for every two-agent arm, so it is not the arm's name.
        root = Path(logs) / f"{tag}-{arm}"
        if not root.is_dir():
            continue
        for pair_dir in sorted(p for p in root.glob("*/*/*/f*_f*") if p.is_dir()):
            row = pair_row(tag, arm, pair_dir)
            if row:
                rows.append(row)
    return rows


def exact_median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def median(values):
    value = exact_median(values)
    return round(value, 1) if value is not None else None


def mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 2) if values else None


def summarize_arm(attempts):
    """The arm's rates over every attempt, an agent error counting as a
    failure as the paper counts it, and over the complete attempts (no
    agent in Error); the latency, token and cost figures over the
    complete attempts only, since a cut run's clock ran to its deadline
    and its agents' work is partial. A run that measured the harness is
    no attempt of the arm's and is only counted."""
    harness_errors = [r for r in attempts if r.get("harness_error")]
    attempts = [r for r in attempts if not r.get("harness_error")]
    rows = [r for r in attempts if not r["agent_errors"]]
    n = len(rows)
    k = sum(1 for r in rows if r["passed"])
    low, high = wilson(k, n)
    priced = [r for r in rows if r["cost_usd"] is not None]
    total_cost = sum(r["cost_usd"] for r in priced)
    priced_passed = sum(1 for r in priced if r["passed"])
    all_n = len(attempts)
    all_k = sum(1 for r in attempts if r["passed"] and not r["agent_errors"])
    all_low, all_high = wilson(all_k, all_n)
    return {
        "attempts": all_n, "attempts_passed": all_k,
        "pass_rate_errors_as_failures": round(all_k / all_n, 4) if all_n else None,
        "wilson_95_errors_as_failures": [round(all_low, 4), round(all_high, 4)],
        "pairs": n, "passed": k, "pass_rate": round(k / n, 4) if n else None,
        "wilson_95": [round(low, 4), round(high, 4)],
        "agent_errors": sum(1 for r in attempts if r["agent_errors"]),
        "wall_seconds_median": median([r["wall_seconds"] for r in rows]),
        "wall_seconds_mean": mean([r["wall_seconds"] for r in rows]),
        "agents_wall_seconds_median": median([r["agents_wall_seconds"] for r in rows]),
        "agent_seconds_sum_median": median([r["agent_seconds_sum"] for r in rows]),
        "agent_seconds_sum_mean": mean([r["agent_seconds_sum"] for r in rows]),
        "tokens_per_pair_mean": {kind: mean([r[f"{kind}_tokens"] for r in rows])
                                 for kind in ("input", "cache_read", "cache_write", "output", "total")},
        "tokens_per_pair_median": {kind: median([r[f"{kind}_tokens"] for r in rows])
                                   for kind in ("input", "cache_read", "cache_write", "output", "total")},
        "output_lower_bound_pairs": sum(1 for r in rows if r["output_is_lower_bound"]),
        "unmeasured_token_pairs": sum(1 for r in rows if r["total_tokens"] is None),
        "cost_usd_total": round(total_cost, 2), "priced_pairs": len(priced),
        "cost_usd_per_pair_mean": mean([r["cost_usd"] for r in rows]),
        "cost_usd_per_pair_median": median([r["cost_usd"] for r in rows]),
        "cost_usd_per_passing_pair": round(total_cost / priced_passed, 2) if priced_passed else None,
        "tool_calls_per_pair_mean": mean([r["tool_calls"] for r in rows]),
        "messages_per_pair_mean": mean([r["messages_sent"] for r in rows]),
        "message_words_per_pair_mean": mean([r["message_words"] for r in rows]),
        "merge_conflict_pairs": sum(1 for r in rows if r["merge_conflict"]),
        "claims": sum(r["claims"] or 0 for r in rows), "prs_opened": sum(r["prs_opened"] or 0 for r in rows),
        "merges": sum(r["merges"] or 0 for r in rows),
        "protocol_merge_conflicts": sum(r["protocol_merge_conflicts"] or 0 for r in rows),
        "time_to_first_merge_median": median([r["time_to_first_merge_seconds"] for r in rows]),
        **route_summary(rows, harness_errors),
    }


def route_summary(rows, harness_errors):
    """The session route's share of the arm's figures, over the complete
    attempts that have an audit."""
    audited = [r for r in rows if r.get("audited")]
    both = [r for r in audited if r.get("prefix_tokens") is not None and r.get("total_tokens")]
    share = sum(r["prefix_tokens"] for r in both) / sum(r["total_tokens"] for r in both) if both else None
    return {
        "harness_errors": len(harness_errors),
        "harness_error_runs": sorted(f"{r['repo']}/{r['task']}/{r['features']}: {r['harness_error']}"
                                     for r in harness_errors),
        "audited_pairs": len(audited),
        "api_calls_per_pair_mean": mean([r.get("api_calls") for r in audited]),
        "prefix_share_of_tokens": round(share, 4) if share is not None else None,
        "net_tokens_per_pair_mean": mean([r.get("net_tokens") for r in audited]),
        "net_tokens_per_pair_median": median([r.get("net_tokens") for r in audited]),
        "refusal_pairs": sum(1 for r in audited if r.get("refusals")),
        "efforts": sorted({e for r in audited for e in (r.get("efforts") or "").split(",") if e}),
    }


def paired(rows_a, rows_b):
    """Pairs both arms completed (no agent in Error): both pass, only a,
    only b, neither, and the ratio of b's medians to a's on latency,
    tokens and cost, each over the shared pairs both arms measured it on
    and from the medians before rounding, since a median rounded to one
    decimal (a cost of $0.6) moves the ratio by a tenth."""
    def by_key(rows):
        return {(r["repo"], r["task"], r["features"]): r for r in rows
                if not r["agent_errors"] and not r.get("harness_error")}
    a, b = by_key(rows_a), by_key(rows_b)
    shared = sorted(set(a) & set(b))
    counts = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
    for key in shared:
        pa, pb = a[key]["passed"], b[key]["passed"]
        counts["both" if pa and pb else "only_a" if pa else "only_b" if pb else "neither"] += 1
    def ratio(field):
        measured = [k for k in shared if a[k].get(field) is not None and b[k].get(field) is not None]
        ma, mb = exact_median([a[k][field] for k in measured]), exact_median([b[k][field] for k in measured])
        return round(mb / ma, 3) if ma and mb is not None else None
    return {"shared_pairs": len(shared), **counts,
            "wall_seconds_ratio_b_over_a": ratio("wall_seconds"),
            "agent_seconds_sum_ratio_b_over_a": ratio("agent_seconds_sum"),
            "total_tokens_ratio_b_over_a": ratio("total_tokens"),
            "net_tokens_ratio_b_over_a": ratio("net_tokens"),
            "cost_ratio_b_over_a": ratio("cost_usd")}


def num(value):
    return "-" if value is None else f"{value:,.0f}"


def markdown(summary, arms):
    lines = [f"# {summary['tag']}: pass, latency, tokens and cost per arm", ""]
    lines.append("| arm | complete pairs | passed | rate | 95% Wilson | cut runs (errors) | rate, errors as failures | wall s (median) | agent s sum (median) "
                 "| tokens per pair (mean) | output (mean, lower bound) | cost per pair (mean) | cost per pass | msgs per pair |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        s = summary["arms"].get(arm)
        if not s:
            continue
        rate = f"{100 * s['pass_rate']:.1f}%" if s["pass_rate"] is not None else "-"
        ci = f"{100 * s['wilson_95'][0]:.0f}-{100 * s['wilson_95'][1]:.0f}%"
        all_rate = (f"{100 * s['pass_rate_errors_as_failures']:.1f}% ({s['attempts_passed']}/{s['attempts']})"
                    if s["pass_rate_errors_as_failures"] is not None else "-")
        lines.append(f"| {arm} | {s['pairs']} | {s['passed']} | {rate} | {ci} | {s['agent_errors']} | {all_rate} | "
                     f"{s['wall_seconds_median']} | {s['agent_seconds_sum_median']} | "
                     f"{num(s['tokens_per_pair_mean']['total'])} | {num(s['tokens_per_pair_mean']['output'])} | "
                     f"{'$' + format(s['cost_usd_per_pair_mean'], '.2f') if s['cost_usd_per_pair_mean'] is not None else '-'} | "
                     f"{'$' + format(s['cost_usd_per_passing_pair'], '.2f') if s['cost_usd_per_passing_pair'] else '-'} | "
                     f"{s['messages_per_pair_mean'] if s['messages_per_pair_mean'] is not None else '-'} |")
    lines += ["", f"## Paired against {' and '.join(summary['bases'])}, over the pairs both arms completed "
              "(b over a: below 1 means b is cheaper or faster)", ""]
    lines.append("| a | b | shared | both pass | only a | only b | neither | wall ratio | agent s ratio | tokens ratio "
                 "| net tokens ratio | cost ratio |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, p in summary["paired"].items():
        a, b = key.split(" vs ")
        lines.append(f"| {a} | {b} | {p['shared_pairs']} | {p['both']} | {p['only_a']} | {p['only_b']} | {p['neither']} | "
                     f"{p['wall_seconds_ratio_b_over_a']} | {p['agent_seconds_sum_ratio_b_over_a']} | "
                     f"{p['total_tokens_ratio_b_over_a']} | {p.get('net_tokens_ratio_b_over_a')} | "
                     f"{p['cost_ratio_b_over_a']} |")
    lines += route_table(summary, arms)
    unmeasured = {arm: s["unmeasured_token_pairs"] for arm, s in summary["arms"].items() if s["unmeasured_token_pairs"]}
    lines += ["", f"Prices (USD per million tokens): {json.dumps(PRICES['claude-sonnet-5-5'])}; "
              "output tokens are a lower bound where the table says so (summary.json counts the pairs)."]
    if unmeasured:
        lines.append("Token and cost figures leave out the pairs with an agent whose transcript was not found: "
                     + ", ".join(f"{arm} {n}" for arm, n in unmeasured.items()) + ".")
    lines.append("")
    return "\n".join(lines)


def route_table(summary, arms):
    """What the session route did, per arm: the runs left out as harness
    errors and why, and over the audited complete pairs the API calls, the
    share of tokens the fixed prompt took, the tokens net of it, the pairs
    with a refusal and the efforts the agents ran at."""
    lines = ["", "## What the session route did (audit.json)", "",
             "| arm | harness errors (left out) | audited pairs | API calls per pair (mean) | fixed prompt share "
             "| net tokens per pair (median) | pairs with a refusal | efforts |",
             "|---|---|---|---|---|---|---|---|"]
    notes = []
    for arm in arms:
        s = summary["arms"].get(arm)
        if not s or "harness_errors" not in s:
            continue
        share = f"{100 * s['prefix_share_of_tokens']:.0f}%" if s["prefix_share_of_tokens"] is not None else "-"
        lines.append(f"| {arm} | {s['harness_errors']} | {s['audited_pairs']} | "
                     f"{s['api_calls_per_pair_mean'] if s['api_calls_per_pair_mean'] is not None else '-'} | {share} | "
                     f"{num(s['net_tokens_per_pair_median'])} | {s['refusal_pairs']} | {', '.join(s['efforts']) or '-'} |")
        notes += [f"- {arm}: {run}" for run in s["harness_error_runs"]]
    if notes:
        lines += ["", "Runs left out as harness errors, to be run again:", "", *notes]
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--arms", default=",".join(ARMS_DEFAULT))
    parser.add_argument("--out")
    parser.add_argument("--csv")
    parser.add_argument("--md")
    parser.add_argument("--bases", default="solo,coop",
                        help="the arms every other arm is paired against, in order")
    args = parser.parse_args(argv)
    arms = args.arms.split(",")
    bases = args.bases.split(",")
    rows = collect(args.logs, args.tag, arms)
    by_arm = {arm: [r for r in rows if r["arm"] == arm] for arm in arms}
    summary = {"tag": args.tag, "prices_usd_per_mtok": PRICES, "bases": bases,
               "arms": {arm: summarize_arm(by_arm[arm]) for arm in arms if by_arm[arm]}, "paired": {}}
    for base in bases:
        for arm in arms:
            if arm != base and by_arm.get(base) and by_arm.get(arm):
                summary["paired"][f"{base} vs {arm}"] = paired(by_arm[base], by_arm[arm])
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n")
    if args.csv and rows:
        with open(args.csv, "w", newline="") as handle:
            # LF endings: the repository's check takes no carriage return.
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    text = markdown(summary, arms)
    if args.md:
        Path(args.md).write_text(text + "\n")
    print(text if not args.md else f"{len(rows)} pairs over {len(summary['arms'])} arms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
