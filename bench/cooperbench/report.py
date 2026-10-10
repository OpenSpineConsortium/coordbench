"""Set the arms of the experiment side by side, from CooperBench's logs.

Each arm is one or more runs (bench/cooperbench/run_arms.sh names
CooperBench's flash-<arm>-<tag>; coordbench's run.py writes the same
layout), and each pair a run ran leaves result.json and eval.json under
logs/<run>/<setting>/<repo>/<task>/<pair>/, with config.json and
summary.json under logs/<run>/. The report reads CooperBench's runs and
coordbench's alike and measures what CooperBench measures, as close to
its way as the logs allow, each figure beside the paper's or the code's
own number. Where coordbench departs from CooperBench it says so in the
output and here:

- Rates are per arm and model (result.json "model"), with a pooled row
  that sums the counts across models, as App C pools. The first rate is
  the paper's k/n: eval.json both_passed over every attempted pair,
  where a pair with no eval.json or an eval error counts as a failure
  and the agents' status is ignored, as CooperBench's runner scores an
  errored agent's patch. Beside it are CooperBench's own three rates
  (summary.json's passed / (passed + failed), the console's and the
  ablation page's passed / evaluated, the website's passed / attempted)
  and coordbench's rate on the pairs its agents finished, where a pair an
  agent left in Error is set apart rather than failed: a harness error is
  not a coordination outcome. Both have a 95% Wilson interval, and the
  paper's rate a cluster bootstrap over task sets beside it (L484 says
  the paper's intervals were "computed over task sets").
- The coordination gap (Solo minus the arm, absolute and relative, on
  the pairs both scored, Fig 4), the git ablation of the blog post, the
  pass route (naive, identical or lead-alone, BENCHMARK_RESULTS.md's N
  and L), the naive-merge conflict rate (Fig 5b) with the share of clean
  merges whose tests still fail (coordbench's proxy for the paper's
  semantic coordination gap, Sec 5), and the merge cascade's cumulative
  Naive, +Union and +LLM success (Table 6) when eval.json carries
  cascade.py's block, with its two verdicts never mixed.
- Communication: turns (one per sent message, a broadcast one turn;
  speaker changes as a variant, Fig 6a), the overhead of sending in the
  agents' tool calls (Fig 5c; neither denominator here is the paper's
  OpenHands event, so a calls basis and a calls-plus-results basis are
  both given),
  concrete line and file references (Sec 5), and, once judge.json holds
  speech acts, their split, the Plan:Question ratio and first-turn
  planning against conflicts. Message text comes from
  conversation_store.json, events.jsonl or CooperBench's
  conversation.json; a run from before the text was logged says "not
  recorded".
- Failure modes from judge.json (coordbench/judge.py): the three
  communication-error groups over both denominators, the symptoms with
  the implementation share and the failure families, the free
  cross-checks and the emergent tags. No model is called here.
- Difficulty d(t), the stratified curves, AUC and retention (App C),
  through difficulty.py, with its degeneracy stated.
- The dataset (dataset_stats.py): how many of the 652 pairs each run
  covers, the gold-conflict share (76.5% on 63b9d44's dataset against the
  paper's 77.3%), every headline rate again over the pairs unamended since
  the paper's dataset and by spec-audit class, and the pairs with a
  feature that does not grade here.

Significance: exact McNemar for paired arm comparisons (success and
conflict), Fisher's exact test for first-turn planning, Mann-Whitney U
for reference counts; the paper names no test for any of its claims.

    python3 bench/cooperbench/report.py [--logs ~/cooperbench/logs] [--tag cc]
        [--arm solo --arm team ...] [--run LABEL=RUN_NAME ...]
        [--cooperbench-dir ~/cooperbench] [--published coop/index.json]

--arm NAME reads the run <prefix>-NAME-<tag>; --run LABEL=RUN_NAME reads
any run under the label given, and a label may be given several times
(one run per model, or repeated runs), its pairs then pooled under it.
"""

import argparse
import json
import math
import os
import random
import re
import statistics
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import dataset_stats
import difficulty

ARMS = ("solo", "coop", "coop-git", "team")
# The authors' flash results with codex and gpt-5.5, one seed, unreviewed
# (CooperBench's docs/team_harness_ablation_report.html at 63b9d44).
AUTHORS = {"solo": "24/50", "coop": "13/50", "coop-git": "28/50", "team": "31/50"}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
NOT_RECORDED = "not recorded"
BOOTSTRAP_ITERATIONS = 2000
EXACT_MANN_WHITNEY_LIMIT = 40

SEND_TOOLS = ("send_message", "broadcast", "request", "respond")
RECEIVE_TOOLS = ("receive_messages", "wait_for_message")
SEND_ALIASES = re.compile(r"(?<![\w-])coop-(?:send|broadcast|request|respond)(?![\w-])")
RECEIVE_ALIASES = re.compile(r"(?<![\w-])coop-recv(?![\w-])")
LINE_REFERENCE = re.compile(r"\blines?\s+\d+(?:\s*(?:-|to)\s*\d+)?", re.IGNORECASE)
SHORT_LINE_REFERENCE = re.compile(r"\bL\d+(?:\s*-\s*L?\d+)?\b")
PATH_LINE_REFERENCE = re.compile(r"[\w./-]+\.\w+:\d+")
FILE_PATH = re.compile(r"(?<![\w./-])[\w./-]*[\w-]\.(?:py|pyi|ts|tsx|js|go|rs|toml|json|yaml|yml|md|cfg|ini|txt)"
                       r"(?![\w-])")
SPEECH_ACTS = ("Plan", "Question", "Answer", "Update", "Ack")
ERROR_GROUPS = ("Repetition", "Unresponsiveness", "Hallucination")
ERROR_CODES = {"C1a": "Unresponsiveness", "C1b": "Unresponsiveness", "C2": "Unresponsiveness",
               "C3a": "Hallucination", "C3b": "Hallucination", "C5": "Hallucination",
               "C4a": "Repetition", "C4b": "Repetition"}
# Table 1's symptoms with their share, and the website's failure-family
# tree (coordination_failure_taxonomy_tree.png), which has no Broken
# commitment and adds Information asymmetry.
TABLE_1 = {"Work overlap": 33.2, "Divergent architecture": 29.7, "Repetition": 14.7, "Unresponsiveness": 8.7,
           "Unverifiable claims": 4.3, "Broken commitment": 3.7, "Dependency access": 1.7,
           "Placeholder misuse": 1.5, "Parameter flow": 1.3, "Timing dependency": 1.1}
FAMILIES = {"Information asymmetry": "Information", "Unverifiable claims": "Information",
            "Dependency access": "Integration", "Parameter flow": "Integration", "Placeholder misuse": "Integration",
            "Divergent architecture": "Structural", "Work overlap": "Structural", "Timing dependency": "Structural",
            "Repetition": "Communication", "Unresponsiveness": "Communication"}

# The paper's and the website's numbers each table is set beside.
REFERENCES = {
    "rates": "Paper, Fig 4 Left and Table 5 (k/652): Solo claude 47.1% (307), gpt-5 48.3% (315), minimax 36.2% "
             "(236), qwen coder 21.6% (141), qwen 6.3% (41); Coop claude 25.9% (169 on the website and in "
             "Table 6, 168 in Table 5), gpt-5 27.9% (182 / 183), minimax 14.0% (91), qwen coder 13.3% (87), "
             "qwen 4.6% (30).",
    "gap": "Paper, Fig 4 Left (derived): claude 21.2 points (45% relative), gpt-5 20.4 (42%), minimax 22.2 "
           "(61%), qwen coder 8.3 (38%), qwen 1.7 (27%); pooled 46% relative. The abstract's '30% lower' "
           "follows from none of the paper's numbers.",
    "git": "Blog (the-curious-case-of-miscoordination.mdx): Gemini 3 Flash, OpenHands SDK: git 27.76%, no git "
           "26.23%, Solo 48.60%, gaps -20.84 / -22.37, +1.53; Gemini 3 Flash, Mini-SWE: 15.18 / 12.27, Solo "
           "25.20, +2.91; Gemini 3 Pro, Mini-SWE: 21.78 / 20.40, Solo 36.80, +1.38; 'not statistically "
           "significant'.",
    "conflict": "Website coop/index.json hasConflict over the rows with results (with comm, exact): claude "
                "122/607 = 20.1%, gpt-5 156/571 = 27.3%, minimax 54/340 = 15.9%, qwen 257/607 = 42.3%, qwen "
                "coder 376/536 = 70.1%. No comm, read off Fig 5b only: claude ~42%, gpt-5 ~66.5%, minimax "
                "~28%, qwen ~52.5%, qwen coder ~71%.",
    "cascade": "Paper, Table 6 (Naive / +Union / +LLM, % of 652): Claude 4.5 no comm 12.27 / 26.84 / 27.30, "
               "with comm 16.72 / 24.85 / 25.92 (delta -1.4); GPT-5 13.88 / 26.69 / 27.91 and 20.42 / 26.64 / "
               "27.90; average delta -0.5.",
    "turns": "Paper, Fig 6a (average turns): claude 14.5, gpt-5 15.5, minimax 10.8, qwen 3.2, qwen coder 1.1. "
             "Fig 5c (comm events, % of all events): claude 20.0%, gpt-5 16.3%, minimax 13.6%, qwen 6.2%, "
             "qwen coder 3.3%.",
    "references": "Paper, Sec 5: successful trajectories 32.6 vs 22.5 line-number mentions and 13.1 vs 10.0 "
                  "file-path mentions ('successful' and the extraction unstated).",
    "plan": "Paper, Sec 5: Plan:Question 2.04 without conflict vs 1.31 with; a Plan in the first turn 29.4% "
            "conflict rate vs 51.5% without.",
    "errors": "Paper, Fig 6 (percent of conversations): claude Repetition 37.1%, Unresponsiveness 9.9%, "
              "Hallucination 6.9% (any 53.9%); gpt-5 5.1 / 5.8 / 2.1 (13.0%); minimax 17.8 / 21.3 / 5.4 "
              "(44.5%); qwen 6.8 / 1.9 / 0.0 (8.7%); qwen coder 0%.",
    "difficulty": "Paper, Table 5 (AUC Solo / Coop, Delta, retention): gpt-5 0.506 / 0.325, 0.181, 0.64; claude "
                  "0.469 / 0.283, 0.186, 0.60; minimax 0.374 / 0.171, 0.203, 0.46; qwen coder 0.236 / 0.148, "
                  "0.088, 0.63; qwen 0.106 / 0.072, 0.034, 0.68; pooled 0.338 / 0.200, 0.138, 0.59.",
}


# Reading.

def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def json_lines(path):
    """The JSON objects of a .jsonl file, skipping lines that do not parse."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def pair_agents(result):
    """The agents of one result.json, keyed by id. CooperBench's coop and
    team runners and coordbench's run.py write them under "agents";
    CooperBench's solo runner (runner/solo.py) writes the one agent under
    the singular "agent", and reading only the plural would count that
    baseline's tokens as 0 and never see its agent in Error."""
    agents = result.get("agents")
    if isinstance(agents, dict) and agents:
        return agents
    agent = result.get("agent")
    return {"solo": agent} if isinstance(agent, dict) else {}


def pair_tokens(agents):
    """The pair's token total, or None when no count is known. A driver
    that cannot measure usage writes null for the counts (coordbench's
    run.py), so a null is unknown and a measured zero is a zero."""
    known = [a.get(k) for a in agents.values() for k in TOKEN_FIELDS if a.get(k) is not None]
    return sum(known) if known else None


@dataclass
class Pair:
    """One pair of one run, as its result.json and eval.json say."""
    key: str
    label: str
    run: str
    dir: Path
    result: dict
    evaluation: dict = None
    cache: dict = field(default_factory=dict)

    @property
    def agents(self):
        return pair_agents(self.result)

    @property
    def model(self):
        return self.result.get("model")

    @property
    def setting(self):
        return self.result.get("setting") or self.dir.parts[-4]

    @property
    def arm(self):
        return self.result.get("arm")

    @property
    def task_set(self):
        return "/".join(self.key.split("/")[:2])

    @property
    def agent_error(self):
        return any(isinstance(a, dict) and a.get("status") == "Error" for a in self.agents.values())

    @property
    def eval_state(self):
        if not isinstance(self.evaluation, dict):
            return "missing"
        return "error" if self.evaluation.get("error") else "scored"

    @property
    def scored(self):
        return self.eval_state == "scored"

    @property
    def passed(self):
        """The paper's outcome: eval.json both_passed, False when unscored."""
        return self.scored and bool(self.evaluation.get("both_passed"))

    @property
    def finished_outcome(self):
        """coordbench's outcome on finished pairs: an agent in Error or a
        pair without a usable evaluation is an error, not a failure."""
        if self.agent_error or not self.scored:
            return "error"
        return "pass" if self.passed else "fail"

    @property
    def merge(self):
        merge = (self.evaluation or {}).get("merge")
        return merge if isinstance(merge, dict) else {}

    @property
    def cascade(self):
        cascade = (self.evaluation or {}).get("cascade")
        return cascade if isinstance(cascade, dict) else None

    @property
    def tokens(self):
        return pair_tokens(self.agents)

    @property
    def cost(self):
        return self.result.get("total_cost")

    @property
    def seconds(self):
        return self.result.get("duration_seconds")


def read_pair(pair_dir, label, run):
    result = read_json(pair_dir / "result.json")
    return Pair(key="/".join(pair_dir.parts[-3:]), label=label, run=run, dir=pair_dir,
                result=result if isinstance(result, dict) else {}, evaluation=read_json(pair_dir / "eval.json"))


def read_run(logs, run_name, label=None):
    """Every pair of one run, in key order."""
    return [read_pair(path.parent, label or run_name, run_name)
            for path in sorted((Path(logs) / run_name).glob("*/*/*/*/result.json"))]


def arm_outcomes(logs, run_name, label=None):
    """One run's pairs keyed repo/task/pair, the shape render takes."""
    return {pair.key: pair for pair in read_run(logs, run_name, label)}


# Groups: one per arm label and model.

@dataclass
class Group:
    """The pairs of one arm label and one model, every run of them."""
    label: str
    model: str
    pairs: list = field(default_factory=list)

    @property
    def multi_agent(self):
        return any(pair.setting != "solo" for pair in self.pairs)

    def first_attempts(self):
        """Each pair key's first attempt, for the paired tests."""
        found = {}
        for pair in self.pairs:
            found.setdefault(pair.key, pair)
        return found


def make_groups(arms):
    """Groups from {label: {key: Pair}} or {label: [Pair]}, one per label
    and model in the order they first appear."""
    groups = {}
    for label, pairs in arms.items():
        for pair in (pairs.values() if isinstance(pairs, dict) else pairs):
            groups.setdefault((label, pair.model), Group(label, pair.model)).pairs.append(pair)
    return list(groups.values())


def pooled_groups(groups):
    """A pooled group per label with more than one model, its pairs every
    model's: App C pools by summing the counts, never by averaging rates."""
    by_label = {}
    for group in groups:
        by_label.setdefault(group.label, []).append(group)
    return [Group(label, "pooled", [p for g in members for p in g.pairs])
            for label, members in by_label.items() if len(members) > 1]


def model_name(model):
    return model if model else "-"


def find_group(groups, label, model):
    return next((g for g in groups if g.label == label and g.model == model), None)


def role_label(groups, role, given=None):
    """The label of an arm's role (solo, coop, coop-nocomm, coop-git): the
    one given, else the label named like the role, else the first whose
    pairs say they ran that arm (result.json "arm"), or, for solo, the
    solo setting."""
    labels = list(dict.fromkeys(g.label for g in groups))
    if given:
        return given if given in labels else None
    if role in labels:
        return role
    for group in groups:
        if any(pair.arm == role or (role == "solo" and pair.setting == "solo") for pair in group.pairs):
            return group.label
    return None


# Statistics.

def wilson(passed, total, z=1.96):
    """The 95% Wilson score interval for a pass rate, as two percentages."""
    found = difficulty.wilson(passed, total, z)
    return (0.0, 0.0) if found is None else (100 * found[0], 100 * found[1])


def mcnemar_exact(only_first, only_second):
    """Two-sided exact McNemar p-value from the discordant pairs."""
    n = only_first + only_second
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(only_first, only_second) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def fisher_exact(a, b, c, d):
    """Two-sided Fisher exact p for the table [[a, b], [c, d]]: the sum of
    the probabilities of every table with the same margins that is no more
    likely than this one."""
    row, column, total = a + b, a + c, a + b + c + d
    if total == 0:
        return 1.0

    def probability(x):
        return math.comb(column, x) * math.comb(total - column, row - x) / math.comb(total, row)

    observed = probability(a)
    low, high = max(0, row - (total - column)), min(row, column)
    return min(1.0, sum(p for p in map(probability, range(low, high + 1)) if p <= observed * (1 + 1e-7)))


def midranks(values):
    """Ranks from 1 with ties given their mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks, start = [0.0] * len(values), 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for i in range(start, end + 1):
            ranks[order[i]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def mann_whitney(first, second):
    """U of the first sample and the two-sided p: exact over the
    permutations of the midranks when the samples are small, else the
    normal approximation with tie and continuity corrections. None when
    a sample is empty."""
    n1, n2 = len(first), len(second)
    if not n1 or not n2:
        return None
    ranks = midranks(list(first) + list(second))
    u = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    if n1 + n2 <= EXACT_MANN_WHITNEY_LIMIT:
        return u, exact_rank_sum_p(ranks, n1)
    return u, normal_mann_whitney_p(u, n1, n2, ranks)


def exact_rank_sum_p(ranks, n1):
    """P(|R - E R| >= |r - E R|) for the rank sum R of n1 values drawn
    from these ranks, counted over every subset (ranks doubled to integers)."""
    doubled = [int(round(2 * r)) for r in ranks]
    observed, mean = sum(doubled[:n1]), n1 * (len(ranks) + 1)
    ways = [dict() for _ in range(n1 + 1)]
    ways[0][0] = 1
    for value in doubled:
        for size in range(n1, 0, -1):
            for total, count in ways[size - 1].items():
                ways[size][total + value] = ways[size].get(total + value, 0) + count
    extreme = sum(count for total, count in ways[n1].items() if abs(total - mean) >= abs(observed - mean))
    return min(1.0, extreme / math.comb(len(ranks), n1))


def normal_mann_whitney_p(u, n1, n2, ranks):
    total = n1 + n2
    ties = sum(t ** 3 - t for t in (ranks.count(r) for r in set(ranks)))
    variance = n1 * n2 / 12 * ((total + 1) - ties / (total * (total - 1)))
    if variance <= 0:
        return 1.0
    z = max(0.0, abs(u - n1 * n2 / 2) - 0.5) / math.sqrt(variance)
    return math.erfc(z / math.sqrt(2))


def bootstrap_rate(clusters, iterations=BOOTSTRAP_ITERATIONS, seed=0):
    """The 95% percentile interval of a pooled rate k/n, resampling whole
    clusters (task sets) with replacement, as two percentages; None with
    fewer than two clusters, where there is no spread to resample."""
    clusters = [c for c in clusters if c[1]]
    if len(clusters) < 2:
        return None
    rng, rates = random.Random(seed), []
    for _ in range(iterations):
        sample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        rates.append(sum(k for k, _ in sample) / sum(n for _, n in sample))
    rates.sort()
    return 100 * rates[round(0.025 * (len(rates) - 1))], 100 * rates[round(0.975 * (len(rates) - 1))]


def task_set_clusters(pairs, success):
    """(k, n) per task set for a predicate over pairs."""
    clusters = {}
    for pair in pairs:
        tally = clusters.setdefault(pair.task_set, [0, 0])
        tally[0] += bool(success(pair))
        tally[1] += 1
    return [tuple(t) for t in clusters.values()]


# Formatting.

def percent(k, n):
    return f"{100 * k / n:.1f}%" if n else "-"


def interval(k, n):
    if not n:
        return "-"
    low, high = wilson(k, n)
    return f"{low:.1f}-{high:.1f}%"


def bootstrap_cell(pairs, success, seed):
    found = bootstrap_rate(task_set_clusters(pairs, success), seed=seed)
    return "one task set" if found is None else f"{found[0]:.1f}-{found[1]:.1f}%"


def p_value(p):
    return "-" if p is None else f"{p:.3f}"


def table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)] + \
           ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def section(title, body):
    return ["", f"## {title}", ""] + body


# Runs and rates.

def minutes(seconds):
    return f"{seconds / 60:.1f}" if seconds else "-"


def run_rows(runs, dataset):
    rows = []
    for label, run_name, pairs, logs in runs:
        config = read_json(Path(logs) / run_name / "config.json") or {}
        summary = read_json(Path(logs) / run_name / "summary.json") or {}
        provenance = config.get("provenance") or {}
        covered = len({p.key for p in pairs} & dataset.pairs) if dataset and dataset.pairs else None
        rows.append([label, run_name, ", ".join(sorted({model_name(p.model) for p in pairs})) or "-",
                     first_value(pairs, "arm"), first_value(pairs, "driver"),
                     ", ".join(sorted({p.setting for p in pairs})) or "-",
                     len(pairs), "-" if covered is None else f"{covered}/{len(dataset.pairs)}",
                     short_hash((provenance.get("cooperbench") or {}).get("commit")),
                     short_hash((provenance.get("dataset") or {}).get("git_tree")
                                or (provenance.get("dataset") or {}).get("sha256")),
                     config.get("isolation") or first_value(pairs, "isolation"), model_mismatches(pairs),
                     rate_text(summary.get("pass_rate")), minutes(summary.get("total_time_seconds"))])
    return rows


def model_mismatches(pairs):
    """How many agents ran another model than the run asked for, as
    result.json's model_differs says; unknown for runs that predate it."""
    flags = [a.get("model_differs") for p in pairs for a in p.agents.values()
             if isinstance(a, dict) and "model_differs" in a]
    return sum(1 for flag in flags if flag) if flags else NOT_RECORDED


def first_value(pairs, key):
    values = [p.result.get(key) for p in pairs if p.result.get(key)]
    return str(values[0]) if values else NOT_RECORDED


def short_hash(value):
    return value[:10] if value else NOT_RECORDED


def rate_text(value):
    return f"{100 * value:.1f}%" if isinstance(value, (int, float)) else "-"


def runs_section(runs, dataset):
    header = ["Label", "Run", "Models", "Arm", "Driver", "Setting", "Pairs", "Of the benchmark",
              "CooperBench commit", "Dataset", "Isolation", "Agents on another model", "summary.json pass_rate",
              "Run wall (min)"]
    return section("Runs", table(header, run_rows(runs, dataset)) + [
        "", "Of the benchmark counts the run's pairs among subsets/all.json's; the commit and the dataset "
        "(git tree, else content hash) come from config.json, which runs from before it have not. Isolation "
        "is advisory under the session driver, whose subagent works on the host. Models is result.json's "
        "model, the one asked for; the agents that ran another (the session driver reads it from the "
        "transcript) are counted beside it."])


def rate_rows(groups, seed):
    rows = []
    for group in groups:
        pairs = group.pairs
        passed = sum(p.passed for p in pairs)
        finished = [p for p in pairs if p.finished_outcome != "error"]
        finished_passed = sum(p.finished_outcome == "pass" for p in finished)
        rows.append([group.label, model_name(group.model), len(pairs), passed, percent(passed, len(pairs)),
                     interval(passed, len(pairs)), bootstrap_cell(pairs, lambda p: p.passed, seed),
                     f"{finished_passed}/{len(finished)}", percent(finished_passed, len(finished)),
                     interval(finished_passed, len(finished)), len(pairs) - len(finished),
                     AUTHORS.get(group.label, "-")])
    return rows


def rates_section(groups, seed):
    header = ["Arm", "Model", "Pairs", "Passed", "Rate, errors as failures", "95% Wilson",
              "95% bootstrap over task sets", "Finished pairs passed", "Rate on finished pairs",
              "95% Wilson (finished)", "Errors", "Authors"]
    body = table(header, rate_rows(groups, seed)) + [
        "", "The first rate is the paper's k/n: eval.json both_passed over every attempted pair, a pair "
        "without an evaluation or with an eval error failing and the agents' status ignored, as "
        "CooperBench scores an errored agent's patch. The rate on finished pairs is coordbench's: a pair "
        "an agent left in Error, or with no usable evaluation, is set apart (Errors) rather than failed. "
        "The bootstrap resamples task sets.", "", REFERENCES["rates"]]
    return section("Pass rates", body)


def cooperbench_rate_rows(groups):
    rows = []
    for group in groups:
        scored = [p for p in group.pairs if p.scored]
        passed = sum(p.passed for p in scored)
        errors = sum(p.eval_state == "error" for p in group.pairs)
        rows.append([group.label, model_name(group.model), percent(passed, len(scored)),
                     percent(passed, len(scored) + errors), percent(passed, len(group.pairs))])
    return rows


def cooperbench_rates_section(groups):
    header = ["Arm", "Model", "summary.json: passed / (passed + failed)",
              "Console and ablation page: passed / evaluated", "Website and paper: passed / attempted"]
    return section("CooperBench's three rates", table(header, cooperbench_rate_rows(groups)) + [
        "", "runner/core.py's summary.json leaves eval errors out; its console line and "
        "scripts/gen_ablation_report.py count them in; the website and the paper divide by every pair."])


def cost_rows(groups):
    rows = []
    for group in groups:
        tokens = [p.tokens for p in group.pairs if p.tokens is not None]
        costs = [p.cost for p in group.pairs if p.cost is not None]
        seconds = [p.seconds for p in group.pairs if p.seconds]
        sources = sorted({a.get("cost_source") for p in group.pairs for a in p.agents.values()
                          if isinstance(a, dict) and a.get("cost_source")})
        rows.append([group.label, model_name(group.model), len(group.pairs),
                     f"{sum(tokens) / len(tokens):,.0f}" if tokens else "-",
                     f"{sum(costs):.2f}" if costs else "-", ", ".join(sources) or "-",
                     minutes(sum(seconds) / len(seconds) if seconds else None),
                     minutes(statistics.median(seconds) if seconds else None)])
    return rows


def cost_section(groups):
    header = ["Arm", "Model", "Pairs", "Tokens per pair", "Cost (USD, sum)", "Cost source",
              "Minutes per pair (mean)", "Minutes per pair (median)"]
    return section("Cost and time", table(header, cost_rows(groups)) + [
        "", "A pair whose token counts are all null (a driver that could not measure usage) is left out of "
        "the tokens, and '-' means no pair was measured. Cost is result.json's total_cost summed over the "
        "pairs that have one; the median is CooperBench's ablation page's."])


# Paired comparisons and the coordination gap.

def paired_counts(first, second, success=lambda p: p.passed, usable=lambda p: p.scored):
    """Pairs both groups scored, and the discordant counts."""
    a, b = first.first_attempts(), second.first_attempts()
    common = [k for k in a if k in b and usable(a[k]) and usable(b[k])]
    only_first = sum(success(a[k]) and not success(b[k]) for k in common)
    only_second = sum(success(b[k]) and not success(a[k]) for k in common)
    both = sum(success(a[k]) for k in common), sum(success(b[k]) for k in common)
    return len(common), only_first, only_second, both


def comparison_rows(groups):
    rows = []
    for first, second in combinations(groups, 2):
        n, only_first, only_second, _ = paired_counts(first, second)
        rows.append([f"{display(first)} vs {display(second)}", n, only_first, only_second,
                     p_value(mcnemar_exact(only_first, only_second))])
    return rows


def display(group):
    return group.label if not group.model else f"{group.label} ({group.model})"


def comparisons_section(groups):
    header = ["Arms", "Pairs both scored", "Only the first passed", "Only the second passed", "Exact McNemar p"]
    return section("Paired comparisons", table(header, comparison_rows(groups)) + [
        "", "On the pairs both arms scored (an eval.json without an error), the first attempt of each."])


def gap_rows(groups, solo_label):
    rows = []
    for group in groups:
        baseline = find_group(groups, solo_label, group.model) if solo_label else None
        if baseline is None or group.model == "pooled" or not group.multi_agent:
            continue
        rows.append(gap_row(group, [(baseline, group)]))
    for label in dict.fromkeys(g.label for g in groups):
        members = [(find_group(groups, solo_label, g.model), g) for g in groups
                   if g.label == label and g.multi_agent and g.model != "pooled"]
        members = [(b, g) for b, g in members if b is not None]
        if len(members) > 1:
            rows.append(gap_row(Group(label, "pooled"), members))
    return rows


def gap_row(group, couples):
    """The gap summed over (baseline, arm) couples of the same model."""
    n = only_solo = only_arm = solo_passed = arm_passed = 0
    for baseline, arm in couples:
        count, first, second, (k_solo, k_arm) = paired_counts(baseline, arm)
        n, only_solo, only_arm = n + count, only_solo + first, only_arm + second
        solo_passed, arm_passed = solo_passed + k_solo, arm_passed + k_arm
    solo_rate, arm_rate = (solo_passed / n, arm_passed / n) if n else (None, None)
    gap = 100 * (solo_rate - arm_rate) if n else None
    relative = f"{100 * (solo_rate - arm_rate) / solo_rate:.0f}%" if n and solo_rate else "-"
    return [group.label, model_name(group.model), couples[0][0].label, n, percent(solo_passed, n),
            percent(arm_passed, n), "-" if gap is None else f"{gap:.1f}", relative, only_solo, only_arm,
            p_value(mcnemar_exact(only_solo, only_arm))]


def gap_section(groups, solo_label):
    header = ["Arm", "Model", "Solo", "Pairs both scored", "Solo rate", "Arm rate", "Gap (points)",
              "Relative gap", "Only Solo passed", "Only the arm passed", "Exact McNemar p"]
    rows = gap_rows(groups, solo_label)
    if not rows:
        return section("Coordination gap", ["Not computed: it needs a Solo run and a multi-agent run of the "
                                            "same model."])
    return section("Coordination gap", table(header, rows) + [
        "", "Solo minus the arm on the pairs both scored (Fig 4's 'coordination gap'); relative is the gap "
        "over the Solo rate; pooled rows sum the counts across models.", "", REFERENCES["gap"]])


def git_rows(groups, roles):
    rows = []
    for coop in [g for g in groups if g.label == roles["coop"]]:
        git = find_group(groups, roles["coop-git"], coop.model)
        solo = find_group(groups, roles["solo"], coop.model) if roles["solo"] else None
        if git is None:
            continue
        coop_rate, git_rate = rate_of(coop), rate_of(git)
        solo_rate = rate_of(solo) if solo else None
        n, only_coop, only_git, _ = paired_counts(coop, git)
        rows.append([model_name(coop.model), points(coop_rate), points(git_rate), points(solo_rate),
                     signed(git_rate, solo_rate), signed(coop_rate, solo_rate), signed(git_rate, coop_rate),
                     n, p_value(mcnemar_exact(only_coop, only_git))])
    return rows


def rate_of(group):
    return 100 * sum(p.passed for p in group.pairs) / len(group.pairs) if group and group.pairs else None


def points(value):
    return "-" if value is None else f"{value:.1f}%"


def signed(first, second):
    return "-" if first is None or second is None else f"{first - second:+.1f}"


def git_section(groups, roles):
    if not (roles["coop"] and roles["coop-git"]):
        return []
    header = ["Model", "Coop", "Coop-git", "Solo", "Gap with git", "Gap without", "Git improvement",
              "Pairs both scored", "Exact McNemar p"]
    return section("Git ablation", table(header, git_rows(groups, roles)) + [
        "", "The blog post's columns: each rate over the arm's attempted pairs, the gaps signed as arm minus "
        "Solo, the McNemar test on the pairs both scored.", "", REFERENCES["git"]])


# Merges.

def naive_conflict(pair):
    """Whether the two patches conflict on the naive merge: the cascade's
    own merge when eval.json has one, else 63b9d44's merge.status (an
    identical pair merges cleanly; missing_input says nothing); None when
    unknown or for a single agent."""
    naive = (pair.cascade or {}).get("naive")
    if isinstance(naive, dict) and naive.get("conflict") is not None:
        return bool(naive["conflict"])
    status = pair.merge.get("status")
    if status == "conflicts":
        return True
    return False if status in ("clean", "identical") else None


def clean_merge_failed(pair):
    """Whether a pair that merged cleanly still failed its tests, by the
    cascade's naive step when there is one, else by eval.json on a naive
    or identical merge; None when the merge was not clean."""
    if naive_conflict(pair) is not False or not pair.scored:
        return None
    naive = (pair.cascade or {}).get("naive")
    if isinstance(naive, dict) and naive.get("f1") is not None:
        return not (passed_value(naive.get("f1")) and passed_value(naive.get("f2")))
    return not pair.passed


def passed_value(value):
    if isinstance(value, dict):
        return bool(value.get("passed", value.get("both_passed")))
    return bool(value)


def route_letter(pair):
    """BENCHMARK_RESULTS.md's letters: N passed on the naive merge or the
    identical short-circuit, L on the lead's patch alone."""
    strategy = pair.merge.get("strategy")
    if strategy in ("naive", "skip-merge-identical"):
        return "N"
    return "L" if strategy and strategy.startswith("solo-") else ""


def route_rows(groups):
    rows = []
    for group in [g for g in groups if g.multi_agent]:
        passes = [p for p in group.pairs if p.passed]
        strategies = [p.merge.get("strategy") for p in passes]
        routes = {}
        for pair in group.pairs:
            route = (pair.cascade or {}).get("route")
            if route:
                routes[route] = routes.get(route, 0) + 1
        clean = [p for p in group.pairs if p.scored and p.merge.get("status") in ("clean", "identical")]
        rows.append([group.label, model_name(group.model), len(passes), strategies.count("naive"),
                     strategies.count("skip-merge-identical"),
                     sum(1 for s in strategies if s and s.startswith("solo-")),
                     sum(1 for s in strategies if s not in ("naive", "skip-merge-identical")
                         and not (s and s.startswith("solo-"))),
                     ", ".join(f"{route} {count}" for route, count in sorted(routes.items())) or NOT_RECORDED,
                     feature_rates(clean)])
    return rows


def feature_rates(pairs):
    if not pairs:
        return "-"
    first = sum(passed_value((p.evaluation or {}).get("feature1")) for p in pairs)
    second = sum(passed_value((p.evaluation or {}).get("feature2")) for p in pairs)
    return f"{first}/{len(pairs)}, {second}/{len(pairs)}"


def route_section(groups):
    header = ["Arm", "Model", "Passes", "Naive merge (N)", "Identical patches (N)", "Lead alone (L)", "Other",
              "Cascade routes, all pairs", "Features passed on clean or identical merges"]
    return section("Pass route", table(header, route_rows(groups)) + [
        "", "eval.json merge.strategy of each pass (naive, skip-merge-identical, solo-agent1), and cascade.py's "
        "route over every pair that has a cascade. Per-feature results are counted only on clean and "
        "identical merges: on a conflict 63b9d44 writes placeholders unless the lead's patch passes both."])


def conflict_rows(groups):
    rows = []
    for group in [g for g in groups if g.multi_agent]:
        known = [p for p in group.pairs if p.scored and naive_conflict(p) is not None]
        conflicts = sum(naive_conflict(p) for p in known)
        clean = [p for p in known if not naive_conflict(p)]
        semantic = sum(bool(clean_merge_failed(p)) for p in clean)
        rows.append([group.label, model_name(group.model), len(known), conflicts, percent(conflicts, len(known)),
                     interval(conflicts, len(known)), len(clean), semantic, percent(semantic, len(clean))])
    return rows


def conflict_comparison_rows(groups):
    rows = []
    multi = [g for g in groups if g.multi_agent and g.model != "pooled"]
    for first, second in combinations(multi, 2):
        if first.model != second.model:
            continue
        n, only_first, only_second, _ = paired_counts(
            first, second, success=lambda p: bool(naive_conflict(p)),
            usable=lambda p: p.scored and naive_conflict(p) is not None)
        rows.append([f"{first.label} vs {second.label}", model_name(first.model), n, only_first, only_second,
                     p_value(mcnemar_exact(only_first, only_second))])
    return rows


def conflict_section(groups):
    header = ["Arm", "Model", "Pairs with a known merge", "Conflicts", "Conflict rate", "95% Wilson",
              "Clean merges", "Clean but tests fail", "Semantic failure rate"]
    body = table(header, conflict_rows(groups)) + [
        "", "The denominator is the scored pairs whose naive-merge result is known (cascade.naive.conflict, "
        "else eval.json merge.status; missing_input is unknown), as the website's rates use the rows with a "
        "merge report. The semantic failure rate, the share of clean merges whose tests fail, is "
        "coordbench's own proxy for Sec 5's semantic coordination gap; the paper gives no rate.", "",
        REFERENCES["conflict"]]
    comparisons = conflict_comparison_rows(groups)
    if comparisons:
        body += ["", "Conflicts compared on the pairs both arms merged (coop against coop-nocomm reproduces "
                 "Fig 5b):", ""] + table(["Arms", "Model", "Pairs both known", "Only the first conflicted",
                                          "Only the second conflicted", "Exact McNemar p"], comparisons)
    return section("Naive-merge conflicts", body)


def cascade_steps(cascade):
    """naive_ok, union_ok and llm_ok as the legacy aggregation counts them
    (evaluate.py _run_multi_aggregation at 59bbf65), and whether the LLM
    step ran."""
    naive, union, llm = (cascade.get(name) or {} for name in ("naive", "union", "llm"))
    naive_ok = naive.get("conflict") is False and passed_value(naive.get("f1")) and passed_value(naive.get("f2"))
    union_ok = bool(union.get("ran")) and union.get("clean") is not False \
        and passed_value(union.get("f1")) and passed_value(union.get("f2"))
    llm_ok = bool(llm.get("ran")) and passed_value(llm.get("f1")) and passed_value(llm.get("f2"))
    return naive_ok, union_ok, llm_ok, bool(llm.get("ran"))


def verdict_value(verdict):
    if isinstance(verdict, dict):
        value = verdict.get("both_passed", verdict.get("passed"))
        return None if value is None else bool(value)
    return None if verdict is None else bool(verdict)


def cascade_row(group):
    with_cascade = [p for p in group.pairs if p.cascade]
    if not with_cascade:
        return None
    n, steps = len(with_cascade), [cascade_steps(p.cascade) for p in with_cascade]
    naive = sum(s[0] for s in steps)
    union = sum(s[0] or s[1] for s in steps)
    llm_ran = any(s[3] for s in steps)
    llm = sum(s[0] or s[1] or s[2] for s in steps)
    paper = sum(bool(verdict_value(p.cascade.get("verdict_paper_era"))) for p in with_cascade)
    code = sum(bool(verdict_value(p.cascade.get("verdict_63b9d44"))) for p in with_cascade)
    return {"n": n, "naive": 100 * naive / n, "union": 100 * union / n, "llm": 100 * llm / n if llm_ran else None,
            "paper": 100 * paper / n, "code": 100 * code / n, "llm_reason": llm_reason(with_cascade)}


def llm_reason(pairs):
    reasons = {str((p.cascade.get("llm") or {}).get("reason")) for p in pairs
               if not (p.cascade.get("llm") or {}).get("ran") and (p.cascade.get("llm") or {}).get("reason")}
    return "; ".join(sorted(reasons))


def cascade_rows(groups):
    rows, found = [], {}
    for group in [g for g in groups if g.multi_agent]:
        row = cascade_row(group)
        if row is None:
            continue
        found[(group.label, group.model)] = row
        llm = "not run" if row["llm"] is None else f"{row['llm']:.2f} ({row['llm'] - row['union']:+.1f})"
        rows.append([group.label, model_name(group.model), row["n"], f"{row['naive']:.2f}",
                     f"{row['union']:.2f} ({row['union'] - row['naive']:+.1f})", llm,
                     f"{row['paper']:.2f}", f"{row['code']:.2f}"])
    return rows, found


def comm_effect_lines(found, roles):
    lines = []
    for (label, model), row in found.items():
        if label != roles["coop"] or (roles["coop-nocomm"], model) not in found:
            continue
        other = found[(roles["coop-nocomm"], model)]
        final = row["llm"] if row["llm"] is not None else row["union"]
        final_other = other["llm"] if other["llm"] is not None else other["union"]
        lines.append(f"Communication effect, {model_name(model)}: {final - final_other:+.2f} points "
                     f"({label} minus {roles['coop-nocomm']}, at the last step that ran).")
    return lines


def cascade_filters(groups):
    filters = set()
    for pair in (p for g in groups for p in g.pairs if p.cascade):
        for name in ("verdict_63b9d44", "verdict_paper_era"):
            verdict = pair.cascade.get(name)
            if isinstance(verdict, dict) and (verdict.get("test_filter") or verdict.get("filter")):
                filters.add(f"{name}: {verdict.get('test_filter') or verdict.get('filter')}")
    return sorted(filters)


def cascade_section(groups, roles):
    rows, found = cascade_rows(groups)
    if not rows:
        return section("Merge cascade (Table 6)", [
            f"{NOT_RECORDED}: no eval.json here has a cascade block (cascade.py adds it when it scores)."])
    reasons = sorted({row["llm_reason"] for row in found.values() if row["llm_reason"]})
    header = ["Arm", "Model", "Pairs with a cascade", "Naive %", "+Union % (cumulative)", "+LLM % (cumulative)",
              "Paper-era verdict %", "63b9d44 verdict %"]
    effects = comm_effect_lines(found, roles)
    body = table(header, rows) + ([""] + effects if effects else []) + [
        "", "naive_ok, union_ok and llm_ok are exclusive and the percentages cumulative, as the legacy "
        "aggregation computes them, over the pairs with a cascade (the paper's are over all 652). The two "
        "verdicts are the paper era's rule (naive, union, resolver; no lead-alone fallback) and 63b9d44's, "
        "never mixed."]
    if reasons:
        body.append("The learned resolver did not run: " + "; ".join(reasons) + ".")
    filters = cascade_filters(groups)
    if filters:
        body.append("Test-file filters: " + "; ".join(filters) + ".")
    body += ["", REFERENCES["cascade"]]
    return section("Merge cascade (Table 6)", body + alone_lines(groups))


def alone_rows(groups):
    rows = []
    for group in [g for g in groups if g.multi_agent]:
        alone = [p.cascade["agent_alone"] for p in group.pairs
                 if p.cascade and isinstance(p.cascade.get("agent_alone"), dict) and len(p.cascade["agent_alone"]) >= 2]
        if not alone:
            continue
        first = sum(both_suites(list(a.values())[0]) for a in alone)
        second = sum(both_suites(list(a.values())[1]) for a in alone)
        overlap = sum(same_suite(*list(a.values())[:2]) for a in alone)
        rows.append([group.label, model_name(group.model), len(alone), first, second, overlap])
    return rows


def both_suites(result):
    return isinstance(result, dict) and passed_value(result.get("f1")) and passed_value(result.get("f2"))


def same_suite(first, second):
    if not (isinstance(first, dict) and isinstance(second, dict)):
        return False
    return any(passed_value(first.get(s)) and passed_value(second.get(s)) for s in ("f1", "f2"))


def alone_lines(groups):
    rows = alone_rows(groups)
    if not rows:
        return []
    return ["", "Each agent's patch alone against both suites (agents may redivide the features, footnote 1; "
            "both patches passing the same suite is a free sign of duplicated work):", ""] + table(
        ["Arm", "Model", "Pairs", "First agent alone passes both", "Second agent alone passes both",
         "Both pass the same suite"], rows)


# Communication.

@dataclass
class Send:
    """One send call: a broadcast is one send with several recipients."""
    sender: str
    recipients: list
    content: str = None
    message_ids: list = field(default_factory=list)


def store_sends(pair_dir):
    store = read_json(pair_dir / "conversation_store.json")
    if not isinstance(store, dict) or not isinstance(store.get("messages"), list):
        return None
    sends = [Send(row.get("from") or row.get("sender_id"), row.get("recipients") or [row.get("to")],
                  row.get("content"), list(row.get("message_ids") or []))
             for row in store["messages"] if isinstance(row, dict)]
    return sends, all(s.content is not None for s in sends)


def event_sends(events):
    """The sends of a run's events: deliveries grouped by send_id, or, in
    a record from before send_id was logged, by adjacency (a broadcast
    emits its deliveries back to back)."""
    sends, by_call, previous = [], {}, None
    for event in events:
        if event.get("kind") != "message":
            previous = None
            continue
        send_id = event.get("send_id")
        if send_id is not None and send_id in by_call:
            send = by_call[send_id]
        elif send_id is None and previous is not None and previous.sender == event.get("agent"):
            send = previous
        else:
            send = Send(event.get("agent"), [], event.get("content"), [])
            sends.append(send)
            if send_id is not None:
                by_call[send_id] = send
        send.recipients.append(event.get("to"))
        if event.get("message_id") is not None:
            send.message_ids.append(event["message_id"])
        if "content" not in event:
            send.content = None
        previous = send
    return sends, all(s.content is not None for s in sends)


def cooperbench_sends(pair_dir):
    """CooperBench's own conversation.json, one entry per delivery."""
    entries = read_json(pair_dir / "conversation.json")
    if not isinstance(entries, list):
        return None
    sends, seen = [], {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("from"), entry.get("message"), entry.get("timestamp"))
        if key not in seen:
            seen[key] = Send(entry.get("from"), [], entry.get("message"), [])
            sends.append(seen[key])
        seen[key].recipients.append(entry.get("to"))
    return sends, all(s.content is not None for s in sends)


def pair_events(pair):
    if "events" not in pair.cache:
        path = pair.dir / "events.jsonl"
        pair.cache["events"] = json_lines(path) if path.is_file() else None
    return pair.cache["events"]


def pair_sends(pair):
    """(sends, whether their text was recorded), or None when nothing about
    the messages was recorded."""
    if "sends" not in pair.cache:
        found = store_sends(pair.dir)
        if found is None and pair_events(pair) is not None:
            found = event_sends(pair_events(pair))
        if found is None:
            found = cooperbench_sends(pair.dir)
        pair.cache["sends"] = found
    return pair.cache["sends"]


def speaker_runs(sends):
    runs, last = 0, object()
    for send in sends:
        if send.sender != last:
            runs, last = runs + 1, send.sender
    return runs


def references(text):
    lines = len(LINE_REFERENCE.findall(text)) + len(SHORT_LINE_REFERENCE.findall(text)) \
        + len(PATH_LINE_REFERENCE.findall(text))
    return lines, len(FILE_PATH.findall(text))


def transcript_files(pair_dir):
    """Each agent's transcripts, its stream first, then its session file:
    claude-cli writes both, the session driver a session file only, and a
    pilot from before that wrote a one-line stream that holds no turns."""
    found = {}
    for suffix in ("_stream.jsonl", "_session.jsonl"):
        for path in sorted(pair_dir.glob(f"*{suffix}")):
            found.setdefault(path.name[: -len(suffix)], []).append(path)
    return found


def main_chain(records):
    """The records of the agent's own turns: a stream's subagent events
    carry a parent_tool_use_id, a transcript's sidechain records
    isSidechain; a transcript that is all sidechain is a subagent's own
    (the session driver copies one) and counts whole."""
    records = [r for r in records if not r.get("parent_tool_use_id")]
    if any(r.get("isSidechain") is False or "isSidechain" not in r for r in records):
        return [r for r in records if not r.get("isSidechain")]
    return records


def tool_blocks(records, kind):
    """The distinct content blocks of one type in the records' messages."""
    found = {}
    for record in records:
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == kind:
                found[block.get("id") or block.get("tool_use_id") or id(block)] = block
    return list(found.values())


def transcript_counts(path):
    records = main_chain(json_lines(path))
    if not any(r.get("type") == "assistant" for r in records):
        return None
    calls, results = tool_blocks(records, "tool_use"), tool_blocks(records, "tool_result")
    sends = receives = 0
    for call in calls:
        name = str(call.get("name") or "").split("__")[-1]
        command = str((call.get("input") or {}).get("command") or "") if name == "Bash" else ""
        sends += (name in SEND_TOOLS) + len(SEND_ALIASES.findall(command))
        receives += (name in RECEIVE_TOOLS) + len(RECEIVE_ALIASES.findall(command))
    return {"calls": len(calls), "results": len(results), "sends": sends, "receives": receives}


def pair_overhead(pair):
    """Send and receive calls against all the agents' tool calls: the
    hub's own count of coordination calls when events.jsonl is there
    (every alias in a chained shell command counted), else the aliases
    and MCP calls found in the transcripts; None without a transcript."""
    counts = [next((c for c in map(transcript_counts, paths) if c is not None), None)
              for paths in transcript_files(pair.dir).values()]
    counts = [c for c in counts if c is not None]
    if not counts:
        return None
    total = {name: sum(c[name] for c in counts) for name in ("calls", "results", "sends", "receives")}
    events = pair_events(pair)
    if events is not None:
        tools = [e.get("tool") for e in events if e.get("kind") == "tool"]
        total["sends"] = sum(t in SEND_TOOLS for t in tools)
        total["receives"] = sum(t in RECEIVE_TOOLS for t in tools)
    return total


def pair_judge(pair):
    if "judge" not in pair.cache:
        found = read_json(pair.dir / "judge.json")
        pair.cache["judge"] = found if isinstance(found, dict) else None
    return pair.cache["judge"]


def speech_act_sources(pair):
    """The speech-act labels judge.json holds, by source: the judge's own
    and, when it kept one, the keyword baseline among its heuristics."""
    judge = pair_judge(pair) or {}
    heuristics = judge.get("heuristics") if isinstance(judge.get("heuristics"), dict) else {}
    found = {}
    for source, labels in (("judge", judge.get("speech_acts")),
                           ("keyword baseline",
                            heuristics.get("speech_acts") or heuristics.get("speech_acts_keyword"))):
        if isinstance(labels, dict) and labels:
            found[source] = {str(k): normal_act(v) for k, v in labels.items()}
    return found


def normal_act(label):
    text = str(label.get("label") if isinstance(label, dict) else label).strip().capitalize()
    return text if text in SPEECH_ACTS else None


def act_of(send, labels):
    for message_id in send.message_ids:
        if labels.get(str(message_id)):
            return labels[str(message_id)]
    return None


def multi_agent_groups(groups):
    return [g for g in groups if g.multi_agent]


def conversation_rows(groups):
    rows = []
    for group in multi_agent_groups(groups):
        known = [(p, pair_sends(p)) for p in group.pairs if pair_sends(p) is not None]
        with_text = sum(1 for _, (sends, text) in known if text)
        turns = [len(sends) for _, (sends, _) in known]
        runs = [speaker_runs(sends) for _, (sends, _) in known]
        overheads = [o for o in (pair_overhead(p) for p in group.pairs) if o and o["calls"]]
        rows.append([group.label, model_name(group.model), len(known), with_text,
                     f"{statistics.mean(turns):.1f}" if turns else NOT_RECORDED,
                     f"{statistics.mean(runs):.1f}" if runs else NOT_RECORDED] + overhead_cells(overheads))
    return rows


def overhead_cells(overheads):
    if not overheads:
        return [NOT_RECORDED] * 3 + [0]
    calls, results = sum(o["calls"] for o in overheads), sum(o["results"] for o in overheads)
    sends, receives = sum(o["sends"] for o in overheads), sum(o["receives"] for o in overheads)
    return [percent(sends, calls), percent(sends + receives, calls), percent(2 * sends, calls + results),
            len(overheads)]


def conversation_section(groups):
    header = ["Arm", "Model", "Conversations", "With text", "Turns (mean)", "Speaker runs (mean)",
              "Overhead: sends / tool calls", "Sends and receives / tool calls",
              "Sends and their results / calls and results", "Pairs with a transcript"]
    return section("Communication", table(header, conversation_rows(groups)) + [
        "", "A turn is one sent message, one per send call (a broadcast is one turn), from "
        "conversation_store.json, events.jsonl or CooperBench's conversation.json; speaker runs count the "
        "changes of speaker instead. The overhead pools the pairs: the sending calls (send_message, "
        "broadcast, and the team's request and respond) and the receiving ones (receive_messages, "
        "wait_for_message) as the hub counted them, each alias of a chained shell command apart, or as the "
        "transcripts show them, over every tool call of both agents' own turns (subagents left out). The "
        "paper counts OpenHands events (actions and observations), so neither basis is its unit.", "",
        REFERENCES["turns"]])


def act_counts(pairs, source):
    """Per pair with labels from the source: its sends with their acts."""
    found = []
    for pair in pairs:
        labels = speech_act_sources(pair).get(source)
        recorded = pair_sends(pair)
        if labels and recorded:
            found.append((pair, [(send, act_of(send, labels)) for send in recorded[0]]))
    return found


def act_share_row(group, source):
    """Each act's count over the labelled conversations, and its share of
    the tool calls over those that also have a transcript."""
    labelled = act_counts(group.pairs, source)
    if not labelled:
        return None
    measured = [(acts, pair_overhead(pair)) for pair, acts in labelled]
    measured = [(acts, o) for acts, o in measured if o and o["calls"]]
    calls = sum(o["calls"] for _, o in measured)
    cells = []
    for act in SPEECH_ACTS:
        count = sum(1 for _, acts in labelled for _, a in acts if a == act)
        share = sum(1 for acts, _ in measured for _, a in acts if a == act)
        cells.append(f"{count} ({percent(share, calls)})")
    return [group.label, model_name(group.model), source, len(labelled), len(measured)] + cells


def plan_question_row(group, source):
    labelled = act_counts(group.pairs, source)
    if not labelled:
        return None
    cells = []
    for conflicted in (False, True):
        chosen = [acts for pair, acts in labelled if naive_conflict(pair) is conflicted]
        plans = [sum(a == "Plan" for _, a in acts) for acts in chosen]
        questions = [sum(a == "Question" for _, a in acts) for acts in chosen]
        ratios = [p / q for p, q in zip(plans, questions) if q]
        cells += [len(chosen), f"{sum(plans) / sum(questions):.2f}" if sum(questions) else "-",
                  f"{statistics.mean(ratios):.2f}" if ratios else "-"]
    return [group.label, model_name(group.model), source] + cells


def first_turn_rows(group, source):
    labelled = [(p, acts) for p, acts in act_counts(group.pairs, source)
                if acts and p.scored and naive_conflict(p) is not None]
    if not labelled:
        return None
    first = [(acts[0][1] == "Plan", naive_conflict(p)) for p, acts in labelled]
    every = [(all_open_with_plan(acts), naive_conflict(p)) for p, acts in labelled]
    return [group.label, model_name(group.model), source, len(labelled)] + plan_cells(first) + plan_cells(every)


def all_open_with_plan(acts):
    opening = {}
    for send, act in acts:
        opening.setdefault(send.sender, act)
    return bool(opening) and all(act == "Plan" for act in opening.values())


def plan_cells(outcomes):
    """Conflict rate with and without the plan, and Fisher's p."""
    with_plan = [c for planned, c in outcomes if planned]
    without = [c for planned, c in outcomes if not planned]
    a, b = sum(with_plan), len(with_plan) - sum(with_plan)
    c, d = sum(without), len(without) - sum(without)
    return [f"{a}/{len(with_plan)} ({percent(a, len(with_plan))})", f"{c}/{len(without)} ({percent(c, len(without))})",
            p_value(fisher_exact(a, b, c, d))]


def first_turn_bucket_rows(groups, source, found):
    """First-turn planning per difficulty bucket, over every multi-agent
    pair that has a bucket."""
    if not found or not found["pairs"]:
        return []
    buckets = {}
    for group in multi_agent_groups(groups):
        for pair, acts in act_counts(group.pairs, source):
            entry = found["pairs"].get(pair.key)
            if acts and entry and entry["bucket"] is not None and pair.scored and naive_conflict(pair) is not None:
                buckets.setdefault(entry["bucket"], []).append((acts[0][1] == "Plan", naive_conflict(pair)))
    return [[source, difficulty.midpoint(bucket)] + plan_cells(outcomes)
            for bucket, outcomes in sorted(buckets.items())]


def speech_act_section(groups, found):
    sources = sorted({s for g in multi_agent_groups(groups) for p in g.pairs for s in speech_act_sources(p)})
    if not sources:
        return section("Speech acts", [f"{NOT_RECORDED}: no judge.json here holds speech acts "
                                       "(coordbench/judge.py labels them)."])
    shares = [r for g in multi_agent_groups(groups) for s in sources for r in [act_share_row(g, s)] if r]
    ratios = [r for g in multi_agent_groups(groups) for s in sources for r in [plan_question_row(g, s)] if r]
    first = [r for g in multi_agent_groups(groups) for s in sources for r in [first_turn_rows(g, s)] if r]
    buckets = [r for s in sources for r in first_turn_bucket_rows(groups, s, found)]
    body = ["Each act's count, and its share of the agents' tool calls over the conversations that have a "
            "transcript (Fig 5c splits the overhead so):", ""]
    body += table(["Arm", "Model", "Labels", "Conversations", "With a transcript"]
                  + [f"{a} (% of tool calls)" for a in SPEECH_ACTS], shares)
    body += ["", "Plan:Question, by naive-merge conflict, as the ratio of totals and the mean of per-trajectory "
             "ratios (a trajectory with no Question left out of the mean):", ""]
    body += table(["Arm", "Model", "Labels", "Pairs without conflict", "Ratio of totals", "Mean of ratios",
                   "Pairs with conflict", "Ratio of totals", "Mean of ratios"], ratios)
    body += ["", "First-turn planning: the conflict rate when the conversation's first message is a Plan and "
             "when it is not, and, as a variant, when every agent's first message is a Plan (Fisher's exact "
             "test):", ""]
    body += table(["Arm", "Model", "Labels", "Pairs", "First message a Plan", "Otherwise", "Fisher p",
                   "Every agent opens with a Plan", "Otherwise", "Fisher p"], first)
    if buckets:
        body += ["", "By difficulty bucket (App C's ten; the paper's eight are undefined), every arm pooled:", ""]
        body += table(["Labels", "Bucket midpoint", "First message a Plan", "Otherwise", "Fisher p"], buckets)
    return section("Speech acts", body + ["", REFERENCES["plan"]])


SPLITS = (("passed / failed", lambda p: p.passed, lambda p: not p.passed),
          ("no conflict / conflict", lambda p: naive_conflict(p) is False, lambda p: naive_conflict(p) is True))


def pair_references(pair):
    """The pair's line and file references over its message text, or None
    when the text was not recorded."""
    recorded = pair_sends(pair)
    if not recorded or not recorded[1]:
        return None
    counts = [references(send.content) for send in recorded[0]]
    return sum(c[0] for c in counts), sum(c[1] for c in counts)


def reference_rows(groups):
    rows = []
    for group in multi_agent_groups(groups):
        counted = [(p, pair_references(p)) for p in group.pairs]
        counted = [(p, refs) for p, refs in counted if refs is not None]
        if not counted:
            continue
        for name, first_side, second_side in SPLITS:
            first = [refs for p, refs in counted if first_side(p)]
            second = [refs for p, refs in counted if second_side(p)]
            rows.append([group.label, model_name(group.model), name, f"{len(first)} / {len(second)}"]
                        + split_cells([r[0] for r in first], [r[0] for r in second])
                        + split_cells([r[1] for r in first], [r[1] for r in second]))
    return rows


def split_cells(first, second):
    mean = lambda values: f"{statistics.mean(values):.1f}" if values else "-"
    tested = mann_whitney(first, second)
    return [f"{mean(first)} / {mean(second)}", p_value(tested[1] if tested else None)]


def reference_section(groups):
    rows = reference_rows(groups)
    if not rows:
        return section("Concrete references", [f"{NOT_RECORDED}: no message text in these runs."])
    header = ["Arm", "Model", "Split", "Pairs", "Line references, mean", "Mann-Whitney p", "File paths, mean",
              "Mann-Whitney p"]
    return section("Concrete references", table(header, rows) + [
        "", "Regex counts over the message text, per trajectory: line references ('line N', 'lines N-M', "
        "'L<N>', '<path>:<N>') and file paths (a token ending in a source extension). Both splits are given, "
        "since the paper does not say whether 'successful' means passing or avoiding conflict.", "",
        REFERENCES["references"]])


# Failure modes, from judge.json.

def judge_part(pair, *names):
    """One part of the pair's judge.json under any of its names (the
    interface's, then the detector spec's), or None."""
    judge = pair_judge(pair) or {}
    return next((judge[n] for n in names if isinstance(judge.get(n), dict)), None)


def judged(group, *names):
    return [(p, part) for p in group.pairs for part in [judge_part(p, *names)] if part is not None]


def error_group(commerrors):
    """The accepted label's group, or None for no accepted label."""
    issue = commerrors.get("issue")
    if not isinstance(issue, dict) or commerrors.get("accepted") is False:
        return None
    code = issue.get("code") or issue.get("category")
    return issue.get("group") or commerrors.get("group") or ERROR_CODES.get(code)


def error_code(commerrors):
    issue = commerrors.get("issue")
    if not isinstance(issue, dict) or commerrors.get("accepted") is False:
        return None
    return issue.get("code") or issue.get("category")


def error_rows(groups):
    rows, codes = [], []
    for group in multi_agent_groups(groups):
        found = judged(group, "commerrors", "communication_errors")
        if not found:
            continue
        judged_keys = {p.dir for p, _ in found}
        silent = [p for p in group.pairs if p.scored and p.dir not in judged_keys and pair_sends(p)
                  and not pair_sends(p)[0]]
        labels = [error_group(c) for _, c in found]
        cells = [f"{labels.count(g)} ({percent(labels.count(g), len(found))})" for g in ERROR_GROUPS]
        any_error = sum(1 for label in labels if label)
        rows.append([group.label, model_name(group.model), len(found)] + cells
                    + [f"{any_error} ({percent(any_error, len(found))}, {interval(any_error, len(found))})",
                       f"{any_error}/{len(found) + len(silent)} ({percent(any_error, len(found) + len(silent))})"])
        found_codes = [error_code(c) for _, c in found]
        codes.append([group.label, model_name(group.model)]
                     + [found_codes.count(code) for code in ERROR_CODES])
    return rows, codes


def judge_provenance(groups):
    seen = set()
    for pair in (p for g in groups for p in g.pairs):
        judge = pair_judge(pair)
        if judge:
            seen.add(f"{judge.get('backend') or '-'} / {judge.get('model') or '-'} / "
                     f"{judge.get('prompt_variant') or '-'}")
    return sorted(seen)


@dataclass
class SymptomTally:
    """The judged failed pairs of every group, by symptom and family."""
    rows: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    failed: dict = field(default_factory=dict)
    coordination: dict = field(default_factory=dict)
    primary: dict = field(default_factory=dict)
    family: dict = field(default_factory=dict)
    secondary: dict = field(default_factory=dict)


def symptom_tally(groups):
    found = SymptomTally()
    for group in multi_agent_groups(groups):
        judged_failed = [s for p, s in judged(group, "symptoms") if p.scored and not p.passed]
        if not judged_failed:
            continue
        name = display(group)
        coordination = [s for s in judged_failed if s.get("level") == "coordination"]
        implementation = len(judged_failed) - len(coordination)
        found.rows.append([group.label, model_name(group.model), len(judged_failed), implementation,
                           len(coordination), percent(implementation, len(judged_failed))])
        found.columns.append(name)
        found.failed[name], found.coordination[name] = len(judged_failed), len(coordination)
        for symptom in coordination:
            primary = symptom.get("primary") or symptom.get("primary_symptom") or "none"
            tally(found.primary, primary, name)
            tally(found.family, symptom.get("family") or FAMILIES.get(primary) or "unmapped", name)
            for secondary in symptom.get("secondary") or symptom.get("secondary_symptoms") or []:
                tally(found.secondary, secondary, name)
    return found


def tally(counts, key, column):
    counts.setdefault(key, {})
    counts[key][column] = counts[key].get(column, 0) + 1


def distribution_table(counts, found, first_header, reference=None):
    """One row per name, a cell per group: the count, its share of the
    coordination-level failures and of every judged failure."""
    names = list(dict.fromkeys(list(reference or {}) + sorted(counts)))
    rows = []
    for name in names:
        cells = []
        for column in found.columns:
            k = counts.get(name, {}).get(column, 0)
            cells.append(f"{k} ({percent(k, found.coordination[column])}; {percent(k, found.failed[column])})")
        rows.append([name] + cells + ([f"{reference[name]}%" if name in reference else "-"] if reference else []))
    return table([first_header] + found.columns + (["Table 1"] if reference else []), rows)


def symptom_lines(groups):
    found = symptom_tally(groups)
    if not found.rows:
        return []
    lines = ["", "Symptoms of failed pairs; a coordination label needs a verified conversation quote, else the "
             "level is implementation (App G):", ""]
    lines += table(["Arm", "Model", "Failed pairs judged", "Implementation", "Coordination",
                    "Implementation share"], found.rows)
    lines += ["", "Primary symptoms, each cell the count with its share of the coordination-level failures and "
              "of every judged failure (Table 1's basis is unstated; its rows sum to 99.9):", ""]
    lines += distribution_table(found.primary, found, "Symptom", TABLE_1)
    lines += ["", "Failure families (the website's taxonomy tree, which has no Broken commitment):", ""]
    lines += distribution_table(found.family, found, "Family")
    if found.secondary:
        lines += ["", "Secondary symptoms, counted apart:", ""] + distribution_table(found.secondary, found, "Symptom")
    return lines


def heuristic_rows(groups):
    keys = sorted({k for g in groups for p in g.pairs if pair_judge(p)
                   for k in (pair_judge(p).get("heuristics") or {}) if k not in ("speech_acts", "speech_acts_keyword")})
    rows = []
    for group in multi_agent_groups(groups):
        found = [pair_judge(p).get("heuristics") for p in group.pairs
                 if pair_judge(p) and isinstance(pair_judge(p).get("heuristics"), dict)]
        if found:
            rows.append([group.label, model_name(group.model), len(found)]
                        + [sum(1 for h in found if h.get(k)) for k in keys])
    return keys, rows


def emergent_rows(groups):
    tags = sorted({t for g in groups for p in g.pairs if pair_judge(p)
                   for t in (pair_judge(p).get("emergent") or {})})
    rows = []
    for group in multi_agent_groups(groups):
        found = [pair_judge(p).get("emergent") for p in group.pairs
                 if pair_judge(p) and isinstance(pair_judge(p).get("emergent"), dict)]
        if found:
            rows.append([group.label, model_name(group.model), len(found)]
                        + [sum(1 for e in found if isinstance(e.get(t), dict) and e[t].get("present")) for t in tags])
    return tags, rows


def failure_section(groups):
    provenance = judge_provenance(groups)
    if not provenance:
        return section("Failure modes", [f"{NOT_RECORDED}: no judge.json in these runs (coordbench/judge.py "
                                         "writes one per pair; no model is called here)."])
    body = ["Judge (backend / model / prompt variant): " + "; ".join(provenance) + "."]
    errors, codes = error_rows(groups)
    if errors:
        body += ["", "Communication errors, one label per conversation, accepted only with two verified quotes; "
                 "the percentages are over the judged conversations (every one has a message), and the last "
                 "column over every scored pair, those without a message added:", ""]
        body += table(["Arm", "Model", "Judged"] + list(ERROR_GROUPS) + ["Any error", "Any error, all scored"],
                      errors)
        body += [""] + table(["Arm", "Model"] + list(ERROR_CODES), codes) + ["", REFERENCES["errors"]]
    body += symptom_lines(groups)
    keys, heuristics = heuristic_rows(groups)
    if heuristics and keys:
        body += ["", "Free cross-checks, pairs where each found something (beside the labels, never replacing "
                 "them):", ""] + table(["Arm", "Model", "Pairs"] + keys, heuristics)
    tags, emergent = emergent_rows(groups)
    if emergent and tags:
        body += ["", "Emergent behaviours, coordbench-labelled (the paper gives no method):", ""] + table(
            ["Arm", "Model", "Pairs"] + tags, emergent)
    return section("Failure modes", body)


# Difficulty.

def difficulty_input(groups, solo_label):
    solo, arms = {}, {}
    for group in groups:
        if group.model == "pooled":
            continue
        trials = {}
        for pair in group.pairs:
            if pair.scored:
                trials.setdefault(pair.key, []).append(pair.passed)
        if group.label == solo_label:
            solo[group.model or "unknown"] = trials
        elif group.multi_agent:
            arms[(group.label, group.model or "unknown")] = trials
    return solo, arms


def difficulty_entries(found):
    """Every curve, then the pooled curve of each arm run on more than one
    model (with one model it is that model's curve again)."""
    models = {}
    for entry in found["curves"].values():
        models[entry["arm"]] = models.get(entry["arm"], 0) + 1
    return list(found["curves"].items()) + [(f"{arm}|pooled", curve) for arm, curve in found["pooled"].items()
                                           if models.get(arm, 0) > 1]


def difficulty_rows(found):
    rows = []
    for name, entry in difficulty_entries(found):
        arm, model = name.split("|", 1)
        summary = entry["summary"]
        rows.append([arm, model, summary["pairs"], summary["filled_buckets"],
                     "-" if not summary["limits"] else f"{summary['limits'][0]}-{summary['limits'][1]}",
                     decimal(summary["auc_solo"]), decimal(summary["auc_arm"]), decimal(summary["delta_auc"]),
                     decimal(summary["retention"], 2)])
    return rows


def decimal(value, places=3):
    return "-" if value is None else f"{value:.{places}f}"


def bucket_rows(found):
    rows = []
    for name, entry in difficulty_entries(found):
        arm, model = name.split("|", 1)
        for row in entry["buckets"]:
            if row["n"]:
                rows.append([arm, model, row["midpoint"], row["n"], f"{row['k_solo']}/{row['n_solo']}",
                             interval(row["k_solo"], row["n_solo"]), f"{row['k_arm']}/{row['n_arm']}",
                             interval(row["k_arm"], row["n_arm"])])
    return rows


def difficulty_section(found):
    if found is None or not found["curves"]:
        return section("Difficulty (App C)", ["Not computed: it needs a Solo run and a multi-agent run of the "
                                              "same model, both scored."])
    body = found["notes"] + [""] + table(["Arm", "Model", "Pairs", "Filled buckets", "Limits", "AUC Solo",
                                          "AUC arm", "Delta AUC", "Retention"], difficulty_rows(found))
    body += ["", "Filled buckets, Solo and arm passes with their 95% Wilson intervals:", ""]
    body += table(["Arm", "Model", "Midpoint", "Pairs", "Solo", "Wilson", "Arm", "Wilson"], bucket_rows(found))
    return section("Difficulty (App C)", body + ["", REFERENCES["difficulty"]])


# The dataset.

def dataset_bases(dataset):
    """The pair subsets every headline rate is given again over, each
    with a test on a pair's dataset facts, for the facts that are known."""
    bases = [("All pairs", lambda f: True)]
    if dataset.changes is not None:
        bases += [("Features unamended", lambda f: f["amended"] is False),
                  ("Features and task unamended", lambda f: f["amended"] is False and not f["task_changed"])]
    if dataset.audit is not None:
        bases += [(f"Audit {name}", lambda f, name=name: f["audit"] == name)
                  for name in ("both OK", "any SPEC", "any TEST")]
    if dataset.gradeable is not None:
        bases.append(("Gradeable here", lambda f: f["gradeable"] is True))
    return bases


def dataset_rows(groups, dataset, measure):
    bases = dataset_bases(dataset)
    rows = []
    for group in groups:
        cells = []
        for _, keep in bases:
            cells.append(measure(group, [p for p in group.pairs if keep(dataset.facts(p.key))]))
        rows.append([group.label, model_name(group.model)] + cells)
    return [name for name, _ in bases], rows


def pass_cell(group, pairs):
    passed = sum(p.passed for p in pairs)
    return f"{passed}/{len(pairs)} ({percent(passed, len(pairs))})"


def conflict_cell(group, pairs):
    if not group.multi_agent:
        return "-"
    known = [p for p in pairs if p.scored and naive_conflict(p) is not None]
    conflicts = sum(naive_conflict(p) for p in known)
    return f"{conflicts}/{len(known)} ({percent(conflicts, len(known))})"


def gold_line(groups, dataset):
    summary = dataset.gold["summary"]
    keys = {p.key for g in groups for p in g.pairs}
    verdicts = [dataset.gold["pairs"][k] for k in keys if dataset.gold["pairs"].get(k) is not None]
    return (f"Conflicting gold solutions: {summary.get('conflicts')} of {summary.get('total_pairs')} "
            f"({summary.get('conflict_rate')}) in CooperBench's dataset/gold_conflict_report.json, against the "
            f"paper's {dataset_stats.PAPER_GOLD_CONFLICT_RATE}% on the paper-era dataset; {sum(verdicts)} of the "
            f"{len(verdicts)} pairs these runs cover.")


def ungradeable_lines(groups, dataset):
    keys = sorted({p.key for g in groups for p in g.pairs if dataset.facts(p.key)["gradeable"] is False})
    if not keys:
        return []
    return ["", "Pairs with a feature that does not grade here (its tests do not fail on the base or do not "
            "pass with the gold patch), kept in the rates above and listed apart: " + ", ".join(keys) + "."]


def dataset_section(groups, dataset):
    if dataset is None or not dataset.dir.is_dir():
        return section("Dataset", [f"{NOT_RECORDED}: no CooperBench dataset found (--cooperbench-dir)."])
    body = []
    if dataset.gold is not None:
        body += [gold_line(groups, dataset), ""]
    if dataset.changes is None:
        body += [f"No git history with commit {dataset_stats.PAPER_COMMIT} in {dataset.cooperbench_dir}, so the "
                 "pairs unamended since the paper are unknown.", ""]
    names, passes = dataset_rows(groups, dataset, pass_cell)
    body += ["Pass rate, errors as failures:", ""] + table(["Arm", "Model"] + names, passes)
    names, conflicts = dataset_rows([g for g in groups if g.multi_agent], dataset, conflict_cell)
    if conflicts:
        body += ["", "Naive-merge conflict rate:", ""] + table(["Arm", "Model"] + names, conflicts)
    body += ["", "Each headline rate again over the pairs whose two features' feature.md, feature.patch and "
             "tests.patch are as in the paper's dataset (59bbf65), then with the task's runner.sh, Dockerfile "
             "and other files unchanged too, and by the spec audit's verdicts (OK; SPEC: feature.md amended; "
             "TEST: tests.patch amended)."]
    return section("Dataset", body + ungradeable_lines(groups, dataset))


# CooperBench's published runs, on the same pairs.

def published_outcomes(path):
    """{model: {pair key: (passed, conflict)}} from the website's
    coop/index.json, over the rows with results (a row marked missing has
    no merge report), with the paper-era repository name typst mapped to
    typst_task."""
    index = read_json(path)
    found = {}
    for row in (index or {}).get("tasks") or []:
        features = [row.get("f1", ""), row.get("f2", "")]
        if not all(f.startswith("feature") for f in features):
            continue
        key = dataset_stats.pair_key(row.get("repo"), str(row.get("taskId", "")).removeprefix("task"),
                                     [f[7:] for f in features])
        for model, result in (row.get("results") or {}).items():
            if isinstance(result, dict) and not result.get("missing"):
                found.setdefault(model, {})[key] = (bool(result.get("passed")), result.get("hasConflict"))
    return found


def published_rows(groups, published):
    rows = []
    for group in multi_agent_groups(groups):
        ours = group.first_attempts()
        for model, theirs in sorted(published.items()):
            common = [k for k in ours if k in theirs and ours[k].scored]
            if not common:
                continue
            mine, other = [ours[k].passed for k in common], [theirs[k][0] for k in common]
            only_ours = sum(a and not b for a, b in zip(mine, other))
            only_theirs = sum(b and not a for a, b in zip(mine, other))
            conflicts = [(naive_conflict(ours[k]), theirs[k][1]) for k in common]
            known = [(a, b) for a, b in conflicts if a is not None and b is not None]
            rows.append([group.label, model_name(group.model), model, len(common), percent(sum(mine), len(common)),
                         percent(sum(other), len(common)), only_ours, only_theirs,
                         p_value(mcnemar_exact(only_ours, only_theirs)),
                         percent(sum(a for a, _ in known), len(known)), percent(sum(b for _, b in known), len(known))])
    return rows


def published_section(groups, published):
    if published is None:
        return []
    rows = published_rows(groups, published)
    if not rows:
        return section("Against CooperBench's published runs", ["No pair of these runs is among the published "
                                                                "rows with results."])
    header = ["Arm", "Model", "Published model", "Pairs in both", "Our rate", "Published rate", "Only ours passed",
              "Only theirs passed", "Exact McNemar p", "Our conflict rate", "Published conflict rate"]
    return section("Against CooperBench's published runs", table(header, rows) + [
        "", "The website's coop/index.json, rows with results only. Its paper models were scored by the legacy "
        "cascade (naive, union, resolver) and its Gemini rows by 63b9d44's both_passed, so the two published "
        "rules differ, and its stats cannot be recomputed from its rows."])


# Every pair.

def pair_cell(pair):
    if pair.eval_state == "missing":
        return "error"
    if pair.eval_state == "error":
        return "eval error"
    cell = "pass" if pair.passed else "fail"
    letter = route_letter(pair) if pair.passed else ""
    cell += f" {letter}" if letter else ""
    return cell + (" (agent Error)" if pair.agent_error else "")


def pair_rows(groups):
    keys = sorted({p.key for g in groups for p in g.pairs})
    attempts = [g.first_attempts() for g in groups]
    header = ["Pair"] + [display(g) for g in groups]
    rows = [[key] + [pair_cell(a[key]) if key in a else "-" for a in attempts] for key in keys]
    return table(header, rows) + ["", "Each cell is the pair's first attempt: pass or fail by eval.json, N when it "
                                  "passed on the naive merge or identical patches, L on the lead's patch alone, "
                                  "error when it has no eval.json, '-' when the arm did not run it."]


# The report.

def context(arms, options=None):
    """The groups, the arms' roles and the difficulty, worked out once."""
    options = options or {}
    groups = make_groups(arms)
    roles = {role: role_label(groups, role, options.get(role)) for role in ("solo", "coop", "coop-nocomm", "coop-git")}
    solo, arm_trials = difficulty_input(groups, roles["solo"])
    found = difficulty.compute(solo, arm_trials) if solo and arm_trials else None
    return groups, roles, found


def render(arms, runs=None, dataset=None, published=None, options=None):
    """The report as Markdown. arms is {label: {key: Pair}} (arm_outcomes)
    or {label: [Pair]}; runs, when given, is [(label, run name, [Pair],
    logs)] for the Runs table."""
    options = options or {}
    groups, roles, found = context(arms, options)
    everything = groups + pooled_groups(groups)
    seed = options.get("seed", 0)
    lines = ["# Arms side by side"]
    if runs:
        lines += runs_section(runs, dataset)
    lines += rates_section(everything, seed) + cooperbench_rates_section(everything) + cost_section(everything)
    lines += comparisons_section(groups) + gap_section(everything, roles["solo"]) + git_section(groups, roles)
    lines += route_section(everything) + conflict_section(everything) + cascade_section(everything, roles)
    lines += conversation_section(everything) + speech_act_section(groups, found) + reference_section(everything)
    lines += failure_section(everything) + difficulty_section(found) + dataset_section(everything, dataset)
    lines += published_section(groups, published) + section("Every pair", pair_rows(groups))
    return "\n".join(lines) + "\n"


def default_cooperbench_dir(logs):
    if (Path(logs).parent / "dataset").is_dir():
        return Path(logs).parent
    return Path(os.environ.get("COOPERBENCH_DIR") or Path.home() / "cooperbench")


def parse_runs(args):
    runs = [(arm, f"{args.prefix}-{arm}-{args.tag}") for arm in (args.arm or ([] if args.run else ARMS))]
    for entry in args.run:
        label, _, run_name = entry.partition("=")
        runs.append((label, run_name or label))
    return runs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", type=Path, default=Path.home() / "cooperbench" / "logs")
    parser.add_argument("--tag", default="cc")
    parser.add_argument("--prefix", default="flash")
    parser.add_argument("--arm", action="append", default=[],
                        help="an arm whose run is <prefix>-<arm>-<tag>; the four built-in ones when none is given")
    parser.add_argument("--run", action="append", default=[], metavar="LABEL=RUN_NAME",
                        help="any run, reported under LABEL; a label may repeat")
    parser.add_argument("--cooperbench-dir", type=Path, help="the CooperBench checkout whose dataset/ to read")
    parser.add_argument("--survey-dir", type=Path, default=Path.home() / "cooperbench-survey")
    parser.add_argument("--published", type=Path, help="the website's coop/index.json, to compare on the same pairs")
    for role in ("solo", "coop", "coop-nocomm", "coop-git"):
        parser.add_argument(f"--{role}-label", help=f"the label of the {role} arm, when it is not named so")
    parser.add_argument("--seed", type=int, default=0, help="the bootstrap's seed")
    parser.add_argument("--difficulty-out", type=Path, help="also write difficulty.json here")
    args = parser.parse_args(argv)
    runs = [(label, run_name, read_run(args.logs, run_name, label), args.logs) for label, run_name in parse_runs(args)]
    arms = {}
    for label, _, pairs, _ in runs:
        arms.setdefault(label, []).extend(pairs)
    dataset = dataset_stats.Dataset(args.cooperbench_dir or default_cooperbench_dir(args.logs), args.survey_dir)
    published = published_outcomes(args.published) if args.published else None
    options = {"seed": args.seed, **{role: getattr(args, f"{role.replace('-', '_')}_label")
                                     for role in ("solo", "coop", "coop-nocomm", "coop-git")}}
    print(render(arms, runs, dataset, published, options), end="")
    found = context(arms, options)[2]
    if args.difficulty_out and found is not None:
        args.difficulty_out.write_text(json.dumps(found, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
