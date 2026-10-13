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
        result = plan.plan(self.dataset, "real", "flash", "solo,coop,sdlc", "alpha beta", "t1")
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
        self.assertEqual(by_pair[("pallets_click_task", 2068, "f1_f6")][0]["account"], "alpha")
        self.assertEqual(by_pair[("pallets_click_task", 2068, "f2_f10")][0]["account"], "beta")
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
             2: zipped({"solo/r/task1/f1_f2/result.json": "{}", "solo/r/task1/f1_f2/eval.json": "{}"}),
             3: zipped({"sdlc/r/task1/f1_f2/result.json": "{}"}),
             5: zipped({"x": "y"})})

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
        (bad / "openai_tiktoken_task-0.summary.json").write_text(json.dumps(
            {"repo": "openai_tiktoken_task", "task": 0, "status": 1, "seconds": 90, "image_bytes": 0,
             "disk_free_bytes": 12000000000}))
        broken = tmp / "t-survey-go_chi_task-56"
        broken.mkdir()
        (broken / "go_chi_task-56.summary.json").write_text(json.dumps(
            {"repo": "go_chi_task", "task": 56, "status": 1, "seconds": 5, "image_bytes": 0, "disk_free_bytes": 0}))
        text = survey_report.render(survey_report.rows(tmp))
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
        self.assertEqual(subprocess.run([str(KIT / "fake_claude"), "--version"], capture_output=True, text=True,
                                        check=True).stdout.strip(), "0.0.0-fake (Claude Code)")
        for shell in ("sh", "bash", "dash"):
            if shutil.which(shell) is None:
                continue
            with self.subTest(shell=shell):
                result = self.run_fake("Implement feature 1.", shell=shell)
                events = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(events[0]["type"], "system")
                self.assertEqual(events[0]["claude_code_version"], "0.0.0-fake")
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
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets[format('SYMPHONY_CLAUDE_{0}', matrix.account)] }}", real)
        self.assertIn("timeout-minutes: 330", real)
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
