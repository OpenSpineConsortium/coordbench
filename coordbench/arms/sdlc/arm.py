"""The sdlc arm: sdlc-template's claim protocol as a coordination arm.

The template's way of working (docs/agents/PROTOCOL.md in
OpenSpineConsortium/sdlc-template, with the merge habits of its
docs/agents/TEAM.md) is one more way of wiring agents together,
so it can be benchmarked on CooperBench's tasks beside CooperBench's own
arms. Each piece of the protocol becomes a tool the hub serves:

- The queue of issues labelled todo is one issue per feature, numbered by
  the feature and titled by its spec's first heading line, and each agent
  is given its own feature's issue.
- A claim is exclusive and first come, first served. On GitHub that is
  the first push of claude/<issue>, which GitHub refuses a second time;
  here claim_issue checks and records the claim under the run's lock, so
  two agents racing for one issue get the same answer GitHub would give.
  An agent claims only its own issue unless the run option "open_queue"
  is true, as team mode's members take only the tasks the lead gave them.
- The work happens in the open on branch claude/<issue>, pushed to the
  shared remote `team`, with a pull request opened at once
  (open_pull_request) and marked ready when done (mark_ready), which
  releases the claim as deleting the claim file does.
- The owner's merge, or team mode's auto-merge on a green check, is the
  merge gate: it merges the branch into main on the shared remote only
  when it merges cleanly with the current main. The bench's hidden tests
  cannot run at the gate, so merging cleanly is the whole check. When it
  does not merge, main stays as it was and the answer is the board's
  "main moved", put the way TEAM.md has members answer it: merge main in,
  never rebase, so nobody force-pushes.
- The board (board) and one pull request's standing (pull_request_status)
  are read from the run and from the remote itself, so "behind" and
  "conflicting" mean what they mean on GitHub.
- The comments on issues and pull requests are kit's messaging tools,
  with the long poll (wait_for_message), since an agent whose pull request
  waits on a peer's has nothing better to do than wait for its comment.

Labels beyond the four states, blockers, the 24-hour release, the lead and
the template's scripts/check.sh are left out: in a run of two or three
agents on one task pair there is nothing for them to act on.

The protocol's decisions are not this file's. The claim, the pull request,
mark ready and its merge, a pull request's status and the board are
sdlc_protocol.gate's, the one copy of the rules that the template's tools
and the GitHub App decide through as well, so the arm benchmarks the
protocol as it runs there rather than a port of it. The library is
vendored under coordbench/vendor, beside coordbench's own modules, where
the hub imports it and where install_coordbench copies it into each
container with the rest of the directory; VENDORED there names its version
and commit. The gate decides under POLICY below, and over the world of
sdlc_protocol.adapters.bare_git: the real repository named by the run
option "git_remote_host" (the shared remote as the hub's host reaches it),
reached through one scratch clone per run in a temporary directory, with
git run through subprocess with a timeout, every git operation serialised
by the world's own lock, and never a force-push, so main only ever moves
forward. This file keeps what is the arm's own: the tools and their words,
the prompt, the issues' titles, the events the metrics count and where
the queue and the world live in run.state.

The world's lock is always taken before the run's lock and never while
holding it, which is what keeps the hub's metrics (read under the run's
lock) from deadlocking against a merge in progress. Each decision is
taken in the gate's parts, the steps on the queue under the run's lock and
the steps that ask the world outside it, so the run's lock is never held
while git runs. When the run closes, teardown frees the clone and shuts
the world, so a late call is refused rather than cloning again.

A refusal's shell command exits with a code that says what kind of
refusal it is (the gate's EXIT_ constants, listed for the agents in
SKILL.md), so a script can tell losing a race from main having moved.
"""

import functools
import re
import sys
from pathlib import Path

import kit
from arm import AgentSpec, Arm, SUBMISSION_BLOCK, Tool, ToolError

# The vendored copy is put first on the path, so the version VENDORED
# names is the one a run decides with, even where another sdlc_protocol
# is installed: a benchmark's result must not change with the host.
VENDOR_DIR = Path(kit.__file__).resolve().parent / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

from sdlc_protocol import gate  # noqa: E402
from sdlc_protocol.adapters import bare_git  # noqa: E402

# The same rule the coop arms put between a prompt's sections, so the
# prompts of every arm read alike.
SECTION_RULE = "\n\n---\n\n"

# How the gate behaves in this arm. The gate itself merges when a pull
# request is marked ready, standing in for the owner's merge; a branch
# brings a moved main in by merging it, as TEAM.md has members do, so
# nobody force-pushes; and there is no required check, since the bench's
# hidden tests cannot run at the gate. Every agent's clone calls the shared
# remote `team`, which is the name the gate's answers use.
POLICY = gate.Policy(bring_main_in=gate.MERGE, who_merges=gate.GATE, required_check="", remote="team")
MAIN = POLICY.default_branch

# The board's own words for a pull request main has moved past, as the
# gate words them under this policy; the prompt quotes them.
MAIN_MOVED = gate.main_moved_instruction(POLICY)

QUEUE_KEY, GATE_KEY = "sdlc_queue", "sdlc_gate"

# A spec's title is its first heading line: a markdown heading, or a line
# that opens in bold, as CooperBench's specs open ("**Title**: ..."). The
# label such a line starts with ("Title:", "Feature 4:") is the spec's
# form rather than its name, so the board leaves it out.
HEADING_LINE = re.compile(r"\s*(#{1,6}\s|\*\*)")
HEADING_LABEL = re.compile(r"^(title|feature(\s+\d+)?)\s*:\s*", re.IGNORECASE)
# Long enough for every title in CooperBench's dataset; a bold paragraph
# taken for a heading is cut, so it cannot swamp the board.
TITLE_LIMIT = 120

ISSUE_ARGUMENT = {"type": "integer", "description": "the issue's number, which is its feature's number"}

# Two of the gate's refusals say what to do next in words that know no
# tools. The arm names its own command there, as its answers did before the
# gate moved to the library, so the move changed no word an agent reads and
# a run from before it compares with one from after.
COMMAND_HINTS = (
    (re.compile(r"(on #([0-9]+) opens its pull request, and nobody has claimed it; claim it first)$"),
     r"\1 with sdlc-claim \2"),
    (re.compile(r"^(#([0-9]+) has no pull request; open it) (once claude/)"),
     r"\1 with open_pull_request (sdlc-pr \2) \3"),
)

# What each tool does, in a few words, for the table in the prompt; a tool
# missing here is described by the first sentence of its description.
SUMMARIES = {
    "board": "the issues, the claims, the pull requests and how each branch stands to main",
    "claim_issue": "claim an issue; exclusive, first come, first served",
    "open_pull_request": "open the draft pull request from `claude/<issue>` to main, once the branch is pushed",
    "pull_request_status": "how one pull request stands, and whether it merges cleanly with main now",
    "mark_ready": "mark the pull request ready: releases the claim and asks the merge gate to merge it",
    "send_message": "comment to one peer, by agent id",
    "broadcast": "comment to every other agent",
    "receive_messages": "drain your inbox; a JSON list, oldest first",
    "peek_messages": "how many unread messages you have",
    "list_agents": "every agent id in the run",
    "wait_for_message": "wait for the next message instead of polling",
}


def issue_title(spec, number):
    """The issue's title: its spec's first heading line, or Feature N when
    the spec has none, as when the runner gave no spec for it."""
    for line in (spec or "").splitlines():
        if HEADING_LINE.match(line):
            title = heading_text(line)
            if title:
                return title
    return f"Feature {number}"


def heading_text(line):
    """A heading line without its markdown and its label."""
    text = re.sub(r"^#{1,6}\s+|\s+#+$", "", line.strip())
    text = text.replace("**", "").strip("* ")
    text = " ".join(HEADING_LABEL.sub("", text).split())
    return text if len(text) <= TITLE_LIMIT else text[:TITLE_LIMIT - 3].rstrip() + "..."


# Where the gate's queue and world live, and how its refusals reach the agent.

def queue_of(run):
    return run.state[QUEUE_KEY]


def world_of(run):
    world = run.state.get(GATE_KEY)
    if world is None:
        raise ToolError("this run has no git_remote_host, so the merge gate cannot reach the shared "
                        "remote; tell the operator", gate.EXIT_GATE_FAILED)
    return world


def refusals_as_tool_errors(handler):
    """The gate refuses with a GateError that carries its exit code; the hub
    answers a ToolError, so the refusal becomes one with the gate's text, in
    the arm's words, and its code, and the shell command exits as the gate's
    command line would."""
    @functools.wraps(handler)
    def answer(call):
        try:
            return handler(call)
        except gate.GateError as refusal:
            raise ToolError(in_the_arms_words(str(refusal)), refusal.exit_code) from refusal
    return answer


def in_the_arms_words(text):
    """The gate's text with the arm's commands named where it says what to
    do next (COMMAND_HINTS)."""
    for gates, arms in COMMAND_HINTS:
        text = gates.sub(arms, text)
    return text


def issue_number(call):
    """The issue argument as the gate reads one: "#3" and "3" are 3, since
    an agent may write an issue the way the board does."""
    return gate.issue_number(call.arg("issue", required=True))


def standings(run, branches):
    """main's commit and each branch's standing, read live from the remote
    in one fetch; none at all when the run has no remote or git fails."""
    world = run.state.get(GATE_KEY)
    return world.standings(branches) if world is not None else ("", {})


# The tools.

@refusals_as_tool_errors
def claim_issue(call):
    run, me = call.run, call.agent
    number = issue_number(call)
    with run.lock:
        gate.claim(POLICY, queue_of(run), number, me)
    run.emit("claim", me, issue=number)
    branch = POLICY.branch_of(number)
    return (f"claimed #{number}: it is yours until your pull request is marked ready. Next: start "
            f"{branch} from the base ref {queue_of(run).base_ref}, commit, push it "
            f"(git push -u team {branch}), and open the pull request with sdlc-pr {number}.")


@refusals_as_tool_errors
def open_pull_request(call):
    """Checked again once the remote has answered, since the run's lock was
    let go while it did and another call may have raced this one."""
    run, me = call.run, call.agent
    number = issue_number(call)
    with run.lock:
        gate.can_open_pull(POLICY, queue_of(run), number, me)
    gate.require_pushed(POLICY, world_of(run), number)
    with run.lock:
        gate.add_pull(POLICY, queue_of(run), number, me, call.arg("title"))
    run.emit("pr_opened", me, issue=number)
    return (f"opened the pull request for #{number} from {POLICY.branch_of(number)} to {MAIN}, as a draft. "
            f"Push often; when the feature is done, mark it ready with sdlc-ready {number}.")


@refusals_as_tool_errors
def mark_ready(call):
    """gate.mark_ready taken in its parts, so the events go out between
    them. The world is held from first to last: the decision sees one
    fetch of the remote, and what the merge did is in the queue before the
    next call reaches the gate."""
    run, me = call.run, call.agent
    number = issue_number(call)
    world = world_of(run)
    with world:
        with run.lock:
            pull = gate.pull_to_ready(POLICY, queue_of(run), number, me)
        attempt = gate.prepare(POLICY, world, number)
        gate.require_green(POLICY, world, attempt.branch)
        with run.lock:
            gate.begin_attempt(queue_of(run), pull)
        run.emit("ready", me, issue=number)
        run.emit("merge_attempt", me, issue=number, branch_commit=attempt.tip, main_before=attempt.main)
        merge = gate.attempt_merge(world, attempt)
        with run.lock:
            outcome = gate.record_outcome(POLICY, queue_of(run), attempt, merge)
        return answer_merge(run, me, outcome)


def answer_merge(run, me, outcome):
    """The events for what the merge came to, and the agent's answer: the
    gate's text with where to build the patch from once it merged, or a
    refusal carrying the gate's exit code."""
    number = outcome.issue
    if outcome.merged:
        run.emit("merge", me, issue=number, commit=outcome.main)
        return f"{outcome.text} {patch_hint(queue_of(run).base_ref)}"
    if outcome.result == gate.MAIN_MOVED:
        kind = "merge_conflict" if outcome.conflicts else "merge_refused"
        run.emit(kind, me, issue=number, files=list(outcome.conflicts), reason=outcome.reason)
    else:
        run.emit("merge_refused", me, issue=number, reason=outcome.reason)
    raise ToolError(outcome.text, outcome.exit_code)


def patch_hint(base):
    return (f"Build your patch from the integrated main: git fetch team && "
            f"git diff {base} {POLICY.upstream} > patch.txt")


@refusals_as_tool_errors
def pull_request_status(call):
    run = call.run
    number = issue_number(call)
    with run.lock:
        unopened = gate.status_without_pull(queue_of(run), number)
    if unopened:
        return unopened
    branch = POLICY.branch_of(number)
    main, live = standings(run, [branch])
    with run.lock:
        return gate.status_text(POLICY, queue_of(run).pulls[number], live.get(branch), main)


@refusals_as_tool_errors
def board(call):
    run = call.run
    with run.lock:
        branches = gate.open_branches(POLICY, queue_of(run))
    main, live = standings(run, branches)
    with run.lock:
        return gate.board_text(POLICY, queue_of(run), live, main)


def sdlc_tools():
    issue_only = kit.schema(["issue"], issue=ISSUE_ARGUMENT)
    return [
        Tool("board", "The board: every issue (one per feature) with its label, the claims held, the "
             "pull requests with their states, and how each open branch stands to main right now.",
             board, alias="sdlc-board"),
        Tool("claim_issue", "Claim an issue before working on it. A claim is exclusive, first come, first "
             "served: it succeeds once, and a claim of an issue another agent holds is refused. Unless "
             "the run's queue is open, each agent claims only the issue it was given.",
             claim_issue, issue_only, alias="sdlc-claim", argv=("issue",)),
        Tool("open_pull_request", "Open the draft pull request from branch claude/<issue> to main, once that "
             "branch is pushed to team. Only the agent holding the claim opens it; one issue, one pull "
             "request.", open_pull_request,
             kit.schema(["issue"], issue=ISSUE_ARGUMENT, title=kit.TEXT), alias="sdlc-pr", argv=("issue",)),
        Tool("mark_ready", "Mark your pull request ready: this releases your claim and asks the merge gate "
             "to merge claude/<issue> into main on team. The gate merges only when the branch merges "
             "cleanly with the current main; otherwise it leaves main as it was and answers 'main moved', "
             "and you merge team/main into your branch, resolve the conflicts, push, and call mark_ready "
             "again.", mark_ready, issue_only, alias="sdlc-ready", argv=("issue",)),
        Tool("pull_request_status", "How one pull request stands: draft, ready, main moved or merged, and "
             "whether its branch merges cleanly with main right now.",
             pull_request_status, issue_only, alias="sdlc-status", argv=("issue",)),
    ]


# The prompt.

PROTOCOL_BLOCK = """## The claim protocol

You are **{agent}**, one of the agents on this codebase: {everyone}. The
work is a queue of issues, one per feature and numbered by it: {issues}.
Your issue is **#{issue}**, the feature above. {queue_rule}

You work the way this project's protocol has every account work: pick an
issue from the board, claim it, do the work in the open on its own branch
and pull request, and finish by marking the pull request ready, which asks
the merge gate to merge it. One issue, one branch `claude/<issue>`, one pull request.

Each tool below is a tool of the MCP server `coordbench` and also a shell
command; both act on the same board and the same inboxes.

{tool_table}

### Step by step

1. Pick: read the board (`sdlc-board`): the issues, the claims, the pull
   requests and how each branch stands to main.
2. Claim: `sdlc-claim {issue}`. A claim is exclusive and first come, first served:
   it succeeds once, and a claim of an issue another agent holds is
   refused, in which case you pick another.
3. Branch: start `claude/{issue}` from the base ref and push it to `team`
   (Git set-up, below).
4. Work in the open: once the branch is pushed, open the pull request at
   once with `sdlc-pr {issue}`; it stays a draft until you mark it ready.
   Push often, so your peers can fetch your branch and build against it.
   Messages are this project's comments: tell your peers which files you
   will touch, read what they send, and agree on who owns a file two
   features both need.
5. Finish: commit, push, and mark the pull request ready with
   `sdlc-ready {issue}`. That releases your claim and asks for the merge. The
   merge gate merges `claude/{issue}` into `main` on `team` only when it
   merges cleanly with the current main, pushes main, and answers with the
   merge commit.
6. Main moved: when another pull request landed first and yours no longer
   merges cleanly with main, the gate leaves main as it was and answers "{main_moved}".
   Do exactly that, then call `sdlc-ready {issue}` again:

```bash
cd /workspace/repo
git fetch team
git merge team/main          # resolve the conflicts so both features work
git add <the files you resolved>
git commit --no-edit
git push team claude/{issue}
```

`sdlc-status {issue}` tells you beforehand whether your branch still merges
cleanly with main.

Rules that hold everywhere: one issue, one branch `claude/<issue>`, one
pull request. Never push to `main`, which only the merge gate moves, or to
another agent's branch, and never force-push: you bring main in by
merging it, never by rebasing.

### Git set-up

In `/workspace/repo` the remote `team` is the shared repository{remote},
and `team/main` is its default branch. The run began at the base ref
`{base}`, and only the merge gate moves `team/main` past it. Start your
branch from the base ref and publish it:

```bash
cd /workspace/repo
git fetch team
git checkout -b claude/{issue} {base}
# make your change, then commit only the source files it needs
git add <files>
git commit -m "<what the change does>"
git push -u team claude/{issue}
```

Never commit patch.txt: it is your submission, not part of the feature.

### Submitting

Once your pull request has merged, write patch.txt from the integrated
main, so every agent submits the whole team's work:

```bash
cd /workspace/repo
git fetch team
git diff {base} team/main > patch.txt
```

If your pull request cannot merge before you stop, submit your own branch
instead: commit your work, then run `git diff {base} HEAD > patch.txt`."""

CLOSED_QUEUE_RULE = ("In this run each agent claims only the issue it was given, so the board is for "
                     "seeing where your peers stand.")
OPEN_QUEUE_RULE = ("The queue is open: you may claim any issue the board shows unclaimed, yours or "
                   "another's, first come, first served.")


def shell_usage(tool):
    """The command an agent types, as coordbench's cli.py prints it."""
    name = tool.alias or f"coord-{tool.name}"
    if tool.argv:
        words = [f"<{word}>" for word in tool.argv]
    else:
        words = [f"[{key}=...]" for key in tool.input_schema.get("properties", {})]
    return " ".join([name, *words])


def tool_row(tool):
    parameters = ", ".join(tool.input_schema.get("properties", {}))
    mcp_name = f"`{tool.name}`" + (f" ({parameters})" if parameters else "")
    summary = SUMMARIES.get(tool.name) or tool.description.split(". ")[0]
    return f"| {mcp_name} | `{shell_usage(tool)}` | {summary} |"


def tool_table(tools):
    rows = [tool_row(tool) for tool in tools]
    return "\n".join(["| MCP tool | Shell command | What it does |", "| --- | --- | --- |", *rows])


def protocol_block(run, agent, tools):
    queue = queue_of(run)
    mine = queue.given_to(agent)
    remote = run.options.get("git_remote")
    return PROTOCOL_BLOCK.format(
        agent=agent,
        everyone=", ".join(run.agents),
        issues=", ".join(f"#{i.number} given to {i.given_to}" for i in queue.issues.values()),
        issue=mine[0] if mine else "<issue>",
        queue_rule=OPEN_QUEUE_RULE if queue.open_queue else CLOSED_QUEUE_RULE,
        tool_table=tool_table(tools),
        main_moved=MAIN_MOVED,
        remote=f" (`{remote}`)" if remote else "",
        base=queue.base_ref,
    )


def resolve_base_ref(options):
    """The commit every branch starts from. The runner always sets
    base_ref; without it the remote's main at the start stands in, and
    without a remote, team/main, which is right until the first merge."""
    if options.get("base_ref"):
        return str(options["base_ref"])
    if options.get("git_remote_host"):
        main = bare_git.remote_main(str(options["git_remote_host"]), MAIN)
        if main:
            return main
    return POLICY.upstream


def count_events(run, kind):
    return sum(1 for event in run.events if event["kind"] == kind)


def tool_call_counts(run):
    """How often each tool was called, from the hub's "tool" events, as the
    coop arms count them."""
    counts = {}
    for event in run.events:
        if event["kind"] == "tool":
            counts[event["tool"]] = counts.get(event["tool"], 0) + 1
    return counts


def first_merge_seconds(run):
    merges = [event["t"] for event in run.events if event["kind"] == "merge"]
    return min(merges) if merges else None


class SdlcArm(Arm):
    name = "sdlc"
    description = ("The claim protocol of OpenSpineConsortium/sdlc-template: one agent per feature, each "
                   "claiming its issue, working on claude/<issue> on a shared remote and landing it by pull "
                   "request through a merge gate.")
    needs = ("git_remote",)

    def layout(self, features):
        # Numbered as the coop arms number them, lowest feature first, so a
        # task pair has the same agent1 under every arm.
        return [AgentSpec(f"agent{i}", "peer", (number,)) for i, number in enumerate(sorted(features), 1)]

    def setup(self, run):
        given = {number: spec.id for spec in run.agents.values() for number in spec.features}
        titles = {number: issue_title(run.specs.get(number), number) for number in given}
        run.state[QUEUE_KEY] = gate.Queue(given, titles, bool(run.options.get("open_queue")),
                                          resolve_base_ref(run.options))
        remote = run.options.get("git_remote_host")
        run.state[GATE_KEY] = bare_git.BareGitWorld(str(remote), MAIN) if remote else None

    def tools(self, run, agent):
        return sdlc_tools() + kit.messaging_tools(wait=True)

    def prompt(self, run, agent, specs):
        # The spec as CooperBench hands it over, unstripped, as the coop arms do.
        spec = SECTION_RULE.join(specs[number] for number in run.agents[agent].features)
        block = protocol_block(run, agent, list(self.tools_for(run, agent).values()))
        return SECTION_RULE.join([spec, block, SUBMISSION_BLOCK])

    def teardown(self, run):
        # The hub calls this outside run.lock, so waiting here for a merge in
        # progress, which holds the world's lock, cannot deadlock.
        world = run.state.get(GATE_KEY)
        if world is not None:
            world.close()

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run), "tool_calls": tool_call_counts(run),
                "claims": count_events(run, "claim"), "prs_opened": count_events(run, "pr_opened"),
                "merge_attempts": count_events(run, "merge_attempt"), "merges": count_events(run, "merge"),
                "merge_conflicts": count_events(run, "merge_conflict"),
                "time_to_first_merge_seconds": first_merge_seconds(run)}
