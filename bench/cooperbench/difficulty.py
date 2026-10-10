"""Relative difficulty, difficulty-stratified curves, AUC and retention (App C).

The paper (Sec 4 L489-491, App C L1409-1424, Algorithm 1) defines, for
each task pair t and the set M of models run Solo, a raw difficulty
d(t) = 1 - (1/|M|) sum over m of Solo_m(t), rescales it by min-max to
d~(t) in [0, 1] over the benchmark, splits [0, 1] into 10 equal-width
buckets, and per model and bucket computes the Solo and Coop success
rates k/n with 95% Wilson intervals. Each curve is summarised by its
area (trapezoidal integration), Delta AUC = AUC_Solo - AUC_Coop, and
retention = AUC_Coop / AUC_Solo; across models it sums the raw counts
per bucket rather than averaging rates. None of this is in CooperBench's
code at any commit, so it is built here from the text, and every choice
the text leaves open is made in the open and written into the output:

- Solo_m(t) is the share of model m's scored Solo runs of t that passed
  (both features' tests, eval.json both_passed): 0 or 1 with one run per
  model, a fraction with k repeated runs. The paper does not say which;
  its Fig 4 Right fills 9 buckets from 5 models, which binary outcomes
  cannot, so it was probably not binary.
- A pair counts only when it was scored (an eval.json without an error);
  an unscored attempt says nothing about how hard the pair is.
- d(t) uses every model that scored t Solo; a pair missing from some
  model's runs is kept and its models are listed with it.
- The curves of an arm and model run over the pairs that both the arm
  and that model's Solo scored, so the two curves are paired; n is the
  number of pairs, and with repeated runs each run is one trial.
- An empty bucket is kept with n = 0 and no point. The area runs over
  the filled midpoints only, from the first to the last, and those limits
  are written with it; with fewer than two filled buckets there is no
  area.

With the Claude models of one subscription |M| is 1 or 2, so d(t) takes
the values {0, 1} or {0, 0.5, 1} and at most 3 of the 10 buckets fill.
The curves are then degenerate and not comparable with Table 5's five
models, and the output says so with the number of filled buckets beside
every area. More models (free local ones through ANTHROPIC_BASE_URL) or
repeated Solo runs are what make d(t) finer.

    python3 bench/cooperbench/difficulty.py --logs ~/cooperbench/logs
        --solo flash-solo-cc [--solo flash-solo-opus ...]
        --arm coop=flash-coop-cc [--arm team=flash-team-cc ...] [--out difficulty.json]
"""

import argparse
import json
import math
from pathlib import Path

BUCKETS = 10
Z = 1.96
# Table 5: Solo and Coop counts k over 652, AUC_Solo, AUC_Coop, Delta AUC, retention.
TABLE_5 = {
    "gpt-5": (315, 183, 0.506, 0.325, 0.181, 0.64), "claude": (307, 168, 0.469, 0.283, 0.186, 0.60),
    "minimax": (236, 91, 0.374, 0.171, 0.203, 0.46), "qwen coder": (141, 87, 0.236, 0.148, 0.088, 0.63),
    "qwen": (41, 30, 0.106, 0.072, 0.034, 0.68), "pooled": (1039, 558, 0.338, 0.200, 0.138, 0.59),
}
METHOD = {
    "solo_success": "share of the model's scored Solo runs of the pair that passed (binary with one run)",
    "scored": "eval.json present without an error; unscored attempts are left out",
    "d": "1 - mean over the models that scored the pair Solo of Solo_m(t)",
    "d_tilde": "min-max rescale of d over the pairs scored here, not the paper's 652",
    "buckets": "10 equal-width buckets over [0, 1], bucket = floor(10 d~), 1.0 in the last",
    "pairs": "an arm's curves use the pairs both it and that model's Solo scored",
    "auc": "trapezoid over the filled bucket midpoints, first to last; none with fewer than two",
    "pooling": "raw counts summed per bucket across models (Algorithm 1 lines 13-17)",
}


def wilson(passed, total, z=Z):
    """The Wilson score interval of k/n as two fractions; None when n is 0."""
    if total == 0:
        return None
    rate = passed / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - spread), min(1.0, centre + spread)


def bucket_of(d_tilde):
    """The bucket of a rescaled difficulty: floor(10 d~), with 1.0 in the
    last; the small allowance keeps 0.3 out of bucket 2 under float error."""
    return min(BUCKETS - 1, int(math.floor(d_tilde * BUCKETS + 1e-9)))


def midpoint(bucket):
    return round((bucket + 0.5) / BUCKETS, 2)


# Difficulty.

def solo_success(trials):
    """Solo_m(t) per pair and model from {model: {pair: [passed, ...]}}."""
    found = {}
    for model, pairs in trials.items():
        for pair, outcomes in pairs.items():
            if outcomes:
                found.setdefault(pair, {})[model] = sum(outcomes) / len(outcomes)
    return found


def difficulty(solo_trials):
    """Each pair's d, d~, bucket and the models it rests on, and the range
    d spans; d~ and the bucket are None when every pair has the same d,
    since the rescale is then undefined."""
    success = solo_success(solo_trials)
    pairs = {pair: {"d": 1 - sum(by_model.values()) / len(by_model), "models": dict(sorted(by_model.items()))}
             for pair, by_model in sorted(success.items())}
    values = [entry["d"] for entry in pairs.values()]
    low, high = (min(values), max(values)) if values else (None, None)
    for entry in pairs.values():
        entry["d_tilde"] = (entry["d"] - low) / (high - low) if high is not None and high > low else None
        entry["bucket"] = bucket_of(entry["d_tilde"]) if entry["d_tilde"] is not None else None
    return pairs, {"min": low, "max": high}


# Curves.

def empty_rows():
    return [{"bucket": b, "midpoint": midpoint(b), "n": 0, "n_solo": 0, "k_solo": 0, "n_arm": 0, "k_arm": 0}
            for b in range(BUCKETS)]


def curve_counts(pairs, solo_pairs, arm_pairs):
    """Per bucket the pairs and the Solo and arm trials and passes, over
    the pairs with a bucket that both sides scored."""
    rows = empty_rows()
    for pair, entry in pairs.items():
        if entry["bucket"] is None or not solo_pairs.get(pair) or not arm_pairs.get(pair):
            continue
        row = rows[entry["bucket"]]
        row["n"] += 1
        row["n_solo"] += len(solo_pairs[pair])
        row["k_solo"] += sum(solo_pairs[pair])
        row["n_arm"] += len(arm_pairs[pair])
        row["k_arm"] += sum(arm_pairs[pair])
    return rows


def with_rates(rows):
    """The rates and Wilson intervals of each bucket; None where empty."""
    for row in rows:
        for side in ("solo", "arm"):
            n, k = row[f"n_{side}"], row[f"k_{side}"]
            row[f"rate_{side}"] = k / n if n else None
            row[f"wilson_{side}"] = list(wilson(k, n)) if n else None
    return rows


def area(rows, side):
    """The trapezoid under one curve over its filled midpoints."""
    points = [(row["midpoint"], row[f"rate_{side}"]) for row in rows if row["n"]]
    if len(points) < 2:
        return None
    return sum((x2 - x1) * (y1 + y2) / 2 for (x1, y1), (x2, y2) in zip(points, points[1:]))


def summary(rows):
    """AUC of both curves, Delta AUC, retention and the limits used."""
    filled = [row["midpoint"] for row in rows if row["n"]]
    solo, arm = area(rows, "solo"), area(rows, "arm")
    return {"auc_solo": solo, "auc_arm": arm,
            "delta_auc": solo - arm if solo is not None and arm is not None else None,
            "retention": arm / solo if solo and arm is not None else None,
            "limits": [filled[0], filled[-1]] if filled else None, "filled_buckets": len(filled),
            "pairs": sum(row["n"] for row in rows)}


def curve(pairs, solo_pairs, arm_pairs):
    rows = with_rates(curve_counts(pairs, solo_pairs, arm_pairs))
    return {"buckets": rows, "summary": summary(rows)}


def pooled(curves):
    """Algorithm 1's pooling: the counts of several curves summed per bucket."""
    rows = empty_rows()
    for found in curves:
        for row, other in zip(rows, found["buckets"]):
            for name in ("n", "n_solo", "k_solo", "n_arm", "k_arm"):
                row[name] += other[name]
    rows = with_rates(rows)
    return {"buckets": rows, "summary": summary(rows)}


# Everything at once.

def notes(pairs, span, models):
    """What the output can and cannot be compared with."""
    distinct = sorted({round(entry["d"], 6) for entry in pairs.values()})
    found = [f"{len(models)} Solo model(s): {', '.join(models) or 'none'}; d takes {len(distinct)} "
             f"distinct value(s) over {len(pairs)} pair(s)."]
    if len(models) < 3:
        found.append("With one or two models d is coarse ({0, 1} or {0, 0.5, 1} with one run each), so at "
                     "most three buckets fill: the curves, areas and retention are degenerate and not "
                     "comparable with Table 5's five models.")
    if span["min"] is not None and span["min"] == span["max"]:
        found.append("Every pair has the same d, so d~ is undefined and no curve is drawn.")
    found.append("d~ is rescaled over the pairs scored here, not the paper's 652; the paper's per-pair "
                 "Solo outcomes are unpublished, so these pairs cannot be placed on its scale.")
    return found


def compute(solo_trials, arm_trials):
    """difficulty.json's content. solo_trials is {model: {pair: [passed]}};
    arm_trials is {(arm, model): {pair: [passed]}}, one entry per arm and
    model. Curves are drawn per arm and model against that model's Solo,
    and pooled per arm over its models."""
    pairs, span = difficulty(solo_trials)
    curves, by_arm = {}, {}
    for (arm, model), trials in arm_trials.items():
        if model not in solo_trials:
            continue
        found = curve(pairs, solo_trials[model], trials)
        curves[f"{arm}|{model}"] = {"arm": arm, "model": model, **found}
        by_arm.setdefault(arm, []).append(found)
    return {"format": "coordbench-difficulty/1", "method": METHOD,
            "notes": notes(pairs, span, sorted(solo_trials)), "models": sorted(solo_trials), "range": span,
            "pairs": pairs, "curves": curves, "pooled": {arm: pooled(found) for arm, found in by_arm.items()}}


# Reading runs, for the command line: result.json and eval.json only.

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scored_runs(logs, run_name):
    """{model: {pair: [passed]}} over a run's scored pairs, the model from
    result.json, the pair keyed repo/task/pair as report.py keys it."""
    found = {}
    for result_file in sorted((Path(logs) / run_name).glob("*/*/*/*/result.json")):
        evaluation = read_json(result_file.parent / "eval.json")
        if not isinstance(evaluation, dict) or evaluation.get("error"):
            continue
        model = (read_json(result_file) or {}).get("model") or "unknown"
        key = "/".join(result_file.parent.parts[-3:])
        found.setdefault(model, {}).setdefault(key, []).append(bool(evaluation.get("both_passed")))
    return found


def merge_trials(into, found):
    for model, pairs in found.items():
        for pair, outcomes in pairs.items():
            into.setdefault(model, {}).setdefault(pair, []).extend(outcomes)
    return into


def read_trials(logs, solo_runs, arm_runs):
    solo = {}
    for run_name in solo_runs:
        merge_trials(solo, scored_runs(logs, run_name))
    arms = {}
    for label, run_name in arm_runs:
        for model, pairs in scored_runs(logs, run_name).items():
            target = arms.setdefault((label, model), {})
            for pair, outcomes in pairs.items():
                target.setdefault(pair, []).extend(outcomes)
    return solo, arms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", type=Path, default=Path.home() / "cooperbench" / "logs")
    parser.add_argument("--solo", action="append", default=[], metavar="RUN_NAME",
                        help="a Solo run; one or more per model, the model read from result.json")
    parser.add_argument("--arm", action="append", default=[], metavar="LABEL=RUN_NAME",
                        help="a multi-agent run, reported under LABEL")
    parser.add_argument("--out", type=Path, default=Path("difficulty.json"))
    args = parser.parse_args(argv)
    arm_runs = [(entry.partition("=")[0], entry.partition("=")[2] or entry) for entry in args.arm]
    solo, arms = read_trials(args.logs, args.solo, arm_runs)
    found = compute(solo, arms)
    args.out.write_text(json.dumps(found, indent=2) + "\n")
    for note in found["notes"]:
        print(note)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
