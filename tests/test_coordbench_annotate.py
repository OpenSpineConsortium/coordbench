"""annotate.py carries the human steps of the failure analysis: the cause
review of Sec 6.2 and the symptom judge's validation of App G. These pin
the seeded draw and its population (failed multi-agent pairs, harness
errors apart), the packets and what each may show (labels for the cause
reviewer, none for the blind validator), Table 2's definitions word for
word, the tally with Wilson intervals, judge drafts that are never
counted, and the agreement statistics (Wilson, Cohen's kappa) against
worked numbers, App G's own 48 of 50 among them."""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coordbench"))
sys.path.insert(0, str(ROOT / "tests"))

import annotate  # noqa: E402
import judge  # noqa: E402
from test_coordbench_judge import FakeBackend, evaluation, result_record, standard_log  # noqa: E402


class Runs:
    """A logs directory of pairs: failed, passed and one harness error."""

    def __init__(self, test, failed=4, passed=2):
        self.root = Path(tempfile.mkdtemp(prefix="annotate-test-"))
        test.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.failed = [self.pair(f"f{n}", evaluation()) for n in range(failed)]
        self.passed = [self.pair(f"p{n}", evaluation(both_passed=True)) for n in range(passed)]
        self.error = self.pair("err", evaluation(), result_record(error="hub crashed"))

    def pair(self, name, eval_record, result=None):
        directory = self.root / "coop" / "pallets_click_task" / "2068" / name
        directory.mkdir(parents=True)
        result = result or result_record()
        result["features"] = [1, 6]
        result["run_name"] = name
        (directory / "result.json").write_text(json.dumps(result))
        (directory / "eval.json").write_text(json.dumps(eval_record))
        (directory / "events.jsonl").write_text(standard_log()[0].lines())
        (directory / "agent1.patch").write_text("diff --git a/src/a.py b/src/a.py\n+x = 1\n")
        (directory / "agent6.patch").write_text("diff --git a/src/b.py b/src/b.py\n+y = 2\n")
        return directory

    def judge_symptoms(self, directory, level="coordination", primary="Work overlap"):
        """judge.json as judge.py symptoms leaves it, without a call."""
        pair = judge.load_pair(directory)
        data = judge.load_judge(pair)
        data["symptoms"] = {"prompt_variant": "symptoms-r1", "decided_by": "judge", "level": level,
                            "primary": primary, "secondary": [], "family": judge.FAMILIES.get(primary),
                            "evidence": [{"source": "conversation", "quote": "I will add", "verified": True}]}
        judge.save_judge(pair, data)


def run(argv, backend=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        args = annotate.parser_of().parse_args(argv)
        code = annotate.command_causes(args, backend) if backend is not None else annotate.main(argv)
    return code, out.getvalue()


class StatisticsTest(unittest.TestCase):
    def test_wilson_reproduces_app_gs_interval(self):
        low, high = annotate.wilson(48, 50)
        self.assertAlmostEqual(low, 0.865, places=3)
        self.assertAlmostEqual(high, 0.989, places=3)
        self.assertIsNone(annotate.wilson(0, 0))

    def test_cohens_kappa_on_a_worked_example(self):
        self.assertAlmostEqual(annotate.cohen_kappa(list("aabb"), list("abbb")), 0.5)
        self.assertEqual(annotate.cohen_kappa(list("aaaa"), list("aaaa")), 1.0)
        self.assertIsNone(annotate.cohen_kappa([], []))


class CausesTest(unittest.TestCase):
    def setUp(self):
        self.runs = Runs(self)
        self.out = self.runs.root / "review"

    def test_the_draw_is_seeded_over_failed_pairs_only(self):
        run(["causes", str(self.runs.root), "--out", str(self.out), "--sample", "3", "--seed", "7"])
        labels = json.loads((self.out / "labels.json").read_text())
        drawn = [Path(p["pair_dir"]) for p in labels["pairs"]]
        self.assertEqual(len(drawn), 3)
        self.assertTrue(set(drawn) <= {d.resolve() for d in self.runs.failed})
        self.assertEqual((labels["population"], labels["harness_errors_excluded"]), (4, 1))
        again = self.runs.root / "again"
        run(["causes", str(self.runs.root), "--out", str(again), "--sample", "3", "--seed", "7"])
        self.assertEqual([p["pair_dir"] for p in json.loads((again / "labels.json").read_text())["pairs"]],
                         [str(d) for d in drawn])

    def test_a_run_the_audit_found_a_harness_error_is_counted_apart(self):
        """The session route's audit found a failed pair met a full disk (as
        every arm's outlines 1655 f6_f7 did in curse-1, the tests never
        run): it failed for the route, so it is drawn for no cause label and
        judged for no symptom, and counted with the harness errors."""
        (self.runs.failed[0] / "audit.json").write_text(json.dumps({"harness_error": "the run met a full disk"}))
        pair = judge.load_pair(self.runs.failed[0])
        self.assertEqual(judge.symptom_scope(pair), ([], "harness error: the run met a full disk"))
        run(["causes", str(self.runs.root), "--out", str(self.out)])
        labels = json.loads((self.out / "labels.json").read_text())
        self.assertEqual((labels["population"], labels["harness_errors_excluded"]), (3, 2))
        self.assertNotIn(str(self.runs.failed[0].resolve()), [p["pair_dir"] for p in labels["pairs"]])

    def test_the_labels_file_carries_table_2_word_for_word(self):
        run(["causes", str(self.runs.root), "--out", str(self.out)])
        labels = json.loads((self.out / "labels.json").read_text())
        self.assertEqual(labels["definitions"], annotate.TABLE_2)
        prompt = judge.prompt_text("causes-v1.txt")
        for cause, definition in annotate.TABLE_2.items():
            self.assertIn(f"- {cause}: {definition}", prompt)
        self.assertTrue(all(p["cause"] is None for p in labels["pairs"]))

    def test_a_packet_holds_the_labels_conversation_patches_and_test_output(self):
        self.runs.judge_symptoms(self.runs.failed[0])
        run(["causes", str(self.runs.root), "--out", str(self.out), "--sample", "4"])
        labels = json.loads((self.out / "labels.json").read_text())
        entry = next(p for p in labels["pairs"] if Path(p["pair_dir"]) == self.runs.failed[0].resolve())
        packet = (self.out / entry["packet"]).read_text()
        for expected in ("## Labels from judge.json", "primary Work overlap", "## Conversation",
                         "Status update: done", "## Patches", "+x = 1", "FAILED tests/test_termui.py"):
            self.assertIn(expected, packet)

    def test_drafts_are_kept_apart_and_never_shown_to_the_reviewer(self):
        backend = FakeBackend(*[{"cause": "commitment", "rationale": "r"}] * 4)
        code, _ = run(["causes", str(self.runs.root), "--out", str(self.out), "--draft"], backend)
        self.assertEqual(code, 0)
        drafts = json.loads((self.out / "drafts.json").read_text())
        self.assertEqual({d["draft_cause"] for d in drafts["pairs"]}, {"Commitment"})
        self.assertEqual(backend.calls[0][0], judge.prompt_text("causes-v1.txt"))
        labels_text = (self.out / "labels.json").read_text()
        self.assertNotIn("Commitment\"", labels_text.split('"pairs"')[1])
        packets = [p.read_text() for p in (self.out / "packets").iterdir()]
        self.assertFalse(any("draft" in p for p in packets))


class TallyTest(unittest.TestCase):
    def labelled(self, causes, drafts=None):
        runs = Runs(self)
        out = runs.root / "review"
        run(["causes", str(runs.root), "--out", str(out)])
        path = out / "labels.json"
        labels = json.loads(path.read_text())
        for entry, cause in zip(labels["pairs"], causes):
            entry["cause"], entry["note"] = cause, "why"
        path.write_text(json.dumps(labels))
        if drafts:
            (out / "drafts.json").write_text(json.dumps({"pairs": [
                {"pair_dir": e["pair_dir"], "draft_cause": d} for e, d in zip(labels["pairs"], drafts)]}))
        return path, labels

    def test_the_counts_with_wilson_and_the_unlabelled_and_invalid_apart(self):
        path, _ = self.labelled(["Expectation", "expectation", "Hunch", None])
        code, out = run(["tally", str(path)])
        self.assertEqual(code, 0)
        tally = json.loads((path.parent / "tally.json").read_text())
        self.assertEqual(tally["counts"], {"Expectation": 2, "Commitment": 0, "Communication": 0})
        self.assertEqual(tally["labelled"], 2)
        self.assertEqual(len(tally["invalid"]), 1)
        self.assertEqual(tally["wilson_95"]["Expectation"], [round(x, 4) for x in annotate.wilson(2, 2)])
        self.assertIn("2 labelled of 4 drawn; 1 unlabelled; 1 with a label", out)

    def test_drafts_are_compared_never_counted(self):
        path, _ = self.labelled(["Expectation", "Commitment", None, None],
                                drafts=["Expectation", "Communication", "Commitment", "Commitment"])
        run(["tally", str(path)])
        tally = json.loads((path.parent / "tally.json").read_text())
        self.assertEqual(tally["counts"], {"Expectation": 1, "Commitment": 1, "Communication": 0})
        self.assertEqual((tally["draft_agreement"]["compared"], tally["draft_agreement"]["agreed"]), (2, 1))

    def test_apply_writes_the_human_cause_into_judge_json(self):
        path, labels = self.labelled(["Communication"])
        run(["tally", str(path), "--apply"])
        written = json.loads((Path(labels["pairs"][0]["pair_dir"]) / "judge.json").read_text())
        self.assertEqual((written["cause"]["label"], written["cause"]["source"]), ("Communication", "human"))


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.runs = Runs(self)
        for directory, primary in zip(self.runs.failed[:3], ("Work overlap", "Repetition", None)):
            self.runs.judge_symptoms(directory, "implementation" if primary is None else "coordination", primary)
        self.out = self.runs.root / "validation"
        run(["validate", str(self.runs.root), "--out", str(self.out), "--seed", "3"])
        self.sheet_path = self.out / "sheet.json"
        self.sheet = json.loads(self.sheet_path.read_text())

    def test_the_draw_is_over_judged_pairs_and_the_packets_are_blind(self):
        self.assertEqual(self.sheet["drawn"], 3)
        self.assertEqual({Path(p["pair_dir"]) for p in self.sheet["pairs"]},
                         {d.resolve() for d in self.runs.failed[:3]})
        for entry in self.sheet["pairs"]:
            packet = (self.out / entry["packet"]).read_text()
            self.assertNotIn("Labels from judge.json", packet)
            self.assertNotIn("Work overlap", packet)
        self.assertEqual((self.out / "rubric-symptoms-r1.txt").read_text(), judge.prompt_text("symptoms-r1.txt"))

    def fill(self, path, labels):
        sheet = json.loads(self.sheet_path.read_text())
        by_dir = {Path(e["pair_dir"]): e for e in sheet["pairs"]}
        for directory, (level, primary) in labels.items():
            by_dir[directory.resolve()].update(level=level, primary=primary)
        path.write_text(json.dumps(sheet))

    def test_agreement_on_level_and_primary_with_kappa_for_two_humans(self):
        failed = self.runs.failed
        self.fill(self.sheet_path, {failed[0]: ("coordination", "work overlap"),
                                    failed[1]: ("coordination", "Unresponsiveness"),
                                    failed[2]: ("implementation", "Repetition")})
        second = self.out / "second.json"
        self.fill(second, {failed[0]: ("coordination", "Work overlap"), failed[1]: ("implementation", None),
                           failed[2]: ("implementation", None)})
        code, out = run(["agreement", str(self.sheet_path), str(second)])
        self.assertEqual(code, 0)
        summary = json.loads((self.out / "agreement.json").read_text())
        first = summary["human_vs_judge"]
        self.assertEqual((first["labelled"], first["level"]["agreed"], first["primary"]["agreed"]), (3, 3, 2))
        self.assertEqual(summary["second_human"]["level"]["agreed"], 2)
        self.assertEqual(summary["kappa"]["labelled_by_both"], 3)
        self.assertAlmostEqual(summary["kappa"]["kappa_level"], annotate.cohen_kappa(
            ["coordination", "coordination", "implementation"], ["coordination", "implementation", "implementation"]))
        self.assertIn("48 of 50", out)


if __name__ == "__main__":
    unittest.main()
