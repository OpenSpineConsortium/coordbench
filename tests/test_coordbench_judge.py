"""judge.py labels a pair's failure modes as CooperBench's paper does, so
these pin what can be pinned without a model: the conversation it builds
from each record coordbench and CooperBench write, the rendering, the
quote checks and App F's two-quote rule, App G's coercion, the speech-act
and emergent-tag rules, every free cross-check, judge.json's shape (the
one report.py reads), and both backends' command lines and requests
against a fake claude and a local endpoint. No test calls a model: the
judge is a fake that replays canned replies."""

import contextlib
import http.server
import io
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
sys.path.insert(0, str(COORDBENCH))

import judge  # noqa: E402

EPOCH = 1_791_583_200.0  # 2026-10-09T22:00:00Z


class FakeBackend:
    """A judge that replays replies in order and remembers what it was
    asked; an exception among the replies is raised in its turn."""

    name = "fake"

    def __init__(self, *replies, model="fake-model"):
        self.replies, self.model, self.calls = list(replies), model, []

    def describe(self):
        return {"backend": self.name, "model": self.model}

    def complete(self, system, user):
        self.calls.append((system, user))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return (reply if isinstance(reply, str) else json.dumps(reply)), {"cost_usd": 0.0}


class Log:
    """events.jsonl as the hub writes it after this round: run_started,
    each call's "tool" event, and every delivery's "message" event with
    its text, times, message_id and send_id."""

    def __init__(self, agents=(("agent1", [1]), ("agent2", [6]))):
        self.events = []
        self.agents = [a for a, _ in agents]
        self.add("run_started", None, 0.0, epoch=EPOCH, iso="2026-10-09T22:00:00+00:00", arm="coop",
                 agents=[{"id": a, "role": "peer", "features": f} for a, f in agents])

    def add(self, kind, agent, t, **data):
        event = {"seq": len(self.events), "t": t, "kind": kind, "agent": agent, **data}
        self.events.append(event)
        return event

    def send(self, agent, to, text, t, broadcast=False):
        tool = self.add("tool", agent, t, tool="broadcast" if broadcast else "send_message")
        recipients = [a for a in self.agents if a != agent] if broadcast else [to]
        ids = []
        for recipient in recipients:
            seq = len(self.events)
            self.add("message", agent, t, to=recipient, chars=len(text), content=text, timestamp=EPOCH + t,
                     timestamp_iso=f"2026-10-09T22:00:{int(t):02d}", message_id=seq, send_id=tool["seq"])
            ids.append(seq)
        return str(ids[0])

    def lines(self):
        return "".join(json.dumps(e) + "\n" for e in self.events)


def result_record(arm="coop", error=None, agents=(("agent1", 1), ("agent2", 6))):
    record = {"repo": "pallets_click_task", "task_id": 2068, "features": [a[1] for a in agents], "setting": "coop",
              "run_id": "coop-2068-f1_f6-abc", "run_name": "fixture", "model": "claude-sonnet-5-5", "arm": arm,
              "driver": "claude-cli", "agents": {a: {"feature_id": f, "status": "Submitted"} for a, f in agents}}
    if error:
        record["error"] = error
    return record


FAILED_OUTPUT = ("collected 3 items\n" + "x" * 5000 + "\nFAILED tests/test_termui.py::test_edit - AssertionError\n"
                 "Traceback (most recent call last):\n  File \"x.py\", line 1\nE   KeyError: 'priority'\n"
                 "1 failed, 2 passed\n")


def evaluation(both_passed=False, status="clean", strategy="naive", cascade=None, error=None):
    record = {"repo": "pallets_click_task", "task_id": 2068, "features": [1, 6], "setting": "coop",
              "apply_status": {"agent1": "applied", "agent2": "applied"},
              "merge": {"status": status, "strategy": strategy, "diff": "diff --git a/x b/x\n"},
              "feature1": {"feature_id": 1, "passed": both_passed, "exit_code": 0 if both_passed else 1,
                           "tests_passed": 2, "tests_failed": 0 if both_passed else 1,
                           "test_output": "3 passed\n" if both_passed else FAILED_OUTPUT},
              "feature2": {"feature_id": 6, "passed": True, "exit_code": 0, "tests_passed": 4, "tests_failed": 0,
                           "test_output": "4 passed\n"},
              "both_passed": both_passed, "error": error}
    if cascade is not None:
        record["cascade"] = cascade
    return record


AGENT1_PATCH = """diff --git a/src/click/termui.py b/src/click/termui.py
--- a/src/click/termui.py
+++ b/src/click/termui.py
@@ -80,6 +80,12 @@ def edit(
     editor = None
+def edit_files(filenames, editor=None):
+    # agent2 inserts the priority handling here
+    return _edit_many(filenames, editor)
+
diff --git a/tests/test_termui.py b/tests/test_termui.py
--- a/tests/test_termui.py
+++ b/tests/test_termui.py
@@ -1,1 +1,2 @@
+# the other agent adds its tests below
 import click
"""

AGENT2_PATCH = """diff --git a/src/click/termui.py b/src/click/termui.py
--- a/src/click/termui.py
+++ b/src/click/termui.py
@@ -84,4 +84,6 @@ def edit(
     return editor
+    priority = normalize_priority(priority)
+
diff --git a/src/click/_termui_impl.py b/src/click/_termui_impl.py
--- a/src/click/_termui_impl.py
+++ b/src/click/_termui_impl.py
@@ -10,2 +10,4 @@ class Editor:
+    def normalize_priority(self, value):
+        return value
"""

MERGE_NAIVE = "diff --git a/src/click/termui.py b/src/click/termui.py\n+def edit_files(filenames, editor=None):\n"


def standard_log():
    """The conversation most tests read: a broadcast plan with a question,
    a reply naming the same file with NOT, a status claim made twice
    (once naming a function the patch lacks), and a question left
    unanswered."""
    log = Log()
    log.add("tool", "agent2", 1.0, tool="list_agents")
    ids = {"plan": log.send("agent1", None, "Plan: I will add edit_files() to src/click/termui.py lines 80-120. "
                                            "Which lines are you editing?", 10.0, broadcast=True),
           "reply": log.send("agent2", "agent1", "I won\u2019t touch termui.py lines 80-120; I will NOT edit it, "
                                                  "only src/click/_termui_impl.py.", 20.0),
           "claim": log.send("agent1", "agent2", "Status update: done, I added `edit_files` and "
                                                  "`open_editor_many` to termui.py.", 30.0),
           "again": log.send("agent1", "agent2", "Status update: done, I added `edit_files` and "
                                                  "`open_editor_many` to termui.py!", 40.0),
           "question": log.send("agent2", "agent1", "Can you confirm the signature of the priority hook?", 50.0)}
    log.add("receive", "agent1", 21.0, count=1, message_ids=[int(ids["reply"])], call_id=0)
    return log, ids


class PairDir:
    """A pair directory written as run.py writes one."""

    def __init__(self, test, log=None, result=None, eval_record="default", patches=None, files=None):
        self.root = Path(tempfile.mkdtemp(prefix="judge-test-"))
        test.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.dir = self.root / "coop" / "pallets_click_task" / "2068" / "f1_f6"
        self.dir.mkdir(parents=True)
        self.write("result.json", json.dumps(result or result_record()))
        if eval_record == "default":
            eval_record = evaluation()
        if eval_record is not None:
            self.write("eval.json", json.dumps(eval_record))
        if log is not None:
            self.write("events.jsonl", log.lines())
        for name, text in (patches if patches is not None else {"agent1.patch": AGENT1_PATCH,
                                                                 "agent6.patch": AGENT2_PATCH}).items():
            self.write(name, text)
        for name, text in (files or {}).items():
            self.write(name, text)

    def write(self, name, text):
        (self.dir / name).write_text(text, encoding="utf-8")

    def pair(self):
        return judge.load_pair(self.dir)

    def judged(self):
        return json.loads((self.dir / "judge.json").read_text())


def run_command(argv, backend=None):
    """main() for a judge command with a fake backend, its output kept."""
    args = judge.parser_of().parse_args(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = judge.command_judge(args, backend=backend) if backend is not None else judge.main(argv)
    return code, out.getvalue()


# The conversation.

class ConversationTest(unittest.TestCase):
    def test_events_give_one_message_per_send_with_the_deliverys_id(self):
        log, ids = standard_log()
        conversation = judge.conversation_of(PairDir(self, log).pair())
        self.assertEqual(conversation.source, "events.jsonl")
        self.assertEqual([m.id for m in conversation.messages], list(ids.values()))
        self.assertEqual(conversation.messages[0].to, "all")
        self.assertEqual(conversation.messages[1].to, "agent1")

    def test_messages_are_in_time_order(self):
        log = Log()
        late = log.send("agent1", "agent2", "late", 30.0)
        early = log.send("agent2", "agent1", "early", 10.0)
        conversation = judge.conversation_of(PairDir(self, log).pair())
        self.assertEqual([m.id for m in conversation.messages], [early, late])

    def test_a_record_without_text_says_so_rather_than_silence(self):
        log = Log()
        log.add("message", "agent1", 3.0, to="agent2", chars=10)
        conversation = judge.conversation_of(PairDir(self, log).pair())
        self.assertFalse(conversation.recorded)
        self.assertEqual(conversation.reason, judge.NOT_RECORDED)

    def test_a_run_whose_agents_never_wrote_has_an_empty_conversation(self):
        log = Log()
        log.add("tool", "agent1", 1.0, tool="list_agents")
        conversation = judge.conversation_of(PairDir(self, log).pair())
        self.assertTrue(conversation.recorded)
        self.assertEqual(conversation.messages, [])

    def test_team_requests_and_responses_are_messages(self):
        log = Log()
        log.add("request", "agent1", 5.0, request_id="r1", to="agent2", request_kind="plan_approval",
                content="Approve my plan?", timestamp=EPOCH + 5, timestamp_iso="2026-10-09T22:00:05", send_id=None)
        log.add("response", "agent2", 6.0, request_id="r1", to="agent1", content="Approved.",
                timestamp=EPOCH + 6, timestamp_iso="2026-10-09T22:00:06", send_id=None)
        conversation = judge.conversation_of(PairDir(self, log).pair())
        self.assertTrue(conversation.includes_requests)
        self.assertEqual([m.kind for m in conversation.messages], ["request", "response"])
        self.assertIn("(request plan_approval)", judge.message_line(conversation.messages[0]))

    def test_coordbenchs_store_is_read_by_its_message_ids(self):
        store = {"messages": [{"id": 1, "sender_id": "agent_1", "recipient_id": "broadcast", "content": "hi all",
                               "message_type": "info", "timestamp": "2026-10-09T22:00:01", "is_read": 1,
                               "from": "agent1", "to": "broadcast", "recipients": ["agent2"], "message_ids": [7],
                               "send_id": 6, "sent_at": EPOCH + 1, "read_at": {"agent2": EPOCH + 2}}],
                 "export_timestamp": None, "total_messages": 1}
        pair = PairDir(self, files={"conversation_store.json": json.dumps(store)}).pair()
        message = judge.conversation_of(pair).messages[0]
        self.assertEqual((message.id, message.sender, message.to), ("7", "agent1", "all"))

    def test_the_paper_eras_store_is_read_by_its_row_ids(self):
        store = {"messages": [{"id": 3, "sender_id": "agent_3", "recipient_id": "broadcast", "content": "x",
                               "message_type": "info", "timestamp": "2025-12-13T04:52:04.197018", "is_read": 1}]}
        pair = PairDir(self, files={"conversation_store.json": json.dumps(store)}).pair()
        message = judge.conversation_of(pair).messages[0]
        self.assertEqual((message.id, message.sender, message.to), ("3", "agent_3", "all"))

    def test_cooperbenchs_conversation_json_groups_a_broadcasts_entries(self):
        entries = [{"from": "agent1", "to": "agent2", "message": "hello", "timestamp": EPOCH, "feature_id": 1},
                   {"from": "agent1", "to": "agent3", "message": "hello", "timestamp": EPOCH, "feature_id": 1},
                   {"from": "agent2", "to": "agent1", "message": "hi", "timestamp": EPOCH + 1, "feature_id": 6}]
        pair = PairDir(self, files={"conversation.json": json.dumps(entries)}).pair()
        messages = judge.conversation_of(pair).messages
        self.assertEqual([(m.id, m.to) for m in messages], [("1", "all"), ("2", "agent1")])


class RenderTest(unittest.TestCase):
    def test_the_header_names_the_agents_with_their_features_then_one_line_per_message(self):
        log, ids = standard_log()
        pair = PairDir(self, log).pair()
        text, cut = judge.render_conversation(pair, judge.conversation_of(pair))
        self.assertFalse(cut)
        self.assertIn("agent1 implements feature 1; agent2 implements feature 6.", text)
        self.assertIn(f"[{ids['plan']}] 2026-10-09T22:00:10 agent1 -> all: Plan: I will add", text)

    def test_a_long_conversation_keeps_its_first_and_last_fifty_thousand_characters(self):
        log = Log()
        for number in range(30):
            log.send("agent1", "agent2", f"message {number} " + "y" * 5000, float(number))
        pair = PairDir(self, log).pair()
        whole, _ = judge.render_conversation(pair, judge.conversation_of(pair), limit=None)
        text, cut = judge.render_conversation(pair, judge.conversation_of(pair))
        self.assertTrue(cut)
        self.assertTrue(text.startswith(whole[:judge.CONVERSATION_KEEP]))
        self.assertTrue(text.endswith(whole[-judge.CONVERSATION_KEEP:]))
        self.assertIn("are left out here", text)


# Quotes and App F.

class QuoteTest(unittest.TestCase):
    def setUp(self):
        log, self.ids = standard_log()
        self.pair = PairDir(self, log).pair()
        self.messages = judge.conversation_of(self.pair).messages

    def check(self, *quotes):
        return judge.verify_quotes([(q, None) for q in quotes], self.messages)

    def test_whitespace_and_curly_quotes_are_folded(self):
        checks = self.check("I won't   touch termui.py\nlines 80-120")
        self.assertTrue(checks[0]["verified"])
        self.assertEqual(checks[0]["message_id"], self.ids["reply"])

    def test_the_header_and_a_lines_prefix_are_not_message_content(self):
        self.assertFalse(self.check("Conversation of a coordbench run")[0]["verified"])
        self.assertFalse(self.check("agent1 -> all: Plan")[0]["verified"])

    def test_a_copied_line_prefix_is_dropped_before_the_check(self):
        quote = f"[{self.ids['plan']}] 2026-10-09T22:00:10 agent1 -> all: Plan: I will add edit_files()"
        self.assertTrue(self.check(quote)[0]["verified"])

    def test_a_quote_spanning_two_messages_does_not_verify(self):
        self.assertFalse(self.check("to termui.py. Status update")[0]["verified"])

    def test_the_same_words_in_two_messages_are_two_quotes_and_one_quote_listed_twice_is_one(self):
        twice = self.check("I added `edit_files`", "I added `edit_files`")
        self.assertEqual(judge.fresh_count(twice), 2)
        once = self.check("Can you confirm", "Can you confirm")
        self.assertEqual(judge.fresh_count(once), 1)


class CommErrorsTest(unittest.TestCase):
    def setUp(self):
        log, self.ids = standard_log()
        self.pair_dir = PairDir(self, log)

    def judge_with(self, reply, *argv):
        backend = FakeBackend(reply)
        code, out = run_command(["commerrors", str(self.pair_dir.root), *argv], backend)
        self.assertEqual(code, 0, out)
        return self.pair_dir.judged(), backend

    def test_appF_text_is_the_system_prompt_and_the_conversation_the_user_turn(self):
        _, backend = self.judge_with({"evidence": []})
        system, user = backend.calls[0]
        self.assertEqual(system, judge.prompt_text("commerrors-appF-7.txt"))
        self.assertIn("Status update: done", user)

    def test_two_verified_quotes_make_a_label_with_its_group(self):
        reply = {"evidence": ["Status update: done, I added `edit_files` and `open_editor_many` to termui.py.",
                              "Status update: done, I added `edit_files` and `open_editor_many` to termui.py!"],
                 "issue": {"category": "C4b Spammy repetition (near-duplicate status blocks)",
                           "description": "the same status twice"}}
        data, _ = self.judge_with(reply)
        section = data["commerrors"]
        self.assertTrue(section["accepted"])
        self.assertEqual(section["issue"], {"code": "C4b", "group": "Repetition",
                                            "description": "the same status twice"})
        self.assertEqual(section["verified"], [True, True])
        self.assertEqual(section["evidence_message_ids"], [self.ids["claim"], self.ids["again"]])
        self.assertEqual(section["cross_check"]["heuristic"], "near_duplicates")
        self.assertTrue(section["cross_check"]["agrees"])

    def test_one_verified_quote_is_not_enough(self):
        reply = {"evidence": ["Can you confirm the signature", "no reply ever came"],
                 "issue": {"category": "C1a", "description": "unanswered"}}
        section = self.judge_with(reply)[0]["commerrors"]
        self.assertFalse(section["accepted"])
        self.assertIsNone(section["issue"])
        self.assertIn("at least 2", section["rejected_reason"])
        self.assertEqual(section["proposed"]["code"], "C1a")

    def test_plan_drift_stands_only_in_appF_8(self):
        reply = {"evidence": ["I will add edit_files()", "I will NOT edit it"],
                 "issue": {"category": "C5", "description": "drift"}}
        default = self.judge_with(reply)[0]["commerrors"]
        self.assertFalse(default["accepted"])
        self.assertIn("appF-7", default["rejected_reason"])
        variant = self.judge_with(reply, "--variant", "appF-8", "--force")[0]["commerrors"]
        self.assertTrue(variant["accepted"])
        self.assertEqual(variant["group"], "Hallucination")

    def test_no_issue_is_no_label(self):
        section = self.judge_with({"evidence": []})[0]["commerrors"]
        self.assertFalse(section["accepted"])
        self.assertIsNone(section["rejected_reason"])

    def test_a_reply_that_is_not_json_is_asked_once_more(self):
        backend = FakeBackend("I think it is fine.", "```json\n{\"evidence\": []}\n```")
        run_command(["commerrors", str(self.pair_dir.root)], backend)
        data = self.pair_dir.judged()
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(data["calls"]["commerrors"]["attempts"], 2)
        self.assertIn("commerrors", data)

    def test_two_replies_that_are_not_json_leave_no_label_and_keep_the_raw_text(self):
        backend = FakeBackend("no", "still no")
        run_command(["commerrors", str(self.pair_dir.root)], backend)
        data = self.pair_dir.judged()
        self.assertEqual(len(backend.calls), 2)
        self.assertNotIn("commerrors", data)
        self.assertEqual(data["calls"]["commerrors"]["raw"], ["no", "still no"])
        self.assertIn("not a JSON object", data["errors"]["commerrors"])

    def test_a_failed_call_is_an_error_not_a_label(self):
        run_command(["commerrors", str(self.pair_dir.root)], FakeBackend(judge.JudgeError("rate limited")))
        data = self.pair_dir.judged()
        self.assertNotIn("commerrors", data)
        self.assertEqual(data["errors"]["commerrors"], "rate limited")

    def test_the_same_judge_is_not_asked_twice_unless_forced(self):
        self.judge_with({"evidence": []})
        backend = FakeBackend({"evidence": []})
        run_command(["commerrors", str(self.pair_dir.root)], backend)
        self.assertEqual(backend.calls, [])
        run_command(["commerrors", str(self.pair_dir.root), "--force"], backend)
        self.assertEqual(len(backend.calls), 1)

    def test_a_pair_with_no_messages_gets_no_call_and_no_label(self):
        log = Log()
        silent = PairDir(self, log)
        backend = FakeBackend()
        run_command(["commerrors", str(silent.root)], backend)
        data = silent.judged()
        self.assertEqual(backend.calls, [])
        self.assertNotIn("commerrors", data)
        self.assertIn("no messages", data["skipped"]["commerrors"])

    def test_a_dry_run_calls_nothing_and_writes_nothing(self):
        code, out = run_command(["commerrors", "--dry-run", str(self.pair_dir.root)])
        self.assertEqual(code, 0)
        self.assertIn("would call claude-cli", out)
        self.assertFalse((self.pair_dir.dir / "judge.json").exists())

    def test_the_top_level_names_the_latest_call_and_every_section_stays(self):
        self.judge_with({"evidence": []})
        labels = {self.ids["plan"]: "Plan"}
        run_command(["speechacts", str(self.pair_dir.root)], FakeBackend(labels, model="other-model"))
        data = self.pair_dir.judged()
        self.assertIn("commerrors", data)
        self.assertEqual(data["speech_acts"], labels)
        self.assertEqual((data["backend"], data["model"], data["prompt_variant"]),
                         ("fake", "other-model", "speechacts-v1"))
        self.assertEqual(set(data["calls"]), {"commerrors", "speech_acts"})


class RenderCommandTest(unittest.TestCase):
    def test_render_prints_the_system_prompt_and_the_input_and_calls_nothing(self):
        pair_dir = PairDir(self, standard_log()[0])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(judge.main(["render", "commerrors", str(pair_dir.root), "--variant", "appF-8"]), 0)
        text = out.getvalue()
        self.assertIn("- C5 Plan drift / unilateral deviation", text)
        self.assertIn("=== user input ===", text)
        self.assertFalse((pair_dir.dir / "judge.json").exists())


class ErrorCodeTest(unittest.TestCase):
    def test_codes_are_read_however_they_are_written(self):
        self.assertEqual(judge.error_code("C4A"), "C4a")
        self.assertEqual(judge.error_code("c3a: incorrect claim"), "C3a")
        self.assertEqual(judge.error_code("Non-answer or vague answer"), "C2")
        self.assertIsNone(judge.error_code("something else"))

    def test_every_code_maps_to_one_of_the_three_groups(self):
        self.assertEqual(set(judge.ERROR_GROUPS.values()), {"Repetition", "Unresponsiveness", "Hallucination"})
        for variant in judge.COMMERROR_VARIANTS.values():
            self.assertTrue(set(variant["codes"]) <= set(judge.ERROR_GROUPS))


# Symptoms.

def symptom_reply(level="coordination", primary="Work overlap", secondary=(), evidence=None):
    return {"level": level, "primary_symptom": primary, "secondary_symptoms": list(secondary),
            "evidence": evidence if evidence is not None else [
                {"source": "conversation", "quote": "I will add edit_files() to src/click/termui.py"},
                {"source": "patch", "quote": "def edit_files(filenames, editor=None):"}]}


class SymptomsTest(unittest.TestCase):
    def judge_with(self, reply, pair_dir=None, *argv):
        pair_dir = pair_dir or PairDir(self, standard_log()[0], files={"merge_naive.diff": MERGE_NAIVE})
        backend = FakeBackend(reply)
        run_command(["symptoms", str(pair_dir.root), *argv], backend)
        return pair_dir.judged(), backend

    def test_a_verified_conversation_quote_keeps_a_coordination_label(self):
        data, _ = self.judge_with(symptom_reply(secondary=["broken commitment", "Not a symptom"]))
        section = data["symptoms"]
        self.assertEqual((section["level"], section["primary"], section["family"]),
                         ("coordination", "Work overlap", "Structural"))
        self.assertEqual(section["secondary"], ["Broken commitment"])
        self.assertEqual(section["secondary_families"], [None])
        self.assertEqual(section["invalid_names"], ["Not a symptom"])
        self.assertEqual([e["verified"] for e in section["evidence"]], [True, True])
        self.assertFalse(section["coerced"])
        self.assertEqual(section["failed_under"], ["63b9d44"])

    def test_without_a_verified_conversation_quote_the_level_is_implementation(self):
        reply = symptom_reply(evidence=[{"source": "patch", "quote": "def edit_files(filenames, editor=None):"},
                                        {"source": "conversation", "quote": "words nobody wrote"}])
        section = self.judge_with(reply)[0]["symptoms"]
        self.assertEqual((section["level"], section["primary"], section["secondary"]), ("implementation", None, []))
        self.assertTrue(section["coerced"])
        self.assertEqual(section["proposed"]["primary_symptom"], "Work overlap")

    def test_a_quote_is_checked_against_the_source_it_names_only(self):
        reply = symptom_reply(evidence=[{"source": "patch", "quote": "I will add edit_files()"},
                                        {"source": "outcome", "quote": "KeyError: 'priority'"}])
        section = self.judge_with(reply)[0]["symptoms"]
        self.assertEqual([e["verified"] for e in section["evidence"]], [False, True])
        self.assertEqual(section["level"], "implementation")

    def test_the_input_holds_the_outcome_the_filtered_patches_and_the_conversation(self):
        _, backend = self.judge_with(symptom_reply())
        system, user = backend.calls[0]
        self.assertEqual(system, judge.prompt_text("symptoms-r1.txt"))
        self.assertIn("FAILED tests/test_termui.py::test_edit", user)
        self.assertIn("merge: status clean, strategy naive", user)
        self.assertIn("decided verdict_63b9d44 (merge_naive.diff)", user)
        self.assertNotIn("the other agent adds its tests below", user)
        self.assertIn("# Conversation evidence", user)
        self.assertLess(user.count("x" * 100), 50)

    def test_a_passed_pair_and_a_harness_error_are_out_of_scope(self):
        passed = PairDir(self, standard_log()[0], eval_record=evaluation(both_passed=True))
        backend = FakeBackend()
        run_command(["symptoms", str(passed.root)], backend)
        self.assertIn("passed", passed.judged()["skipped"]["symptoms"])
        broken = PairDir(self, standard_log()[0], result=result_record(error="hub crashed"))
        run_command(["symptoms", str(broken.root)], backend)
        self.assertTrue(broken.judged()["skipped"]["symptoms"].startswith("harness error"))
        self.assertEqual(backend.calls, [])

    def test_a_pair_the_paper_eras_cascade_fails_is_in_scope_under_that_basis(self):
        cascade = {"ran": True, "verdict_paper_era": False, "route": "none",
                   "deciding_artifact": {"verdict_63b9d44": "agent1.patch", "verdict_paper_era": "merge_union.diff"}}
        pair_dir = PairDir(self, standard_log()[0], files={"merge_union.diff": "union diff text\n"},
                           eval_record=evaluation(both_passed=True, status="conflicts", strategy="solo-agent1",
                                                  cascade=cascade))
        data, backend = self.judge_with(symptom_reply(), pair_dir)
        self.assertEqual(data["symptoms"]["failed_under"], ["paper_era"])
        self.assertIn("decided verdict_paper_era (merge_union.diff)", backend.calls[0][1])

    def test_a_silent_failed_pair_is_implementation_by_app_gs_rule_without_a_call(self):
        pair_dir = PairDir(self, Log())
        data, backend = self.judge_with(symptom_reply(), pair_dir)
        self.assertEqual(backend.calls, [])
        self.assertEqual((data["symptoms"]["decided_by"], data["symptoms"]["level"]), ("rule", "implementation"))
        self.assertNotIn("symptoms", data["calls"])

    def test_the_tree_variant_offers_information_asymmetry(self):
        reply = symptom_reply(primary="Information asymmetry")
        default = self.judge_with(reply)[0]["symptoms"]
        self.assertIsNone(default["primary"])
        self.assertEqual(default["invalid_names"], ["Information asymmetry"])
        tree = self.judge_with(reply, None, "--variant", "symptoms-r1-tree")[0]["symptoms"]
        self.assertEqual((tree["primary"], tree["family"]), ("Information asymmetry", "Information"))


class DecidingArtifactTest(unittest.TestCase):
    def test_a_clean_merge_is_read_from_its_diff_and_the_fallback_from_agent1s_patch(self):
        clean = PairDir(self, files={"merge_naive.diff": MERGE_NAIVE}).pair()
        self.assertEqual(judge.deciding_artifact(clean), ("merge_naive.diff", MERGE_NAIVE))
        fallback = PairDir(self, eval_record=evaluation(status="conflicts", strategy="solo-agent1")).pair()
        self.assertEqual(judge.deciding_artifact(fallback)[0], "agent1.patch")

    def test_the_test_file_filter_is_cooperbenchs(self):
        filtered = judge.filter_test_files(AGENT1_PATCH)
        self.assertIn("src/click/termui.py", filtered)
        self.assertNotIn("tests/test_termui.py", filtered)
        for path in ("pkg/metrics_test.go", "src/foo.spec.ts", "src/FooTest.java", "testdata/x.txt"):
            self.assertTrue(judge.is_test_path(f"diff --git a/{path} b/{path}"), path)
        self.assertFalse(judge.is_test_path("diff --git a/src/contest.py b/src/contest.py"))


# Speech acts and emergent tags.

class SpeechActsTest(unittest.TestCase):
    def test_labels_outside_the_five_and_unknown_ids_are_dropped_and_skipped_ids_named(self):
        log, ids = standard_log()
        pair_dir = PairDir(self, log)
        reply = {ids["plan"]: "plan", ids["reply"]: "Answer", "999": "Update", ids["claim"]: "Boast"}
        run_command(["speechacts", str(pair_dir.root)], FakeBackend(reply))
        data = pair_dir.judged()
        self.assertEqual(data["speech_acts"], {ids["plan"]: "Plan", ids["reply"]: "Answer"})
        self.assertIn(["999", "Update"], data["calls"]["speech_acts"]["invalid"])
        self.assertIn(ids["question"], data["calls"]["speech_acts"]["missing"])


class EmergentTest(unittest.TestCase):
    def setUp(self):
        log, self.ids = standard_log()
        self.pair_dir = PairDir(self, log)

    def tags(self, reply):
        run_command(["emergent", str(self.pair_dir.root), "--force"], FakeBackend(reply))
        return self.pair_dir.judged()

    def test_role_division_needs_a_verified_quote_from_each_agent(self):
        both = [{"quote": "I will add edit_files()"}, {"quote": "I will NOT edit it"}]
        data = self.tags({"role_division": {"present": True, "evidence": both},
                          "negotiation": {"present": True, "evidence": both[:1]}})
        self.assertTrue(data["emergent"]["role_division"]["present"])
        self.assertFalse(data["emergent"]["negotiation"]["present"])
        self.assertTrue(data["emergent"]["negotiation"]["proposed"])
        self.assertIn("two agents", data["emergent"]["negotiation"]["rejected_reason"])

    def test_resource_division_needs_a_file_or_a_line_range(self):
        vague = [{"quote": "Which lines are you editing?"}, {"quote": "I will NOT edit it"}]
        data = self.tags({"resource_division": {"present": True, "evidence": vague}})
        self.assertFalse(data["emergent"]["resource_division"]["present"])
        specific = [{"quote": "src/click/termui.py lines 80-120"}, {"quote": "I will NOT edit it"}]
        data = self.tags({"resource_division": {"present": True, "evidence": specific}})
        self.assertTrue(data["emergent"]["resource_division"]["present"])

    def test_the_git_settings_judged_failures_are_kept_apart_with_the_blogs_names(self):
        data = self.tags({"blame": {"present": True, "evidence": [{"quote": "Can you confirm the signature"}]}})
        self.assertTrue(data["git_failures"]["blame"]["present"])
        self.assertEqual(data["git_failures"]["blame"]["blog_name"], "Hallucination in Communication")
        self.assertNotIn("blame", data["emergent"])


# The free cross-checks.

def message(id_, sender, to, content, at):
    return judge.Message(id_, sender, to, content, EPOCH + at)


class ConversationHeuristicsTest(unittest.TestCase):
    def test_a_question_nobody_answers_and_one_answered_off_topic(self):
        messages = [message("1", "agent1", "agent2", "Which config format do you prefer?", 1),
                    message("2", "agent2", "agent1", "Pushing my branch soon.", 2),
                    message("3", "agent2", "agent1", "Should I rename the module?", 3)]
        unanswered, ignored = judge.question_checks(messages, ["agent1", "agent2"])
        self.assertEqual(unanswered, ["3"])
        self.assertEqual(ignored, [["1", "2"]])

    def test_a_reply_that_takes_up_the_question_is_not_ignored(self):
        messages = [message("1", "agent1", "agent2", "Which config format do you prefer?", 1),
                    message("2", "agent2", "agent1", "The TOML config format, please.", 2)]
        self.assertEqual(judge.question_checks(messages, ["agent1", "agent2"]), ([], []))

    def test_near_duplicates_are_same_sender_and_marked_status_like(self):
        messages = [message("1", "agent1", "agent2", "Status: done with edit_files in termui.py lines 72-77", 1),
                    message("2", "agent2", "agent1", "Status: done with edit_files in termui.py lines 72-77", 2),
                    message("3", "agent1", "agent2", "Status: done with edit_files in termui.py lines 72-78", 3)]
        found = judge.near_duplicates(messages)
        self.assertEqual([(a, b, s) for a, b, _, s in found], [("1", "3", True)])
        self.assertGreaterEqual(found[0][2], judge.NEAR_DUPLICATE)

    def test_the_keyword_baseline_follows_its_rules_in_order(self):
        messages = [message("1", "agent1", "all", "Plan: I will take cli.py. Which file is yours?", 1),
                    message("2", "agent2", "agent1", "Mine is core.py.", 2),
                    message("3", "agent2", "agent1", "Done with core.py.", 3),
                    message("4", "agent1", "agent2", "Thanks, ok.", 4),
                    message("5", "agent1", "agent2", "Merging now.", 5)]
        labels = judge.keyword_speech_acts(messages, ["agent1", "agent2"])
        self.assertEqual(labels, {"1": "Plan", "2": "Answer", "3": "Update", "4": "Ack", "5": None})

    def test_claims_naming_what_the_patch_lacks(self):
        messages = [message("1", "agent1", "agent2", "Done: I added `edit_files` and open_editor_many() "
                                                     "to src/click/missing.py.", 1)]
        found = judge.contradictions(messages, {"agent1": AGENT1_PATCH})
        self.assertEqual({(f["name"], f["kind"]) for f in found},
                         {("open_editor_many", "identifier"), ("src/click/missing.py", "path")})

    def test_resource_division_and_negotiation_candidates(self):
        log, ids = standard_log()
        messages = judge.conversation_of(PairDir(self, log).pair()).messages
        self.assertEqual(judge.resource_division_candidates(messages), [[ids["plan"], ids["reply"], "termui.py"]])
        options = [message("1", "agent1", "agent2", "Two options: (1) I add IsHash; (2) you add IsRegex. "
                                                    "Which option do you prefer?", 1),
                   message("2", "agent2", "agent1", "Let's do option (1).", 2)]
        self.assertEqual(judge.negotiation_candidates(options), [["1", "2"]])


class OutcomeHeuristicsTest(unittest.TestCase):
    def test_overlapping_hunks_on_one_file(self):
        found = judge.overlapping_hunks({"agent1": AGENT1_PATCH, "agent2": AGENT2_PATCH})
        self.assertEqual(found, [{"file": "src/click/termui.py", "agents": ["agent1", "agent2"],
                                  "ranges": [[80, 85], [84, 87]], "same_heading": "def edit("}])

    def test_insertion_markers_in_code_not_in_tests(self):
        found = judge.insertion_markers({"agent1": AGENT1_PATCH})
        self.assertEqual([(f["file"], f["line"]) for f in found],
                         [("src/click/termui.py", "# agent2 inserts the priority handling here")])

    def test_unanswered_requests_and_a_clean_merge_that_fails(self):
        events = [{"seq": 4, "kind": "request", "request_id": "a"}, {"seq": 5, "kind": "request", "request_id": "b"},
                  {"seq": 6, "kind": "response", "request_id": "a"}]
        self.assertEqual(judge.unanswered_requests(events), ["5"])
        self.assertTrue(judge.clean_merge_failed(evaluation()))
        self.assertIsNone(judge.clean_merge_failed(evaluation(status="conflicts")))

    def test_the_heuristics_command_writes_them_and_names_what_it_could_not_compute(self):
        log, ids = standard_log()
        pair_dir = PairDir(self, log)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(judge.main(["heuristics", str(pair_dir.root)]), 0)
        data = pair_dir.judged()
        heuristics = data["heuristics"]
        self.assertEqual(heuristics["unanswered_questions"], [ids["question"]])
        self.assertEqual([d[:2] for d in heuristics["near_duplicates"]], [[ids["claim"], ids["again"]]])
        self.assertIn("open_editor_many", {c["name"] for c in heuristics["contradictions"]})
        self.assertEqual(heuristics["speech_acts_keyword"][ids["plan"]], "Plan")
        self.assertIn("missing_final_status", data["heuristics_meta"]["not_computed"])
        self.assertNotIn("missing_final_status", heuristics)

    def test_a_pilot_without_text_leaves_the_conversation_checks_out(self):
        log = Log()
        log.add("message", "agent1", 3.0, to="agent2", chars=10)
        data, _ = judge.heuristics_of(PairDir(self, log).pair(), judge.Conversation([], recorded=False,
                                                                                     reason="not recorded"))
        self.assertNotIn("unanswered_questions", data)
        self.assertIn("overlapping_hunks", data)


def transcript(*records):
    return "".join(json.dumps(r) + "\n" for r in records)


def bash(at, command, use_id, output="", is_error=False):
    """A tool call and its result as a Claude Code transcript holds them."""
    return [{"type": "assistant", "timestamp": at, "isSidechain": False,
             "message": {"content": [{"type": "tool_use", "id": use_id, "name": "Bash",
                                      "input": {"command": command}}]}},
            {"type": "user", "timestamp": at, "isSidechain": False,
             "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": output,
                                      "is_error": is_error}]}}]


def edit(at, use_id):
    return [{"type": "assistant", "timestamp": at, "isSidechain": False,
             "message": {"content": [{"type": "tool_use", "id": use_id, "name": "Edit", "input": {}}]}}]


GIT_LOG = """commit 1111111111111111111111111111111111111111 (agent2/main)
Author: agent2 <agent@cooperbench.local>
Date:   Fri Oct 9 22:10:00 2026 +0000

    rewrite termui

 src/click/termui.py | 150 ++++++++++----------------------------------------
 1 file changed, 30 insertions(+), 120 deletions(-)

commit 2222222222222222222222222222222222222222 (agent1/main)
Author: agent1 <agent@cooperbench.local>
Date:   Fri Oct 9 22:05:00 2026 +0000

    add edit_files

 src/click/termui.py | 6 ++++++
 1 file changed, 6 insertions(+)
"""


class TranscriptHeuristicsTest(unittest.TestCase):
    def git_pair(self):
        log = Log()
        log.send("agent1", "agent2", "I pushed edit_files.", 120.0)
        agent1 = bash("2026-10-09T22:01:40Z", "git fetch origin && git log origin/agent2", "a1") \
            + bash("2026-10-09T22:01:50Z", "git commit --amend -m x; git push --force origin main", "a2") \
            + bash("2026-10-09T22:01:55Z", "git push origin main", "a3") \
            + bash("2026-10-09T22:02:00Z", "python -m pytest tests -q", "a4", "1 failed, 3 passed") \
            + edit("2026-10-09T22:30:00Z", "a5")
        agent2 = bash("2026-10-09T22:03:00Z", "git push origin main", "b1") \
            + bash("2026-10-09T22:04:00Z", "git reset --hard origin/main", "b2")
        return PairDir(self, log, result=result_record(arm="coop-git"),
                       files={"agent1_session.jsonl": transcript(*agent1), "agent2_session.jsonl": transcript(*agent2),
                              "git_log.txt": GIT_LOG}).pair()

    def test_git_disasters_impatience_and_broken_code(self):
        found, missing = judge.git_checks(self.git_pair())
        self.assertEqual(missing, {})
        self.assertEqual([(d["agent"], d["kind"]) for d in found["git_disasters"]],
                         [("agent1", "amend then force push"), ("agent2", "reset --hard")])
        self.assertEqual([(i["agent"], i["command"]) for i in found["git_impatience"]],
                         [("agent1", "git fetch origin")])
        self.assertEqual(found["git_broken"], [{"agent": "agent1", "push": "git push origin main",
                                                "test": "python -m pytest tests -q"}])

    def test_a_commit_deleting_a_hundred_lines_of_the_partners_file(self):
        found = judge.git_destructive(GIT_LOG, {"agent1", "agent2"})
        self.assertEqual(found, [{"commit": "111111111111", "author": "agent2", "file": "src/click/termui.py",
                                  "deletions": 120, "approximate": True}])

    def test_an_agent_whose_last_message_precedes_its_last_edit(self):
        pair = self.git_pair()
        missing, times = judge.final_status(pair, judge.conversation_of(pair))
        self.assertEqual(missing, ["agent1"])
        self.assertIsNone(times["agent2"]["last_edit_at"])

    def test_sidechain_records_are_left_out(self):
        records = [{"isSidechain": False, "message": {"content": [{"type": "tool_use", "id": "1", "name": "Bash",
                                                                   "input": {"command": "ls"}}]}},
                   {"isSidechain": True, "message": {"content": [{"type": "tool_use", "id": "2", "name": "Edit",
                                                                  "input": {}}]}}]
        self.assertEqual([c.name for c in judge.tool_uses(judge.main_chain(records))], ["Bash"])

    def test_the_stat_bar_gives_exact_or_scaled_deletions(self):
        self.assertEqual(judge.stat_deletions(6, "++++--"), (2, False))
        self.assertEqual(judge.stat_deletions(300, "+" * 10 + "-" * 50), (250, True))


# Backends.

def fake_claude(directory, reply):
    """A claude that records how it was run and answers with reply."""
    record = directory / "record.json"
    script = directory / "claude"
    script.write_text(f"""#!{sys.executable}
import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("9.9.9 (Claude Code)")
    sys.exit(0)
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "cwd_listing": os.listdir("."), "env": dict(os.environ),
           "stdin": sys.stdin.read()}}, open({str(record)!r}, "w"))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": {reply!r},
                  "total_cost_usd": 0.01, "usage": {{"input_tokens": 5}}, "modelUsage": {{"claude-x": {{}}}}}}))
""", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, record


class ClaudeCliTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="judge-cli-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.scratch, ignore_errors=True))

    def run_fake(self, environ):
        script, record = fake_claude(self.scratch, '{"evidence": []}')
        backend = judge.ClaudeCli("claude-x", binary=str(script))
        with mock.patch.dict(os.environ, environ, clear=False):
            for name in ("CLAUDE_CODE_OAUTH_TOKEN", "COORDBENCH_CLAUDE_TOKEN"):
                if name not in environ:
                    os.environ.pop(name, None)
            text, info = backend.complete("THE SYSTEM PROMPT", "THE CONVERSATION")
        return text, info, json.loads(record.read_text()), backend

    def test_the_prompt_replaces_the_system_prompt_and_every_tool_and_server_is_off(self):
        text, info, seen, backend = self.run_fake({"COORDBENCH_CLAUDE_TOKEN": "tok"})
        self.assertEqual(text, '{"evidence": []}')
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "THE SYSTEM PROMPT")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], judge.EMPTY_MCP)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-x")
        for flag in ("--print", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, argv)
        self.assertNotIn("--append-system-prompt", argv)
        self.assertEqual(seen["stdin"], "THE CONVERSATION")
        self.assertEqual(info["cost_usd"], 0.01)
        self.assertEqual(backend.describe()["cli_version"], "9.9.9 (Claude Code)")

    def test_it_runs_from_an_empty_directory_with_a_throwaway_config_and_the_drivers_token(self):
        _, _, seen, _ = self.run_fake({"COORDBENCH_CLAUDE_TOKEN": "tok", "ANTHROPIC_API_KEY": "key",
                                       "SECRET_ELSEWHERE": "s"})
        env = seen["env"]
        self.assertEqual(seen["cwd_listing"], [])
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("SECRET_ELSEWHERE", env)
        self.assertNotIn("COORDBENCH_CLAUDE_TOKEN", env)
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].startswith(str(Path(seen["cwd"]).parent)))
        self.assertNotEqual(env["HOME"], os.environ.get("HOME"))
        self.assertFalse(Path(seen["cwd"]).exists())

    def test_a_failed_run_is_a_judge_error(self):
        done = mock.Mock(returncode=1, stdout='{"is_error": true, "subtype": "error", "result": "no auth"}',
                         stderr="")
        with self.assertRaises(judge.JudgeError):
            judge.claude_reply(done)


class Endpoint:
    """A local OpenAI-compatible endpoint that records each request."""

    def __init__(self, test, reply):
        seen = self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                seen.append({"path": self.path, "headers": dict(self.headers),
                             "body": json.loads(self.rfile.read(length))})
                answer = json.dumps({"model": "served-model", "usage": {"total_tokens": 9},
                                     "choices": [{"message": {"content": reply}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


class OpenAICompatibleTest(unittest.TestCase):
    def test_a_reasoning_model_gets_no_temperature_and_its_effort_is_sent(self):
        endpoint = Endpoint(self, '{"evidence": []}')
        backend = judge.OpenAICompatible("gpt-5", endpoint.url, key="sk-test", reasoning_effort="high")
        text, info = backend.complete("SYSTEM", "USER")
        request = endpoint.seen[0]
        self.assertEqual(text, '{"evidence": []}')
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertNotIn("temperature", request["body"])
        self.assertEqual(request["body"]["reasoning_effort"], "high")
        self.assertEqual(request["body"]["messages"], [{"role": "system", "content": "SYSTEM"},
                                                       {"role": "user", "content": "USER"}])
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(info["served_models"], ["served-model"])
        self.assertIsNone(backend.describe()["temperature"])

    def test_another_model_gets_temperature_zero(self):
        endpoint = Endpoint(self, "{}")
        judge.OpenAICompatible("gpt-4.1", endpoint.url).complete("S", "U")
        self.assertEqual(endpoint.seen[0]["body"]["temperature"], 0)
        self.assertNotIn("Authorization", endpoint.seen[0]["headers"])

    def test_the_key_comes_from_the_environment_and_the_record_holds_no_secret(self):
        args = judge.parser_of().parse_args(["commerrors", "x", "--backend", "openai-compatible",
                                             "--base-url", "https://user:pw@api.example.com/v1"])
        with mock.patch.dict(os.environ, {judge.JUDGE_KEY: "sk-env"}):
            backend = judge.make_backend(args)
        self.assertEqual((backend.model, backend.key), ("gpt-5", "sk-env"))
        self.assertEqual(backend.describe()["endpoint"], "https://api.example.com/v1")


# The prompts.

PAPER_TABLE_1 = [
    "Both agents independently implement the same functionality, duplicating work and overwriting details.",
    "Incompatible design decisions lead to semantic loss even under a clean merge.",
    "Verbose status messages add little new information and reduce signal.",
    "Direct questions or requests are not answered, breaking the decision loop.",
    "Agent asserts a change or interface decision without evidence the partner can check (no checkable commitment).",
    "Confident completion claims create false shared context when the promised change is absent.",
    "Missing risk communication leaves agents unable to anticipate merged dependency interactions (e.g., circular "
    "imports).",
    "An explicit integration contract exists but is applied differently than agreed.",
    "Ambiguity about a changing interface leaves one agent implementing against an outdated contract.",
    "Agents agree on order but fail to communicate an enforceable plan that preserves it after merge.",
]
APP_G_RULE = ("A key operational distinction in our rubric is between implementation failures (an individual agent "
              "delivers incomplete/buggy code regardless of coordination) and coordination failures (a breakdown "
              "that is only apparent when we consider what agents said and assumed under workspace isolation). "
              "Concretely, we require explicit conversation evidence to assign a coordination-failure label; if "
              "the only evidence is in the code or error trace, we default to implementation-level failure rather "
              "than inferring a coordination breakdown.")


class PromptTest(unittest.TestCase):
    def test_every_prompt_is_ascii_with_lf_and_a_final_newline(self):
        for path in sorted(judge.PROMPTS.iterdir()):
            data = path.read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\r", data, path.name)
            self.assertTrue(data.endswith(b"\n"), path.name)

    def test_every_variant_names_a_prompt_that_is_there(self):
        tables = (judge.COMMERROR_VARIANTS, judge.SYMPTOM_VARIANTS, judge.SPEECH_ACT_VARIANTS,
                  judge.EMERGENT_VARIANTS, judge.CAUSE_VARIANTS)
        for table in tables:
            for variant in table.values():
                self.assertTrue((judge.PROMPTS / variant["file"]).is_file(), variant["file"])

    def test_appF_7_lists_its_codes_and_appF_8_adds_plan_drift_alone(self):
        seven, eight = judge.prompt_text("commerrors-appF-7.txt"), judge.prompt_text("commerrors-appF-8.txt")
        for code in judge.COMMERROR_VARIANTS["appF-7"]["codes"]:
            self.assertIn(f"- {code} {judge.ERROR_NAMES[code]}\n", seven)
        self.assertNotIn("C4a Incorrect claim", seven)
        added = [line for line in eight.splitlines() if line not in seven.splitlines()]
        self.assertEqual(added, ["- C5 Plan drift / unilateral deviation"])
        self.assertTrue(seven.startswith("You are a careful reviewer of two agent collaboration conversations."))

    def test_the_symptom_rubric_carries_the_papers_text_verbatim(self):
        rubric = judge.prompt_text("symptoms-r1.txt")
        self.assertIn("Table 1 | Coordination failure symptoms. Observable patterns in how coordination breakdowns "
                      "surface in merged artifacts.", rubric)
        for name, meaning in zip(judge.TABLE_1, PAPER_TABLE_1):
            self.assertIn(f"- {name}: {meaning}\n", rubric)
        self.assertIn(APP_G_RULE, rubric)
        for name in judge.TABLE_1:
            self.assertIn(f"\n{name}.\n  ", rubric.split("Examples.")[1])
        self.assertIn("A msg: WAIT Agent 10! If you add the section header AND my guid type to your branch, that "
                      "WILL create a merge conflict!", rubric)
        self.assertIn("mit structured labels (a primary symptom plus any secondary symptoms) together with short "
                      "supporting evidence snippets", rubric)
        tree = judge.prompt_text("symptoms-r1-tree.txt")
        added = [line for line in tree.splitlines() if line not in rubric.splitlines()]
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0].startswith("- Information asymmetry:"))

    def test_the_families_follow_the_websites_tree(self):
        self.assertNotIn("Broken commitment", judge.FAMILIES)
        self.assertEqual(set(judge.TABLE_1) - set(judge.FAMILIES), {"Broken commitment"})


class ReportCompatibilityTest(unittest.TestCase):
    """report.py reads judge.json by these keys; a speech act is keyed by
    the id conversation_store.json lists first for its send."""

    def test_the_sections_carry_the_keys_report_py_reads(self):
        log, ids = standard_log()
        pair_dir = PairDir(self, log)
        reply = {"evidence": ["Can you confirm the signature", "Which lines are you editing?"],
                 "issue": {"category": "C1a", "description": "x"}}
        run_command(["commerrors", str(pair_dir.root)], FakeBackend(reply))
        run_command(["speechacts", str(pair_dir.root)], FakeBackend({ids["plan"]: "Plan"}))
        data = pair_dir.judged()
        self.assertEqual(data["format"], judge.FORMAT)
        self.assertEqual(set(data["commerrors"]["issue"]), {"code", "group", "description"})
        self.assertEqual(data["pair"]["arm"], "coop")
        plan_event = next(e for e in log.events if e.get("kind") == "message" and str(e["message_id"]) == ids["plan"])
        self.assertEqual(plan_event["message_id"], int(next(iter(data["speech_acts"]))))


if __name__ == "__main__":
    unittest.main()
