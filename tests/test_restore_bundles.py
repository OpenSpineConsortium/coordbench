"""The restore of the curse-1 experiment's bundled runs into a logs
directory (the notes' run/restore-bundles.py): where two bundles hold a
run at one path the later start stands for it, a harness error or an
unaudited run is never copied, a run the logs already hold is left as it
is, a dry run writes nothing, and a copy cut short is made again."""

import contextlib
import importlib.util
import io
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

    def bundle_run(self, bundle, started, harness_error=None, audited=True):
        run = self.results / bundle / "raw" / PAIR
        run.mkdir(parents=True)
        (run / "result.json").write_text(json.dumps({"started_at": started}))
        if audited:
            (run / "audit.json").write_text(json.dumps({"harness_error": harness_error}))

    def bundle_config(self, bundle, text):
        (self.results / bundle / "raw" / PAIR.parts[0] / "config.json").write_text(text)

    def restore(self, bundles=("pilot", "step"), dry_run=False):
        return restore_bundles.restore(self.results, self.logs, list(bundles), dry_run=dry_run)

    def started_in_logs(self):
        return json.loads((self.logs / PAIR / "result.json").read_text())["started_at"]

    def test_the_later_start_stands_for_the_path_in_either_bundle_order(self):
        self.bundle_run("pilot", LATE)
        self.bundle_run("step", EARLY)
        for order in (("pilot", "step"), ("step", "pilot")):
            with self.subTest(order=order):
                shutil.rmtree(self.logs, ignore_errors=True)
                self.restore(order)
                self.assertEqual(self.started_in_logs(), LATE)

    def test_a_later_harness_error_keeps_the_earlier_attempt_out_too(self):
        self.bundle_run("pilot", EARLY)
        self.bundle_run("step", LATE, harness_error="the run met a full disk")
        done = self.restore()
        self.assertFalse((self.logs / PAIR).exists())
        self.assertEqual(done["spoiled"], [(f"step/{PAIR}", "the run met a full disk")])

    def test_a_run_without_an_audit_is_not_copied(self):
        self.bundle_run("step", LATE, audited=False)
        done = self.restore()
        self.assertFalse((self.logs / PAIR).exists())
        self.assertEqual([name for name, _ in done["spoiled"]], [f"step/{PAIR}"])

    def test_a_run_the_logs_hold_is_left_as_it_is(self):
        self.bundle_run("step", LATE)
        (self.logs / PAIR).mkdir(parents=True)
        (self.logs / PAIR / "result.json").write_text(json.dumps({"started_at": "the rerun"}))
        done = self.restore()
        self.assertEqual(self.started_in_logs(), "the rerun")
        self.assertEqual(done["kept"], [str(PAIR)])

    def test_a_dry_run_writes_nothing(self):
        self.bundle_run("step", LATE)
        self.bundle_config("step", "{}\n")
        done = self.restore(dry_run=True)
        self.assertEqual(done["copied"], [str(PAIR)])
        self.assertEqual(done["configs"], [PAIR.parts[0]])
        self.assertFalse(self.logs.exists())

    def test_a_copy_cut_short_is_made_again_and_cleared(self):
        self.bundle_run("step", LATE)
        partial = self.logs / restore_bundles.STAGING / "run"
        partial.mkdir(parents=True)
        (partial / "result.json").write_text(json.dumps({"started_at": "half written"}))
        self.restore()
        self.assertEqual(self.started_in_logs(), LATE)
        self.assertFalse((self.logs / restore_bundles.STAGING).exists())

    def test_an_arm_takes_the_first_bundle_config_and_never_its_summary(self):
        self.bundle_run("pilot", EARLY)
        self.bundle_run("step", LATE)
        self.bundle_config("pilot", '{"from": "pilot"}\n')
        self.bundle_config("step", '{"from": "step"}\n')
        (self.results / "step" / "raw" / PAIR.parts[0] / "summary.json").write_text("{}\n")
        self.restore()
        config = self.logs / PAIR.parts[0] / "config.json"
        self.assertEqual(json.loads(config.read_text()), {"from": "pilot"})
        self.assertFalse((self.logs / PAIR.parts[0] / "summary.json").exists())

    def test_a_config_the_logs_hold_is_left_as_it_is(self):
        self.bundle_run("step", LATE)
        self.bundle_config("step", '{"from": "step"}\n')
        (self.logs / PAIR.parts[0]).mkdir(parents=True)
        (self.logs / PAIR.parts[0] / "config.json").write_text('{"from": "logs"}\n')
        done = self.restore()
        self.assertEqual(json.loads((self.logs / PAIR.parts[0] / "config.json").read_text()), {"from": "logs"})
        self.assertEqual(done["configs"], [])

    def test_a_bundle_that_is_not_there_stops_the_restore(self):
        self.bundle_run("step", LATE)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            restore_bundles.main([str(self.results), str(self.logs), "--bundles", "step, pilot"])
        self.assertEqual(stop.exception.code, 2)
        self.assertFalse(self.logs.exists())

    def test_the_default_bundles_are_in_the_repository(self):
        for name in restore_bundles.BUNDLES.split(","):
            with self.subTest(bundle=name):
                self.assertTrue((ROOT / "bench/cooperbench/results" / name / "raw").is_dir())


if __name__ == "__main__":
    unittest.main()
