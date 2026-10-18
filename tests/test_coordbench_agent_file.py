"""--agent-file: every agent of a run runs as a Symphony agent file."""

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "coordbench"))

import agent_file  # noqa: E402
import drivers  # noqa: E402
import run  # noqa: E402

AGENT = """---
name: reviewer
description: Reviews one change.
tools: Read, Grep
model: opus
---

Review the change.
"""


class AgentFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = self.dir / "reviewer.md"
        self.path.write_text(AGENT)

    def test_the_definition_leaves_the_model_to_the_run(self):
        found = agent_file.definition(self.path)
        self.assertEqual(found["name"], "reviewer")
        self.assertEqual(len(found["sha256"]), 64)
        self.assertEqual(found["agents"]["reviewer"]["tools"], ["Read", "Grep"])
        self.assertNotIn("model", found["agents"]["reviewer"])

    def test_the_flags_run_the_session_as_the_agent(self):
        job = drivers.AgentJob(run_id="r", agent="a1", role="", features=(1,), container="c", prompt="p", env={},
                               arm_dir="/x", arm_plugin=False, agent_definition=agent_file.definition(self.path))
        flags = drivers.claude_flags(job)
        self.assertEqual(flags[-4:], ["--agents", drivers.CONTAINER_AGENT_FILE, "--agent", "reviewer"])

    def test_another_driver_is_refused_not_ignored(self):
        args = types.SimpleNamespace(agent_file=str(self.path), driver="session")
        with self.assertRaisesRegex(SystemExit, "claude-cli"):
            run.agent_definition_of(args, "claude-cli")
        args.driver = None
        self.assertEqual(run.agent_definition_of(args, "claude-cli")["name"], "reviewer")

    def test_an_agent_that_drops_the_arms_coordination_tools_is_refused(self):
        definition = agent_file.definition(self.path)
        with self.assertRaisesRegex(SystemExit, "coordination"):
            run.refuse_agent_without_coordination(definition, {"mcp_servers": {"coord": {}}, "plugin": False})
        run.refuse_agent_without_coordination(definition, {"mcp_servers": {}, "plugin": False})
        self.path.write_text(AGENT.replace("tools: Read, Grep", "tools: Read, mcp__coord__post"))
        run.refuse_agent_without_coordination(agent_file.definition(self.path), {"mcp_servers": {"coord": {}}})

    def test_a_bad_file_is_refused(self):
        self.path.write_text("no front matter\n")
        args = types.SimpleNamespace(agent_file=str(self.path), driver="claude-cli")
        with self.assertRaisesRegex(SystemExit, "agent-file"):
            run.agent_definition_of(args, "claude-cli")


if __name__ == "__main__":
    unittest.main()
