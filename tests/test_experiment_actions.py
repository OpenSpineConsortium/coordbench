"""The benchmark in GitHub Actions (.github/workflows/bench.yml): the
matrix planner, the artifact reader that lets a dispatch resume, the
survey's table, the fake agent, and the workflow's own text, held to what
bench/cooperbench/README.md and PLAN.md say of them."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "bench" / "cooperbench"
ACTIONS = KIT / "actions"
sys.path.insert(0, str(ACTIONS))
import artifacts  # noqa: E402
import environment  # noqa: E402
import plan  # noqa: E402
import survey_report  # noqa: E402

FLASH = {"name": "flash", "tasks": [
    {"repo": "pallets_click_task", "task_id": 2068, "pairs": [[1, 6], [2, 10], [5, 7]]},
    {"repo": "go_chi_task", "task_id": 26, "pairs": [[1, 2]]},
    {"repo": "pallets_jinja_task", "task_id": 1559, "pairs": [[1, 8], [4, 8]]}]}


def dataset_dir(tmp):
    subsets = Path(tmp) / "dataset" / "subsets"
    subsets.mkdir(parents=True)
    (subsets / "flash.json").write_text(json.dumps(FLASH), encoding="utf-8")
    return subsets.parent


class Planner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="coordbench-plan-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dataset = dataset_dir(self.tmp)

    def test_flash_is_every_pair_in_the_subset_s_order_and_an_entry_narrows_it(self):
        everything = plan.chosen_pairs(self.dataset, "flash")
        self.assertEqual([(r, t, f) for r, t, f in everything][:2],
                         [("pallets_click_task", 2068, (1, 6)), ("pallets_click_task", 2068, (2, 10))])
        self.assertEqual(len(everything), 6)
        self.assertEqual(plan.chosen_pairs(self.dataset, "go_chi_task/26, pallets_jinja_task/1559/f4_f8"),
                         [("go_chi_task", 26, (1, 2)), ("pallets_jinja_task", 1559, (4, 8))])
        self.assertEqual(plan.chosen_pairs(self.dataset, "pallets_jinja_task/1559/8_4"),
                         [("pallets_jinja_task", 1559, (4, 8))])
        listed = Path(self.tmp) / "pairs.txt"
        listed.write_text("# the pilot\npallets_click_task/2068/f1_f6\n\ngo_chi_task/26  # alpine\n")
        self.assertEqual(plan.chosen_pairs(self.dataset, f"@{listed}"),
                         [("pallets_click_task", 2068, (1, 6)), ("go_chi_task", 26, (1, 2))])
        for bad in ("pallets_click_task", "pallets_click_task/2068/f1_f9", "nothing/1", "../x/1"):
            with self.subTest(entry=bad), self.assertRaises(ValueError):
                plan.chosen_pairs(self.dataset, bad)

    def arms_dir(self, *names):
        arms = Path(self.tmp) / "arms"
        for name in names:
            (arms / name).mkdir(parents=True, exist_ok=True)
            (arms / name / "arm.json").write_text("{}")
        return arms

    def test_a_pair_s_arms_share_one_account_and_the_arm_order_rotates(self):
        result = plan.plan(self.dataset, "real", "flash", "solo,coop,sdlc", "1 2", "t1")
        items = result["items"]
        self.assertEqual(len(items), 18)
        by_pair = {}
        for item in items:
            by_pair.setdefault((item["repo"], item["task"], item["features"]), []).append(item)
        for arms in by_pair.values():
            self.assertEqual(len({a["account"] for a in arms}), 1)
        first = [a["arm"] for a in by_pair[("pallets_click_task", 2068, "f1_f6")]]
        second = [a["arm"] for a in by_pair[("pallets_click_task", 2068, "f2_f10")]]
        self.assertEqual(first, ["solo", "coop", "sdlc"])
        self.assertEqual(second, ["coop", "sdlc", "solo"])
        self.assertEqual(by_pair[("pallets_click_task", 2068, "f1_f6")][0]["account"], "1")
        self.assertEqual(by_pair[("pallets_click_task", 2068, "f2_f10")][0]["account"], "2")
        self.assertEqual(items[0]["name"], "t1-solo-pallets_click_task-2068-f1_f6")
        self.assertEqual(items[0]["artifact"], "t1__solo__pallets_click_task__2068__f1_f6")
        self.assertEqual(artifacts.run_name_of(items[0]["artifact"]), items[0]["name"])
        self.assertEqual(result["survey"], [{"repo": "pallets_click_task", "task": 2068}, {"repo": "go_chi_task", "task": 26},
                                            {"repo": "pallets_jinja_task", "task": 1559}])

    def test_an_account_gets_at_most_the_queue_s_hundred_and_the_plan_says_what_it_left_out(self):
        pairs = [("r", n, (1, 2)) for n in range(60)]
        items, done, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one"], "t")
        self.assertEqual(len(items), plan.QUEUE_LIMIT)
        self.assertEqual((done, dropped), (0, 20))
        items, done, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one", "two"], "t")
        self.assertEqual((len(items), dropped), (120, 0))

    def test_finished_items_are_left_out_before_the_caps_and_a_pair_keeps_its_account(self):
        pairs = [("r", n, (1, 2)) for n in range(60)]
        first, _, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one"], "t")
        self.assertEqual(dropped, 20)
        finished = {item["artifact"] for item in first}
        second, done, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one"], "t", finished=finished)
        self.assertEqual((len(second), done, dropped), (20, 100, 0))
        self.assertEqual({item["artifact"] for item in first} & {item["artifact"] for item in second}, set())
        # With two accounts, a pair's account is its place in the list, whatever was finished before.
        first, _, _ = plan.plan_items(pairs, ["solo"], ["one", "two"], "t")
        finished = {item["artifact"] for item in first[:30]}
        second, _, _ = plan.plan_items(pairs, ["solo"], ["one", "two"], "t", finished=finished)
        by_task = {item["task"]: item["account"] for item in first}
        self.assertTrue(all(by_task[item["task"]] == item["account"] for item in second))

    def test_a_matrix_holds_at_most_256_jobs_in_any_mode_and_a_fake_run_has_no_queue_cap(self):
        pairs = [("r", n, (1, 2)) for n in range(43)]
        items, _, dropped = plan.plan_items(pairs, list(plan.ARMS) + ["coop-nocomm"], ["one", "two", "three"], "t")
        self.assertEqual((len(items), dropped), (plan.MATRIX_LIMIT, 2))
        items, _, dropped = plan.plan_items(pairs, list(plan.ARMS), ["fake"], "t", queue_limit=0)
        self.assertEqual((len(items), dropped), (215, 0))
        many = {"name": "flash", "tasks": [{"repo": "r", "task_id": n, "pairs": [[1, 2]]} for n in range(120)]}
        (Path(self.dataset) / "subsets" / "flash.json").write_text(json.dumps(many), encoding="utf-8")
        fake = plan.plan(self.dataset, "fake", "flash", "solo,coop", "", "t")
        self.assertEqual((len(fake["items"]), fake["dropped"]), (240, 0))
        real = plan.plan(self.dataset, "real", "flash", "solo,coop,sdlc", "1", "t")
        self.assertEqual((len(real["items"]), real["dropped"]), (100, 260))
        self.assertTrue(any("256 jobs from one matrix and queues at most 100 per account" in n for n in real["notes"]))
        with self.assertRaises(ValueError):
            plan.plan(self.dataset, "real", "flash", "solo", "1, nine", "t")

    def test_a_real_run_needs_accounts_and_the_other_modes_do_not(self):
        with self.assertRaises(ValueError):
            plan.plan(self.dataset, "real", "flash", "solo", "", "t")
        fake = plan.plan(self.dataset, "fake", "go_chi_task/26", "solo", "", "t")
        self.assertEqual([i["account"] for i in fake["items"]], ["fake"])
        survey = plan.plan(self.dataset, "survey", "flash", "", "", "t")
        self.assertEqual(len(survey["survey"]), 3)
        self.assertEqual((survey["items"], survey["count"], survey["notes"]), ([], 3, []))

    def test_a_fake_tag_says_so_and_a_real_run_refuses_one(self):
        self.assertEqual(plan.plan(self.dataset, "fake", "flash", "solo", "", "bench-1")["tag"], "fake-bench-1")
        self.assertEqual(plan.plan(self.dataset, "fake", "flash", "solo", "", "smoke-pr7-1")["tag"], "smoke-pr7-1")
        self.assertEqual(plan.plan(self.dataset, "survey", "flash", "", "", "survey-1")["tag"], "survey-1")
        self.assertEqual(plan.plan(self.dataset, "real", "flash", "solo", "1", "bench-1")["tag"], "bench-1")
        for tag in ("fake-bench-1", "smoke-pr7-1"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                plan.plan(self.dataset, "real", "flash", "solo", "1", tag)

    def test_a_real_run_names_only_slots_whose_secret_is_set(self):
        self.assertEqual(plan.plan(self.dataset, "real", "flash", "solo", "1,3", "t", present={"1", "3"})["accounts"],
                         ["1", "3"])
        with self.assertRaises(ValueError) as refused:
            plan.plan(self.dataset, "real", "flash", "solo", "1,3", "t", present={"1"})
        self.assertIn("SYMPHONY_CLAUDE_3", str(refused.exception))
        self.assertIsNone(plan.secrets_present({}))
        self.assertEqual(plan.secrets_present({"HAS_SECRET_1": "true", "HAS_SECRET_2": "false"}), {"1"})

    def test_a_listing_that_fails_or_is_cut_plans_every_item_and_says_so(self):
        environ = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": "t", "GITHUB_API_URL": "https://api.example"}

        class Refusing:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
        with mock.patch.dict(os.environ, environ, clear=False), \
                mock.patch.object(artifacts, "OPENER", Refusing()):
            finished, note = plan.listed_finished("fake", "t")
        self.assertEqual(finished, set())
        self.assertIn("could not list the finished items", note)
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "t"}, clear=True):
            finished, note = plan.listed_finished("fake", "t")
        self.assertEqual(finished, set())
        self.assertIn("could not list", note)
        many = [{"id": n, "name": f"fake-t__solo__r__{n}__f1_f2__done", "created_at": "2026-10-02T00:00:00Z",
                 "expired": False} for n in range(artifacts.PAGE * artifacts.MOST_PAGES + 1)]
        with mock.patch.dict(os.environ, environ, clear=False), \
                mock.patch.object(artifacts, "OPENER", FakeGitHub(many, {})):
            finished, note = plan.listed_finished("fake", "t")
        self.assertEqual(len(finished), artifacts.PAGE * artifacts.MOST_PAGES)
        self.assertIn("listing was cut", note)
        self.assertEqual(plan.listed_finished("survey", "t"), (set(), None))

    def test_a_real_dispatch_with_a_fake_tag_is_refused_before_the_listing(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err, self.assertRaises(SystemExit) as stop:
            plan.main(["--dataset-dir", str(self.dataset), "--mode", "real", "--pairs", "flash", "--accounts", "1",
                       "--tag", "fake-1", "--list-finished"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("fake-", err.getvalue())

    def test_a_real_plan_notes_a_pair_list_or_arms_that_are_not_the_registered_ones(self):
        arms = list(plan.ARMS)
        self.assertEqual(plan.registration_notes("real", plan.REGISTERED_PAIRS, arms), [])
        self.assertEqual(plan.registration_notes("fake", "flash", ["solo"]), [])
        notes = plan.registration_notes("real", "flash", ["solo", "coop"])
        self.assertEqual(len(notes), 2)
        self.assertIn("not the registered list @bench/cooperbench/pairs.txt", notes[0])
        self.assertIn("not PLAN.md's solo,coop,coop-git,team,sdlc: solo,coop", notes[1])
        real = plan.plan(self.dataset, "real", "flash", "solo", "1", "t")
        self.assertIn("not the registered list", real["notes"][0])

    def test_the_timeout_has_a_default_and_a_bound_per_mode(self):
        self.assertEqual(plan.chosen_timeout("real", ""), 120)
        self.assertEqual(plan.chosen_timeout("fake", " "), 20)
        self.assertEqual(plan.chosen_timeout("real", "90"), 90)
        for mode, text in (("real", "121"), ("fake", "60"), ("real", "0"), ("real", "ten"), ("real", "1.5")):
            with self.subTest(mode=mode, text=text), self.assertRaises(ValueError):
                plan.chosen_timeout(mode, text)
        self.assertEqual(plan.plan(self.dataset, "fake", "flash", "solo", "", "t", timeout_text="25")["timeout_min"],
                         25)

    def test_an_arm_the_runner_lacks_fails_the_plan(self):
        arms = self.arms_dir("solo", "sdlc")
        self.assertEqual(plan.chosen_arms("sdlc, solo", arms), ["sdlc", "solo"])
        with self.assertRaises(ValueError) as refused:
            plan.chosen_arms("solo,sldc", arms)
        self.assertIn("sldc", str(refused.exception))
        self.assertIn("sdlc, solo", str(refused.exception))
        for arm in plan.ARMS + ("coop-nocomm",):
            self.assertTrue((plan.ARMS_DIR / arm / "arm.json").is_file(), arm)

    def test_the_command_writes_the_workflow_s_outputs(self):
        out = Path(self.tmp) / "outputs"
        finished = Path(self.tmp) / "finished.txt"
        finished.write_text("smoke-pr7__solo__go_chi_task__26__f1_f2\n")
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code = plan.main(["--dataset-dir", str(self.dataset), "--mode", "fake", "--pairs", "go_chi_task/26",
                              "--arms", "solo,sdlc", "--tag", "smoke-pr7", "--finished", str(finished)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed.getvalue())["pairs"], 1)
        lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
        self.assertEqual(lines["count"], "1")
        self.assertEqual(lines["tag"], "smoke-pr7")
        self.assertEqual(lines["timeout_min"], "20")
        self.assertEqual([i["arm"] for i in json.loads(lines["matrix"])["include"]], ["sdlc"])
        self.assertEqual(json.loads(lines["survey"]), {"include": [{"repo": "go_chi_task", "task": 26}]})
        self.assertIn("1 items finished by an earlier dispatch", lines["notes"])
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            plan.main(["--dataset-dir", str(self.dataset), "--mode", "fake", "--tag", "bad tag"])
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            plan.main(["--dataset-dir", str(self.dataset), "--mode", "real", "--accounts", "1", "--tag", "fake-t"])

    def test_an_empty_pairs_input_is_the_whole_subset(self):
        self.assertEqual(len(plan.chosen_pairs(self.dataset, "")), 6)


class FakeGitHub:
    """urllib's opener as the artifacts API: a list of artifacts, each
    zip behind a redirect to a blob store that takes no token."""

    def __init__(self, listed, zips):
        self.listed = listed
        self.zips = zips
        self.requests = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, dict(request.header_items())))
        if "/actions/artifacts/" in url and url.endswith("/zip"):
            artifact_id = url.rsplit("/", 2)[1]
            raise urllib.error.HTTPError(url, 302, "Found", {"Location": f"https://blob.example/{artifact_id}"}, None)
        if url.startswith("https://blob.example/"):
            return io.BytesIO(self.zips[int(url.rsplit("/", 1)[1])])
        query = dict(part.split("=") for part in url.split("?", 1)[1].split("&"))
        page = int(query.get("page", 1))
        found = [a for a in self.listed if "name" not in query or a["name"] == urllib.parse.unquote(query["name"])]
        start = (page - 1) * artifacts.PAGE
        return io.BytesIO(json.dumps({"total_count": len(found),
                                      "artifacts": found[start:start + artifacts.PAGE]}).encode())


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as packed:
        for name, text in files.items():
            packed.writestr(name, text)
    return buffer.getvalue()


import urllib.parse  # noqa: E402


class Artifacts(unittest.TestCase):
    CONF = {"api": "https://api.example", "repository": "o/r", "token": "ghs_fake_token_for_the_test"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-artifacts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.github = FakeGitHub(
            [{"id": 1, "name": "t__solo__r__1__f1_f2", "created_at": "2026-10-01T00:00:00Z", "expired": False},
             {"id": 2, "name": "t__solo__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False},
             {"id": 3, "name": "t__sdlc__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False},
             {"id": 4, "name": "t__coop__r__1__f1_f2", "created_at": "2026-10-03T00:00:00Z", "expired": True},
             {"id": 5, "name": "other__solo__r__1__f1_f2", "created_at": "2026-10-03T00:00:00Z", "expired": False},
             {"id": 6, "name": "t__team__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False},
             {"id": 7, "name": "t__solo__r__1__f1_f2__done", "created_at": "2026-10-02T00:00:00Z", "expired": False},
             {"id": 8, "name": "t__survey__r__1", "created_at": "2026-10-02T00:00:00Z", "expired": False}],
            {1: zipped({"solo/r/task1/f1_f2/result.json": "{}"}),
             2: zipped({"solo/r/task1/f1_f2/result.json": json.dumps({"agent": {"status": "Submitted"}}),
                        "solo/r/task1/f1_f2/eval.json": json.dumps({"both_passed": False, "error": None})}),
             3: zipped({"sdlc/r/task1/f1_f2/result.json": "{}"}),
             5: zipped({"x": "y"}),
             6: zipped({"team/r/task1/f1_f2/result.json": json.dumps({"agents": {"agent1": {"status": "Submitted"}}})}),
             7: zipped({"done.txt": "done"}),
             8: zipped({"r-1.json": "[]"})})

    def test_a_finished_pair_is_scored_with_no_agent_in_error(self):
        scored = {"both_passed": True, "error": None}
        self.assertTrue(artifacts.holds_finished_pair(zipped({
            "coop/r/task1/f1_f2/result.json": json.dumps({"agents": {"agent1": {"status": "Submitted"},
                                                                     "agent2": {"status": "Submitted"}}}),
            "coop/r/task1/f1_f2/eval.json": json.dumps(scored)})))
        for name, result, evaluation, state in (
                ("an agent in Error", {"agents": {"agent1": {"status": "Submitted"}, "agent2": {"status": "Error"}}},
                 scored, "unfinished"),
                ("an eval error", {"agents": {"agent1": {"status": "Submitted"}}}, {"error": "docker died"}, "unscored"),
                ("a pair error", {"error": "HubError: gone", "agents": {"agent1": {"status": "Submitted"}}}, scored,
                 "unfinished"),
                ("no agents", {}, scored, "unfinished")):
            with self.subTest(case=name):
                data = zipped({"coop/r/task1/f1_f2/result.json": json.dumps(result),
                               "coop/r/task1/f1_f2/eval.json": json.dumps(evaluation)})
                self.assertFalse(artifacts.holds_finished_pair(data))
                self.assertEqual(artifacts.zip_state(data), state)
        self.assertEqual(artifacts.zip_state(zipped({"coop/r/task1/f1_f2/eval.json": json.dumps(scored)})), "unfinished")
        self.assertEqual(artifacts.zip_state(zipped({"coop/r/task1/f1_f2/result.json": json.dumps(
            {"agents": {"agent1": {"status": "Submitted"}}})})), "unscored")

    def test_an_agent_that_ran_out_its_time_is_kept_and_one_that_failed_is_run_again(self):
        """A timeout is the benchmark's own limit reached, scored as any
        other attempt (run.py pair_finished reads the same end_reason);
        an Error for any other cause is no sample."""
        timed_out = {"status": "Error", "error": "timed out after 7200 seconds",
                     "end_reason": "timed out after 7200 seconds"}
        scored = {"both_passed": True, "error": None}
        result = {"agents": {"agent1": {"status": "Submitted"}, "agent2": timed_out}}
        self.assertEqual(artifacts.pair_state(result, scored), "finished")
        self.assertEqual(artifacts.pair_state(result, None), "unscored")
        limited = {"status": "Error", "error": None, "end_reason": "Usage limit reached"}
        self.assertEqual(artifacts.pair_state({"agents": {"agent1": limited}}, scored), "unfinished")
        self.assertEqual(artifacts.pair_state({"agent": timed_out}, scored), "finished")
        self.assertEqual(artifacts.pair_state({"agent": {"status": "Error", "end_reason": None}}, scored), "unfinished")
        sys.path.insert(0, str(ROOT / "coordbench"))
        try:
            import drivers
        finally:
            sys.path.pop(0)
        self.assertEqual(artifacts.TIMED_OUT, drivers.TIMED_OUT)

    def test_in_real_mode_a_fake_agent_s_pair_is_not_finished(self):
        fake = zipped({"coop/r/task1/f1_f2/result.json": json.dumps({"agents": {
            "agent1": {"status": "Submitted", "claude_code_version": "0.0.0-fake.1330271801"},
            "agent2": {"status": "Submitted", "claude_code_version": "0.0.0-fake.1330271801"}}}),
            "coop/r/task1/f1_f2/eval.json": json.dumps({"both_passed": False})})
        self.assertTrue(artifacts.holds_finished_pair(fake, "fake"))
        self.assertFalse(artifacts.holds_finished_pair(fake, "real"))
        self.assertEqual(artifacts.zip_state(fake, "real"), "unfinished")

    def test_finished_reads_the_artifacts_of_the_name_newest_first_and_says_what_to_do(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", opener=self.github), 0)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__sdlc__r__1__f1_f2", opener=self.github), 1)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__team__r__1__f1_f2", opener=self.github), 3)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__coop__r__1__f1_f2", opener=self.github), 1)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__none__r__1__f1_f2", opener=self.github), 1)
        self.assertIn("scored again", printed.getvalue())
        fetched = [url for url, _ in self.github.requests if url.endswith("/zip")]
        self.assertEqual(fetched, ["https://api.example/repos/o/r/actions/artifacts/2/zip",
                                   "https://api.example/repos/o/r/actions/artifacts/3/zip",
                                   "https://api.example/repos/o/r/actions/artifacts/6/zip"])

    def test_an_older_finished_artifact_counts_when_a_rerun_of_the_name_failed(self):
        github = FakeGitHub(
            [{"id": 1, "name": "t__solo__r__1__f1_f2", "created_at": "2026-10-01T00:00:00Z", "expired": False},
             {"id": 2, "name": "t__solo__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False}],
            {1: zipped({"solo/r/task1/f1_f2/result.json": json.dumps({"agent": {"status": "Submitted"}}),
                        "solo/r/task1/f1_f2/eval.json": json.dumps({"both_passed": True})}),
             2: zipped({"solo/r/task1/f1_f2/result.json": json.dumps({"agent": {"status": "Error"}})})})
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", opener=github), 0)
            artifacts.command_download(self.CONF, "t", self.tmp, opener=github)
        self.assertTrue([url for url, _ in github.requests if url.endswith("/zip")][-1].endswith("artifacts/1/zip"))
        line = json.loads(printed.getvalue().splitlines()[-1])
        self.assertEqual((line["created_at"], line["attempts"]), ("2026-10-01T00:00:00Z", 2))
        self.assertTrue((self.tmp / "t-solo-r-1-f1_f2" / "solo" / "r" / "task1" / "f1_f2" / "eval.json").is_file())

    def test_the_blob_store_is_asked_with_no_token(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", opener=self.github)
        for url, headers in self.github.requests:
            if url.startswith("https://blob.example/"):
                self.assertNotIn("Authorization", headers)
            else:
                self.assertEqual(headers.get("Authorization"), f"Bearer {self.CONF['token']}")

    def test_a_failed_read_is_its_own_answer_not_a_reason_to_run_the_pair(self):
        class Refusing:
            def open(self, request, timeout=None):
                raise urllib.error.URLError("no route")

        class Garbled(FakeGitHub):
            def open(self, request, timeout=None):
                if request.full_url.startswith("https://blob.example/"):
                    return io.BytesIO(b"PK\x03\x04 not a zip")
                return super().open(request, timeout)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", opener=Refusing()), 2)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2",
                                                        opener=Garbled(self.github.listed, {})), 2)
        self.assertEqual(printed.getvalue().count("could not read the artifacts"), 2)

    def test_the_opener_stops_at_the_redirect_so_the_token_never_crosses_hosts(self):
        """GitHub answers the zip request with a redirect to its blob
        store; urllib's own redirect handler would send the Authorization
        header again to that host, and StopRedirect is what stops it."""
        import email
        import urllib.response

        class Transport(urllib.request.BaseHandler):
            handler_order = 100

            def __init__(self, zips):
                self.zips = zips
                self.requests = []

            def https_open(self, request):
                url = request.full_url
                self.requests.append((url, dict(request.header_items())))
                if "/actions/artifacts/" in url and url.endswith("/zip"):
                    headers = email.message_from_string(f"Location: https://blob.example/{url.rsplit('/', 2)[1]}\n")
                    return self.answer(io.BytesIO(b""), headers, url, 302, "Found")
                return self.answer(io.BytesIO(self.zips[int(url.rsplit("/", 1)[1])]),
                                   email.message_from_string(""), url, 200, "OK")

            @staticmethod
            def answer(body, headers, url, code, reason):
                answer = urllib.response.addinfourl(body, headers, url, code)
                answer.msg = reason
                return answer
        data = zipped({"x": "y"})
        guarded = Transport({5: data})
        opener = urllib.request.build_opener(artifacts.StopRedirect, guarded)
        self.assertEqual(artifacts.fetch_zip(self.CONF, 5, opener), data)
        blob = [headers for url, headers in guarded.requests if url.startswith("https://blob.example/")]
        self.assertEqual(len(blob), 1)
        self.assertNotIn("Authorization", {key for key in blob[0]})
        self.assertIn(artifacts.StopRedirect, [type(handler) for handler in artifacts.OPENER.handlers])
        # Without it, urllib follows the redirect with the first request's headers, token included.
        unguarded = Transport({5: data})
        self.assertEqual(urllib.request.build_opener(unguarded).open(urllib.request.Request(
            f"{self.CONF['api']}/repos/{self.CONF['repository']}/actions/artifacts/5/zip",
            headers={"Authorization": "Bearer t"})).read(), data)
        blob = [headers for url, headers in unguarded.requests if url.startswith("https://blob.example/")]
        self.assertIn("Authorization", {key for key in blob[0]})

    def test_fetch_unpacks_the_attempt_finished_chose(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(artifacts.command_fetch(self.CONF, "t__team__r__1__f1_f2", self.tmp, opener=self.github), 0)
            self.assertEqual(artifacts.command_fetch(self.CONF, "t__none__r__1__f1_f2", self.tmp, opener=self.github), 1)
        self.assertTrue((self.tmp / "t-team-r-1-f1_f2" / "team" / "r" / "task1" / "f1_f2" / "result.json").is_file())

    def test_an_older_unscored_attempt_is_scored_again_rather_than_the_pair_run_on_usage(self):
        """A rerun that failed after an attempt whose agents submitted:
        finished answers 3 for that older attempt, fetch takes it, and
        download keeps it; a finished attempt wins over both, whatever
        came after it."""
        submitted = json.dumps({"agents": {"agent1": {"status": "Submitted"}}})
        failed = json.dumps({"agents": {"agent1": {"status": "Error", "end_reason": "Usage limit reached"}}})
        scored = json.dumps({"both_passed": True})
        listed = [{"id": 21, "name": "u__coop__r__1__f1_f2", "created_at": "2026-10-03T00:00:00Z", "expired": False},
                  {"id": 22, "name": "u__coop__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False},
                  {"id": 23, "name": "u__coop__r__1__f1_f2", "created_at": "2026-10-01T00:00:00Z", "expired": False}]
        zips = {21: zipped({"coop/r/task1/f1_f2/result.json": failed, "coop/r/task1/f1_f2/note": "newest"}),
                22: zipped({"coop/r/task1/f1_f2/result.json": submitted, "coop/r/task1/f1_f2/note": "unscored"}),
                23: zipped({"coop/r/task1/f1_f2/result.json": failed})}
        github = FakeGitHub(listed, zips)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "u__coop__r__1__f1_f2", opener=github), 3)
            self.assertEqual(artifacts.command_fetch(self.CONF, "u__coop__r__1__f1_f2", self.tmp, opener=github), 0)
            artifacts.command_download(self.CONF, "u", self.tmp / "all", opener=github)
        self.assertIn("2026-10-02", printed.getvalue().splitlines()[0])
        self.assertEqual((self.tmp / "u-coop-r-1-f1_f2" / "coop" / "r" / "task1" / "f1_f2" / "note").read_text(), "unscored")
        self.assertEqual((self.tmp / "all" / "u-coop-r-1-f1_f2" / "coop" / "r" / "task1" / "f1_f2" / "note").read_text(),
                         "unscored")
        line = json.loads(printed.getvalue().splitlines()[-1])
        self.assertEqual((line["arm"], line["attempts"]), ("coop", 3))
        # Each command read the three attempts once and fetched the chosen one no second time.
        self.assertEqual(sum(url.startswith("https://blob.example/") for url, _ in github.requests), 9)
        zips[23] = zipped({"coop/r/task1/f1_f2/result.json": submitted, "coop/r/task1/f1_f2/eval.json": scored})
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(artifacts.command_finished(self.CONF, "u__coop__r__1__f1_f2", opener=FakeGitHub(listed, zips)), 0)

    def test_a_file_cut_short_is_read_as_missing_and_never_hides_an_older_finished_attempt(self):
        """A write a full disk cut short leaves an empty eval.json or
        result.json, which run.py and check-dir read as missing (unscored,
        unfinished); the artifact's reader reads it the same way rather
        than call the whole name unreadable, so finished and download
        still take the older finished attempt."""
        submitted = json.dumps({"agents": {"agent1": {"status": "Submitted"}}})
        pair = "coop/r/task1/f1_f2/"
        self.assertEqual(artifacts.zip_state(zipped({pair + "result.json": submitted, pair + "eval.json": ""})),
                         "unscored")
        self.assertEqual(artifacts.zip_state(zipped({pair + "result.json": "{\"agents\": {"})), "unfinished")
        listed = [{"id": 31, "name": "v__coop__r__1__f1_f2", "created_at": "2026-10-03T00:00:00Z", "expired": False},
                  {"id": 32, "name": "v__coop__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False}]
        zips = {31: zipped({pair + "result.json": submitted, pair + "eval.json": ""}),
                32: zipped({pair + "result.json": submitted, pair + "eval.json": json.dumps({"both_passed": True}),
                            pair + "note": "older"})}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "v__coop__r__1__f1_f2",
                                                        opener=FakeGitHub(listed, zips)), 0)
            artifacts.command_download(self.CONF, "v", self.tmp / "all", opener=FakeGitHub(listed, zips))
        self.assertIn("finished in run", printed.getvalue())
        self.assertEqual((self.tmp / "all" / "v-coop-r-1-f1_f2" / pair / "note").read_text(), "older")

    def test_a_run_directory_is_checked_by_the_same_rule(self):
        pair = self.tmp / "run" / "coop" / "r" / "task1" / "f1_f2"
        pair.mkdir(parents=True)
        (pair / "result.json").write_text(json.dumps({"agents": {"agent1": {"status": "Submitted"}}}))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(artifacts.command_check_dir(self.tmp / "run"), 1)
            (pair / "eval.json").write_text(json.dumps({"both_passed": True}))
            self.assertEqual(artifacts.command_check_dir(self.tmp / "run"), 0)
            self.assertEqual(artifacts.command_check_dir(self.tmp / "nowhere"), 1)

    def test_the_markers_name_the_finished_items(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_list_finished(self.CONF, "t", self.github)
        self.assertEqual(printed.getvalue().split(), ["t__solo__r__1__f1_f2"])

    def test_download_unpacks_the_newest_of_each_name_under_the_tag_into_run_directories(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_download(self.CONF, "t", self.tmp, opener=self.github)
        lines = [json.loads(line) for line in printed.getvalue().splitlines()]
        self.assertEqual([line["run_name"] for line in lines],
                         ["t-sdlc-r-1-f1_f2", "t-solo-r-1-f1_f2", "t-survey-r-1", "t-team-r-1-f1_f2"])
        self.assertTrue((self.tmp / "t-solo-r-1-f1_f2" / "solo" / "r" / "task1" / "f1_f2" / "eval.json").is_file())
        self.assertTrue((self.tmp / "t-sdlc-r-1-f1_f2" / "sdlc" / "r" / "task1" / "f1_f2" / "result.json").is_file())
        self.assertFalse((self.tmp / "other-solo-r-1-f1_f2").exists())
        self.assertFalse((self.tmp / "t-solo-r-1-f1_f2-done").exists())

    def test_download_takes_another_tag_s_survey_and_goes_on_past_a_bad_artifact(self):
        listed = self.github.listed + [
            {"id": 9, "name": "s__survey__r__1", "created_at": "2026-10-02T00:00:00Z", "expired": False},
            {"id": 10, "name": "s__solo__r__1__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False}]
        zips = {**self.github.zips, 9: zipped({"r-1.json": "[]"}), 10: zipped({"x": "y"})}

        class Gone(FakeGitHub):
            def open(self, request, timeout=None):
                if request.full_url.endswith("/artifacts/3/zip"):
                    raise urllib.error.HTTPError(request.full_url, 410, "Gone", {}, None)
                return super().open(request, timeout)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_download(self.CONF, "t", self.tmp, survey_tag="s", opener=Gone(listed, zips))
        lines = [json.loads(line) for line in printed.getvalue().splitlines()]
        self.assertEqual([line.get("run_name") for line in lines],
                         ["s-survey-r-1", None, "t-solo-r-1-f1_f2", "t-survey-r-1", "t-team-r-1-f1_f2"])
        self.assertIn("410", lines[1]["error"])
        self.assertEqual(lines[1]["name"], "t__sdlc__r__1__f1_f2")
        self.assertTrue((self.tmp / "s-survey-r-1" / "r-1.json").is_file())
        self.assertFalse((self.tmp / "s-solo-r-1-f1_f2").exists())

    def test_a_listing_cut_short_is_said(self):
        most = artifacts.PAGE * artifacts.MOST_PAGES
        many = [{"id": n, "name": f"u__solo__r__{n}__f1_f2", "created_at": "2026-10-02T00:00:00Z", "expired": False}
                for n in range(most + 1)]
        github = FakeGitHub(many, {})
        found, cut = artifacts.list_artifacts(self.CONF, opener=github)
        self.assertEqual((len(found), cut), (most, True))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_download(self.CONF, "none", self.tmp, opener=github)
        self.assertIn("cut at 5000", json.loads(printed.getvalue().splitlines()[0])["warning"])
        # Exactly as many as the pages hold is all there is: nothing was cut.
        found, cut = artifacts.list_artifacts(self.CONF, opener=FakeGitHub(many[:most], {}))
        self.assertEqual((len(found), cut), (most, False))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_download(self.CONF, "none", self.tmp, opener=FakeGitHub(many[:most], {}))
        self.assertNotIn("warning", printed.getvalue())

        class Uncounted(FakeGitHub):
            def open(self, request, timeout=None):
                answer = json.loads(super().open(request, timeout).read())
                answer.pop("total_count")
                return io.BytesIO(json.dumps(answer).encode())

        # An answer with no count says the listing may be cut, as before.
        self.assertEqual(artifacts.list_artifacts(self.CONF, opener=Uncounted(many[:most], {}))[1], True)

    def test_a_zip_that_escapes_its_directory_is_refused(self):
        with self.assertRaises(ValueError):
            artifacts.unpack(zipped({"../escape.txt": "x"}), self.tmp / "run")
        self.assertFalse((self.tmp / "escape.txt").exists())


class SurveyReport(unittest.TestCase):
    def test_the_table_reads_cooperbench_s_verdicts_and_the_survey_s_summaries(self):
        tmp = Path(tempfile.mkdtemp(prefix="coordbench-survey-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        good = tmp / "t-survey-pallets_click_task-2068"
        good.mkdir()
        (good / "pallets_click_task-2068.json").write_text(json.dumps([
            {"task": "pallets_click_task/task2068", "feature": "feature1", "verdict": "OK", "note": ""},
            {"task": "pallets_click_task/task2068", "feature": "feature6", "verdict": "OK", "note": ""}]))
        (good / "pallets_click_task-2068.summary.json").write_text(json.dumps(
            {"repo": "pallets_click_task", "task": 2068, "status": 0, "seconds": 310, "image_bytes": 1050000000,
             "disk_free_bytes": 9000000000}))
        bad = tmp / "t-survey-openai_tiktoken_task-0"
        bad.mkdir()
        (bad / "openai_tiktoken_task-0.json").write_text(json.dumps([
            {"task": "openai_tiktoken_task/task0", "feature": "feature1", "verdict": "OK", "note": ""},
            {"task": "openai_tiktoken_task/task0", "feature": "feature2", "verdict": "GOLD_FAILS", "note": "download"}]))
        # check_gradeable.py exits 0 whatever its verdicts, so the status is 0 here too.
        (bad / "openai_tiktoken_task-0.summary.json").write_text(json.dumps(
            {"repo": "openai_tiktoken_task", "task": 0, "status": 0, "seconds": 90, "image_bytes": 0,
             "disk_free_bytes": 12000000000}))
        broken = tmp / "t-survey-go_chi_task-56"
        broken.mkdir()
        (broken / "go_chi_task-56.summary.json").write_text(json.dumps(
            {"repo": "go_chi_task", "task": 56, "status": 1, "seconds": 5, "image_bytes": 0, "disk_free_bytes": 0}))
        other = tmp / "other-survey-pillow_task-25"
        other.mkdir()
        (other / "pillow_task-25.summary.json").write_text(json.dumps(
            {"repo": "pillow_task", "task": 25, "status": 0, "seconds": 1, "image_bytes": 0, "disk_free_bytes": 0}))
        self.assertEqual(len(survey_report.rows(tmp)), 4)
        text = survey_report.render(survey_report.rows(tmp, "t"))
        self.assertIn("| go_chi_task 56 | no report (the check failed before writing one) | - |", text)
        self.assertIn("| openai_tiktoken_task 0 | not all | 1/2 | - | 90 | 12.00 |", text)
        self.assertIn("| pallets_click_task 2068 | all | 2/2 | 1.05 | 310 | 9.00 |", text)
        self.assertIn("1 of 3 tasks graded whole on the runner.", text)
        self.assertIsNone(survey_report.gradeable_features({"results": []}))
        planned = tmp / "plan.json"
        planned.write_text(json.dumps({"include": [{"repo": "pallets_click_task", "task": 2068},
                                                   {"repo": "typst_task", "task": 6554}]}))
        text = survey_report.render(survey_report.rows(tmp, "t", survey_report.planned_tasks(planned)))
        self.assertIn("| typst_task 6554 | no artifact (the job ended before the check wrote its summary) | - |", text)
        self.assertIn("1 of 4 tasks graded whole on the runner.", text)


class FakeAgent(unittest.TestCase):
    """bench/cooperbench/fake_claude, run as the claude-cli driver runs
    claude, in a scratch repository: Claude Code's stream-json out, a
    patch.txt left, and the sdlc arm's commands used when they are there."""

    # No user's or system's git configuration takes part (a signing key
    # or a hook there would fail every test), and git's detached
    # maintenance never races the cleanup (symphony-template#63).
    GIT_SETTINGS = {"gc.auto": "0", "gc.autoDetach": "false", "maintenance.auto": "false",
                    "maintenance.autoDetach": "false", "receive.autogc": "false"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-fake-agent-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_COUNT": str(len(self.GIT_SETTINGS))}
        for index, (key, value) in enumerate(self.GIT_SETTINGS.items()):
            self.environment[f"GIT_CONFIG_KEY_{index}"] = key
            self.environment[f"GIT_CONFIG_VALUE_{index}"] = value
        self.repo = self.tmp / "repo"
        self.git("init", "-q", str(self.repo))
        self.git("-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                 "--allow-empty", "-m", "base")

    def git(self, *arguments, **keywords):
        """git in the test's environment; a repository made here takes the
        settings into its own config too, for the receive-pack a push
        starts, which the environment does not reach."""
        done = subprocess.run(["git", *arguments], env=self.environment, check=True, **keywords)
        if arguments[0] == "init":
            for key, value in self.GIT_SETTINGS.items():
                subprocess.run(["git", "-C", arguments[-1], "config", key, value], env=self.environment, check=True)
        return done

    def run_fake(self, prompt, shell="sh", extra_path=None, agent="agent1"):
        environment = {**self.environment, "COORD_RPC_URL": f"http://hub/rpc/{agent}"}
        if extra_path:
            environment["PATH"] = f"{extra_path}:{environment['PATH']}"
        return subprocess.run([shell, str(KIT / "fake_claude"), "-p", "--output-format", "stream-json", "--", prompt],
                              cwd=self.repo, capture_output=True, text=True, env=environment, check=True)

    def test_it_speaks_stream_json_and_leaves_a_patch(self):
        version = subprocess.run([str(KIT / "fake_claude"), "--version"], capture_output=True, text=True,
                                 check=True).stdout.strip()
        # The version carries the file's checksum, so an edited fake agent
        # is overlaid onto an image already labelled with an earlier one.
        self.assertRegex(version, r"^0\.0\.0-fake\.\d+ \(Claude Code\)$")
        for shell in ("sh", "bash", "dash"):
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                result = self.run_fake("Implement feature 1.", shell=shell)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(events[0]["type"], "system")
                self.assertEqual(events[0]["claude_code_version"], version.split()[0])
                self.assertEqual(events[-1]["type"], "result")
                self.assertEqual(events[-1]["usage"]["output_tokens"], 0)
                patch = (self.repo / "patch.txt").read_text()
                self.assertIn("coordbench_fake_agent1.txt", patch)
                self.assertIn("+A change by the fake agent agent1", patch)

    def test_in_the_sdlc_arm_it_claims_pushes_opens_and_readies_its_issue(self):
        tools = self.tmp / "tools"
        tools.mkdir()
        log = self.tmp / "calls.log"
        for name in ("sdlc-claim", "sdlc-pr", "sdlc-ready"):
            (tools / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            (tools / name).chmod(0o755)
        remote = self.tmp / "team.git"
        self.git("init", "-q", "--bare", str(remote))
        self.git("-C", str(self.repo), "remote", "add", "team", str(remote))
        self.git("-C", str(self.repo), "push", "-q", "team", "HEAD:main")
        result = self.run_fake("Your issue is **#3**. Write patch.txt.", extra_path=str(tools), agent="agent2")
        self.assertEqual(log.read_text().splitlines(), ["sdlc-claim 3", "sdlc-pr 3", "sdlc-ready 3"])
        branches = self.git("-C", str(remote), "branch", "--format=%(refname:short)",
                            capture_output=True, text=True).stdout.split()
        self.assertIn("claude/3", branches)
        self.assertIn("claimed, opened and readied #3", result.stdout)
        self.assertTrue((self.repo / "patch.txt").exists())

    def sdlc_tools(self, ready_answer, claim_status=0, ready_script=None):
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        (tools / "sdlc-claim").write_text(f"#!/bin/sh\nexit {claim_status}\n")
        (tools / "sdlc-pr").write_text("#!/bin/sh\nexit 0\n")
        (tools / "sdlc-ready").write_text(ready_script or f"#!/bin/sh\necho '{ready_answer}'\n")
        for name in ("sdlc-claim", "sdlc-pr", "sdlc-ready"):
            (tools / name).chmod(0o755)
        remote = self.tmp / "team.git"
        self.git("init", "-q", "--bare", str(remote))
        self.git("-C", str(self.repo), "remote", "add", "team", str(remote))
        self.git("-C", str(self.repo), "push", "-q", "team", "HEAD:main")
        return tools, remote

    def test_a_coordination_command_that_fails_ends_the_agent_in_error(self):
        tools, _ = self.sdlc_tools("draft", claim_status=1)
        environment = dict(self.environment, COORD_RPC_URL="http://hub/agent2", PATH=f"{tools}:{os.environ['PATH']}")
        result = subprocess.run(["sh", str(KIT / "fake_claude"), "-p", "--", "Your issue is **#3**."],
                                cwd=self.repo, capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 1)
        last = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(last["type"], "result")
        self.assertTrue(last["is_error"])
        self.assertIn("sdlc-claim 3 failed", last["result"])
        self.assertFalse((self.repo / "patch.txt").exists())

    def test_patch_txt_is_the_integrated_diff_only_once_the_pull_request_merged(self):
        tools, remote = self.sdlc_tools("draft")
        result = self.run_fake("Your issue is **#3**.", extra_path=str(tools), agent="agent2")
        self.assertIn("(draft)", result.stdout)
        own = (self.repo / "patch.txt").read_text()
        self.assertIn("coordbench_fake_agent2.txt", own)
        # Another agent's work on team/main is not the agent's own until the pull request merged.
        other = self.tmp / "other"
        self.git("clone", "-q", "-b", "main", str(remote), str(other))
        (other / "coordbench_fake_agent1.txt").write_text("another agent's change\n")
        self.git("-C", str(other), "add", "-A")
        self.git("-C", str(other), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "x")
        pushed = subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:main"], capture_output=True,
                                text=True, env=self.environment)
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertNotIn("coordbench_fake_agent1.txt", own)
        (tools / "sdlc-ready").write_text("#!/bin/sh\necho 'merged'\n")
        result = self.run_fake("Your issue is **#4**.", extra_path=str(tools), agent="agent3")
        self.assertIn("(merged)", result.stdout)
        integrated = (self.repo / "patch.txt").read_text()
        self.assertIn("coordbench_fake_agent1.txt", integrated)


    def test_main_moved_makes_it_merge_main_in_and_ready_again(self):
        """sdlc-ready exits 3 with its text on stderr when main moved past
        the branch (the gate's EXIT_MAIN_MOVED), as cli.py does: the agent
        merges team/main in, pushes, and asks again."""
        calls = self.tmp / "ready.calls"
        tools, remote = self.sdlc_tools("", ready_script=f"""#!/bin/sh
count=$(cat "{calls}" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "{calls}"
if [ "$count" = 1 ]; then
    echo 'main moved: fetch team, merge team/main in, push, and call sdlc-ready again' >&2
    exit 3
fi
echo 'merged: claude/3 is in main as abc1234'
""")
        other = self.tmp / "other"
        self.git("clone", "-q", "-b", "main", str(remote), str(other))
        (other / "coordbench_fake_agent1.txt").write_text("another agent's change\n")
        self.git("-C", str(other), "add", "-A")
        self.git("-C", str(other), "commit", "-q", "-m", "x")
        self.git("-C", str(other), "push", "-q", "origin", "HEAD:main")
        result = self.run_fake("Your issue is **#3**.", extra_path=str(tools), agent="agent2")
        self.assertIn("(merged)", result.stdout)
        self.assertEqual(calls.read_text().strip(), "2")
        on_branch = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "claude/3"], env=self.environment,
                                   capture_output=True, text=True, check=True).stdout
        self.assertIn("x", on_branch.splitlines())
        self.assertIn("coordbench_fake_agent1.txt", (self.repo / "patch.txt").read_text())
        for line in result.stdout.splitlines():
            json.loads(line)
        # Any other status is a failure, with the command's first line in the reason.
        (tools / "sdlc-ready").write_text("#!/bin/sh\necho 'refused: no such issue' >&2\nexit 1\n")
        environment = dict(self.environment, COORD_RPC_URL="http://hub/agent3", PATH=f"{tools}:{os.environ['PATH']}")
        failed = subprocess.run(["sh", str(KIT / "fake_claude"), "-p", "--", "Your issue is **#4**."],
                                cwd=self.repo, capture_output=True, text=True, env=environment)
        self.assertEqual(failed.returncode, 1)
        last = json.loads(failed.stdout.splitlines()[-1])
        self.assertEqual(last["result"], "sdlc-ready 4 failed (1): refused: no such issue")

    def test_every_event_is_valid_json_whatever_the_texts_hold(self):
        # JSON takes no control character raw: a newline, a carriage
        # return or a tab becomes a space and any other is dropped.
        environment = {**self.environment, "COORD_RPC_URL": 'http://hub/rpc/agent "one"\\two\tthree',
                       "ANTHROPIC_MODEL": 'a "quoted"\nmodel\twith\ra\x01bell\x1f'}
        result = subprocess.run(["sh", str(KIT / "fake_claude"), "-p", "--output-format", "stream-json", "--", "x"],
                                cwd=self.repo, capture_output=True, env=environment, check=True)
        lines = result.stdout.split(b"\n")
        self.assertEqual(lines[-1], b"")
        events = [json.loads(line) for line in lines[:-1]]
        self.assertEqual(events[0]["model"], 'a "quoted" model with abell')
        self.assertIn('agent "one"\\two three', events[0]["session_id"])
        self.assertEqual(events[-1]["type"], "result")


class EnvironmentGate(unittest.TestCase):
    """The plan job's gate before a real run: the environment must exist
    and have a required reviewer, and nothing it cannot read lets a run
    through."""
    CONF = Artifacts.CONF

    class Answers:
        def __init__(self, answer):
            self.answer = answer
            self.requests = []

        def open(self, request, timeout=None):
            self.requests.append(request)
            if isinstance(self.answer, Exception):
                raise self.answer
            return io.BytesIO(json.dumps(self.answer).encode())

    def gate(self, answer):
        opener = self.Answers(answer)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            status = environment.command_check(self.CONF, "bench", opener)
        return status, out.getvalue(), opener

    def test_an_environment_with_a_required_reviewer_lets_the_run_through(self):
        status, said, opener = self.gate({"name": "bench", "protection_rules": [
            {"type": "wait_timer", "wait_timer": 0},
            {"type": "required_reviewers", "reviewers": [{"type": "User", "reviewer": {"login": "owner"}}]}]})
        self.assertEqual(status, 0)
        self.assertIn("has a required reviewer", said)
        self.assertEqual(opener.requests[0].full_url, "https://api.example/repos/o/r/environments/bench")

    def test_a_rule_against_self_review_with_the_actor_as_its_only_reviewer_stops_the_run(self):
        def rule(prevent, *reviewers):
            return {"name": "bench", "protection_rules": [
                {"type": "required_reviewers", "prevent_self_review": prevent, "reviewers": list(reviewers)}]}
        owner = {"type": "User", "reviewer": {"login": "owner"}}
        other = {"type": "User", "reviewer": {"login": "other"}}
        team = {"type": "Team", "reviewer": {"slug": "maintainers"}}
        opener = self.Answers(rule(True, owner))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(environment.command_check(self.CONF, "bench", opener, actor="owner"), 1)
        self.assertIn("forbids self-review", out.getvalue())
        for name, answer, actor in (("another reviewer", rule(True, owner, other), "owner"),
                                    ("a team", rule(True, owner, team), "owner"),
                                    ("self-review allowed", rule(False, owner), "owner"),
                                    ("another actor", rule(True, owner), "other"),
                                    ("no actor known", rule(True, owner), None)):
            with self.subTest(case=name), mock.patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(environment.command_check(self.CONF, "bench", self.Answers(answer), actor=actor), 0)

    def test_a_missing_environment_or_one_with_no_reviewer_or_an_unreadable_one_stops_the_run(self):
        cases = {
            "no rules": {"name": "bench", "protection_rules": []},
            "a reviewer rule with nobody": {"name": "bench", "protection_rules": [{"type": "required_reviewers",
                                                                                   "reviewers": []}]},
            "only a wait timer": {"name": "bench", "protection_rules": [{"type": "wait_timer", "wait_timer": 5}]},
            "not there": urllib.error.HTTPError("u", 404, "Not Found", {}, None),
            "forbidden": urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
            "network": urllib.error.URLError("no route"),
        }
        for name, answer in cases.items():
            with self.subTest(case=name):
                status, said, _ = self.gate(answer)
                self.assertEqual(status, 1, said)
                self.assertTrue(said.strip(), name)


class Workflow(unittest.TestCase):
    """bench.yml and the composite action, read as text (the checks add no
    YAML parser): every file they name exists, the real jobs wait in the
    environment and queue per account, and the token reaches a job by the
    secret's name only."""

    def setUp(self):
        self.workflow = (ROOT / ".github" / "workflows" / "bench.yml").read_text(encoding="utf-8")
        self.action = (ACTIONS / "run-pair" / "action.yml").read_text(encoding="utf-8")

    def test_every_script_the_workflow_and_the_action_name_exists_and_parses(self):
        named = set(re.findall(r"bench/cooperbench/[A-Za-z0-9_./-]+\.(?:sh|py)", self.workflow + self.action))
        self.assertGreaterEqual(len(named), 5)
        for path in sorted(named):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)
                if path.endswith(".sh"):
                    parsed = subprocess.run(["bash", "-n", str(ROOT / path)], capture_output=True, text=True)
                    self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertIn("uses: ./bench/cooperbench/actions/run-pair", self.workflow)
        self.assertTrue((ACTIONS / "run-pair" / "action.yml").is_file())

    def job(self, name):
        """The text of one job of bench.yml, from its line to the next job's."""
        start = self.workflow.index(f"\n  {name}:\n") + 1
        rest = self.workflow[start + len(name) + 4:]
        match = re.search(r"\n  [a-z-]+:\n", rest)
        return self.workflow[start:start + len(name) + 4 + (match.start() if match else len(rest))]

    def test_no_job_runs_on_an_empty_matrix_which_github_fails(self):
        for name in ("run", "run-real", "approve"):
            with self.subTest(job=name):
                self.assertIn("needs.plan.outputs.count != '0'", self.job(name))
        self.assertIn("count: ${{ steps.plan.outputs.count }}", self.job("plan"))

    def test_the_jobs_are_cut_at_their_own_lines(self):
        self.assertTrue(self.job("run").startswith("  run:\n"))
        self.assertIn("mode: fake", self.job("run"))
        self.assertNotIn("run-real", self.job("run"))
        self.assertNotIn("Plan the matrix", self.job("run"))
        self.assertTrue(self.job("report").endswith("overwrite: true\n"))

    def test_one_approval_gates_the_real_jobs_which_queue_per_account_with_the_secret_by_name(self):
        approve, real = self.job("approve"), self.job("run-real")
        self.assertIn("environment: bench", approve)
        self.assertIn("if: needs.plan.outputs.mode == 'real'", approve)
        self.assertNotIn("environment:", real)
        self.assertIn("needs: [plan, approve]", real)
        self.assertIn("group: bench-${{ matrix.account }}", real)
        self.assertIn("queue: max", real)
        self.assertNotIn("cancel-in-progress", real)
        # Each slot's secret by its fixed name, never one looked up by a
        # computed name, which hands the runner every secret.
        self.assertNotIn("secrets[", self.workflow)
        for slot in plan.SLOTS:
            self.assertIn(f"(matrix.account == '{slot}' && secrets.SYMPHONY_CLAUDE_{slot})", real)
            self.assertIn(f"HAS_SECRET_{slot}: ${{{{ secrets.SYMPHONY_CLAUDE_{slot} != '' }}}}", self.job("plan"))
        self.assertNotIn("SYMPHONY_CLAUDE_9", self.workflow)
        self.assertIn("timeout-minutes: 330", real)
        gate = self.job("plan")
        self.assertIn("if: steps.plan.outputs.mode == 'real'", gate)
        self.assertIn("run: python3 bench/cooperbench/actions/environment.py bench", gate)
        self.assertIn("--list-finished", gate)
        fake = self.job("run")
        self.assertNotIn("secrets", fake)
        self.assertNotIn("environment:", fake)
        self.assertNotIn("concurrency:", fake)
        for text in (self.workflow, self.action):
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            self.assertNotIn("ANTHROPIC_API_KEY", code)

    def test_nothing_is_published_until_scrubbed_and_never_after_a_cancel(self):
        for name in ("run", "run-real"):
            with self.subTest(job=name):
                job = self.job(name)
                self.assertEqual(job.count("if: ${{ !cancelled() && steps.pair.outputs.publish == 'true' }}"), 1)
                self.assertEqual(job.count("if: ${{ !cancelled() && steps.pair.outputs.finished == 'true' && "
                                           "steps.upload.outcome == 'success' }}"), 1)
                self.assertEqual(job.count("overwrite: true"), 2)
                self.assertNotIn("if: always()", job)
                self.assertIn("timeout_min: ${{ needs.plan.outputs.timeout_min }}", job)
        self.assertIn("if: always()\n", self.job("report"))
        action = "\n".join(line for line in self.action.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("if: ${{ !cancelled() && steps.finished.outcome == 'success' && "
                      "steps.finished.outputs.skip != 'true' }}", action)
        # A step whose condition names no status function runs only when
        # every step before it succeeded, and the pair's step fails
        # whenever run.py exits 1: so every step after it says
        # !cancelled(), or a failed pair would publish no marker.
        after_pair = action[action.index("id: pair"):]
        conditions = re.findall(r"^\s+if: (.+)$", after_pair, re.M)
        self.assertEqual(len(conditions), 2, conditions)
        for condition in conditions:
            with self.subTest(condition=condition):
                self.assertTrue(condition.startswith("${{ !cancelled() && "), condition)
        self.assertIn("if: ${{ !cancelled() && steps.scrub.outputs.publish == 'true' }}\n      id: done", action)
        self.assertIn('fetch "$ARTIFACT" "$RUNNER_TEMP/rescore" --mode "$MODE"', action)
        self.assertIn("RESCORE_DIR: ${{ runner.temp }}/rescore", action)
        pair = (ACTIONS / "pair.sh").read_text(encoding="utf-8")
        self.assertLess(pair.index("bash bench/cooperbench/setup.sh"), pair.index('mv "$RESCORE_DIR/$RUN_NAME"'))
        self.assertIn("run: bash bench/cooperbench/actions/pair.sh", action)
        self.assertIn("bash bench/cooperbench/actions/scrub.sh", action)
        self.assertLess(action.index("artifacts.py finished"), action.index("free_disk.sh"))
        self.assertLess(action.index("artifacts.py fetch"), action.index("run: bash bench/cooperbench/actions/pair.sh"))

    def test_the_workflow_reads_only_and_runs_a_pull_request_in_fake_mode(self):
        self.assertIn("permissions:\n  contents: read\n  actions: read\n", self.workflow)
        self.assertIn("pull_request:\n    paths:\n      - .github/workflows/bench.yml\n      - bench/cooperbench/**\n"
                      "      - coordbench/**", self.workflow)
        for name in ("MODE", "PAIRS", "ARMS", "TAG"):
            self.assertIn(f"{name}: ${{{{ github.event_name == 'pull_request' && ", self.workflow)
        self.assertNotIn("inputs.pairs ||", self.workflow)
        self.assertIn("vars.SYMPHONY_CLAUDE_ACCOUNTS", self.workflow)
        self.assertIn("COOPERBENCH_COMMIT: 63b9d44", self.workflow)
        pinned = re.findall(r"uses: ([a-z-]+/[a-z-]+)@(v\d+)", self.workflow + self.action)
        self.assertTrue(pinned)
        self.assertEqual({action for action, _ in pinned}, {"actions/checkout", "actions/upload-artifact"})

    def test_no_secret_but_the_eight_slots_reaches_any_job_and_nothing_widens_the_read_only_permissions(self):
        code = "\n".join(line for line in self.workflow.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(set(re.findall(r"secrets[.\[]\s*([A-Za-z0-9_]+)", code)),
                         {f"SYMPHONY_CLAUDE_{slot}" for slot in plan.SLOTS})
        self.assertNotRegex(code, r"toJSON\(\s*secrets|secrets\s*\)|secrets\s*\}")
        self.assertEqual(code.count("permissions:"), 1)
        for use in re.findall(r"uses: (\S+)", self.workflow + self.action):
            with self.subTest(uses=use):
                self.assertTrue(use.startswith("./") or re.fullmatch(r"[\w-]+/[\w-]+@v\d+", use), use)

    def test_the_workflow_and_setup_sh_pin_the_same_cooperbench_commit(self):
        setup = (KIT / "setup.sh").read_text(encoding="utf-8")
        full = re.search(r"^commit=([0-9a-f]{7,40})", setup, re.M).group(1)
        short = re.search(r"COOPERBENCH_COMMIT: ([0-9a-f]+)", self.workflow).group(1)
        self.assertGreaterEqual(len(short), 7)
        self.assertTrue(full.startswith(short), (full, short))

    def test_free_disk_runs_only_on_a_hosted_runner(self):
        script = (ACTIONS / "free_disk.sh").read_text(encoding="utf-8")
        self.assertLess(script.index('if [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]'), script.index("rm -rf"))
        # GITHUB_ACTIONS is true on a self-hosted runner too, the owner's
        # own machine, so only RUNNER_ENVIRONMENT lets the deletions run.
        # The script runs with nothing of the caller's environment but
        # PATH, so no exported function or BASH_ENV stands in for a command.
        for environment_name in ("self-hosted", "", None):
            with self.subTest(runner=environment_name):
                env = {"PATH": os.environ.get("PATH", ""), "GITHUB_ACTIONS": "true"}
                if environment_name is not None:
                    env["RUNNER_ENVIRONMENT"] = environment_name
                done = subprocess.run(["bash", str(ACTIONS / "free_disk.sh")], env=env, capture_output=True, text=True)
                self.assertEqual((done.returncode, done.stdout), (0, ""))
                self.assertIn("GitHub-hosted runner", done.stderr)

    def test_free_disk_on_a_hosted_runner_removes_the_toolchains_and_the_images(self):
        # sudo is a stub that only logs what it was asked, so the test
        # deletes nothing wherever it runs: it is checked to be the sudo
        # the script will find, and the script gets nothing of the
        # caller's environment, where an exported function or BASH_ENV
        # would come before PATH.
        with tempfile.TemporaryDirectory() as tmp:
            stubs, log = Path(tmp) / "bin", Path(tmp) / "asked"
            stubs.mkdir()
            sudo = stubs / "sudo"
            sudo.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
            sudo.chmod(0o755)
            path = f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}"
            self.assertEqual(shutil.which("sudo", path=path), str(sudo))
            done = subprocess.run(["bash", str(ACTIONS / "free_disk.sh")], capture_output=True, text=True,
                                  env={"PATH": path, "HOME": tmp, "RUNNER_ENVIRONMENT": "github-hosted"})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn("GitHub-hosted runner", done.stderr)
            asked = log.read_text(encoding="utf-8").splitlines()
        self.assertIn("rm -rf /usr/share/dotnet", asked)
        self.assertIn("rm -rf /opt/hostedtoolcache/CodeQL", asked)
        self.assertEqual(asked[-1], "docker image prune -af")

    def test_the_artifact_names_are_the_planner_s(self):
        self.assertIn("name: ${{ matrix.artifact }}", self.workflow)
        self.assertIn("name: ${{ matrix.artifact }}__done", self.workflow)
        self.assertIn("name: ${{ needs.plan.outputs.tag }}__survey__${{ matrix.repo }}__${{ matrix.task }}", self.workflow)
        self.assertIn("name: report-${{ needs.plan.outputs.tag }}-${{ github.run_id }}", self.workflow)
        self.assertEqual(plan.artifact_name("t", "solo", ("r", 1, (1, 2))), "t__solo__r__1__f1_f2")
        self.assertEqual(artifacts.DONE_SUFFIX, "__done")
        self.assertIn("SURVEY_TAG: ${{ inputs.survey_tag }}", self.job("report"))
        self.assertIn("SURVEY_PLAN: ${{ needs.plan.outputs.survey }}", self.job("report"))
        self.assertIn("PLAN_MATRIX: ${{ needs.plan.outputs.matrix }}", self.job("report"))

    def test_the_dispatch_defaults_to_the_registered_pair_list(self):
        self.assertIn('default: "@bench/cooperbench/pairs.txt"', self.workflow)
        self.assertEqual(plan.REGISTERED_PAIRS, "@bench/cooperbench/pairs.txt")

    def test_the_scrub_step_stops_what_may_still_write_before_it_rewrites(self):
        scrub = (ACTIONS / "scrub.sh").read_text(encoding="utf-8")
        code = "\n".join(line for line in scrub.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn('pkill -f "[c]oordbench/run.py run"', code)
        self.assertIn("docker ps -q --filter label=coordbench.run", code)
        self.assertLess(code.index("docker rm -f"), code.index("run.py scrub --run-dir"))
        runner = (ROOT / "coordbench" / "run.py").read_text(encoding="utf-8")
        self.assertIn('"--label", f"coordbench.run={plan[\'run_id\']}"', runner)

    def test_the_readme_and_the_plan_name_the_workflow_and_the_owner_s_settings(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        for text in (".github/workflows/bench.yml", "SYMPHONY_CLAUDE_ACCOUNTS", "SYMPHONY_CLAUDE_<", "environment bench",
                     "fake_claude", "METRICS.md", "PLAN.md"):
            with self.subTest(text=text):
                self.assertIn(text, readme)
        for name in ("PLAN.md", "METRICS.md", "pairs.txt"):
            self.assertTrue((KIT / name).is_file(), name)
        self.assertIn("pairs.txt", self.workflow)

    def test_the_plan_and_the_readme_say_what_the_report_and_the_smoke_check_do(self):
        def prose(text):
            return " ".join(word for word in text.split() if word != "#")
        plan_text, readme = (prose((KIT / name).read_text(encoding="utf-8")) for name in ("PLAN.md", "README.md"))
        # The attempt the report reads, as artifacts.chosen_state picks it.
        self.assertIn("the newest artifact of its name that holds a finished pair, else the newest whose agents "
                      "did not fail but that is unscored, else the newest,", plan_text)
        self.assertIn("each by its newest finished attempt, else its newest unscored one, else its newest", plan_text)
        # The smoke check a pull request runs, as the workflow sets it.
        smoke = {name: re.search(rf"{name}: \$\{{\{{ github.event_name == 'pull_request' && '([^']+)'",
                                 self.workflow).group(1) for name in ("PAIRS", "ARMS")}
        self.assertEqual(smoke, {"PAIRS": "pallets_click_task/2068/f1_f6", "ARMS": "solo,coop-git,sdlc"})
        self.assertIn("runs one fake pair, pallets_click 2068's features 1 and 6, under three arms "
                      "(solo, coop-git and sdlc) as its check.", readme)
        self.assertIn("runs one pair of pallets_click 2068 this way under three arms as its check.",
                      prose(self.workflow))


if __name__ == "__main__":
    unittest.main()
