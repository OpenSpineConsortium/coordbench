"""The human steps of CooperBench's failure analysis: causes and the judge's validation.

    python3 coordbench/annotate.py causes <path>... --out DIR [--sample 50] [--seed N] [--draft ...]
    python3 coordbench/annotate.py tally DIR/labels.json [--apply]
    python3 coordbench/annotate.py validate <path>... --out DIR [--sample 50] [--seed N]
    python3 coordbench/annotate.py agreement DIR/sheet.json [OTHER/sheet.json]

Causes (Sec 6.2, Table 2) are assigned by hand, as the paper assigns
them: "we manually reviewed 50 failed Coop traces ... causes require
deeper interpretation of the coordination dynamics and are therefore
manually assigned." `causes` draws failed multi-agent pairs uniformly
without replacement (the paper's sampling is unstated), seeded so the
draw can be repeated, and writes one review packet per pair (the symptom
labels, the conversation, the patches, the merged artifacts and the test
outputs, the material Sec 6.2 names) and labels.json, which takes one of
Expectation, Commitment or Communication per pair, with Table 2's
definitions word for word, and a note. `tally` counts the labels with
Wilson intervals and, with --apply, writes each into the pair's
judge.json as its "cause". With --draft, a judge drafts a cause per pair
into drafts.json, which the packets and labels.json never show, so the
reviewer is not led; a draft is never counted, and the tally reports
only how often the drafts agree with the human labels.

The judge's validation follows App G: "we randomly sampled 50
trajectories and had human experts independently label them using the
same rubric. Human labels matched the LLM annotations on 48 of 50 cases".
`validate` draws judged pairs at random, seeded, and writes blind packets
(no label in them), the rubric the judge used and sheet.json for the
human's level and symptoms. `agreement` reports the agreement on the
level and on the primary symptom with Wilson intervals, the paper's
"matched" basis being unstated, and Cohen's kappa when a second human's
sheet is given.

The draw is over the pairs found, sorted by path, with Python's
random.Random(seed).sample, so a seed names one draw for one set of runs.
This module uses judge.py's readers and needs nothing else; it calls a
model only for --draft.
"""

import argparse
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import judge  # noqa: E402

DEFAULT_SAMPLE = 50
DEFAULT_SEED = 0
LABELS_FORMAT = "coordbench-cause-labels/1"
SHEET_FORMAT = "coordbench-symptom-sheet/1"
DRAFTS_FORMAT = "coordbench-cause-drafts/1"
Z_95 = 1.959963984540054

# Table 2's definitions, word for word.
TABLE_2 = {
    "Expectation": "Cases where one agent has clearly communicated what they are doing, but the other agent "
                   "still treats the situation as if that work is not being done. This reflects a failure to "
                   "model the state of the other agent's code changes and what that means for the system as a "
                   "whole.",
    "Commitment": "Cases where an agent is not doing the things they promised to do. This includes failures to "
                  "establish or maintain verifiable integration contracts, where agents make commitments but do "
                  "not follow through on them.",
    "Communication": "Breakdowns in using language to coordinate. This includes failures in information sharing "
                     "and decision loops between agents, where agents do not effectively communicate their "
                     "intentions, questions, or status updates.",
}
# What the counts are set beside: Table 2 (of 50 traces) and the
# website's causes/index.json (23 entries, which do not reproduce it).
CAUSE_REFERENCES = {"paper Table 2 (%)": {"Expectation": 42, "Commitment": 32, "Communication": 26},
                    "website causes/index.json (count of 23)": {"Expectation": 8, "Commitment": 9,
                                                                "Communication": 6}}
# The blog's Expectation card (curse-of-coordination.mdx) says expectation
# failure "happens in 63% of failures", which Table 2's 42% contradicts;
# both are printed so neither is mistaken for the other.
CAUSE_CONFLICT = ("The blog (curse-of-coordination.mdx) says expectation failure 'happens in 63% of failures', "
                  "against Table 2's 42%; the paper does not reconcile them.")
VALIDATION_REFERENCE = "App G: 48 of 50 matched (96%), Wilson 95% [86%, 99%]"
CAUSE_INSTRUCTIONS = ("Read each pair's packet, then set its cause to one of Expectation, Commitment or "
                      "Communication, by Table 2's definitions, and say why in its note. Leave cause null for a "
                      "pair you cannot decide; it is counted as unlabelled.")
SHEET_INSTRUCTIONS = ("Read each pair's packet and the rubric, then set level to coordination or implementation, "
                      "primary to one symptom name (null for implementation) and secondary to any other names. "
                      "The rubric's decision rule holds: coordination needs explicit conversation evidence.")


def warn(text):
    print(f"annotate: {text}", file=sys.stderr)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Statistics.

def wilson(successes, total, z=Z_95):
    """The Wilson score interval for successes out of total, or None for
    no trials."""
    if total <= 0:
        return None
    share = successes / total
    denominator = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denominator
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def cohen_kappa(first, second):
    """Cohen's kappa of two raters' labels over the same items; None when
    there is nothing to compare, 1.0 when chance agreement is already
    certain and they agree."""
    if not first or len(first) != len(second):
        return None
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / total
    categories = set(first) | set(second)
    expected = sum((first.count(c) / total) * (second.count(c) / total) for c in categories)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else None
    return (observed - expected) / (1 - expected)


def share_text(successes, total):
    if not total:
        return "-"
    low, high = wilson(successes, total)
    return f"{successes}/{total} ({100 * successes / total:.1f}%, Wilson 95% [{100 * low:.1f}%, {100 * high:.1f}%])"


def rounded(interval):
    return None if interval is None else [round(interval[0], 4), round(interval[1], 4)]


# Drawing pairs.

def failed_pairs(paths):
    """(failed multi-agent pairs, harness errors): the population of the
    cause review, with the harness errors counted apart."""
    failed, errors = [], []
    for pair in judge.loaded_pairs(paths):
        bases, reason = judge.symptom_scope(pair)
        if bases:
            failed.append(pair)
        elif reason and reason.startswith("harness error"):
            errors.append(pair)
    return sorted(failed, key=lambda p: str(p.dir)), errors


def judged_pairs(paths):
    """Pairs whose judge.json holds symptoms a judge made (not App G's
    rule over a silent pair), the population of the validation."""
    found = []
    for pair in judge.loaded_pairs(paths):
        symptoms = (judge.read_json(pair.dir / judge.JUDGE_FILE) or {}).get("symptoms")
        if isinstance(symptoms, dict) and symptoms.get("decided_by") == "judge":
            found.append(pair)
    return sorted(found, key=lambda p: str(p.dir))


def draw(population, size, seed):
    return random.Random(seed).sample(population, min(size, len(population)))


def safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")


def packet_name(number, pair):
    return f"packets/{number:02d}-{safe(pair.key)}.md"


# Review packets.

def outcome_part(pair):
    if pair.evaluation is None:
        return ["## Outcome", "", "Not scored (no eval.json).", ""]
    return ["## Outcome", "", judge.fence(judge.outcome_text(pair.evaluation)), ""]


def label_lines(pair):
    """The judge's labels for the cause reviewer, who reads the symptom
    labels as the paper's reviewers did."""
    data = judge.read_json(pair.dir / judge.JUDGE_FILE) or {}
    lines = ["## Labels from judge.json", ""]
    symptoms, errors = data.get("symptoms"), data.get("commerrors")
    if isinstance(symptoms, dict):
        lines.append(f"Symptoms ({symptoms.get('prompt_variant')}, {symptoms.get('decided_by')}): level "
                     f"{symptoms.get('level')}, primary {symptoms.get('primary')}, secondary "
                     f"{', '.join(symptoms.get('secondary') or []) or 'none'}, family {symptoms.get('family')}.")
        lines += [f"- {e.get('source')}: \"{e.get('quote')}\" (verified {e.get('verified')})"
                  for e in symptoms.get("evidence") or []]
    else:
        lines.append("Symptoms: not labelled.")
    if isinstance(errors, dict):
        issue = errors.get("issue") or {}
        lines.append(f"Communication error ({errors.get('prompt_variant')}): "
                     f"{issue.get('code') or 'none'} {issue.get('group') or ''}".rstrip() + ".")
    calls = data.get("calls") or {}
    judges = sorted({f"{c.get('backend')} {c.get('model')}" for c in calls.values() if isinstance(c, dict)})
    lines += ["", f"Judged by: {', '.join(judges) or 'nobody'}.", ""]
    return lines


def conversation_part(pair):
    conversation = judge.conversation_of(pair)
    if not conversation.recorded:
        return ["## Conversation", "", conversation.reason + ".", ""]
    rendered, _ = judge.render_conversation(pair, conversation, limit=None)
    return ["## Conversation", "", judge.fence(rendered), ""]


def patch_part(pair):
    lines = ["## Patches", ""]
    for agent, raw in judge.agent_patches(pair).items():
        name = judge.patch_file(pair, agent)
        lines += [f"### {agent}: {name}, test files left out as CooperBench 63b9d44 does", "",
                  judge.fence(judge.filter_test_files(raw) or "(empty)", "diff"), ""]
    return lines


def merged_part(pair):
    lines = ["## Merged artifacts", ""]
    decided, _ = judge.deciding_artifact(pair)
    lines.append(f"The 63b9d44 verdict was read from: {decided or 'not recorded'}.")
    for name in ("merge_naive.diff", "merge_union.diff", "merge_llm.diff"):
        text = judge.read_text(pair.dir / name)
        if text is not None:
            lines += ["", f"### {name}", "", judge.fence(text or "(empty)", "diff")]
    return lines + [""]


def test_part(pair):
    lines = ["## Test outputs", ""]
    for feature in judge.feature_results(pair.evaluation or {}):
        lines += [f"### Feature {feature.get('feature_id')}: passed {feature.get('passed')}", "",
                  judge.fence(str(feature.get("test_output") or "(no output)")), ""]
    return lines


def packet_text(pair, with_labels):
    result = pair.result
    head = [f"# {result.get('repo')} task {result.get('task_id')}, features "
            f"{', '.join(map(str, result.get('features') or []))}", "",
            f"Arm {result.get('arm')}, setting {result.get('setting')}, model {result.get('model')}, run "
            f"{result.get('run_name')}. Pair directory: {pair.dir}.", ""]
    parts = head + outcome_part(pair) + (label_lines(pair) if with_labels else [])
    parts += conversation_part(pair) + patch_part(pair) + merged_part(pair) + test_part(pair)
    return "\n".join(parts).rstrip() + "\n"


def write_packets(out, pairs, with_labels):
    (out / "packets").mkdir(parents=True, exist_ok=True)
    names = []
    for number, pair in enumerate(pairs, 1):
        name = packet_name(number, pair)
        (out / name).write_text(packet_text(pair, with_labels), encoding="utf-8")
        names.append(name)
    return names


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


# causes.

def labels_document(args, population, errors, drawn, names):
    return {"format": LABELS_FORMAT, "created_at": now(), "seed": args.seed, "sample": args.sample,
            "population": len(population), "harness_errors_excluded": len(errors), "drawn": len(drawn),
            "sampling": "uniform without replacement over the failed multi-agent pairs found, sorted by path, "
                        "with Python's random.Random(seed).sample",
            "causes": list(judge.CAUSES), "definitions": TABLE_2, "instructions": CAUSE_INSTRUCTIONS,
            "pairs": [{"key": p.key, "pair_dir": str(p.dir.resolve()), "packet": n, "cause": None, "note": ""}
                      for p, n in zip(drawn, names)]}


def draft_one(pair, backend):
    """A judge's cause draft for one pair, from the symptom input and the
    pair's symptom labels."""
    failed, _ = judge.symptom_scope(pair)
    conversation = judge.conversation_of(pair)
    text, _, _ = judge.symptom_input(pair, conversation, failed)
    text += "\n" + "\n".join(label_lines(pair))
    variant = "causes-v1"
    file_name = judge.CAUSE_VARIANTS[variant]["file"]
    system = judge.prompt_text(file_name)
    answer = judge.ask(backend, system, text)
    call = judge.call_record(backend, variant, file_name, system, text, answer, False)
    return {"key": pair.key, "pair_dir": str(pair.dir.resolve()), **judge.cause_draft(answer), "judge": call}


def write_drafts(out, drawn, backend):
    drafts = []
    for pair in drawn:
        try:
            drafts.append(draft_one(pair, backend))
        except judge.JudgeError as problem:
            drafts.append({"key": pair.key, "pair_dir": str(pair.dir.resolve()), "draft_cause": None,
                           "error": str(problem)})
    write_json(out / "drafts.json", {"format": DRAFTS_FORMAT, "created_at": now(),
                                     "note": "judge drafts, never counted in the tally", "pairs": drafts})


def command_causes(args, backend=None):
    population, errors = failed_pairs(args.paths)
    if not population:
        warn("no failed multi-agent pair found")
        return 2
    out = Path(args.out)
    drawn = draw(population, args.sample, args.seed)
    names = write_packets(out, drawn, with_labels=True)
    write_json(out / "labels.json", labels_document(args, population, errors, drawn, names))
    if args.draft:
        write_drafts(out, drawn, backend or judge.make_backend(args))
    print(f"drew {len(drawn)} of {len(population)} failed multi-agent pairs (seed {args.seed}; "
          f"{len(errors)} harness errors left out); fill {out / 'labels.json'}")
    return 0


# tally.

def cause_of(entry):
    text = str(entry.get("cause") or "").strip().capitalize()
    return text if text in judge.CAUSES else None


def read_labels(path):
    data = judge.read_json(path)
    if not isinstance(data, dict) or data.get("format") != LABELS_FORMAT:
        raise SystemExit(f"annotate: {path} is not a labels file written by annotate.py causes")
    return data


def cause_counts(entries):
    labelled = [e for e in entries if cause_of(e)]
    invalid = [e["key"] for e in entries if e.get("cause") and not cause_of(e)]
    counts = {cause: sum(1 for e in labelled if cause_of(e) == cause) for cause in judge.CAUSES}
    return labelled, invalid, counts


def draft_agreement(labels_path, labelled):
    drafts = judge.read_json(Path(labels_path).parent / "drafts.json")
    if not isinstance(drafts, dict):
        return None
    by_dir = {d.get("pair_dir"): d.get("draft_cause") for d in drafts.get("pairs") or [] if isinstance(d, dict)}
    compared = [(cause_of(e), by_dir[e["pair_dir"]]) for e in labelled if by_dir.get(e["pair_dir"])]
    agreed = sum(1 for human, draft in compared if human == draft)
    return {"compared": len(compared), "agreed": agreed, "wilson_95": rounded(wilson(agreed, len(compared)))}


def apply_causes(labels_path, labelled):
    for entry in labelled:
        try:
            pair = judge.load_pair(entry["pair_dir"])
        except FileNotFoundError as error:
            warn(str(error))
            continue
        data = judge.load_judge(pair)
        data["cause"] = {"label": cause_of(entry), "note": entry.get("note") or "", "source": "human",
                         "labels_file": str(Path(labels_path).resolve()), "applied_at": now()}
        judge.save_judge(pair, data)


def tally_report(labelled, invalid, counts, entries, drafts):
    total = len(labelled)
    lines = ["# Failure causes (Table 2), labelled by hand", "",
             f"{total} labelled of {len(entries)} drawn; {len(entries) - total - len(invalid)} unlabelled; "
             f"{len(invalid)} with a label that is none of the three.", "",
             "| Cause | Count | Share (Wilson 95%) | Paper Table 2 | Website index |",
             "| --- | --- | --- | --- | --- |"]
    for cause in judge.CAUSES:
        paper = CAUSE_REFERENCES["paper Table 2 (%)"][cause]
        site = CAUSE_REFERENCES["website causes/index.json (count of 23)"][cause]
        lines.append(f"| {cause} | {counts[cause]} | {share_text(counts[cause], total)} | {paper}% | {site} of 23 |")
    lines += ["", CAUSE_CONFLICT]
    if drafts:
        lines += ["", f"Judge drafts (never counted) agree with the human label on "
                      f"{share_text(drafts['agreed'], drafts['compared'])}."]
    return "\n".join(lines)


def command_tally(args):
    data = read_labels(args.labels)
    entries = [e for e in data.get("pairs") or [] if isinstance(e, dict)]
    labelled, invalid, counts = cause_counts(entries)
    drafts = draft_agreement(args.labels, labelled)
    summary = {"format": "coordbench-cause-tally/1", "created_at": now(),
               "labels_file": str(Path(args.labels).resolve()),
               "drawn": len(entries), "labelled": len(labelled), "invalid": invalid,
               "counts": counts, "wilson_95": {c: rounded(wilson(counts[c], len(labelled))) for c in judge.CAUSES},
               "references": CAUSE_REFERENCES, "reference_conflict": CAUSE_CONFLICT, "draft_agreement": drafts}
    write_json(Path(args.labels).parent / "tally.json", summary)
    print(tally_report(labelled, invalid, counts, entries, drafts))
    if args.apply:
        apply_causes(args.labels, labelled)
    return 0


# validate.

def rubric_files(out, drawn):
    """The rubric of every symptom variant the drawn pairs were judged
    with, written beside the sheet as the judge read it."""
    variants = sorted({(judge.read_json(p.dir / judge.JUDGE_FILE) or {})["symptoms"].get("prompt_variant")
                       or "symptoms-r1" for p in drawn})
    names = []
    for variant in variants:
        file_name = judge.SYMPTOM_VARIANTS.get(variant, judge.SYMPTOM_VARIANTS["symptoms-r1"])["file"]
        name = f"rubric-{variant}.txt"
        (out / name).write_text(judge.prompt_text(file_name), encoding="utf-8")
        names.append(name)
    return names, variants


def sheet_document(args, population, drawn, names, rubrics, variants):
    symptom_names = sorted({n for v in variants for n in judge.SYMPTOM_VARIANTS.get(v, {}).get("names", ())})
    return {"format": SHEET_FORMAT, "created_at": now(), "seed": args.seed, "sample": args.sample,
            "population": len(population), "drawn": len(drawn),
            "sampling": "uniform without replacement over the pairs a judge labelled, sorted by path, with "
                        "Python's random.Random(seed).sample",
            "levels": list(judge.LEVELS), "symptoms": symptom_names, "rubric_files": rubrics,
            "instructions": SHEET_INSTRUCTIONS,
            "pairs": [{"key": p.key, "pair_dir": str(p.dir.resolve()), "packet": n, "level": None, "primary": None,
                       "secondary": [], "note": ""} for p, n in zip(drawn, names)]}


def command_validate(args):
    population = judged_pairs(args.paths)
    if not population:
        warn("no pair with judged symptoms found (run judge.py symptoms first)")
        return 2
    out = Path(args.out)
    drawn = draw(population, args.sample, args.seed)
    names = write_packets(out, drawn, with_labels=False)
    rubrics, variants = rubric_files(out, drawn)
    write_json(out / "sheet.json", sheet_document(args, population, drawn, names, rubrics, variants))
    print(f"drew {len(drawn)} of {len(population)} judged pairs (seed {args.seed}); the packets are blind; "
          f"fill {out / 'sheet.json'}")
    return 0


# agreement.

ALL_SYMPTOMS = judge.TABLE_1 + (judge.INFORMATION_ASYMMETRY,)


def human_labels(entry):
    """(level, primary) as a human gave them, normalised; None for a pair
    not yet labelled."""
    level = str(entry.get("level") or "").strip().lower()
    if level not in judge.LEVELS:
        return None
    primary = judge.symptom_name(entry.get("primary"), ALL_SYMPTOMS) if level == "coordination" else None
    return level, primary


def judge_labels(pair_dir):
    symptoms = (judge.read_json(Path(pair_dir) / judge.JUDGE_FILE) or {}).get("symptoms")
    if not isinstance(symptoms, dict):
        return None
    return symptoms.get("level"), symptoms.get("primary")


def read_sheet(path):
    data = judge.read_json(path)
    if not isinstance(data, dict) or data.get("format") != SHEET_FORMAT:
        raise SystemExit(f"annotate: {path} is not a sheet written by annotate.py validate")
    return {e["pair_dir"]: e for e in data.get("pairs") or [] if isinstance(e, dict) and "pair_dir" in e}


def against_judge(sheet):
    """Per labelled pair: the human's labels and the judge's."""
    rows = []
    for pair_dir, entry in sheet.items():
        human, machine = human_labels(entry), judge_labels(pair_dir)
        if human is not None and machine is not None:
            rows.append((entry.get("key") or pair_dir, human, machine))
    return rows


def agreement_block(rows):
    level = sum(1 for _, h, m in rows if h[0] == m[0])
    primary = sum(1 for _, h, m in rows if h[1] == m[1])
    both = sum(1 for _, h, m in rows if h == m)
    total = len(rows)
    return {"labelled": total,
            "level": {"agreed": level, "wilson_95": rounded(wilson(level, total))},
            "primary": {"agreed": primary, "wilson_95": rounded(wilson(primary, total))},
            "both": {"agreed": both, "wilson_95": rounded(wilson(both, total))},
            "disagreements": [{"key": k, "human": list(h), "judge": list(m)} for k, h, m in rows if h != m]}


def kappa_block(first, second):
    keys = [k for k in first if human_labels(first[k]) and k in second and human_labels(second[k])]
    one = [human_labels(first[k]) for k in keys]
    two = [human_labels(second[k]) for k in keys]
    level = cohen_kappa([a[0] for a in one], [b[0] for b in two])
    primary = cohen_kappa([str(a[1]) for a in one], [str(b[1]) for b in two])
    return {"labelled_by_both": len(keys), "kappa_level": level, "kappa_primary": primary}


def agreement_report(summary):
    block = summary["human_vs_judge"]
    total = block["labelled"]
    lines = ["# The symptom judge against human labels (App G)", "",
             f"Level: {share_text(block['level']['agreed'], total)}",
             f"Primary symptom: {share_text(block['primary']['agreed'], total)}",
             f"Both: {share_text(block['both']['agreed'], total)}", "", f"Reference: {VALIDATION_REFERENCE}."]
    second, kappa = summary.get("second_human"), summary.get("kappa")
    if second:
        total = second["labelled"]
        lines += ["", f"Second human against the judge: level {share_text(second['level']['agreed'], total)}, "
                      f"primary {share_text(second['primary']['agreed'], total)}.",
                  f"Cohen's kappa between the two humans over {kappa['labelled_by_both']} pairs: level "
                  f"{kappa_text(kappa['kappa_level'])}, primary {kappa_text(kappa['kappa_primary'])}."]
    return "\n".join(lines)


def kappa_text(value):
    return "-" if value is None else f"{value:.3f}"


def command_agreement(args):
    first = read_sheet(args.sheet)
    summary = {"format": "coordbench-symptom-agreement/1", "created_at": now(),
               "sheet": str(Path(args.sheet).resolve()), "basis": "exact match of the level, of the primary "
               "symptom (none for implementation), and of both; the paper's 'matched' basis is unstated",
               "human_vs_judge": agreement_block(against_judge(first)), "reference": VALIDATION_REFERENCE}
    if args.second:
        second = read_sheet(args.second)
        summary["second_sheet"] = str(Path(args.second).resolve())
        summary["second_human"] = agreement_block(against_judge(second))
        summary["kappa"] = kappa_block(first, second)
    write_json(Path(args.sheet).parent / "agreement.json", summary)
    print(agreement_report(summary))
    return 0


def parser_of():
    parser = argparse.ArgumentParser(description="The human steps of the failure analysis: cause labels and the "
                                                 "symptom judge's validation.")
    commands = parser.add_subparsers(dest="command", required=True)
    causes = commands.add_parser("causes", help="draw failed pairs and write review packets and labels.json")
    causes.add_argument("paths", nargs="+")
    causes.add_argument("--out", required=True, help="the directory for the packets and labels.json")
    causes.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    causes.add_argument("--seed", type=int, default=DEFAULT_SEED)
    causes.add_argument("--draft", action="store_true", help="also have a judge draft each cause (model calls; "
                                                             "kept in drafts.json and never counted)")
    judge.add_backend_options(causes, judging=False)
    tally = commands.add_parser("tally", help="count the cause labels with Wilson intervals")
    tally.add_argument("labels")
    tally.add_argument("--apply", action="store_true", help="write each label into its pair's judge.json")
    validate = commands.add_parser("validate", help="draw judged pairs and write a blind sheet")
    validate.add_argument("paths", nargs="+")
    validate.add_argument("--out", required=True)
    validate.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    validate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    agreement = commands.add_parser("agreement", help="score a filled sheet against the judge")
    agreement.add_argument("sheet")
    agreement.add_argument("second", nargs="?", help="a second human's sheet, for Cohen's kappa")
    return parser


def main(argv=None):
    args = parser_of().parse_args(argv)
    commands = {"causes": command_causes, "tally": command_tally, "validate": command_validate,
                "agreement": command_agreement}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
