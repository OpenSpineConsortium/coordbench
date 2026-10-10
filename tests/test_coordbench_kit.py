"""kit.py is the messaging every arm but solo serves, ported from CooperBench's
coop_msg.py, and the long poll its team mode registers. These tests pin
the port against coop_msg.py's own behaviour (a message to any name is
queued, an empty or missing text is sent, a missing recipient is refused
as argparse refuses it, "sent to" is marked for stderr and broadcast says
nothing, every entry has timestamp_iso), the long poll's text and schema
against mcp_server.py's (read from CooperBench's source when it is at
hand), the long poll's safety: the wait and the pop in one hold of the
lock, a closed run that wakes it, and a timeout refused rather than
crashed on; and what a reader of the events needs: the time a wait spent
blocked booked on its Call, the cap above the schema's maximum, and the
undo that puts back what a drain or a wait took for a reply the hub could
not write."""

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

import kit  # noqa: E402
from arm import AgentSpec, Arm, Call, Run, ToolError  # noqa: E402

AGENTS = ("agent1", "agent2", "agent3")
# mcp_server.py's text at 63b9d44, copied here so a drift in kit.py shows
# even where CooperBench's source is not at hand.
COOPERBENCH_DESCRIPTION = ("Block server-side until a message arrives in your team inbox, "
                           "or until timeout_ms elapses.  Returns the message text on hit, "
                           "or a 'no message' marker on timeout.  Prefer this to busy-polling "
                           "coop-recv when you're idle between substantive actions.")
COOPERBENCH_TIMEOUT = {"type": "integer", "minimum": 100, "maximum": 30000, "default": 5000,
                       "description": "How long to block before giving up."}
MARKER = "(no message arrived within the timeout)"
ENTRY_KEYS = {"from", "to", "content", "timestamp", "timestamp_iso"}
# What every arm's .mcp.json lets one call to the hub run, in milliseconds;
# a wait the hub holds longer than this answers nobody.
CLIENT_TIMEOUT_MS = 330000
TEAM_ARM = COORDBENCH / "arms" / "team" / "arm.py"


def module_constant(path, name):
    """The value a module file assigns to a top-level name, read with ast
    rather than imported, so the test needs none of the module's imports."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} assigns no {name}")


def cooperbench_wait_tool():
    """CooperBench's wait_for_message as mcp_server.py lists it, from a
    checkout named by COOPERBENCH_DIR or at ~/cooperbench, else None. The
    listing is read from the file with ast rather than imported, since
    the module imports redis at its top and the literal needs nothing."""
    roots = [os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")]
    for root in filter(None, roots):
        path = Path(root) / "src" / "cooperbench" / "team_harness" / "mcp_server.py"
        if path.is_file():
            return tools_list_literal(path)["tools"][0]
    return None


def tools_list_literal(path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == "_tools_list":
            returned = next(n for n in ast.walk(node) if isinstance(n, ast.Return))
            return ast.literal_eval(returned.value)
    raise AssertionError(f"{path} has no _tools_list")


COOPERBENCH_TOOL = cooperbench_wait_tool()


def tools(wait=True):
    return {tool.name: tool for tool in kit.messaging_tools(wait=wait)}


class DrainedBetween(threading.Condition):
    """A condition whose wait, once it holds, empties the agent's inbox
    before handing control back: the race between a wait and its pop (a
    receive_messages by the same agent slipping in), made certain."""

    def __init__(self, run, agent):
        super().__init__()
        self.run, self.agent = run, agent

    def wait_for(self, predicate, timeout=None):
        held = super().wait_for(predicate, timeout)
        kit.inbox(self.run, self.agent).clear()
        return held


class KitTest(unittest.TestCase):
    def setUp(self):
        self.run = Run("r1", Arm(), [AgentSpec(agent, "peer", (n,)) for n, agent in enumerate(AGENTS, 1)])

    def call(self, agent, name, **arguments):
        return tools()[name].handler(Call(self.run, agent, arguments))

    def refused(self, agent, name, **arguments):
        with self.assertRaises(ToolError) as caught:
            self.call(agent, name, **arguments)
        return caught.exception

    def contents(self, agent):
        return [entry["content"] for entry in kit.inbox(self.run, agent)]

    def message_events(self):
        return [event for event in self.run.events if event["kind"] == "message"]

    def in_a_thread(self, agent, name, **arguments):
        """Run the tool on a thread; the result and the seconds it took are
        read back through the list the thread fills."""
        outcome = []
        started = time.monotonic()

        def work():
            outcome.append(self.call(agent, name, **arguments))
            outcome.append(time.monotonic() - started)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread, outcome


class SendTest(KitTest):
    """The commands as coop_msg.py has them."""

    def test_a_message_to_any_name_is_queued_without_refusal(self):
        # coop_msg.py pushes to <recipient>:inbox whatever the name is;
        # list_agents is how an agent learns the names.
        self.assertEqual(self.call("agent1", "send_message", to="agent9", text="hi"), "sent to agent9")
        self.assertEqual(self.contents("agent9"), ["hi"])
        self.assertEqual(kit.inbox(self.run, "agent9")[0]["to"], "agent9")
        self.assertEqual([e["to"] for e in self.message_events()], ["agent9"])
        self.assertEqual(self.contents("agent2"), [])

    def test_an_empty_or_missing_text_is_sent_as_empty(self):
        # coop_msg.py's content is optional and read from stdin when
        # missing, which at its end is "".
        self.call("agent1", "send_message", to="agent2", text="")
        self.call("agent1", "send_message", to="agent2")
        self.assertEqual(self.contents("agent2"), ["", ""])
        self.assertEqual(self.call("agent1", "broadcast"), "")
        self.assertEqual((self.contents("agent2"), self.contents("agent3")), (["", "", ""], [""]))
        self.assertEqual([e["chars"] for e in self.message_events()], [0, 0, 0, 0])

    def test_a_missing_recipient_is_refused_as_argparse_refuses_it(self):
        for arguments in ({"text": "hi"}, {"to": None, "text": "hi"}):
            with self.subTest(arguments=arguments):
                problem = self.refused("agent1", "send_message", **arguments)
                self.assertEqual(str(problem), "the following arguments are required: recipient")
                self.assertEqual(problem.exit_code, 2)
        self.assertEqual(self.run.state.get("inboxes", {}), {})
        self.assertEqual(self.message_events(), [])

    def test_a_recipient_or_text_that_is_not_a_string_is_refused_before_anything_is_queued(self):
        # An integer text used to be delivered and then reported to the
        # sender as a hub crash when len() failed; a list as the recipient
        # crashed on the inbox lookup.
        for arguments in ({"to": ["agent2"], "text": "hi"}, {"to": "agent2", "text": 5},
                          {"to": 2, "text": "hi"}, {"to": "agent2", "text": ["hi"]}):
            with self.subTest(arguments=arguments):
                self.refused("agent1", "send_message", **arguments)
        self.assertEqual(self.run.state.get("inboxes", {}), {})
        self.assertEqual(self.message_events(), [])

    def test_a_broadcast_of_a_bad_text_reaches_nobody(self):
        # The text is checked before the first peer, so it is all or none.
        self.refused("agent2", "broadcast", text=5)
        self.assertEqual(self.run.state.get("inboxes", {}), {})
        self.assertEqual(self.message_events(), [])

    def test_broadcast_says_nothing_and_reaches_every_peer(self):
        self.assertEqual(self.call("agent2", "broadcast", text="config.py is mine"), "")
        self.assertEqual([self.contents(a) for a in AGENTS], [["config.py is mine"], [], ["config.py is mine"]])

    def test_every_entry_carries_both_timestamps(self):
        before = datetime.now()
        self.call("agent1", "send_message", to="agent2", text="now")
        entry = kit.inbox(self.run, "agent2")[0]
        self.assertEqual(set(entry), ENTRY_KEYS)
        self.assertIsInstance(entry["timestamp"], float)
        stamped = datetime.fromisoformat(entry["timestamp_iso"])
        self.assertLessEqual(before, stamped)
        self.assertLessEqual(stamped, datetime.now())

    def test_the_commands_take_coop_msgs_positionals(self):
        # coop-send <recipient> [content], coop-broadcast [content]: one
        # word each, the content optional, so an unquoted extra word is a
        # word too many, as argparse says.
        send, cast = tools()["send_message"], tools()["broadcast"]
        self.assertEqual((send.argv, cast.argv), (("to", "text"), ("text",)))
        self.assertEqual(send.input_schema["required"], ["to"])
        self.assertEqual(cast.input_schema["required"], [])

    def test_send_is_marked_to_print_on_stderr_and_nothing_else_is(self):
        marks = {name: tool.input_schema.get("x-coordbench-prints") for name, tool in tools().items()}
        self.assertEqual(marks, {"send_message": "stderr", "broadcast": None, "receive_messages": None,
                                 "peek_messages": None, "list_agents": None, "wait_for_message": None})


class WaitTest(KitTest):
    """The long poll: CooperBench's tool in text and schema, coordbench's in
    behaviour, and safe under concurrent calls."""

    def test_the_tool_is_cooperbenchs_in_text_and_schema(self):
        # The schema differs from mcp_server.py's by "required": [] alone,
        # which kit.schema always adds; the maximum stays CooperBench's
        # advertised 30000 whatever the handler takes.
        tool = tools()["wait_for_message"]
        self.assertEqual(tool.description, COOPERBENCH_DESCRIPTION)
        self.assertEqual(tool.input_schema, {"type": "object", "properties": {"timeout_ms": COOPERBENCH_TIMEOUT},
                                             "required": []})
        self.assertEqual(kit.ADVERTISED_LONGEST_WAIT_MS, 30000)
        self.assertNotIn("wait_for_message", tools(wait=False))

    @unittest.skipIf(COOPERBENCH_TOOL is None,
                     "CooperBench's source is not here; set COOPERBENCH_DIR to its checkout")
    def test_the_tool_matches_mcp_servers_listing(self):
        tool = tools()["wait_for_message"]
        self.assertEqual(COOPERBENCH_TOOL["name"], tool.name)
        self.assertEqual(COOPERBENCH_TOOL["description"], tool.description)
        theirs = {key: value for key, value in tool.input_schema.items() if key != "required"}
        self.assertEqual(COOPERBENCH_TOOL["inputSchema"], theirs)

    def test_the_cap_is_the_team_arms_300_seconds_under_the_client_timeout(self):
        # The handler honours a timeout_ms past the schema's maximum, up to
        # the team arm's cap on a request's wait; both sit under the 330 s
        # the arms' .mcp.json gives one call, so the hub answers first.
        self.assertEqual(kit.LONGEST_WAIT_MS, 300000)
        self.assertEqual(kit.LONGEST_WAIT_MS, module_constant(TEAM_ARM, "LONGEST_WAIT_SECONDS") * 1000)
        self.assertLess(kit.LONGEST_WAIT_MS, CLIENT_TIMEOUT_MS)
        self.assertGreater(kit.LONGEST_WAIT_MS, kit.ADVERTISED_LONGEST_WAIT_MS)
        self.assertEqual(kit.wait_seconds(30001), 30.001)
        self.assertEqual(kit.wait_seconds(300000), 300.0)
        self.assertEqual(kit.wait_seconds("1e9"), 300.0)

    def test_the_marker_is_cooperbenchs(self):
        self.assertEqual(kit.NO_MESSAGE, MARKER)

    def test_hands_over_the_oldest_and_leaves_the_rest(self):
        for text in ("one", "two"):
            self.call("agent2", "send_message", to="agent1", text=text)
        handed = json.loads(self.call("agent1", "wait_for_message", timeout_ms=100))
        self.assertEqual((handed["from"], handed["content"]), ("agent2", "one"))
        self.assertEqual(self.contents("agent1"), ["two"])
        self.assertEqual([e["count"] for e in self.run.events if e["kind"] == "receive"], [1])

    def test_returns_the_marker_once_the_timeout_passes(self):
        started = time.monotonic()
        self.assertEqual(self.call("agent1", "wait_for_message", timeout_ms=100), MARKER)
        self.assertGreaterEqual(time.monotonic() - started, 0.09)

    def test_wakes_when_a_message_arrives(self):
        thread, outcome = self.in_a_thread("agent1", "wait_for_message", timeout_ms=10000)
        time.sleep(0.2)
        self.call("agent3", "send_message", to="agent1", text="wake")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "the wait slept on past the message")
        self.assertEqual(json.loads(outcome[0])["content"], "wake")
        self.assertLess(outcome[1], 5)

    def test_a_closed_run_wakes_the_wait_at_once(self):
        # The wait used to look only at the inbox, so a close woke it and
        # it slept on until its timeout.
        thread, outcome = self.in_a_thread("agent1", "wait_for_message", timeout_ms=10000)
        time.sleep(0.2)
        with self.run.lock:
            self.run.closed = True
            self.run.lock.notify_all()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "the wait slept on past the close")
        self.assertEqual(outcome[0], MARKER)
        self.assertLess(outcome[1], 5)

    def test_a_closed_run_hands_over_a_message_the_inbox_already_holds(self):
        # The close ends the wait, not the inbox: what is there is handed
        # over, and only an empty inbox gives the marker.
        self.call("agent2", "send_message", to="agent1", text="before the close")
        with self.run.lock:
            self.run.closed = True
            self.run.lock.notify_all()
        started = time.monotonic()
        handed = json.loads(self.call("agent1", "wait_for_message", timeout_ms=10000))
        self.assertEqual(handed["content"], "before the close")
        self.assertEqual(self.call("agent1", "wait_for_message", timeout_ms=10000), MARKER)
        self.assertLess(time.monotonic() - started, 5)

    def test_the_time_spent_waiting_is_booked_on_the_call(self):
        # The hub's tool_done event carries blocked_ms from call.blocked_s,
        # which is how a reader tells a wait on the other agent from a hub
        # that was slow to answer: a wait that ran out booked its whole
        # timeout, one woken by a message the time until it came, and one
        # that found a message waiting next to nothing.
        wait = tools()["wait_for_message"].handler
        ran_out = Call(self.run, "agent1", {"timeout_ms": 200})
        self.assertEqual(wait(ran_out), MARKER)
        self.assertGreaterEqual(ran_out.blocked_s, 0.19)
        self.assertLess(ran_out.blocked_s, 5)
        woken = Call(self.run, "agent1", {"timeout_ms": 10000})
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(wait(woken)), daemon=True)
        thread.start()
        time.sleep(0.2)
        self.call("agent2", "send_message", to="agent1", text="wake")
        thread.join(timeout=5)
        self.assertEqual(json.loads(outcome[0])["content"], "wake")
        self.assertGreaterEqual(woken.blocked_s, 0.15)
        self.assertLess(woken.blocked_s, 5)
        self.call("agent2", "send_message", to="agent1", text="ready")
        at_once = Call(self.run, "agent1", {"timeout_ms": 10000})
        self.assertEqual(json.loads(wait(at_once))["content"], "ready")
        self.assertLess(at_once.blocked_s, 0.1)

    def test_an_inbox_drained_between_the_wait_and_the_pop_gives_the_marker(self):
        # The wait and the pop used to be two holds of the lock, and a
        # receive_messages between them left pop(0) an empty list.
        self.run.lock = DrainedBetween(self.run, "agent1")
        self.call("agent2", "send_message", to="agent1", text="gone")
        self.assertEqual(self.call("agent1", "wait_for_message", timeout_ms=100), MARKER)

    def test_a_cancelled_call_gives_the_marker_at_once_and_takes_nothing(self):
        # The client gave up on the wait (notifications/cancelled): the
        # hub sets the call's event and wakes the run. A message that
        # arrives with the cancellation, or was there before the call,
        # stays for the next call, since this one's answer reaches nobody.
        wait = tools()["wait_for_message"].handler
        call = Call(self.run, "agent1", {"timeout_ms": 10000})
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(wait(call)), daemon=True)
        started = time.monotonic()
        thread.start()
        time.sleep(0.2)
        with self.run.lock:
            self.call("agent2", "send_message", to="agent1", text="kept")
            call.cancelled.set()
            self.run.lock.notify_all()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "the wait slept on past the cancellation")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(outcome, [MARKER])
        self.assertEqual(self.contents("agent1"), ["kept"])
        cancelled_before = Call(self.run, "agent1", {"timeout_ms": 100})
        cancelled_before.cancelled.set()
        self.assertEqual(wait(cancelled_before), MARKER)
        self.assertEqual(self.contents("agent1"), ["kept"])
        self.assertEqual([e for e in self.run.events if e["kind"].startswith("receive")], [])
        self.assertEqual((call.undos, cancelled_before.undos), ([], []))

    def test_a_message_taken_for_a_reply_never_written_goes_back_at_the_head(self):
        # The hub runs the call's undo when it could not write the answer:
        # the message goes back first, the run is woken for whoever waits
        # on the inbox, and the record keeps both the handover and its
        # loss, so the receive is not counted as one the agent saw.
        wait = tools()["wait_for_message"].handler
        for text in ("one", "two"):
            self.call("agent2", "send_message", to="agent1", text=text)
        call = Call(self.run, "agent1", {"timeout_ms": 100})
        self.assertEqual(json.loads(wait(call))["content"], "one")
        self.assertEqual(self.contents("agent1"), ["two"])
        woken = []
        waiter = threading.Thread(target=lambda: woken.append(
            self.run.wait_until(lambda: len(self.contents("agent1")) == 2, 5)), daemon=True)
        waiter.start()
        time.sleep(0.2)
        call.lost()
        waiter.join(timeout=5)
        self.assertEqual(self.contents("agent1"), ["one", "two"])
        self.assertEqual(woken, [True])
        self.assertEqual([(e["kind"], e["agent"], e["count"]) for e in self.run.events
                          if e["kind"].startswith("receive")],
                         [("receive", "agent1", 1), ("receive_lost", "agent1", 1)])
        empty = Call(self.run, "agent3", {"timeout_ms": 100})
        self.assertEqual(wait(empty), MARKER)
        self.assertEqual(empty.undos, [])

    def test_a_drain_whose_reply_was_never_written_puts_the_messages_back_in_order(self):
        # In the pilot the reply to a coop-recv met a broken pipe and the
        # drained messages went with it. The drain now registers the same
        # undo as a wait: the hub runs it, the messages go back at the
        # head of the inbox in their order, ahead of what arrived since,
        # the run is woken for whoever waits, and the record keeps the
        # handover and its loss with one count.
        receive = tools()["receive_messages"].handler
        for text in ("one", "two"):
            self.call("agent2", "send_message", to="agent1", text=text)
        drain = Call(self.run, "agent1", {})
        self.assertEqual([m["content"] for m in json.loads(receive(drain))], ["one", "two"])
        self.assertEqual(self.contents("agent1"), [])
        self.call("agent3", "send_message", to="agent1", text="three")
        woken = []
        waiter = threading.Thread(target=lambda: woken.append(
            self.run.wait_until(lambda: len(self.contents("agent1")) == 3, 5)), daemon=True)
        waiter.start()
        time.sleep(0.2)
        drain.lost()
        waiter.join(timeout=5)
        self.assertEqual(self.contents("agent1"), ["one", "two", "three"])
        self.assertEqual(woken, [True])
        self.assertEqual([(e["kind"], e["agent"], e["count"]) for e in self.run.events
                          if e["kind"].startswith("receive")],
                         [("receive", "agent1", 2), ("receive_lost", "agent1", 2)])
        self.assertEqual([m["content"] for m in json.loads(self.call("agent1", "receive_messages"))],
                         ["one", "two", "three"])
        empty = Call(self.run, "agent3", {})
        self.assertEqual(json.loads(receive(empty)), [])
        self.assertEqual(empty.undos, [])

    def test_a_timeout_that_is_not_a_finite_number_is_refused(self):
        for value in ("soon", "", float("inf"), float("-inf"), "nan", [5000], {"ms": 5000}):
            with self.subTest(timeout_ms=value):
                self.refused("agent1", "wait_for_message", timeout_ms=value)

    def test_the_timeout_is_read_as_a_number_and_held_between_the_bounds(self):
        self.assertEqual(kit.wait_seconds("5000.0"), 5.0)
        self.assertEqual(kit.wait_seconds(2500), 2.5)
        self.assertEqual(kit.wait_seconds(10), 0.1)
        self.assertEqual(kit.wait_seconds(-5), 0.1)
        started = time.monotonic()
        self.assertEqual(self.call("agent1", "wait_for_message", timeout_ms="0.5"), MARKER)
        self.assertGreaterEqual(time.monotonic() - started, 0.09)

    def test_a_null_or_absent_timeout_means_the_default(self):
        self.assertEqual(kit.wait_seconds(kit.DEFAULT_WAIT_MS), 5.0)
        self.call("agent2", "send_message", to="agent1", text="ready")
        for arguments in ({}, {"timeout_ms": None}):
            with self.subTest(arguments=arguments):
                self.call("agent2", "send_message", to="agent1", text="ready")
                self.assertEqual(json.loads(self.call("agent1", "wait_for_message", **arguments))["content"],
                                 "ready")



class RecordTest(KitTest):
    """What the agents never see: each delivery's "message" event carries
    the text, the entry's two timestamps, its own seq as message_id and
    the sending call's tool event as send_id; each receive and each undo
    names the message ids it handed over or put back. conversation.py
    builds CooperBench's files from exactly this."""

    def over_the_hub(self, agent, name, **arguments):
        """A call framed as the hub frames it, so it has a tool event."""
        import mcp
        tool_list = {tool.name: tool for tool in kit.messaging_tools()}
        result, problem = mcp.call_tool(self.run, agent, tool_list, {"name": name, "arguments": arguments})
        self.assertIsNone(problem)
        return result["content"][0]["text"]

    def test_a_message_event_carries_the_text_its_timestamps_and_its_own_seq(self):
        self.over_the_hub("agent1", "send_message", to="agent2", text="I take cli.py")
        tool, message = [e for e in self.run.events if e["kind"] in ("tool", "message")]
        entry = kit.inbox(self.run, "agent2")[0]
        self.assertEqual(message["content"], "I take cli.py")
        self.assertEqual((message["timestamp"], message["timestamp_iso"]),
                         (entry["timestamp"], entry["timestamp_iso"]))
        self.assertEqual(message["message_id"], message["seq"])
        self.assertEqual(message["send_id"], tool["seq"])
        self.assertEqual(entry.message_id, message["seq"])

    def test_the_agent_reads_coop_msgs_five_keys_and_nothing_of_the_record(self):
        self.over_the_hub("agent1", "send_message", to="agent2", text="hello")
        received = json.loads(self.over_the_hub("agent2", "receive_messages"))
        self.assertEqual([set(entry) for entry in received], [ENTRY_KEYS])
        self.over_the_hub("agent1", "send_message", to="agent2", text="again")
        self.assertEqual(set(json.loads(self.over_the_hub("agent2", "wait_for_message", timeout_ms=100))),
                         ENTRY_KEYS)

    def test_the_deliveries_of_one_broadcast_share_its_send_id(self):
        self.over_the_hub("agent2", "broadcast", text="config.py is mine")
        self.over_the_hub("agent2", "send_message", to="agent1", text="and you?")
        tools = [e["seq"] for e in self.run.events if e["kind"] == "tool"]
        sent = self.message_events()
        self.assertEqual([(e["to"], e["send_id"]) for e in sent],
                         [("agent1", tools[0]), ("agent3", tools[0]), ("agent1", tools[1])])
        self.assertEqual(len({e["message_id"] for e in sent}), 3)

    def test_a_handler_called_directly_has_no_send_id(self):
        self.call("agent1", "send_message", to="agent2", text="hi")
        self.assertIsNone(self.message_events()[0]["send_id"])

    def test_receives_and_their_undo_name_the_message_ids(self):
        for text in ("one", "two"):
            self.over_the_hub("agent2", "send_message", to="agent1", text=text)
        ids = [e["message_id"] for e in self.message_events()]
        drain = Call(self.run, "agent1", {}, tool_seq=41)
        tools()["receive_messages"].handler(drain)
        drain.lost()
        self.over_the_hub("agent1", "wait_for_message", timeout_ms=100)
        self.over_the_hub("agent1", "receive_messages")
        received = [(e["kind"], e["message_ids"], e["call_id"]) for e in self.run.events
                    if e["kind"].startswith("receive")]
        wait_seq, drain_seq = [e["seq"] for e in self.run.events if e["kind"] == "tool"][-2:]
        self.assertEqual(received, [("receive", ids, 41), ("receive_lost", ids, 41),
                                    ("receive", ids[:1], wait_seq), ("receive", ids[1:], drain_seq)])

    def test_an_empty_drain_names_no_message(self):
        self.over_the_hub("agent3", "receive_messages")
        receive = next(e for e in self.run.events if e["kind"] == "receive")
        self.assertEqual((receive["count"], receive["message_ids"]), (0, []))

    def test_the_text_stays_in_the_record_and_off_the_trace_stream(self):
        import trace
        heard = trace.listen(trace.Memory())
        self.addCleanup(trace.unlisten, heard)
        self.over_the_hub("agent1", "send_message", to="agent2", text="a secret plan")
        streamed = next(e for e in heard if e["kind"] == "message")
        self.assertNotIn("content", streamed)
        self.assertNotIn("a secret plan", json.dumps(list(heard)))
        self.assertEqual(self.message_events()[0]["content"], "a secret plan")
        self.assertEqual(streamed["message_id"], self.message_events()[0]["message_id"])

    def test_the_metrics_count_words_beside_characters(self):
        self.over_the_hub("agent1", "send_message", to="agent2", text="I take  cli.py\nand config.py")
        self.over_the_hub("agent1", "broadcast", text="done")
        metrics = kit.message_metrics(self.run)
        self.assertEqual((metrics["messages_sent"], metrics["message_words"]), (3, 5 + 1 + 1))


class GitLogTest(unittest.TestCase):
    """The shared remote's history, taken as the run closes: in place for a
    repository on this host, through a shallow fetch for a URL, and
    without the upstream history the remote was seeded with."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote = self.tmp / "remote.git"
        self.git("init", "-q", "--bare", str(self.remote))
        work = self.tmp / "work"
        self.git("init", "-q", "-b", "main", str(work))
        self.base = self.commit(work, "upstream.txt", "the project's own history\n", "upstream commit")
        self.git("-C", str(work), "push", "-q", str(self.remote), "main")
        self.git("-C", str(work), "checkout", "-q", "-b", "agent1")
        self.commit(work, "cli.py", "print('verbose')\n", "agent1 adds the flag")
        self.git("-C", str(work), "push", "-q", str(self.remote), "agent1")

    def git(self, *arguments):
        # The host's own git configuration (a signing key, a push hook) is
        # left out, so the repository is the same wherever the test runs.
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@example.com",
                       "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@example.com"}
        done = subprocess.run(["git", *arguments], capture_output=True, text=True, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def commit(self, work, name, text, message):
        (work / name).write_text(text, encoding="utf-8")
        self.git("-C", str(work), "add", name)
        self.git("-C", str(work), "commit", "-q", "-m", message)
        return self.git("-C", str(work), "rev-parse", "HEAD")

    def test_a_repository_here_is_logged_in_place_with_every_branch_and_its_stat(self):
        log = kit.git_log(str(self.remote))
        self.assertIn("agent1 adds the flag", log)
        self.assertIn("upstream commit", log)
        self.assertIn("cli.py | 1 +", log)

    def test_the_history_below_the_base_is_left_out(self):
        log = kit.git_log(str(self.remote), self.base)
        self.assertIn("agent1 adds the flag", log)
        self.assertNotIn("upstream commit", log)

    def test_a_base_the_repository_lacks_logs_everything(self):
        self.assertIn("upstream commit", kit.git_log(str(self.remote), "0" * 40))

    def test_a_url_is_fetched_first(self):
        log = kit.git_log(self.remote.as_uri(), self.base)
        self.assertIn("agent1 adds the flag", log)
        self.assertNotIn("upstream commit", log)

    def test_a_remote_that_is_not_there_is_a_runtime_error_naming_the_step(self):
        with self.assertRaises(RuntimeError) as failed:
            kit.git_log((self.tmp / "missing.git").as_uri())
        self.assertTrue(str(failed.exception).startswith("git fetch failed"), str(failed.exception))


if __name__ == "__main__":
    unittest.main()
