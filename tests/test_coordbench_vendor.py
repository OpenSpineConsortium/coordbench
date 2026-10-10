"""coordbench/vendor holds a copy of sdlc-protocol, the library whose merge
gate the sdlc arm decides through, put there by the library's
scripts/vendor.sh. These tests hold the copy to the line VENDORED writes
for it, so a copy changed by hand or half updated is caught, and hold the
arm to deciding with that copy, under the policy it states, and in its own
words where the gate's leave the arm's commands unnamed. The library's own
tests cover the gate itself; tests/test_coordbench_arm_sdlc.py shows the
arm keeps every promise it made before the gate moved there."""

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
VENDOR = COORDBENCH / "vendor"
sys.path.insert(0, str(COORDBENCH))

from arm import Call, Run, ToolError, find_arm  # noqa: E402

VENDORED_LINE = re.compile(r"sdlc-protocol ([0-9]+\.[0-9]+\.[0-9]+) ([0-9a-f]{40})\n")


def arm_globals():
    """The sdlc arm module's names, as the hub loads it."""
    return type(find_arm("sdlc")).setup.__globals__


class VendoredCopyTest(unittest.TestCase):
    def test_vendored_names_a_release_and_a_commit_and_the_copy_is_that_release(self):
        line = (VENDOR / "VENDORED").read_text(encoding="ascii")
        match = VENDORED_LINE.fullmatch(line)
        self.assertIsNotNone(match, f"VENDORED is not one line naming a version and a clean commit: {line!r}")
        package = (VENDOR / "sdlc_protocol" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{match.group(1)}"\n', package)

    def test_every_vendored_file_is_ascii_with_lf_and_a_final_newline(self):
        files = [path for path in sorted(VENDOR.rglob("*"))
                 if path.is_file() and "__pycache__" not in path.parts]
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=str(path.relative_to(VENDOR))):
                data = path.read_bytes()
                self.assertNotIn(b"\r", data)
                self.assertTrue(all(byte < 128 for byte in data))
                self.assertTrue(data.endswith(b"\n"))


class TheSdlcArmDecidesWithTheCopyTest(unittest.TestCase):
    def test_the_gate_and_the_world_are_the_vendored_ones(self):
        names = arm_globals()
        package = VENDOR / "sdlc_protocol"
        self.assertEqual(Path(names["gate"].__file__).resolve(), package / "gate.py")
        self.assertEqual(Path(names["bare_git"].__file__).resolve(), package / "adapters" / "bare_git.py")

    def test_the_gate_merges_and_a_branch_brings_main_in_by_merging_it(self):
        names = arm_globals()
        gate, policy = names["gate"], names["POLICY"]
        self.assertEqual((policy.who_merges, policy.bring_main_in), (gate.GATE, gate.MERGE))
        self.assertEqual((policy.required_check, policy.remote, policy.default_branch), ("", "team", "main"))

    def test_two_refusals_name_the_arms_commands_as_they_did_before_the_move(self):
        """The gate's words for these two say what to do next without naming
        a tool; the arm's answers named its commands there before the gate
        moved to the library, and still do. Neither refusal touches git, so
        the shared remote named here need not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            arm = find_arm("sdlc")
            run = Run("r1", arm, arm.layout([1, 2]), {"git_remote_host": str(Path(tmp) / "none.git"),
                                                      "base_ref": "0a1b2c3"})
            arm.setup(run)
            self.addCleanup(arm.teardown, run)
            answers = {}
            for tool, issue in (("open_pull_request", 1), ("mark_ready", 2)):
                try:
                    arm.tools_for(run, "agent1")[tool].handler(Call(run, "agent1", {"issue": issue}))
                except ToolError as refusal:
                    answers[tool] = (str(refusal), refusal.exit_code)
        self.assertEqual(answers["open_pull_request"],
                         ("only the agent holding the claim on #1 opens its pull request, and nobody has "
                          "claimed it; claim it first with sdlc-claim 1", 1))
        self.assertEqual(answers["mark_ready"],
                         ("#2 has no pull request; open it with open_pull_request (sdlc-pr 2) once claude/2 "
                          "is pushed", 1))


if __name__ == "__main__":
    unittest.main()
