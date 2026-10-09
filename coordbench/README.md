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
| team | a lead and members, a shared task list, typed requests, a scratchpad; its wait_for_message works, where CooperBench's did not (see below) | shared_volume |
| sdlc | this repository's claim protocol: issues, exclusive claims, pull requests, a merge gate that answers "main moved" | git_remote |

solo, coop, coop-git and team are CooperBench's own arms (cooperbench/
CooperBench at 63b9d44, MIT licence) re-made as plug-ins: their prompts
are CooperBench's word for word, and tests compare them with CooperBench's
own builders when its source is present (COOPERBENCH_DIR). Their tools
keep CooperBench's command names (coop-send, coop-task-claim and the rest,
with the same arguments and exit codes) and are also MCP tools. coop-send
and coop-broadcast behave as coop_msg.py's do in every arm that has them,
the sdlc arm included: a message to any name is queued, an inbox being
made for the name, so a misspelt peer is written to where nobody reads and
coop-agents is how an agent learns the names; an empty or missing text is
sent as ""; a missing recipient exits 2 in argparse's words; an unquoted
message of several words is a word too many (exit 2); coop-send says
"sent to <recipient>" on stderr and nothing on stdout, and coop-broadcast
says nothing; every received entry carries timestamp and timestamp_iso. A
run through coordbench can therefore be checked against the same arm run
through CooperBench's runner (experiment/run_arms.sh), which tells how
much of a result is the harness rather than the arm.

The team arm keeps four differences from CooperBench's team mode, each
deliberate and listed with its reason at the top of arms/team/arm.py. The
task list lives in the run's memory under the run's lock, so a claim is
atomic in fact, where CooperBench reads the owner and then writes it. A
request's --wait is capped at 300 seconds, where coop_task.py waits as
long as asked: the callers give up above that cap on purpose (Claude Code
after the 330-second timeout the arm's .mcp.json sets, cli.py after 600),
so an unbounded wait would be cut off by the client anyway; a wait the
client cancels takes no response, and one whose answer the client was not
there to read puts the response back. wait_for_message returns a message,
which CooperBench's never did at 63b9d44: the tool its team mode registers
for its CLI agents (team_harness/mcp_server.py) strips the #run:<id>
fragment from CB_TEAM_REDIS_URL and BLPOPs <agent>:inbox, while
coop_msg.py keeps the same fragment of COOP_REDIS_URL as a key prefix and
pushes to run:<id>:<agent>:inbox, so every call there timed out with the
no-message marker and left the inbox for coop-recv. coordbench's
wait_for_message carries CooperBench's description and schema word for
word but works: it hands over the oldest message, removes it from the
inbox, and holds the wait to the schema's range of 100 to 30000 ms, where
CooperBench clamps only the lower end. The mirror of the list under
/workspace/shared/tasks is written by the hub from its host, so it needs
the volume's host path (the run option shared_dir_host); it writes only
into a real directory named tasks, never through a symlink an agent
planted in its place, and not once the run has closed. Everything else an
agent sees is CooperBench's: a request is queued for whatever name it is
addressed to, a title or a body of several words wants its quotes and a
word too many exits 2, and an empty title or body is accepted.

## An arm is a Claude Code plugin

```
my-arm/
  .claude-plugin/plugin.json   {"name": "coordbench-my-arm", "version": "0.1.0", "description": "..."}
  .mcp.json                    {"mcpServers": {"coordbench": {"type": "http", "url": "${COORD_MCP_URL}",
                                "timeout": 330000, "headers": {"Authorization": "Bearer ${COORD_AGENT_TOKEN:-}"}}}}
  skills/coordbench-my-arm/SKILL.md   the protocol, in words
  arm.json                     {"entry": "arm.py:MyArm", "name": "my-arm", "description": "...",
                                "needs": [], "plugins": [], "mcp_servers": {}}
  arm.py                       the arm's code
```

The timeout, in milliseconds, is how long Claude Code lets one call to
this server run, and it is needed: without it Claude Code aborts any HTTP
MCP tool call after 60 seconds, which a tool that blocks by design
(wait_for_message, or the team arm's request, which waits up to 300
seconds) would outlast, so its answer would reach nobody. 330000 is past
the longest wait any built-in arm allows, and every built-in arm's
.mcp.json carries the same value.

Because the directory is a Claude Code plugin, an agent started with
`claude --plugin-dir my-arm` and COORD_MCP_URL in its environment has the
arm's tools over MCP and its skill, with nothing else to install. The
headers key carries the agent's own hub token, which a run the runner
creates wants on every request: Claude Code expands ${COORD_AGENT_TOKEN:-}
to the token, or to nothing for a run without tokens, which ignores the
header. Any other MCP client reaches the same tools at the same URL, or
through the stdio bridge for clients that only speak stdio:

```json
{"mcpServers": {"coordbench": {"command": "python3", "args": ["/opt/coordbench/bridge.py"],
                               "env": {"COORD_MCP_URL": "http://host.docker.internal:8765/mcp/RUN/agent1"}}}}
```

The bridge reads COORD_AGENT_TOKEN from its environment as well, when the
run wants one. Any shell reaches the tools as commands: `python3 cli.py
install /usr/local/bin` writes one wrapper per tool, under the tool's
alias (coop-send) and as coord-<tool>, and the wrappers send the same
token on every request.

.mcp.json and skills/ are optional. An arm with no tools has no .mcp.json,
since there is nothing for the MCP server to list, and an arm whose prompt
already says everything ships no skill. solo is both: its directory holds
.claude-plugin/plugin.json, arm.json and arm.py and nothing else, so a
Claude Code that loads the plugin sees what CooperBench's solo agent saw.

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
- `instructions(run, agent)`: text sent as the MCP server's instructions,
  which Claude Code puts into the agent's system prompt; "" by default,
  and every built-in arm leaves it so, because CooperBench's agents got
  none (its team server sends no instructions and its other modes have no
  server); the key is left out of initialize when it is empty.
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
wait_for_message, the schema helper and message metrics, and
marked_for_stderr, which marks a tool's input schema
("x-coordbench-prints": "stderr") so that its shell command prints the
tool's text on stderr, as coop-send does. The events log under --log-dir
is written best effort: a write the disk refuses is complained about once
per run on the hub's stderr, and the event stays in memory, where the
runner reads it.

A tool that blocks has two more things to do, both on the Call it is
given. Its wait predicate reads `call.cancelled`, a threading.Event the
hub sets when the client cancels the request (MCP's
notifications/cancelled); once it is set the tool takes nothing and
returns, since its answer would reach nobody. After it takes something
from run.state for the caller, it registers `call.on_lost(undo)`: the hub
runs the undo, under run.lock, if the reply to the call could not be
written because the connection dropped, so what was taken goes back for
the next call. kit.py's wait_for_message and the team arm's request do
both.

## Bringing in existing Claude extensions

An arm can bring any Claude Code plugin or MCP server along, so wiring
built for people (a memory server, a code-review plugin, a hook) can be
benchmarked as coordination:

- arm.json "plugins": plugin directories, copied into each agent's
  container and loaded with --plugin-dir; git URLs, cloned on the host and
  copied the same way; or plugin .zip URLs, handed to Claude Code as
  --plugin-url (nothing is copied for them);
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
  never written anywhere. The runner asks the hub for a token per agent;
  it reaches the container as COORD_AGENT_TOKEN by name too, in the docker
  client's environment for the one `docker run` that starts the container,
  from which claude, cli.py and bridge.py inherit it (.mcp.json sends it
  as the Authorization header; cli.py and bridge.py send it on every
  request). Each agent's time limit holds inside its container: claude
  runs under coreutils timeout, and a timed-out agent is also stopped
  through the pid its shell leaves in /tmp/coord-claude.pid, since the
  task images have no pkill.
- `session`: this Claude Code session's own subagents. The agent's
  repository is a host directory mounted into its container, commands run
  with docker exec, and the coordination commands are per-agent wrappers on
  the host. Each agent's token is a file named `token`, mode 0600, beside
  its wrappers, which read it with cat when they run, so neither a
  wrapper's text nor a process list shows it; the plan.json prepare writes
  holds the tokens too and has the same mode, and the plan prepare prints
  leaves them out. `run.py prepare` provisions the pair and prints a plan
  of prompts; the session spawns one subagent per prompt (the Workflow
  tool); `run.py finish` waits for each agent's marker, scores and tears
  down. A subagent leaves no stream on the host, so result.json records
  its tokens, steps and cost as null (unknown), never as zero, and
  experiment/report.py shows them as '-'.
- your own: `--driver path/to/driver.py:Class`, any class with start(job)
  and wait(handle) (drivers.py says what they take and return; the class
  attributes host_workspace, resumable, framework and measures_usage tell
  the runner what the driver wants, and a driver that does not set
  measures_usage = True has its usage recorded as null).

## Running

After experiment/setup.sh and experiment/prepare_task.sh for the task:

```
python3 experiment/coordbench/run.py run --arm team --repo pallets_click_task --task 2068 \
    --subset flash --driver claude-cli --name flash-team-coordbench
python3 experiment/report.py --run team-coordbench=flash-team-coordbench --run team-cooperbench=flash-team-cc
```

The logs take CooperBench's layout, so `cooperbench eval` and
experiment/report.py read them as they read the authors' runs. result.json
holds the fields CooperBench's coop.py and solo.py write (per agent the
status, cost, steps, the token counts, patch_lines and error; total_cost
and total_steps for the run) plus the arm, the driver and the hub's
metrics. A driver that cannot measure usage (drivers.py, measures_usage)
writes null, not 0, for an agent's cost, steps and token counts, and null
totals when no agent was measured; report.py takes a null as unknown,
leaves such a pair out of "Tokens per pair", and shows '-' when no pair of
the arm was measured. report.py reads a pair's agents from the "agents"
block that coordbench and CooperBench's coop and team runners write, or
from the one "agent" block CooperBench's solo runner writes, and sets a
pair an agent left in Error apart in both, so a coordbench solo row and
the authors' solo row are measured the same way. Once a pair's metrics
and events are fetched the runner releases the run (DELETE
/api/runs/{run}), so a long-lived hub forgets finished runs; a hub without
that route only keeps them, which the runner reports as a warning. A new
arm needs no change to anything but its own directory: `--arm
path/to/my-arm`, and the hub is started with that directory allowed.

## The hub, for other harnesses

`python3 hub.py --host 0.0.0.0 --port 8765 [--log-dir DIR] [--arm-dir DIR]`
serves:

- `POST /api/runs` {"arm", "features", "specs", "options", "run_id",
  "agent_tokens"} -> the run's agents, each with its mcp_url, rpc_url and
  prompt, and with "agent_tokens": true a bearer token of its own, which
  this answer alone carries; features must not repeat and are at most 16
  (a task pair is 2); `GET /api/runs/{run}`, `GET /api/runs/{run}/events`,
  `POST /api/runs/{run}/close` -> metrics; `DELETE /api/runs/{run}` ->
  {"run_id", "released": true}, which closes the run if it is still open
  and then forgets it, so a hub that serves many runs keeps none of the
  finished ones (the runner sends it once it holds the metrics and the
  events); `GET /api/health`;
- `POST /mcp/{run}/{agent}`: MCP over Streamable HTTP (2025-06-18; JSON
  answers, an Mcp-Session-Id from an initialize answered with a result; a
  notifications/cancelled on the session ends the tools/call it names
  without that call taking anything, and a reply the client was not there
  to read has what its calls took put back). A body the hub cannot read as
  a request, or an MCP-Protocol-Version it does not speak on anything but
  an initialize, is answered 400 with a JSON-RPC error that has no id;
  each request of a batch is answered on its own. The server instructions
  in initialize are the arm's (Arm.instructions), none by default;
- `GET` and `POST /rpc/{run}/{agent}`: the tools as plain JSON for shell
  commands.

A run created with "agent_tokens": true wants each agent's own token on
its /mcp (POST) and /rpc (GET and POST), as `Authorization: Bearer <token>`,
and answers 401 with {"error": ...} to anything else, after the Origin
check and before anything in the body is read; GET /api/runs/{run} never
shows a token. A run created without stays open to anyone who knows its
URLs.

Loading an arm runs its code in the hub, so the hub loads only built-in
arms and arms under its --arm-dir directories. With COORD_ADMIN_TOKEN in
its environment the /api routes want that bearer token, which the runner
holds and agents do not. In a run the runner creates, each agent is told
apart by a token of its own as well as by its URL: the runner asks for
agent_tokens, so an agent cannot speak as another, and nothing else on
the network (the hub binds to every address of its host so that
containers can reach it) can speak at all. A run another harness creates
without agent_tokens is told apart by URL alone, as CooperBench's Redis
told agents apart by key, and an agent that wanted to could speak as
another; the events log would show it.
