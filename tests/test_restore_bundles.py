"""The restore of the curse-1 experiment's bundled runs into a logs
directory (the notes' run/restore-bundles.py): where two bundles hold a
run at one path the later start stands for it, a harness error is never
copied, and a run the logs already hold is left as it is."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bench/cooperbench/results/curse-of-coordination-notes/run/restore-bundles.py"
SPEC = importlib.util.spec_from_file_location("restore_bundles", SCRIPT)
restore_bundles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore_bundles)

PAIR = Path("curse-1-coop/coop/pallets_click_task/2068/f5_f11")
EARLY, LATE = "2026-10-15T06:03:42", "2026-10-15T10:38:13"


class RestoreBundlesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.results, self.logs = self.tmp / "results", self.tmp / "logs"

    def bundle_run(self, bundle, started, harness_error=None):
        run = self.results / bundle / "raw" / PAIR
        run.mkdir(parents=True)
        (run / "result.json").write_text(json.dumps({"started_at": started}))
        (run / "audit.json").write_text(json.dumps({"harness_error": harness_error}))

    def restore(self):
        return restore_bundles.restore(self.results, self.logs, ["pilot", "step"], dry_run=False)

    def started_in_logs(self):
        return json.loads((self.logs / PAIR / "result.json").read_text())["started_at"]

    def test_the_later_start_stands_for_the_path_whichever_bundle_comes_first(self):
        self.bundle_run("pilot", LATE)
        self.bundle_run("step", EARLY)
        self.restore()
        self.assertEqual(self.started_in_logs(), LATE)

    def test_a_later_harness_error_keeps_the_earlier_attempt_out_too(self):
        self.bundle_run("pilot", EARLY)
        self.bundle_run("step", LATE, harness_error="the run met a full disk")
        done = self.restore()
        self.assertFalse((self.logs / PAIR).exists())
        self.assertEqual(done["spoiled"], [f"step/{PAIR}"])

    def test_a_run_the_logs_hold_is_left_as_it_is(self):
        self.bundle_run("step", LATE)
        (self.logs / PAIR).mkdir(parents=True)
        (self.logs / PAIR / "result.json").write_text(json.dumps({"started_at": "the rerun"}))
        done = self.restore()
        self.assertEqual(self.started_in_logs(), "the rerun")
        self.assertEqual(done["kept"], [str(PAIR)])

    def test_an_arm_takes_the_bundle_config_and_never_its_summary(self):
        self.bundle_run("step", LATE)
        arm = self.results / "step" / "raw" / "curse-1-coop"
        (arm / "config.json").write_text("{}\n")
        (arm / "summary.json").write_text("{}\n")
        self.restore()
        self.assertTrue((self.logs / "curse-1-coop" / "config.json").exists())
        self.assertFalse((self.logs / "curse-1-coop" / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
