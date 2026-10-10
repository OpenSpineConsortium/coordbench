"""The subscription token can arrive as COORDBENCH_CLAUDE_TOKEN, since a
Claude Code cloud environment withholds CLAUDE_CODE_OAUTH_TOKEN from the
session; these pin how the runner and the kit adopt it."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coordbench"))

import drivers  # noqa: E402


class AdoptTokenAlias(unittest.TestCase):
    def test_the_alias_fills_the_name_claude_code_reads(self):
        environ = {"COORDBENCH_CLAUDE_TOKEN": "alias"}
        self.assertTrue(drivers.adopt_token_alias(environ))
        self.assertEqual(environ["CLAUDE_CODE_OAUTH_TOKEN"], "alias")

    def test_a_token_under_its_own_name_wins(self):
        environ = {"CLAUDE_CODE_OAUTH_TOKEN": "own", "COORDBENCH_CLAUDE_TOKEN": "alias"}
        self.assertTrue(drivers.adopt_token_alias(environ))
        self.assertEqual(environ["CLAUDE_CODE_OAUTH_TOKEN"], "own")

    def test_neither_name_leaves_it_unset(self):
        environ = {}
        self.assertFalse(drivers.adopt_token_alias(environ))
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", environ)

    def test_the_alias_never_reaches_the_docker_client(self):
        self.assertIn("COORDBENCH_CLAUDE_TOKEN", drivers.HELD_BACK)


class TheKit(unittest.TestCase):
    def kit(self, environ):
        """run_arms.sh up to its token check: given no arm it would start
        CooperBench, so this runs only the lines before run()."""
        script = (ROOT / "bench" / "cooperbench" / "run_arms.sh").read_text(encoding="utf-8")
        head = script.split("run() {", 1)[0].replace("set -euo pipefail", "set -eu")
        head += '\n[ "$CLAUDE_CODE_OAUTH_TOKEN" = alias ] && echo adopted\n'
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"), **environ}
        return subprocess.run(["bash", "-c", head, "run_arms.sh", "repo", "1"], env=env,
                              capture_output=True, text=True)

    def test_the_kit_adopts_the_alias(self):
        done = self.kit({"COORDBENCH_CLAUDE_TOKEN": "alias"})
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "adopted"), done.stderr)

    def test_the_kit_names_the_alias_when_neither_is_set(self):
        done = self.kit({})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("COORDBENCH_CLAUDE_TOKEN", done.stderr)


if __name__ == "__main__":
    unittest.main()
