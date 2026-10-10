"""The failure modes of a coordbench pair, labelled as CooperBench's paper labels them.

    python3 coordbench/judge.py heuristics <path>...
    python3 coordbench/judge.py commerrors <path>... [--variant appF-7|appF-8]
    python3 coordbench/judge.py symptoms <path>... [--variant symptoms-r1|symptoms-r1-tree]
    python3 coordbench/judge.py speechacts <path>...
    python3 coordbench/judge.py emergent <path>...
    python3 coordbench/judge.py render <section> <path> [--variant V]

A path is a pair's directory (the one that holds result.json) or any
directory above pair directories, which is searched for them. Every
command writes <pair_dir>/judge.json, adding or replacing its own part and
leaving the rest as it was; bench/cooperbench/report.py reads it.

No run spends a token on judging: the model-judged measures are this
separate command, which the owner runs on purpose. heuristics and render
call no model; the other four call one judge per pair. --dry-run says
which pairs would be judged and how large each input is, without a call.

What each command does, after the paper:

- commerrors: App F's communication-error detector (Sec 5, Fig 6). One
  conversation is every message of a pair, both directions, in time
  order; a broadcast is one message. App F's prompt is sent word for word
  (prompts/commerrors-appF-7.txt) as the system prompt, the conversation
  as the user turn. A label stands only when its code is in the variant's
  taxonomy and at least two of its quotes are found verbatim, after
  whitespace and curly quotes are folded, inside the content of one
  message each (the paper's "at least two exact quotes"); otherwise it is
  kept as rejected, with the reason. The codes map to App F's three groups:
  Unresponsiveness C1a, C1b, C2; Hallucination C3a, C3b; Repetition C4a,
  C4b.
- symptoms: App G's symptom annotator (Sec 6.1, Table 1) over every
  failed multi-agent pair, with the conversation, both patches after
  CooperBench's test-file filter, the merged artifact that decided the
  verdict, the conflict regions and the test outcome. The paper's rubric
  is unpublished, so prompts/symptoms-r1.txt is coordbench's
  reconstruction around everything the paper prints of it: Table 1's
  meanings, App H's example of each symptom and App G's rule. A
  coordination label needs at least one verified conversation quote,
  else the level becomes implementation with no symptom (App G: "we
  default to implementation-level failure"). The family comes from the
  website's taxonomy tree, which has no Broken commitment.
- speechacts: one label per message from Plan, Question, Answer, Update
  and Ack (Fig 5c). The paper gives no method; the definitions are
  coordbench's (prompts/speechacts-v1.txt), and a keyword baseline sits
  beside the judge's labels among the heuristics.
- emergent: Sec 6.4's role division, resource division and negotiation,
  defined by the paper's own sentences, and the website's git-setting
  failures blame and sagas. The paper counts none of them; these are
  coordbench's labels, kept apart from CooperBench's measures. Role
  division, resource division and negotiation stand only with a verified
  quote from each of two agents (the paper's "mutual confirmation").

Deviations, each for a reason:

- The fourth bullet of App F is coded C3a. The paper prints "C4a", which
  clashes with the repetition C4a; its own evidence rule and taxonomy
  note call it C3a. Variant appF-8 adds "C5 Plan drift / unilateral
  deviation" to recover Fig 6(d)'s category, which the printed prompt
  lacks; C5 is coordbench's code, counted under Hallucination where
  Fig 6(d) and two of the website's images place it:
  conversation_errors_v5_buckets.png, the source of Fig 6, and
  coordination_failure_taxonomy_tree.png. The default, appF-7, is the
  printed list.
- One line after App F's text restates its Output sentence as a JSON
  schema, so a reply can be read without guessing its shape.
- The judge model. App F names none; Sec 6.1's annotator is GPT-5. The
  openai-compatible backend runs GPT-5, or any model at any endpoint the
  owner names, for the closest match; claude-cli, the default, runs a
  Claude model on the owner's subscription, which is the one deviation
  that cannot be avoided when it is used. Every output records backend,
  model and prompt variant.
- claude-cli runs `claude --print` with App F's text as the whole system
  prompt (--system-prompt replaces Claude Code's own), every tool off,
  no MCP server, no setting source, no skill and no session saved, from
  an empty temporary directory with a throwaway CLAUDE_CONFIG_DIR and
  HOME, so no CLAUDE.md, memory or settings reach the model. The CLI
  exposes no temperature; the paper states none for this judge either.
  The credentials are the drivers' (CLAUDE_CODE_OAUTH_TOKEN, or
  COORDBENCH_CLAUDE_TOKEN), never ANTHROPIC_API_KEY.
- openai-compatible POSTs to <base>/chat/completions with the standard
  library. A reasoning model (gpt-5, o1, o3, o4) accepts only its default
  temperature, so none is sent and reasoning_effort is recorded instead;
  any other model gets temperature 0.
- One retry when a reply is not JSON, never more; a reply that is still
  not JSON leaves no label, and the raw text is kept.
- A quote is checked against message content only, never the header or
  a line's "[id] time from -> to:" prefix, and must lie inside one
  message.
- Team requests and responses are messages too, so a team pair's
  conversation includes them; CooperBench's own conversation.json leaves
  them out, since its team protocol keeps them apart.

The free cross-checks (heuristics) sit beside the labels and never
replace them: unanswered and ignored questions (C1a and C1b candidates),
same-sender near-duplicates with difflib's ratio of at least 0.9 (C4a and
C4b), completion claims naming an identifier or a file the sender's patch
lacks (C3a and Broken commitment), the keyword speech acts, overlapping
hunks of the two patches (Work overlap), a clean merge whose tests fail
(Divergent architecture, and the semantic gap), unanswered team requests
(Unresponsiveness), App D's two compliance checks (an agent whose last
message precedes its last edit, and insertion-marker comments in a
patch), candidates for resource division and negotiation, and, for the
git arms, the website's git-setting failures that the logs can show
(force pushes and hard resets, fetches before the partner's first push,
a failed test run after a push, a commit deleting a hundred lines of a
file the partner changed). The git labels are coordbench's, never
CooperBench rates.

The module reads JSON and text only and imports nothing of coordbench,
like timeline.py, so it can be copied beside a run with its prompts/.
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

FORMAT = "coordbench-judge/1"
JUDGE_FILE = "judge.json"
PROMPTS = Path(__file__).resolve().parent / "prompts"

# App F's codes, its three groups (the taxonomy note and Fig 6) and its
# category names; C5 is coordbench's code for Fig 6(d)'s "Plan drift".
ERROR_GROUPS = {"C1a": "Unresponsiveness", "C1b": "Unresponsiveness", "C2": "Unresponsiveness",
                "C3a": "Hallucination", "C3b": "Hallucination", "C5": "Hallucination",
                "C4a": "Repetition", "C4b": "Repetition"}
ERROR_NAMES = {"C1a": "Unanswered direct question (no reply)", "C1b": "Unanswered direct question (ignored)",
               "C2": "Non-answer or vague answer", "C3a": "Incorrect claim (uncorrected)",
               "C3b": "Incorrect claim (corrected)", "C4a": "Spammy repetition (repeats same information)",
               "C4b": "Spammy repetition (near-duplicate status blocks)", "C5": "Plan drift / unilateral deviation"}
APPF_7 = ("C1a", "C1b", "C2", "C3a", "C3b", "C4a", "C4b")
COMMERROR_VARIANTS = {"appF-7": {"file": "commerrors-appF-7.txt", "codes": APPF_7},
                      "appF-8": {"file": "commerrors-appF-8.txt", "codes": APPF_7 + ("C5",)}}
QUOTES_NEEDED = 2

# Table 1's symptoms in its order, and the website's taxonomy tree
# (coordination_failure_taxonomy_tree.png), which groups them in four
# families, adds Information asymmetry and leaves Broken commitment out.
TABLE_1 = ("Work overlap", "Divergent architecture", "Repetition", "Unresponsiveness", "Unverifiable claims",
           "Broken commitment", "Dependency access", "Placeholder misuse", "Parameter flow", "Timing dependency")
INFORMATION_ASYMMETRY = "Information asymmetry"
SYMPTOM_VARIANTS = {"symptoms-r1": {"file": "symptoms-r1.txt", "names": TABLE_1},
                    "symptoms-r1-tree": {"file": "symptoms-r1-tree.txt", "names": TABLE_1 + (INFORMATION_ASYMMETRY,)}}
FAMILIES = {"Information asymmetry": "Information", "Unverifiable claims": "Information",
            "Dependency access": "Integration", "Parameter flow": "Integration", "Placeholder misuse": "Integration",
            "Divergent architecture": "Structural", "Work overlap": "Structural", "Timing dependency": "Structural",
            "Repetition": "Communication", "Unresponsiveness": "Communication"}
LEVELS = ("coordination", "implementation")
EVIDENCE_SOURCES = ("conversation", "patch", "outcome")
SYMPTOM_RUBRIC = ("coordbench's reconstruction: the paper's rubric and prompt are unpublished, so the prompt "
                  "is written around Table 1's caption and ten meanings, App H's example for each symptom, "
                  "App G's decision rule and its instruction to emit labels, all verbatim, with a tie-break "
                  "for the confusion App G names")

SPEECH_ACTS = ("Plan", "Question", "Answer", "Update", "Ack")
SPEECH_ACT_VARIANTS = {"speechacts-v1": {"file": "speechacts-v1.txt"}}
EMERGENT_TAGS = ("role_division", "resource_division", "negotiation")
GIT_JUDGE_TAGS = ("blame", "sagas")
EMERGENT_VARIANTS = {"emergent-v1": {"file": "emergent-v1.txt"}}
EMERGENT_RULES = {
    "role_division": "a verified quote from each of two agents (Sec 6.4: mutual confirmation)",
    "resource_division": "a verified quote from each of two agents, one naming a file or a line range "
                         "(Sec 6.4: specificity)",
    "negotiation": "a verified quote from each of two agents: the options and the choice",
    "blame": "one verified quote",
    "sagas": "two verified quotes",
}
CAUSES = ("Expectation", "Commitment", "Communication")
CAUSE_VARIANTS = {"causes-v1": {"file": "causes-v1.txt"}}

# The blog post's names for the git-setting failures
# (the-curious-case-of-miscoordination.mdx), beside git-failures.json's ids.
GIT_BLOG_NAMES = {"impatience": "The Real-Time Interaction Problem", "destructive": "Uncooperative Behaviors",
                  "broken": "The Commitment Problem", "git_disasters": "Misuse of Cooperation Tools",
                  "blame": "Hallucination in Communication", "sagas": "Hallucination in Communication"}
GIT_ARMS = ("coop-git", "sdlc")

# Input sizes. The spec's: a conversation above 100,000 characters keeps
# its first and last 50,000; the last 4,000 characters of a test output.
CONVERSATION_LIMIT = 100_000
CONVERSATION_KEEP = 50_000
TEST_TAIL = 4_000
MATCHED_LINES_KEPT = 200
PATCH_LIMIT = 40_000
CONFLICT_LIMIT = 20_000
LISTED_KEPT = 50
NEAR_DUPLICATE = 0.9
DESTRUCTIVE_LINES = 100

DEFAULT_MODELS = {"claude-cli": "claude-sonnet-5-5", "openai-compatible": "gpt-5"}
JUDGE_URL = "COORDBENCH_JUDGE_URL"
JUDGE_KEY = "COORDBENCH_JUDGE_KEY"
JUDGE_MODEL = "COORDBENCH_JUDGE_MODEL"
TOKEN_NAMES = ("CLAUDE_CODE_OAUTH_TOKEN", "COORDBENCH_CLAUDE_TOKEN")
# What the judge's claude needs from the environment besides the token:
# where its programs are, the locale, and a proxy with its certificates.
PASSED_THROUGH = ("PATH", "LANG", "LC_ALL", "TZ", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
                  "http_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
EMPTY_MCP = '{"mcpServers": {}}'
REASONING_MODEL = re.compile(r"^(?:[\w.-]+/)?(?:gpt-5|o[1-9])", re.IGNORECASE)
CALL_TIMEOUT = 600

NOT_RECORDED = "message text not recorded (a run from before coordbench logged it)"


def warn(text):
    print(f"judge: {text}", file=sys.stderr)


class JudgeError(Exception):
    """A judge call that failed outright: the CLI or the endpoint said so."""


# Reading a pair directory.

def read_json(path):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        warn(f"{path}: cannot read: {error}")
        return None


def read_jsonl(path):
    """A JSON-lines file as its objects, a cut line skipped; None when the
    file is not there."""
    path = Path(path)
    if not path.is_file():
        return None
    found = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line) if line.strip() else None
        except ValueError:
            value = None
        if isinstance(value, dict):
            found.append(value)
    return found


def read_text(path):
    path = Path(path)
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


@dataclass
class Pair:
    dir: Path
    result: dict
    evaluation: dict = None
    events: list = None

    @property
    def key(self):
        """run/arm/repo/task/features, which names a pair among runs."""
        result = self.result
        features = "_".join(f"f{f}" for f in result.get("features") or [])
        return f"{result.get('run_name')}/{result.get('arm') or result.get('setting')}/{result.get('repo')}/" \
               f"{result.get('task_id')}/{features}"


def load_pair(pair_dir):
    pair_dir = Path(pair_dir)
    result = read_json(pair_dir / "result.json")
    if not isinstance(result, dict):
        raise FileNotFoundError(f"{pair_dir / 'result.json'} is not there to read")
    evaluation = read_json(pair_dir / "eval.json")
    return Pair(pair_dir, result, evaluation if isinstance(evaluation, dict) else None,
                read_jsonl(pair_dir / "events.jsonl"))


def pair_dirs(paths):
    """Every pair directory at or under the paths, each once, in order."""
    found = []
    for path in map(Path, paths):
        candidates = [path] if (path / "result.json").is_file() else sorted(p.parent for p in path.rglob("result.json"))
        for candidate in candidates:
            if candidate.resolve() not in {f.resolve() for f in found}:
                found.append(candidate)
    return found


def agent_records(result):
    """result.json's agents by id; solo's singular "agent" under its id."""
    agents = result.get("agents")
    if isinstance(agents, dict):
        return {k: v for k, v in agents.items() if isinstance(v, dict)}
    single = result.get("agent")
    if isinstance(single, dict):
        return {single.get("id") or "solo": single}
    return {}


def as_list(value):
    if isinstance(value, list):
        return value
    return [] if value is None else [value]


def agent_features(pair):
    """Each agent's features by id, from the hub's run_started event, else
    from result.json's records."""
    started = next((e for e in pair.events or [] if e.get("kind") == "run_started"), None)
    if started and isinstance(started.get("agents"), list):
        return {a["id"]: list(a.get("features") or []) for a in started["agents"] if isinstance(a, dict) and "id" in a}
    return {agent: as_list(record.get("feature_id", record.get("features")))
            for agent, record in agent_records(pair.result).items()}


def is_multi_agent(pair):
    return len(agent_features(pair)) >= 2


def patch_file(pair, agent):
    """The agent's patch as run.py names it: agent<feature>.patch for its
    first feature, else <agent>.patch."""
    features = agent_features(pair).get(agent) or []
    names = [f"agent{features[0]}.patch"] if features else []
    if pair.result.get("setting") == "solo":
        names.append("solo.patch")
    for name in names + [f"{agent}.patch"]:
        if (pair.dir / name).is_file():
            return name
    return None


def agent_patches(pair):
    """Each agent's patch text by id, for the agents that left one."""
    found = {}
    for agent in agent_features(pair):
        name = patch_file(pair, agent)
        if name:
            found[agent] = read_text(pair.dir / name) or ""
    return found


def pair_header(pair):
    result = pair.result
    return {"repo": result.get("repo"), "task_id": result.get("task_id"), "features": result.get("features"),
            "arm": result.get("arm"), "setting": result.get("setting"), "model": result.get("model"),
            "driver": result.get("driver"), "run_name": result.get("run_name"), "run_id": result.get("run_id")}


# The conversation.

@dataclass
class Message:
    id: str
    sender: str
    to: str
    content: str
    timestamp: float = None
    timestamp_iso: str = None
    kind: str = "message"
    note: str = ""
    recipients: list = field(default_factory=list)


@dataclass
class Conversation:
    messages: list
    source: str = None
    recorded: bool = True
    reason: str = None
    includes_requests: bool = False


def run_epoch(events):
    started = next((e for e in events or [] if e.get("kind") == "run_started"), None)
    return started.get("epoch") if started else None


def event_time(event, epoch):
    """An event's epoch time: its own timestamp, else run_started's epoch
    plus its t."""
    if isinstance(event.get("timestamp"), (int, float)):
        return float(event["timestamp"])
    if epoch is not None and isinstance(event.get("t"), (int, float)):
        return float(epoch) + float(event["t"])
    return None


def iso_of(epoch):
    return None if epoch is None else datetime.fromtimestamp(epoch).isoformat()


def epoch_of_iso(text):
    """An ISO time as epoch seconds; a naive one is read as local time, as
    coop_msg.py's datetime.now().isoformat() wrote it."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def continues(group, event):
    """Whether a delivery with no send_id is the next of the broadcast
    whose deliveries came just before it: same sender, same text."""
    return bool(group) and group[0].get("send_id") is None and group[0].get("agent") == event.get("agent") \
        and group[0].get("content") == event.get("content")


def delivery_groups(events):
    """The "message" events grouped by the call that sent them; a record
    without send_id groups a broadcast's deliveries by adjacency."""
    groups, by_call, previous = [], {}, None
    for event in events:
        if event.get("kind") != "message":
            previous = None
            continue
        send_id = event.get("send_id")
        group = by_call.get(send_id) if send_id is not None else None
        if group is None and send_id is None and continues(previous, event):
            group = previous
        if group is None:
            group = []
            groups.append(group)
            if send_id is not None:
                by_call[send_id] = group
        group.append(event)
        previous = group
    return groups


def message_of_group(group, tools, epoch):
    first = group[0]
    recipients = [e.get("to") for e in group]
    broadcast = tools.get(first.get("send_id")) == "broadcast" or len(recipients) > 1
    at = event_time(first, epoch)
    return Message(id=str(first.get("message_id", first.get("seq"))), sender=first.get("agent"),
                   to="all" if broadcast else str(recipients[0]), content=first["content"], timestamp=at,
                   timestamp_iso=first.get("timestamp_iso") or iso_of(at), recipients=recipients)


def message_of_request(event, epoch):
    """A team request or response, a message of its own kind."""
    at = event_time(event, epoch)
    if event.get("kind") == "request":
        note = f"request {event.get('request_kind') or ''}".strip()
    else:
        note = f"response to request {event.get('request_id')}"
    return Message(id=str(event.get("seq")), sender=event.get("agent"), to=str(event.get("to")),
                   content=event["content"], timestamp=at, timestamp_iso=event.get("timestamp_iso") or iso_of(at),
                   kind=event["kind"], note=note, recipients=[event.get("to")])


def in_time_order(messages):
    """Sorted by time; a message with no time keeps its place after the
    timed ones, and a tie keeps the record's order."""
    indexed = list(enumerate(messages))
    indexed.sort(key=lambda item: (item[1].timestamp is None, item[1].timestamp or 0.0, item[0]))
    return [message for _, message in indexed]


def conversation_from_events(events):
    epoch = run_epoch(events)
    tools = {e.get("seq"): e.get("tool") for e in events if e.get("kind") == "tool"}
    messages = [message_of_group(g, tools, epoch) for g in delivery_groups(events)]
    requests = [message_of_request(e, epoch) for e in events
                if e.get("kind") in ("request", "response") and isinstance(e.get("content"), str)]
    return Conversation(in_time_order(messages + requests), "events.jsonl", includes_requests=bool(requests))


def conversation_from_store(store):
    """conversation_store.json, coordbench's (with message_ids) or the
    paper era's (id, sender_id, recipient_id only)."""
    messages = []
    for row in store["messages"]:
        if not isinstance(row, dict) or not isinstance(row.get("content"), str):
            continue
        ids = row.get("message_ids") or [row.get("id")]
        to = row.get("to") or row.get("recipient_id")
        at = row.get("sent_at") if isinstance(row.get("sent_at"), (int, float)) else epoch_of_iso(row.get("timestamp"))
        messages.append(Message(id=str(ids[0]), sender=row.get("from") or row.get("sender_id"),
                                to="all" if to == "broadcast" else str(to), content=row["content"], timestamp=at,
                                timestamp_iso=row.get("timestamp"), recipients=row.get("recipients") or [to]))
    return Conversation(in_time_order(messages), "conversation_store.json")


def conversation_from_cooperbench(entries):
    """CooperBench's own conversation.json: one entry per delivery, ids by
    position since it has none."""
    messages, seen = [], {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("message"), str):
            continue
        key = (entry.get("from"), entry["message"], entry.get("timestamp"))
        if key in seen:
            seen[key].recipients.append(entry.get("to"))
            seen[key].to = "all"
            continue
        at = entry.get("timestamp") if isinstance(entry.get("timestamp"), (int, float)) else None
        seen[key] = Message(id=str(len(messages) + 1), sender=entry.get("from"), to=str(entry.get("to")),
                            content=entry["message"], timestamp=at, timestamp_iso=iso_of(at),
                            recipients=[entry.get("to")])
        messages.append(seen[key])
    return Conversation(in_time_order(messages), "conversation.json")


def text_recorded(events):
    """Whether the events hold the messages' text: every delivery carries
    its content (the pilots' did not)."""
    deliveries = [e for e in events if e.get("kind") == "message"]
    return all("content" in e for e in deliveries)


def conversation_of(pair):
    """The pair's conversation from the richest record there is: the
    events (which also hold a team's requests), the message store, then
    CooperBench's conversation.json. A record that kept no text yields
    no conversation and says so, so silence is never read into it."""
    events = pair.events
    if events is not None and text_recorded(events):
        return conversation_from_events(events)
    store = read_json(pair.dir / "conversation_store.json")
    if isinstance(store, dict) and isinstance(store.get("messages"), list):
        return conversation_from_store(store)
    entries = read_json(pair.dir / "conversation.json")
    if isinstance(entries, list):
        return conversation_from_cooperbench(entries)
    if events is not None:
        return Conversation([], None, recorded=False, reason=NOT_RECORDED)
    return Conversation([], None, recorded=False, reason="no record of the messages (no events.jsonl, "
                                                          "conversation_store.json or conversation.json)")


# Rendering it for a judge.

def agents_line(pair):
    parts = []
    for agent, features in agent_features(pair).items():
        if features:
            noun = "feature" if len(features) == 1 else "features"
            parts.append(f"{agent} implements {noun} {', '.join(map(str, features))}")
        else:
            parts.append(agent)
    return "; ".join(parts) + "." if parts else "The agents are not recorded."


def conversation_header(pair, conversation):
    result = pair.result
    return [f"Conversation of a coordbench run: repository {result.get('repo')}, task {result.get('task_id')}. "
            + agents_line(pair),
            f"{len(conversation.messages)} messages in time order, each as [message id] time sender -> "
            "recipient (all for a broadcast): content.", ""]


def message_line(message):
    note = f"({message.note}) " if message.note else ""
    return f"[{message.id}] {message.timestamp_iso or '-'} {message.sender} -> {message.to}: {note}{message.content}"


def bounded(text, limit=CONVERSATION_LIMIT, keep=CONVERSATION_KEEP):
    """The text whole, or its first and last `keep` characters with a line
    saying how much was left out between them; and whether it was cut."""
    if len(text) <= limit:
        return text, False
    left_out = len(text) - 2 * keep
    note = (f"\n[coordbench: this conversation is {len(text):,} characters long; the {left_out:,} characters "
            f"after the first {keep:,} are left out here, and the last {keep:,} follow.]\n")
    return text[:keep] + note + text[-keep:], True


def render_conversation(pair, conversation, limit=CONVERSATION_LIMIT):
    lines = conversation_header(pair, conversation) + [message_line(m) for m in conversation.messages]
    text = "\n".join(lines) + "\n"
    return bounded(text) if limit else (text, False)


# Checking quotes.

CURLY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
                       "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"'})
RENDERED_PREFIX = re.compile(r"^\[[^\]\s]+\]\s+\S+\s+\S+\s+->\s+\S+:\s+")
WRAPPING = {'"': '"', "'": "'", "`": "`"}


def normal(text):
    """Whitespace collapsed and curly quotes folded to ASCII."""
    return " ".join(str(text).translate(CURLY).split())


def clean_quote(quote):
    """The quote as it is looked for: normalised, one pair of wrapping
    quote marks taken off, and a copied "[id] time from -> to:" prefix
    dropped, since the check is on message content alone."""
    text = normal(quote)
    if len(text) >= 2 and WRAPPING.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return RENDERED_PREFIX.sub("", text)


def quotes_of(evidence):
    """A reply's evidence as (quote, claimed message id) pairs: a list of
    strings, or of objects with a quote."""
    found = []
    for item in evidence if isinstance(evidence, list) else []:
        if isinstance(item, str):
            found.append((item, None))
        elif isinstance(item, dict) and isinstance(item.get("quote") or item.get("text"), str):
            found.append((item.get("quote") or item.get("text"), item.get("message_id")))
    return found


def messages_holding(quote, messages):
    text = clean_quote(quote)
    if not text:
        return []
    return [m for m in messages if text in normal(m.content)]


def verify_quotes(quotes, messages):
    """Each quote with where it was found. A quote verifies when it lies
    inside one message's content; fresh says it counts towards the two a
    label needs, which a quote repeated in the evidence does only when it
    occurs in another message too (the same words said twice are two
    quotes, the same quote listed twice is one)."""
    checks, used = [], set()
    for quote, _claimed in quotes:
        holders = messages_holding(quote, messages)
        key = clean_quote(quote)
        fresh = next((m for m in holders if (key, m.id) not in used), None)
        if fresh is not None:
            used.add((key, fresh.id))
        chosen = fresh or (holders[0] if holders else None)
        checks.append({"quote": quote, "verified": bool(holders), "fresh": fresh is not None,
                       "message_id": chosen.id if chosen else None,
                       "sender": chosen.sender if chosen else None,
                       "found_in": [m.id for m in holders][:LISTED_KEPT]})
    return checks


def fresh_count(checks):
    return sum(1 for check in checks if check["verified"] and check["fresh"])


# Backends.

def judge_environment(config_dir, home, environ=None):
    """The judge's claude environment: what it needs to run and reach the
    API, the drivers' token under the name Claude Code reads, and a
    throwaway config directory and home. ANTHROPIC_API_KEY never passes."""
    environ = os.environ if environ is None else environ
    env = {name: environ[name] for name in PASSED_THROUGH if name in environ}
    token = next((environ[name] for name in TOKEN_NAMES if environ.get(name)), None)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    env.update({"CLAUDE_CONFIG_DIR": str(config_dir), "HOME": str(home),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    return env


def claude_command(binary, model, system):
    return [binary, "--print", "--output-format", "json", "--model", model, "--max-turns", "1",
            "--system-prompt", system, "--tools", "", "--strict-mcp-config", "--mcp-config", EMPTY_MCP,
            "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence"]


class ClaudeCli:
    """claude --print, one turn, no tools, in an empty directory."""

    name = "claude-cli"

    def __init__(self, model, timeout=CALL_TIMEOUT, binary="claude"):
        self.model, self.timeout, self.binary = model, timeout, binary
        self._version = None

    def version(self):
        if self._version is None:
            try:
                done = subprocess.run([self.binary, "--version"], capture_output=True, text=True, timeout=60,
                                      stdin=subprocess.DEVNULL)
                self._version = done.stdout.strip() or None
            except (OSError, subprocess.SubprocessError):
                self._version = None
        return self._version

    def describe(self):
        return {"backend": self.name, "model": self.model, "cli_version": self.version(),
                "temperature": None, "temperature_note": "the CLI exposes no temperature"}

    def complete(self, system, user):
        with tempfile.TemporaryDirectory(prefix="coordbench-judge-") as scratch:
            work, config, home = (Path(scratch) / name for name in ("work", "config", "home"))
            for directory in (work, config, home):
                directory.mkdir()
            try:
                done = subprocess.run(claude_command(self.binary, self.model, system), input=user,
                                      capture_output=True, text=True, cwd=work, timeout=self.timeout,
                                      env=judge_environment(config, home))
            except (OSError, subprocess.SubprocessError) as problem:
                raise JudgeError(f"claude did not run: {problem}") from None
        return claude_reply(done)


def claude_reply(done):
    """The text and the call's record from claude's JSON result."""
    try:
        result = json.loads(done.stdout)
    except ValueError:
        raise JudgeError(f"claude exited {done.returncode} without a JSON result: "
                         f"{(done.stderr or done.stdout)[-500:]}") from None
    if done.returncode != 0 or result.get("is_error") or not isinstance(result.get("result"), str):
        raise JudgeError(f"claude failed ({result.get('subtype')}): {str(result.get('result'))[-500:]}")
    usage = result.get("modelUsage")
    return result["result"], {"cost_usd": result.get("total_cost_usd"), "usage": result.get("usage"),
                              "served_models": sorted(usage) if isinstance(usage, dict) else None,
                              "duration_ms": result.get("duration_ms")}


def shown_url(url):
    """The endpoint as recorded, with no user or password in it."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = (f"[{host}]" if ":" in host else host) + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def opener_for(url):
    """A local endpoint is reached directly, past any proxy."""
    if urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class OpenAICompatible:
    """POST <base>/chat/completions with the standard library."""

    name = "openai-compatible"

    def __init__(self, model, url, key=None, timeout=CALL_TIMEOUT, temperature=None, reasoning_effort=None):
        self.model, self.url, self.key, self.timeout = model, url.rstrip("/"), key, timeout
        self.reasoning = bool(REASONING_MODEL.match(model))
        self.temperature = None if self.reasoning else (0 if temperature is None else temperature)
        self.reasoning_effort = reasoning_effort if self.reasoning else None

    def describe(self):
        record = {"backend": self.name, "model": self.model, "endpoint": shown_url(self.url),
                  "temperature": self.temperature, "reasoning_effort": self.reasoning_effort}
        if self.reasoning:
            record["temperature_note"] = "a reasoning model takes only its default temperature, so none is sent"
        return record

    def body(self, system, user):
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def complete(self, system, user):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = urllib.request.Request(f"{self.url}/chat/completions", headers=headers,
                                         data=json.dumps(self.body(system, user)).encode("utf-8"))
        try:
            with opener_for(self.url).open(request, timeout=self.timeout) as response:
                answer = json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as problem:
            raise JudgeError(f"the endpoint failed: {problem}") from None
        try:
            text = answer["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise JudgeError(f"the endpoint's answer has no message: {str(answer)[:500]}") from None
        return str(text), {"usage": answer.get("usage"), "served_models": [answer.get("model")]}


def make_backend(args):
    model = args.model or os.environ.get(JUDGE_MODEL) or DEFAULT_MODELS[args.backend]
    if args.backend == "claude-cli":
        return ClaudeCli(model, args.timeout, args.claude_bin)
    url = args.base_url or os.environ.get(JUDGE_URL)
    if not url:
        raise SystemExit(f"judge: the openai-compatible backend needs --base-url or {JUDGE_URL}")
    return OpenAICompatible(model, url, os.environ.get(JUDGE_KEY), args.timeout, args.temperature,
                            args.reasoning_effort)


# Asking.

@dataclass
class Answer:
    parsed: dict
    raws: list
    info: dict


FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_reply(text):
    """The reply's JSON object, from the whole text, a fenced block, or the
    span from its first brace to its last; None when there is none."""
    text = FENCE.sub("", text.strip())
    for candidate in (text, text[text.find("{"): text.rfind("}") + 1]):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def ask(backend, system, user):
    """One call, and one retry when the reply is not JSON."""
    raws, info = [], {}
    for _ in range(2):
        text, info = backend.complete(system, user)
        raws.append(text)
        parsed = parse_reply(text)
        if parsed is not None:
            return Answer(parsed, raws, info)
    return Answer(None, raws, info)


def prompt_text(file_name):
    return (PROMPTS / file_name).read_text(encoding="utf-8")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def call_record(backend, variant, file_name, system, user, answer, truncated):
    return {**backend.describe(), "prompt_variant": variant, "prompt_file": f"prompts/{file_name}",
            "prompt_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(), "created_at": now(),
            "attempts": len(answer.raws), "raw": answer.raws,
            "parse_error": None if answer.parsed is not None else "the reply was not a JSON object after one retry",
            "input_chars": len(user), "input_truncated": truncated, **answer.info}


# Communication errors (App F).

CODE = re.compile(r"\bC([1-5])([ab])?\b", re.IGNORECASE)


def error_code(category):
    """App F's code in a category as given, "C4A", "c3a: ...", or a name."""
    text = str(category or "")
    match = CODE.search(text)
    if match:
        return f"C{match.group(1)}{(match.group(2) or '').lower()}"
    wanted = normal(text).lower()
    return next((code for code, name in ERROR_NAMES.items() if normal(name).lower() == wanted), None)


def proposed_issue(issue):
    if not isinstance(issue, dict):
        return None
    category = issue.get("category") or issue.get("id") or issue.get("code")
    if not category:
        return None
    return {"category": str(category), "code": error_code(category), "description": issue.get("description")}


def judge_verdict(proposed, checks, codes, variant):
    """Whether the proposed issue stands, and why not when it does not."""
    if proposed is None:
        return False, None
    if proposed["code"] not in codes:
        return False, f"category {proposed['category']!r} is not in {variant}'s taxonomy"
    found = fresh_count(checks)
    if found < QUOTES_NEEDED:
        return False, (f"{found} evidence quote(s) found verbatim in a message; App F needs at least "
                       f"{QUOTES_NEEDED}")
    return True, None


CODE_HEURISTICS = {"C1a": "unanswered_questions", "C1b": "ignored_questions", "C3a": "contradictions",
                   "C4a": "near_duplicates", "C4b": "near_duplicates"}


def cross_check(code, heuristics):
    """Whether the free check for the accepted code found a candidate."""
    key = CODE_HEURISTICS.get(code)
    if key is None or key not in (heuristics or {}):
        return {"heuristic": key, "candidates": None, "agrees": None}
    found = heuristics[key]
    if code == "C4b":
        found = [pair for pair in found if pair[3]]
    return {"heuristic": key, "candidates": len(found), "agrees": bool(found)}


def commerrors_section(variant, conversation, answer, heuristics=None):
    parsed = answer.parsed
    checks = verify_quotes(quotes_of(parsed.get("evidence")), conversation.messages)
    proposed = proposed_issue(parsed.get("issue"))
    accepted, reason = judge_verdict(proposed, checks, COMMERROR_VARIANTS[variant]["codes"], variant)
    code = proposed["code"] if accepted else None
    return {"prompt_variant": variant, "message_count": len(conversation.messages),
            "conversation_source": conversation.source, "includes_requests": conversation.includes_requests,
            "issue": {"code": code, "group": ERROR_GROUPS[code], "description": proposed["description"]}
            if accepted else None,
            "accepted": accepted, "group": ERROR_GROUPS[code] if accepted else None,
            "proposed": proposed, "rejected_reason": reason,
            "evidence": [c["quote"] for c in checks], "verified": [c["verified"] for c in checks],
            "evidence_message_ids": [c["message_id"] for c in checks], "evidence_checks": checks,
            "cross_check": cross_check(code, heuristics) if accepted else None}


# Symptoms (App G, Table 1).

# CooperBench 63b9d44's test-file rule (eval/sandbox.py:676-714), so the
# patches the judge reads are the ones the scorer merged.
TEST_DIR = re.compile(r"/(tests?|__tests__|spec|testdata)/")
TEST_FILE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test\.[A-Za-z0-9]+|[^/]+\.(test|spec)\.[A-Za-z0-9]+"
                       r"|[^/]*Test[s]?\.(java|kt|cs|scala)|tests?\.py)$")


def is_test_path(header):
    paths = re.findall(r"[ab]/(\S+)", header)
    return any(TEST_DIR.search("/" + p) or TEST_FILE.search(p) for p in paths)


def filter_test_files(patch):
    if not patch:
        return patch
    kept, skipping = [], False
    for line in patch.split("\n"):
        if line.startswith("diff --git"):
            skipping = is_test_path(line)
        if not skipping:
            kept.append(line)
    text = "\n".join(kept)
    return text if not text or text.endswith("\n") else text + "\n"


def symptom_scope(pair):
    """(the bases the pair failed under, why it is out of scope): every
    scored multi-agent pair that failed under 63b9d44's verdict or the
    paper-era cascade's, harness errors excluded and counted apart."""
    if not is_multi_agent(pair):
        return [], "a single agent: the symptoms are of multi-agent failures"
    if pair.result.get("error"):
        return [], f"harness error: {pair.result['error']}"
    evaluation = pair.evaluation
    if evaluation is None:
        return [], "not scored (no eval.json)"
    if evaluation.get("error"):
        return [], f"harness error in scoring: {evaluation['error']}"
    failed = [] if evaluation.get("both_passed") else ["63b9d44"]
    cascade = evaluation.get("cascade") if isinstance(evaluation.get("cascade"), dict) else {}
    if cascade.get("ran") and cascade.get("verdict_paper_era") is False:
        failed.append("paper_era")
    return failed, (None if failed else "passed under every verdict recorded")


FAILURE_LINE = re.compile(r"FAILED|Error|Traceback")


def feature_results(evaluation):
    """eval.json's feature1, feature2, ... in order."""
    found, number = [], 1
    while isinstance(evaluation.get(f"feature{number}"), dict):
        found.append(evaluation[f"feature{number}"])
        number += 1
    return found


def test_lines(output):
    """Every line of a test output matching FAILED, Error or Traceback (up
    to MATCHED_LINES_KEPT), and the output's last TEST_TAIL characters."""
    matched = [line for line in output.splitlines() if FAILURE_LINE.search(line)]
    extra = len(matched) - MATCHED_LINES_KEPT
    kept = matched[:MATCHED_LINES_KEPT] + ([f"[coordbench: {extra} more matching lines left out]"] if extra > 0 else [])
    return kept, output[-TEST_TAIL:]


def outcome_status_lines(evaluation):
    merge = evaluation.get("merge") or {}
    lines = [f"apply_status: {json.dumps(evaluation.get('apply_status'))}",
             f"merge: status {merge.get('status')}, strategy {merge.get('strategy')}",
             f"both_passed (CooperBench 63b9d44): {evaluation.get('both_passed')}"]
    cascade = evaluation.get("cascade") if isinstance(evaluation.get("cascade"), dict) else {}
    if cascade.get("ran"):
        lines.append(f"paper-era cascade (coordbench): route {cascade.get('route')}, verdict "
                     f"{cascade.get('verdict_paper_era')}, naive conflict {cascade.get('has_naive_merge_conflict')}")
    return lines


def outcome_text(evaluation, full=False):
    """The outcome evidence as the judge reads it; full keeps each test
    output whole, the text quotes are checked against."""
    lines = outcome_status_lines(evaluation)
    for feature in feature_results(evaluation):
        output = str(feature.get("test_output") or "")
        lines.append(f"feature {feature.get('feature_id')}: passed {feature.get('passed')}, tests_passed "
                     f"{feature.get('tests_passed')}, tests_failed {feature.get('tests_failed')}")
        if full:
            lines += ["test output:", output]
            continue
        matched, tail = test_lines(output)
        lines += [f"lines matching FAILED, Error or Traceback ({len(matched)}):"] + matched
        lines += [f"last {TEST_TAIL:,} characters of the test output:", tail]
    return "\n".join(lines) + "\n"


def deciding_artifact(pair, basis="verdict_63b9d44"):
    """(name, text) of the merged artifact the verdict was read from: the
    cascade's record when there is one, else from eval.json's merge (a
    clean merge's diff, else agent1's patch alone, as 63b9d44's
    identical-patch and solo-agent1 routes read it)."""
    evaluation = pair.evaluation or {}
    cascade = evaluation.get("cascade") if isinstance(evaluation.get("cascade"), dict) else {}
    name = (cascade.get("deciding_artifact") or {}).get(basis)
    if name is None and basis == "verdict_63b9d44":
        name = fallback_artifact(pair, evaluation)
    if name == "eval.json merge.diff":
        return name, str((evaluation.get("merge") or {}).get("diff") or "")
    text = read_text(pair.dir / name) if name else None
    return (name, text) if text is not None else (None, None)


def fallback_artifact(pair, evaluation):
    merge = evaluation.get("merge") or {}
    if merge.get("status") == "clean":
        return "merge_naive.diff" if (pair.dir / "merge_naive.diff").is_file() else "eval.json merge.diff"
    first = next(iter(agent_features(pair)), None)
    return patch_file(pair, first) if first else None


def conflict_regions(pair):
    """The naive merge's conflict blocks, three lines of context before
    each, from merge_naive.diff when the merge conflicted."""
    text = read_text(pair.dir / "merge_naive.diff") or ""
    lines, kept, inside = text.splitlines(), [], False
    for number, line in enumerate(lines):
        if "<<<<<<<" in line:
            inside = True
            kept += lines[max(0, number - 3):number]
        if inside:
            kept.append(line)
        if ">>>>>>>" in line:
            inside = False
            kept.append("")
    return "\n".join(kept)


def fence(text, language=""):
    """A Markdown fence that no run of backticks in the text can close."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    marks = "`" * max(3, longest + 1)
    return f"{marks}{language}\n{text.rstrip()}\n{marks}"


def capped(text, limit, label, cut):
    if len(text) <= limit:
        return text
    cut.append(label)
    return text[:limit] + f"\n[coordbench: {len(text) - limit:,} more characters of {label} left out]\n"


def patch_evidence(pair, failed_under, cut):
    """The patch section of the symptom input and every text a patch quote
    may come from, by name."""
    sources, parts = {}, ["# Patch evidence", ""]
    for agent, raw in agent_patches(pair).items():
        name = patch_file(pair, agent)
        filtered = filter_test_files(raw)
        sources[name] = filtered
        parts += [f"## {agent}'s patch ({name}, test files left out as CooperBench 63b9d44 does)",
                  fence(capped(filtered, PATCH_LIMIT, name, cut), "diff"), ""]
    bases = ["verdict_63b9d44"] + (["verdict_paper_era"] if "paper_era" in failed_under else [])
    for basis in bases:
        name, text = deciding_artifact(pair, basis)
        if name and name not in sources:
            sources[name] = text
            parts += [f"## The merged artifact that decided {basis} ({name})",
                      fence(capped(text, PATCH_LIMIT, name, cut), "diff"), ""]
    regions = conflict_regions(pair)
    if regions:
        sources["conflict regions"] = regions
        parts += ["## Conflict regions of the naive merge",
                  fence(capped(regions, CONFLICT_LIMIT, "the conflict regions", cut), "diff"), ""]
    return parts, sources


def symptom_input(pair, conversation, failed_under):
    """The user turn of the symptom judge and what its quotes are checked
    against: the outcome, the patches and the merged artifacts, and the
    conversation."""
    cut = []
    rendered, truncated = render_conversation(pair, conversation)
    if truncated:
        cut.append("the conversation")
    patch_parts, patch_sources = patch_evidence(pair, failed_under, cut)
    parts = [f"# Pair: {pair.result.get('repo')} task {pair.result.get('task_id')}. {agents_line(pair)}",
             f"Failed under: {', '.join(failed_under)}.", "", "# Outcome evidence",
             outcome_text(pair.evaluation), *patch_parts, "# Conversation evidence", rendered]
    sources = {"patch": patch_sources, "outcome": outcome_text(pair.evaluation, full=True)}
    inputs = {"patches": sorted(patch_sources), "truncated": cut,
              "deciding_artifact": deciding_artifact(pair)[0]}
    return "\n".join(parts), sources, inputs


def code_view(patch):
    """A diff's lines without their +, - or space, as a quote of code reads."""
    lines = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---", "diff --git", "index ", "@@")):
            continue
        lines.append(line[1:] if line[:1] in ("+", "-", " ") else line)
    return "\n".join(lines)


def find_in_texts(quote, texts):
    text = clean_quote(quote)
    if not text:
        return None
    for name, body in texts.items():
        if text in normal(body) or text in normal(code_view(body)):
            return name
    return None


def symptom_evidence(evidence, conversation, sources):
    """Each quote checked against the source it names, and that source only."""
    checked = []
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
            continue
        source, quote = str(item.get("source") or "").lower(), item["quote"]
        entry = {"source": source, "quote": quote, "message_id": None, "claimed_message_id": item.get("message_id"),
                 "verified": False, "found_in": None}
        if source == "conversation":
            holders = messages_holding(quote, conversation.messages)
            entry.update(verified=bool(holders), message_id=holders[0].id if holders else None,
                         found_in=holders[0].id if holders else None)
        elif source == "patch":
            entry["found_in"] = find_in_texts(quote, sources["patch"])
            entry["verified"] = entry["found_in"] is not None
        elif source == "outcome":
            entry["verified"] = clean_quote(quote) in normal(sources["outcome"]) and bool(clean_quote(quote))
            entry["found_in"] = "outcome" if entry["verified"] else None
        checked.append(entry)
    return checked


def symptom_name(value, names):
    """A symptom name as Table 1 writes it, matched without case or
    punctuation; None for anything else."""
    wanted = re.sub(r"[^a-z]", "", str(value or "").lower())
    return next((name for name in names if re.sub(r"[^a-z]", "", name.lower()) == wanted), None) if wanted else None


def proposed_symptoms(parsed):
    return {"level": parsed.get("level"), "primary_symptom": parsed.get("primary_symptom", parsed.get("primary")),
            "secondary_symptoms": parsed.get("secondary_symptoms", parsed.get("secondary"))}


def symptom_labels(proposed, names):
    """The level, primary and secondary names, and the names given that
    are none of the variant's."""
    invalid = []
    primary = symptom_name(proposed["primary_symptom"], names)
    if proposed["primary_symptom"] and primary is None:
        invalid.append(str(proposed["primary_symptom"]))
    secondary = []
    for value in as_list(proposed["secondary_symptoms"]):
        name = symptom_name(value, names)
        if name is None:
            invalid.append(str(value))
        elif name != primary and name not in secondary:
            secondary.append(name)
    level = str(proposed["level"] or "").strip().lower()
    return (level if level in LEVELS else None), primary, secondary, invalid


def coerced_level(level, primary, conversation_verified):
    """App G's rule made mechanical: coordination needs a verified
    conversation quote, and an implementation failure has no symptom."""
    if level == "coordination" and not conversation_verified:
        return "implementation", "no conversation quote verified, so the level defaults to implementation (App G)"
    if level is None:
        if primary and conversation_verified:
            return "coordination", "no valid level given; a symptom with a verified conversation quote is coordination"
        return "implementation", "no valid level given, and no symptom with a verified conversation quote"
    return level, None


def symptoms_section(variant, conversation, answer, sources, failed_under, inputs):
    names = SYMPTOM_VARIANTS[variant]["names"]
    proposed = proposed_symptoms(answer.parsed)
    level, primary, secondary, invalid = symptom_labels(proposed, names)
    evidence = symptom_evidence(answer.parsed.get("evidence"), conversation, sources)
    talked = any(e["source"] == "conversation" and e["verified"] for e in evidence)
    level, reason = coerced_level(level, primary, talked)
    if level == "implementation":
        primary, secondary = None, []
    return {"prompt_variant": variant, "rubric": SYMPTOM_RUBRIC, "decided_by": "judge", "level": level,
            "primary": primary, "secondary": secondary, "family": FAMILIES.get(primary),
            "secondary_families": [FAMILIES.get(name) for name in secondary], "evidence": evidence,
            "proposed": proposed, "coerced": reason is not None, "coercion_reason": reason, "invalid_names": invalid,
            "failed_under": failed_under, "inputs": inputs}


def silent_symptoms(variant, failed_under):
    """A failed pair with no message has no conversation evidence, so App
    G's rule decides it without a judge."""
    return {"prompt_variant": variant, "rubric": SYMPTOM_RUBRIC, "decided_by": "rule", "level": "implementation",
            "primary": None, "secondary": [], "family": None, "secondary_families": [], "evidence": [],
            "proposed": None, "coerced": True,
            "coercion_reason": "no messages, so no conversation evidence: implementation (App G)",
            "invalid_names": [], "failed_under": failed_under, "inputs": None}


# Speech acts (Fig 5c).

def speech_act_labels(parsed):
    """The reply as {id: label}: a mapping, {"labels": mapping}, or a list
    of {message_id, label}."""
    labels = parsed.get("labels", parsed)
    if isinstance(labels, list):
        labels = {str(i.get("message_id")): i.get("label") for i in labels if isinstance(i, dict)}
    return labels if isinstance(labels, dict) else {}


def speech_act_name(label):
    wanted = str(label or "").strip().lower()
    return next((act for act in SPEECH_ACTS if act.lower() == wanted), None)


def speech_acts_section(conversation, answer):
    """(the labels by message id, what was left out): ids the conversation
    lacks and labels outside the five are dropped, and every message the
    reply skipped is named."""
    ids = [m.id for m in conversation.messages]
    labels, invalid = {}, []
    for message_id, label in speech_act_labels(answer.parsed).items():
        act = speech_act_name(label)
        if str(message_id) in ids and act:
            labels[str(message_id)] = act
        else:
            invalid.append([str(message_id), label])
    return labels, {"invalid": invalid[:LISTED_KEPT], "missing": [i for i in ids if i not in labels][:LISTED_KEPT]}


# Emergent behaviours (Sec 6.4) and the git setting's judged failures.

PATH_OR_LINES = re.compile(r"[\w./-]+\.\w{1,5}\b|\blines?\s+\d+|\bL\d+\b|:\d+")


def tag_present(tag, proposed, checks):
    """The tag's rule over its verified quotes (EMERGENT_RULES)."""
    verified = [c for c in checks if c["verified"] and c["fresh"]]
    senders = {c["sender"] for c in verified}
    if not proposed:
        return False, None
    if tag in EMERGENT_TAGS and len(senders) < 2:
        return False, "no verified quote from each of two agents"
    if tag == "resource_division" and not any(PATH_OR_LINES.search(clean_quote(c["quote"])) for c in verified):
        return False, "no verified quote names a file or a line range"
    if tag == "blame" and not verified:
        return False, "no verified quote"
    if tag == "sagas" and len(verified) < 2:
        return False, "fewer than two verified quotes"
    return True, None


def tag_entry(tag, reply, conversation):
    entry = reply if isinstance(reply, dict) else {}
    proposed = bool(entry.get("present"))
    checks = verify_quotes(quotes_of(entry.get("evidence")), conversation.messages)
    present, reason = tag_present(tag, proposed, checks)
    return {"present": present, "proposed": proposed, "rejected_reason": reason, "rule": EMERGENT_RULES[tag],
            "evidence": [{k: c[k] for k in ("quote", "message_id", "sender", "verified")} for c in checks]}


def emergent_sections(conversation, answer):
    parsed = answer.parsed
    emergent = {tag: tag_entry(tag, parsed.get(tag), conversation) for tag in EMERGENT_TAGS}
    git = {tag: {**tag_entry(tag, parsed.get(tag), conversation), "blog_name": GIT_BLOG_NAMES[tag]}
           for tag in GIT_JUDGE_TAGS}
    return emergent, git


# Cause drafts (annotate.py's optional step).

def cause_draft(answer):
    cause = str(answer.parsed.get("cause") or "").strip().capitalize() if answer.parsed else ""
    return {"draft_cause": cause if cause in CAUSES else None, "rationale": (answer.parsed or {}).get("rationale"),
            "invalid": None if cause in CAUSES else cause or None}


# The free cross-checks.

STOPWORDS = frozenset((
    "the and for you your are was were will with that this have has had not but can all any its our out from "
    "into they them then than there what when where which who why how would could should just also about "
    "been being does did let now here yes okay sure please thanks great good like make made one two get got "
    "use using used see need needs want each some more most very much well only still both such same other "
    "these those their itself myself yourself we're i'm i've i'll you're it's that's let's".split()))
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_']{2,}")
STATUS_LIKE = re.compile(r"\b(status|update|done|completed|progress)\b", re.IGNORECASE)


def content_words(text):
    return {w.lower() for w in WORD.findall(normal(text))} - STOPWORDS


def addressees(message, senders):
    if message.to == "all":
        return [s for s in senders if s != message.sender]
    return [message.to]


def question_checks(messages, senders):
    """(C1a candidates, C1b candidates): a message with '?' whose
    addressees send nothing later, or whose addressees' next messages
    share no content word with it."""
    unanswered, ignored = [], []
    for index, message in enumerate(messages):
        if "?" not in message.content:
            continue
        later = messages[index + 1:]
        replies = [next((m for m in later if m.sender == a), None) for a in addressees(message, senders)]
        replies = [r for r in replies if r is not None]
        if not replies:
            unanswered.append(message.id)
        elif all(not content_words(message.content) & content_words(r.content) for r in replies):
            ignored.append([message.id, replies[0].id])
    return unanswered[:LISTED_KEPT], ignored[:LISTED_KEPT]


def similarity(first, second):
    matcher = difflib.SequenceMatcher(None, first, second)
    if matcher.real_quick_ratio() < NEAR_DUPLICATE or matcher.quick_ratio() < NEAR_DUPLICATE:
        return 0.0
    return matcher.ratio()


def near_duplicates(messages):
    """Same-sender pairs whose texts have a difflib ratio of at least 0.9,
    each marked status-like when both read as status updates."""
    found = []
    for index, first in enumerate(messages):
        for second in messages[index + 1:]:
            if second.sender != first.sender:
                continue
            ratio = similarity(normal(first.content), normal(second.content))
            if ratio >= NEAR_DUPLICATE:
                status = bool(STATUS_LIKE.search(first.content) and STATUS_LIKE.search(second.content))
                found.append([first.id, second.id, round(ratio, 3), status])
    return found[:LISTED_KEPT]


PLAN_WORDS = re.compile(r"\bI will\b|\bI'll\b|\bplan\b", re.IGNORECASE)
UPDATE_WORDS = re.compile(r"\b(done|status|update)\b", re.IGNORECASE)
ACK_WORDS = re.compile(r"\b(thanks|thank you|ok|okay|ack|acknowledged)\b", re.IGNORECASE)


def keyword_act(message, opening, answering):
    """The keyword baseline's label, rules in this order: Plan for a
    sender's first message saying 'I will' or 'plan', Question for a '?',
    Answer for a reply to a question put to the sender, Update for 'done',
    'status' or 'update', Ack for 'thanks', 'ok' or 'ack'; None otherwise."""
    text = message.content.translate(CURLY)
    if opening and PLAN_WORDS.search(text):
        return "Plan"
    if "?" in text:
        return "Question"
    if answering:
        return "Answer"
    if UPDATE_WORDS.search(text):
        return "Update"
    if ACK_WORDS.search(text):
        return "Ack"
    return None


def keyword_speech_acts(messages, senders):
    labels, seen, waiting = {}, set(), {}
    for message in messages:
        opening = message.sender not in seen
        labels[message.id] = keyword_act(message, opening, bool(waiting.get(message.sender)))
        seen.add(message.sender)
        waiting[message.sender] = False
        if "?" in message.content:
            for addressee in addressees(message, senders):
                waiting[addressee] = True
    return labels


FILE_PATH = re.compile(r"(?<![\w./-])[\w./-]*[\w-]\.(?:py|pyi|ts|tsx|js|jsx|go|rs|java|kt|rb|c|h|cc|cpp|hpp|cs|"
                       r"toml|json|yaml|yml|md|rst|cfg|ini|txt|sh)(?![\w-])")
LINE_RANGE = re.compile(r"\blines?\s+\d+\s*(?:-|to)\s*\d+|\bL\d+\s*-\s*L?\d+|:\d+\s*-\s*\d+", re.IGNORECASE)
NEGATION = re.compile(r"\bnot\b|\bwon't\b|\bwill not\b|\bavoid\b|\bstay out\b|\bstay away\b", re.IGNORECASE)
OPTIONS = re.compile(r"\(1\).*\(2\)|\boption\s*(?:1|a|one)\b.*\boption\s*(?:2|b|two)\b", re.IGNORECASE | re.DOTALL)
CHOICE = re.compile(r"\boption\s*(?:\d|[ab]|one|two)\b|\(\d\)|\b(?:first|second) option\b|\bgo with\b",
                    re.IGNORECASE)


def basenames(text):
    return {Path(p).name for p in FILE_PATH.findall(text.translate(CURLY))}


def resource_division_candidates(messages):
    """A message naming a file and a line range, answered by a partner who
    names the same file with a not, a won't or an avoid (row 95)."""
    found = []
    for index, message in enumerate(messages):
        files = basenames(message.content)
        if not files or not LINE_RANGE.search(message.content):
            continue
        for reply in messages[index + 1:]:
            shared = files & basenames(reply.content)
            if reply.sender != message.sender and shared and NEGATION.search(reply.content.translate(CURLY)):
                found.append([message.id, reply.id, sorted(shared)[0]])
                break
    return found[:LISTED_KEPT]


def negotiation_candidates(messages):
    """A message setting out options (1) and (2), or Option 1 and 2, with a
    question, whose partner's next message names one (row 96)."""
    found = []
    for index, message in enumerate(messages):
        if "?" not in message.content or not OPTIONS.search(message.content):
            continue
        reply = next((m for m in messages[index + 1:] if m.sender != message.sender), None)
        if reply is not None and CHOICE.search(reply.content):
            found.append([message.id, reply.id])
    return found[:LISTED_KEPT]


CLAIM = re.compile(r"\b(done|added|implemented|completed|finished|created)\b", re.IGNORECASE)
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BACKTICKED = re.compile(r"`([^`\n]+)`")


def looks_like_code(token, backticked, followed_by_call):
    if len(token) < 4:
        return False
    snake = "_" in token.strip("_")
    camel = bool(re.search(r"[a-z][A-Z]", token))
    return snake or camel or followed_by_call or backticked


def claimed_names(line):
    """The identifiers a claim line names: snake_case, camelCase, a name
    called with (), or a name in backticks."""
    names = set()
    ticked = {t for span in BACKTICKED.findall(line) for t in TOKEN.findall(span)}
    for match in TOKEN.finditer(line):
        token = match.group(0)
        called = line[match.end():match.end() + 1] == "("
        if looks_like_code(token, token in ticked, called):
            names.add(token)
    return names


def patch_paths(patch):
    return [m.group(1) for m in re.finditer(r"^diff --git a/(\S+) b/", patch, re.MULTILINE)]


def path_touched(path, touched):
    wanted = path.lstrip("./").split("/")
    for file in touched:
        parts = file.split("/")
        tail = min(len(wanted), len(parts))
        if wanted[-tail:] == parts[-tail:]:
            return True
    return False


def claim_lines(text):
    return [line for line in re.split(r"\n|(?<=[.!])\s+", text.translate(CURLY)) if CLAIM.search(line)]


def contradictions(messages, patches):
    """Completion claims naming an identifier the sender's patch lacks, or
    a file it never touches (C3a and Broken commitment candidates)."""
    found, seen = [], set()
    for message in messages:
        patch = patches.get(message.sender)
        if patch is None:
            continue
        touched = patch_paths(patch)
        for line in claim_lines(message.content):
            missing = [(name, "identifier") for name in sorted(claimed_names(line)) if name not in patch]
            missing += [(path, "path") for path in sorted(set(FILE_PATH.findall(line)))
                        if not path_touched(path, touched)]
            for name, kind in missing:
                if (message.id, name) not in seen:
                    seen.add((message.id, name))
                    found.append({"message_id": message.id, "agent": message.sender, "name": name, "kind": kind})
    return found[:LISTED_KEPT]


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$")


def hunks(patch):
    """(file, first old line, last old line, heading) per hunk."""
    found, file = [], None
    for line in patch.splitlines():
        header = re.match(r"^diff --git a/(\S+) b/", line)
        if header:
            file = header.group(1)
            continue
        match = HUNK.match(line)
        if match and file:
            start, length = int(match.group(1)), int(match.group(2) if match.group(2) is not None else 1)
            found.append((file, start, start + max(length, 1) - 1, match.group(3).strip()))
    return found


def overlapping_hunks(patches):
    """Hunks of two agents' patches on the same file whose base line ranges
    meet (row 78's free signal for Work overlap)."""
    agents, found = sorted(patches), []
    for index, first in enumerate(agents):
        for second in agents[index + 1:]:
            for a in hunks(filter_test_files(patches[first])):
                for b in hunks(filter_test_files(patches[second])):
                    if a[0] == b[0] and a[1] <= b[2] and b[1] <= a[2]:
                        found.append({"file": a[0], "agents": [first, second], "ranges": [[a[1], a[2]], [b[1], b[2]]],
                                      "same_heading": a[3] if a[3] and a[3] == b[3] else None})
    return found[:LISTED_KEPT]


COMMENT = re.compile(r"^\+\s*(#|//|/\*|\*|--|<!--|;)")
MARKER = re.compile(r"\b(?:other|another|second|partner'?s?)\s+agent\b|\bagent[ _-]?\d+\b|\bagent_[a-z0-9]+\b"
                    r"|\bteammate\b|\bpartner\b|\binsert(?:ion)?\s+(?:point|marker|here)\b|\[[\w.]+:[\w.]+\]"
                    r"|\bplaceholder\b|\bgoes here\b", re.IGNORECASE)


def insertion_markers(patches):
    """Added comment lines that name the partner or an insertion point, the
    markers App D's prompt forbids (L1565-1571)."""
    found = []
    for agent in sorted(patches):
        file = None
        for line in filter_test_files(patches[agent]).splitlines():
            header = re.match(r"^diff --git a/(\S+) b/", line)
            if header:
                file = header.group(1)
            elif not line.startswith("+++") and COMMENT.match(line) and MARKER.search(line):
                found.append({"agent": agent, "file": file, "line": line[1:].strip()[:200]})
    return found[:LISTED_KEPT]


def unanswered_requests(events):
    """A team request no response ever answered (row 81's signal)."""
    answered = {e.get("request_id") for e in events if e.get("kind") == "response"}
    return [str(e.get("seq")) for e in events if e.get("kind") == "request" and e.get("request_id") not in answered]


def clean_merge_failed(evaluation):
    """True for a clean naive merge whose tests fail (Divergent
    architecture's signal, and the semantic gap's proxy); None when the
    merge was not clean, where it does not apply."""
    merge = (evaluation or {}).get("merge") or {}
    if merge.get("status") != "clean":
        return None
    return not evaluation.get("both_passed")


# Transcripts: each agent's own tool calls.

@dataclass
class ToolUse:
    name: str
    input: dict
    at: float = None
    output: str = ""
    is_error: bool = False


def main_chain(records):
    """The agent's own turns: a stream's subagent events carry a
    parent_tool_use_id and a transcript's sidechain records isSidechain;
    a transcript that is all sidechain is a subagent's own (the session
    driver copies one) and counts whole."""
    records = [r for r in records if not r.get("parent_tool_use_id")]
    if any(r.get("isSidechain") is False or "isSidechain" not in r for r in records):
        return [r for r in records if not r.get("isSidechain")]
    return records


def blocks(record):
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(content):
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def tool_uses(records):
    """The tool calls in order, each with its result."""
    calls, by_id = [], {}
    for record in records:
        at = epoch_of_iso(record.get("timestamp"))
        for block in blocks(record):
            if block.get("type") == "tool_use":
                call = ToolUse(str(block.get("name")), block.get("input") or {}, at)
                calls.append(call)
                by_id[block.get("id")] = call
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                call = by_id[block["tool_use_id"]]
                call.output, call.is_error = result_text(block.get("content")), bool(block.get("is_error"))
    return calls


def agent_tool_uses(pair, agent):
    """The agent's tool calls from its session transcript (which has
    times), else its stream; None when neither shows a call. A solo
    agent's files are solo_*, whatever its id, as run.py names them."""
    stem = "solo" if pair.result.get("setting") == "solo" else agent
    for name in (f"{stem}_session.jsonl", f"{stem}_stream.jsonl"):
        records = read_jsonl(pair.dir / name)
        calls = tool_uses(main_chain(records)) if records else []
        if calls:
            return calls
    return None


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
EDIT_COMMAND = re.compile(r"\bsed\s+-i\b|\bgit\s+apply\b|\bpatch\s+-p\d")
TEST_COMMAND = re.compile(r"\bpytest\b|\bpy\.test\b|-m\s+unittest\b|\bgo\s+test\b|\bcargo\s+test\b|"
                          r"\bnpm\s+(?:run\s+)?test\b|\byarn\s+test\b|\bjest\b|\bvitest\b|\btox\b")
TEST_FAILED = re.compile(r"\bFAILED\b|\b[1-9]\d* failed\b|Traceback \(most recent call last\)|^error(?:\[E\d+\])?:",
                         re.MULTILINE)


def command_of(call):
    return str(call.input.get("command") or "") if call.name == "Bash" else ""


def is_edit(call):
    return call.name in EDIT_TOOLS or bool(EDIT_COMMAND.search(command_of(call)))


def last_edit_time(calls):
    edits = [c for c in calls if is_edit(c)]
    return edits[-1].at if edits else None


def final_status(pair, conversation):
    """(agents whose last message precedes their last edit, or who sent
    none after editing; each agent's two times), App D's 'you must send a
    final status update message' checked as far as the times allow."""
    missing, times = [], {}
    for agent in agent_features(pair):
        calls = agent_tool_uses(pair, agent)
        edited = last_edit_time(calls) if calls else None
        sent = [m.timestamp for m in conversation.messages if m.sender == agent and m.timestamp is not None]
        last_sent = max(sent) if sent else None
        times[agent] = {"last_edit_at": edited, "last_message_at": last_sent}
        if edited is not None and (last_sent is None or last_sent < edited):
            missing.append(agent)
    timed = [a for a, t in times.items() if t["last_edit_at"] is not None]
    return (missing if timed else None), times


# The git setting.

GIT_SEGMENT = re.compile(r"&&|\|\||;|\n|\|")
FORCE_PUSH = re.compile(r"\bgit\s+push\b.*(?:\s-f\b|\s--force\b|\s--force-with-lease\b|\s\+\S)")
HARD_RESET = re.compile(r"\bgit\s+reset\b.*--hard\b")
AMEND = re.compile(r"\bgit\s+commit\b.*--amend\b")
PUSH = re.compile(r"\bgit\s+push\b")
FETCH = re.compile(r"\bgit\s+(?:fetch|pull|remote\s+update)\b")


def git_segments(calls):
    """(call, one git command) for every git command of every Bash call."""
    found = []
    for call in calls:
        for segment in GIT_SEGMENT.split(command_of(call)):
            if re.search(r"\bgit\b", segment):
                found.append((call, segment.strip()))
    return found


def git_disasters(agent, segments):
    found, amended = [], False
    for _call, command in segments:
        amended = amended or bool(AMEND.search(command))
        if FORCE_PUSH.search(command):
            found.append({"agent": agent, "kind": "amend then force push" if amended else "force push",
                          "command": command[:200]})
        elif HARD_RESET.search(command):
            found.append({"agent": agent, "kind": "reset --hard", "command": command[:200]})
    return found


def first_push_time(segments):
    return next((call.at for call, command in segments if PUSH.search(command)), None)


def git_impatience(agent, segments, partner_push):
    """Fetches before the partner's first push (or with no push at all)."""
    return [{"agent": agent, "command": command[:200], "at": call.at, "partner_first_push_at": partner_push}
            for call, command in segments
            if FETCH.search(command) and (partner_push is None or call.at < partner_push)]


def git_broken(agent, calls):
    """A push whose next test run by the pusher fails: one entry per such
    test run, named with the last push before it."""
    found, pushed = [], None
    for call in calls:
        command = command_of(call)
        if PUSH.search(command):
            pushed = command
        elif pushed is not None and TEST_COMMAND.search(command):
            if call.is_error or TEST_FAILED.search(call.output):
                found.append({"agent": agent, "push": pushed[:200], "test": command[:200]})
            pushed = None
    return found


STAT_LINE = re.compile(r"^ (\S.*?)\s+\|\s+(\d+)\s*([+-]*)\s*$")


def stat_deletions(count, bar):
    """Deleted lines from a --stat line: exact when the bar has one mark
    per line, else scaled from its marks (approximate)."""
    plus, minus = bar.count("+"), bar.count("-")
    if plus + minus == count:
        return minus, False
    if plus + minus == 0:
        return None, False
    return round(count * minus / (plus + minus)), True


def parse_git_log(text):
    commits = []
    for line in text.splitlines():
        if line.startswith("commit "):
            commits.append({"sha": line.split()[1], "author": None, "files": []})
        elif commits and line.startswith("Author:"):
            commits[-1]["author"] = line[len("Author:"):].strip().split(" <")[0]
        elif commits:
            match = STAT_LINE.match(line)
            if match:
                deleted, approximate = stat_deletions(int(match.group(2)), match.group(3))
                commits[-1]["files"].append({"path": match.group(1).strip(), "deletions": deleted,
                                             "approximate": approximate})
    return commits


def same_file(first, second):
    """Paths from --stat, which may shorten a long one to .../tail."""
    a, b = first.replace(".../", ""), second.replace(".../", "")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def git_destructive(text, agents):
    """A commit deleting DESTRUCTIVE_LINES or more lines of a file another
    agent changed in the run."""
    commits = [c for c in parse_git_log(text) if c["author"] in agents]
    found = []
    for commit in commits:
        partner_files = [f["path"] for c in commits if c["author"] != commit["author"] for f in c["files"]]
        for file in commit["files"]:
            if (file["deletions"] or 0) >= DESTRUCTIVE_LINES and any(same_file(file["path"], p) for p in partner_files):
                found.append({"commit": commit["sha"][:12], "author": commit["author"], "file": file["path"],
                              "deletions": file["deletions"], "approximate": file["approximate"]})
    return found[:LISTED_KEPT]


def is_git_pair(pair):
    return pair.result.get("arm") in GIT_ARMS or (pair.dir / "git_log.txt").is_file()


def git_checks(pair):
    """The git-setting detectors the logs can feed, and why any could not run."""
    found, missing = {}, {}
    calls = {agent: agent_tool_uses(pair, agent) for agent in agent_features(pair)}
    if all(c is None for c in calls.values()):
        missing.update(dict.fromkeys(("git_disasters", "git_impatience", "git_broken"), "no transcript"))
    else:
        segments = {agent: git_segments(c or []) for agent, c in calls.items()}
        found["git_disasters"] = [d for agent, s in segments.items() for d in git_disasters(agent, s)][:LISTED_KEPT]
        found["git_broken"] = [b for agent, c in calls.items() for b in git_broken(agent, c or [])][:LISTED_KEPT]
        timed = all(call.at is not None for s in segments.values() for call, _ in s)
        if timed:
            found["git_impatience"] = impatience_of(segments)
        else:
            missing["git_impatience"] = "the transcripts carry no times (a stream without a session file)"
    log = read_text(pair.dir / "git_log.txt")
    if log is None:
        missing["git_destructive"] = "no git_log.txt"
    else:
        found["git_destructive"] = git_destructive(log, set(agent_features(pair)))
    return found, missing


def impatience_of(segments):
    pushes = {agent: first_push_time(s) for agent, s in segments.items()}
    found = []
    for agent, agent_segments in segments.items():
        partners = [pushes[p] for p in segments if p != agent]
        partner_push = min((t for t in partners if t is not None), default=None)
        found += git_impatience(agent, agent_segments, partner_push)
    return found[:LISTED_KEPT]


# Every heuristic together.

HEURISTIC_DEFINITIONS = {
    "unanswered_questions": "C1a candidates: a message with '?' whose addressees send nothing later",
    "ignored_questions": "C1b candidates: [question, reply] where the reply shares no content word with it",
    "near_duplicates": "C4a/C4b candidates: [id, id, difflib ratio >= 0.9, both status-like], same sender",
    "contradictions": "C3a and Broken commitment candidates: a done/added/implemented claim naming an "
                      "identifier the sender's patch lacks or a file it never touches",
    "speech_acts_keyword": "the keyword baseline of the speech acts, beside the judge's",
    "resource_division_candidates": "[message, reply, file]: a file and a line range answered with not/won't/avoid",
    "negotiation_candidates": "[message, reply]: options (1) and (2) with a question, answered by a choice",
    "overlapping_hunks": "Work overlap signal: hunks of two patches on one file with meeting base line ranges",
    "insertion_markers": "App D: added comment lines naming the partner or an insertion point",
    "clean_merge_failed": "Divergent architecture signal: a clean naive merge whose tests fail",
    "unanswered_requests": "Unresponsiveness signal: team requests no response answered",
    "missing_final_status": "App D: agents whose last message precedes their last edit, or who sent none after it",
    "git_disasters": "coordbench's label for git-failures.json's git_disasters: force pushes and hard resets",
    "git_impatience": "coordbench's label for impatience: fetches before the partner's first push",
    "git_broken": "coordbench's label for broken: a push followed by a failing test run",
    "git_destructive": "coordbench's label for destructive: a commit deleting 100 or more lines of a file the "
                       "partner changed (from git log --stat, approximate when the bar is scaled)",
}


def conversation_checks(pair, conversation, found, missing):
    keys = ("unanswered_questions", "ignored_questions", "near_duplicates", "speech_acts_keyword",
            "resource_division_candidates", "negotiation_candidates", "contradictions")
    if not conversation.recorded:
        missing.update(dict.fromkeys(keys, conversation.reason))
        return
    messages = conversation.messages
    senders = list(dict.fromkeys(list(agent_features(pair)) + [m.sender for m in messages]))
    found["unanswered_questions"], found["ignored_questions"] = question_checks(messages, senders)
    found["near_duplicates"] = near_duplicates(messages)
    found["speech_acts_keyword"] = keyword_speech_acts(messages, senders)
    found["resource_division_candidates"] = resource_division_candidates(messages)
    found["negotiation_candidates"] = negotiation_candidates(messages)
    found["contradictions"] = contradictions(messages, agent_patches(pair))


def outcome_checks(pair, found, missing):
    patches = agent_patches(pair)
    if len(patches) >= 2:
        found["overlapping_hunks"] = overlapping_hunks(patches)
    else:
        missing["overlapping_hunks"] = "fewer than two patches"
    found["insertion_markers"] = insertion_markers(patches)
    clean = clean_merge_failed(pair.evaluation)
    if clean is None:
        missing["clean_merge_failed"] = "no clean naive merge (not scored, or the merge conflicted)"
    else:
        found["clean_merge_failed"] = clean
    if any(e.get("kind") == "request" for e in pair.events or []):
        found["unanswered_requests"] = unanswered_requests(pair.events)


def heuristics_of(pair, conversation):
    """(the heuristics, their record): every free check that the pair's
    files can feed; one they cannot is left out and named, never set to
    nothing found."""
    found, missing = {}, {}
    conversation_checks(pair, conversation, found, missing)
    outcome_checks(pair, found, missing)
    status, times = final_status(pair, conversation) if conversation.recorded else (None, {})
    if status is None:
        missing["missing_final_status"] = "no transcript with timed edits, or no message text"
    else:
        found["missing_final_status"] = status
    if is_git_pair(pair):
        git_found, git_missing = git_checks(pair)
        found.update(git_found)
        missing.update(git_missing)
    meta = {"computed_at": now(), "conversation_source": conversation.source,
            "message_count": len(conversation.messages) if conversation.recorded else None,
            "not_computed": missing, "definitions": {k: HEURISTIC_DEFINITIONS[k] for k in found},
            "final_status_times": times, "git_blog_names": GIT_BLOG_NAMES if is_git_pair(pair) else None}
    return found, meta


# judge.json.

def load_judge(pair):
    data = read_json(pair.dir / JUDGE_FILE)
    data = data if isinstance(data, dict) and data.get("format") == FORMAT else {"format": FORMAT}
    data["pair"] = pair_header(pair)
    for key in ("calls", "skipped", "errors"):
        data.setdefault(key, {})
    return data


def save_judge(pair, data):
    path = pair.dir / JUDGE_FILE
    temporary = path.with_name(JUDGE_FILE + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def store(data, name, section, call=None):
    """One section in, with the call that made it; the top-level backend,
    model, prompt variant and time are the latest call's."""
    data[name] = section
    data["skipped"].pop(name, None)
    data["errors"].pop(name, None)
    if call is not None:
        data["calls"][name] = call
        for key, value in (("backend", call["backend"]), ("model", call["model"]),
                           ("prompt_variant", call["prompt_variant"]), ("created_at", call["created_at"])):
            data[key] = value


def already_judged(data, name, backend, variant):
    call = data["calls"].get(name) or {}
    return name in data and call.get("backend") == backend.name and call.get("model") == backend.model \
        and call.get("prompt_variant") == variant


# The commands.

@dataclass
class Job:
    """One section to judge: its name in judge.json, the prompt, and how to
    read the pair and the reply."""
    section: str
    variant: str
    file: str


def conversation_scope(pair, conversation):
    if not is_multi_agent(pair):
        return "a single agent has no conversation"
    if not conversation.recorded:
        return conversation.reason
    if not conversation.messages:
        return "no messages, so no judge call and no label"
    return None


def prepared(job, pair, conversation):
    """(user input, truncated, extra) for a job, or the reason it does not
    apply; extra carries what the section needs back."""
    if job.section == "symptoms":
        failed, reason = symptom_scope(pair)
        if reason:
            return None, reason
        if not conversation.recorded:
            return None, conversation.reason
        if not conversation.messages:
            return {"silent": True, "failed": failed}, None
        text, sources, inputs = symptom_input(pair, conversation, failed)
        return {"text": text, "truncated": bool(inputs["truncated"]), "sources": sources, "inputs": inputs,
                "failed": failed}, None
    reason = conversation_scope(pair, conversation)
    if reason:
        return None, reason
    text, truncated = render_conversation(pair, conversation)
    return {"text": text, "truncated": truncated}, None


def apply_answer(job, data, conversation, answer, extra, call):
    if job.section == "commerrors":
        store(data, "commerrors", commerrors_section(job.variant, conversation, answer, data.get("heuristics")), call)
    elif job.section == "symptoms":
        store(data, "symptoms", symptoms_section(job.variant, conversation, answer, extra["sources"],
                                                 extra["failed"], extra["inputs"]), call)
    elif job.section == "speech_acts":
        labels, left_out = speech_acts_section(conversation, answer)
        store(data, "speech_acts", labels, {**call, **left_out})
    elif job.section == "emergent":
        emergent, git = emergent_sections(conversation, answer)
        store(data, "emergent", emergent, call)
        store(data, "git_failures", git)


def refresh_heuristics(data, pair, conversation):
    data["heuristics"], data["heuristics_meta"] = heuristics_of(pair, conversation)


def judge_pair(job, pair, backend, options):
    """Judge one pair for one job; returns the line printed for it."""
    conversation = conversation_of(pair)
    data = load_judge(pair)
    refresh_heuristics(data, pair, conversation)
    extra, reason = prepared(job, pair, conversation)
    if reason:
        if not options.dry_run:
            data["skipped"][job.section] = reason
            save_judge(pair, data)
        return f"skipped: {reason}"
    if extra.get("silent"):
        if not options.dry_run:
            store(data, "symptoms", silent_symptoms(job.variant, extra["failed"]))
            save_judge(pair, data)
        return "implementation by App G's rule (no messages), no call"
    if not options.force and already_judged(data, job.section, backend, job.variant):
        if not options.dry_run:
            save_judge(pair, data)
        return f"already judged by {backend.name} {backend.model} {job.variant} (--force to judge again)"
    if options.dry_run:
        return f"would call {backend.name} {backend.model}: {len(extra['text']):,} characters of input"
    return call_and_store(job, pair, data, conversation, extra, backend)


def call_and_store(job, pair, data, conversation, extra, backend):
    system = prompt_text(job.file)
    try:
        answer = ask(backend, system, extra["text"])
    except JudgeError as problem:
        data["errors"][job.section] = str(problem)
        save_judge(pair, data)
        return f"error: {problem}"
    call = call_record(backend, job.variant, job.file, system, extra["text"], answer, extra["truncated"])
    if answer.parsed is None:
        data["calls"][job.section] = call
        data["errors"][job.section] = call["parse_error"]
        save_judge(pair, data)
        return f"error: {call['parse_error']}"
    apply_answer(job, data, conversation, answer, extra, call)
    save_judge(pair, data)
    return "judged"


def job_for(command, variant):
    tables = {"commerrors": ("commerrors", COMMERROR_VARIANTS, "appF-7"),
              "symptoms": ("symptoms", SYMPTOM_VARIANTS, "symptoms-r1"),
              "speechacts": ("speech_acts", SPEECH_ACT_VARIANTS, "speechacts-v1"),
              "emergent": ("emergent", EMERGENT_VARIANTS, "emergent-v1")}
    section, variants, default = tables[command]
    variant = variant or default
    if variant not in variants:
        raise SystemExit(f"judge: {command} has no variant {variant!r}; it has {', '.join(variants)}")
    return Job(section, variant, variants[variant]["file"])


class DryBackend:
    """Names the backend a dry run would call, and never calls it."""

    def __init__(self, name, model):
        self.name, self.model = name, model


def backend_for(args):
    if args.dry_run:
        return DryBackend(args.backend, args.model or os.environ.get(JUDGE_MODEL) or DEFAULT_MODELS[args.backend])
    return make_backend(args)


def loaded_pairs(paths):
    pairs = []
    for directory in pair_dirs(paths):
        try:
            pairs.append(load_pair(directory))
        except FileNotFoundError as error:
            warn(str(error))
    return pairs


def command_heuristics(args):
    for pair in loaded_pairs(args.paths):
        data = load_judge(pair)
        refresh_heuristics(data, pair, conversation_of(pair))
        save_judge(pair, data)
        found = sorted(k for k, v in data["heuristics"].items() if v and k != "speech_acts_keyword")
        print(f"{pair.dir}: {', '.join(found) or 'nothing found'}")
    return 0


def command_judge(args, backend=None):
    job = job_for(args.command, args.variant)
    backend = backend or backend_for(args)
    pairs = loaded_pairs(args.paths)
    if not pairs:
        warn("no pair directory (one with result.json) found")
        return 2
    tally = {}
    for pair in pairs:
        line = judge_pair(job, pair, backend, args)
        tally[line.split(":")[0]] = tally.get(line.split(":")[0], 0) + 1
        print(f"{pair.dir}: {line}")
    print("; ".join(f"{count} {what}" for what, count in sorted(tally.items())))
    return 0


def command_render(args):
    """The system prompt and the input one judge call would get, printed
    and never sent."""
    job = job_for(args.section, args.variant)
    found = pair_dirs([args.path])
    if not found:
        warn("no pair directory (one with result.json) found")
        return 2
    pair = load_pair(found[0])
    conversation = conversation_of(pair)
    extra, reason = prepared(job, pair, conversation)
    if reason or extra.get("silent"):
        print(f"no call for this pair: {reason or 'no messages (App G decides it as implementation)'}")
        return 0
    print(f"=== system prompt ({job.variant}, prompts/{job.file}) ===")
    print(prompt_text(job.file))
    print("=== user input ===")
    print(extra["text"])
    return 0


def add_backend_options(parser, judging=True):
    """The backend's options; judging adds the judge commands' own (the
    variant, a dry run, judging again)."""
    parser.add_argument("--backend", choices=sorted(DEFAULT_MODELS), default="claude-cli",
                        help="claude-cli (the subscription, the default) or openai-compatible")
    parser.add_argument("--model", help=f"the judge's model (default ${JUDGE_MODEL}, else "
                                        f"{DEFAULT_MODELS['claude-cli']} or {DEFAULT_MODELS['openai-compatible']})")
    parser.add_argument("--base-url", help=f"openai-compatible API base, e.g. https://api.openai.com/v1 "
                                           f"(default ${JUDGE_URL}); the key is ${JUDGE_KEY}, never a flag")
    parser.add_argument("--temperature", type=float, help="openai-compatible, a model that takes one (default 0)")
    parser.add_argument("--reasoning-effort", help="openai-compatible, a reasoning model's effort (sent and recorded)")
    parser.add_argument("--claude-bin", default="claude", help="the claude CLI to run (default claude)")
    parser.add_argument("--timeout", type=float, default=CALL_TIMEOUT, help="seconds per call")
    if not judging:
        return
    parser.add_argument("--variant", help="the prompt variant")
    parser.add_argument("--dry-run", action="store_true", help="say what would be judged; call nothing, write nothing")
    parser.add_argument("--force", action="store_true", help="judge again a section the same judge already made")


def parser_of():
    parser = argparse.ArgumentParser(description="Label a coordbench pair's failure modes as CooperBench's "
                                                 "paper does; only the judge commands call a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    free = commands.add_parser("heuristics", help="the free cross-checks only; no model call")
    free.add_argument("paths", nargs="+")
    for name, text in (("commerrors", "App F's communication errors"), ("symptoms", "App G's failure symptoms"),
                       ("speechacts", "speech acts per message"), ("emergent", "Sec 6.4's emergent behaviours")):
        judged = commands.add_parser(name, help=f"{text}; one model call per pair")
        judged.add_argument("paths", nargs="+")
        add_backend_options(judged)
    render = commands.add_parser("render", help="print one call's prompt and input; no model call")
    render.add_argument("section", choices=("commerrors", "symptoms", "speechacts", "emergent"))
    render.add_argument("path")
    render.add_argument("--variant")
    return parser


def main(argv=None):
    args = parser_of().parse_args(argv)
    if args.command == "heuristics":
        return command_heuristics(args)
    if args.command == "render":
        return command_render(args)
    return command_judge(args)


if __name__ == "__main__":
    sys.exit(main())
