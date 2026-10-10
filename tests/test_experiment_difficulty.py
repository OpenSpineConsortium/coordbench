"""difficulty.py builds App C's relative difficulty, its ten buckets, the
trapezoidal areas and retention from Solo and multi-agent outcomes, and
states where the paper leaves a choice open. These pin the bucket edges,
d(t) with one and two models and with repeated runs, the paired curves,
the area's limits, the pooling by summed counts, the degeneracy notes,
and the reading of runs from result.json and eval.json alone."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "cooperbench"))

import difficulty  # noqa: E402


def write_pair(logs, run, key, model, passed=None, error=None):
    pair_dir = logs / run / "coop" / Path(key)
    pair_dir.mkdir(parents=True)
    (pair_dir / "result.json").write_text(json.dumps({"model": model}))
    if passed is not None or error:
        (pair_dir / "eval.json").write_text(json.dumps({"both_passed": bool(passed), "error": error}))


class BucketTest(unittest.TestCase):
    def test_bucket_edges(self):
        self.assertEqual(difficulty.bucket_of(0.0), 0)
        self.assertEqual(difficulty.bucket_of(0.3), 3)
        self.assertEqual(difficulty.bucket_of(0.7), 7)
        self.assertEqual(difficulty.bucket_of(0.999), 9)
        self.assertEqual(difficulty.bucket_of(1.0), 9)

    def test_a_value_inside_a_bucket_is_floored_never_rounded(self):
        # floor(10 d~): 0.35 and 0.39 are bucket 3, 0.95 bucket 9, and 0.05
        # bucket 0, where rounding would move each up a bucket.
        for d_tilde, bucket in ((0.05, 0), (0.15, 1), (0.35, 3), (0.39, 3), (0.45, 4), (0.65, 6), (0.95, 9)):
            self.assertEqual(difficulty.bucket_of(d_tilde), bucket, d_tilde)

    def test_midpoints(self):
        self.assertEqual(difficulty.midpoint(0), 0.05)
        self.assertEqual(difficulty.midpoint(9), 0.95)

    def test_wilson_matches_the_papers_validation_interval(self):
        low, high = difficulty.wilson(48, 50)
        self.assertEqual((round(low, 3), round(high, 3)), (0.865, 0.989))
        self.assertIsNone(difficulty.wilson(0, 0))


class DifficultyTest(unittest.TestCase):
    def test_one_model_gives_binary_difficulty(self):
        pairs, span = difficulty.difficulty({"m1": {"a": [True], "b": [False]}})
        self.assertEqual((pairs["a"]["d"], pairs["b"]["d"]), (0.0, 1.0))
        self.assertEqual((pairs["a"]["bucket"], pairs["b"]["bucket"]), (0, 9))
        self.assertEqual(span, {"min": 0.0, "max": 1.0})

    def test_two_models_average(self):
        pairs, _ = difficulty.difficulty({"m1": {"a": [True], "b": [True], "c": [False]},
                                          "m2": {"a": [True], "b": [False], "c": [False]}})
        self.assertEqual([pairs[k]["d"] for k in "abc"], [0.0, 0.5, 1.0])
        self.assertEqual(pairs["b"]["bucket"], 5)
        self.assertEqual(pairs["b"]["models"], {"m1": 1.0, "m2": 0.0})

    def test_repeated_runs_give_a_fraction(self):
        pairs, _ = difficulty.difficulty({"m1": {"a": [True, False, True, True], "b": [False]}})
        self.assertAlmostEqual(pairs["a"]["d"], 0.25)

    def test_the_rescale_runs_over_the_pairs_scored(self):
        pairs, _ = difficulty.difficulty({"m1": {"a": [True, True, False, False], "b": [True, False, False, False]}})
        self.assertEqual((pairs["a"]["d_tilde"], pairs["b"]["d_tilde"]), (0.0, 1.0))

    def test_one_value_of_d_leaves_the_rescale_undefined(self):
        pairs, span = difficulty.difficulty({"m1": {"a": [True], "b": [True]}})
        self.assertIsNone(pairs["a"]["d_tilde"])
        self.assertEqual(span, {"min": 0.0, "max": 0.0})
        found = difficulty.compute({"m1": {"a": [True], "b": [True]}}, {("coop", "m1"): {"a": [False]}})
        self.assertTrue(any("undefined" in note for note in found["notes"]))


class CurveTest(unittest.TestCase):
    SOLO = {"m1": {"a": [True], "b": [True], "c": [False], "d": [False]}}
    COOP = {("coop", "m1"): {"a": [True], "b": [False], "c": [False], "e": [True]}}

    def test_the_curves_are_paired_and_summarised(self):
        found = difficulty.compute(self.SOLO, self.COOP)
        entry = found["curves"]["coop|m1"]
        first, last = entry["buckets"][0], entry["buckets"][9]
        self.assertEqual((first["n"], first["k_solo"], first["k_arm"]), (2, 2, 1))
        self.assertEqual((last["n"], last["k_solo"], last["k_arm"]), (1, 0, 0))
        self.assertEqual(entry["buckets"][4]["n"], 0)
        self.assertIsNone(entry["buckets"][4]["rate_solo"])
        summary = entry["summary"]
        self.assertAlmostEqual(summary["auc_solo"], 0.9 * (1.0 + 0.0) / 2)
        self.assertAlmostEqual(summary["auc_arm"], 0.9 * (0.5 + 0.0) / 2)
        self.assertAlmostEqual(summary["delta_auc"], 0.225)
        self.assertAlmostEqual(summary["retention"], 0.5)
        self.assertEqual(summary["limits"], [0.05, 0.95])
        self.assertEqual(summary["filled_buckets"], 2)
        self.assertEqual(summary["pairs"], 3)

    def test_one_filled_bucket_has_no_area(self):
        found = difficulty.compute({"m1": {"a": [True], "b": [False]}}, {("coop", "m1"): {"a": [True]}})
        summary = found["curves"]["coop|m1"]["summary"]
        self.assertIsNone(summary["auc_solo"])
        self.assertIsNone(summary["retention"])
        self.assertEqual(summary["filled_buckets"], 1)

    def test_pooling_sums_counts_across_models(self):
        solo = {"m1": {"a": [True], "b": [False]}, "m2": {"a": [True], "b": [False]}}
        arms = {("coop", "m1"): {"a": [True], "b": [False]}, ("coop", "m2"): {"a": [False], "b": [False]}}
        found = difficulty.compute(solo, arms)
        pooled = found["pooled"]["coop"]["buckets"][0]
        self.assertEqual((pooled["n"], pooled["n_arm"], pooled["k_arm"]), (2, 2, 1))
        self.assertAlmostEqual(pooled["rate_arm"], 0.5)

    def test_an_arm_without_solo_of_its_model_is_left_out(self):
        found = difficulty.compute(self.SOLO, {("coop", "other"): {"a": [True]}})
        self.assertEqual(found["curves"], {})

    def test_the_notes_say_the_curves_are_degenerate_with_few_models(self):
        notes = difficulty.compute(self.SOLO, self.COOP)["notes"]
        self.assertIn("1 Solo model(s): m1", notes[0])
        self.assertTrue(any("not comparable with Table 5" in note for note in notes))


class ReadRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_scored_pairs_are_read(self):
        write_pair(self.logs, "solo", "r/1/f1_f2", "m1", passed=True)
        write_pair(self.logs, "solo", "r/1/f1_f3", "m1", passed=False)
        write_pair(self.logs, "solo", "r/1/f2_f3", "m1", error="boom")
        write_pair(self.logs, "solo", "r/2/f1_f2", "m1")
        found = difficulty.scored_runs(self.logs, "solo")
        self.assertEqual(found, {"m1": {"r/1/f1_f2": [True], "r/1/f1_f3": [False]}})

    def test_the_command_writes_difficulty_json(self):
        write_pair(self.logs, "solo", "r/1/f1_f2", "m1", passed=True)
        write_pair(self.logs, "solo", "r/1/f1_f3", "m1", passed=False)
        write_pair(self.logs, "coop", "r/1/f1_f2", "m1", passed=False)
        write_pair(self.logs, "coop", "r/1/f1_f3", "m1", passed=False)
        out = self.logs / "difficulty.json"
        with open(self.logs / "stdout.txt", "w") as sink:
            stdout, sys.stdout = sys.stdout, sink
            try:
                difficulty.main(["--logs", str(self.logs), "--solo", "solo", "--arm", "coop=coop",
                                 "--out", str(out)])
            finally:
                sys.stdout = stdout
        found = json.loads(out.read_text())
        self.assertEqual(found["format"], "coordbench-difficulty/1")
        self.assertEqual(found["pairs"]["r/1/f1_f3"]["d"], 1.0)
        self.assertAlmostEqual(found["curves"]["coop|m1"]["summary"]["auc_solo"], 0.45)


if __name__ == "__main__":
    unittest.main()
