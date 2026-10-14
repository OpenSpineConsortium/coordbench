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
        items, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one"], "t")
        self.assertEqual(len(items), plan.QUEUE_LIMIT)
        self.assertEqual(dropped, 20)
        items, dropped = plan.plan_items(pairs, ["solo", "coop"], ["one", "two"], "t")
        self.assertEqual((len(items), dropped), (120, 0))

    def test_a_matrix_holds_at_most_256_jobs_in_any_mode_and_a_fake_run_has_no_queue_cap(self):
        pairs = [("r", n, (1, 2)) for n in range(43)]
        items, dropped = plan.plan_items(pairs, list(plan.ARMS) + ["coop-nocomm"], ["one", "two", "three"], "t")
        self.assertEqual((len(items), dropped), (plan.MATRIX_LIMIT, 2))
        items, dropped = plan.plan_items(pairs, list(plan.ARMS), ["fake"], "t", queue_limit=0)
        self.assertEqual((len(items), dropped), (215, 0))
        many = {"name": "flash", "tasks": [{"repo": "r", "task_id": n, "pairs": [[1, 2]]} for n in range(120)]}
        (Path(self.dataset) / "subsets" / "flash.json").write_text(json.dumps(many), encoding="utf-8")
        fake = plan.plan(self.dataset, "fake", "flash", "solo,coop", "", "t")
        self.assertEqual((len(fake["items"]), fake["dropped"]), (240, 0))
        real = plan.plan(self.dataset, "real", "flash", "solo,coop,sdlc", "1", "t")
        self.assertEqual((len(real["items"]), real["dropped"]), (100, 260))
        self.assertIn("256 jobs from one matrix and queues at most 100 per account", real["notes"][0])
        with self.assertRaises(ValueError):
            plan.plan(self.dataset, "real", "flash", "solo", "1, nine", "t")

    def test_a_real_run_needs_accounts_and_the_other_modes_do_not(self):
        with self.assertRaises(ValueError):
            plan.plan(self.dataset, "real", "flash", "solo", "", "t")
        fake = plan.plan(self.dataset, "fake", "go_chi_task/26", "solo", "", "t")
        self.assertEqual([i["account"] for i in fake["items"]], ["fake"])
        survey = plan.plan(self.dataset, "survey", "flash", "", "", "t")
        self.assertEqual(len(survey["survey"]), 3)

    def test_the_command_writes_the_workflow_s_outputs(self):
        out = Path(self.tmp) / "outputs"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out)}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code = plan.main(["--dataset-dir", str(self.dataset), "--mode", "fake", "--pairs", "go_chi_task/26",
                              "--arms", "solo,sdlc", "--tag", "smoke-pr7"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed.getvalue())["pairs"], 1)
        lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
        self.assertEqual(lines["count"], "2")
        self.assertEqual(lines["tag"], "smoke-pr7")
        self.assertEqual([i["arm"] for i in json.loads(lines["matrix"])["include"]], ["solo", "sdlc"])
        self.assertEqual(json.loads(lines["survey"]), {"include": [{"repo": "go_chi_task", "task": 26}]})
        with self.assertRaises(SystemExit):
            plan.main(["--dataset-dir", str(self.dataset), "--mode", "fake", "--tag", "bad tag"])


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
        return io.BytesIO(json.dumps({"artifacts": found[start:start + artifacts.PAGE]}).encode())


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
             {"id": 5, "name": "other__solo__r__1__f1_f2", "created_at": "2026-10-03T00:00:00Z", "expired": False}],
            {1: zipped({"solo/r/task1/f1_f2/result.json": "{}"}),
             2: zipped({"solo/r/task1/f1_f2/result.json": json.dumps({"agent": {"status": "Submitted"}}),
                        "solo/r/task1/f1_f2/eval.json": json.dumps({"both_passed": False, "error": None})}),
             3: zipped({"sdlc/r/task1/f1_f2/result.json": "{}"}),
             5: zipped({"x": "y"})})

    def test_a_finished_pair_is_scored_with_no_agent_in_error(self):
        scored = {"both_passed": True, "error": None}
        self.assertTrue(artifacts.holds_finished_pair(zipped({
            "coop/r/task1/f1_f2/result.json": json.dumps({"agents": {"agent1": {"status": "Submitted"},
                                                                     "agent2": {"status": "Submitted"}}}),
            "coop/r/task1/f1_f2/eval.json": json.dumps(scored)})))
        for name, result, evaluation in (
                ("an agent in Error", {"agents": {"agent1": {"status": "Submitted"}, "agent2": {"status": "Error"}}}, scored),
                ("an eval error", {"agents": {"agent1": {"status": "Submitted"}}}, {"error": "docker died"}),
                ("a pair error", {"error": "HubError: gone", "agents": {"agent1": {"status": "Submitted"}}}, scored),
                ("no agents", {}, scored)):
            with self.subTest(case=name):
                self.assertFalse(artifacts.holds_finished_pair(zipped({
                    "coop/r/task1/f1_f2/result.json": json.dumps(result),
                    "coop/r/task1/f1_f2/eval.json": json.dumps(evaluation)})))
        self.assertFalse(artifacts.holds_finished_pair(zipped({"coop/r/task1/f1_f2/eval.json": json.dumps(scored)})))

    def test_finished_reads_the_newest_artifact_of_the_name_and_wants_an_eval(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", self.github), 0)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__sdlc__r__1__f1_f2", self.github), 1)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__coop__r__1__f1_f2", self.github), 1)
            self.assertEqual(artifacts.command_finished(self.CONF, "t__none__r__1__f1_f2", self.github), 1)
        fetched = [url for url, _ in self.github.requests if url.endswith("/zip")]
        self.assertEqual(fetched, ["https://api.example/repos/o/r/actions/artifacts/2/zip",
                                   "https://api.example/repos/o/r/actions/artifacts/3/zip"])

    def test_the_blob_store_is_asked_with_no_token(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", self.github)
        for url, headers in self.github.requests:
            if url.startswith("https://blob.example/"):
                self.assertNotIn("Authorization", headers)
            else:
                self.assertEqual(headers.get("Authorization"), f"Bearer {self.CONF['token']}")

    def test_an_api_failure_means_the_pair_runs(self):
        class Refusing:
            def open(self, request, timeout=None):
                raise urllib.error.URLError("no route")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            self.assertEqual(artifacts.command_finished(self.CONF, "t__solo__r__1__f1_f2", Refusing()), 1)
        self.assertIn("the pair runs", printed.getvalue())

    def test_download_unpacks_the_newest_of_each_name_under_the_tag_into_run_directories(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            artifacts.command_download(self.CONF, "t", self.tmp, self.github)
        lines = [json.loads(line) for line in printed.getvalue().splitlines()]
        self.assertEqual([line["run_name"] for line in lines], ["t-sdlc-r-1-f1_f2", "t-solo-r-1-f1_f2"])
        self.assertTrue((self.tmp / "t-solo-r-1-f1_f2" / "solo" / "r" / "task1" / "f1_f2" / "eval.json").is_file())
        self.assertTrue((self.tmp / "t-sdlc-r-1-f1_f2" / "sdlc" / "r" / "task1" / "f1_f2" / "result.json").is_file())
        self.assertFalse((self.tmp / "other-solo-r-1-f1_f2").exists())

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


class FakeAgent(unittest.TestCase):
    """bench/cooperbench/fake_claude, run as the claude-cli driver runs
    claude, in a scratch repository: Claude Code's stream-json out, a
    patch.txt left, and the sdlc arm's commands used when they are there."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coordbench-fake-agent-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "base"], check=True)

    def run_fake(self, prompt, shell="sh", extra_path=None, agent="agent1"):
        environment = {**os.environ, "COORD_RPC_URL": f"http://hub/rpc/{agent}"}
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
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "team", str(remote)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "team", "HEAD:main"], check=True)
        result = self.run_fake("Your issue is **#3**. Write patch.txt.", extra_path=str(tools), agent="agent2")
        self.assertEqual(log.read_text().splitlines(), ["sdlc-claim 3", "sdlc-pr 3", "sdlc-ready 3"])
        branches = subprocess.run(["git", "-C", str(remote), "branch", "--format=%(refname:short)"],
                                  capture_output=True, text=True, check=True).stdout.split()
        self.assertIn("claude/3", branches)
        self.assertIn("claimed, opened and readied #3", result.stdout)
        self.assertTrue((self.repo / "patch.txt").exists())

    def sdlc_tools(self, ready_answer, claim_status=0):
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        (tools / "sdlc-claim").write_text(f"#!/bin/sh\nexit {claim_status}\n")
        (tools / "sdlc-pr").write_text("#!/bin/sh\nexit 0\n")
        (tools / "sdlc-ready").write_text(f"#!/bin/sh\necho '{ready_answer}'\n")
        for name in ("sdlc-claim", "sdlc-pr", "sdlc-ready"):
            (tools / name).chmod(0o755)
        remote = self.tmp / "team.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "team", str(remote)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "push", "-q", "team", "HEAD:main"], check=True)
        return tools, remote

    def test_a_coordination_command_that_fails_ends_the_agent_in_error(self):
        tools, _ = self.sdlc_tools("draft", claim_status=1)
        environment = dict(os.environ, COORD_RPC_URL="http://hub/agent2", PATH=f"{tools}:{os.environ['PATH']}")
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
        subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
        (other / "coordbench_fake_agent1.txt").write_text("another agent's change\n")
        subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-q", "-m", "x"],
                       check=True)
        pushed = subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertNotIn("coordbench_fake_agent1.txt", own)
        (tools / "sdlc-ready").write_text("#!/bin/sh\necho 'merged'\n")
        result = self.run_fake("Your issue is **#4**.", extra_path=str(tools), agent="agent3")
        self.assertIn("(merged)", result.stdout)
        integrated = (self.repo / "patch.txt").read_text()
        self.assertIn("coordbench_fake_agent1.txt", integrated)


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

    def test_the_real_jobs_wait_for_a_reviewer_and_queue_per_account_with_the_secret_by_name(self):
        real = self.workflow[self.workflow.index("  run-real:"):self.workflow.index("  report:")]
        self.assertIn("environment: bench", real)
        self.assertIn("group: bench-${{ matrix.account }}", real)
        self.assertIn("queue: max", real)
        self.assertNotIn("cancel-in-progress", real)
        # Each slot's secret by its fixed name, never one looked up by a
        # computed name, which hands the runner every secret.
        self.assertNotIn("secrets[", self.workflow)
        for slot in plan.SLOTS:
            self.assertIn(f"(matrix.account == '{slot}' && secrets.SYMPHONY_CLAUDE_{slot})", real)
        self.assertNotIn("SYMPHONY_CLAUDE_9", real)
        self.assertIn("timeout-minutes: 330", real)
        gate = self.workflow[self.workflow.index("\n  plan:"):self.workflow.index("\n  survey:")]
        self.assertIn("if: steps.plan.outputs.mode == 'real'", gate)
        self.assertIn("run: python3 bench/cooperbench/actions/environment.py bench", gate)
        fake = self.workflow[self.workflow.index("  run:"):self.workflow.index("  run-real:")]
        self.assertNotIn("secrets", fake)
        self.assertNotIn("environment:", fake)
        for text in (self.workflow, self.action):
            code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
            self.assertNotIn("ANTHROPIC_API_KEY", code)

    def test_the_workflow_reads_only_and_runs_a_pull_request_in_fake_mode(self):
        self.assertIn("permissions:\n  contents: read\n  actions: read\n", self.workflow)
        self.assertIn("pull_request:\n    paths:\n      - .github/workflows/bench.yml\n      - bench/cooperbench/**\n"
                      "      - coordbench/**", self.workflow)
        self.assertIn("MODE: ${{ inputs.mode || 'fake' }}", self.workflow)
        self.assertIn("vars.SYMPHONY_CLAUDE_ACCOUNTS", self.workflow)
        self.assertIn(f"COOPERBENCH_COMMIT: 63b9d44", self.workflow)
        pinned = re.findall(r"uses: ([a-z-]+/[a-z-]+)@(v\d+)", self.workflow + self.action)
        self.assertTrue(pinned)
        self.assertEqual({action for action, _ in pinned}, {"actions/checkout", "actions/upload-artifact"})

    def test_the_artifact_names_are_the_planner_s(self):
        self.assertIn("name: ${{ matrix.artifact }}", self.workflow)
        self.assertIn("name: ${{ needs.plan.outputs.tag }}__survey__${{ matrix.repo }}__${{ matrix.task }}", self.workflow)
        self.assertIn("name: report-${{ needs.plan.outputs.tag }}-${{ github.run_id }}", self.workflow)
        self.assertEqual(plan.artifact_name("t", "solo", ("r", 1, (1, 2))), "t__solo__r__1__f1_f2")

    def test_the_readme_and_the_plan_name_the_workflow_and_the_owner_s_settings(self):
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        for text in (".github/workflows/bench.yml", "SYMPHONY_CLAUDE_ACCOUNTS", "SYMPHONY_CLAUDE_<", "environment bench",
                     "fake_claude", "METRICS.md", "PLAN.md"):
            with self.subTest(text=text):
                self.assertIn(text, readme)
        for name in ("PLAN.md", "METRICS.md", "pairs.txt"):
            self.assertTrue((KIT / name).is_file(), name)
        self.assertIn("pairs.txt", self.workflow)


if __name__ == "__main__":
    unittest.main()
