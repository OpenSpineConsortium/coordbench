"""new_arm.py writes a complete, loadable arm from a name, so a newcomer
starts from a working directory rather than from README.md's prose, and
--check is the smoke test that proves the directory works. These tests
pin what makes the archetype trustworthy: the checked-in example
(coordbench/examples/board) is byte for byte what the
generator writes for the name "board", so the two cannot drift; --check
passes on the example and on a fresh arm and fails with exit 1 and one
line on an arm that lacks the tools it exercises; a generated arm loads
through the hub by its directory and its tools answer; its files have the
shape of the built-in arms' (the same .mcp.json, a skill that names every
tool and shell command, an arm.json that agrees with the class); a bad
name, an existing directory or a description that is not ASCII is
refused with exit 2 and nothing written; and every file written is ASCII
with LF endings and ends with a newline."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
NEW_ARM = COORDBENCH / "new_arm.py"
EXAMPLE = COORDBENCH / "examples" / "board"
COOP_MCP_JSON = COORDBENCH / "arms" / "coop" / ".mcp.json"
sys.path.insert(0, str(COORDBENCH))

import cli  # noqa: E402
import hub as hub_module  # noqa: E402
import new_arm  # noqa: E402
from arm import SUBMISSION_BLOCK, Call, ToolError, find_arm  # noqa: E402

EXPECTED_FILES = {".claude-plugin/plugin.json", ".mcp.json", "skills/coordbench-board/SKILL.md",
                  "arm.json", "arm.py"}
MESSAGING = {"send_message": "coop-send", "broadcast": "coop-broadcast", "receive_messages": "coop-recv",
             "peek_messages": "coop-peek", "list_agents": "coop-agents", "wait_for_message": ""}
BOARD_TOOLS = {"post_notice": "board-post", "read_board": "board-read"}
ARM_JSON_KEYS = {"entry", "name", "description", "needs", "plugins", "mcp_servers"}
# Unstripped, with a trailing newline, as CooperBench reads feature.md.
SPECS = {1: "# Verbose flag\n\nAdd a --verbose flag to the CLI.\n", 2: "# TOML\n\nRead TOML configs.\n"}
BAD_NAMES = ("My-Arm", "3way", "a_b", "-x", "x-", "a--b", "", "my arm", "caf\u00e9")


def run_main(argv):
    """new_arm.main in-process: its exit code, stdout and stderr. argparse
    leaves through SystemExit for words it cannot read; that is the
    command's exit code too."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = new_arm.main(argv)
        except SystemExit as stopped:
            code = stopped.code
    return code, out.getvalue(), err.getvalue()


def files_under(directory):
    """Every file below the directory by its relative path, Python's cache
    left out, since loading an arm writes one."""
    return {p.relative_to(directory).as_posix(): p for p in directory.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def frontmatter(text):
    """The YAML frontmatter's fields and the body after it, read line by
    line as key: value, as the built-in arms' tests read them."""
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return None, text
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        fields[key] = value
    return fields, "\n".join(lines[end + 1:])


def new_run(arm, features=(1, 2)):
    """A run of the arm as the hub builds one, with its tools reachable."""
    hub = hub_module.Hub()
    hub.create_run(arm, list(features), SPECS, None, "r1")
    return hub, hub.hosted("r1").run


def call(run, agent, tool, **arguments):
    return run.arm.tools_for(run, agent)[tool].handler(Call(run, agent, arguments))


class ExampleTest(unittest.TestCase):
    """The checked-in example is the generator's output and nothing else."""

    def test_regenerating_the_example_reproduces_every_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main(["board", tmp])
            self.assertEqual((code, err), (0, ""), err)
            generated = files_under(Path(tmp) / "board")
            checked_in = files_under(EXAMPLE)
            self.assertEqual(set(generated), EXPECTED_FILES)
            self.assertEqual(set(checked_in), EXPECTED_FILES)
            for relative in EXPECTED_FILES:
                with self.subTest(file=relative):
                    self.assertEqual(generated[relative].read_bytes(), checked_in[relative].read_bytes())

    def test_the_example_is_a_plugin_and_an_arm_shaped_as_the_built_ins_are(self):
        plugin = json.loads((EXAMPLE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin, {"name": "coordbench-board", "version": "0.1.0",
                                  "description": new_arm.default_description("board")})
        self.assertEqual((EXAMPLE / ".mcp.json").read_bytes(), COOP_MCP_JSON.read_bytes())
        config = json.loads((EXAMPLE / "arm.json").read_text(encoding="utf-8"))
        arm = find_arm(str(EXAMPLE))
        self.assertEqual(set(config), ARM_JSON_KEYS)
        self.assertEqual(config["entry"], "arm.py:BoardArm")
        self.assertEqual(type(arm).__name__, "BoardArm")
        self.assertEqual((config["name"], config["description"]), (arm.name, arm.description))
        self.assertEqual((config["needs"], config["plugins"], config["mcp_servers"]), ([], [], {}))
        self.assertEqual(tuple(arm.needs), ())

    def test_the_example_skill_has_frontmatter_and_names_every_tool_and_command(self):
        fields, body = frontmatter((EXAMPLE / "skills" / "coordbench-board" / "SKILL.md")
                                   .read_text(encoding="utf-8"))
        self.assertIsNotNone(fields, "SKILL.md must open with YAML frontmatter")
        self.assertEqual(set(fields), {"name", "description"})
        self.assertEqual(fields["name"], "coordbench-board")
        self.assertNotIn(": ", fields["description"])
        self.assertNotIn(" #", fields["description"])
        _, run = new_run(find_arm(str(EXAMPLE)))
        tools = run.arm.tools_for(run, "agent1")
        self.assertEqual({name: tool.alias for name, tool in tools.items()}, {**MESSAGING, **BOARD_TOOLS})
        for name, tool in tools.items():
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", body)
                self.assertIn(f"`{tool.alias or 'coord-' + name}", body)

    def test_check_passes_on_the_example(self):
        code, out, err = run_main(["--check", str(EXAMPLE)])
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("loaded BoardArm from", out)
        self.assertIn("agent1 (peer, feature 1), agent2 (peer, feature 2)", out)
        self.assertIn("post_notice (board-post), read_board (board-read)", out)
        self.assertIn("agent1 post_notice -> posted", out)
        self.assertIn("agent2 read_board -> agent1: agent1 takes cli.py", out)
        self.assertIn("agent2 send_message -> sent to agent1", out)
        self.assertIn('"notices_posted": 1', out)
        self.assertIn('"messages_sent": 1', out)
        self.assertTrue(out.rstrip().endswith("check ok"), out)


class GeneratedArmTest(unittest.TestCase):
    """A fresh arm with a dashed name and a description of its own, written
    by the command line as a newcomer would run it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()
        self.arm_dir = self.parent / "two-boards"
        self.description = "Two boards, one per feature, and messages between them."
        self.result = subprocess.run(
            [sys.executable, str(NEW_ARM), "two-boards", str(self.parent), "--description", self.description],
            capture_output=True, text=True, cwd=ROOT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_command_writes_the_files_and_says_how_to_try_the_arm(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertEqual(self.result.stderr, "")
        lines = self.result.stdout.splitlines()
        self.assertEqual(lines[0], f"wrote {self.arm_dir}/")
        self.assertEqual(set(line.strip() for line in lines[1:6]),
                         {"skills/coordbench-two-boards/SKILL.md" if f == "skills/coordbench-board/SKILL.md" else f
                          for f in EXPECTED_FILES})
        self.assertEqual(len(lines), 7, "one line says how to try the arm")
        try_line = lines[6]
        self.assertIn(f"--check {self.arm_dir}", try_line)
        self.assertIn(f"--arm-dir {self.parent}", try_line)
        self.assertIn(f'POST /api/runs with {{"arm": "{self.arm_dir}"', try_line)
        self.assertIn('"Writing an arm" in coordbench/README.md', try_line)
        self.assertEqual(set(files_under(self.arm_dir)),
                         {f.replace("coordbench-board", "coordbench-two-boards") for f in EXPECTED_FILES})

    def test_check_passes_on_the_fresh_arm(self):
        code, out, err = run_main(["--check", str(self.arm_dir)])
        self.assertEqual((code, err), (0, ""), err)
        self.assertIn("loaded TwoBoardsArm from", out)
        self.assertTrue(out.rstrip().endswith("check ok"), out)

    def test_the_arm_loads_through_the_hub_by_its_directory_and_its_tools_answer(self):
        # The hub loads only arms under its --arm-dir directories, so the
        # parent is passed as one, as `hub.py --arm-dir <parent>` passes it.
        hub = hub_module.Hub(arm_dirs=[self.parent])
        created = hub.create_run(str(self.arm_dir), [2, 1], {1: SPECS[1], 2: SPECS[2]}, None, "pair")
        self.assertEqual(created["arm"], "two-boards")
        self.assertEqual([(a["id"], a["role"], a["features"]) for a in created["agents"]],
                         [("agent1", "peer", [2]), ("agent2", "peer", [1])])
        run = hub.hosted("pair").run
        described = {t["name"]: t for t in hub.describe_tools(run, "agent1")}
        self.assertEqual({name: t["alias"] for name, t in described.items()}, {**MESSAGING, **BOARD_TOOLS})
        self.assertEqual(described["post_notice"]["argv"], ["text..."])
        # board-post takes the rest of the line, so a notice needs no quotes.
        self.assertEqual(cli.argv_arguments(described["post_notice"], ["I", "take", "cli.py"]),
                         {"text": "I take cli.py"})
        self.assertEqual(hub.call(run, "agent2", "read_board", {}), {"text": "(the board is empty)", "isError": False})
        self.assertEqual(hub.call(run, "agent1", "post_notice", {"text": "I take cli.py"}),
                         {"text": "posted", "isError": False})
        self.assertEqual(hub.call(run, "agent2", "post_notice", {"text": "config.py is mine"})["isError"], False)
        self.assertEqual(hub.call(run, "agent2", "read_board", {})["text"],
                         "agent1: I take cli.py\nagent2: config.py is mine")
        self.assertEqual(hub.call(run, "agent2", "send_message", {"to": "agent1", "text": "ok"})["text"],
                         "sent to agent1")
        received = json.loads(hub.call(run, "agent1", "receive_messages", {})["text"])
        self.assertEqual([(m["from"], m["content"]) for m in received], [("agent2", "ok")])
        metrics = hub.close_run("pair")
        self.assertEqual(metrics["notices_posted"], 2)
        self.assertEqual(metrics["notices_by_agent"], {"agent1": 1, "agent2": 1})
        self.assertEqual(metrics["messages_sent"], 1)
        self.assertEqual(metrics["events"]["notice"], 2)
        self.assertEqual(metrics["events"]["tool"], 6)

    def test_the_prompt_puts_the_board_paragraph_between_the_spec_and_the_submission_block(self):
        arm = find_arm(str(self.arm_dir))
        _, run = new_run(arm)
        for agent, peer, feature in (("agent1", "agent2", 1), ("agent2", "agent1", 2)):
            with self.subTest(agent=agent):
                prompt = arm.prompt(run, agent, SPECS)
                spec, paragraph, submission = prompt.split("\n\n## ")
                self.assertEqual(spec, SPECS[feature].strip())
                self.assertTrue(paragraph.startswith("The shared board\n\n"))
                self.assertIn(f"You are {agent}, working alongside {peer}.", paragraph)
                self.assertIn("post_notice", paragraph)
                self.assertIn("board-post", paragraph)
                self.assertIn("read_board", paragraph)
                self.assertEqual("## " + submission, SUBMISSION_BLOCK)

    def test_a_run_of_one_agent_has_a_prompt_too(self):
        arm = find_arm(str(self.arm_dir))
        _, run = new_run(arm, (1,))
        self.assertIn("You are agent1, working alongside no other agent.", arm.prompt(run, "agent1", SPECS))

    def test_the_description_reaches_the_plugin_the_arm_json_and_the_class(self):
        plugin = json.loads((self.arm_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        config = json.loads((self.arm_dir / "arm.json").read_text(encoding="utf-8"))
        arm = find_arm(str(self.arm_dir))
        self.assertEqual(plugin["name"], "coordbench-two-boards")
        self.assertEqual(plugin["description"], self.description)
        self.assertEqual(config["description"], self.description)
        self.assertEqual(arm.description, self.description)
        self.assertEqual(config["entry"], "arm.py:TwoBoardsArm")
        self.assertEqual(arm.name, "two-boards")

    def test_the_skill_names_every_tool_and_command_of_the_fresh_arm(self):
        skill = self.arm_dir / "skills" / "coordbench-two-boards" / "SKILL.md"
        fields, body = frontmatter(skill.read_text(encoding="utf-8"))
        self.assertEqual(fields["name"], "coordbench-two-boards")
        self.assertIn("two-boards protocol", fields["description"])
        _, run = new_run(find_arm(str(self.arm_dir)))
        for name, tool in run.arm.tools_for(run, "agent1").items():
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", body)
                self.assertIn(f"`{tool.alias or 'coord-' + name}", body)

    def test_post_notice_refuses_a_missing_or_non_string_text(self):
        _, run = new_run(find_arm(str(self.arm_dir)))
        with self.assertRaises(ToolError):
            call(run, "agent1", "post_notice")
        with self.assertRaises(ToolError):
            call(run, "agent1", "post_notice", text=5)
        self.assertEqual(call(run, "agent2", "read_board"), "(the board is empty)")
        self.assertEqual(run.arm.metrics(run)["notices_posted"], 0)

    def test_the_arm_py_carries_a_stub_for_each_override_it_does_not_make(self):
        source = (self.arm_dir / "arm.py").read_text(encoding="utf-8")
        for stub in ("# def layout(self, features):", "# def setup(self, run):",
                     "# def instructions(self, run, agent):", "# def teardown(self, run):",
                     '# needs = ("git_remote", "shared_volume"):'):
            with self.subTest(stub=stub):
                self.assertIn(stub, source)
        self.assertIn("one agent per\n    # feature by default (agent1, agent2 in the features' order), all \"peer\"", source)
        self.assertIn("free what setup took, once, when the run closes", source)

    def test_every_generated_file_is_ascii_with_lf_and_ends_with_a_newline(self):
        for directory in (self.arm_dir, EXAMPLE):
            for relative, path in sorted(files_under(directory).items()):
                with self.subTest(file=f"{directory.name}/{relative}"):
                    data = path.read_bytes()
                    self.assertNotIn(b"\r", data)
                    self.assertTrue(all(byte < 128 for byte in data))
                    self.assertTrue(data.endswith(b"\n"))


class ClassNameTest(unittest.TestCase):
    def test_each_dashed_segment_is_capitalized_then_arm(self):
        self.assertEqual(new_arm.class_name_of("board"), "BoardArm")
        self.assertEqual(new_arm.class_name_of("two-boards"), "TwoBoardsArm")
        self.assertEqual(new_arm.class_name_of("arm2-v3"), "Arm2V3Arm")


class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_name_that_is_not_a_plugin_name_is_refused_and_nothing_written(self):
        for name in BAD_NAMES:
            with self.subTest(name=name):
                code, out, err = run_main([name, str(self.parent)])
                self.assertEqual(code, 2)
                # A name that starts with a dash is an option argparse does
                # not know, refused in its words; the rest in the generator's.
                self.assertTrue(err.startswith("usage:" if name.startswith("-") else "refused: "), err)
                self.assertEqual(out, "")
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_an_existing_directory_is_refused_and_left_alone(self):
        taken = self.parent / "taken"
        taken.mkdir()
        (taken / "keep.txt").write_text("mine\n", encoding="utf-8")
        code, out, err = run_main(["taken", str(self.parent)])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual([p.name for p in taken.iterdir()], ["keep.txt"])
        self.assertEqual((taken / "keep.txt").read_text(encoding="utf-8"), "mine\n")

    def test_an_existing_file_of_that_name_is_refused_too(self):
        (self.parent / "note").write_text("", encoding="utf-8")
        code, _, err = run_main(["note", str(self.parent)])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_a_parent_that_is_not_a_directory_is_refused(self):
        code, _, err = run_main(["fine", str(self.parent / "missing")])
        self.assertEqual(code, 2)
        self.assertIn("is not a directory", err)

    def test_a_description_that_is_not_one_line_of_ascii_is_refused(self):
        for description in ("", "   ", "two\nlines", "caf\u00e9", "tab\there"):
            with self.subTest(description=description):
                code, _, err = run_main(["fine", str(self.parent), "--description", description])
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith("refused: "), err)
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_check_with_a_name_or_no_arguments_at_all_is_a_usage_error(self):
        for argv in (["--check", str(EXAMPLE), "board", str(self.parent)], [], ["board"]):
            with self.subTest(argv=argv):
                code, out, err = run_main(argv)
                self.assertEqual(code, 2)
                self.assertIn("error:", err)
                self.assertEqual(out, "")
                self.assertEqual(list(self.parent.iterdir()), [])


class CheckFailureTest(unittest.TestCase):
    def test_an_arm_without_the_board_tools_fails_the_check_with_one_line(self):
        # The built-in coop arm has the messaging but no post_notice.
        code, out, err = run_main(["--check", "coop"])
        self.assertEqual(code, 1)
        self.assertEqual(err, "check failed: unknown tool 'post_notice'\n")
        self.assertNotIn("check ok", out)

    def test_a_directory_that_is_no_arm_fails_the_check_with_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_main(["--check", tmp])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("check failed: no arm named "), err)
        self.assertEqual(err.count("\n"), 1)
        self.assertEqual(out, "")

    def test_a_refused_tool_call_fails_the_check(self):
        class Refusing:
            """A hub whose every call is refused, as a tool that raises
            ToolError is answered."""

            def call(self, run, agent, tool, arguments):
                return {"text": "no", "isError": True}

        with self.assertRaises(RuntimeError) as failed:
            new_arm.answer_of(Refusing(), None, "agent1", "post_notice", {"text": "x"})
        self.assertEqual(str(failed.exception), "agent1's post_notice was refused: no")


if __name__ == "__main__":
    unittest.main()
