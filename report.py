"""Set the four arms of the experiment side by side, from CooperBench's logs.

Each arm is one CooperBench run (experiment/run_arms.sh names them
flash-<arm>-<tag>), and each pair it ran leaves result.json (the agents'
status, tokens and time) and eval.json (whether both features' tests
passed on the merged code) under logs/<run>/<setting>/<repo>/<task>/<pair>/.
This reads those files and prints Markdown: each arm's pass rate with a
95% Wilson interval next to the authors' number, every pair of arms
compared on the pairs both finished (an exact McNemar test, since the
arms ran the same pairs), and one row per pair. A pair an agent left in
Error, or that has no eval.json yet, is counted apart and never as a
failure, as CooperBench's own pass rate does.

    python3 experiment/report.py [--logs ~/cooperbench/logs] [--tag cc]
"""

import argparse
import json
import math
from itertools import combinations
from pathlib import Path

ARMS = ("solo", "coop", "coop-git", "team")
# The authors' flash results with codex and gpt-5.5, one seed, unreviewed
# (CooperBench's docs/team_harness_ablation_report.html at 63b9d44).
AUTHORS = {"solo": "24/50", "coop": "13/50", "coop-git": "28/50", "team": "31/50"}


def pair_outcome(pair_dir):
    """"pass", "fail" or "error" for one pair, with its usage."""
    result_file, eval_file = pair_dir / "result.json", pair_dir / "eval.json"
    result = json.loads(result_file.read_text()) if result_file.exists() else {}
    agents = result.get("agents") or {}
    usage = {
        "tokens": sum(a.get(k) or 0 for a in agents.values()
                      for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")),
        "seconds": result.get("duration_seconds") or 0,
    }
    if not eval_file.exists() or any(a.get("status") == "Error" for a in agents.values()):
        return "error", usage
    evaluation = json.loads(eval_file.read_text())
    if evaluation.get("error"):
        return "error", usage
    return ("pass" if evaluation.get("both_passed") else "fail"), usage


def arm_outcomes(logs, run_name):
    """Every pair of one arm's run, keyed repo/task/pair."""
    outcomes = {}
    for result_file in sorted((logs / run_name).glob("*/*/*/*/result.json")):
        pair_dir = result_file.parent
        key = "/".join(pair_dir.parts[-3:])
        outcomes[key] = pair_outcome(pair_dir)
    return outcomes


def wilson(passed, total, z=1.96):
    """The 95% Wilson score interval for a pass rate, as two percentages."""
    if total == 0:
        return 0.0, 0.0
    rate = passed / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return 100 * max(0.0, centre - spread), 100 * min(1.0, centre + spread)


def mcnemar_exact(only_first, only_second):
    """Two-sided exact McNemar p-value from the discordant pairs."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_first, only_second) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def summary_rows(arms):
    rows = ["| Arm | Passed | Rate | 95% interval | Errors | Tokens per pair | Minutes per pair | Authors |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm, outcomes in arms.items():
        scored = [o for o, _ in outcomes.values() if o != "error"]
        passed = scored.count("pass")
        low, high = wilson(passed, len(scored))
        rate = 100 * passed / len(scored) if scored else 0.0
        errors = len(outcomes) - len(scored)
        count = max(1, len(outcomes))
        tokens = sum(u["tokens"] for _, u in outcomes.values()) / count
        minutes = sum(u["seconds"] for _, u in outcomes.values()) / 60 / count
        rows.append(f"| {arm} | {passed}/{len(scored)} | {rate:.0f}% | {low:.0f}-{high:.0f}% | {errors} "
                    f"| {tokens:,.0f} | {minutes:.1f} | {AUTHORS.get(arm, '-')} |")
    return rows


def comparison_rows(arms):
    rows = ["| Arms | Pairs both scored | Only the first passed | Only the second passed | Exact McNemar p |",
            "| --- | --- | --- | --- | --- |"]
    for first, second in combinations(arms, 2):
        common = [k for k in arms[first] if k in arms[second]
                  and arms[first][k][0] != "error" and arms[second][k][0] != "error"]
        only_first = sum(arms[first][k][0] == "pass" and arms[second][k][0] == "fail" for k in common)
        only_second = sum(arms[first][k][0] == "fail" and arms[second][k][0] == "pass" for k in common)
        rows.append(f"| {first} vs {second} | {len(common)} | {only_first} | {only_second} "
                    f"| {mcnemar_exact(only_first, only_second):.3f} |")
    return rows


def pair_rows(arms):
    keys = sorted({k for outcomes in arms.values() for k in outcomes})
    rows = ["| Pair | " + " | ".join(arms) + " |", "| --- |" + " --- |" * len(arms)]
    for key in keys:
        cells = [arms[arm].get(key, ("-", None))[0] for arm in arms]
        rows.append(f"| {key} | " + " | ".join(cells) + " |")
    return rows


def render(arms):
    return "\n".join(["# Four arms, side by side", "", "## Pass rates", ""] + summary_rows(arms)
                     + ["", "## Paired comparisons", ""] + comparison_rows(arms)
                     + ["", "## Every pair", ""] + pair_rows(arms)) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", type=Path, default=Path.home() / "cooperbench" / "logs")
    parser.add_argument("--tag", default="cc")
    args = parser.parse_args(argv)
    arms = {arm: arm_outcomes(args.logs, f"flash-{arm}-{args.tag}") for arm in ARMS}
    print(render(arms), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
