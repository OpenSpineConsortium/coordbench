"""compare.py and viewer/compare.html: the report command embeds the runs
into a copy of the page, and the page reads every shape the exporter and
CooperBench's website write. The page's schema block must be the
exporter's SCHEMA, so the two cannot drift; its palette must cover every
tool type in both themes; it must load nothing from outside. When node is
on PATH, the page's own normaliser runs over one sample of each shape
(as the page loads none of its DOM code until start(), the model can run
alone); elsewhere that part is skipped, as on a machine without node."""

import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
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
vm.runInContext(main + "\n;globalThis.__api = { detectShape, normalise, matrixCell, MATRIX_COLUMNS, judgeCaption };", sandbox);
const api = sandbox.__api;
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((c) => {
  try {
    const run = api.normalise(c.data, { label: c.label, extras: c.extras || {} });
    const cells = {};
    api.MATRIX_COLUMNS.forEach(([key]) => { const cell = api.matrixCell(run, key); cells[key] = [cell.state, cell.source]; });
    return { label: c.label, shape: run.shape, steps: run.steps.length, hidden: run.steps.filter((s) => s.hidden).length,
      lanes: run.lanes, messages: run.messages.map((m) => [m.from, m.to, m.read_ms != null]), kind: run.timestamps_kind,
      speed: run.speed, outcome: run.outcome, cells, flags: run.flags, warnings: run.warnings.length,
      judge: run.labels.judge_meta ? api.judgeCaption(run.labels.judge_meta) : null };
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
                 {"label": "partial", "data": partial_sample()}]
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
                                  "silent": "coordbench", "partial": "coordbench"})
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
        self.assertFalse(self.runs["coordbench"]["speed"]["tokens_partial"])

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
  const a = run("a", { wall_ms: 1000, first_message_ms: 250, steps_per_min: 6, tokens: 100, tokens_partial: false });
  const b = run("b", { wall_ms: 4000, first_message_ms: 2000, steps_per_min: 3, tokens: 50, tokens_partial: true });
  const c = run("c", { wall_ms: 500, first_message_ms: null, steps_per_min: 12, tokens: null }, "interpolated");
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

    def test_removing_a_run_keeps_the_chosen_baseline(self):
        self.assertEqual(self.out["after_removing_before"], [None, 1, "r2"])
        self.assertEqual(self.out["after_removing_after"], [None, 1, "r2"])
        note, baseline, label = self.out["after_removing_baseline"]
        self.assertEqual((baseline, label), (0, "r1"))
        self.assertIn("the baseline r2 was removed", note)

    def test_a_typst_pair_s_result_is_found_in_either_row(self):
        self.assertEqual(self.out["index"], [{"passed": False}, {"passed": False}, {"passed": True}, None])

    def test_result_json_s_naive_times_are_read_in_the_run_host_s_zone(self):
        expected = 1791580000000 - 2000
        self.assertEqual(self.out["naive_times"], [expected, expected + 500, expected])


if __name__ == "__main__":
    unittest.main()
