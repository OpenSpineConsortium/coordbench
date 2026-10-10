"""The sdlc-watch arm is the sdlc arm with every agent watching its peers'
pull requests. These tests pin what it adds and that it adds nothing else:
it has the sdlc arm's tools plus peer_pull_requests (sdlc-peers), and its
prompt is the sdlc arm's with one section on watching put before the
submission block. The tool is checked against a real bare repository with
two working clones playing the two agents, as the sdlc arm's tests do:
two drafts that change the same lines are reported as conflicting before
either is marked ready, lines both added are counted, two drafts that
change one file in different places merge cleanly, an agent that took its
peer's work by merging the peer's branch is not told that what it took
overlaps, a merged peer is reported as merged, and each look says which
peers pushed since the last one. The metrics count the looks and say when
overlap and a conflict were first seen."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import test_coordbench_arm_sdlc as sdlc_tests
from test_coordbench_arm_sdlc import (BASE_TEXT, SPECS, SharedRemote, call_over_mcp, commit_and_push,
                                      frontmatter, git, start_branch)

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
ARM_DIR = COORDBENCH / "arms" / "sdlc-watch"
SDLC_DIR = COORDBENCH / "arms" / "sdlc"
sys.path.insert(0, str(COORDBENCH))

from arm import SUBMISSION_BLOCK, Call, Run, ToolError, find_arm  # noqa: E402

SDLC_TOOLS = ("board", "claim_issue", "open_pull_request", "mark_ready", "pull_request_status")
HELPER = "def shared_helper_for_names(value):\n    return value.strip()\n"
WATCH_HEADING = "## Watching your peers' pull requests"


def arm_module():
    """The arm's own module, for its parsing helpers; loaded from its file,
    as the hub loads it, since arms are not importable by name."""
    spec = importlib.util.spec_from_file_location("sdlc_watch_under_test", ARM_DIR / "arm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_run(remote=None, features=(1, 2)):
    arm = find_arm(str(ARM_DIR))
    options = {"git_remote_host": str(remote.bare), "base_ref": remote.base} if remote else {}
    run = Run("r1", arm, arm.layout(list(features)), options,
              specs={number: SPECS[number] for number in features})
    arm.setup(run)
    return arm, run


def call(run, agent, tool, **arguments):
    return run.arm.tools_for(run, agent)[tool].handler(Call(run, agent, arguments))


def peers(run, agent):
    return call(run, agent, "peer_pull_requests")


class Pair(unittest.TestCase):
    """Two agents, each holding its own issue on a shared remote."""

    def setUp(self):
        self.remote = SharedRemote()
        self.arm, self.run = new_run(self.remote)
        self.clones = {agent: self.remote.clone(agent) for agent in ("agent1", "agent2")}
        for number, agent in ((1, "agent1"), (2, "agent2")):
            call(self.run, agent, "claim_issue", issue=number)
            start_branch(self.clones[agent], number, self.remote.base)

    def tearDown(self):
        self.arm.teardown(self.run)
        self.remote.close()

    def push(self, agent, number, files, message="work"):
        commit_and_push(self.clones[agent], number, files, message)

    def open_draft(self, agent, number):
        call(self.run, agent, "open_pull_request", issue=number)


class ArmFilesTest(unittest.TestCase):
    """The directory is a Claude Code plugin and a coordbench arm, made the
    way the sdlc arm's is."""

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_arm_loads_by_directory_and_by_built_in_name(self):
        for arm in (find_arm(str(ARM_DIR)), find_arm("sdlc-watch")):
            self.assertEqual(type(arm).__name__, "SdlcWatchArm")
            self.assertEqual(arm.name, "sdlc-watch")

    def test_plugin_and_mcp_json_are_the_contracts(self):
        plugin = self.read_json(ARM_DIR / ".claude-plugin" / "plugin.json")
        self.assertEqual(set(plugin), {"name", "version", "description"})
        self.assertEqual(plugin["name"], "coordbench-sdlc-watch")
        self.assertEqual(self.read_json(ARM_DIR / ".mcp.json"), sdlc_tests.MCP_JSON)

    def test_arm_json_agrees_with_the_arm(self):
        config = self.read_json(ARM_DIR / "arm.json")
        arm = find_arm(str(ARM_DIR))
        self.assertEqual(set(config), sdlc_tests.ARM_JSON_KEYS)
        self.assertEqual(config["entry"], "arm.py:SdlcWatchArm")
        self.assertEqual(config["description"], arm.description)
        self.assertEqual(config["needs"], ["git_remote"])
        self.assertEqual(tuple(arm.needs), ("git_remote",))

    def test_the_skill_names_every_tool_and_its_shell_command(self):
        fields, body = frontmatter((ARM_DIR / "skills" / "coordbench-sdlc-watch" / "SKILL.md").read_text())
        self.assertEqual(fields["name"], "coordbench-sdlc-watch")
        self.assertNotIn(": ", fields["description"])
        arm, run = new_run()
        for tool in arm.tools_for(run, "agent1").values():
            self.assertIn(f"`{tool.name}`", body)
            self.assertIn(f"`{tool.alias or 'coord-' + tool.name}", body)
        self.assertIn(WATCH_HEADING, body)


class ToolsAndPromptTest(unittest.TestCase):
    def test_the_tools_are_the_sdlc_arms_and_one_more(self):
        arm, run = new_run()
        names = list(arm.tools_for(run, "agent1"))
        sdlc_arm = find_arm(str(SDLC_DIR))
        sdlc_run = Run("r1", sdlc_arm, sdlc_arm.layout([1, 2]), {}, specs={1: SPECS[1], 2: SPECS[2]})
        sdlc_arm.setup(sdlc_run)
        self.assertEqual([name for name in names if name != "peer_pull_requests"],
                         list(sdlc_arm.tools_for(sdlc_run, "agent1")))
        self.assertEqual(names[:6], [*SDLC_TOOLS, "peer_pull_requests"])
        self.assertEqual(arm.tools_for(run, "agent1")["peer_pull_requests"].alias, "sdlc-peers")

    def test_the_prompt_puts_the_watch_section_before_the_submission_block(self):
        arm, run = new_run()
        prompt = arm.prompt(run, "agent2", SPECS)
        self.assertTrue(prompt.startswith(SPECS[2]))
        self.assertTrue(prompt.endswith(SUBMISSION_BLOCK))
        self.assertLess(prompt.index("## The claim protocol"), prompt.index(WATCH_HEADING))
        self.assertLess(prompt.index(WATCH_HEADING), prompt.index(SUBMISSION_BLOCK))
        self.assertIn("`sdlc-pr 2`", prompt.split(WATCH_HEADING)[1])
        self.assertIn("| `peer_pull_requests` | `sdlc-peers` |", prompt)

    def test_without_a_remote_the_look_is_refused_as_the_gate_failing(self):
        arm, run = new_run()
        with self.assertRaises(ToolError) as refused:
            peers(run, "agent1")
        self.assertEqual(refused.exception.exit_code, 4)


class DiffParsingTest(unittest.TestCase):
    def test_ranges_are_the_old_sides_and_an_insertion_is_a_point(self):
        module = arm_module()
        diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -3,2 +3,3 @@ def f():\n"
                "-    old line one here\n-    old line two here\n+    a new line long enough\n"
                "+++ a line that looks like a header\n+    short\n@@ -10,0 +11 @@\n+    inserted line, long enough\n"
                "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n"
                "@@ -1 +0,0 @@\n-x = 1\n")
        ranges, added = module.parse_unified_diff(diff)
        self.assertEqual(ranges, {"x.py": [(3, 4), (10, 10)], "gone.py": [(1, 1)]})
        self.assertEqual(added, {"a new line long enough", "++ a line that looks like a header",
                                 "inserted line, long enough"})

    def test_ranges_are_merged_when_shown(self):
        module = arm_module()
        self.assertEqual(module.ranges_text([(10, 12), (13, 15), (2, 2)]), "2, 10-15")
        self.assertEqual(module.ranges_text(None), "none")


class PeerLookTest(Pair):
    def test_before_anything_is_pushed_nothing_is_compared(self):
        text = peers(self.run, "agent1")
        self.assertIn("You have not pushed claude/1 yet", text)
        self.assertIn("#2 (agent2, claimed, no pull request yet): claude/2 is not pushed yet.", text)

    def test_two_drafts_on_the_same_lines_are_a_conflict_seen_before_the_gate(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello there'") + "\n\n" + HELPER})
        self.open_draft("agent1", 1)
        self.push("agent2", 2, {"app.py": BASE_TEXT.replace("'hello'", "'hi'") + "\n\n" + HELPER})
        self.open_draft("agent2", 2)
        text = peers(self.run, "agent2")
        self.assertIn("#1 (agent1, draft): claude/1 at", text)
        self.assertIn("You both change app.py: their lines 2, 6, yours 2, 6.", text)
        self.assertIn("Your branch and theirs conflict, as they stand, in app.py.", text)
        self.assertIn('Lines you both added: 2, such as "def shared_helper_for_names(value):"', text)
        self.assertIn("git diff team/main...team/claude/1 -- app.py", text)
        self.assertEqual(self.remote.main(), self.remote.base, "a look never moves main")

    def test_one_file_changed_in_different_places_merges_cleanly(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello there'")})
        self.push("agent2", 2, {"app.py": BASE_TEXT.replace("'base'", "'part two'")})
        text = peers(self.run, "agent1")
        self.assertIn("You both change app.py: their lines 6, yours 2.", text)
        self.assertIn("Your branch and theirs merge cleanly as they stand.", text)
        self.assertNotIn("Lines you both added", text)

    def test_different_files_are_no_overlap(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT + "\nX = 'feature one value'\n"})
        self.push("agent2", 2, {"bye.py": "def bye():\n    return 'goodbye for now'\n"})
        text = peers(self.run, "agent1")
        self.assertIn("It changes 1 file: bye.py (+2 -0).", text)
        self.assertIn("You change none of the same files.", text)
        self.assertNotIn("Settle overlap", text)

    def test_work_taken_by_merging_the_peers_branch_is_not_overlap(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT + "\n\n" + HELPER})
        clone = self.clones["agent2"]
        git(clone, "fetch", "--quiet", "team")
        git(clone, "merge", "--quiet", "--no-edit", "team/claude/1")
        self.push("agent2", 2, {"bye.py": "from app import shared_helper_for_names\n"})
        for agent, line in (("agent1", "It changes 1 file: bye.py (+1 -0)."),
                            ("agent2", "Everything on it is already in your branch or in main.")):
            text = peers(self.run, agent)
            self.assertIn(line, text)
            self.assertNotIn("You both change", text)
            self.assertNotIn("Lines you both added", text)

    def test_a_merged_peer_is_reported_as_merged(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT + "\nX = 'feature one value'\n"})
        self.open_draft("agent1", 1)
        call(self.run, "agent1", "mark_ready", issue=1)
        self.push("agent2", 2, {"bye.py": "def bye():\n    return 'goodbye for now'\n"})
        text = peers(self.run, "agent2")
        self.assertIn("#1 (agent1, merged): claude/1 has merged into main", text)
        self.assertIn("Your branch claude/2 at", text)
        self.assertIn("changes 1 file since it left main: bye.py.", text)

    def test_each_look_says_who_pushed_since_the_last_one(self):
        self.push("agent2", 2, {"bye.py": "def bye():\n    return 'goodbye'\n"})
        self.assertIn("pushed since your last look", peers(self.run, "agent1"))
        self.assertIn("nothing new since your last look", peers(self.run, "agent1"))
        self.push("agent2", 2, {"bye.py": "def bye():\n    return 'goodbye for now'\n"})
        self.assertIn("pushed since your last look", peers(self.run, "agent1"))
        # Each agent's looks are its own: agent2 has not looked at claude/1 yet.
        self.push("agent1", 1, {"app.py": BASE_TEXT + "\nX = 'feature one value'\n"})
        self.assertIn("pushed since your last look", peers(self.run, "agent2"))
        self.assertIn("nothing new since your last look", peers(self.run, "agent1"))

    def test_the_look_is_a_tool_call_over_mcp_and_an_event(self):
        self.push("agent1", 1, {"app.py": BASE_TEXT.replace("'hello'", "'hello there'")})
        self.push("agent2", 2, {"app.py": BASE_TEXT.replace("'hello'", "'hi'")})
        text, is_error, code = call_over_mcp(self.run, "agent1", "peer_pull_requests")
        self.assertFalse(is_error, text)
        self.assertEqual(code, 0)
        checks = [event for event in self.run.events if event["kind"] == "peer_check"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["agent"], "agent1")
        self.assertEqual(checks[0]["issue"], 1)
        self.assertEqual(checks[0]["peers"][0]["shared"], ["app.py"])
        self.assertEqual(checks[0]["peers"][0]["conflicts"], ["app.py"])


class MetricsTest(Pair):
    def test_the_metrics_count_the_looks_and_when_overlap_was_first_seen(self):
        empty = self.arm.metrics(self.run)
        self.assertEqual((empty["peer_checks"], empty["overlap_first_seen_seconds"],
                          empty["conflict_first_seen_seconds"], empty["most_lines_both_added"]), (0, None, None, 0))
        self.push("agent1", 1, {"app.py": BASE_TEXT + "\n\n" + HELPER})
        self.open_draft("agent1", 1)
        peers(self.run, "agent2")
        self.push("agent2", 2, {"app.py": BASE_TEXT + "\n\n" + HELPER.replace("strip()", "strip().lower()")})
        self.open_draft("agent2", 2)
        peers(self.run, "agent2")
        metrics = self.arm.metrics(self.run)
        self.assertEqual(metrics["peer_checks"], 2)
        self.assertEqual(metrics["peer_checks_by_agent"], {"agent2": 2})
        checks = [event for event in self.run.events if event["kind"] == "peer_check"]
        self.assertEqual(metrics["overlap_first_seen_seconds"], checks[1]["t"])
        self.assertEqual(metrics["conflict_first_seen_seconds"], checks[1]["t"])
        self.assertEqual(metrics["most_lines_both_added"], 1)
        self.assertEqual(set(metrics["pr_opened_seconds"]), {"agent1", "agent2"})
        self.assertEqual(metrics["claims"], 2, "the sdlc arm's metrics are all still there")


if __name__ == "__main__":
    unittest.main()
