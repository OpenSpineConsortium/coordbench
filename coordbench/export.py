"""One coordbench run as CooperBench's own viewer reads a run.

    python3 coordbench/export.py <pair_dir>... [--site <out_dir>] [--model-key KEY]
                                 [--cooperbench-dir DIR] [--causes FILE] [--no-pair-files]

A pair directory is the one that holds result.json; a directory given
without one is searched for every result.json below it, so a whole run
exports in one call. It may be a coordbench pair directory or a
CooperBench claude_code run, since both hold result.json, eval.json and
<agent>_stream.jsonl or <agent>_session.jsonl; a CooperBench run has
conversation.json and <agent>_sent.jsonl where coordbench has
events.jsonl.

What it writes, and why each file has the shape it has:

- trajectory.json.gz in the pair directory (unless --no-pair-files), and
  agent<feature>_traj.json or solo_traj.json when they are missing and a
  transcript is there: the trajectory object below, and the files
  CooperBench's runner writes per agent, whose messages come from
  CooperBench's parse_session_jsonl, vendored here verbatim (MIT).
- With --site, the CooperBench website's layout, so its stock viewer
  (src/pages/viewer.astro) opens a coordbench run unchanged:
  static/data/<coop|coop_git>/trajectories/<model_key>/
  <repo>_task<N>_feature<a>_feature<b>.json.gz (gzip 9, compact JSON),
  static/data/<coop|coop_git>/index.json merged as the website's
  write_index merges it, legacy/<coop|coop_git>/<repo>/task<N>/
  aggregated_results_<model_key>_k1.json for pairs scored through the
  cascade (the shape generate_coop_index.py reads), and, for pairs a
  human labelled (--causes), static/data/causes/index.json with
  causes/<cause>/<id>_trajectory.json in the 7-key step shape.

The trajectory object is {"metadata", "steps"}. metadata carries the
stock viewer's keys (repo, task_id, features, agent_1 .. agent_k,
total_cost, started_at, duration_seconds) and metadata.coordbench holds
everything else (FORMAT): the agents, the outcome, where the times came
from, the files read, the messages with their read times, the hub's
events on the wall clock, and judge.json's labels when there are any.
Lanes are ordinal: agent_k is the k-th agent in result.json order, which
is sorted by feature, never the feature number, since the website's
prepare scripts look agents up by feature number and get every pair but
feature1_feature2 wrong.

Steps are one per tool call, folding the call and its result as the
OpenHands SDK and mini-swe shapes do. The source of record is the agent's
<agent>_session.jsonl, Claude Code's own transcript, whose lines carry
their UTC times. It may be a concatenation of several transcripts (the
claude_code adapter cats every one it finds), so it is split into
threads by sessionId and, for sidechains, by agentId: the main thread is
the one whose first user message carries the agent's prompt or run
token, preferring a thread that is not a sidechain, and every other
thread's calls become steps marked coordbench.subagent. Without a
transcript, <agent>_stream.jsonl gives the same blocks without times,
which are spaced evenly over the agent's phase (the website's SDK
approach, marked "interpolated"); without either, as in the session
driver's pilots, the hub's own record of the coordination calls gives
steps at their real times and nothing else (marked "hub-events"). The
comparison viewer leaves both out of speed rankings by default.

The hub join copies the hub's timing, message ids and message text onto
the steps that made the calls, since the hub's record is authoritative
for what was delivered. A call is matched by agent, tool and time (the
transcript's tool_use time against run_started's epoch plus the event's
t), the order among candidates only breaking ties: a call the hub
refused emits no "tool" event, one Bash command can chain several
aliases, and a Bash broadcast is one tool use but several deliveries, so
a join by ordinal alone would drift. Whatever is left unmatched on
either side is listed in join_warnings.

The stock viewer drops a step whose text contains any of
STOCK_VIEWER_FILTERS (viewer.astro:513-522, on the first non-empty of
content, text and message); prompts are never steps here, and any other
step the filter would drop is listed in filtered_by_stock_viewer. It
also reads only agent_1 and agent_2 for its run panel, so a run with
more agents says so in metadata.coordbench.stock_viewer_note and puts
every agent's cost in total_cost.

The module reads JSON and writes files only and imports nothing of
coordbench, like timeline.py, so it can be copied alone beside a run."""

import argparse
import gzip
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "coordbench-trajectory/1"
TIMESTAMP_KINDS = ("session-jsonl", "interpolated", "hub-events")
OBSERVATION_LIMIT = 10_000
THOUGHT_LIMIT = 5_000
GZIP_LEVEL = 9
COMPACT = (",", ":")

# The arms whose agents share a git remote, which the website files under
# coop_git and the stock viewer opens with setting=coopGit.
GIT_ARMS = ("coop-git", "sdlc")
SETTING_DIRS = ("coop", "coop_git")
# The dataset renamed typst to typst_task after 59bbf65; the website keeps
# the paper models' rows under the old name and the Gemini rows under the
# new one, so a coordbench typst pair meets both only through the alias.
REPO_ALIASES = {"typst": "typst_task"}
STOCK_VIEWER_FILTERS = ("You are OpenHands agent", "<ROLE>", "You are agent_", "**FEATURE DESCRIPTION**",
                        "Added workspace context")
# viewer.astro's tool types and their order in its palette (890-907).
TOOL_TYPES = ("bash", "find_grep", "view", "edit", "create", "communication", "task_tracking", "submit",
              "think", "recall", "condensation", "message", "other")
CAUSES = ("expectation", "commitment", "communication")
SEVEN_KEYS = ("id", "timestampMs", "toolType", "toolName", "details", "args", "agentId")
NO_NEW_MESSAGES = "No new messages."
# Slack, in seconds, around a step's span when it is matched to the hub's
# record of the call: the transcript writes the tool use as the block
# streams in, before the call reaches the hub, and the result after it.
JOIN_SLACK_SECONDS = 2.0
TEST_COMMAND = re.compile(r"\b(pytest|go test|cargo test|npm test|jest|vitest)\b")

# The coordination tools by the names the arms give them, each with how
# the stock viewer's vocabulary names it.
SEND_TOOLS = ("send_message", "broadcast")
RECEIVE_TOOLS = ("receive_messages", "wait_for_message")
TEAM_TASK_TOOLS = ("task_create", "task_claim", "task_update", "task_list")
SDLC_TOOLS = ("board", "claim_issue", "open_pull_request", "mark_ready", "pull_request_status")
COORDINATION_TOOLS = (SEND_TOOLS + RECEIVE_TOOLS + ("peek_messages", "list_agents") + TEAM_TASK_TOOLS
                      + ("request", "respond", "pending") + SDLC_TOOLS)
# The shell commands, as cli.py installs them and CooperBench's coop_msg.py
# and coop_task.py name them; coord-<tool> is every tool's own command.
ALIASES = {
    "coop-send": "send_message", "coop-broadcast": "broadcast", "coop-recv": "receive_messages",
    "coop-peek": "peek_messages", "coop-agents": "list_agents", "coop-task-create": "task_create",
    "coop-task-claim": "task_claim", "coop-task-update": "task_update", "coop-task-list": "task_list",
    "coop-request": "request", "coop-respond": "respond", "coop-pending": "pending",
    "sdlc-board": "board", "sdlc-claim": "claim_issue", "sdlc-pr": "open_pull_request",
    "sdlc-ready": "mark_ready", "sdlc-status": "pull_request_status",
}
MCP_TOOL = re.compile(r"^mcp__(.+)__(\w+)$")
COORDINATION_SERVER = re.compile(r"coordbench|cooperbench", re.IGNORECASE)
TODO_STATUS = {"pending": "todo", "in_progress": "in_progress", "completed": "done"}
TASK_STATUS = {"open": "todo", "in_progress": "in_progress", "blocked": "todo", "done": "done"}
SYNTHETIC_MODEL = "<synthetic>"

# The schema the comparison viewer's normaliser reads; its page carries
# the same values, and the tests hold the two together.
# The OpenHands SDK shape's action names (the website's
# prepare_oh_sdk_trajectories.py), which tell it from mini-swe's, whose
# action is the command itself.
SDK_ACTIONS = ("TerminalAction", "FileEditorAction", "TaskTrackerAction", "SendMessageAction", "FinishAction",
               "ThinkAction", "ReceiveMessage")
SCHEMA = {"format": FORMAT, "timestamp_kinds": list(TIMESTAMP_KINDS), "tool_types": list(TOOL_TYPES),
          "stock_viewer_filters": list(STOCK_VIEWER_FILTERS), "repo_aliases": REPO_ALIASES,
          "setting_dirs": list(SETTING_DIRS), "seven_keys": list(SEVEN_KEYS), "causes": list(CAUSES),
          "no_new_messages": NO_NEW_MESSAGES, "sdk_actions": list(SDK_ACTIONS),
          "test_command": TEST_COMMAND.pattern}


def warn(text):
    print(f"export: {text}", file=sys.stderr)


# Reading files. Every reader returns None for a file that is not there,
# which is the normal case for a run that predates the file.

def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        warn(f"{path}: not JSON, left out")
        return None


def json_lines(text):
    """The JSON objects of a JSONL text in order; a line that is not one
    (the stderr Claude Code mixes into its stream, a line cut by a full
    disk) is skipped."""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


def read_jsonl(path):
    text = read_text(path)
    return None if text is None else list(json_lines(text))


# Times. Claude Code writes UTC with a Z; run.py writes naive local times,
# read here as the host's local time, which is what datetime.now() gave.

def parse_time(value):
    """An ISO 8601 text or an epoch number as an aware datetime, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def utc_iso(moment):
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


def milliseconds_between(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() * 1000, 3)


def clip(text, limit):
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    return text if len(text) <= limit else text[:limit]


# CooperBench's parse_session_jsonl and _content_blocks_to_text, verbatim
# from src/cooperbench/agents/claude_code/parsers.py at 63b9d44 (MIT,
# Copyright (c) the CooperBench authors), so agent<feature>_traj.json holds
# what CooperBench's runner would write; the tests hold them to the source
# when a checkout is at hand.

def _iter_json_lines(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _content_blocks_to_text(content: Any) -> str:
    """Flatten a Claude message ``content`` field to a plain string.

    Anthropic messages use a list of typed blocks (text/tool_use/
    tool_result/thinking).  Downstream CooperBench code does
    ``"send_message" in msg["content"]`` so we must always return a string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text:
                parts.append(text)
        elif btype == "thinking":
            thought = block.get("thinking") or block.get("text") or ""
            if thought:
                parts.append(f"[thinking] {thought}")
        elif btype == "tool_use":
            name = block.get("name", "")
            args = block.get("input")
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except TypeError:
                args_str = str(args)
            parts.append(f"[tool_use {name}] {args_str}")
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner_text = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in inner)
            elif inner is None:
                inner_text = ""
            else:
                inner_text = str(inner)
            parts.append(f"[tool_result] {inner_text}")
        else:
            try:
                parts.append(json.dumps(block, ensure_ascii=False))
            except TypeError:
                parts.append(str(block))
    return "\n".join(p for p in parts if p)


def parse_session_jsonl(text: str) -> list[dict[str, str]]:
    """Convert Claude Code session JSONL to OpenAI-style chat messages.

    Returns a list of ``{"role": ..., "content": ...}`` dicts, sorted by
    ``timestamp``.  ``content`` is always a string.

    Role resolution: prefer ``message.role`` when present, otherwise fall
    back to ``event.type``.  Recent claude-code session writers emit
    assistant turns with ``message.role: None`` (the role is only in the
    top-level ``type`` field), so a strict role-validation check would
    silently drop every LLM turn.
    """
    events: list[dict[str, Any]] = list(_iter_json_lines(text))
    events.sort(key=lambda e: e.get("timestamp") or "")

    messages: list[dict[str, str]] = []
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role") or event.get("type")
        if role not in {"user", "assistant", "system"}:
            continue
        content_text = _content_blocks_to_text(message.get("content"))
        messages.append({"role": role, "content": content_text})
    return messages


# The pair directory.

def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def feature_numbers(result):
    features = result.get("features") or []
    return sorted(int(f) for f in features if isinstance(f, int) or (isinstance(f, str) and f.isdigit()))


def agent_entries(result):
    """The run's agents in result.json's order, each with its record: the
    "agents" map of a pair (sorted by feature, which is how coop.py writes
    it), or solo's singular "agent"."""
    if isinstance(result.get("agents"), dict):
        entries = [(name, record) for name, record in result["agents"].items() if isinstance(record, dict)]
        return sorted(entries, key=lambda entry: feature_key(entry[1]))
    if isinstance(result.get("agent"), dict):
        record = result["agent"]
        return [(record.get("id") or "solo", record)]
    return []


def feature_key(record):
    feature = record.get("feature_id")
    if isinstance(feature, list):
        feature = feature[0] if feature else None
    return (0, feature) if isinstance(feature, int) else (1, 0)


def agent_feature(record, result):
    feature = record.get("feature_id")
    if feature is None and isinstance(record.get("features"), list):
        return list(record["features"])
    if feature is None and result.get("setting") == "solo":
        return feature_numbers(result)
    return feature


def describe_agents(pair_dir, result):
    agents = []
    for index, (name, record) in enumerate(agent_entries(result), 1):
        agents.append({"id": name, "lane": f"agent_{index}", "index": index - 1, "record": record,
                       "feature_id": agent_feature(record, result),
                       "role": record.get("role") or record.get("team_role"),
                       "session": pair_dir / f"{name}_session.jsonl", "stream": pair_dir / f"{name}_stream.jsonl",
                       "outcome": read_json(pair_dir / f"{name}_session_outcome.json")})
    return agents


def prompt_files(pair_dir, agents):
    names = []
    for agent in agents:
        for suffix in ("_prompt.md", ".session-prompt.md"):
            if (pair_dir / f"{agent['id']}{suffix}").is_file():
                names.append(f"{agent['id']}{suffix}")
    return names


def load_pair(pair_dir):
    """What the export reads from one pair directory; result.json must be
    there."""
    pair_dir = Path(pair_dir)
    result = read_json(pair_dir / "result.json")
    if not isinstance(result, dict):
        raise FileNotFoundError(f"{pair_dir / 'result.json'} is not there to read")
    pair = {"dir": pair_dir, "result": result, "evaluation": read_json(pair_dir / "eval.json"),
            "events": read_jsonl(pair_dir / "events.jsonl"), "judge": judge_labels(pair_dir),
            "conversation": read_json(pair_dir / "conversation.json"), "sources": []}
    pair["agents"] = describe_agents(pair_dir, result)
    for name in ("result.json", "eval.json", "events.jsonl", "conversation.json", "conversation_store.json"):
        if (pair_dir / name).is_file():
            pair["sources"].append(name)
    if pair["judge"] is not None:
        pair["sources"].append(pair["judge"][0])
    return pair


def judge_labels(pair_dir):
    """judge.json, the post-hoc judge's labels, or failure_modes.json, the
    name the first draft of the detector spec gave it: (name, labels)."""
    for name in ("judge.json", "failure_modes.json"):
        labels = read_json(pair_dir / name)
        if isinstance(labels, dict):
            return name, labels
    return None


# The hub's record: anchored on the wall clock, its calls with their
# outcomes, and what each call delivered or handed over.

def run_started(events):
    return next((e for e in events or () if e.get("kind") == "run_started"), None)


def phase_record(result, phase, agent=None):
    for record in result.get("timings") or []:
        if isinstance(record, dict) and record.get("phase") == phase and record.get("agent") == agent:
            return record
    return None


def hub_anchor(events, result):
    """The epoch the events' t counts from, and where it came from:
    run_started's epoch, or for a run from before that event the middle
    of the run_create phase, when the hub made the Run and started its
    clock (good to half that phase, a few milliseconds), or for a run from
    before the timings the pair's start, which comes a little before the
    hub's Run (good to the seconds the hub took to start). A run with no
    events has nothing to anchor."""
    if not events:
        return None, None
    started = run_started(events)
    if started and number(started.get("epoch")) is not None:
        return started["epoch"], "run_started"
    record = phase_record(result, "run_create")
    if record:
        begin, end = parse_time(record.get("started")), parse_time(record.get("ended"))
        if begin and end:
            return (begin.timestamp() + end.timestamp()) / 2, "run_create phase (approximate)"
    if parse_time(result.get("started_at")):
        return parse_time(result["started_at"]).timestamp(), "pair start (approximate)"
    return None, None


def absolute(event, anchor):
    t = number(event.get("t"))
    return None if anchor is None or t is None else round(anchor + t, 6)


def hub_calls(events, anchor):
    """Every "tool" event as a call, paired with its "tool_done" (the next
    one of the same agent and tool, since a done names no call) and with
    the deliveries and receipts it produced."""
    calls, open_calls = [], {}
    for event in events or ():
        kind = event.get("kind")
        if kind == "tool":
            call = {"seq": event.get("seq"), "agent": event.get("agent"), "tool": event.get("tool"),
                    "via": event.get("via"), "t": number(event.get("t")), "abs_ts": absolute(event, anchor),
                    "done": None, "messages": [], "receipts": [], "joined": False}
            calls.append(call)
            open_calls.setdefault((call["agent"], call["tool"]), []).append(call)
        elif kind == "tool_done":
            waiting = open_calls.get((event.get("agent"), event.get("tool"))) or []
            if waiting:
                waiting.pop(0)["done"] = event
    attach_deliveries(calls, events or ())
    return calls


SENDING_TOOLS = SEND_TOOLS + ("request", "respond")
RECEIVING_TOOLS = RECEIVE_TOOLS + ("pending",)


def attach_deliveries(calls, events):
    """Each delivery to the call whose seq is its send_id, and each receipt
    to the call whose seq is its call_id. Before those ids were recorded
    (the pilots), a delivery goes to its sender's latest sending call and
    a receipt to its agent's latest receiving call, which is where the
    hub emitted them, inside the call."""
    by_seq = {call["seq"]: call for call in calls}
    latest = {}
    for event in events:
        kind, agent = event.get("kind"), event.get("agent")
        if kind == "tool" and event.get("tool") in SENDING_TOOLS + RECEIVING_TOOLS:
            role = "send" if event.get("tool") in SENDING_TOOLS else "receive"
            latest[(agent, role)] = by_seq.get(event.get("seq"))
        if kind == "message":
            call = by_seq.get(event["send_id"]) if "send_id" in event else latest.get((agent, "send"))
            if call is not None:
                call["messages"].append(event)
        elif kind in ("receive", "receive_lost"):
            call = by_seq.get(event.get("call_id")) if "call_id" in event else latest.get((agent, "receive"))
            if call is not None:
                call["receipts"].append(event)


def hub_messages(events, anchor):
    """The deliveries as messages, {id, from, to, content, sent_at,
    read_at}, with read_at the times of the receipts that stand (a
    receive_lost takes back the receive before it). A pilot's message
    event has no text, and its content is None."""
    messages, by_id = [], {}
    for event in events or ():
        if event.get("kind") != "message":
            continue
        message_id = event.get("message_id", event.get("seq"))
        sent = event.get("timestamp")
        message = {"id": message_id, "from": event.get("agent"), "to": event.get("to"),
                   "content": event.get("content"), "send_id": event.get("send_id"),
                   "sent_at": utc_iso(parse_time(sent)) if number(sent) is not None
                   else utc_iso(parse_time(absolute(event, anchor))), "read_at": []}
        messages.append(message)
        by_id[message_id] = message
    for event in events or ():
        for message_id in event.get("message_ids") or ():
            message = by_id.get(message_id)
            if message is None:
                continue
            if event.get("kind") == "receive":
                message["read_at"].append(utc_iso(parse_time(absolute(event, anchor))))
            elif event.get("kind") == "receive_lost" and message["read_at"]:
                message["read_at"].pop()
    return messages


def conversation_messages(conversation):
    """CooperBench's conversation.json as messages, for a run without
    events.jsonl; it records no read times."""
    messages = []
    for index, entry in enumerate(conversation or []):
        if isinstance(entry, dict):
            messages.append({"id": index, "from": entry.get("from"), "to": entry.get("to"),
                             "content": entry.get("message"), "send_id": None,
                             "sent_at": utc_iso(parse_time(entry.get("timestamp"))), "read_at": []})
    return messages


def hub_events_on_clock(events, anchor):
    return [{**event, "abs_ts": absolute(event, anchor)} for event in events or ()]


# Transcripts. A raw call is one tool use or one thinking block, before
# it is mapped onto the viewer's vocabulary.

def content_blocks(message):
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in content or [] if isinstance(block, dict)]


def result_text(block):
    inner = block.get("content")
    if isinstance(inner, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in inner)
    return "" if inner is None else str(inner)


def split_threads(lines):
    """The transcript's lines by thread: the main sessions by sessionId,
    a sidechain by sessionId and agentId, since Claude Code writes a
    subagent's records with its parent's sessionId."""
    threads = {}
    for order, line in enumerate(lines):
        key = (line.get("sessionId"), line.get("agentId") if line.get("isSidechain") else None,
               bool(line.get("isSidechain")))
        threads.setdefault(key, []).append((order, line))
    return threads


def first_user_text(thread):
    for _, line in thread:
        if line.get("type") == "user" and isinstance(line.get("message"), dict):
            content = line["message"].get("content")
            if isinstance(content, str):
                return content
            texts = [b.get("text", "") for b in content_blocks(line["message"]) if b.get("type") == "text"]
            if texts:
                return "\n".join(texts)
    return ""


def opening_text(thread, lines=6):
    """The first user message and the first few replies: where the
    session driver's run token is."""
    texts = [first_user_text(thread)]
    for _, line in thread[:lines]:
        if line.get("type") == "assistant":
            texts.append(_content_blocks_to_text((line.get("message") or {}).get("content")))
    return "\n".join(texts)


def main_thread(threads, hints):
    """The thread that is the agent's own session: the one whose opening
    carries a hint (its prompt's first line, its run token), a main
    session before a sidechain; else the longest main session; else the
    longest thread."""
    def carries_hint(key):
        opening = opening_text(threads[key])
        return any(hint and hint in opening for hint in hints)
    keys = sorted(threads, key=lambda key: (key[2], -len(threads[key])))
    for key in keys:
        if carries_hint(key):
            return key
    return keys[0] if keys else None


def prompt_hints(pair_dir, agent):
    hints = []
    outcome = agent.get("outcome") or {}
    if outcome.get("run_token"):
        hints.append(outcome["run_token"])
    for suffix in ("_prompt.md", ".session-prompt.md"):
        text = read_text(pair_dir / f"{agent['id']}{suffix}")
        first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
        if first:
            hints.append(first[:200])
    return hints


def sorted_thread(thread):
    return sorted(thread, key=lambda entry: (entry[1].get("timestamp") or "", entry[0]))


def raw_calls_of_thread(thread, subagent):
    """The thread's tool uses and thinking blocks in time order, each
    with the text the assistant wrote since the call before, and each
    tool use's result folded in; and the thread's last assistant text."""
    calls, by_tool_id, pending = [], {}, []
    for order, line in sorted_thread(thread):
        moment, message = parse_time(line.get("timestamp")), line.get("message")
        if line.get("type") == "assistant" and isinstance(message, dict):
            model = message.get("model") if message.get("model") != SYNTHETIC_MODEL else None
            for block in content_blocks(message):
                add_assistant_block(block, moment, model, order, subagent, calls, by_tool_id, pending)
        elif line.get("type") == "user" and isinstance(message, dict):
            for block in content_blocks(message):
                if block.get("type") == "tool_result" and block.get("tool_use_id") in by_tool_id:
                    fold_result(by_tool_id[block["tool_use_id"]], block, line.get("toolUseResult"), moment)
    return calls, "\n\n".join(pending).strip()


def add_assistant_block(block, moment, model, order, subagent, calls, by_tool_id, pending):
    kind = block.get("type")
    if kind == "text" and block.get("text"):
        pending.append(block["text"])
    elif kind == "thinking" and (block.get("thinking") or block.get("text")):
        calls.append(new_call("think", None, {"thought": block.get("thinking") or block.get("text")},
                              None, moment, model, order, subagent, ""))
    elif kind == "tool_use":
        call = new_call("tool", block.get("name") or "", block.get("input") or {}, block.get("id"), moment,
                        model, order, subagent, "\n\n".join(pending))
        pending.clear()
        calls.append(call)
        if block.get("id"):
            by_tool_id[block["id"]] = call


def new_call(kind, tool, tool_input, tool_use_id, moment, model, order, subagent, thought):
    return {"kind": kind, "tool": tool, "input": tool_input if isinstance(tool_input, dict) else {"value": tool_input},
            "tool_use_id": tool_use_id, "ts": moment, "end": None, "model": model, "order": order,
            "subagent": subagent, "thought": thought, "result": None, "extra": None, "is_error": False,
            "hub": []}


def fold_result(call, block, extra, moment):
    call["result"] = result_text(block)
    call["extra"] = extra
    call["is_error"] = bool(block.get("is_error"))
    call["end"] = moment


def stream_calls(lines):
    """A stream-json file's tool uses and thinking blocks, without times;
    its result event; and its init event."""
    thread = [(order, line) for order, line in enumerate(lines) if line.get("type") in ("assistant", "user")]
    calls, last_text = raw_calls_of_thread(thread, False)
    result = next((line for line in reversed(lines) if line.get("type") == "result"), None)
    return calls, last_text, result


def is_stream(lines):
    """Whether the file is Claude Code's stream-json: the session driver
    once wrote a one-line coordbench_session record under the same name."""
    return bool(lines) and any(line.get("type") in ("system", "assistant", "user", "result") for line in lines)


# Usage, counted once per API message: one message spans several lines,
# one per content block, each repeating its usage, and only some carry the
# final count, so each field is the largest any of its lines gives.

USAGE_FIELDS = (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
                ("cache_write", "cache_creation_input_tokens"))


def transcript_usage(lines):
    per_message, models = {}, []
    for index, line in enumerate(lines):
        message = line.get("message")
        if line.get("type") != "assistant" or not isinstance(message, dict):
            continue
        if message.get("model") and message["model"] != SYNTHETIC_MODEL and message["model"] not in models:
            models.append(message["model"])
        counts = per_message.setdefault(message.get("id") or f"line-{index}", dict.fromkeys(dict(USAGE_FIELDS), 0))
        usage = message.get("usage") or {}
        for name, source in USAGE_FIELDS:
            counts[name] = max(counts[name], int(number(usage.get(source)) or 0))
    if not per_message:
        return None, models
    return {name: sum(counts[name] for counts in per_message.values()) for name, _ in USAGE_FIELDS}, models


def record_usage(record):
    """result.json's four token fields as the export's, None when the
    driver measured none."""
    fields = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_tokens",
              "cache_write": "cache_write_tokens"}
    usage = {name: number(record.get(source)) for name, source in fields.items()}
    return usage if any(value is not None for value in usage.values()) else None


def token_usage(tokens):
    """llm_metrics' accumulated_token_usage, the totals the legacy files
    carry: the prompt total counts cached reads and writes too."""
    tokens = tokens or {}
    prompt = sum(tokens.get(name) or 0 for name in ("input", "cache_read", "cache_write"))
    return {"prompt_tokens": prompt, "completion_tokens": tokens.get("output") or 0,
            "cache_read_tokens": tokens.get("cache_read") or 0, "cache_write_tokens": tokens.get("cache_write") or 0}


# One agent's calls, from whichever source the pair has.

def agent_window(result, agent):
    """The agent's phase on the wall clock: its "agent" timing, else the
    pair's start and end."""
    record = phase_record(result, "agent", agent["id"])
    if record:
        return parse_time(record.get("started")), parse_time(record.get("ended"))
    return parse_time(result.get("started_at")), parse_time(result.get("ended_at"))


def collect_agent(pair, agent):
    """The agent's raw calls and where their times came from, with what
    the source says beside them: the last assistant text, the stream's
    result event, the usage and the models seen, the agent's end."""
    session = read_jsonl(agent["session"]) or []
    stream = read_jsonl(agent["stream"]) or []
    stream = stream if is_stream(stream) else []
    found = {"calls": [], "kind": None, "last_text": "", "result_event": None, "usage": None, "models": [],
             "end": None, "init": next((line for line in stream if line.get("type") == "system"), None)}
    if stream:
        found["result_event"] = next((line for line in reversed(stream) if line.get("type") == "result"), None)
        pair["sources"].append(agent["stream"].name)
    if session:
        pair["sources"].append(agent["session"].name)
        collect_session(pair, agent, session, found)
    elif stream:
        collect_stream(pair, agent, stream, found)
    found["end"] = agent_end(pair, agent, found)
    return found


def collect_session(pair, agent, session, found):
    threads = split_threads(session)
    main = main_thread(threads, prompt_hints(pair["dir"], agent))
    for key, thread in threads.items():
        calls, last_text = raw_calls_of_thread(thread, key != main)
        found["calls"].extend(calls)
        if key == main:
            found["last_text"] = last_text
            found["first_moment"] = min((parse_time(line.get("timestamp")) for _, line in thread
                                         if parse_time(line.get("timestamp"))), default=None)
            found["last_moment"] = max((parse_time(line.get("timestamp")) for _, line in thread
                                        if parse_time(line.get("timestamp"))), default=None)
    found["kind"] = "session-jsonl"
    found["usage"], found["models"] = transcript_usage(session)


def collect_stream(pair, agent, stream, found):
    calls, last_text, _ = stream_calls(stream)
    start, end = agent_window(pair["result"], agent)
    spread_evenly(calls, start, end)
    found.update({"calls": calls, "last_text": last_text, "kind": "interpolated"})
    usage, found["models"] = transcript_usage(stream)
    found["usage"] = record_usage(agent["record"]) or usage


def spread_evenly(calls, start, end):
    """The website's interpolate_timestamps: evenly spaced times from the
    agent's start to its end."""
    if start is None or end is None or not calls:
        return
    step = (end - start) / max(len(calls), 1)
    for index, call in enumerate(calls):
        call["ts"] = start + step * index
        call["end"] = start + step * (index + 1)


def agent_end(pair, agent, found):
    """When the agent finished: the session driver's marker (its outcome's
    ended_at, else the agent phase's end, which the runner took when it
    saw the marker); under claude-cli the transcript's last line; else
    the phase's end."""
    outcome = agent.get("outcome") or {}
    if parse_time(outcome.get("ended_at")):
        return parse_time(outcome["ended_at"])
    _, phase_end = agent_window(pair["result"], agent)
    if pair["result"].get("driver") == "session" and phase_end:
        return phase_end
    return found.get("last_moment") or phase_end


# Joining the transcript's coordination calls to the hub's record.

def coordination_tool(name):
    """The arm tool an MCP tool name calls, or None for any other tool."""
    match = MCP_TOOL.match(name or "")
    if match and COORDINATION_SERVER.search(match.group(1)) and match.group(2) in COORDINATION_TOOLS:
        return match.group(2)
    return None


def unwrap_docker(command):
    """The command inside `docker exec [options] <container> sh|bash -c|-lc
    <cmd>`, which is how the session driver's subagent runs every command
    in its container; any other command as it is."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if words[:2] != ["docker", "exec"]:
        return command
    index = 2
    while index < len(words) and words[index].startswith("-"):
        takes_value = words[index] in ("-w", "--workdir", "-u", "--user", "-e", "--env")
        index += 2 if takes_value else 1
    rest = words[index + 1:]
    if len(rest) >= 3 and Path(rest[0]).name in ("sh", "bash") and rest[1] in ("-c", "-lc", "-cl"):
        return rest[2]
    return command


def segment_heads(command):
    """The first word of each simple command in a shell command line, split
    on ;, &&, || and |, with variable assignments skipped, so a command
    named only inside a quoted string is not taken for one that runs."""
    heads = []
    for segment in re.split(r"&&|\|\||[;|\n]", command or ""):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        words = [w for w in words if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", w)]
        if words:
            heads.append((Path(words[0]).name, words))
    return heads


def alias_tool(word):
    tool = ALIASES.get(word) or (word[len("coord-"):] if word.startswith("coord-") else None)
    return tool if tool in COORDINATION_TOOLS else None


def aliases_in(command):
    """The coordination commands a shell command line runs, in order, by
    tool."""
    return [alias_tool(head) for head, _ in segment_heads(command) if alias_tool(head)]


def first_alias(command):
    """The first coordination command a shell command line runs, as (tool,
    its words after the command), or (None, None)."""
    for head, words in segment_heads(command):
        if alias_tool(head):
            return alias_tool(head), words[1:]
    return None, None


def expected_tools(call):
    """The hub tools a raw call should have reached, and by which way."""
    if call["kind"] != "tool":
        return [], None
    tool = coordination_tool(call["tool"])
    if tool:
        return [tool], "mcp"
    if call["tool"] == "Bash":
        return aliases_in(unwrap_docker(call["input"].get("command") or "")), "shell"
    return [], None


def join_hub(calls_by_agent, hub, warnings):
    """Copy each hub call onto the raw call that made it, matching by
    agent, tool and time, or by order alone when the call has no time;
    what is left on either side goes into warnings. An agent given None
    has no transcript to join, and its calls come from the hub alone."""
    for agent_id, calls in calls_by_agent.items():
        if calls is None:
            continue
        mine = [h for h in hub if h["agent"] == agent_id]
        for call in calls:
            tools, via = expected_tools(call)
            for tool in tools:
                match = best_hub_call(call, tool, via, mine)
                if match is None:
                    warnings.append(f"{agent_id}: a {tool} call at {utc_iso(call['ts']) or 'an unknown time'} "
                                    "has no hub event")
                    continue
                match["joined"] = True
                call["hub"].append(match)
        left = [h for h in mine if not h["joined"]]
        for tool in sorted({h["tool"] for h in left}):
            count = sum(1 for h in left if h["tool"] == tool)
            warnings.append(f"{agent_id}: {count} hub {tool} call(s) match no transcript step")


def best_hub_call(call, tool, via, candidates):
    free = [h for h in candidates if not h["joined"] and h["tool"] == tool and (h["via"] in (None, via))]
    if call["ts"] is None or all(h["abs_ts"] is None for h in free):
        return free[0] if free else None
    begin = call["ts"].timestamp() - JOIN_SLACK_SECONDS
    end = (call["end"] or call["ts"]).timestamp() + JOIN_SLACK_SECONDS
    inside = [h for h in free if h["abs_ts"] is not None and begin <= h["abs_ts"] <= end]
    return min(inside, key=lambda h: (abs(h["abs_ts"] - call["ts"].timestamp()), h["seq"])) if inside else None


def hub_only_calls(hub, agent_id):
    """Raw calls built from the hub's record alone, for an agent with no
    transcript and no stream: the coordination calls at their real times."""
    calls = []
    for order, record in enumerate(h for h in hub if h["agent"] == agent_id):
        moment = parse_time(record["abs_ts"])
        done = record["done"] or {}
        took = number(done.get("duration_ms"))
        end = parse_time(record["abs_ts"] + took / 1000) if moment and took is not None else None
        call = new_call("tool", f"mcp__coordbench__{record['tool']}", hub_input(record), None, moment, None,
                        order, False, "")
        call.update({"end": end, "hub": [record], "is_error": bool(done.get("is_error")), "from_hub": True})
        record["joined"] = True
        calls.append(call)
    return calls


def hub_input(record):
    sent = record["messages"]
    if not sent:
        return {}
    text = sent[0].get("content")
    return {"to": sent[0].get("to"), "text": text} if record["tool"] == "send_message" else {"text": text}


# Mapping raw calls onto the viewer's vocabulary.

def hub_summary(record):
    done = record.get("done") or {}
    return {"seq": record["seq"], "abs_ts": record["abs_ts"], "duration_ms": done.get("duration_ms"),
            "blocked_ms": done.get("blocked_ms"), "is_error": done.get("is_error"), "cancelled": done.get("cancelled")}


def base_step(call, lane, function_name, action, args, tool_type, message="", observation=None):
    step = {"timestamp": utc_iso(call["ts"]), "ended_at": utc_iso(call["end"]),
            "duration_ms": milliseconds_between(call["ts"], call["end"]), "source": "agent", "agentId": lane,
            "action": action, "args": args, "message": clip(message, OBSERVATION_LIMIT),
            "observation": clip(call["result"] if observation is None else observation, OBSERVATION_LIMIT),
            "thought": clip(call["thought"], THOUGHT_LIMIT),
            "tool_call_metadata": {"function_name": function_name, "tool_use_id": call["tool_use_id"]},
            "toolName": function_name, "toolType": "other" if call["subagent"] else tool_type,
            "is_error": call["is_error"],
            "coordbench": {"tool": call["tool"] if call["kind"] == "tool" else "thinking", "input": call["input"]}}
    if call["hub"]:
        step["coordbench"]["hub"] = hub_summary(call["hub"][0])
        if len(call["hub"]) > 1:
            step["coordbench"]["hub_calls"] = [hub_summary(record) for record in call["hub"]]
    if call["subagent"]:
        step["coordbench"]["subagent"] = True
    if call.get("from_hub"):
        step["coordbench"]["from_hub"] = True
    step["_model"] = call["model"]
    step["_order"] = call["order"]
    return step


def map_call(call, lane):
    """One raw call as viewer steps: one step, or for a receive one per
    message it handed over."""
    if call["kind"] == "think":
        thought = call["input"]["thought"]
        return [base_step(call, lane, "think", "think", {"thought": thought}, "think", thought, "")]
    tool = coordination_tool(call["tool"])
    if tool:
        return coordination_steps(call, lane, tool, call["input"], call["result"])
    if call["tool"] == "Bash":
        return bash_steps(call, lane)
    mapper = TOOL_MAPPERS.get(call["tool"])
    if mapper:
        return [mapper(call, lane)]
    name = (call["tool"] or "tool").lower()
    return [base_step(call, lane, name, name, call["input"], "other", f"{call['tool']} call")]


def bash_steps(call, lane):
    """A shell command as one step; one that runs a coordination command
    reads as that tool, the session driver's docker exec unwrapped."""
    command = unwrap_docker(call["input"].get("command") or "")
    tool, words = first_alias(command)
    if tool:
        return coordination_steps(call, lane, tool, alias_input(tool, words), bash_output(call))
    args = {"command": command, "description": call["input"].get("description"),
            "timeout": call["input"].get("timeout")}
    output = bash_output(call)
    return [base_step(call, lane, "execute_bash", "run", args, bash_type(command), f"Running command: {command}",
                      output)]


def bash_type(command):
    lowered = (command or "").lower()
    return "find_grep" if "grep" in lowered or "find " in lowered or "locate" in lowered else "bash"


def bash_output(call):
    extra = call["extra"]
    if not isinstance(extra, dict) or "stdout" not in extra:
        return call["result"] or ""
    output = extra.get("stdout") or ""
    if extra.get("stderr"):
        output = f"{output}\n[stderr]\n{extra['stderr']}" if output else f"[stderr]\n{extra['stderr']}"
    return output


# Each command's positional arguments and flags, as the arms declare them
# (argv and flags in kit.py's messaging_tools and the team and sdlc arms'
# tools), so a shell call's words read back as the arguments a tool call
# would have carried.
ALIAS_ARGV = {"send_message": ("to", "text..."), "broadcast": ("text...",), "task_create": ("title...",),
              "task_claim": ("task_id",), "task_update": ("task_id", "status"),
              "request": ("recipient", "kind", "body..."), "respond": ("request_id", "body..."), "claim_issue": ("issue",), "open_pull_request": ("issue",),
              "mark_ready": ("issue",), "pull_request_status": ("issue",)}
ALIAS_FLAGS = {"--assign": "assign", "-n": "note", "--note": "note", "--wait": "wait"}
ALIAS_SWITCHES = {"--mine": "mine", "--open": "open"}


def alias_input(tool, words):
    """The arguments a coordination command was given, from its words."""
    arguments, positional, index = {}, [], 0
    while index < len(words):
        word = words[index]
        if word in ALIAS_SWITCHES:
            arguments[ALIAS_SWITCHES[word]] = True
        elif word in ALIAS_FLAGS and index + 1 < len(words):
            arguments[ALIAS_FLAGS[word]] = words[index + 1]
            index += 1
        else:
            positional.append(word)
        index += 1
    names = ALIAS_ARGV.get(tool, ())
    for position, name in enumerate(names):
        if name.endswith("..."):
            arguments[name[:-3]] = " ".join(positional[position:])
            break
        if position < len(positional):
            arguments[name] = positional[position]
    return arguments


def read_step(call, lane):
    tool_input = call["input"]
    args = {"command": "view", "path": tool_input.get("file_path")}
    if number(tool_input.get("offset")) is not None or number(tool_input.get("limit")) is not None:
        offset = number(tool_input.get("offset")) or 0
        args["view_range"] = [offset, offset + (number(tool_input.get("limit")) or 0)]
    extra = call["extra"] if isinstance(call["extra"], dict) else {}
    content = (extra.get("file") or {}).get("content") if isinstance(extra.get("file"), dict) else None
    observation = content if content is not None else call["result"] or ""
    return base_step(call, lane, "str_replace_editor", "read", args, "view", f"Reading file: {args['path']}",
                     observation)


def edit_step(call, lane):
    tool_input = call["input"]
    args = {"command": "str_replace", "path": tool_input.get("file_path"), "old_str": tool_input.get("old_string"),
            "new_str": tool_input.get("new_string"), "replace_all": tool_input.get("replace_all")}
    return base_step(call, lane, "str_replace_editor", "edit", args, "edit", f"Editing file: {args['path']}")


def multi_edit_step(call, lane):
    edits = [edit for edit in call["input"].get("edits") or [] if isinstance(edit, dict)]
    args = {"command": "str_replace", "path": call["input"].get("file_path"),
            "old_str": "\n...\n".join(edit.get("old_string") or "" for edit in edits),
            "new_str": "\n...\n".join(edit.get("new_string") or "" for edit in edits)}
    return base_step(call, lane, "str_replace_editor", "edit", args, "edit", f"Editing file: {args['path']}")


def notebook_step(call, lane):
    args = {"command": "str_replace", "path": call["input"].get("notebook_path"),
            "new_str": call["input"].get("new_source")}
    return base_step(call, lane, "str_replace_editor", "edit", args, "edit", f"Editing notebook: {args['path']}")


def write_step(call, lane):
    content = call["input"].get("content")
    args = {"command": "create", "path": call["input"].get("file_path"), "file_text": content, "new_str": content}
    extra = call["extra"] if isinstance(call["extra"], dict) else {}
    tool_type = "create" if extra.get("type") == "create" else "edit"
    return base_step(call, lane, "str_replace_editor", "edit", args, tool_type, f"Writing file: {args['path']}")


def search_step(call, lane):
    """Grep and Glob as a readable line marked as no shell command; Glob
    reads "find" so the stock viewer's own rule types it as the search
    it is."""
    tool_input = call["input"]
    where = tool_input.get("path") or "."
    if call["tool"] == "Grep":
        command = f"[Grep] {tool_input.get('pattern')} in {where}"
        if tool_input.get("glob"):
            command += f" glob={tool_input['glob']}"
    else:
        command = f"[Glob] find {tool_input.get('pattern')} in {where}"
    return base_step(call, lane, "execute_bash", "run", {"command": command}, "find_grep", command)


def todo_step(call, lane):
    todos = [todo for todo in call["input"].get("todos") or [] if isinstance(todo, dict)]
    task_list = [{"id": todo.get("id") or str(index), "title": todo.get("content"),
                  "status": TODO_STATUS.get(todo.get("status"), todo.get("status"))}
                 for index, todo in enumerate(todos, 1)]
    return base_step(call, lane, "task_tracker", "task_tracking", {"command": "plan", "task_list": task_list},
                     "task_tracking", f"Managing {len(task_list)} task items.")


TOOL_MAPPERS = {"Read": read_step, "Edit": edit_step, "MultiEdit": multi_edit_step,
                "NotebookEdit": notebook_step, "Write": write_step, "Grep": search_step, "Glob": search_step,
                "TodoWrite": todo_step}


def coordination_steps(call, lane, tool, tool_input, output):
    output = output or ""
    if tool in SEND_TOOLS:
        return [send_step(call, lane, tool, tool_input, output)]
    if tool in RECEIVE_TOOLS:
        return receive_steps(call, lane, output)
    if tool in TEAM_TASK_TOOLS:
        return [team_task_step(call, lane, tool, tool_input, output)]
    if tool in ("request", "respond"):
        return [request_step(call, lane, tool, tool_input, output)]
    if tool == "pending":
        return [base_step(call, lane, "openhands_comm_get", "ReceiveMessageAction", {"content": output},
                          "communication", output, output)]
    if tool in ("peek_messages", "list_agents"):
        name = "coop_peek" if tool == "peek_messages" else "coop_agents"
        return [base_step(call, lane, name, name, tool_input, "other", output, output)]
    return [base_step(call, lane, f"sdlc_{tool}", f"sdlc_{tool}", tool_input, "other", output, output)]


def delivered(call):
    return [message for record in call["hub"] for message in record["messages"]]


def send_step(call, lane, tool, tool_input, output):
    deliveries = delivered(call)
    text = next((d.get("content") for d in deliveries if d.get("content") is not None),
                tool_input.get("text") or tool_input.get("content") or "")
    to = "broadcast" if tool == "broadcast" else (deliveries[0].get("to") if deliveries else tool_input.get("to"))
    step = base_step(call, lane, "openhands_comm_send", "SendMessageAction", {"content": text, "to": to},
                     "communication", text, output)
    if deliveries:
        step["coordbench"]["message_ids"] = [d.get("message_id", d.get("seq")) for d in deliveries]
    if deliveries and all(d.get("content") is None for d in deliveries) and not text:
        step["coordbench"]["text_recorded"] = False
    return step


def handed_over(call, messages_by_id):
    """The messages a receive handed over: from the hub's receipts when
    the hub recorded which; as many unknown messages as a pilot's receipt
    counted, when it recorded only how many; else read back from the
    tool's output."""
    receipts = [event for record in call["hub"] for event in record["receipts"] if event.get("kind") == "receive"]
    ids = [i for event in receipts for i in event.get("message_ids") or ()]
    if ids and all(i in messages_by_id for i in ids):
        return [messages_by_id[i] for i in ids]
    output = call["result"] if call["tool"] != "Bash" else bash_output(call)
    parsed = parsed_messages(output)
    counted = sum(number(event.get("count")) or 0 for event in receipts)
    if not parsed and counted and not ids:
        return [{"id": None, "from": None, "content": None}] * int(counted)
    return parsed


def parsed_messages(output):
    """The messages a receive printed: coop-recv's JSON list or
    wait_for_message's one object; nothing for any other text."""
    try:
        value = json.loads(output or "")
    except ValueError:
        return []
    entries = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [{"id": None, "from": e.get("from"), "content": e.get("content")} for e in entries
            if isinstance(e, dict) and "content" in e]


def receive_steps(call, lane, output):
    messages = handed_over(call, call.get("_messages_by_id") or {})
    if not messages:
        return [base_step(call, lane, "openhands_comm_get", "ReceiveMessageAction",
                          {"content": NO_NEW_MESSAGES, "from": None}, "communication", NO_NEW_MESSAGES, output)]
    steps = []
    for message in messages:
        text = message.get("content")
        step = base_step(call, lane, "openhands_comm_get", "ReceiveMessageAction",
                         {"content": text, "from": message.get("from")}, "communication", text or "", text or "")
        if message.get("id") is not None:
            step["coordbench"]["message_ids"] = [message["id"]]
        if text is None:
            step["coordbench"]["text_recorded"] = False
        steps.append(step)
    return steps


def task_list_of(tool, tool_input, output):
    """The tasks a team tool's answer shows, in the stock viewer's task
    card shape: task_list's JSON list, or the one task the call touched."""
    try:
        value = json.loads(output)
    except ValueError:
        value = None
    if isinstance(value, list):
        return [{"id": t.get("id"), "title": t.get("title"),
                 "status": TASK_STATUS.get(t.get("status"), t.get("status"))} for t in value if isinstance(t, dict)]
    if tool == "task_create":
        return [{"id": output.strip() or None, "title": tool_input.get("title"), "status": "todo"}]
    task_id = tool_input.get("task_id") or (tool_input.get("argv") or [None])[0]
    status = "in_progress" if tool == "task_claim" else TASK_STATUS.get(tool_input.get("status"), tool_input.get("status"))
    return [{"id": task_id, "title": tool_input.get("note") or task_id, "status": status}] if task_id else []


def team_task_step(call, lane, tool, tool_input, output):
    args = {"command": "plan", "task_list": task_list_of(tool, tool_input, output), "tool": tool}
    return base_step(call, lane, "task_tracker", "task_tracking", args, "task_tracking", output, output)


def request_step(call, lane, tool, tool_input, output):
    body = tool_input.get("body") or ""
    if tool == "request":
        args = {"content": body, "to": tool_input.get("recipient"), "request_kind": tool_input.get("kind"),
                "request_id": output.strip().splitlines()[0] if output.strip() else None}
    else:
        args = {"content": body, "to": None, "request_id": tool_input.get("request_id")}
    return base_step(call, lane, "openhands_comm_send", "SendMessageAction", args, "communication", body, output)


def finish_step(agent, found, lane):
    """The agent's last step: what it said when it ended, at its end."""
    result_event = found.get("result_event") or {}
    message = result_event.get("result") if isinstance(result_event.get("result"), str) else found["last_text"]
    moment = found.get("end")
    call = new_call("finish", "finish", {}, None, moment, None, 10 ** 9, False, "")
    call["end"] = moment
    step = base_step(call, lane, "finish", "FinishAction", {"message": message or ""}, "submit", message or "", "")
    step["coordbench"] = {"tool": "finish", "input": {}, "status": agent["record"].get("status")}
    return step


# The agents' steps, numbered after one sort.

def stock_viewer_drops(step):
    """viewer.astro's isSystemPrompt: the first non-empty of content, text
    and message, tested for the five prompt markers."""
    text = step.get("content") or step.get("text") or step.get("message") or ""
    return isinstance(text, str) and any(marker in text for marker in STOCK_VIEWER_FILTERS)


def get_tool_type(step):
    """A port of viewer.astro's getToolType (854-888), the stock viewer's
    rule where a step carries no toolType of its own."""
    name = (step.get("tool_call_metadata") or {}).get("function_name") or step.get("toolName") or ""
    args = step.get("args") or {}
    if name == "finish":
        return "submit"
    if name in ("openhands_comm_send", "openhands_comm_get"):
        return "communication"
    if name == "task_tracker":
        return "task_tracking"
    if name == "think":
        return "think"
    if name == "execute_bash":
        return bash_type(args.get("command") or "")
    if name == "str_replace_editor":
        return {"view": "view", "create": "create"}.get(args.get("command"), "edit")
    return "other"


def agent_steps(agent, found, messages_by_id):
    lane = agent["lane"]
    calls = found["calls"]
    steps = []
    for call in calls:
        call["_messages_by_id"] = messages_by_id
        steps.extend(map_call(call, lane))
    if found.get("end") or calls or found.get("result_event"):
        steps.append(finish_step(agent, found, lane))
    return steps


def number_steps(steps_by_agent):
    """Every agent's steps in one list sorted by time, then by agent and
    file order, numbered 0..n-1; a step with no time sorts last."""
    ordered = []
    for index, steps in enumerate(steps_by_agent):
        for position, step in enumerate(steps):
            moment = parse_time(step["timestamp"])
            ordered.append(((0, moment.timestamp()) if moment else (1, 0), index, position, step))
    ordered.sort(key=lambda entry: entry[:3])
    numbered = []
    for number_, (_, _, _, step) in enumerate(ordered):
        numbered.append({"id": number_, **step})
    return numbered


def mark_models(steps, lanes_with_model):
    """The model on each agent's first assistant-derived step, where the
    stock viewer looks first (viewer.astro:635-640)."""
    seen = set()
    for step in steps:
        model = step.pop("_model", None)
        step.pop("_order", None)
        if step["agentId"] in seen or not model:
            continue
        seen.add(step["agentId"])
        step["tool_call_metadata"]["model_response"] = {"model": model}
    for lane, model in lanes_with_model.items():
        if lane not in seen and model:
            first = next((s for s in steps if s["agentId"] == lane), None)
            if first:
                first["tool_call_metadata"]["model_response"] = {"model": model}


def add_llm_metrics(steps, agents_meta):
    """llm_metrics on each agent's last step: its cost (left out when
    null, since the stock viewer prints 0 as N/A and adds it to the
    total), its token totals and its model."""
    for meta in agents_meta:
        last = next((s for s in reversed(steps) if s["agentId"] == meta["lane"]), None)
        if last is None:
            continue
        metrics = {"accumulated_token_usage": token_usage(meta["tokens"]), "model": meta["model"]}
        if meta["cost"] is not None:
            metrics = {"accumulated_cost": meta["cost"], **metrics}
        last["llm_metrics"] = metrics


# The trajectory object.

def features_text(result):
    return "_".join(f"feature{f}" for f in feature_numbers(result))


def agent_meta(agent, found, result):
    record = agent["record"]
    usage = found.get("usage") or record_usage(record)
    model = record.get("model") if agent_model_known(record) else None
    model = model or (found["models"][0] if found.get("models") else None) or result.get("model")
    result_event = found.get("result_event") or {}
    return {"lane": agent["lane"], "id": agent["id"], "feature_id": agent["feature_id"], "role": agent["role"],
            "model": model, "status": record.get("status"), "steps": number(record.get("steps")),
            "tokens": usage, "cost": number(record.get("cost")), "duration_ms": number(record.get("duration_ms")),
            "api_duration_ms": number(record.get("api_duration_ms")), "started_at": utc_iso(agent_start(result, agent, found)),
            "ended_at": utc_iso(found.get("end")), "model_usage": result_event.get("modelUsage")}


def agent_start(result, agent, found):
    """When the agent began: its "agent" phase in the timings, else its
    transcript's first line (the prompt), else unknown, since a run's own
    start includes the harness's setup and the first hub call comes after
    the agent has read its task."""
    record = phase_record(result, "agent", agent["id"])
    if record and parse_time(record.get("started")):
        return parse_time(record["started"])
    return found.get("first_moment")


def agent_model_known(record):
    return isinstance(record.get("model"), str) and bool(record.get("model"))


def stock_agent_block(meta, record):
    return {"model": meta["model"], "steps": number(record.get("steps")), "cost": meta["cost"],
            "input_tokens": number(record.get("input_tokens")), "output_tokens": number(record.get("output_tokens"))}


def outcome_of(evaluation):
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    merge = evaluation.get("merge") if isinstance(evaluation.get("merge"), dict) else {}
    cascade = evaluation.get("cascade") if isinstance(evaluation.get("cascade"), dict) else {}
    return {"both_passed": evaluation.get("both_passed") if evaluation else None,
            "merge_status": merge.get("status"), "merge_strategy": merge.get("strategy"),
            "apply_status": evaluation.get("apply_status"), "has_naive_merge_conflict": naive_conflict(merge, cascade),
            "paper_both_passed": cascade.get("verdict_paper_era"), "cascade_route": cascade.get("route")}


def naive_conflict(merge, cascade):
    if isinstance(cascade.get("has_naive_merge_conflict"), bool):
        return cascade["has_naive_merge_conflict"]
    return has_conflict(merge)


def has_conflict(merge):
    """The website's hasConflict: a conflicted naive merge, or None when
    the merge never happened (no merge, an error, a missing patch)."""
    status = (merge or {}).get("status")
    if status in (None, "error", "missing_input"):
        return None
    return status == "conflicts"


def total_cost(metas):
    costs = [meta["cost"] for meta in metas if meta["cost"] is not None]
    return sum(costs) if costs else None


def timestamps_kind(found_by_agent):
    """Where the run's step times came from: session-jsonl only when every
    agent's did, interpolated when any agent's were spread evenly, and
    hub-events when the hub's record was all there was."""
    kinds = {found["kind"] or "hub-events" for found in found_by_agent} or {"hub-events"}
    if kinds == {"session-jsonl"}:
        return "session-jsonl"
    return "interpolated" if "interpolated" in kinds else "hub-events"


def stock_viewer_note(agents):
    if len(agents) <= 2:
        return None
    return (f"The stock viewer's run panel reads only agent_1 and agent_2 and folds every later lane into lane "
            f"B; this run has {len(agents)} agents, so their tokens and cost are only in metadata.coordbench "
            "and every agent's cost is in total_cost.")


def build_trajectory(pair_dir, cooperbench_dir=None, cause=None):
    """The trajectory object of one pair directory."""
    pair = load_pair(pair_dir)
    read_hub(pair)
    found_by_agent = [collect_agent(pair, agent) for agent in pair["agents"]]
    join_hub({a["id"]: f["calls"] if f["kind"] else None for a, f in zip(pair["agents"], found_by_agent)},
             pair["hub"], pair["warnings"])
    for agent, found in zip(pair["agents"], found_by_agent):
        if found["kind"] is None:
            found["calls"] = hub_only_calls(pair["hub"], agent["id"])
    by_id = {m["id"]: m for m in pair["messages"] if m["id"] is not None}
    steps = number_steps([agent_steps(agent, found, by_id) for agent, found in zip(pair["agents"], found_by_agent)])
    metas = [agent_meta(a, f, pair["result"]) for a, f in zip(pair["agents"], found_by_agent)]
    mark_models(steps, {meta["lane"]: meta["model"] for meta in metas})
    add_llm_metrics(steps, metas)
    metadata = stock_metadata(pair["result"], pair, metas)
    metadata["coordbench"] = coordbench_metadata(pair, metas, found_by_agent, steps, cooperbench_dir, cause)
    return {"metadata": metadata, "steps": steps}


def read_hub(pair):
    """The hub's record on the pair: its clock, its calls and its messages,
    or for a CooperBench run, which has no events, conversation.json's."""
    events = pair["events"]
    pair["anchor"], pair["anchor_source"] = hub_anchor(events, pair["result"])
    pair["hub"] = hub_calls(events, pair["anchor"])
    pair["messages"] = hub_messages(events, pair["anchor"]) if events else conversation_messages(pair["conversation"])
    pair["warnings"] = []


def stock_metadata(result, pair, metas):
    metadata = {"repo": result.get("repo"), "task_id": f"task{result.get('task_id')}",
                "features": features_text(result)}
    for agent, meta in zip(pair["agents"], metas):
        metadata[agent["lane"]] = stock_agent_block(meta, agent["record"])
    cost = total_cost(metas)
    if cost is not None:
        metadata["total_cost"] = cost
    metadata["started_at"] = utc_iso(parse_time(result.get("started_at")))
    metadata["duration_seconds"] = number(result.get("duration_seconds"))
    return metadata


def coordbench_metadata(pair, metas, found_by_agent, steps, cooperbench_dir, cause):
    result, messages = pair["result"], pair["messages"]
    block = {"format": FORMAT, "run_name": result.get("run_name"), "run_id": result.get("run_id"),
             "arm": arm_of(result), "driver": driver_of(result), "setting": result.get("setting"),
             "feature_ids": feature_numbers(result),
             "model": result.get("model"), "agents": metas, "outcome": outcome_of(pair["evaluation"]),
             "timestamps": timestamps_kind(found_by_agent), "anchor": pair["anchor_source"],
             "sources": sorted(set(pair["sources"])), "prompt_files": prompt_files(pair["dir"], pair["agents"]),
             "join_warnings": pair["warnings"], "filtered_by_stock_viewer": [s["id"] for s in steps if stock_viewer_drops(s)],
             "messages": messages, "messages_recorded": all(m["content"] is not None for m in messages),
             "hub_events": hub_events_on_clock(pair["events"], pair["anchor"])}
    note = stock_viewer_note(pair["agents"])
    if note:
        block["stock_viewer_note"] = note
    if pair["judge"] is not None:
        block["failure_modes"] = pair["judge"][1]
    info = features_info(result, cooperbench_dir or cooperbench_home(pair))
    if info:
        block["features_info"] = info
    if cause:
        block["cause"] = cause
    return block


def arm_of(result):
    return result.get("arm") or result.get("setting")


def driver_of(result):
    return result.get("driver") or result.get("agent_framework")


# The dataset beside the run: feature titles by the website's own rule,
# and the spec audit's verdict on each feature.

def cooperbench_home(pair):
    plan = read_json(pair["dir"] / "plan.json") or {}
    for candidate in (plan.get("cooperbench_dir"), os.environ.get("COOPERBENCH_DIR"), Path.home() / "cooperbench"):
        if candidate and (Path(candidate) / "dataset").is_dir():
            return Path(candidate)
    return None


def extract_title(content):
    """generate_tasks_json.py's extract_title, the website's rule."""
    lines = content.strip().split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("**Title**:"):
            return line.replace("**Title**:", "").strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("**Title") and ":" in line:
            return line.split(":", 1)[1].strip()
    for line in lines[:5]:
        if line.strip() and not line.startswith("#"):
            return line.strip()[:100]
    return "Untitled Feature"


def features_info(result, cooperbench_dir):
    dataset = Path(cooperbench_dir) / "dataset" if cooperbench_dir else None
    if dataset is None or not dataset.is_dir():
        return None
    verdicts = read_json(dataset / ".spec_audit_verdicts.json") or {}
    task = f"task{result.get('task_id')}"
    info = []
    for feature in feature_numbers(result):
        spec = read_text(dataset / str(result.get("repo")) / task / f"feature{feature}" / "feature.md")
        verdict = verdicts.get(f"{result.get('repo')}/{task}|feature{feature}")
        info.append({"feature": feature, "title": extract_title(spec) if spec else None,
                     "spec_audit": {"verdict": verdict[0], "note": verdict[1]} if isinstance(verdict, list)
                     and len(verdict) == 2 else None})
    return info


# The pair directory's own files.

def write_gzip_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, separators=COMPACT).encode("utf-8")
    path.write_bytes(gzip.compress(data, compresslevel=GZIP_LEVEL, mtime=0))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def runner_trajectory_files(pair_dir, result):
    """agent<feature>_traj.json (or solo_traj.json) as CooperBench's runner
    writes them, for each agent whose transcript is there and whose file
    is not: run.py writes them for a new run, this fills an older one."""
    written = []
    for name, record in agent_entries(result):
        session = read_text(pair_dir / f"{name}_session.jsonl")
        if not session:
            continue
        feature = agent_feature(record, result)
        file_name = "solo_traj.json" if result.get("setting") == "solo" else f"agent{feature}_traj.json"
        if (pair_dir / file_name).exists():
            continue
        body = {"repo": result.get("repo"), "task_id": result.get("task_id")}
        body.update({"features": feature} if result.get("setting") == "solo" else {"feature_id": feature})
        body.update({"agent_id": name, "model": result.get("model"), "status": record.get("status"),
                     "cost": record.get("cost"), "steps": record.get("steps"),
                     "messages": parse_session_jsonl(session)})
        if record.get("team_role"):
            body["team_role"] = record["team_role"]
        write_json(pair_dir / file_name, body)
        written.append(file_name)
    return written


def write_pair(pair_dir, cooperbench_dir=None, cause=None):
    """The pair directory's own export: trajectory.json.gz and any missing
    runner trajectory file. Returns the trajectory object."""
    pair_dir = Path(pair_dir)
    trajectory = build_trajectory(pair_dir, cooperbench_dir, cause)
    write_gzip_json(pair_dir / "trajectory.json.gz", trajectory)
    runner_trajectory_files(pair_dir, read_json(pair_dir / "result.json") or {})
    return trajectory


# The website's layout.

def slug(text):
    return re.sub(r"[^a-z0-9]", "_", str(text).lower())


def model_key(trajectory):
    block = trajectory["metadata"]["coordbench"]
    return slug(f"{block.get('model')}_{block.get('arm')}_{block.get('driver')}")


def display_name(trajectory):
    block = trajectory["metadata"]["coordbench"]
    return f"{block.get('model')} (coordbench {block.get('arm')}, {block.get('driver')})"


def setting_dir(trajectory):
    return "coop_git" if trajectory["metadata"]["coordbench"].get("arm") in GIT_ARMS else "coop"


def canonical_repo(repo):
    return REPO_ALIASES.get(repo, repo)


def trajectory_file_name(repo, task_id, features):
    return f"{repo}_{task_id}_{features}.json.gz"


def viewer_url(key, repo, task_id, features, setting):
    url = f"/viewer?model={key}&repo={repo}&task={task_id}&features={features}"
    return url + "&setting=coopGit" if setting == "coop_git" else url


def index_row(trajectory, key):
    metadata = trajectory["metadata"]
    outcome = metadata["coordbench"]["outcome"]
    parts = metadata["features"].split("_")
    passed = outcome["both_passed"] is True
    conflict = has_conflict({"status": outcome["merge_status"]}) if outcome["both_passed"] is not None else None
    result = {"passed": passed, "hasConflict": conflict}
    if outcome["both_passed"] is None:
        result["error"] = True
    return {"repo": metadata["repo"], "taskId": metadata["task_id"], "features": metadata["features"],
            "f1": parts[0] if parts else None, "f2": parts[1] if len(parts) > 1 else None,
            "results": {key: result}, "hasTrajectory": {key: True}}


def row_key(row):
    return (canonical_repo(row.get("repo")), row.get("taskId"), row.get("features"))


def merge_index(index, rows, key, display):
    """index.json with the rows merged in as write_index merges them: by
    (repo, taskId, features), the repo through REPO_ALIASES so a typst
    pair meets the paper models' typst rows. The model's stats are counted
    from every row it has, so merging twice changes nothing; rate is
    rounded to one decimal, as write_index rounds it."""
    index = json.loads(json.dumps(index or {}))
    models = index.setdefault("models", [])
    if key not in models:
        models.append(key)
    index.setdefault("modelDisplayNames", {})[key] = display
    lookup = {row_key(row): row for row in index.setdefault("tasks", [])}
    for row in rows:
        existing = lookup.get(row_key(row))
        if existing is None:
            index["tasks"].append(row)
            lookup[row_key(row)] = row
            continue
        existing.setdefault("results", {})[key] = row["results"][key]
        existing.setdefault("hasTrajectory", {})[key] = row["hasTrajectory"][key]
    index["tasks"].sort(key=lambda row: (row.get("repo") or "", row.get("taskId") or "", row.get("features") or ""))
    index.setdefault("stats", {})[key] = model_stats(index["tasks"], key)
    return index


def model_stats(rows, key):
    results = [row["results"][key] for row in rows if key in (row.get("results") or {})]
    total, passed = len(results), sum(1 for r in results if r.get("passed") is True)
    return {"total": total, "passed": passed, "rate": round(passed / total * 100, 1) if total else 0}


def row_repo(index, row):
    """The repo name the index's row for this pair carries, which may be
    the alias: the stock viewer builds its URL from it."""
    for existing in index.get("tasks", []):
        if row_key(existing) == row_key(row):
            return existing.get("repo")
    return row.get("repo")


def legacy_detail(trajectory, evaluation):
    """One detailed_results entry of 59bbf65's aggregated_results, from the
    cascade block, under 63b9d44's pass rule (f1/f2) and with the paper
    era's regex rule beside it."""
    cascade = evaluation["cascade"]
    features = trajectory["metadata"]["coordbench"]["feature_ids"]
    entry = {"feature1_id": features[0], "feature2_id": features[1] if len(features) > 1 else None,
             "has_naive_merge_conflict": cascade.get("has_naive_merge_conflict")}
    for step in ("naive", "union", "llm"):
        record = cascade.get(step) if isinstance(cascade.get(step), dict) else {}
        for side in ("1", "2"):
            entry[f"feature{side}_{step}_merge_test_passed"] = record.get(f"f{side}")
            entry[f"feature{side}_{step}_merge_test_passed_regex"] = record.get(f"f{side}_regex")
    return entry


def merge_legacy(existing, trajectory, setting, entry):
    """aggregated_results_<model>_k1.json for one task with the entry
    merged in by its feature pair, and the totals evaluate.py computes."""
    document = json.loads(json.dumps(existing or {}))
    details = [d for d in document.get("detailed_results", [])
               if (d.get("feature1_id"), d.get("feature2_id")) != (entry["feature1_id"], entry["feature2_id"])]
    details.append(entry)
    details.sort(key=lambda d: (d.get("feature1_id") or 0, d.get("feature2_id") or 0))
    metadata = trajectory["metadata"]
    task_id = int(metadata["task_id"][len("task"):])
    document.update({"setting": setting, "repo_name": metadata["repo"], "task_id": task_id, "k": 1, "pass_rule": "63b9d44 (exit code and a passed count); *_regex keys are 59bbf65's"})
    document.update(cascade_totals(details))
    document["detailed_results"] = details
    return document


def cascade_totals(details):
    naive = union = llm = 0
    for d in details:
        naive_ok = d["has_naive_merge_conflict"] is False and bool(d["feature1_naive_merge_test_passed"]) \
            and bool(d["feature2_naive_merge_test_passed"])
        union_ok = bool(d["feature1_union_merge_test_passed"]) and bool(d["feature2_union_merge_test_passed"]) \
            and not naive_ok
        llm_ok = bool(d["feature1_llm_merge_test_passed"]) and bool(d["feature2_llm_merge_test_passed"]) \
            and not naive_ok and not union_ok
        naive, union, llm = naive + naive_ok, union + union_ok, llm + llm_ok
    total = len(details)
    pct = (lambda count: count / total * 100.0) if total else (lambda count: 0.0)
    return {"total_tasks": total, "naive_merge_success": naive, "naive_merge_success_pct": pct(naive),
            "union_merge_success": naive + union, "union_merge_success_pct": pct(naive + union),
            "llm_merge_success": naive + union + llm, "llm_merge_success_pct": pct(naive + union + llm)}


def seven_key_steps(trajectory):
    """The cause examples' step shape (index_cause_trajectories.py's
    transform_step): communication keeps its text in details, no args."""
    steps = []
    for step in trajectory["steps"]:
        moment = parse_time(step.get("timestamp"))
        communication = step.get("toolType") == "communication"
        steps.append({"id": step["id"], "timestampMs": int(moment.timestamp() * 1000) if moment else 0,
                      "toolType": step.get("toolType"), "toolName": step.get("toolName"),
                      "details": (step.get("args") or {}).get("content") if communication else None,
                      "args": None if communication else step.get("args"), "agentId": step.get("agentId")})
    return steps


def cause_entry(trajectory, key, cause):
    metadata = trajectory["metadata"]
    identifier = f"{metadata['repo']}_{metadata['task_id']}_{metadata['features']}_{key}"
    return {"classification": cause["cause"], "task_id": metadata["task_id"], "model": key,
            "features": metadata["features"], "summary": cause.get("summary") or "",
            "failure": (cause.get("note") or "")[:500], "project": metadata["repo"], "id": identifier,
            "trajectory_url": f"static/data/causes/{cause['cause']}/{identifier}_trajectory.json",
            "step_count": len(trajectory["steps"])}


def merge_causes(index, entry):
    index = json.loads(json.dumps(index or {}))
    for cause in CAUSES:
        index.setdefault(cause, [])
    for cause in CAUSES:
        index[cause] = [e for e in index[cause] if e.get("id") != entry["id"]]
    index[entry["classification"]].append(entry)
    return index


def write_site(out_dir, exported, key_override=None):
    """Every exported pair into the website's layout under out_dir:
    [(pair_dir, trajectory, evaluation)]. Returns the viewer URLs."""
    out_dir, urls = Path(out_dir), []
    data = out_dir / "static" / "data"
    for pair_dir, trajectory, evaluation in exported:
        key = key_override or model_key(trajectory)
        setting = setting_dir(trajectory)
        row = index_row(trajectory, key)
        index_path = data / setting / "index.json"
        index = merge_index(read_json(index_path) or {}, [row], key, display_name(trajectory))
        write_json(index_path, index)
        metadata = trajectory["metadata"]
        for repo in sorted({metadata["repo"], row_repo(index, row)}):
            name = trajectory_file_name(repo, metadata["task_id"], metadata["features"])
            write_gzip_json(data / setting / "trajectories" / key / name, trajectory)
        urls.append(viewer_url(key, row_repo(index, row), metadata["task_id"], metadata["features"], setting))
        if isinstance(evaluation, dict) and isinstance(evaluation.get("cascade"), dict):
            write_legacy(out_dir, trajectory, setting, key, evaluation)
        cause = metadata["coordbench"].get("cause")
        if cause:
            write_cause(data, pair_dir, trajectory, key, cause)
    return urls


def write_legacy(out_dir, trajectory, setting, key, evaluation):
    metadata = trajectory["metadata"]
    path = out_dir / "legacy" / setting / metadata["repo"] / metadata["task_id"] / f"aggregated_results_{key}_k1.json"
    write_json(path, merge_legacy(read_json(path), trajectory, setting, legacy_detail(trajectory, evaluation)))


def write_cause(data, pair_dir, trajectory, key, cause):
    entry = cause_entry(trajectory, key, cause)
    causes = data / "causes"
    write_json(causes / "index.json", merge_causes(read_json(causes / "index.json"), entry))
    (causes / entry["classification"]).mkdir(parents=True, exist_ok=True)
    (causes / entry["classification"] / f"{entry['id']}_trajectory.json").write_text(
        json.dumps(seven_key_steps(trajectory)), encoding="utf-8")
    store = read_json(Path(pair_dir) / "conversation_store.json")
    if isinstance(store, dict):
        (causes / entry["classification"] / f"{entry['id']}_conversation.json").write_text(
            json.dumps(store, indent=2), encoding="utf-8")


# Human cause labels, as a labels file gives them.

def load_causes(path):
    """A labels file as {key: {"cause", "note"}}: a JSON object mapping a
    pair (its directory, its run_id, or "<repo>/task<N>/<features>") to a
    cause name or to {cause or label, note}, or a list of such objects
    naming the pair by pair_dir, run_id or repo, task_id and features."""
    value = read_json(path)
    if value is None:
        return {}
    items = value.items() if isinstance(value, dict) else ((pair_name(item), item) for item in value or []
                                                          if isinstance(item, dict))
    labels = {}
    for key, label in items:
        cause = label if isinstance(label, str) else (label or {}).get("cause") or (label or {}).get("label")
        if isinstance(cause, str) and cause.lower() in CAUSES and key:
            labels[str(key)] = {"cause": cause.lower(), "note": (label or {}).get("note") if isinstance(label, dict) else None}
    return labels


def pair_name(item):
    if item.get("pair_dir") or item.get("run_id"):
        return item.get("pair_dir") or item.get("run_id")
    features = item.get("features")
    if isinstance(features, list):
        features = "_".join(f"feature{f}" for f in sorted(features))
    return f"{item.get('repo')}/task{item.get('task_id')}/{features}" if item.get("repo") else None


def cause_for(labels, pair_dir, result):
    keys = (str(pair_dir), str(Path(pair_dir).resolve()), str(result.get("run_id")),
            f"{result.get('repo')}/task{result.get('task_id')}/{features_text(result)}")
    return next((labels[key] for key in keys if key in labels), None)


# The command.

def pair_dirs(paths):
    """Each path that holds result.json, and for any other directory every
    directory below it that does, in sorted order."""
    found = []
    for path in map(Path, paths):
        if (path / "result.json").is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(p.parent for p in path.rglob("result.json")))
        else:
            warn(f"{path}: no result.json here or below")
    return found


def export(paths, site=None, key=None, cooperbench_dir=None, causes=None, pair_files=True):
    labels = load_causes(causes) if causes else {}
    exported = []
    for pair_dir in pair_dirs(paths):
        result = read_json(pair_dir / "result.json") or {}
        cause = cause_for(labels, pair_dir, result)
        build = write_pair if pair_files else build_trajectory
        try:
            trajectory = build(pair_dir, cooperbench_dir, cause)
        except (OSError, ValueError) as error:
            warn(f"{pair_dir}: {error}")
            continue
        exported.append((pair_dir, trajectory, read_json(pair_dir / "eval.json")))
    urls = write_site(site, exported, key) if site else []
    return exported, urls


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export coordbench runs for CooperBench's viewer and coordbench's.")
    parser.add_argument("pairs", nargs="+", help="pair directories, or directories to search for them")
    parser.add_argument("--site", help="write the website's static/data layout under this directory")
    parser.add_argument("--model-key", help="the viewer's model key, instead of <model>_<arm>_<driver>")
    parser.add_argument("--cooperbench-dir", help="a CooperBench checkout, for feature titles and the spec audit")
    parser.add_argument("--causes", help="a labels file of human cause labels (expectation, commitment, communication)")
    parser.add_argument("--no-pair-files", action="store_true", help="write nothing into the pair directories")
    args = parser.parse_args(argv)
    exported, urls = export(args.pairs, args.site, args.model_key, args.cooperbench_dir, args.causes,
                            not args.no_pair_files)
    for (pair_dir, trajectory, _), url in zip(exported, urls or [None] * len(exported)):
        block = trajectory["metadata"]["coordbench"]
        line = f"{pair_dir}: {len(trajectory['steps'])} steps, timestamps {block['timestamps']}"
        if block["join_warnings"]:
            line += f", {len(block['join_warnings'])} join warning(s)"
        print(line + (f", {url}" if url else ""))
    return 0 if exported else 1


if __name__ == "__main__":
    sys.exit(main())
