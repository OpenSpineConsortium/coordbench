# coordbench: coordination as a plug-in

coordbench measures how the way coding agents are wired together changes
what they get done. It keeps the task, the agents and the scoring fixed and
makes the wiring a plug-in, so two ways of coordinating can be compared on
the same work, and a new way can be tried by writing one small directory.

Three things plug in:

- an **arm**: how agents coordinate (alone, by messages, through a shared
  git remote, as a team with a lead, through pull requests and a merge
  gate, or anything new);
- a **driver**: which agent does the work (headless Claude Code in a
  container, this Claude Code session's own subagents, or any harness with
  a start and a wait);
- the **tasks and the scorer**: CooperBench's dataset and its own
  evaluation, unchanged, so results sit next to the authors'.

```
            runner (run.py)                         hub (hub.py)
  task pair -> provision -> driver per agent        one Run per pair
               containers    |                      the arm's tools, served as
               git remote    v                        MCP over HTTP   (/mcp)
               volume      agent  <---- tools ---->   stdio bridge    (bridge.py)
                             |                        shell commands  (/rpc, cli.py)
                             v                      events -> metrics
                          patch.txt -> CooperBench's evaluation -> eval.json
```

## The arms that come with it

| Arm | What it is | Needs |
| --- | --- | --- |
| solo | one agent implements both features | nothing |
| coop | one agent per feature, messages only (CooperBench's Coop) | nothing |
| coop-git | coop plus a shared git remote they push to and merge from | git_remote |
| team | a lead and members, a shared task list, typed requests, a scratchpad | shared_volume |
| sdlc | this repository's claim protocol: issues, exclusive claims, pull requests, a merge gate that answers "main moved" | git_remote |

solo, coop, coop-git and team are CooperBench's own arms (cooperbench/
CooperBench at 63b9d44, MIT licence) re-made as plug-ins: their prompts
are CooperBench's word for word, and tests compare them with CooperBench's
own builders when its source is present (COOPERBENCH_DIR). Their tools
keep CooperBench's command names (coop-send, coop-task-claim and the rest,
with the same arguments and exit codes) and are also MCP tools. A run
through coordbench can therefore be checked against the same arm run
through CooperBench's runner (experiment/run_arms.sh), which tells how
much of a result is the harness rather than the arm.

## An arm is a Claude Code plugin

```
my-arm/
  .claude-plugin/plugin.json   {"name": "coordbench-my-arm", "version": "0.1.0", "description": "..."}
  .mcp.json                    {"mcpServers": {"coordbench": {"type": "http", "url": "${COORD_MCP_URL}"}}}
  skills/coordbench-my-arm/SKILL.md   the protocol, in words
  arm.json                     {"entry": "arm.py:MyArm", "name": "my-arm", "description": "...",
                                "needs": [], "plugins": [], "mcp_servers": {}}
  arm.py                       the arm's code
```

Because the directory is a Claude Code plugin, an agent started with
`claude --plugin-dir my-arm` and COORD_MCP_URL in its environment has the
arm's tools over MCP and its skill, with nothing else to install. Any other
MCP client reaches the same tools at the same URL, or through the stdio
bridge for clients that only speak stdio:

```json
{"mcpServers": {"coordbench": {"command": "python3", "args": ["/opt/coordbench/bridge.py"],
                               "env": {"COORD_MCP_URL": "http://host.docker.internal:8765/mcp/RUN/agent1"}}}}
```

and any shell reaches them as commands: `python3 cli.py install /usr/local/bin`
writes one wrapper per tool, under the tool's alias (coop-send) and as
coord-<tool>.

## Writing an arm

An arm is a subclass of Arm (arm.py) that overrides what it changes. The
whole of a working arm that gives every agent CooperBench's messaging and
a shared notice board:

```python
from arm import Arm, Tool
import kit


def post(call):
    with call.run.lock:
        call.run.state.setdefault("board", []).append(f"{call.agent}: {call.arg('text', required=True)}")
    call.run.emit("post", call.agent)
    return "posted"


def read(call):
    with call.run.lock:
        return "\n".join(call.run.state.get("board", [])) or "(the board is empty)"


class BoardArm(Arm):
    name = "board"

    def tools(self, run, agent):
        return kit.messaging_tools() + [
            Tool("post_notice", "Pin a line on the shared board.", post,
                 kit.schema(["text"], text=kit.TEXT), alias="board-post", argv=("text...",)),
            Tool("read_board", "Every line on the board, oldest first.", read, alias="board-read"),
        ]

    def prompt(self, run, agent, specs):
        protocol = "Use post_notice (board-post) to say which files you take; read_board first."
        return super().prompt(run, agent, specs).replace("## Submission protocol",
                                                         protocol + "\n\n## Submission protocol")
```

What the base class gives and what an arm may override:

- `layout(features)`: the agents, their roles and features; one agent per
  feature by default (agent1, agent2 in the features' order), all "peer".
- `setup(run)`: seed run.state (a task list, a queue); `run.specs` holds
  each feature's spec.
- `tools(run, agent)`: the agent's tools. A Tool has a name, a description,
  a JSON schema and a handler that takes a Call and returns text, or raises
  ToolError(message, exit_code) to refuse. `roles` limits it to some roles;
  `alias`, `argv` and `flags` make its shell command.
- `prompt(run, agent, specs)`: what the agent is told; the base prompt is
  its feature specs and CooperBench's submission block.
- `metrics(run)`: numbers from run.events (every tool call and every
  `run.emit(kind, agent, ...)`) and run.state.
- `teardown(run)`: free what setup took, once, when the run closes.
- `needs`: "git_remote" (a bare repository, remote `team` in every agent's
  repository; run.options git_remote, git_remote_host and base_ref say
  where) and "shared_volume" (/workspace/shared in every container;
  run.options shared_dir and shared_dir_host). The hub refuses a run
  whose options do not provide what its arm needs.

run.lock is a re-entrant condition that guards run.state; every change
under it, and every emit, wakes the tools waiting on it, so a blocking
tool is `run.wait_until(lambda: <condition on run.state>, timeout)`. kit.py
holds the blocks most arms share: CooperBench's messaging, the long-poll
wait_for_message, the schema helper and message metrics.

## Bringing in existing Claude extensions

An arm can bring any Claude Code plugin or MCP server along, so wiring
built for people (a memory server, a code-review plugin, a hook) can be
benchmarked as coordination:

- arm.json "plugins": plugin directories (copied into each agent's
  container) or URLs, each loaded with --plugin-dir or --plugin-url;
- arm.json "mcp_servers": MCP server configs in .mcp.json form, handed to
  each agent with --mcp-config;
- the arm's own directory may carry anything else a plugin can: subagents
  (agents/), hooks (hooks/hooks.json), more skills. A hook is a way to wire
  agents without asking them: a PostToolUse hook that runs coop-peek after
  every edit puts the peers' news in front of the agent whether it asks or
  not.

## Drivers

- `claude-cli`: headless Claude Code in each agent's container, the arm
  loaded with --plugin-dir, as CooperBench's claude_code adapter runs it.
  It needs CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) in the
  runner's environment; the token reaches the container by name only and is
  never written anywhere.
- `session`: this Claude Code session's own subagents. The agent's
  repository is a host directory mounted into its container, commands run
  with docker exec, and the coordination commands are per-agent wrappers on
  the host. `run.py prepare` provisions the pair and prints a plan of
  prompts; the session spawns one subagent per prompt (the Workflow tool);
  `run.py finish` waits for each agent's marker, scores and tears down.
- your own: `--driver path/to/driver.py:Class`, any class with start(job)
  and wait(handle) (drivers.py says what they take and return).

## Running

After experiment/setup.sh and experiment/prepare_task.sh for the task:

```
python3 experiment/coordbench/run.py run --arm team --repo pallets_click_task --task 2068 \
    --subset flash --driver claude-cli --name flash-team-coordbench
python3 experiment/report.py --run team-coordbench=flash-team-coordbench --run team-cooperbench=flash-team-cc
```

The logs take CooperBench's layout, so `cooperbench eval` and
experiment/report.py read them as they read the authors' runs. A new arm
needs no change to anything but its own directory: `--arm path/to/my-arm`,
and the hub is started with that directory allowed.

## The hub, for other harnesses

`python3 hub.py --host 0.0.0.0 --port 8765 [--log-dir DIR] [--arm-dir DIR]`
serves:

- `POST /api/runs` {"arm", "features", "specs", "options", "run_id"} ->
  the run's agents, each with its mcp_url, rpc_url and prompt;
  `GET /api/runs/{run}`, `GET /api/runs/{run}/events`,
  `POST /api/runs/{run}/close` -> metrics; `GET /api/health`;
- `POST /mcp/{run}/{agent}`: MCP over Streamable HTTP (2025-06-18; JSON
  answers, an Mcp-Session-Id from initialize);
- `GET` and `POST /rpc/{run}/{agent}`: the tools as plain JSON for shell
  commands.

Loading an arm runs its code in the hub, so the hub loads only built-in
arms and arms under its --arm-dir directories. With COORD_ADMIN_TOKEN in
its environment the /api routes want that bearer token, which the runner
holds and agents do not. Agents are told apart by their URLs only, as
CooperBench's Redis told them apart by key: an agent that wanted to could
speak as another. Benchmarked agents have no reason to, and the events log
would show it.
