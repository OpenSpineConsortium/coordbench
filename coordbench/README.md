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
| coop-nocomm | coop's layout with messaging banned, CooperBench's `--no-messaging`: no tools and no word of a partner | nothing |
| coop-git | coop plus a shared git remote they push to and merge from | git_remote |
| team | a lead and members, a shared task list, typed requests, a scratchpad; its wait_for_message works, where CooperBench's did not (see below) | shared_volume |
| sdlc | sdlc-template's claim protocol: issues, exclusive claims, pull requests, a merge gate that answers "main moved" | git_remote |
| sdlc-watch | sdlc plus watching the peers' pull requests while working: early drafts, frequent pushes, and a tool that sets each peer's branch beside one's own (shared files, whether the two merge cleanly, lines both added) | git_remote |

solo, coop, coop-git, coop-nocomm and team are CooperBench's own arms
(cooperbench/CooperBench at 63b9d44, MIT licence) re-made as plug-ins:
their prompts are CooperBench's word for word but for one sentence that
coop, coop-git and team add naming the MCP tool behind each command, and
tests compare them with CooperBench's own builders when its source is
present (COOPERBENCH_DIR). Their tools keep CooperBench's command names
(coop-send, coop-task-claim and the rest, with the same arguments and exit
codes) and are also MCP tools, and the arm loads as a Claude Code plugin
with a skill. That surface, the run option tool_surface "mcp", is the
default. `--option tool_surface=bash` reproduces the surface CooperBench's
claude_code agents had instead, for coop, coop-git and team: the prompt
without the added sentence, byte for byte the authors' text; no plugin, so
no skill; and the coop-* commands in Bash, with, for team, the one MCP
server CooperBench registers, cooperbench-team, serving wait_for_message
alone. Arm.claude_surface(options) says what a driver hands Claude Code
for it (whether to load the plugin, which MCP servers), the hub's MCP
endpoint lists only that tool, and the metrics record tool_surface. The
sdlc arm, whose prompt names its MCP tools, offers "mcp" alone and the hub
refuses it the other. coop-send
and coop-broadcast behave as coop_msg.py's do in every arm that has them,
the sdlc arm included: a message to any name is queued, an inbox being
made for the name, so a misspelt peer is written to where nobody reads and
coop-agents is how an agent learns the names; an empty or missing text is
sent as ""; a missing recipient exits 2 in argparse's words; an unquoted
message of several words is a word too many (exit 2); coop-send says
"sent to <recipient>" on stderr and nothing on stdout, and coop-broadcast
says nothing; every received entry carries timestamp and timestamp_iso. A
run through coordbench can therefore be checked against the same arm run
through CooperBench's runner (bench/cooperbench/run_arms.sh), which tells how
much of a result is the harness rather than the arm.

The team arm keeps four differences from CooperBench's team mode, each
deliberate and listed with its reason at the top of arms/team/arm.py. The
task list lives in the run's memory under the run's lock, so a claim is
atomic in fact, where CooperBench reads the owner and then writes it. A
request's --wait is capped at 300 seconds, where coop_task.py waits as
long as asked: the callers give up above that cap on purpose (Claude Code
after the 330-second timeout the arm's .mcp.json sets, cli.py after 600),
so an unbounded wait would be cut off by the client anyway. A wait the
client cancels takes no response; a wait cancelled after it took its
response, or whose answer the client was not there to read, has the
response put back on the board by the hub, through the undo the request
tool registers, so it is there for the next wait. wait_for_message
returns a message, which CooperBench's never did at 63b9d44: the tool its
team mode registers for its CLI agents (team_harness/mcp_server.py)
strips the #run:<id> fragment from CB_TEAM_REDIS_URL and BLPOPs
<agent>:inbox, while coop_msg.py keeps the same fragment of
COOP_REDIS_URL as a key prefix and pushes to run:<id>:<agent>:inbox, so
every call there timed out with the no-message marker and left the inbox
for coop-recv. coordbench's wait_for_message carries CooperBench's
description and schema word for word (but for the empty "required" list
kit.schema adds) and works: it hands over the oldest message, removes it
from the inbox, and holds the wait between 100 ms and 300 s
(kit.LONGEST_WAIT_MS), where CooperBench clamps only the lower end. The
schema keeps CooperBench's advertised maximum of 30000, so an MCP client
that validates its arguments against the schema stops there, exactly as
with CooperBench's server; the shell commands and /rpc check nothing
against it, and a call from there may ask for up to 300 s, which is the
team arm's cap on a request's wait and sits under the 330 s the arms'
.mcp.json gives one call. The mirror of the list under
/workspace/shared/tasks is written by the hub from its host, so it needs
the volume's host path (the run option shared_dir_host); it writes only
into a real directory named tasks, never through a symlink an agent
planted in its place, and not when the run was already closed when the
list was read. A close that lands between that read and the writes still
gets one mirror, which is harmless: the runner removes the volume only
after the close has answered, and only the tasks leaf is ever made, so a
mirror after the removal lands nowhere. Everything else an agent sees is
CooperBench's: a request is queued for whatever name it is addressed to,
a title or a body of several words wants its quotes and a word too many
exits 2, and an empty title or body is accepted. A missing argument exits
2 with argparse's words, as coop_task.py does: the shell command sends a
required positional it has no word for as missing (an optional body is
still read from stdin), and the hub refuses it. A --wait that is not a
number is refused by the shell command before anything is sent; so are
the words true, false and null, which would otherwise be read as 1.0,
0.0 and no wait.

The sdlc arm benchmarks the claim protocol as it runs everywhere else
rather than a port of it. Its claims, pull requests, merges, statuses and
board are decided by sdlc_protocol.gate, the library the template's tools
and the GitHub App decide through as well, over that library's bare-git
world (sdlc_protocol.adapters.bare_git), under a policy in which the gate
itself merges a pull request once it is marked ready and a branch brings
a moved main in by merging it; the arm keeps its tools, its prompt, its
metrics and its words, and names its own commands where two of the
gate's refusals name none. The library is vendored at
coordbench/vendor/sdlc_protocol, and coordbench/vendor/VENDORED records
the copy: the version and the commit it was made from, then the sha256
of every file. The directory is inside the package, so the hub imports
it from there and install_coordbench copies it into every agent's
container with the rest. The copy is never edited here: a change to the
protocol is made in OpenSpineConsortium/sdlc-protocol, whose sync
workflow brings it here as the pull request sdlc-protocol/sync, and
scripts/check.sh fails on a copy that differs from its record.
coordbench/vendor/README.md says how the copy is refreshed, and
tests/test_coordbench_vendor.py holds it to the record and, on CI, which
clones sdlc-protocol, to the library's own files.

The sdlc-watch arm is the sdlc arm, loaded from its directory as coop-git
loads coop, plus one tool and one section of the prompt. The sdlc arm's
board sets each branch beside main only, so two drafts that change the
same lines, or each write the same helper, find out at the merge gate
after both are done. sdlc-watch adds peer_pull_requests (sdlc-peers),
which reads the shared remote and sets every peer's pull request beside
the caller's branch: the files each changes, the files both change and
the lines each touched there, whether the two branches merge cleanly as
they stand (git merge-tree), the lines both added, and whether the peer
pushed since the caller last looked. Each side's change is read from
where it left main and the other branch, so an agent that took its
peer's work by merging the peer's branch is not told that what it took
overlaps. The prompt asks for the habits that make the tool worth
calling: open the draft at the first commit, push after each step that
works, look after every push and before marking ready, and settle
overlap while both pull requests are drafts. Every look is a peer_check
event, and the metrics add how often each agent looked, when overlap
and a conflict were first seen, the most lines both added, and when
each draft was opened, beside the sdlc arm's merge_conflicts, so a run
says whether a conflict was seen before the gate met it.

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
run wants one. When the hub refuses a message it cannot read as a request
(an id that is null or a boolean, say) with a 400 carrying a JSON-RPC error
that names no id, the bridge writes that error, the hub's code and message,
to the stdio client under each id of the message, as the hub would have
answered on stdio; a 400 whose body is no JSON-RPC error reaches the client
as an internal error (-32603) quoting the status and the body, like any
other HTTP error. Any shell reaches the tools as commands: `python3 cli.py
install /usr/local/bin` writes one wrapper per tool, under the tool's alias
(coop-send) and as coord-<tool>, and the wrappers send the same token on
every request; `install --token-file PATH` writes PATH absolute into the
wrappers, so they read the token from any directory, and `install --debug`
writes wrappers that export COORD_TRACE=debug themselves. Both read
COORD_TRACE and COORD_TRACE_FILE from their environment, as "Running"
describes under the agent's side of a debug run; in prod they write
nothing. A command's
last positional word comes from stdin when it is missing and the tool's
schema does not require it (coop-send's text and coop-request's body, as
coop_msg.py and coop_task.py read theirs), so `coop-send agent2 < note.txt`
works; a required one left missing (coop-task-create's title,
coop-task-claim's task_id, coop-task-update's status) is sent as missing
and the hub refuses it with exit 2 in argparse's words, so the stdin at
/dev/null every command gets from the drivers never becomes a task titled
"". A word for an argument the schema types as a number is refused with
exit 2 and argparse's wording when it is true, false or null, as
coop_task.py's --wait (type=float) refuses them.

.mcp.json and skills/ are optional. An arm with no tools has no .mcp.json,
since there is nothing for the MCP server to list, and an arm whose prompt
already says everything ships no skill. solo is both: its directory holds
.claude-plugin/plugin.json, arm.json and arm.py and nothing else, so a
Claude Code that loads the plugin sees what CooperBench's solo agent saw.

## Starting a new arm

new_arm.py writes the directory above, complete and loadable, from a
name, so a new arm starts from a working directory rather than from the
prose that follows:

```
python3 coordbench/new_arm.py my-arm path/to/arms [--description TEXT]
python3 coordbench/new_arm.py --check path/to/arms/my-arm
```

The first command writes path/to/arms/my-arm/: .claude-plugin/plugin.json
(coordbench-my-arm, version 0.1.0, the description), the .mcp.json every
built-in arm carries, skills/coordbench-my-arm/SKILL.md naming every tool
and shell command, arm.json, and arm.py, a documented subclass of Arm that
gives every agent CooperBench's messaging (kit.messaging_tools()) and the
notice board of the example below (post_notice and read_board, board-post
and board-read in the shell), puts one paragraph about the board before
the submission block of the prompt, counts the notices in its metrics,
and carries a commented one-line stub for each override it does not make
(layout, setup, instructions, teardown, needs) with the sentence from
this file that says what the override is for. The name is a plugin name,
lowercase letters, digits and dashes starting with a letter; a directory
that exists is never written over; every file written is ASCII with LF.
The command ends with one line saying how to try the arm: --check, then
the hub with --arm-dir path/to/arms and POST /api/runs with the arm's
directory, or run.py run --arm path/to/arms/my-arm, which does both. The
second command is the smoke test: it loads the arm as the hub does,
builds a run of two agents in-process, lists the tools, pins a notice as
agent1 and reads the board as agent2, sends a message, closes the run and
prints the metrics; any step that fails ends it with exit 1 and the
exception's one line. examples/board/ is the generator's own output for
the name "board", kept in the tree as the worked example; a test
regenerates it and compares every byte, so the archetype and the example
cannot drift apart.

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
- `metrics(run)`: numbers from run.events (every tool call, framed by a
  "tool" event before its handler runs, with via, "mcp" or "shell", the
  transport it came by, and a "tool_done" event after it returns, the
  second with duration_ms, the handler's wall time, blocked_ms, the part
  of it spent waiting for another agent, is_error and cancelled; and
  every `run.emit(kind, agent, ...)`) and run.state. The default metrics
  count events by kind, so that count includes tool_done, and name the
  run's tool_surface. Both durations are milliseconds as floats rounded to
  three decimals, so a hub cost below a millisecond still shows.
- `artifacts(run)`: files the run leaves, {file name: a JSON value or
  text}, taken once when the run closes, before teardown, and answered
  beside the metrics; the team arm's are CooperBench's task_log.json and
  tasks.json. The hub adds git_log.txt itself for a run with a shared
  remote (below).
- `teardown(run)`: free what setup took, once, when the run closes.
- `needs`: "git_remote" (a bare repository, remote `team` in every agent's
  repository; run.options git_remote, git_remote_host and base_ref say
  where) and "shared_volume" (/workspace/shared in every container;
  run.options shared_dir and shared_dir_host). The hub refuses a run
  whose options do not provide what its arm needs.

The record. The hub's first event in every run, seq 0, is run_started:
the wall clock its t counts from (epoch, and iso in UTC), the arm, the
agents with their roles and features, and the tool surface, so epoch + t
puts any event at its absolute time. kit's messaging records each
delivery as a "message" event carrying the text (content), coop_msg.py's
timestamp and timestamp_iso, its own seq as message_id and the seq of
the send's or broadcast's "tool" event as send_id (the call's
`call.tool_seq`), so the deliveries of one broadcast share it; each
"receive" and "receive_lost" names the message_ids it handed over or put
back and the call (call_id). The team arm's request and response events
carry their body as content too, which CooperBench's task-log does not
keep. conversation.py, which imports nothing of coordbench, derives
CooperBench's conversation.json, each sender's <agent>_sent.jsonl and the
paper-era message store, conversation_store.json, from these events. The
text stays in the run's own record and events.jsonl, written as it is
sent, and is left off trace.py's stream: a field named content never
reaches a listener, the hub's --debug file included.

run.lock is a re-entrant condition that guards run.state; every change
under it, and every emit, wakes the tools waiting on it, so a blocking tool
is `run.wait_until(lambda: <condition on run.state>, timeout)`. kit.py
holds the blocks most arms share: CooperBench's messaging, the long-poll
wait_for_message, the schema helper and message metrics, and
marked_for_stderr, which marks a tool's input schema
("x-coordbench-prints": "stderr") so that its shell command prints the
tool's text on stderr, as coop-send does. The events log under --log-dir is
written best effort: the file is opened at the run's first event and held
open until the runner releases the run, and a write the disk refuses, or a
log directory that is not there when the first event comes, is complained
about once per run on the hub's stderr while the event stays in memory,
where the runner reads it; later writes are still tried.

A tool that blocks has three more things to do, all on the Call it is
given. Its wait predicate reads `call.cancelled`, a threading.Event the
hub sets when the client cancels the request (MCP's
notifications/cancelled); once it is set the tool takes nothing and
returns, since its answer would reach nobody. After it takes something
from run.state for the caller, it registers `call.on_lost(undo)`: the hub
runs the undo, under run.lock, when the reply to the call reaches nobody,
because the connection dropped before the reply was written or because
the call was cancelled after its take, so what was taken goes back for
the next call, once. Around each wait it calls `call.waited(seconds)`
with the time the wait took, so the "tool_done" event the hub emits when
the handler returns can say how much of the call's wall time
(duration_ms) was spent waiting for another agent (blocked_ms); the
difference is what the hub itself cost. kit.py's wait_for_message and the
team arm's request do all three.

A reader of events.jsonl tells the hub's time from the agent's by those
two framing events. For wait_for_message, the "tool_done" event's
blocked_ms is the time spent in the wait itself, so a wait that ran out
shows blocked_ms close to duration_ms, and a hub that was slow shows the
gap between them; for the team arm's request with a wait, blocked_ms is
the time spent in the wait for the peer's response, so duration_ms minus
blocked_ms is the hub's own cost of the call. A "receive_lost" event
follows a "receive" whose reply reached nobody (the hub could not write
it, or the call was cancelled after its take), for a drain (coop-recv) as
for a wait, with count the number of messages put back at the head of
the inbox in their order; the "receive" stays, so a reader subtracts the
one from the other rather than count the messages twice when the next
call takes them.

Every event and timing, the hub's, the runner's and an arm's, goes through
trace.py, one module that imports nothing else from coordbench, so it can
be copied alone into a container or another repository, and one stream with
one shape, so a reader adds logging in one line. `run.emit(kind, agent,
**fields)` is the run's own event: kept in run.events and events.jsonl as
before, and put on the stream under the run's id (the field names seq, t,
kind, agent and run are the run's; a kind adds none of them).
`trace.event("kind", run=run.id, agent=agent, ...)` records one event of
the stream alone; `with trace.span("rebase", run=run.id, agent=agent): ...`
around a block, or `@trace.span("rebase")` on a function, records two, the
start and `rebase_done` with duration_ms (a float, three decimals) and ok
(False when the block raised; the exception propagates), which the hub's
--debug log sees beside the run's events and the run's metrics do not.
`trace.debug(kind, **fields)` and `trace.debug_span(...)` are the same but
recorded in debug mode only, at one comparison's cost in prod. Fields are
JSON values the caller already has: trace computes nothing the caller did
not pass, so a field that costs to compute goes behind `if trace.level ==
"debug":`. A tool that waits through `run.wait_until(ready, timeout,
agent=call.agent)`, as kit.py's wait_for_message and the team arm's request
do, is recorded in debug as a "lock_wait" with waited_ms and whether it
woke or timed out. Listeners, registered with `trace.listen(callable)`
(which gives the callable back) and removed with `trace.unlisten`, receive
each event as a dict {"kind", "t", "ts", **fields}, t seconds since
trace.started (a monotonic reference) unless the caller passed its own, as
a run's record does, and ts epoch seconds, and must not raise: one that
raises is dropped after one complaint on the hub's stderr, so a bad sink
cannot break a tool call. `trace.JsonLines(path or stream)` is the
production listener, one line per event, flushed, appended to a file it
opens on the first event; `trace.Memory()` is a list that is also a
listener, for tests and for a run's in-memory events.

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

- `claude-cli`: headless Claude Code in each agent's container, as
  CooperBench's claude_code adapter runs it, the arm loaded with
  --plugin-dir on the default tool surface; `--option tool_surface=bash`
  loads no plugin and, for team, hands over only the cooperbench-team
  server through --mcp-config (Claude Code expands ${COORD_MCP_URL} and
  ${COORD_AGENT_TOKEN:-} there as in a plugin's .mcp.json).
  It needs CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) in the
  runner's environment, or COORDBENCH_CLAUDE_TOKEN in a cloud environment,
  which withholds the first name and which run.py copies to it; the token reaches the container by name only and is
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
  tool, with the model each agent's entry names); `run.py finish` waits
  for each agent's marker, scores and tears down. A subagent leaves no
  stream on the host, so result.json records its steps, duration_ms and
  api_duration_ms as null (unknown), never as zero. Its tokens come from
  its own transcript: the prompt asks the subagent to open its first
  reply with a run token, finish finds the transcript under Claude Code's
  projects directory that carries it, among those whose prompt names the
  agent's own prompt file (or, next, the one whose prompt names that file
  and that read no other run's token, for an agent that never wrote the
  token in a reply; last, one whose prompt a full disk dropped, by the
  token in its reply), copies it as `<agent>_session.jsonl`, sums each
  API message's usage once and estimates the cost at the model's price
  (cost_source "estimated from tokens"; null for a model without a
  price). Claude Code writes a
  message's final output count only on the line that carries its
  stop_reason, which most messages lack; for those the output count and
  the cost stay null, and output_tokens_lower_bound and cost_lower_bound
  give what the transcript does show. A subagent whose transcript
  is not found keeps null usage, and bench/cooperbench/report.py shows
  it as '-'. How it ended is in `<agent>_session_outcome.json`, and
  result.json marks the run's isolation "advisory", since the subagent's
  file tools reach the host. The agent's wall time is in result.json's
  timings, measured by the runner from prepare's start to the marker.
- your own: `--driver path/to/driver.py:Class`, any class with start(job)
  and wait(handle) (drivers.py says what they take and return; the class
  attributes host_workspace, resumable, framework, measures_usage,
  isolation and differences_from_cooperbench tell the runner what the
  driver wants and what config.json says of it, and a driver that does
  not set measures_usage = True has its usage recorded as null).

## Running

After bench/cooperbench/setup.sh and bench/cooperbench/prepare_task.sh for the task:

```
python3 coordbench/run.py run --arm team --repo pallets_click_task --task 2068 \
    --subset flash --driver claude-cli --name flash-team-coordbench
python3 bench/cooperbench/report.py --run team-coordbench=flash-team-coordbench --run team-cooperbench=flash-team-cc
```

`--subset` alone runs every pair of the subset across its tasks, as
CooperBench's runner does, and `--repo`/`--task` narrow it;
`--concurrency N` runs N pairs at once on one hub (CooperBench runs 20).
`--reference-merge clean` (or `conflicting`) keeps only the pairs whose gold
patches merge clean, or conflict, by the dataset's gold_conflict_report.json,
so a subset can be drawn by what the reference solutions do. The run first
checks that the Docker daemon answers `docker info` and refuses to start
otherwise, as `prepare` does. A pair's result.json carries `error_kind`:
`infrastructure` for a failure of Docker or the hub, `setup` for any other
failure before the agents ran, and `agent` when an agent ended in Error;
summary.json counts the infrastructure ones as `infrastructure_errors`.
`--agent-model agent2=MODEL` gives one agent its own model,
`--disallowed-tools WebFetch,WebSearch` keeps the agents to local tools
as the paper did, and `--dataset-dir` points the specs and the scorer at
another dataset (a 59bbf65 worktree for the paper's) while CooperBench's
own code scores.
`--agent-file FILE` runs every agent of the pair as a Claude Code
subagent file, the format Symphony keeps its agents in
(symphony-template's .claude/agents/ and docs/agents/AGENTS.md, where
`python3 tools/agents.py import` brings one in from anywhere), so a
team's own developer agent can be measured under any arm: on the
claude-cli driver only, as `claude --agents FILE --agent NAME`, with the
arm's prompt still the task and the run's model and disallowed tools
holding over the file's; result.json then names the agent and the
sha256 of its file under `agent_file` (coordbench/agent_file.py).

The logs take CooperBench's layout, so `cooperbench eval` and
bench/cooperbench/report.py read them as they read the authors' runs.
`<run>/config.json` is written when the run starts and `<run>/summary.json`
when it ends, with the keys and formulas of CooperBench's runner (the
summary's pass_rate is passed over passed and failed, the tally comes
from eval.json alone) and coordbench's beside them: the arm, driver,
options and limits, the CooperBench commit and the dataset's tree hash,
each task image's id and overlay label, the driver's departures from
CooperBench's adapter, and the Claude Code versions, models, tools and
MCP servers the agents reported. result.json holds the fields
CooperBench's coop.py, team.py and solo.py write, in their order (per
agent the status, cost, steps, the token counts, patch_lines and error;
total_cost, total_steps, messages_sent and log_dir for the run; team's
lead_agent and team_role; solo's one agent under "agent", its files named
solo_*), with error CooperBench's (an exception's message) and
coordbench's account of how an agent ended in end_reason, and then the
arm, the driver, agents_seconds, the isolation, the provenance, the
image and the hub's metrics. The team arm's pairs go under team/ with
the setting "team", as CooperBench's team runner writes them. Beside
result.json are each agent's agent<feature>_traj.json (or solo_traj.json)
in CooperBench's shape, the conversation files, and what the arm hands
back at close. A driver that cannot measure usage (drivers.py,
measures_usage) writes null, not 0, for an agent's cost, steps and token
counts, and null totals when no agent was measured; report.py takes a
null as unknown, leaves such a pair out of "Tokens per pair", and shows
'-' when no pair of the arm was measured.

Each agent's record also carries duration_ms and api_duration_ms, Claude
Code's own wall time for the agent and the part of it spent waiting on
the API, read from its result event; the session driver, which sees no
stream, writes null for both. result.json's duration_seconds runs from
the pair's plan to the agents' end, so it holds the run's creation on the
hub and the provisioning, where CooperBench's holds each agent's
container start, install and cleanup; agents_seconds is the agents' phase
alone, from the first start to the last end. result.json's "timings" is the harness's
own clock: one record per phase of the pair, with the phase, the agent it
was for (null for the run's own phases), its start and end as ISO 8601
and its seconds. The phases are hub (starting or connecting to the hub;
the run command does this once, so a --subset run repeats the same record
in every pair), run_create (POST /api/runs), git_remote, shared_volume,
provision (one per agent, from its docker run through its wrappers),
agent (one per agent, from the driver's start to its wait's return; for
the session driver, from prepare's start to the marker finish sees),
close (close and release on the hub), score (CooperBench's evaluation),
cascade (the merge cascade beside it in eval.json) and teardown. Only the phases that happened are recorded, and run and
finish end with one line such as `timings: provision 42 s, agents 1040 s
(agent1 1030 s, agent2 980 s), score 95 s, teardown 6.0 s`, where the
agents' figure is the span from the first start to the last end, since
they run at once.

report.py reads a pair's agents from the "agents" block that coordbench
and CooperBench's coop and team runners write, or from the one "agent"
block CooperBench's solo runner writes, and sets a pair an agent left in
Error apart in both, so a coordbench solo row and the authors' solo row
are measured the same way. Once a pair's metrics and events are fetched
the runner releases the run (DELETE /api/runs/{run}), so a long-lived hub
forgets finished runs; a hub without that route only keeps them, which
the runner reports as a warning. A hub that closed the run but could not
give its events leaves events.jsonl empty under a warning that says so,
keeps the metrics in result.json and is still asked to forget the run;
only a hub that could not close the run at all leaves the metrics empty
and the run unreleased. A new arm needs no change to anything but its own
directory: `--arm path/to/my-arm`, and the hub is started with that
directory allowed.

Reading the time. result.json's timings, each agent's duration_ms and
api_duration_ms, and the hub's tool_done events (every call's duration_ms,
its blocked_ms, whether it was an error or cancelled) are read into one
page by `python3 coordbench/timeline.py <run_dir>` (the pair's
directory, the one holding result.json; `--json` for the same as data).
Every duration but a phase's is taken on one monotonic clock, the one
trace.py's trace.now_ms reads, so the hub's tool_done figures, the runner's
docker and hub_call durations and a span's duration_ms are comparable to
the millisecond; a phase's seconds are the difference of its ISO 8601 start
and end, the wall clock, since those two stamps are what result.json keeps.
Infrastructure is the harness's cost: the phases around the agents, summed
as recorded, plus the hub's handling time, duration less blocked over every
call. The Agents table gives each agent its wall time, the seconds it was
blocked waiting on a peer, which is the arm's coordination cost rather than
a slow hub, its seconds in hub calls, its call, error and cancellation
counts, Claude Code's model time when the driver reported it, and what is
left, the agent's own shell commands, edits and thinking. Idle stretches
lists each agent's three longest gaps between one call's end and its next
call: a long gap with no coordination is the agent working or thinking
alone, where a long blocked time is the agent waiting. A run from before
these fields (the pilots) prints "not recorded" in their place and measures
its idle stretches from one call's start to the next; a run with no
events.jsonl still prints what result.json holds.

Debug mode. The trace level is read once from the environment variable
COORD_TRACE, "off", "prod" or "debug", prod by default. Off records nothing
and costs one check per event. Prod is the passive production level: one
check per event when nobody listens, which is the runner's and the hub's
own state, and otherwise one dict handed to each listener a deployment
registered (trace.JsonLines writes and flushes one line of it), with no
computation beyond what the caller passed, so it rides along in a
deployment. Debug, switched on with `--debug` on run.py's run, prepare and
finish (run and prepare pass it on to the hub they start; hub.py takes the
same flag for a hub started by hand), or with COORD_TRACE=debug in the
environment, and never a default, records everything the playground needs
to find where time goes; it adds events and never changes the ones
result.json and events.jsonl hold today (tool, tool_done with duration_ms
and blocked_ms, the timings with phase, agent, started, ended and seconds),
so a reader of a debug run reads the same fields plus more kinds. The flag
reaches the agents' side too: every agent's container starts with
COORD_TRACE=debug, which each command run in it inherits, and the session
driver's wrappers on the host are written with `install --debug`, since
the shell that runs them is the session's, not one the runner starts; so
the agents' shell commands and their stdio bridge record their timings,
as the paragraph on the agent's side below says. In prod the runner gives
the agents no COORD_TRACE at all.

Tracing. The timings are also events on trace.py's one stream as they
happen: a `phase` event when a phase begins (with `phase` and `agent`) and
a `phase_done` when it ends (with `duration_ms`, `ok`, `phase` and
`agent`), one for every record result.json gets, the agents' phase among
them though prepare records its start and finish its end. A deployment (the
App, an Actions job) attaches its own sink with `run.listen(callable)`,
which is trace.listen for everything the runner emits and gives the
callable back; `run.unlisten` takes it off. In prod that is the phases and
nothing else: no file is written for the trace, and each event the runner
could emit costs one comparison, with a clock read before a docker command
or a hub call. `--debug` (or `COORD_TRACE=debug` in the environment) is the
playground's mode: it adds every docker command (`docker`: `subcommand`,
`argument`, the thing it acted on, `exit_code`, null for one that timed
out, `duration_ms` and `output_bytes`; never the command line), every hub
call (`hub_call`: `method`, `path`, `status`, null when nothing answered,
`duration_ms`), every file the runner or a driver writes (`wrote`: `file`
and `bytes`, never the text) and the session driver's every look for its
marker (`marker_poll`: `agent`, `found`), and the runner writes every event
of the process to `<run_dir>/trace.jsonl`, one JSON object per line, which
finish appends to after prepare. The hub phase of a `run` command comes
before any run directory exists, so it reaches the listeners but no
trace.jsonl. result.json, events.jsonl and the summary line are what they
were: debug adds events and changes none.

The agent's side of a debug run. cli.py and bridge.py read COORD_TRACE too,
from the environment the agent's commands and its MCP server run in. With
COORD_TRACE=debug every request a shell command makes to /rpc is an
"rpc_call" span (method, path, tool and request_bytes as it starts; status,
response_bytes, duration_ms and ok on rpc_call_done), every alias-run one
"command" event (command, tool, exit_code, duration_ms), every POST the
stdio bridge makes a "bridge_post" span (ids, request_bytes; status,
response_bytes, duration_ms and ok, a streamed answer's bytes counted as
they arrive) and every session the bridge reopens after a 404 a
"bridge_reinit" event (session, reopened). They are written as JSON lines
to the process's stderr, the agent's own stream, so a command's timing
shows beside its output, or appended to the file COORD_TRACE_FILE names
when it is set; a message's text, a tool's arguments and the agent's token
are never in them. In prod, the default, the two files register no listener
and write nothing, and their stdout and stderr are what they were. Both
load trace.py from beside themselves when the directory was copied whole,
never the standard library's module of that name, and a process that
already imported coordbench's trace shares it; a copy of cli.py or
bridge.py alone records nothing and works as before.

## After a run

What a pair leaves is read by four tools, none of which calls a model
unless the owner asks it to:

- The merge cascade, coordbench/cascade.py, runs as part of scoring, for
  every merged pair CooperBench could evaluate, and adds a "cascade"
  block to eval.json beside CooperBench's own keys, which it never
  changes: the paper's naive, union and learned-resolver merges, each
  agent's patch alone against both suites, and both verdicts, 63b9d44's
  (verdict_63b9d44, CooperBench's both_passed) and the paper era's
  (verdict_paper_era), never mixed. It runs in a sandbox of its own
  through CooperBench's backend and merge helpers, from the dataset the
  scorer was handed, and writes merge_naive.diff, merge_union.diff and
  merge_llm.diff beside eval.json. The learned resolver runs only when
  COORDBENCH_RESOLVER_URL names an OpenAI-compatible API base
  (COORDBENCH_RESOLVER_MODEL picks the model, COORDBENCH_RESOLVER_KEY the
  key, which no file holds); otherwise the block says it did not run. It
  costs a sandbox and at least four test runs per pair.
- export.py writes the pair's trajectory.json.gz once the pair is
  scored, and `python3 coordbench/export.py <pair or run dir>... --site
  OUT` lays runs out as CooperBench's website does, so its own viewer
  opens them; `python3 coordbench/compare.py <pair dir or .json.gz>...
  -o report.html` puts runs and CooperBench's published trajectories side
  by side in one offline page (coordbench/viewer/README.md).
- judge.py labels failure modes as the paper does, run on purpose:
  `heuristics` (free, no model), `commerrors` (App F), `symptoms`,
  `speechacts` and `emergent`, with `--backend claude-cli` (the default,
  the owner's subscription through COORDBENCH_CLAUDE_TOKEN or
  CLAUDE_CODE_OAUTH_TOKEN, never ANTHROPIC_API_KEY) or
  `--backend openai-compatible` (COORDBENCH_JUDGE_URL and
  COORDBENCH_JUDGE_KEY); --dry-run says what would be sent. Each pair
  gets a judge.json recording, per section, the backend, model and
  prompt variant; the prompts are under coordbench/prompts/.
  annotate.py draws the seeded sample of failed pairs for the human
  cause labels (`causes`), tallies them and writes each into judge.json
  (`tally --apply`), and measures the symptom judge against human labels
  (`validate`, `agreement`).
- bench/cooperbench/report.py reads all of it, old runs included, where a
  file a run did not write reads "not recorded".

## The hub, for other harnesses

`python3 hub.py --host 0.0.0.0 --port 8765 [--log-dir DIR] [--arm-dir DIR] [--debug]`
serves:

- `POST /api/runs` {"arm", "features", "specs", "options", "run_id",
  "agent_tokens"} -> the run's agents, each with its mcp_url, rpc_url and
  prompt, and with "agent_tokens": true a bearer token of its own, which
  this answer alone carries; features must not repeat and are at most 16
  (a task pair is 2); `GET /api/runs/{run}`, `GET /api/runs/{run}/events`,
  `POST /api/runs/{run}/close` -> {"metrics", "artifacts"}, the second the
  files the close that closed the run took before the arm's teardown:
  the arm's own (Arm.artifacts) and, for a run whose options name a
  shared remote, git_log.txt, `git log --all --reflog --stat` of it less
  the history below base_ref: read in place, reflog included, from
  git_remote_dir, the bare repository's directory on the hub's host, when
  the options give one, and otherwise fetched from git_remote_host, 500
  commits deep and with no reflog, which git's protocol does not send;
  `DELETE /api/runs/{run}` ->
  {"run_id", "released": true}, which closes the run if it is still open
  and then forgets it, so a hub that serves many runs keeps none of the
  finished ones (the runner sends it once it holds the metrics and the
  events); `GET /api/health`;
- `POST /mcp/{run}/{agent}`: MCP over Streamable HTTP (2025-06-18; JSON
  answers, an Mcp-Session-Id from an initialize answered with
  initialize's own result under its own id; a notifications/cancelled on
  the session ends the tools/call it names without that call taking
  anything, or with what it had already taken put back, and a reply the
  client was not there to read has what its calls took put back). A body
  the hub cannot read as a request (not JSON, nested too deep to parse, an
  object that is neither a request nor a response, an id that is not a
  string or an integer, an empty batch), or an MCP-Protocol-Version it
  does not speak on anything but an initialize, is answered 400 with a
  JSON-RPC error that has no id; each request of a batch is answered on
  its own. A tools/call whose arguments are not an object, or name
  something the tool's inputSchema does not list (when the schema lists
  properties and does not set additionalProperties), is refused -32602
  before anything runs, the second naming the stray key and the names the
  tool takes; over /rpc the same refusal is a 400 whose reason the shell
  command exits 2 with, so an agent that types timeout=180 for timeout_ms
  hears so instead of waiting the default. The server instructions in
  initialize are the arm's (Arm.instructions), none by default;
- `GET` and `POST /rpc/{run}/{agent}`: the tools as plain JSON for shell
  commands.

In prod (the default, COORD_TRACE=prod) the hub keeps each run's events and
records nothing of its own: an event outside a run costs one check. With
--debug, or COORD_TRACE=debug in its environment, the hub says so once on
stderr and writes every event of the stream, its own and every run's with
the run's id, to <log_dir>/hub-trace.jsonl with --log-dir and to stderr
without: "http_request" per request (method, path, status, request_bytes,
response_bytes, duration_ms, thread), "lock_wait" per Run.wait_until
(agent, waited_ms, woke), "tool_arguments" per tool call (the size in bytes
of its arguments, never their content), "session_opened" per MCP session
and "cancel" per notifications/cancelled (request_id, found). Debug is
never on by default. COORD_TRACE=off silences the stream; a run's events
and events.jsonl are kept at every level, since they are the hub's data,
not its logging.

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
