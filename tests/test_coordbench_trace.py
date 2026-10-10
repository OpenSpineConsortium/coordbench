"""trace.py is the one stream every other coordbench module logs into, so
these pin its contract clause by clause: the level from the environment,
the fast paths that make it free when off or unheard, the shape of an
event, debug gating, spans in both forms, listener handling and the two
listeners it ships."""

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
TRACE_PY = COORDBENCH / "trace.py"
sys.path.insert(0, str(COORDBENCH))

# coordbench's trace.py shares its name with the standard library's tracer;
# a process that happened to load that one first gets ours here instead.
if getattr(sys.modules.get("trace"), "__file__", None) != str(TRACE_PY):
    sys.modules.pop("trace", None)
import trace  # noqa: E402


def lines_run(function, *args, **kwargs):
    """The line numbers executed inside function's own code, in order."""
    code, lines = function.__code__, []

    def tracer(frame, what, arg):
        if frame.f_code is code and what == "line":
            lines.append(frame.f_lineno)
        return tracer

    sys.settrace(tracer)
    try:
        function(*args, **kwargs)
    finally:
        sys.settrace(None)
    return lines


def level_in_subprocess(environment):
    """trace.level as a fresh interpreter reads it, with COORD_TRACE as given (None: unset)."""
    env = {key: value for key, value in os.environ.items() if key != "COORD_TRACE"}
    if environment is not None:
        env["COORD_TRACE"] = environment
    code = f"import sys; sys.path.insert(0, {str(COORDBENCH)!r}); import trace; print(trace.level)"
    done = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True,
                          cwd=tempfile.gettempdir(), check=True)
    return done.stdout.strip(), done.stderr


class TraceCase(unittest.TestCase):
    """Every test starts at prod with nobody listening and leaves it so."""

    def setUp(self):
        self.reset()
        self.addCleanup(self.reset)

    @staticmethod
    def reset():
        trace.set_level("prod")
        for listener in list(trace._listeners):
            trace.unlisten(listener)

    def memory(self):
        return trace.listen(trace.Memory())


class LevelTests(TraceCase):
    def test_level_comes_from_the_environment_with_prod_as_default(self):
        self.assertEqual(level_in_subprocess(None), ("prod", ""))
        self.assertEqual(level_in_subprocess("debug"), ("debug", ""))
        self.assertEqual(level_in_subprocess("off"), ("off", ""))

    def test_an_unknown_coord_trace_is_prod_under_one_complaint(self):
        level, stderr = level_in_subprocess("verbose")
        self.assertEqual(level, "prod")
        self.assertEqual(stderr.count("\n"), 1)
        self.assertIn("COORD_TRACE='verbose'", stderr)

    def test_set_level_changes_the_level_and_refuses_an_unknown_one(self):
        self.assertEqual(trace.level, "prod")
        trace.set_level("debug")
        self.assertEqual(trace.level, "debug")
        trace.set_level("off")
        self.assertEqual(trace.level, "off")
        with self.assertRaises(ValueError):
            trace.set_level("loud")
        self.assertEqual(trace.level, "off")

    def test_the_module_imports_only_the_standard_library(self):
        tree = ast.parse(TRACE_PY.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"functools", "json", "os", "sys", "threading", "time"})
        self.assertLess(len(TRACE_PY.read_text(encoding="utf-8").splitlines()), 200)


class FastPathTests(TraceCase):
    def assert_one_check(self, function, *args, **kwargs):
        """The function ran its check and returned: two lines, the first of
        its body, and never looked at the clock."""
        with mock.patch.object(trace, "time") as clock:
            lines = lines_run(function, *args, **kwargs)
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(lines[0], min(line for line in lines))
        clock.monotonic.assert_not_called()
        clock.time.assert_not_called()

    def test_event_when_off_is_one_check_even_with_a_listener(self):
        memory = self.memory()
        trace.set_level("off")
        self.assert_one_check(trace.event, "tool", agent="agent1")
        self.assertEqual(memory, [])

    def test_event_with_nobody_listening_is_one_check(self):
        self.assert_one_check(trace.event, "tool", agent="agent1")

    def test_debug_outside_debug_is_one_comparison(self):
        memory = self.memory()
        self.assert_one_check(trace.debug, "queue", depth=3)
        self.assertEqual(memory, [])

    def test_a_listener_in_prod_takes_the_slow_path(self):
        self.memory()
        self.assertGreater(len(lines_run(trace.event, "tool")), 2)


class EventTests(TraceCase):
    def test_event_shape(self):
        memory = self.memory()
        payload = {"text": "hello"}
        before = time.time()
        trace.event("message", agent="agent1", to="agent2", body=payload)
        self.assertEqual(len(memory), 1)
        record = memory[0]
        self.assertEqual(list(record), ["kind", "t", "ts", "agent", "to", "body"])
        self.assertEqual(record["kind"], "message")
        self.assertIsInstance(record["t"], float)
        self.assertGreaterEqual(record["t"], 0)
        self.assertLessEqual(record["t"], time.monotonic() - trace.started)
        self.assertGreaterEqual(record["ts"], before)
        self.assertLessEqual(record["ts"], time.time())
        self.assertEqual(record["agent"], "agent1")
        self.assertIs(record["body"], payload, "a field is the caller's own object, not a copy")

    def test_event_returns_nothing_and_hands_the_same_dict_to_every_listener(self):
        first, second = self.memory(), self.memory()
        self.assertIsNone(trace.event("tool", agent="agent1"))
        self.assertIs(first[0], second[0])

    def test_a_callers_own_clock_replaces_t(self):
        memory = self.memory()
        trace.event("tool", t=1.5, seq=7, run="r1")
        self.assertEqual(memory[0]["t"], 1.5)
        self.assertEqual(memory[0]["seq"], 7)
        self.assertEqual(memory[0]["run"], "r1")

    def test_debug_emits_only_in_debug(self):
        memory = self.memory()
        trace.debug("queue", depth=1)
        self.assertEqual(memory, [])
        trace.set_level("debug")
        trace.debug("queue", depth=2)
        self.assertEqual([(e["kind"], e["depth"]) for e in memory], [("queue", 2)])
        trace.set_level("prod")
        trace.debug("queue", depth=3)
        self.assertEqual(len(memory), 1)

    def test_started_and_now_ms(self):
        self.assertIsInstance(trace.started, float)
        self.assertLessEqual(trace.started, time.monotonic())
        first = trace.now_ms()
        self.assertIsInstance(first, float)
        self.assertGreaterEqual(first, 0)
        self.assertEqual(first, round(first, 3))
        time.sleep(0.002)
        self.assertGreater(trace.now_ms(), first)


class SpanTests(TraceCase):
    def test_span_as_a_context_manager(self):
        memory = self.memory()
        with trace.span("provision", agent="agent1"):
            time.sleep(0.01)
        self.assertEqual([e["kind"] for e in memory], ["provision", "provision_done"])
        start, done = memory
        self.assertEqual(start["agent"], "agent1")
        self.assertEqual(done["agent"], "agent1")
        self.assertIs(done["ok"], True)
        self.assertIsInstance(done["duration_ms"], float)
        self.assertEqual(done["duration_ms"], round(done["duration_ms"], 3))
        self.assertGreaterEqual(done["duration_ms"], 10)
        self.assertNotIn("duration_ms", start)

    def test_span_records_a_failure_and_lets_it_propagate(self):
        memory = self.memory()
        with self.assertRaises(KeyError):
            with trace.span("score", agent="agent2"):
                raise KeyError("missing")
        self.assertEqual([e["kind"] for e in memory], ["score", "score_done"])
        self.assertIs(memory[1]["ok"], False)
        self.assertEqual(memory[1]["agent"], "agent2")

    def test_span_as_a_decorator_adds_no_fields_of_its_own(self):
        memory = self.memory()

        @trace.span("score")
        def score(a, b=1):
            """Adds."""
            return a + b

        self.assertEqual(score.__name__, "score")
        self.assertEqual(score.__doc__, "Adds.")
        self.assertEqual(score(2, b=3), 5)
        self.assertEqual([e["kind"] for e in memory], ["score", "score_done"])
        self.assertEqual(set(memory[0]), {"kind", "t", "ts"})
        self.assertEqual(set(memory[1]), {"kind", "t", "ts", "duration_ms", "ok"})
        self.assertIs(memory[1]["ok"], True)

    def test_a_decorated_function_that_raises_is_recorded_and_still_raises(self):
        memory = self.memory()

        @trace.span("close", run="r1")
        def close():
            raise ValueError("no")

        with self.assertRaises(ValueError):
            close()
        self.assertEqual([(e["kind"], e["run"]) for e in memory], [("close", "r1"), ("close_done", "r1")])
        self.assertIs(memory[1]["ok"], False)

    def test_a_decorated_function_measures_each_call_on_its_own(self):
        memory = self.memory()
        entered = threading.Event()

        @trace.span("agent")
        def work(seconds, mark=None):
            if mark:
                mark.set()
            time.sleep(seconds)

        slow = threading.Thread(target=work, args=(0.05, entered))
        slow.start()
        entered.wait(5)
        work(0)
        slow.join(5)
        done = [e["duration_ms"] for e in memory if e["kind"] == "agent_done"]
        self.assertEqual(len(done), 2, memory)
        quick, long = done
        self.assertGreaterEqual(long, 50)
        self.assertLess(quick, long)

    def test_debug_span_is_only_recorded_in_debug(self):
        memory = self.memory()
        with trace.debug_span("lock", agent="agent1"):
            pass

        @trace.debug_span("wait")
        def wait():
            return "done"

        self.assertEqual(wait(), "done")
        self.assertEqual(memory, [])
        trace.set_level("debug")
        with trace.debug_span("lock", agent="agent1"):
            pass
        wait()
        self.assertEqual([e["kind"] for e in memory], ["lock", "lock_done", "wait", "wait_done"])
        self.assertEqual(memory[1]["agent"], "agent1")
        self.assertIs(memory[3]["ok"], True)

    def test_a_debug_span_entered_in_debug_closes_even_if_the_level_drops(self):
        memory = self.memory()
        trace.set_level("debug")
        with trace.debug_span("lock"):
            trace.set_level("prod")
        self.assertEqual([e["kind"] for e in memory], ["lock", "lock_done"])


class ListenerTests(TraceCase):
    def test_listen_returns_its_argument_and_registers_it_once(self):
        seen = []

        @trace.listen
        def sink(event):
            seen.append(event["kind"])

        self.assertTrue(callable(sink))
        self.assertIs(trace.listen(sink), sink)
        trace.event("tool")
        self.assertEqual(seen, ["tool"])

    def test_two_empty_memories_are_both_registered(self):
        first, second = self.memory(), self.memory()
        self.assertEqual(first, second, "empty lists compare equal, which must not merge them")
        trace.event("tool")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_unlisten_removes_a_listener_and_tolerates_a_stranger(self):
        memory = self.memory()
        trace.unlisten(memory)
        trace.unlisten(memory)
        trace.unlisten(lambda event: None)
        trace.event("tool")
        self.assertEqual(memory, [])

    def test_a_raising_listener_is_dropped_after_one_complaint(self):
        memory = self.memory()
        calls = []

        def bad(event):
            calls.append(event["kind"])
            raise RuntimeError("sink is broken")

        trace.listen(bad)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            trace.event("tool", agent="agent1")
            trace.event("tool_done", agent="agent1")
        self.assertEqual(calls, ["tool"])
        self.assertEqual([e["kind"] for e in memory], ["tool", "tool_done"])
        complaint = stderr.getvalue()
        self.assertEqual(complaint.count("\n"), 1)
        self.assertIn("RuntimeError('sink is broken')", complaint)
        self.assertIn("no more events", complaint)
        self.assertNotIn(bad, [known for known in trace._listeners])

    def test_memory_is_a_list_that_collects_in_order(self):
        memory = trace.Memory()
        self.assertIsInstance(memory, list)
        trace.listen(memory)
        trace.event("a")
        trace.event("b", n=2)
        self.assertEqual([e["kind"] for e in memory], ["a", "b"])
        self.assertEqual(memory[1]["n"], 2)
        self.assertEqual(json.loads(json.dumps(memory))[0]["kind"], "a")


class JsonLinesTests(TraceCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = self.dir / "events.jsonl"

    def read(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_a_path_is_opened_on_the_first_event_and_appended_one_flushed_line_at_a_time(self):
        self.path.write_text('{"kind": "earlier"}\n', encoding="utf-8")
        sink = trace.listen(trace.JsonLines(str(self.path)))
        self.assertEqual(len(self.read()), 1)
        trace.event("tool", agent="agent1")
        self.assertEqual([e["kind"] for e in self.read()], ["earlier", "tool"], "flushed while still open")
        trace.event("tool_done", agent="agent1", duration_ms=1.5)
        records = self.read()
        self.assertEqual([e["kind"] for e in records], ["earlier", "tool", "tool_done"])
        self.assertEqual(records[2]["duration_ms"], 1.5)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("\n"), 3)
        sink.close()
        trace.event("late")
        self.assertEqual([e["kind"] for e in self.read()][-1], "late", "a closed sink reopens, appending")
        sink.close()

    def test_a_path_that_never_sees_an_event_is_never_created(self):
        sink = trace.JsonLines(self.path)
        trace.listen(sink)
        sink.close()
        self.assertFalse(self.path.exists())

    def test_a_stream_is_written_flushed_and_left_open(self):
        class Stream(io.StringIO):
            flushes = 0

            def flush(self):
                self.flushes += 1
                super().flush()

        stream = Stream()
        sink = trace.listen(trace.JsonLines(stream))
        trace.event("a")
        trace.event("b")
        self.assertEqual([json.loads(line)["kind"] for line in stream.getvalue().splitlines()], ["a", "b"])
        self.assertEqual(stream.flushes, 2)
        sink.close()
        self.assertFalse(stream.closed)

    def test_a_field_that_is_not_json_is_written_as_its_repr_and_keeps_the_sink(self):
        sink = trace.listen(trace.JsonLines(self.path))
        self.addCleanup(sink.close)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            trace.event("path", where=Path("/tmp/x"))
            trace.event("after")
        self.assertEqual(stderr.getvalue(), "")
        records = self.read()
        self.assertEqual(records[0]["where"], repr(Path("/tmp/x")))
        self.assertEqual(records[1]["kind"], "after")

    def test_lines_from_many_threads_never_interleave(self):
        sink = trace.listen(trace.JsonLines(self.path))
        self.addCleanup(sink.close)

        def burst(name):
            for n in range(50):
                trace.event("tick", thread=name, n=n)

        threads = [threading.Thread(target=burst, args=(f"t{i}",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        records = self.read()
        self.assertEqual(len(records), 400)
        self.assertEqual({(r["thread"], r["n"]) for r in records}, {(f"t{i}", n) for i in range(8) for n in range(50)})


if __name__ == "__main__":
    unittest.main()
