"""compare.py and viewer/compare.html: the report command embeds the runs
into a copy of the page, and the page reads every shape the exporter and
CooperBench's website write. The page's schema block must be the
exporter's SCHEMA, so the two cannot drift; its palette must cover every
tool type in both themes; it must load nothing from outside. When node is
on PATH, the page's own normaliser runs over one sample of each shape
(as the page loads none of its DOM code until start(), the model can run
alone); elsewhere that part is skipped, as on a machine without node.
When node can load Playwright, the report is also laid out at a phone's
width in headless Chromium."""

import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coordbench"))

import compare  # noqa: E402
import export  # noqa: E402

PAGE = ROOT / "coordbench" / "viewer" / "compare.html"
FIXTURE = ROOT / "tests" / "fixtures" / "export" / "coop_pair"
NO_DATASET = "/nonexistent-cooperbench"


def page_text():
    return PAGE.read_text(encoding="utf-8")


def block(html, element_id):
    match = re.search(r'<script id="' + element_id + r'" type="application/json">(.*?)</script>', html, re.S)
    return match.group(1)


class Page(unittest.TestCase):

    def test_the_schema_block_is_the_exporters(self):
        self.assertEqual(json.loads(block(page_text(), "coordbench-schema")), json.loads(json.dumps(export.SCHEMA)))

    def test_the_page_is_ascii_with_lf_and_a_final_newline(self):
        data = PAGE.read_bytes()
        data.decode("ascii")
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"\n"))

    def test_a_bar_has_no_minimum_width(self):
        """A bar is as wide as its value; a minimum would draw a stub for a
        "-" or a 0."""
        rule = re.search(r"\.bar \{([^}]*)\}", page_text()).group(1)
        self.assertNotIn("min-width", rule)

    def test_the_page_loads_nothing_from_outside(self):
        html = page_text()
        self.assertNotRegex(html, r"<script[^>]+src=")
        self.assertNotRegex(html, r"<link[^>]+href=")
        self.assertNotRegex(html, r"@import|url\(\s*['\"]?https?:")

    def test_every_tool_type_has_a_colour_in_both_themes(self):
        html = page_text()
        light, dark = html.split("@media (prefers-color-scheme: dark)", 1)
        for tool_type in export.TOOL_TYPES:
            self.assertIn(f"--tt-{tool_type}:", light)
            self.assertIn(f"--tt-{tool_type}:", dark.split("}", 2)[0] + dark.split("}", 2)[1])
            self.assertIn(f".tt-{tool_type} {{", html)

    def test_the_embedded_block_is_empty_until_a_report_fills_it(self):
        self.assertIn(compare.PLACEHOLDER, page_text())

    def test_the_normaliser_reads_the_schema_it_is_given(self):
        html = page_text()
        for name in ("SCHEMA.format", "SCHEMA.sdk_actions", "SCHEMA.tool_types", "SCHEMA.no_new_messages",
                     "SCHEMA.repo_aliases", "SCHEMA.test_command"):
            self.assertIn(name, html)
        self.assertIn('c.timestamps === "session-jsonl"', html)


class Report(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root)

    def bundle(self, html):
        return json.loads(block(html, "embedded-runs"))

    def published_site(self):
        """A website tree with one published trajectory, its index row,
        a cause entry and the task titles."""
        data = self.root / "site" / "static" / "data"
        trajectories = data / "coop" / "trajectories" / "gpt5"
        trajectories.mkdir(parents=True)
        steps = [{"id": 0, "timestamp": "2025-10-20T14:23:29", "source": "agent", "action": "run",
                  "args": {"command": "ls"}, "agentId": "agent_1", "tool_call_metadata": {"function_name": "execute_bash"}}]
        path = trajectories / "typst_task6554_feature1_feature2.json.gz"
        path.write_bytes(gzip.compress(json.dumps(steps).encode()))
        index = {"models": ["gpt5"], "tasks": [{"repo": "typst", "taskId": "task6554", "features": "feature1_feature2",
                                               "results": {"gpt5": {"passed": True, "hasConflict": False}}}]}
        (data / "coop" / "index.json").write_text(json.dumps(index))
        (data / "causes").mkdir()
        (data / "causes" / "index.json").write_text(json.dumps({"expectation": [{"classification": "expectation",
            "task_id": "task6554", "model": "gpt5_k1", "features": "feature1_feature2", "project": "typst"}],
            "commitment": [], "communication": []}))
        (data / "tasks.json").write_text(json.dumps([{"repoKey": "typst", "taskId": "task6554", "features": [
            {"id": "feature1", "title": "One"}, {"id": "feature2", "title": "Two"}, {"id": "feature3", "title": "Three"}]}]))
        return path

    def test_a_pair_directory_is_exported_on_the_fly_with_its_patches(self):
        bundle = self.bundle(compare.report([FIXTURE], cooperbench_dir=NO_DATASET))
        run = bundle["runs"][0]
        self.assertEqual(run["label"], "coop claude-cli fixture")
        self.assertEqual(run["data"]["metadata"]["coordbench"]["format"], export.FORMAT)
        self.assertEqual(sorted(run["extras"]["patches"]), ["agent1.patch", "agent6.patch"])
        self.assertFalse((FIXTURE / "trajectory.json.gz").exists())

    def test_a_published_file_brings_its_row_cause_and_titles(self):
        path = self.published_site()
        run = self.bundle(compare.report([path]))["runs"][0]
        extras = run["extras"]
        self.assertEqual(run["label"], "gpt5 typst_task6554_feature1_feature2")
        self.assertEqual(extras["index_result"], {"passed": True, "hasConflict": False})
        self.assertEqual(extras["causes"][0]["classification"], "expectation")
        self.assertEqual(extras["feature_titles"], [{"feature": "feature1", "title": "One"},
                                                    {"feature": "feature2", "title": "Two"}])

    def test_a_typst_pair_s_result_is_found_in_whichever_row_holds_it(self):
        """The website keeps a typst pair as two rows, typst for the paper
        models and typst_task for the Gemini runs; a Gemini file finds its
        result in the second whatever name it is asked by."""
        self.published_site()
        data = self.root / "site" / "static" / "data"
        index = json.loads((data / "coop" / "index.json").read_text())
        index["tasks"].append({"repo": "typst_task", "taskId": "task6554", "features": "feature1_feature2",
                               "results": {"gemini_flash_sdk": {"passed": False, "hasConflict": True}}})
        (data / "coop" / "index.json").write_text(json.dumps(index))
        gemini = data / "coop" / "trajectories" / "gemini_flash_sdk"
        gemini.mkdir()
        for name in ("typst_task_task6554_feature1_feature2.json.gz", "typst_task6554_feature1_feature2.json.gz"):
            (gemini / name).write_bytes(gzip.compress(json.dumps([]).encode()))
            extras = compare.site_extras(gemini / name)
            self.assertEqual(extras.get("index_result"), {"passed": False, "hasConflict": True}, name)

    def test_a_pair_the_index_lacks_is_marked_not_published(self):
        path = self.published_site()
        renamed = path.with_name("typst_task6554_feature1_feature3.json.gz")
        path.rename(renamed)
        extras = self.bundle(compare.report([renamed]))["runs"][0]["extras"]
        self.assertTrue(extras["index_missing"])

    def test_labels_and_baseline_are_kept(self):
        bundle = self.bundle(compare.report([FIXTURE, FIXTURE], ["one", "two"], 1, NO_DATASET))
        self.assertEqual([r["label"] for r in bundle["runs"]], ["one", "two"])
        self.assertEqual(bundle["baseline"], 1)
        self.assertEqual(self.bundle(compare.report([FIXTURE], baseline=5, cooperbench_dir=NO_DATASET))["baseline"], 0)

    def test_no_text_in_a_run_can_close_the_script_element(self):
        html = compare.embedded_json({"text": "</script><script>alert(1)</script><!--"})
        self.assertNotIn("<", html)
        self.assertEqual(json.loads(html)["text"], "</script><script>alert(1)</script><!--")

    def test_the_command_writes_one_file(self):
        out = self.root / "report.html"
        self.assertEqual(compare.main([str(FIXTURE), "-o", str(out), "--cooperbench-dir", NO_DATASET]), 0)
        html = out.read_text()
        self.assertNotIn(compare.PLACEHOLDER, html)
        html.encode("ascii")

    def test_a_missing_run_is_an_error(self):
        self.assertEqual(compare.main([str(self.root / "nothing.json"), "-o", str(self.root / "x.html")]), 1)


NODE = shutil.which("node")
HARNESS = r"""
const fs = require("fs"); const vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const schema = html.match(/<script id="coordbench-schema" type="application\/json">([\s\S]*?)<\/script>/)[1];
const scripts = html.match(/<script>([\s\S]*?)<\/script>/g);
const main = scripts[scripts.length - 1].replace(/^<script>/, "").replace(/<\/script>$/, "");
const sandbox = { console, URLSearchParams, TextDecoder, Date, JSON, Math, Set, Object, Array, String, Number, RegExp,
  window: { COORDBENCH_COMPARE_NO_START: true, location: { search: "" } },
  document: { getElementById: (id) => ({ textContent: id === "coordbench-schema" ? schema : "null" }) } };
vm.createContext(sandbox);
vm.runInContext(main + "\n;globalThis.__api = { detectShape, normalise, matrixCell, MATRIX_COLUMNS, judgeCaption, outcomeParts, " +
  "cellClass, stepsText, listCountText, kindBadge, agentStepsCell, agentMetrics };", sandbox);
const api = sandbox.__api;
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((c) => {
  try {
    const run = api.normalise(c.data, { label: c.label, extras: c.extras || {} });
    const cells = {};
    api.MATRIX_COLUMNS.forEach(([key]) => { const cell = api.matrixCell(run, key); cells[key] = [cell.state, cell.source]; });
    const classes = {};
    api.MATRIX_COLUMNS.forEach(([key]) => { classes[key] = api.cellClass(key, api.matrixCell(run, key).state); });
    return { label: c.label, shape: run.shape, steps: run.steps.length, hidden: run.steps.filter((s) => s.hidden).length,
      lanes: run.lanes, messages: run.messages.map((m) => [m.from, m.to, m.read_ms != null]), kind: run.timestamps_kind,
      speed: run.speed, outcome: run.outcome, cells, classes, flags: run.flags, warnings: run.warnings.length,
      judge: run.labels.judge_meta ? api.judgeCaption(run.labels.judge_meta) : null,
      badges: api.outcomeParts(run).map((b) => [b.text, b.title]), clock: run.clock.anchor, start_ms: run.start_ms,
      end_ms: run.end_ms, agent_steps: run.agents.map((a) => [a.steps, a.steps_less]), steps_text: api.stepsText(run),
      list_text: api.listCountText(run, 1), agent_tokens: run.agents.map((a) => a.tokens_reason || null),
      agent_costs: run.agents.map((a) => [a.cost, a.cost_source, a.cost_reason]), kind_badge: api.kindBadge(run),
      steps_cell: api.agentStepsCell(run), agent_spans: run.agents.map((a) => api.agentMetrics(run, a).wall_ms),
      agent_shares: run.agents.map((a) => api.agentMetrics(run, a).comm_share) };
  } catch (error) { return { label: c.label, error: error.message }; }
});
process.stdout.write(JSON.stringify(out));
"""


def legacy_sample():
    def event(agent, n, t, **fields):
        return {"id": n, "timestamp": f"2025-10-20T14:23:{t:02d}.000000", "agentId": agent, **fields}
    send = {"action": "call_tool_mcp", "source": "agent", "tool_call_metadata": {"function_name": "openhands_comm_send"},
            "args": {"name": "openhands_comm_send", "arguments": {"content": "I take prompts.py lines 1-40?"}}}
    return [event("agent_1", 0, 1, action="system", source="agent", message="You are OpenHands agent"),
            event("agent_1", 1, 2, action="message", source="user", message="You are agent_1 working on"),
            event("agent_1", 2, 5, **send),
            event("agent_1", 3, 6, observation="mcp", source="agent", cause=2, content="Message sent successfully"),
            event("agent_1", 4, 8, action="read", source="agent", tool_call_metadata={"function_name": "str_replace_editor"},
                  args={"path": "/workspace/a.py"}),
            event("agent_2", 0, 1, action="system", source="agent", message="You are OpenHands agent"),
            event("agent_2", 1, 9, action="message", source="user",
                  message="[Inter-agent message] From agent_1 at 2025-10-20T14:23:05: I take prompts.py lines 1-40?"),
            event("agent_2", 2, 12, action="finish", source="agent", tool_call_metadata={"function_name": "finish"},
                  args={"final_thought": "done"})]


def website_steps(sdk):
    action = "TerminalAction" if sdk else "ls -la"
    steps = [{"id": 0, "timestamp": "2026-02-04T04:17:18+00:00", "source": "agent", "action": action, "args": {},
              "agentId": "agent_1", "tool_call_metadata": {"function_name": "execute_bash"}, "thought": ""},
             {"id": 1, "timestamp": "2026-02-04T04:17:30+00:00", "source": "agent", "action": "send_message agent2",
              "args": {"recipient": "agent2", "content": "hello"}, "agentId": "agent_1",
              "tool_call_metadata": {"function_name": "openhands_comm_send"}},
             {"id": 2, "timestamp": "2026-02-04T04:17:40+00:00", "source": "agent", "action": "receive_message from agent1",
              "args": {"content": "hello"}, "agentId": "agent_2", "tool_call_metadata": {"function_name": "openhands_comm_get"}}]
    return {"metadata": {"repo": "pallets_click_task", "task_id": "task2068", "features": "feature9_feature12",
                         "agent_1": {"model": "m", "steps": 0, "cost": 0}}, "steps": steps}


def seven_key_sample():
    return [{"id": 1, "timestampMs": 1762830005732, "agentId": "agent_6", "toolName": "execute_bash", "toolType": "bash"},
            {"id": 2, "timestampMs": 1762830006732, "agentId": "agent_3", "toolName": "openhands_comm_send",
             "toolType": "communication", "details": "plan"}]


def stepless_sample():
    events = [json.loads(line) for line in (FIXTURE / "events.jsonl").read_text().splitlines()]
    return {"coordbench_pair_files": {"result": json.loads((FIXTURE / "result.json").read_text()),
                                      "evaluation": json.loads((FIXTURE / "eval.json").read_text()), "events": events,
                                      "conversation": None, "judge": json.loads((FIXTURE / "judge.json").read_text())}}


def stepless_pilot_sample():
    """The fixture's pair files as a session-driver pilot left them: no
    run_started event and no timings."""
    files = stepless_sample()["coordbench_pair_files"]
    files["events"] = [e for e in files["events"] if e["kind"] != "run_started"]
    del files["result"]["timings"]
    return {"coordbench_pair_files": files}


def silent_sample():
    """The fixture's export with judge.json as judge.py writes it for a
    failed pair with no messages: App G's rule decided the symptoms and
    no model was called."""
    data = export.build_trajectory(FIXTURE, NO_DATASET)
    data["metadata"]["coordbench"]["failure_modes"] = {"calls": {}, "symptoms": {
        "prompt_variant": "symptoms-r1", "decided_by": "rule", "level": "implementation", "primary": None,
        "secondary": [], "evidence": [], "coerced": True}}
    return data


def partial_sample():
    """The fixture's export with one agent's output count not measured."""
    data = export.build_trajectory(FIXTURE, NO_DATASET)
    data["metadata"]["coordbench"]["agents"][1]["tokens"]["output"] = None
    return data


def unfinished_sample():
    """The fixture's export with agent2's last API message lacking its
    stop_reason, as most messages of Claude Code 2.1.295's transcripts do:
    its output count is not final, so neither is the total."""
    with tempfile.TemporaryDirectory() as root:
        pair = Path(root) / "pair"
        shutil.copytree(FIXTURE, pair)
        lines = [json.loads(line) for line in (pair / "agent2_session.jsonl").read_text().splitlines()]
        last = max(i for i, line in enumerate(lines) if line.get("type") == "assistant")
        lines[last]["message"]["stop_reason"] = None
        (pair / "agent2_session.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
        return export.build_trajectory(pair, NO_DATASET)


def pilot_sample():
    """The fixture as a session-driver pilot recorded it, before per-agent
    timings and run_started: no transcript, no timings, the hub's events
    counted from a clock the run never wrote down."""
    with tempfile.TemporaryDirectory() as root:
        pair = Path(root) / "pair"
        shutil.copytree(FIXTURE, pair)
        for name in ("agent1_session.jsonl", "agent2_session.jsonl", "agent1_stream.jsonl"):
            (pair / name).unlink()
        events = [json.loads(line) for line in (FIXTURE / "events.jsonl").read_text().splitlines()]
        (pair / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events if e["kind"] != "run_started"))
        result = json.loads((pair / "result.json").read_text())
        result["driver"] = "session"
        del result["timings"]
        (pair / "result.json").write_text(json.dumps(result))
        return export.build_trajectory(pair, NO_DATASET)


def costed_sample():
    """The fixture as the session driver records cost: agent1's priced by
    Claude Code, agent2's output counts not final, so it has no cost and
    only cost_lower_bound."""
    with tempfile.TemporaryDirectory() as root:
        pair = Path(root) / "pair"
        shutil.copytree(FIXTURE, pair)
        result = json.loads((pair / "result.json").read_text())
        result["agents"]["agent1"]["cost_source"] = "claude code"
        result["agents"]["agent2"].update({"cost": None, "cost_source": None, "cost_lower_bound": 0.04})
        (pair / "result.json").write_text(json.dumps(result))
        return export.build_trajectory(pair, NO_DATASET)


UNRESOLVED_EVAL = {"merge": {"status": "conflicts", "strategy": "naive", "diff": ""}, "both_passed": False,
                   "cascade": {"verdict_paper_era": False, "route": "none", "lower_bound": True,
                               "has_naive_merge_conflict": True,
                               "llm": {"ran": False, "needed": True, "reason": "no resolver endpoint"}}}


def unresolved_sample():
    """The fixture scored as check/e2egit was: the naive merge conflicted,
    the lead's patch alone failed (63b9d44 leaves the strategy naive), and
    the paper-era cascade failed naive and union and needed the learned
    resolver, which did not run."""
    with tempfile.TemporaryDirectory() as root:
        pair = Path(root) / "pair"
        shutil.copytree(FIXTURE, pair)
        evaluation = json.loads((pair / "eval.json").read_text())
        evaluation.update({"merge": UNRESOLVED_EVAL["merge"], "both_passed": False})
        evaluation["cascade"].update(UNRESOLVED_EVAL["cascade"])
        (pair / "eval.json").write_text(json.dumps(evaluation))
        return export.build_trajectory(pair, NO_DATASET)


def unresolved_files_sample():
    files = stepless_sample()["coordbench_pair_files"]
    files["evaluation"].update({"merge": UNRESOLVED_EVAL["merge"], "both_passed": False})
    files["evaluation"]["cascade"].update(UNRESOLVED_EVAL["cascade"])
    return {"coordbench_pair_files": files}


def legacy_event(agent, n, seconds, **fields):
    moment = datetime(2025, 10, 20, 14, 0, 0) + timedelta(seconds=seconds)
    return {"id": n, "timestamp": moment.isoformat(timespec="microseconds"), "agentId": agent, "source": "agent", **fields}


def short_log_sample():
    """CooperBench's gpt5 pallets_click 2068 feature1_feature6 file in
    small: agent_1's log is a few events over seconds of agent_2's
    twenty minutes."""
    run = {"action": "run", "tool_call_metadata": {"function_name": "execute_bash"}, "args": {"command": "ls"}}
    events = [legacy_event("agent_2", i, i * 60, **run) for i in range(21)]
    events += [legacy_event("agent_1", 0, 1000, **run), legacy_event("agent_1", 1, 1007, **run)]
    return events


def finished_short_sample():
    """short_log_sample with agent_1's few seconds ending in its finish:
    it was done early while agent_2 kept working, and its log is whole."""
    finish = {"action": "finish", "tool_call_metadata": {"function_name": "finish"}, "args": {"final_thought": "done"}}
    return short_log_sample() + [legacy_event("agent_1", 2, 1009, **finish)]


def apart_sample(short):
    """Two agents whose logs do not overlap, an hour apart: each is ten
    minutes long, or agent_1's is two seconds when short."""
    run = {"action": "run", "tool_call_metadata": {"function_name": "execute_bash"}, "args": {"command": "ls"}}
    first = [legacy_event("agent_1", i, i * (0.2 if short else 60), **run) for i in range(11)]
    return first + [legacy_event("agent_2", i, 3600 + i * 60, **run) for i in range(11)]


def judged_sample():
    """The fixture's export with judge.json's calls, as judge.py writes
    them: one entry per section, each naming its own judge."""
    data = export.build_trajectory(FIXTURE, NO_DATASET)
    data["metadata"]["coordbench"]["failure_modes"]["calls"] = {
        "commerrors": {"backend": "claude-cli", "model": "m1", "prompt_variant": "appF-7"},
        "symptoms": {"backend": "claude-cli", "model": "m1", "prompt_variant": "symptoms-r1"},
        "speech_acts": {"backend": "openai-compatible", "model": "gpt-5", "prompt_variant": "speechacts-v1"}}
    return data


@unittest.skipUnless(NODE, "node is not on PATH")
class Normaliser(unittest.TestCase):
    """The page's own normaliser, run by node over one sample per shape."""

    @classmethod
    def setUpClass(cls):
        cases = [{"label": "coordbench", "data": export.build_trajectory(FIXTURE, NO_DATASET)},
                 {"label": "legacy", "data": legacy_sample(), "extras": {"index_result": {"passed": False, "hasConflict": False}}},
                 {"label": "sdk", "data": website_steps(True)}, {"label": "miniswe", "data": website_steps(False)},
                 {"label": "seven", "data": seven_key_sample()}, {"label": "stepless", "data": stepless_sample()},
                 {"label": "unknown", "data": {"hello": 1}},
                 {"label": "unpublished", "data": legacy_sample(), "extras": {"index_missing": True}},
                 {"label": "judged", "data": judged_sample()}, {"label": "silent", "data": silent_sample()},
                 {"label": "partial", "data": partial_sample()}, {"label": "unfinished", "data": unfinished_sample()},
                 {"label": "pilot", "data": pilot_sample()}, {"label": "short", "data": short_log_sample()},
                 {"label": "stepless-pilot", "data": stepless_pilot_sample()},
                 {"label": "apart", "data": apart_sample(False)}, {"label": "apart-short", "data": apart_sample(True)},
                 {"label": "finished-short", "data": finished_short_sample()}, {"label": "costed", "data": costed_sample()},
                 {"label": "unresolved", "data": unresolved_sample()},
                 {"label": "unresolved-files", "data": unresolved_files_sample()}]
        done = subprocess.run([NODE, "-e", HARNESS, str(PAGE)], input=json.dumps(cases), capture_output=True,
                              text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        cls.runs = {run["label"]: run for run in json.loads(done.stdout)}

    def test_each_shape_is_detected(self):
        shapes = {label: run.get("shape") for label, run in self.runs.items()}
        self.assertEqual(shapes, {"coordbench": "coordbench", "legacy": "legacy-openhands", "sdk": "openhands-sdk",
                                  "miniswe": "mini-swe", "seven": "seven-key", "stepless": "coordbench-stepless",
                                  "unknown": None, "unpublished": "legacy-openhands", "judged": "coordbench",
                                  "silent": "coordbench", "partial": "coordbench", "unfinished": "coordbench",
                                  "pilot": "coordbench", "short": "legacy-openhands", "apart": "legacy-openhands",
                                  "apart-short": "legacy-openhands", "stepless-pilot": "coordbench-stepless",
                                  "finished-short": "legacy-openhands", "costed": "coordbench", "unresolved": "coordbench",
                                  "unresolved-files": "coordbench-stepless"})
        self.assertIn("not a trajectory", self.runs["unknown"]["error"])

    def test_the_coordbench_export_reads_whole(self):
        run = self.runs["coordbench"]
        self.assertEqual(run["steps"], 23)
        self.assertEqual(run["lanes"], ["agent_1", "agent_2"])
        self.assertEqual(run["kind"], "real")
        self.assertEqual(len(run["messages"]), 4)
        self.assertEqual(run["cells"]["unresponsiveness"], ["present", "judge"])
        self.assertEqual(run["cells"]["cause"], ["unlabelled", None])
        self.assertEqual(run["cells"]["naive_conflict"], ["present", "data"])
        self.assertAlmostEqual(run["speed"]["blocked_ms"], 1000.0)
        self.assertEqual(run["outcome"]["route"], "union")

    def test_legacy_receipts_give_read_times_and_prompts_are_no_steps(self):
        run = self.runs["legacy"]
        self.assertEqual(run["steps"], 4)
        self.assertEqual(run["hidden"], 1)
        self.assertEqual(run["messages"], [["agent_1", "agent_2", True]])
        self.assertEqual(run["cells"]["unresponsiveness"], ["present", "heuristic"])
        self.assertEqual(run["cells"]["primary"], ["unpublished", None])
        self.assertEqual(run["cells"]["semantic_failure"], ["present", "data"])

    def test_website_shapes_are_marked_as_their_times_are(self):
        self.assertEqual(self.runs["sdk"]["kind"], "interpolated")
        self.assertEqual(self.runs["miniswe"]["kind"], "real")
        self.assertEqual(self.runs["miniswe"]["messages"], [["agent_1", "agent_2", True]])
        self.assertGreaterEqual(self.runs["sdk"]["warnings"], 2)

    def test_feature_numbered_ids_map_to_lanes_in_order(self):
        self.assertEqual(self.runs["seven"]["lanes"], ["agent_1", "agent_2"])

    def test_a_pair_directory_without_an_export_shows_the_hubs_calls(self):
        run = self.runs["stepless"]
        self.assertEqual(run["kind"], "hub-events")
        self.assertEqual(run["steps"], 10)
        self.assertEqual(run["cells"]["unresponsiveness"], ["present", "judge"])

    def test_the_judge_caption_names_every_section_s_judge(self):
        self.assertEqual(self.runs["judged"]["judge"],
                         "claude-cli, m1: appF-7, symptoms-r1; openai-compatible, gpt-5: speechacts-v1")
        self.assertNotIn(";", self.runs["coordbench"]["judge"] or "")

    def test_symptoms_app_g_s_rule_decided_are_marked_rule_and_name_no_judge(self):
        run = self.runs["silent"]
        self.assertEqual(run["cells"]["primary"], ["absent", "rule"])
        self.assertEqual(run["cells"]["secondary"], ["absent", "rule"])
        self.assertEqual(run["judge"], "")
        self.assertEqual(self.runs["judged"]["cells"]["primary"][1], "judge")

    def test_a_token_total_that_leaves_a_count_out_is_a_lower_bound(self):
        self.assertTrue(self.runs["partial"]["speed"]["tokens_partial"])
        self.assertEqual(self.runs["partial"]["agent_tokens"], [None, "a count was not measured"])
        self.assertFalse(self.runs["coordbench"]["speed"]["tokens_partial"])
        self.assertIsNone(self.runs["coordbench"]["speed"]["tokens_reason"])

    def test_a_token_total_from_output_counts_that_may_not_be_final_is_a_lower_bound(self):
        run = self.runs["unfinished"]
        self.assertTrue(run["speed"]["tokens_partial"])
        self.assertEqual(run["agent_tokens"], [None, "the transcript's output counts may not be final"])
        self.assertIn("may not be final", run["speed"]["tokens_reason"])

    def test_each_scoring_rule_shows_under_its_own_name(self):
        """The fixture's 63b9d44 verdict came from the lead's patch alone and
        its paper-era verdict from the union merge: each is named as what
        it is, and neither stands in the other's place."""
        badges = dict(self.runs["coordbench"]["badges"])
        self.assertEqual(list(badges), ["63b9d44 fail", "63b9d44 strategy: lead's patch alone", "naive conflict",
                                        "paper-era pass", "paper-era route: union"])
        self.assertIn("eval.json both_passed", badges["63b9d44 fail"])
        self.assertIn("merge.strategy", badges["63b9d44 strategy: lead's patch alone"])
        self.assertIn("cascade.route", badges["paper-era route: union"])
        self.assertEqual(self.runs["coordbench"]["outcome"]["route"], "union")
        self.assertEqual([text for text, _ in self.runs["legacy"]["badges"]], ["index fail"])
        self.assertIn("index.json", dict(self.runs["legacy"]["badges"])["index fail"])
        pilot = [text for text, _ in self.runs["pilot"]["badges"]]
        self.assertEqual(pilot[-1], "paper-era route: union")

    def test_behaviours_are_coloured_apart_from_failures(self):
        classes = self.runs["legacy"]["classes"]
        self.assertEqual(self.runs["legacy"]["cells"]["role_division"], ["present", "heuristic"])
        self.assertEqual(classes["role_division"], "cell state-present kind-behaviour")
        self.assertEqual(classes["semantic_failure"], "cell state-present kind-failure")
        self.assertEqual(classes["negotiation"].split()[-1], "kind-behaviour")
        self.assertEqual(classes["unresponsiveness"].split()[-1], "kind-failure")

    def test_steps_are_the_stock_viewers_count_and_the_other_count_says_what_it_leaves_out(self):
        """The legacy sample's eight events less the three prompts the
        stock viewer's filters drop are five steps; the page's own cards
        fold the observation into its action and leave the receipt out."""
        run = self.runs["legacy"]
        self.assertEqual((run["speed"]["steps"], run["speed"]["steps_less"]), (5, 3))
        self.assertEqual(run["agent_steps"], [[3, 2], [2, 1]])
        self.assertEqual(run["steps_text"], "5 steps, 3 without observations and receipts")
        self.assertIn("1 of 4 step cards shown (5 steps", run["list_text"])
        self.assertAlmostEqual(run["speed"]["time_per_step_ms"], run["speed"]["wall_ms"] / 5)
        coordbench = self.runs["coordbench"]
        self.assertEqual(coordbench["speed"]["steps"], 23)
        self.assertEqual(coordbench["list_text"], "1 of 23 steps shown")
        self.assertEqual(sum(steps for steps, _ in coordbench["agent_steps"]), 23)

    def test_a_run_without_its_own_start_counts_from_the_pairs_start(self):
        """A pilot's first step is its first hub call; its clock starts at
        result.json's started_at, so the wall time is the pair's and the
        first message comes 4 s after the start, as events.jsonl's t says
        it does."""
        pilot, fixture = self.runs["pilot"], self.runs["coordbench"]
        self.assertEqual(pilot["clock"], "pair start")
        self.assertEqual(pilot["end_ms"] - pilot["start_ms"], 42000)
        self.assertEqual(pilot["speed"]["wall_ms"], 42000)
        self.assertAlmostEqual(pilot["speed"]["first_message_ms"], 4000, delta=1)
        self.assertEqual(fixture["clock"], "agents' start")
        self.assertEqual(self.runs["stepless"]["clock"], "agents' start")
        files = self.runs["stepless-pilot"]
        self.assertEqual(files["clock"], "pair start")
        self.assertEqual(files["speed"]["wall_ms"], 42000)
        self.assertAlmostEqual(files["speed"]["first_message_ms"], 4000, delta=1)
        self.assertEqual(self.runs["legacy"]["clock"], "first event")

    def test_an_agent_log_covering_a_sliver_of_the_run_is_flagged(self):
        truncated = self.runs["short"]["flags"]["truncated"]
        self.assertEqual([(x["lane"], x["span_ms"], x["of"]) for x in truncated], [("agent_1", 7000, "the wall time")])
        self.assertEqual(truncated[0]["of_ms"], 1200000)
        self.assertEqual(self.runs["legacy"]["flags"]["truncated"], [])
        self.assertTrue(self.runs["apart"]["flags"]["non_overlapping"])
        self.assertEqual(self.runs["apart"]["flags"]["truncated"], [])
        apart = self.runs["apart-short"]["flags"]["truncated"]
        self.assertEqual([(x["lane"], x["of"]) for x in apart], [("agent_1", "the longest agent log")])
        self.assertEqual(self.runs["sdk"]["flags"]["truncated"], [])
        self.assertEqual(self.runs["pilot"]["flags"]["truncated"], [])

    def test_a_short_log_that_ends_with_its_finish_is_not_flagged(self):
        """An agent done early while its partner kept working has a whole
        log; only a short log with no finish at its end may have lost the
        rest."""
        self.assertEqual(self.runs["finished-short"]["flags"]["truncated"], [])
        self.assertEqual(len(self.runs["short"]["flags"]["truncated"]), 1)

    def test_a_run_with_only_the_hub_s_calls_marks_its_steps_and_measures_no_rate(self):
        """A run with no transcript has only its coordination calls for
        steps: the counts are floors, the rates and shares over them are
        not measured, and the badge says why."""
        for label in ("pilot", "stepless"):
            run = self.runs[label]
            speed = run["speed"]
            self.assertEqual(run["kind"], "hub-events")
            self.assertIn("hub's record", speed["bounds"]["steps"])
            for key in ("steps_per_min", "time_per_step_ms", "comm_share", "longest_idle_ms"):
                self.assertIsNone(speed[key], key)
                self.assertIn("no transcript", speed["unmeasured"][key])
            self.assertIsNotNone(speed["first_message_ms"])
            self.assertIn("&gt;= ", run["steps_cell"])
            self.assertIn("hub calls only", run["steps_cell"])
            self.assertIn('title="no transcript', run["kind_badge"])
            self.assertEqual(run["agent_shares"], [None, None])
        self.assertEqual(self.runs["pilot"]["steps_text"], str(self.runs["pilot"]["speed"]["steps"]) + " hub calls, no transcript")
        self.assertIn("hub calls shown", self.runs["pilot"]["list_text"])
        fixture = self.runs["coordbench"]
        self.assertEqual(fixture["speed"]["unmeasured"], {})
        self.assertNotIn("steps", fixture["speed"]["bounds"])
        self.assertNotIn("hub calls", fixture["steps_cell"])
        self.assertIn('title="step times are the log', fixture["kind_badge"])

    def test_a_pilot_s_agents_span_from_the_pair_s_start_as_its_wall_time_does(self):
        for label in ("pilot", "stepless-pilot"):
            speed = self.runs[label]["speed"]
            self.assertEqual(speed["active_ms"], speed["wall_ms"], label)
            self.assertEqual(max(self.runs[label]["agent_spans"]), speed["wall_ms"], label)

    def test_a_cost_says_where_it_came_from_and_a_floor_is_marked(self):
        run = self.runs["costed"]
        self.assertEqual(run["agent_costs"], [[0.1234, "claude code", None], [0.04, "estimated from tokens",
            "estimated from tokens whose output counts may not be final (result.json cost_lower_bound)"]])
        self.assertAlmostEqual(run["speed"]["cost"], 0.1634)
        self.assertIn("may not be final", run["speed"]["bounds"]["cost"])
        self.assertEqual(run["speed"]["cost_sources"], ["claude code", "estimated from tokens"])
        fixture = self.runs["coordbench"]
        self.assertIsNone(fixture["speed"]["bounds"]["cost"])
        self.assertEqual([source for _, source, _ in fixture["agent_costs"]], ["result.json", "result.json"])

    def test_a_paper_era_fail_that_needed_the_resolver_is_undecided(self):
        """Naive and union failed and the learned resolver was needed but
        not run: the paper-era verdict is undecided, not a fail, and the
        63b9d44 strategy says no merge passed rather than naming the naive
        merge, which was never tested."""
        strategy = "63b9d44 strategy: none (naive conflicted, lead's patch alone failed)"
        for label in ("unresolved", "unresolved-files"):
            badges = dict(self.runs[label]["badges"])
            self.assertEqual(list(badges), ["63b9d44 fail", strategy, "naive conflict", "paper-era undecided",
                                            "paper-era route: learned resolver not run"], label)
            self.assertIn("no resolver endpoint", badges["paper-era undecided"])
            self.assertIn("merge.status conflicts", badges[strategy])
            self.assertTrue(self.runs[label]["outcome"]["paper_undecided"])
        self.assertFalse(self.runs["coordbench"]["outcome"]["paper_undecided"])
        self.assertIn("decision 7", dict(self.runs["coordbench"]["badges"])["paper-era pass"])

    def test_a_missing_index_row_is_not_published_never_a_fail(self):
        run = self.runs["unpublished"]
        self.assertIsNone(run["outcome"]["passed"])
        self.assertEqual(run["cells"]["naive_conflict"], ["unpublished", None])


CHECKS_HARNESS = r"""
const fs = require("fs"); const vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const schema = html.match(/<script id="coordbench-schema" type="application\/json">([\s\S]*?)<\/script>/)[1];
const scripts = html.match(/<script>([\s\S]*?)<\/script>/g);
const main = scripts[scripts.length - 1].replace(/^<script>/, "").replace(/<\/script>$/, "");
const sandbox = { console, URLSearchParams, TextDecoder, Date, JSON, Math, Set, Map, Object, Array, String, Number, RegExp,
  window: { COORDBENCH_COMPARE_NO_START: true, location: { search: "" } },
  document: { getElementById: (id) => ({ textContent: id === "coordbench-schema" ? schema : "null" }) } };
vm.createContext(sandbox);
vm.runInContext(main + "\n;globalThis.__checks = " + fs.readFileSync(0, "utf8") + ";", sandbox);
process.stdout.write(JSON.stringify(sandbox.__checks()));
"""

# Each check runs inside the page's script, so it calls the page's own
# functions; the speed table's arithmetic, the baseline's place, the
# index lookup and the naive-time reading.
PAGE_CHECKS = r"""() => {
  const metric = (key) => SPEED_METRICS.find((m) => m.key === key);
  const run = (label, speed, kind) => ({ label, speed, timestamps_kind: kind || "real" });
  const a = run("a", { wall_ms: 1000, first_message_ms: 250, steps_per_min: 6, tokens: 100, cost: 2, steps: 40, bounds: {} });
  const b = run("b", { wall_ms: 4000, first_message_ms: 2000, steps_per_min: 3, tokens: 50, cost: 1, steps: 12,
    bounds: { tokens: "a count was not measured", cost: "an agent's cost was not measured", steps: "hub calls only" } });
  const c = run("c", { wall_ms: 500, first_message_ms: null, steps_per_min: 12, tokens: null, bounds: {} }, "interpolated");
  const plain = { relative: false, ownScale: false }, relative = { relative: true, ownScale: false };
  const own = { relative: false, ownScale: true }, both = { relative: true, ownScale: true };
  const values = (m, options, base) => [a, b, c].map((r) => metricValue(r, metric(m), options, base));
  const ranks = (m, options, base, include) =>
    rankedRuns(metric(m), [a, b, c], values(m, options, base), include).map((x) => x.r.label);
  const out = {
    plain: values("wall_ms", plain, a), relative_to_a: values("wall_ms", relative, a),
    relative_to_b: values("wall_ms", relative, b), own_scale: values("first_message_ms", own, a),
    own_scale_relative: values("first_message_ms", both, a),
    rank_time: ranks("wall_ms", plain, a, false), rank_time_all: ranks("wall_ms", plain, a, true),
    rank_rate: ranks("steps_per_min", plain, a, true), rank_relative: ranks("wall_ms", relative, b, true),
    lower_bound: [lowerBound(b, metric("tokens"), plain, a), lowerBound(b, metric("tokens"), relative, a),
                  lowerBound(b, metric("tokens"), relative, b), lowerBound(a, metric("tokens"), plain, a)],
    other_bounds: [boundReason(b, metric("cost"), plain, a), boundReason(b, metric("steps"), plain, a),
                   boundReason(b, metric("wall_ms"), plain, a), boundReason(c, metric("tokens"), plain, a)],
    bar_html: [barHtml(a, 0), barHtml(a, 25), barHtml(c, 25)],
    block_ends: [blockEnd({ timestamps_kind: "hub-events" }, [{ t_ms: 0, end_ms: 5 }, { t_ms: 100 }], 0),
                 blockEnd({ timestamps_kind: "real" }, [{ t_ms: 0, end_ms: 5 }, { t_ms: 100 }], 0),
                 blockEnd({ timestamps_kind: "hub-events" }, [{ t_ms: 0 }], 0)],
    stock_duration: [stockDurationText(492900), stockDurationText(603999)],
    csv: speedCsv([{ label: "pilot", shape: "coordbench", timestamps_kind: "hub-events", clock: { anchor: "pair start" },
      lanes: [], flags: { truncated: [] }, speed: { steps: 29, cost: 0.5, cost_sources: ["claude code", "estimated from tokens"],
        bounds: { steps: "hub", steps_less: "hub", cost: "floor", tokens: null }, unmeasured: { comm_share: "no transcript" } } }]),
  };
  page.runs = ["r0", "r1", "r2", "r3"].map((label, index) => ({ label, index }));
  page.baseline = 2;
  out.after_removing_before = [removeRun(0), page.baseline, page.runs[page.baseline].label];
  out.after_removing_after = [removeRun(2), page.baseline, page.runs[page.baseline].label];
  out.after_removing_baseline = [removeRun(1), page.baseline, page.runs[page.baseline].label];
  const rows = (repo, model) => ({ repo, taskId: "task6554", features: "feature1_feature2",
    results: { [model]: { passed: model === "gpt5" } } });
  const index = { tasks: [rows("typst", "gpt5"), rows("typst_task", "gemini_flash_sdk")] };
  const job = (repo, model) => ({ repo, task: "task6554", features: "feature1_feature2", model });
  out.index = [indexResult(index, job("typst_task", "gemini_flash_sdk")), indexResult(index, job("typst", "gemini_flash_sdk")),
               indexResult(index, job("typst_task", "gpt5")), indexResult(index, job("typst", "claude"))];
  const time = metric("first_message_ms"), count = metric("steps"), share = metric("comm_share");
  out.bars = [barWidths(time, [40], plain), barWidths(time, [40, 5000], plain), barWidths(time, [1000, 4000, null], plain),
              barWidths(time, [0.004], relative), barWidths(share, [0.0004, 0.5], plain), barWidths(count, [3], plain),
              barWidths(time, [60, 140], plain)];
  out.timeline_widths = [timelineWidth(343), timelineWidth(1248), timelineWidth(0)];
  const agent = (started_ms) => ({ started_ms });
  out.clocks = [coordbenchClock([agent(5), agent(3)], "run_started", 1), coordbenchClock([agent(null)], "pair start (approximate)", 1),
                coordbenchClock([agent(null)], "run_create phase (approximate)", 1), coordbenchClock([agent(null)], "run_started", 1),
                coordbenchClock([agent(null)], null, null)];
  out.naive_times = [resultTimeMs("2026-10-09T23:06:38", { utc_offset_seconds: 7200 }),
                     resultTimeMs("2026-10-09T23:06:38.500000", { utc_offset_seconds: 7200 }),
                     resultTimeMs("2026-10-09T21:06:38Z", { utc_offset_seconds: 7200 })];
  return out;
}"""


@unittest.skipUnless(NODE, "node is not on PATH")
class PageFunctions(unittest.TestCase):
    """The page's speed arithmetic and ranks, the baseline kept on the run
    the user chose when another is removed, the typst rows' lookup by
    model and the reading of result.json's naive times, run by node."""

    @classmethod
    def setUpClass(cls):
        done = subprocess.run([NODE, "-e", CHECKS_HARNESS, str(PAGE)], input=PAGE_CHECKS, capture_output=True,
                              text=True, timeout=60)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout)

    def test_relative_speed_is_each_run_over_the_baseline(self):
        self.assertEqual(self.out["plain"], [1000, 4000, 500])
        self.assertEqual(self.out["relative_to_a"], [1, 4, 0.5])
        self.assertEqual(self.out["relative_to_b"], [0.25, 1, 0.125])
        self.assertEqual(self.out["own_scale"], [0.25, 0.5, None])
        self.assertEqual(self.out["own_scale_relative"], [1, 2, None])

    def test_ranks_put_the_fastest_first_and_leave_unreliable_times_out(self):
        self.assertEqual(self.out["rank_time"], ["a", "b"])
        self.assertEqual(self.out["rank_time_all"], ["c", "a", "b"])
        self.assertEqual(self.out["rank_rate"], ["c", "a", "b"])
        self.assertEqual(self.out["rank_relative"], ["c", "a", "b"])

    def test_a_partial_token_total_is_marked_unless_its_baseline_is_partial_too(self):
        self.assertEqual(self.out["lower_bound"], [True, True, False, False])

    def test_a_cost_and_a_hub_only_step_count_are_lower_bounds_with_their_reasons(self):
        self.assertEqual(self.out["other_bounds"], ["an agent's cost was not measured", "hub calls only", None, None])

    def test_a_bar_of_zero_width_is_no_bar_at_all(self):
        """A "-" and a 0.0 s leave the track empty: a stub, or a hatched
        bar's border, would read as a small value."""
        none, plain, hatched = self.out["bar_html"]
        self.assertEqual(none, "")
        self.assertEqual(plain, '<div class="bar" style="width:25.00%"></div>')
        self.assertIn("unreliable", hatched)

    def test_a_hub_call_is_drawn_as_long_as_it_took_and_a_logged_step_until_the_next(self):
        self.assertEqual(self.out["block_ends"], [5, 100, 0])

    def test_the_speed_csv_keeps_the_lower_bounds_the_gaps_and_the_cost_sources(self):
        head, row = [line.split(",") for line in self.out["csv"].splitlines()]
        fields = dict(zip(head, row))
        self.assertEqual(fields["lower_bounds"], "steps steps_without_observations_and_receipts cost")
        self.assertEqual(fields["not_measured"], "comm_share")
        self.assertEqual(fields["cost_sources"], "claude code; estimated from tokens")
        self.assertEqual((fields["steps"], fields["comm_share"]), ("29", ""))

    def test_the_stats_bar_s_duration_is_printed_as_the_stock_viewer_prints_it(self):
        self.assertEqual(self.out["stock_duration"], ["8m 12s", "10m 3s"])

    def test_removing_a_run_keeps_the_chosen_baseline(self):
        self.assertEqual(self.out["after_removing_before"], [None, 1, "r2"])
        self.assertEqual(self.out["after_removing_after"], [None, 1, "r2"])
        note, baseline, label = self.out["after_removing_baseline"]
        self.assertEqual((baseline, label), (0, "r1"))
        self.assertIn("the baseline r2 was removed", note)

    def test_a_typst_pair_s_result_is_found_in_either_row(self):
        self.assertEqual(self.out["index"], [{"passed": False}, {"passed": False}, {"passed": True}, None])

    def test_a_bar_is_as_long_as_the_value_it_prints(self):
        """A lone 0.04 s prints as 0.0 s and draws no bar, where scaling by
        the largest raw value drew a full one; bars scale by what is
        shown, so two values that print alike (0.06 s and 0.14 s, both
        0.1 s) draw alike."""
        lone, beside, plain, ratio, share, steps, tenths = self.out["bars"]
        self.assertEqual(lone, [0])
        self.assertEqual(beside, [0, 100])
        self.assertEqual(plain, [25, 100, 0])
        self.assertEqual(ratio, [0])
        self.assertEqual(share, [0, 100])
        self.assertEqual(steps, [100])
        self.assertEqual(tenths, [100, 100])

    def test_a_timeline_keeps_its_text_size_and_scrolls_when_narrow(self):
        self.assertEqual(self.out["timeline_widths"], [560, 1234, 1000])

    def test_a_run_s_clock_starts_at_its_agents_its_pair_or_its_first_event(self):
        self.assertEqual(self.out["clocks"], [{"anchor": "agents' start", "ms": 3}, {"anchor": "pair start", "ms": 1},
                                              {"anchor": "pair start", "ms": 1}, {"anchor": "first event", "ms": None},
                                              {"anchor": "first event", "ms": None}])

    def test_result_json_s_naive_times_are_read_in_the_run_host_s_zone(self):
        expected = 1791580000000 - 2000
        self.assertEqual(self.out["naive_times"], [expected, expected + 500, expected])


def playwright_found():
    """Whether node can load Playwright, the one way here to lay the page
    out in a real browser; machines without it skip the layout check."""
    if not NODE:
        return False
    done = subprocess.run([NODE, "-e", "require('playwright')"], capture_output=True, timeout=60)
    return done.returncode == 0


LAYOUT = r"""
const { chromium } = require("playwright");
(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (error) { process.stdout.write(JSON.stringify({ skip: error.message })); return; }
  const page = await browser.newPage({ viewport: { width: 375, height: 800 } });
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.goto(process.argv[1]);
  await page.waitForSelector(".timeline svg");
  const facts = await page.evaluate(() => {
    const heights = (selector) => Array.from(document.querySelectorAll(selector)).map((e) => e.getBoundingClientRect().height);
    const track = document.querySelector(".timeline .track");
    const hint = document.querySelector(".timeline .scroll-hint");
    const before = { text: hint.textContent, shown: hint.getBoundingClientRect().height > 0, classes: track.className };
    track.scrollLeft = track.scrollWidth;
    track.dispatchEvent(new Event("scroll"));
    return { lane: Math.min(...heights(".timeline .lane-label")), axis: Math.min(...heights(".timeline .axis")),
      page_width: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth,
      svg: document.querySelector(".timeline svg").getBoundingClientRect().width, track: track.clientWidth,
      track_scrolls: track.scrollWidth > track.clientWidth, hint_before: before,
      hint_after: { text: hint.textContent, classes: track.className } };
  });
  await browser.close();
  process.stdout.write(JSON.stringify(Object.assign(facts, { problems })));
})();
"""


@unittest.skipUnless(playwright_found(), "node cannot load playwright")
class PhoneLayout(unittest.TestCase):
    """At a phone's width the timelines keep their lane letters and axis
    labels at their CSS sizes, scroll inside their own box, and the page
    itself does not scroll sideways."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp())
        report = cls.root / "report.html"
        report.write_text(compare.report([FIXTURE], cooperbench_dir=NO_DATASET), encoding="utf-8")
        done = subprocess.run([NODE, "-e", LAYOUT, report.as_uri()], capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise AssertionError(done.stderr)
        cls.facts = json.loads(done.stdout)
        if "skip" in cls.facts:
            raise unittest.SkipTest("no browser for playwright: " + cls.facts["skip"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def test_the_timeline_text_stays_readable(self):
        self.assertEqual(self.facts["problems"], [])
        self.assertGreaterEqual(self.facts["lane"], 12)
        self.assertGreaterEqual(self.facts["axis"], 11)

    def test_the_timeline_scrolls_in_its_box_and_the_page_does_not(self):
        self.assertTrue(self.facts["track_scrolls"])
        self.assertEqual(self.facts["svg"], 560)
        self.assertLessEqual(self.facts["page_width"], self.facts["viewport"])

    def test_a_timeline_wider_than_its_box_says_what_is_in_view(self):
        """A phone hides its scroll bars, so a timeline that scrolls fades
        at the edge it scrolls toward and its hint names the stretch of
        the axis in view, up to the axis's end once scrolled there."""
        before, after = self.facts["hint_before"], self.facts["hint_after"]
        self.assertTrue(before["shown"])
        self.assertRegex(before["text"], r"^In view: 0\.0 s to .+ of .+; scroll sideways for the rest\.$")
        self.assertIn("more-right", before["classes"])
        self.assertNotIn("more-left", before["classes"])
        self.assertIn("more-left", after["classes"])
        self.assertNotIn("more-right", after["classes"])
        end = before["text"].split(" of ")[1].split(";")[0]
        self.assertIn(" to " + end + " of " + end + ";", after["text"])


if __name__ == "__main__":
    unittest.main()
