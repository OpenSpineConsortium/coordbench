"""The CooperBench kit's shell scripts, held to what a reader of
bench/cooperbench/README.md is told: each parses, refuses an unset
variable, and reads the settings the README names, so the workflow that
runs them on GitHub's runners and the cloud session that runs them by
hand set the same names."""

import subprocess
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1] / "bench" / "cooperbench"
SCRIPTS = ("setup.sh", "prepare_task.sh", "overlay_task_image.sh", "build_task_image.sh", "survey.sh",
           "run_arms.sh", "pilot.sh")


class KitScripts(unittest.TestCase):
    def test_every_script_parses_and_refuses_an_unset_variable(self):
        # survey.sh and pilot.sh carry on past a failed task on purpose, so
        # not every script sets -e; every one refuses an unset variable.
        for name in SCRIPTS:
            with self.subTest(script=name):
                text = (KIT / name).read_text(encoding="utf-8")
                self.assertTrue(text.startswith("#!/bin/bash\n"), name)
                self.assertRegex(text, r"\nset -[a-z]*u", name)
                parsed = subprocess.run(["bash", "-n", str(KIT / name)], capture_output=True, text=True)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_the_settings_the_readme_names_are_the_ones_the_scripts_read(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        scripts = {name: (KIT / name).read_text(encoding="utf-8") for name in SCRIPTS}
        wanted = {"COORDBENCH_CLAUDE_BIN": ("setup.sh", "prepare_task.sh", "overlay_task_image.sh"),
                  "CLAUDE_CODE_VERSION": ("setup.sh",),
                  "COORDBENCH_PROXY_CA": ("overlay_task_image.sh", "build_task_image.sh"),
                  "COORDBENCH_REGISTRY": ("setup.sh", "overlay_task_image.sh"),
                  "COORDBENCH_MIRROR": ("prepare_task.sh",),
                  "COOPERBENCH_DIR": ("setup.sh", "prepare_task.sh", "survey.sh", "run_arms.sh", "pilot.sh")}
        for variable, readers in wanted.items():
            with self.subTest(variable=variable):
                self.assertIn(variable, readme)
                for name in readers:
                    self.assertIn(variable, scripts[name], f"{name} does not read {variable}")

    def test_the_pinned_claude_code_version_is_one_version_number(self):
        text = (KIT / "setup.sh").read_text(encoding="utf-8")
        self.assertRegex(text, r'claude_version=\$\{CLAUDE_CODE_VERSION:-\d+\.\d+\.\d+\}')
        # The overlay copies whatever binary the host has, so the version it
        # labels the image with is read from that binary, never from here.
        overlay = (KIT / "overlay_task_image.sh").read_text(encoding="utf-8")
        self.assertIn('version=$("$claude_bin" --version', overlay)
        self.assertIn("cooperbench.claude=$version", overlay)


if __name__ == "__main__":
    unittest.main()
