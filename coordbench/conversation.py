"""CooperBench's conversation files, derived from a run's events.

kit.py records every delivery as a "message" event carrying its text
(content), coop_msg.py's two timestamps, its own seq (message_id) and the
seq of the "tool" event of the call that sent it (send_id); every
"receive" and "receive_lost" names the message ids it handed over or put
back; and the hub's first event, run_started, gives the wall clock the
events' t counts from and each agent's features. These functions turn
that record into the files CooperBench writes for a run, so the tools
that read the authors' runs read coordbench's. They are pure, a list of
events in and JSON values out, and the module imports nothing of
coordbench, so a reader of an events.jsonl can use it alone.

- conversation(events) is conversation.json as runner/coop.py and
  runner/team.py write it through _extract_conversation: the sent
  messages, one entry per delivery (coop_msg.py logs one line per
  recipient, so a broadcast to two peers is two), each {from, to,
  message, timestamp, feature_id} with the sender's feature, gathered
  agent by agent and sorted by the float timestamp, as coop.py sorts.
- sent_by(events) is each sender's <agent>_sent.jsonl, the lines
  coop_msg.py appends to COOP_LOG_PATH ({from, to, content, timestamp,
  timestamp_iso}), which the claude_code adapter copies only when there
  are any; sent_lines() writes one.
- store(events) is conversation_store.json, the shape of the message
  store the paper's authors exported (the website's
  causes/<cause>/<id>_conversation.json): {messages, export_timestamp,
  total_messages}, each message {id, sender_id, recipient_id, content,
  message_type, timestamp, is_read} and coordbench's own ids after them
  (ROW_KEYS and COORDBENCH_KEYS). A row is one send: a broadcast is one
  row whose recipient_id is "broadcast", its deliveries grouped by their
  send_id. Agents are named agent_<feature>, as the paper era named them,
  with coordbench's agent1 and agent2 beside. is_read is 1 when every
  delivery of the row was taken by a receive that no receive_lost undid.
- files(events) is every file above by name, as a runner writes them.

A "message" event from before the text was logged (the pilots) has no
content and is left out of all of them, since there is no text to
report, and files() then writes nothing at all, so a reader says "not
recorded" rather than "no messages".
"""

import json
from datetime import datetime

# The store's keys in the authors' order, then coordbench's.
ROW_KEYS = ("id", "sender_id", "recipient_id", "content", "message_type", "timestamp", "is_read")
COORDBENCH_KEYS = ("from", "to", "recipients", "message_ids", "send_id", "sent_at", "read_at")
BROADCAST = "broadcast"
# Every row of the authors' exports has this type.
MESSAGE_TYPE = "info"


def message_events(events):
    """The deliveries whose text was recorded, in the order they happened."""
    return [event for event in events if event.get("kind") == "message" and "content" in event]


def run_started(events):
    return next((event for event in events if event.get("kind") == "run_started"), None)


def agents_of(events):
    """Each agent's features by id, in the run's order, from run_started;
    empty for a run that has none."""
    started = run_started(events) or {}
    return {agent["id"]: list(agent.get("features") or []) for agent in started.get("agents") or []}


def feature_of(agent, agents):
    """The agent's feature, as CooperBench's per-agent feature_id: its
    first, since an agent of a pair has one; None when unknown."""
    features = agents.get(agent)
    return features[0] if features else None


def senders_in_order(agents, messages):
    """The run's agents in their order, then any other sender as it first
    appears, so nothing recorded is dropped."""
    order = list(agents)
    for event in messages:
        if event.get("agent") not in order:
            order.append(event.get("agent"))
    return order


def timestamp_key(entry):
    """coop.py's _message_timestamp_key: the timestamp as a float, 0.0 when
    it is missing or not a number."""
    try:
        return float(entry.get("timestamp"))
    except (TypeError, ValueError):
        return 0.0


def conversation(events):
    agents = agents_of(events)
    messages = message_events(events)
    entries = []
    for sender in senders_in_order(agents, messages):
        for event in messages:
            if event.get("agent") == sender:
                entries.append({"from": sender, "to": event.get("to"), "message": event["content"],
                                "timestamp": event.get("timestamp"), "feature_id": feature_of(sender, agents)})
    entries.sort(key=timestamp_key)
    return entries


def sent_by(events):
    rows = {}
    for event in message_events(events):
        rows.setdefault(event.get("agent"), []).append(
            {"from": event.get("agent"), "to": event.get("to"), "content": event["content"],
             "timestamp": event.get("timestamp"), "timestamp_iso": event.get("timestamp_iso")})
    return rows


def sent_lines(rows):
    """One <agent>_sent.jsonl's text: a line per row, written as
    coop_msg.py's _log_send writes it."""
    return "".join(json.dumps(row) + "\n" for row in rows)


def epoch_of(event, started):
    """When an event happened, as epoch seconds: run_started's epoch plus
    the event's t; None without a run_started."""
    if started is None or "epoch" not in started:
        return None
    return round(started["epoch"] + event.get("t", 0.0), 6)


def reads_of(events):
    """For each message id, the times of the takes that stand: a receive
    adds one, a receive_lost that follows takes the last one back."""
    started, reads = run_started(events), {}
    for event in events:
        kind = event.get("kind")
        for message_id in event.get("message_ids") or ():
            taken = reads.setdefault(message_id, [])
            if kind == "receive":
                taken.append(epoch_of(event, started))
            elif kind == "receive_lost" and taken:
                taken.pop()
    return reads


def sends(events):
    """The deliveries grouped by the call that sent them, in the order the
    calls happened; a delivery with no send_id is a send of its own."""
    groups, by_call = [], {}
    for event in message_events(events):
        send_id = event.get("send_id")
        if send_id is None:
            groups.append([event])
        elif send_id in by_call:
            by_call[send_id].append(event)
        else:
            by_call[send_id] = [event]
            groups.append(by_call[send_id])
    return groups


def paper_id(agent, agents):
    """agent_<feature>, the paper era's name for an agent of the run; any
    other name (a misspelt recipient) as it was given."""
    feature = feature_of(agent, agents)
    return f"agent_{feature}" if feature is not None else agent


def store_row(number, deliveries, agents, tools, reads):
    first = deliveries[0]
    is_broadcast = tools.get(first.get("send_id")) == "broadcast"
    recipients = [delivery.get("to") for delivery in deliveries]
    ids = [delivery.get("message_id") for delivery in deliveries]
    read_at = {recipient: min((t for t in reads.get(message_id) or () if t is not None), default=None)
               for recipient, message_id in zip(recipients, ids)}
    every_read = all(reads.get(message_id) for message_id in ids)
    return {"id": number, "sender_id": paper_id(first.get("agent"), agents),
            "recipient_id": BROADCAST if is_broadcast else paper_id(first.get("to"), agents),
            "content": first["content"], "message_type": MESSAGE_TYPE,
            "timestamp": first.get("timestamp_iso"), "is_read": 1 if every_read else 0,
            "from": first.get("agent"), "to": BROADCAST if is_broadcast else first.get("to"),
            "recipients": recipients, "message_ids": ids, "send_id": first.get("send_id"),
            "sent_at": first.get("timestamp"), "read_at": read_at}


def export_time(events):
    """When the record ends, in the store's naive ISO form: the run's last
    event, as coop_msg.py's datetime.now() would have read it then."""
    started = run_started(events)
    if started is None or not events:
        return None
    return datetime.fromtimestamp(epoch_of(events[-1], started)).isoformat()


def store(events, exported_at=None):
    agents = agents_of(events)
    tools = {event.get("seq"): event.get("tool") for event in events if event.get("kind") == "tool"}
    reads = reads_of(events)
    rows = [store_row(number, deliveries, agents, tools, reads)
            for number, deliveries in enumerate(sends(events), 1)]
    return {"messages": rows, "export_timestamp": exported_at or export_time(events),
            "total_messages": len(rows)}


def text_recorded(events):
    """Whether every delivery in the record carries its text; the pilots'
    did not, and a conversation built from them would read as silence."""
    return all("content" in event for event in events if event.get("kind") == "message")


def files(events):
    """The conversation files of a run by name, each a JSON value or, for
    a .jsonl, its text: conversation.json and conversation_store.json
    always ([] and an empty store when nobody wrote, as coop.py writes an
    empty conversation.json), and an <agent>_sent.jsonl per agent that
    sent anything. A run of one agent gets none, as CooperBench's solo
    runner writes none, and so does a record from before the text was
    logged, whose files would say nobody wrote when the text is only
    not recorded."""
    if len(agents_of(events)) == 1 or not text_recorded(events):
        return {}
    written = {"conversation.json": conversation(events), "conversation_store.json": store(events)}
    for agent, rows in sent_by(events).items():
        written[f"{agent}_sent.jsonl"] = sent_lines(rows)
    return written
