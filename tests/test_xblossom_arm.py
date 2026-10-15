"""The xblossom arm (bench/cooperbench/arms/xblossom) is the team arm plus
one prompt section, so a difference between the two arms in a run comes
from the prompt alone. These tests pin what makes that so: the pair's
setting is the team arm's, the team prompt is an exact prefix of every
xblossom prompt on both tool surfaces, with and without a git remote and
for one to three agents, the section leaves no placeholder and asks for
no inbox habit the team lead lacks, only a member writes its patch to the
volume, the fact template stores a line full of shell syntax exactly,
alone and inside the session route's bash -lc '...', as long as the line
holds no single quote, which the section tells the agents to leave out
and which no quoting inside that wrapper could carry, and the arm runs in
a hub as the team arm does: an atomic claim, a refused second claim with
exit 2, a done note carrying its certificate, and metrics that count the
fact files on the volume and never a symlink planted there.

new_arm.py --check cannot pass for this arm, as it cannot for the team
arm: it builds its run with no options, so the hub refuses an arm that
needs the shared volume before the first tool call."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "coordbench"))

import hub as hub_module  # noqa: E402
import run as run_module  # noqa: E402
from arm import find_arm  # noqa: E402

ARM_DIR = ROOT / "bench" / "cooperbench" / "arms" / "xblossom"
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
FACT = 'src/x.py: `edit()` is a stub, "quoted" $HOME -- checked by: grep -n "def edit" src/x.py'


def blob_hash(content):
    """What git hash-object prints for these bytes in a SHA-1 repository."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def options(volume, surface="mcp", git=False):
    chosen = {"shared_dir": "/workspace/shared", "shared_dir_host": str(volume), "tool_surface": surface}
    if git:
        chosen.update(git_remote="git://example/repo.git", git_remote_host="/nonexistent")
    return chosen


def prompts(arm, features, opts, run_id):
    hub = hub_module.Hub()
    specs = {n: f"Spec of feature {n}.\n" for n in features}
    answer = hub.create_run(arm, features, specs, opts, run_id, agent_tokens=True)
    hub.close_run(run_id)
    return {agent["id"]: (agent["role"], agent["prompt"]) for agent in answer["agents"]}


def answer_text(hub, run, agent, tool, arguments):
    answer = hub.call(run, agent, tool, arguments)
    if answer["isError"]:
        raise AssertionError(f"{agent}'s {tool} failed: {answer}")
    return answer["text"]


class XblossomArmTest(unittest.TestCase):

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="coordbench-xblossom-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        self.arm = find_arm(str(ARM_DIR))
        self.team = run_module.find_arm("team")

    def test_the_pair_is_filed_as_the_team_arm_files_it(self):
        layout = [("agent1", [5]), ("agent2", [11])]
        self.assertEqual(run_module.pair_setting(self.arm, layout), "team")
        self.assertEqual(run_module.pair_setting(self.team, layout), "team")
        self.assertEqual(run_module.pair_setting(self.arm, layout[:1]), "solo")
        self.assertEqual(self.arm.needs, self.team.needs)

    def test_the_team_prompt_is_a_prefix_and_the_section_holds_together(self):
        volume = self.scratch / "vol"
        for surface in ("mcp", "bash"):
            for git in (False, True):
                for features in ([5], [5, 11], [5, 7, 11]):
                    tag = f"{surface}-{int(git)}-{len(features)}"
                    ours = prompts(self.arm, features, options(volume, surface, git), "xb-" + tag)
                    theirs = prompts(self.team, features, options(volume, surface, git), "team-" + tag)
                    self.assertEqual(sorted(ours), sorted(theirs))
                    for agent, (role, prompt) in ours.items():
                        with self.subTest(case=tag, agent=agent):
                            self.check_prompt(role, prompt, theirs[agent][1], alone=len(features) == 1)

    def check_prompt(self, role, prompt, team_prompt, alone):
        self.assertTrue(prompt.startswith(team_prompt), "the team prompt is a prefix")
        added = prompt[len(team_prompt):]
        if alone:
            self.assertEqual(added, "", "a team of one gets the team prompt alone")
            return
        # Every agent of a team gets the section, the lead included, on
        # both surfaces and with or without a git remote.
        self.assertIn(role, ("lead", "member"))
        self.assertIn("Four working rules", added)
        self.assertIsNone(PLACEHOLDER.search(added), "no placeholder left")
        self.assertNotIn("$(git diff", added, "the certificate substitutes no command")
        self.assertNotIn("coop-recv", added, "no inbox habit added")
        self.assertEqual("git diff > /workspace/shared/" in added, role == "member",
                         "only a member writes a patch to the volume")
        self.assertIn("owner is empty", " ".join(added.split()), "a lost claim goes to an unowned task")

    def test_the_fact_template_stores_shell_syntax_exactly(self):
        prompt = prompts(self.arm, [5, 11], options(self.scratch / "vol"), "xb-fact")["agent2"][1]
        flat = " ".join(prompt.split())
        self.assertIn("Leave single quotes out of the line", flat, "the one thing bash -lc '...' cannot carry")
        self.assertIn("use only double quotes inside it", flat, "the done note's own quoting")
        self.assertNotIn("'", FACT, "the line under test is one the section allows")
        block = next(b for b in re.findall(r"```bash\n(.*?)```", prompt, re.S) if "<<" in b)
        repo, shared = self.scratch / "repo", self.scratch / "shared"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "x.py").write_text("def edit():\n    pass\n", encoding="ascii")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        script = (block.replace("/workspace/repo", str(repo)).replace("/workspace/shared", str(shared))
                  .replace("<path>: <fact> -- checked by: <command>", FACT).replace("<path>", "src/x.py"))
        direct = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        wrapped = subprocess.run(["bash", "-c", f"bash -lc '{script}'"], capture_output=True, text=True)
        self.assertEqual((direct.returncode, wrapped.returncode), (0, 0), direct.stderr + wrapped.stderr)
        stored = [path.read_text(encoding="utf-8") for path in (shared / "facts").iterdir()]
        self.assertEqual(stored, [FACT + "\n" + FACT + "\n"], "both runs stored the line, under one hash")

    def test_a_run_claims_certifies_and_counts_its_facts(self):
        volume = self.scratch / "vol"
        hub = hub_module.Hub()
        specs = {1: "Add a --verbose flag.\n", 2: "Read TOML config.\n"}
        hub.create_run(self.arm, [1, 2], specs, options(volume), "xb-smoke")
        run = hub.hosted("xb-smoke").run
        task = answer_text(hub, run, "agent1", "task_create", {"title": "config loader: config.py"})
        answer_text(hub, run, "agent2", "task_claim", {"task_id": task})
        refused = hub.call(run, "agent1", "task_claim", {"task_id": task})
        self.assertTrue(refused["isError"])
        self.assertEqual(refused.get("exitCode"), 2, "a lost claim exits 2")
        facts = volume / "facts"
        facts.mkdir(parents=True)
        (facts / blob_hash(b"def load(path):\n")).write_text("config.py: load reads text -- checked by: grep\n")
        (facts / ("0" * 40)).symlink_to("/etc/hostname")
        (facts / "notes.txt").write_text("not a fact file\n")
        note = ("patch: /workspace/shared/agent2.patch\n config.py | 3 ++-\n"
                " 1 file changed, 2 insertions(+), 1 deletion(-)\ntest: python -m pytest -> exit 0")
        answer_text(hub, run, "agent2", "task_update", {"task_id": task, "status": "done", "note": note})
        listed = json.loads(answer_text(hub, run, "agent1", "task_list", {"mine": False, "open": False}))
        self.assertTrue(any(t["id"] == task and t["status"] == "done" and t["last_note"] == note for t in listed))
        metrics = hub.close_run("xb-smoke")
        self.assertEqual((metrics["fact_files"], metrics["fact_bytes"]),
                         (1, len("config.py: load reads text -- checked by: grep\n")))
        for key in ("claims_per_agent", "tasks_total", "tasks_done"):
            self.assertIn(key, metrics, "the team arm's metrics stay")

    def test_the_metrics_say_unknown_when_the_volume_is_out_of_sight(self):
        # The arm's module is loaded by path and kept nowhere else.
        fact_metrics = type(self.arm).metrics.__globals__["fact_metrics"]
        unseen = type("Run", (), {"options": {"shared_dir_host": str(self.scratch / "missing")}})()
        self.assertEqual(fact_metrics(unseen), {"fact_files": None, "fact_bytes": None})
        (self.scratch / "empty").mkdir()
        empty = type("Run", (), {"options": {"shared_dir_host": str(self.scratch / "empty")}})()
        self.assertEqual(fact_metrics(empty), {"fact_files": 0, "fact_bytes": 0})


if __name__ == "__main__":
    unittest.main()
