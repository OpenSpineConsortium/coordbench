"""conversation.py derives CooperBench's conversation files from a run's
events, so a coordbench run is read by the tools that read the authors'
runs. These tests drive a real hub run of the coop arm, over the shell
path and over MCP, and pin what each file must be: conversation.json in
runner/coop.py's shape (one entry per delivery, sorted by the float
timestamp), <agent>_sent.jsonl in coop_msg.py's, and the paper-era store
in the shape of the website's exports, one row per send with its read
receipt, which a receive gives and a receive_lost takes back."""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py, which hub.py imports, shares its name with the
# standard library's tracer; a process that loaded that one first gets
# ours here instead.
if getattr(sys.modules.get("trace"), "__file__", None) != str(COORDBENCH / "trace.py"):
    sys.modules.pop("trace", None)
import conversation  # noqa: E402
import hub as hub_module  # noqa: E402
import mcp  # noqa: E402
from arm import Call  # noqa: E402

SPECS = {"3": "feature three", "5": "feature five", "8": "feature eight"}
# The keys of runner/coop.py's entries and of coop_msg.py's log lines, in
# their order.
CONVERSATION_KEYS = ["from", "to", "message", "timestamp", "feature_id"]
SENT_KEYS = ["from", "to", "content", "timestamp", "timestamp_iso"]
# The authors' store rows, as the website's causes/*_conversation.json
# files hold them.
STORE_KEYS = ["id", "sender_id", "recipient_id", "content", "message_type", "timestamp", "is_read"]


class CoopRun:
    """A coop run of three agents (features 3, 5 and 8) on an in-process
    hub, its calls made over /rpc as the shell commands make them, or
    over MCP."""

    def __init__(self, features=(3, 5, 8), arm="coop"):
        self.hub = hub_module.Hub()
        self.hub.create_run(arm, list(features), SPECS, None, "conv")
        self.run = self.hub.hosted("conv").run

    def shell(self, agent, tool, **arguments):
        return self.hub.call(self.run, agent, tool, arguments)["text"]

    def over_mcp(self, agent, tool, **arguments):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}
        return mcp.handle(request, self.run, agent)["result"]["content"][0]["text"]

    def events(self):
        return self.hub.events("conv")


def session():
    """agent1 tells agent2, agent2 broadcasts, agent3 writes to a misspelt
    name; agent2 drains its inbox over the shell, agent1 over MCP, and
    agent3's drain of the broadcast reaches nobody and is put back."""
    pair = CoopRun()
    pair.shell("agent1", "send_message", to="agent2", text="I take cli.py")
    pair.over_mcp("agent2", "broadcast", text="config.py is mine")
    pair.shell("agent3", "send_message", to="agent9", text="hello?")
    pair.shell("agent2", "receive_messages")
    pair.over_mcp("agent1", "receive_messages")
    drain = Call(pair.run, "agent3", {})
    pair.run.arm.tools_for(pair.run, "agent3")["receive_messages"].handler(drain)
    drain.lost()
    return pair


class ConversationJsonTest(unittest.TestCase):
    def test_one_entry_per_delivery_in_coops_shape_sorted_by_timestamp(self):
        entries = conversation.conversation(session().events())
        self.assertEqual([list(entry) for entry in entries], [CONVERSATION_KEYS] * 4)
        self.assertEqual([(e["from"], e["to"], e["message"], e["feature_id"]) for e in entries],
                         [("agent1", "agent2", "I take cli.py", 3),
                          ("agent2", "agent1", "config.py is mine", 5),
                          ("agent2", "agent3", "config.py is mine", 5),
                          ("agent3", "agent9", "hello?", 8)])
        stamps = [entry["timestamp"] for entry in entries]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(isinstance(stamp, float) for stamp in stamps))

    def test_entries_are_gathered_agent_by_agent_then_sorted_as_coop_py_sorts(self):
        # A stable sort over the agents' lists in the run's order, with
        # coop.py's key: a missing or unreadable timestamp sorts as 0.0.
        events = [started(["agent1", "agent2"]),
                  message(5, "agent2", "agent1", "b", 10.0), message(6, "agent1", "agent2", "a", 10.0),
                  message(7, "agent2", "agent1", "c", None), message(8, "agent1", "agent2", "d", "soon")]
        entries = conversation.conversation(events)
        self.assertEqual([entry["message"] for entry in entries], ["d", "c", "a", "b"])

    def test_events_from_before_the_text_was_logged_give_nothing(self):
        # The pilots' message events carry to and chars but no content.
        events = [{"seq": 0, "t": 1.0, "kind": "message", "agent": "agent1", "to": "agent2", "chars": 5}]
        self.assertEqual(conversation.conversation(events), [])
        self.assertEqual(conversation.sent_by(events), {})
        self.assertEqual(conversation.store(events)["messages"], [])


class SentJsonlTest(unittest.TestCase):
    def test_each_sender_gets_coop_msgs_lines(self):
        rows = conversation.sent_by(session().events())
        self.assertEqual(sorted(rows), ["agent1", "agent2", "agent3"])
        self.assertEqual([list(row) for row in rows["agent2"]], [SENT_KEYS] * 2)
        self.assertEqual([row["to"] for row in rows["agent2"]], ["agent1", "agent3"])
        stamp = datetime.fromisoformat(rows["agent1"][0]["timestamp_iso"]).timestamp()
        self.assertAlmostEqual(stamp, rows["agent1"][0]["timestamp"], places=3)

    def test_the_lines_are_written_as_coop_msg_writes_them(self):
        rows = conversation.sent_by(session().events())["agent2"]
        text = conversation.sent_lines(rows)
        self.assertEqual(text, "".join(json.dumps(row) + "\n" for row in rows))
        self.assertEqual([json.loads(line) for line in text.splitlines()], rows)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.pair = session()
        self.store = conversation.store(self.pair.events())

    def test_the_document_has_the_authors_three_keys(self):
        self.assertEqual(list(self.store), ["messages", "export_timestamp", "total_messages"])
        self.assertEqual(self.store["total_messages"], 3)
        datetime.fromisoformat(self.store["export_timestamp"])

    def test_a_row_is_one_send_with_the_authors_keys_first_and_coordbenchs_after(self):
        rows = self.store["messages"]
        self.assertEqual([list(row) for row in rows],
                         [STORE_KEYS + list(conversation.COORDBENCH_KEYS)] * 3)
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])
        self.assertEqual({row["message_type"] for row in rows}, {"info"})

    def test_agents_are_named_by_feature_and_a_broadcast_is_one_row(self):
        rows = self.store["messages"]
        self.assertEqual([(r["sender_id"], r["recipient_id"], r["content"]) for r in rows],
                         [("agent_3", "agent_5", "I take cli.py"), ("agent_5", "broadcast", "config.py is mine"),
                          ("agent_8", "agent9", "hello?")])
        self.assertEqual([(r["from"], r["to"], r["recipients"]) for r in rows],
                         [("agent1", "agent2", ["agent2"]), ("agent2", "broadcast", ["agent1", "agent3"]),
                          ("agent3", "agent9", ["agent9"])])
        self.assertEqual([len(r["message_ids"]) for r in rows], [1, 2, 1])

    def test_a_row_is_read_once_every_delivery_was_taken_and_not_put_back(self):
        rows = self.store["messages"]
        # agent2 drained agent1's message; agent1 took the broadcast, but
        # agent3's take of it reached nobody and went back; nobody reads
        # agent9's inbox.
        self.assertEqual([row["is_read"] for row in rows], [1, 0, 0])
        self.assertIsNotNone(rows[1]["read_at"]["agent1"])
        self.assertIsNone(rows[1]["read_at"]["agent3"])
        self.assertIsNone(rows[2]["read_at"]["agent9"])

    def test_the_read_time_is_the_receives_on_the_runs_wall_clock(self):
        events = self.pair.events()
        epoch = events[0]["epoch"]
        drain = next(e for e in events if e["kind"] == "receive" and e["agent"] == "agent2")
        self.assertAlmostEqual(self.store["messages"][0]["read_at"]["agent2"], epoch + drain["t"], places=5)
        self.assertGreaterEqual(self.store["messages"][0]["read_at"]["agent2"],
                                self.store["messages"][0]["sent_at"] - 0.01)

    def test_a_message_put_back_and_taken_again_is_read_at_the_second_take(self):
        events = [started(["agent1", "agent2"]), message(1, "agent1", "agent2", "hi", 100.0),
                  {"seq": 2, "t": 3.0, "kind": "receive", "agent": "agent2", "message_ids": [1]},
                  {"seq": 3, "t": 4.0, "kind": "receive_lost", "agent": "agent2", "message_ids": [1]},
                  {"seq": 4, "t": 9.0, "kind": "receive", "agent": "agent2", "message_ids": [1]}]
        row = conversation.store(events)["messages"][0]
        self.assertEqual((row["is_read"], row["read_at"]), (1, {"agent2": 1000.0 + 9.0}))

    def test_an_export_time_can_be_given(self):
        self.assertEqual(conversation.store([], "2026-01-01T00:00:00")["export_timestamp"], "2026-01-01T00:00:00")
        self.assertIsNone(conversation.store([])["export_timestamp"])


class FilesTest(unittest.TestCase):
    def test_a_multi_agent_run_gets_every_file_by_name(self):
        written = conversation.files(session().events())
        self.assertEqual(sorted(written), ["agent1_sent.jsonl", "agent2_sent.jsonl", "agent3_sent.jsonl",
                                           "conversation.json", "conversation_store.json"])
        self.assertIsInstance(written["agent1_sent.jsonl"], str)
        json.dumps(written)

    def test_a_silent_pair_still_gets_an_empty_conversation_and_no_sent_file(self):
        written = conversation.files(CoopRun(features=(3, 5)).events())
        self.assertEqual(written["conversation.json"], [])
        self.assertEqual(written["conversation_store.json"]["total_messages"], 0)
        self.assertEqual(sorted(written), ["conversation.json", "conversation_store.json"])

    def test_a_solo_run_gets_none(self):
        self.assertEqual(conversation.files(CoopRun(features=(3, 5), arm="solo").events()), {})

    def test_a_record_from_before_the_text_was_logged_gets_none(self):
        # An empty conversation.json would say nobody wrote; the truth is
        # that what they wrote was not recorded.
        events = [started(["agent1", "agent2"]),
                  {"seq": 1, "t": 1.0, "kind": "message", "agent": "agent1", "to": "agent2", "chars": 5}]
        self.assertFalse(conversation.text_recorded(events))
        self.assertEqual(conversation.files(events), {})


class TeamTest(unittest.TestCase):
    def test_a_message_taken_by_a_wait_is_read(self):
        hub = hub_module.Hub()
        hub.create_run("team", [3, 5], SPECS, {"shared_dir": "/workspace/shared"}, "team")
        run = hub.hosted("team").run
        hub.call(run, "agent2", "send_message", {"to": "agent1", "text": "blocked on task 1"})
        hub.call(run, "agent1", "wait_for_message", {"timeout_ms": 100})
        rows = conversation.store(hub.events("team"))["messages"]
        self.assertEqual([(r["sender_id"], r["recipient_id"], r["is_read"]) for r in rows],
                         [("agent_5", "agent_3", 1)])


def started(agents, epoch=1000.0):
    return {"seq": 0, "t": 0.0, "kind": "run_started", "agent": None, "epoch": epoch,
            "agents": [{"id": agent, "role": "peer", "features": [n]} for n, agent in enumerate(agents, 1)]}


def message(seq, sender, recipient, text, timestamp):
    return {"seq": seq, "t": 1.0, "kind": "message", "agent": sender, "to": recipient, "chars": len(text),
            "content": text, "timestamp": timestamp, "timestamp_iso": "2026-01-01T00:00:00",
            "message_id": seq, "send_id": None}


if __name__ == "__main__":
    unittest.main()
