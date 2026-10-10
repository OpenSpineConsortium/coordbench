"""The solo, coop and coop-git arms are CooperBench's own settings re-made as
plug-ins, so a run through coordbench can be set beside a run through
CooperBench's runner. These tests pin what makes that comparison fair:
the agents are laid out and numbered as CooperBench numbers them, each
prompt is CooperBench's text in CooperBench's order (checked against its
own build_instruction when its source is at hand), each arm serves the
tools CooperBench's agents had and no others, and each directory is a
well-formed Claude Code plugin as well as a coordbench arm."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
ARMS = COORDBENCH / "arms"
sys.path.insert(0, str(COORDBENCH))

import hub as hub_module  # noqa: E402
import mcp  # noqa: E402
from arm import SUBMISSION_BLOCK, AgentSpec, Call, Run, ToolError, find_arm  # noqa: E402

ARM_NAMES = ("solo", "coop", "coop-git", "coop-nocomm")
# The arms with tools ship a skill and an MCP config; solo and coop-nocomm,
# with none, ship neither, as CooperBench's solo agent and its agents under
# --no-messaging had neither.
ARMS_WITH_TOOLS = ("coop", "coop-git")
ARMS_WITHOUT_TOOLS = ("solo", "coop-nocomm")
SOLO_FILES = {".claude-plugin/plugin.json", "arm.json", "arm.py"}
CLASSES = {"solo": "SoloArm", "coop": "CoopArm", "coop-git": "CoopGitArm", "coop-nocomm": "CoopNoCommArm"}
NEEDS = {"solo": (), "coop": (), "coop-git": ("git_remote",), "coop-nocomm": ()}
BASH = {"tool_surface": "bash"}
# Claude Code gives up on an HTTP MCP call after 60 s unless the server's
# config says otherwise; 330 s is past the longest wait any built-in arm
# allows (300 s). The header carries the agent's own hub token, which a
# run the runner creates wants on every request; with the variable unset
# it reads "Bearer ", which an open run ignores.
MCP_JSON = {"mcpServers": {"coordbench": {"type": "http", "url": "${COORD_MCP_URL}", "timeout": 330000,
                                           "headers": {"Authorization": "Bearer ${COORD_AGENT_TOKEN:-}"}}}}
LONGEST_ARM_WAIT_MS = 300 * 1000
ARM_JSON_KEYS = {"entry", "name", "description", "needs", "plugins", "mcp_servers"}
ALIASES = {"send_message": "coop-send", "broadcast": "coop-broadcast", "receive_messages": "coop-recv",
           "peek_messages": "coop-peek", "list_agents": "coop-agents"}
RULE = "\n\n---\n\n"
# Unstripped, with a trailing newline, as CooperBench reads feature.md.
SPECS = {1: "# Verbose flag\n\nAdd a --verbose flag to the CLI.\n",
         2: "# TOML\n\nTeach the config loader to read TOML.\n",
         3: "# Colours\n\nColour the help output.\n"}


def load(name):
    """An arm by its directory, the way a plug-in from elsewhere is loaded."""
    return find_arm(str(ARMS / name))


def new_run(name, features=(1, 2), options=None):
    arm = load(name)
    run = Run("r1", arm, arm.layout(list(features)), options)
    arm.setup(run)
    return arm, run


def call(run, agent, tool, **arguments):
    return run.arm.tools_for(run, agent)[tool].handler(Call(run, agent, arguments))


def call_over_mcp(run, agent, tool, **arguments):
    """A tools/call as the hub answers it, so the run records a "tool" event."""
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}
    return mcp.handle(request, run, agent)["result"]


def frontmatter(text):
    """The YAML frontmatter's fields and the body after it. The fields are
    read line by line as key: value; a value a YAML parser would read
    otherwise (another ': ' or a ' #') is refused by the test below."""
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        return None, text
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        fields[key] = value
    return fields, "\n".join(lines[end + 1:])


def cooperbench_prompt_module():
    """CooperBench's prompt.py, loaded on its own from a checkout named by
    COOPERBENCH_DIR (the variable bench/cooperbench/setup.sh uses) or at
    ~/cooperbench. The file alone, not the package: the package's
    __init__ imports the whole runner and its third-party dependencies,
    while prompt.py needs nothing but the standard library."""
    roots = [os.environ.get("COOPERBENCH_DIR"), str(Path.home() / "cooperbench")]
    for root in filter(None, roots):
        path = Path(root) / "src" / "cooperbench" / "agents" / "_coop" / "prompt.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("cooperbench_coop_prompt", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


COOPERBENCH = cooperbench_prompt_module()


class ArmFilesTest(unittest.TestCase):
    """Each arm directory is a Claude Code plugin and a coordbench arm."""

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_each_arm_loads_by_its_directory(self):
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                arm = load(name)
                self.assertEqual(type(arm).__name__, CLASSES[name])
                self.assertEqual(arm.name, name)
                self.assertEqual(arm.config["name"], name)

    def test_each_arm_loads_by_its_built_in_name(self):
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                self.assertEqual(type(find_arm(name)).__name__, CLASSES[name])

    def test_plugin_json_names_the_plugin(self):
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                plugin = self.read_json(ARMS / name / ".claude-plugin" / "plugin.json")
                self.assertEqual(set(plugin), {"name", "version", "description"})
                self.assertEqual(plugin["name"], f"coordbench-{name}")
                self.assertEqual(plugin["version"], "0.1.0")
                self.assertTrue(plugin["description"].strip())

    def test_mcp_json_names_the_hub_with_a_timeout_past_the_longest_wait(self):
        for name in ARMS_WITH_TOOLS:
            with self.subTest(arm=name):
                config = self.read_json(ARMS / name / ".mcp.json")
                self.assertEqual(config, MCP_JSON)
                self.assertGreater(config["mcpServers"]["coordbench"]["timeout"], LONGEST_ARM_WAIT_MS)

    def test_the_arms_without_tools_ship_only_the_plugins_name_the_arm_and_its_code(self):
        # No skill and no .mcp.json: a Claude Code that loads the plugin
        # sees what CooperBench's solo agent, or its agents under
        # --no-messaging, saw: the prompt and nothing more.
        for name in ARMS_WITHOUT_TOOLS:
            with self.subTest(arm=name):
                files = {str(p.relative_to(ARMS / name)) for p in (ARMS / name).rglob("*")
                         if p.is_file() and "__pycache__" not in p.parts}
                self.assertEqual(files, SOLO_FILES)
                self.assertFalse((ARMS / name / ".mcp.json").exists())
                self.assertFalse((ARMS / name / "skills").exists())

    def test_arm_json_agrees_with_the_arm(self):
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                config = self.read_json(ARMS / name / "arm.json")
                arm = load(name)
                self.assertEqual(set(config), ARM_JSON_KEYS)
                self.assertEqual(config["entry"], f"arm.py:{CLASSES[name]}")
                self.assertEqual(config["name"], name)
                self.assertEqual(config["description"], arm.description)
                self.assertEqual(tuple(config["needs"]), NEEDS[name])
                self.assertEqual(tuple(arm.needs), NEEDS[name])
                self.assertEqual(config["plugins"], [])
                self.assertEqual(config["mcp_servers"], {})

    def test_skill_frontmatter_names_the_skill(self):
        for name in ARMS_WITH_TOOLS:
            with self.subTest(arm=name):
                skill = ARMS / name / "skills" / f"coordbench-{name}" / "SKILL.md"
                fields, body = frontmatter(skill.read_text(encoding="utf-8"))
                self.assertIsNotNone(fields, "SKILL.md must open with YAML frontmatter")
                self.assertEqual(set(fields), {"name", "description"})
                self.assertEqual(fields["name"], f"coordbench-{name}")
                self.assertRegex(fields["name"], r"^[a-z0-9-]+$")
                self.assertTrue(fields["description"].strip())
                self.assertNotIn(": ", fields["description"])
                self.assertNotIn(" #", fields["description"])
                self.assertTrue(body.strip())

    def test_skills_name_every_tool_and_its_shell_alias(self):
        for name in ARMS_WITH_TOOLS:
            with self.subTest(arm=name):
                arm, run = new_run(name)
                body = (ARMS / name / "skills" / f"coordbench-{name}" / "SKILL.md").read_text(encoding="utf-8")
                for agent in run.agents:
                    for tool in arm.tools_for(run, agent).values():
                        self.assertIn(f"`{tool.name}`", body)
                        self.assertIn(f"`{tool.alias}", body)

    def test_coop_skills_tell_the_agent_nothing_the_prompt_does_not(self):
        # The skills restate CooperBench's prompt; the sentence about
        # messages never being pushed into the conversation was coordbench's
        # own addition, which the authors' agents never read.
        for name in ARMS_WITH_TOOLS:
            with self.subTest(arm=name):
                body = (ARMS / name / "skills" / f"coordbench-{name}" / "SKILL.md").read_text(encoding="utf-8")
                self.assertNotIn("never pushed", body)
                self.assertNotIn("only when you receive them", body)

    def test_coop_git_skill_gives_the_merge_sequence(self):
        body = (ARMS / "coop-git" / "skills" / "coordbench-coop-git" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("git fetch team && git merge --no-edit team/<peer> || true", body)
        self.assertIn("git diff team/main..HEAD > patch.txt", body)

    def test_every_file_is_ascii_with_lf(self):
        for name in ARM_NAMES:
            for path in sorted((ARMS / name).rglob("*")):
                if path.is_dir() or "__pycache__" in path.parts:
                    continue
                with self.subTest(file=str(path.relative_to(ARMS))):
                    data = path.read_bytes()
                    self.assertNotIn(b"\r", data)
                    self.assertTrue(all(byte < 128 for byte in data))


class LayoutTest(unittest.TestCase):
    def test_solo_is_one_agent_with_every_feature_in_the_given_order(self):
        # solo.py walks the features as the task names them, unsorted.
        self.assertEqual(load("solo").layout([2, 1]), [AgentSpec("agent1", "solo", (2, 1))])

    def test_coop_arms_number_agents_by_sorted_feature(self):
        # coop.py sorts the features and gives agent1 the lowest.
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                self.assertEqual(load(name).layout([2, 1]),
                                 [AgentSpec("agent1", "peer", (1,)), AgentSpec("agent2", "peer", (2,))])

    def test_coop_nocomm_keeps_coops_layout(self):
        self.assertEqual(load("coop-nocomm").layout([2, 1]), load("coop").layout([2, 1]))

    def test_coop_arms_give_each_of_three_features_its_own_agent(self):
        agents = load("coop").layout([3, 1, 2])
        self.assertEqual([(a.id, a.features) for a in agents],
                         [("agent1", (1,)), ("agent2", (2,)), ("agent3", (3,))])


class PromptTest(unittest.TestCase):
    """The blocks, in CooperBench's order: spec, submission, coop, git."""

    def prompts(self, name, features=(1, 2)):
        arm, run = new_run(name, features)
        return arm, run, {agent: arm.prompt(run, agent, SPECS) for agent in run.agents}

    def test_solo_prompt_heads_each_feature_then_gives_the_submission_block(self):
        _, _, prompts = self.prompts("solo", (2, 1))
        expected = RULE.join([f"## Feature 2\n\n{SPECS[2]}", f"## Feature 1\n\n{SPECS[1]}", SUBMISSION_BLOCK])
        self.assertEqual(prompts["agent1"], expected)

    def test_coop_prompt_is_spec_submission_then_cooperation(self):
        arm, run, prompts = self.prompts("coop")
        for agent, partner, feature in (("agent1", "agent2", 1), ("agent2", "agent1", 2)):
            with self.subTest(agent=agent):
                sections = prompts[agent].split(RULE)
                self.assertEqual(len(sections), 3)
                self.assertEqual(sections[0], SPECS[feature])
                self.assertEqual(sections[1], SUBMISSION_BLOCK)
                self.assertTrue(sections[2].startswith("## Cooperation protocol"))
                self.assertIn(f"You are **{agent}**, working alongside: **{partner}**.", sections[2])
                self.assertNotIn("## Git collaboration", prompts[agent])

    def test_coop_prompt_names_the_mcp_tool_behind_each_command(self):
        arm, run, prompts = self.prompts("coop")
        note = arm.mcp_note(run, "agent1")
        self.assertEqual(note.count(". "), 0, "the addition is one sentence")
        for name, alias in ALIASES.items():
            self.assertIn(f"`{alias}` is `{name}`", note)
        self.assertIn("```\n\n" + note + "\n\nRecommended workflow:", prompts["agent1"])
        self.assertEqual(prompts["agent1"].count(note), 1)

    def test_coop_git_prompt_adds_the_git_block_last(self):
        _, _, prompts = self.prompts("coop-git")
        for agent, partner in (("agent1", "agent2"), ("agent2", "agent1")):
            with self.subTest(agent=agent):
                sections = prompts[agent].split(RULE)
                self.assertEqual(len(sections), 4)
                self.assertEqual(sections[1], SUBMISSION_BLOCK)
                self.assertTrue(sections[2].startswith("## Cooperation protocol"))
                self.assertTrue(sections[3].startswith("## Git collaboration"))
                self.assertIn(f"- Your branch: `{agent}` (already created and pushed)", sections[3])
                self.assertIn(f"- Partner branches: `team/{partner}`", sections[3])
                self.assertIn(f"git merge --no-edit team/{partner} || true", sections[3])
                self.assertIn(f"git push team {agent} --force || true", sections[3])

    def test_three_agents_name_both_partners_and_merge_both(self):
        _, _, prompts = self.prompts("coop-git", (1, 2, 3))
        prompt = prompts["agent2"]
        self.assertIn("working alongside: **agent1, agent3**", prompt)
        self.assertIn("Partner branches: `team/agent1`, `team/agent3`", prompt)
        self.assertEqual(prompt.count("git fetch team && git merge --no-edit"), 2)
        self.assertIn("git log team/agent1 --oneline -10", prompt)

    def test_coop_nocomm_prompt_is_the_spec_and_the_submission_block_alone(self):
        # Nothing about a partner: no cooperation block and no git block,
        # as build_instruction writes it given no agents.
        _, run, prompts = self.prompts("coop-nocomm")
        for agent, feature in (("agent1", 1), ("agent2", 2)):
            with self.subTest(agent=agent):
                self.assertEqual(prompts[agent], RULE.join([SPECS[feature], SUBMISSION_BLOCK]))
                self.assertNotIn("agent", prompts[agent].replace("patch.txt", ""))

    def test_the_bash_surface_leaves_the_mcp_sentence_out(self):
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                arm, run = new_run(name, options=BASH)
                self.assertEqual(arm.mcp_note(run, "agent1"), "")
                self.assertNotIn("MCP", arm.prompt(run, "agent1", SPECS))

    def test_a_coop_run_of_one_has_no_cooperation_block(self):
        # CooperBench drops the block when an agent has no partners.
        _, _, prompts = self.prompts("coop-git", (2,))
        self.assertEqual(prompts["agent1"], RULE.join([SPECS[2], SUBMISSION_BLOCK]))


@unittest.skipIf(COOPERBENCH is None, "CooperBench's source is not here; set COOPERBENCH_DIR to its checkout")
class CooperBenchParityTest(unittest.TestCase):
    """The prompts against CooperBench's own build_instruction, same inputs."""

    def test_solo_prompt_is_cooperbenchs(self):
        arm, run = new_run("solo", (2, 1))
        task = RULE.join(f"## Feature {n}\n\n{SPECS[n]}" for n in (2, 1))
        self.assertEqual(arm.prompt(run, "agent1", SPECS), COOPERBENCH.build_instruction(task))

    def test_coop_prompts_are_cooperbenchs_but_for_the_mcp_sentence(self):
        for name, git in (("coop", False), ("coop-git", True)):
            for features in ((1, 2), (1, 2, 3)):
                arm, run = new_run(name, features)
                for agent, spec in run.agents.items():
                    with self.subTest(arm=name, agent=agent, features=features):
                        ours = arm.prompt(run, agent, SPECS)
                        ours = ours.replace("\n\n" + arm.mcp_note(run, agent), "", 1)
                        theirs = COOPERBENCH.build_instruction(
                            SPECS[spec.features[0]], agents=list(run.agents), agent_id=agent, git_enabled=git)
                        self.assertEqual(ours, theirs)

    def test_coop_nocomm_prompts_are_cooperbenchs_under_no_messaging(self):
        # cooperbench run --no-messaging: is_coop is false, so the adapter
        # calls build_instruction with agents and agent_id None, and the git
        # block, which follows the cooperation block, goes with it.
        for features in ((1, 2), (1, 2, 3)):
            arm, run = new_run("coop-nocomm", features)
            for agent, spec in run.agents.items():
                with self.subTest(agent=agent, features=features):
                    theirs = COOPERBENCH.build_instruction(SPECS[spec.features[0]], agents=None, agent_id=None)
                    self.assertEqual(arm.prompt(run, agent, SPECS), theirs)

    def test_bash_surface_prompts_are_cooperbenchs_byte_for_byte(self):
        for name, git in (("coop", False), ("coop-git", True)):
            for features in ((1, 2), (1, 2, 3)):
                arm, run = new_run(name, features, BASH)
                for agent, spec in run.agents.items():
                    with self.subTest(arm=name, agent=agent, features=features):
                        theirs = COOPERBENCH.build_instruction(
                            SPECS[spec.features[0]], agents=list(run.agents), agent_id=agent, git_enabled=git)
                        self.assertEqual(arm.prompt(run, agent, SPECS), theirs)

    def test_submission_block_is_cooperbenchs(self):
        self.assertEqual(SUBMISSION_BLOCK, COOPERBENCH._SUBMISSION_BLOCK)


class ToolsTest(unittest.TestCase):
    def test_solo_has_no_tools(self):
        arm, run = new_run("solo")
        self.assertEqual(arm.tools_for(run, "agent1"), {})

    def test_coop_nocomm_has_no_tools(self):
        arm, run = new_run("coop-nocomm")
        for agent in run.agents:
            self.assertEqual(arm.tools_for(run, agent), {})

    def test_on_the_bash_surface_mcp_serves_nothing_and_the_shell_everything(self):
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                arm, run = new_run(name, options=BASH)
                self.assertEqual(arm.mcp_tools_for(run, "agent1"), {})
                self.assertEqual(set(arm.tools_for(run, "agent1")), set(ALIASES))

    def test_the_bash_surface_loads_no_plugin_and_no_server(self):
        # CooperBench's claude_code coop agent had no plugin and no MCP
        # server: the coop-* commands in Bash were all.
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                self.assertEqual(load(name).claude_surface(BASH),
                                 {"tool_surface": "bash", "plugin": False, "mcp_servers": {}})
                self.assertEqual(load(name).claude_surface({}),
                                 {"tool_surface": "mcp", "plugin": True, "mcp_servers": {}})

    def test_coop_arms_have_cooperbenchs_messaging_and_no_wait(self):
        for name in ("coop", "coop-git"):
            arm, run = new_run(name)
            for agent in run.agents:
                with self.subTest(arm=name, agent=agent):
                    tools = arm.tools_for(run, agent)
                    self.assertEqual({t: tools[t].alias for t in tools}, ALIASES)
                    self.assertNotIn("wait_for_message", tools)


class MessagingTest(unittest.TestCase):
    """A run driven through Call, as the hub drives it for every transport."""

    def test_send_then_peek_then_receive(self):
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                _, run = new_run(name)
                self.assertEqual(call(run, "agent1", "send_message", to="agent2", text="I take cli.py"),
                                 "sent to agent2")
                self.assertEqual(call(run, "agent2", "peek_messages"), "1")
                self.assertEqual(call(run, "agent1", "peek_messages"), "0")
                received = json.loads(call(run, "agent2", "receive_messages"))
                self.assertEqual([(m["from"], m["to"], m["content"]) for m in received],
                                 [("agent1", "agent2", "I take cli.py")])
                self.assertEqual(call(run, "agent2", "peek_messages"), "0")
                self.assertEqual(json.loads(call(run, "agent2", "receive_messages")), [])

    def test_broadcast_reaches_every_peer_but_the_sender_and_says_nothing(self):
        # coop-broadcast prints nothing in coop_msg.py, so the tool's text
        # is empty and the command stays silent.
        _, run = new_run("coop", (1, 2, 3))
        self.assertEqual(call(run, "agent2", "broadcast", text="config.py is mine"), "")
        self.assertEqual([call(run, a, "peek_messages") for a in run.agents], ["1", "0", "1"])
        message = json.loads(call(run, "agent3", "receive_messages"))[0]
        self.assertEqual((message["from"], message["content"]), ("agent2", "config.py is mine"))

    def test_messages_arrive_oldest_first(self):
        _, run = new_run("coop")
        for text in ("one", "two", "three"):
            call(run, "agent2", "send_message", to="agent1", text=text)
        received = json.loads(call(run, "agent1", "receive_messages"))
        self.assertEqual([m["content"] for m in received], ["one", "two", "three"])

    def test_list_agents_names_everyone(self):
        _, run = new_run("coop-git")
        self.assertEqual(call(run, "agent1", "list_agents"), "agent1\nagent2")

    def test_a_send_to_any_name_or_of_nothing_is_queued_as_coop_msg_queues_it(self):
        # coop_msg.py checks neither the recipient's name nor the text: a
        # message to a name not in the run waits in an inbox nobody reads,
        # and a missing text is sent as "" (what stdin gives at its end).
        _, run = new_run("coop")
        self.assertEqual(call(run, "agent1", "send_message", to="agent9", text="hello"), "sent to agent9")
        self.assertEqual(call(run, "agent1", "send_message", to="agent2"), "sent to agent2")
        self.assertEqual(call(run, "agent2", "peek_messages"), "1")
        self.assertEqual([m["content"] for m in json.loads(call(run, "agent2", "receive_messages"))], [""])
        self.assertEqual(run.state["inboxes"]["agent9"][0]["content"], "hello")

    def test_a_send_without_a_recipient_is_refused_with_argparses_code(self):
        _, run = new_run("coop")
        with self.assertRaises(ToolError) as caught:
            call(run, "agent1", "send_message", text="hello")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertEqual(call(run, "agent2", "peek_messages"), "0")

    def test_each_entry_carries_cooperbenchs_fields(self):
        _, run = new_run("coop")
        call(run, "agent1", "send_message", to="agent2", text="hello")
        entry = json.loads(call(run, "agent2", "receive_messages"))[0]
        self.assertEqual(set(entry), {"from", "to", "content", "timestamp", "timestamp_iso"})

    def test_a_drain_whose_reply_was_never_written_puts_the_messages_back(self):
        # The hub runs the call's undo when the client was gone before the
        # answer was written (a broken pipe, as in the pilot): the drained
        # messages go back at the head of the inbox, in order, ahead of
        # what arrived since, and the record keeps the handover and its
        # loss, so a reader does not count them received twice.
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                arm, run = new_run(name)
                for text in ("one", "two"):
                    call(run, "agent1", "send_message", to="agent2", text=text)
                drain = Call(run, "agent2", {})
                drained = json.loads(arm.tools_for(run, "agent2")["receive_messages"].handler(drain))
                self.assertEqual([m["content"] for m in drained], ["one", "two"])
                self.assertEqual(call(run, "agent2", "peek_messages"), "0")
                call(run, "agent1", "send_message", to="agent2", text="three")
                drain.lost()
                self.assertEqual(call(run, "agent2", "peek_messages"), "3")
                self.assertEqual([m["content"] for m in json.loads(call(run, "agent2", "receive_messages"))],
                                 ["one", "two", "three"])
                self.assertEqual([(e["kind"], e["count"]) for e in run.events if e["kind"].startswith("receive")],
                                 [("receive", 2), ("receive_lost", 2), ("receive", 3)])


class MetricsTest(unittest.TestCase):
    def test_coop_metrics_count_messages_and_each_tools_calls(self):
        for name in ("coop", "coop-git"):
            with self.subTest(arm=name):
                arm, run = new_run(name)
                call_over_mcp(run, "agent1", "send_message", to="agent2", text="hello")
                call_over_mcp(run, "agent1", "send_message", to="agent2", text="again")
                call_over_mcp(run, "agent2", "broadcast", text="ok")
                call_over_mcp(run, "agent2", "peek_messages")
                call_over_mcp(run, "agent2", "receive_messages")
                refused = call_over_mcp(run, "agent2", "send_message", to="agent1", text=5)
                self.assertTrue(refused["isError"])
                metrics = arm.metrics(run)
                self.assertEqual(metrics["messages_sent"], 3)
                self.assertEqual(metrics["messages_by_agent"], {"agent1": 2, "agent2": 1})
                self.assertEqual(metrics["message_chars"], len("hello") + len("again") + len("ok"))
                # A refused call is still a call the agent made.
                self.assertEqual(metrics["tool_calls"], {"send_message": 3, "broadcast": 1,
                                                         "peek_messages": 1, "receive_messages": 1})
                self.assertEqual(metrics["events"]["tool"], 6)

    def test_the_arms_without_tools_carry_the_same_keys_at_zero(self):
        for name in ARMS_WITHOUT_TOOLS:
            with self.subTest(arm=name):
                arm, run = new_run(name)
                metrics = arm.metrics(run)
                self.assertEqual(metrics["messages_sent"], 0)
                self.assertEqual(metrics["tool_calls"], {})
                self.assertEqual(set(metrics), set(load("coop").metrics(new_run("coop")[1])))

    def test_the_metrics_say_which_tool_surface_ran(self):
        self.assertEqual(load("coop").metrics(new_run("coop")[1])["tool_surface"], "mcp")
        self.assertEqual(load("coop").metrics(new_run("coop", options=BASH)[1])["tool_surface"], "bash")


class HubTest(unittest.TestCase):
    """The arms as the runner reaches them: by name, through the hub."""

    def test_the_hub_creates_a_run_of_each_arm(self):
        hub = hub_module.Hub()
        specs = {str(n): text for n, text in SPECS.items()}
        options = {"git_remote": "git://host.docker.internal/team.git", "base_ref": "abc123"}
        expected = {"solo": [("agent1", "solo", [2, 1])],
                    "coop": [("agent1", "peer", [1]), ("agent2", "peer", [2])],
                    "coop-git": [("agent1", "peer", [1]), ("agent2", "peer", [2])],
                    "coop-nocomm": [("agent1", "peer", [1]), ("agent2", "peer", [2])]}
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                answer = hub.create_run(name, [2, 1], specs, options, f"run-{name}")
                self.assertEqual(answer["arm"], name)
                self.assertEqual([(a["id"], a["role"], a["features"]) for a in answer["agents"]],
                                 expected[name])
                for agent in answer["agents"]:
                    for number in agent["features"]:
                        self.assertIn(SPECS[number], agent["prompt"])
                    self.assertIn(SUBMISSION_BLOCK, agent["prompt"])
                described = hub.get_run(f"run-{name}")
                self.assertEqual(described["metrics"]["tool_calls"], {})

    def test_every_arm_here_offers_both_surfaces_and_sdlc_only_mcp(self):
        hub = hub_module.Hub()
        specs = {str(n): text for n, text in SPECS.items()}
        options = {"git_remote": "git://host.docker.internal/team.git", "base_ref": "abc123", **BASH}
        for name in ARM_NAMES:
            with self.subTest(arm=name):
                hub.create_run(name, [1, 2], specs, options, f"bash-{name}")
        with self.assertRaises(hub_module.HubError) as refused:
            hub.create_run("sdlc", [1, 2], specs, options, "bash-sdlc")
        self.assertEqual(refused.exception.status, 400)

    def test_the_hub_serves_coop_tools_with_their_aliases(self):
        hub = hub_module.Hub()
        hub.create_run("coop", [1, 2], {"1": SPECS[1], "2": SPECS[2]}, None, "r-tools")
        run = hub.hosted("r-tools").run
        described = {t["name"]: (t["alias"], t["argv"]) for t in hub.describe_tools(run, "agent1")}
        self.assertEqual(described["send_message"], ("coop-send", ["to", "text"]))
        self.assertEqual({name: alias for name, (alias, _) in described.items()}, ALIASES)
        answer = hub.call(run, "agent1", "send_message", {"to": "agent2", "text": "hi"})
        self.assertEqual(answer, {"text": "sent to agent2", "isError": False})
        self.assertEqual(hub.close_run("r-tools")["tool_calls"], {"send_message": 1})


if __name__ == "__main__":
    unittest.main()
