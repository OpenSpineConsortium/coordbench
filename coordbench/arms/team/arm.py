"""The team arm: CooperBench's team mode, the best arm of the authors' ablation.

One agent per feature, numbered in the order of the sorted features. The
first, which has the lowest feature, is the lead and the rest are members,
as runner/team.py lays them out. Every agent gets the same tools:

- the shared task list (team_harness/task_list.py and coop_task.py):
  create, claim, update and list, with CooperBench's fields, its four
  statuses and its claim rule, under which a task owned by another agent,
  a pre-assigned one included, cannot be claimed;
- the typed request protocol (team_harness/protocol.py): request, respond
  and pending, for questions that want an answer by id;
- CooperBench's messaging with wait_for_message, the long poll its team
  mode registers for CLI agents (team_harness/mcp_server.py).

Each tool carries the shell command CooperBench installs for it
(install_snippet.sh) as its alias, so `coop-task-claim <id>` works as in
the authors' runs. setup() seeds the list as runner/team.py does: one task
per member feature, assigned to that member, and the lead's
integrate-and-submit task. The prompt is build_team_instruction's
(team_harness/prompt.py), word for word, with one sentence added after the
command list naming the MCP tool behind each command. The arm needs the
shared scratchpad the prompt names (/workspace/shared) and runs without
git, as the authors' ablation did; given a shared remote (the run option
"git_remote") it adds CooperBench's git block, which coop-git holds.

Where this differs from CooperBench, and why:

- The list lives in the run's memory, not Redis, and every check-and-write
  holds the run's lock, so a claim is atomic in fact; CooperBench reads
  the owner and then writes it, which its own comments call race-safe
  only because contention is small.
- A request to an agent that is not in the run is refused, as kit's
  messages are, rather than queued for nobody.
- A request's wait is capped (LONGEST_WAIT_SECONDS) and a closed run wakes
  it, so a blocked call never holds a hub thread past the run.
- cli.py maps a command's words onto argv by position only, while
  coop_task.py takes flags (--assign, -n, --mine, --open, --wait). The
  tools whose commands take flags give their trailing words to argv slots
  that only the shell fills (words, flag, second_flag), and read those
  words back the way coop_task.py's argparse would. The slots are left out
  of the MCP schemas, so an MCP client sees clean arguments and never has
  a note or a body re-read as flags.
- A refused call exits 1 from cli.py; coop_task.py exits 2 for a lost
  claim, 3 for an update by a non-owner, and so on. The message on stderr
  is the same.
- The mirror of the list under /workspace/shared/tasks, which coop-task-list
  writes as it lists, is written by the hub, so it needs the shared
  directory as the hub's host sees it (the run option "shared_dir_host");
  without one there is no mirror.

Every ported piece is from CooperBench at 63b9d44 (MIT licence) and says
where it came from.
"""

import functools
import importlib.util
import json
import os
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

import kit
from arm import SUBMISSION_BLOCK, AgentSpec, Arm, Tool, ToolError, builtin_arms_dir

# build_team_instruction joins its sections with this rule.
SECTION_RULE = "\n\n---\n\n"

# CooperBench's text uses the em dash; the escape keeps this file ASCII
# while the prompt keeps the authors' character.
DASH = "\u2014"

# task_list.py's VALID_STATUSES.
VALID_STATUSES = frozenset({"open", "in_progress", "blocked", "done"})

# runner/team.py seeds the list in this name.
BENCH_RUNNER = "bench-runner"

# coop_task.py waits as long as asked; the hub caps it well under cli.py's
# ten-minute timeout, so the command hears back before it gives up.
LONGEST_WAIT_SECONDS = 300

# The run event each audit-log entry is recorded as, so events.jsonl
# keeps the same trail CooperBench keeps in its task-log.
EVENT_KINDS = {"create": "task_create", "claim": "task_claim", "update": "task_update",
               "request": "request", "response": "response"}

TEXT = kit.TEXT
BOOLEAN = {"type": "boolean"}


def fresh_id(*taken):
    """A ten-hex-digit id, as task_list.py and protocol.py make them, that
    none of the given collections holds."""
    while True:
        candidate = uuid.uuid4().hex[:10]
        if not any(candidate in ids for ids in taken):
            return candidate


class Board:
    """The team's shared state as CooperBench keeps it in Redis: the tasks,
    the open requests, the responses not yet collected, and one audit log
    of both (the task-log). Every method holds the run's lock across its
    whole read, check and write, which is what makes a claim atomic: of
    any number of agents racing for an unowned task, exactly one wins."""

    def __init__(self, run):
        self.run = run
        self.tasks = {}
        self.log = []
        self.requests = {}
        self.responses = {}

    def record(self, kind, **fields):
        """One task-log entry, shaped as CooperBench's _log writes it, kept
        also as a run event."""
        self.log.append({"kind": kind, **fields, "ts": time.time()})
        details = {key: value for key, value in fields.items() if key != "by"}
        self.run.emit(EVENT_KINDS[kind], fields.get("by"), **details)

    def create(self, title, by, owner="", metadata=None):
        """task_list.py's create. A task starts open even when it has an
        owner: only a claim moves it to in_progress."""
        with self.run.lock:
            task_id = fresh_id(self.tasks)
            self.tasks[task_id] = {
                "id": task_id, "title": title, "owner": owner or "", "status": "open",
                "created_by": by, "created_at": time.time(), "last_note": "",
                "metadata": dict(metadata or {}),
            }
            self.record("create", task_id=task_id, by=by, title=title)
        return task_id

    def claim(self, task_id, by):
        """coop_task.py's cmd_claim. A task another agent owns is refused,
        whether it claimed it or was assigned it; claiming one's own task
        again succeeds and is logged again, as in CooperBench."""
        with self.run.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ToolError(f"task {task_id} does not exist")
            if task["owner"] not in ("", by):
                raise ToolError(f"task {task_id} owned by {task['owner']}")
            task.update(owner=by, status="in_progress")
            self.record("claim", task_id=task_id, by=by)

    def update(self, task_id, by, status, note=None):
        """coop_task.py's cmd_update: the owner only, a known status only."""
        with self.run.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise ToolError(f"task {task_id} does not exist")
            if task["owner"] != by:
                raise ToolError(f"task {task_id} owned by {task['owner']!r}, not you")
            if status not in VALID_STATUSES:
                raise ToolError(f"invalid status {status!r}; expected one of {sorted(VALID_STATUSES)}")
            task["status"] = status
            details = {"status": status}
            if note is not None:
                task["last_note"] = details["note"] = note
            self.record("update", task_id=task_id, by=by, **details)

    def snapshot(self):
        """Copies of every task, in id order as coop-task-list prints them,
        and of the log, taken at one moment."""
        with self.run.lock:
            tasks = [{**self.tasks[i], "metadata": dict(self.tasks[i]["metadata"])}
                     for i in sorted(self.tasks)]
            return tasks, [dict(entry) for entry in self.log]

    def request(self, sender, recipient, kind, body):
        """protocol.py's request: an open request in the recipient's queue."""
        if recipient not in self.run.agents:
            raise ToolError(f"no agent named {recipient!r}; the agents are {', '.join(self.run.agents)}")
        with self.run.lock:
            request_id = fresh_id(self.requests, self.responses)
            self.requests[request_id] = {"id": request_id, "from": sender, "to": recipient,
                                         "kind": kind, "body": body, "ts": time.time()}
            self.record("request", request_id=request_id, by=sender, to=recipient, request_kind=kind)
        return request_id

    def await_response(self, request_id, seconds):
        """protocol.py's await_response, a BLPOP on the request's responses:
        the oldest response once one is queued, or None when the time is up
        or the run has closed."""
        run = self.run
        run.wait_until(lambda: bool(self.responses.get(request_id)) or run.closed, seconds)
        with run.lock:
            queue = self.responses.get(request_id)
            return queue.pop(0) if queue else None

    def respond(self, responder, request_id, body):
        """protocol.py's respond: only the addressee answers, and only once,
        since answering closes the request."""
        with self.run.lock:
            request = self.requests.get(request_id)
            if request is None:
                raise ToolError(f"unknown request {request_id}")
            if request["to"] != responder:
                raise ToolError(f"request {request_id} not addressed to {responder}")
            response = {"id": request_id, "from": responder, "to": request["from"],
                        "body": body, "ts": time.time()}
            self.responses.setdefault(request_id, []).append(response)
            del self.requests[request_id]
            self.record("response", request_id=request_id, by=responder, to=request["from"])

    def pending(self, agent):
        """protocol.py's list_pending: the open requests to this agent, by id."""
        with self.run.lock:
            return [dict(self.requests[i]) for i in sorted(self.requests)
                    if self.requests[i]["to"] == agent]


def board_of(run):
    with run.lock:
        if "team" not in run.state:
            run.state["team"] = Board(run)
        return run.state["team"]


def lead_of(run):
    """The lead's id. build_team_instruction's fallback name stands in for a
    run without one, which this arm's layout never makes."""
    return next((agent for agent, spec in run.agents.items() if spec.role == "lead"), "team-lead")


# Shell words, read as coop_task.py's argparse reads them.

CREATE_OPTIONS = {"--assign": ("assign", True)}
UPDATE_OPTIONS = {"-n": ("note", True), "--note": ("note", True)}
LIST_OPTIONS = {"--mine": ("mine", False), "--open": ("open", False)}
REQUEST_OPTIONS = {"--wait": ("wait", True)}

UPDATE_ARGV = ("task_id", "status", "flag", "note")
LIST_ARGV = ("flag", "second_flag")


def read_words(words, options):
    """Split shell words into options and positionals: a word naming an
    option is that option, a switch or taking the next word (or the text
    after '=') as its value; every other word is positional, in order."""
    found, positionals, queue = {}, [], list(words)
    while queue:
        word = queue.pop(0)
        flag, equals, attached = word.partition("=")
        if flag not in options:
            positionals.append(word)
            continue
        name, takes_value = options[flag]
        if not takes_value:
            found[name] = True
        elif equals:
            found[name] = attached
        elif queue:
            found[name] = queue.pop(0)
        else:
            raise ToolError(f"argument {flag}: expected one argument")
    return found, positionals


def exactly(positionals, names):
    """The positionals for these names, refused as argparse refuses too few
    or too many."""
    if len(positionals) < len(names):
        raise ToolError(f"the following arguments are required: {', '.join(names[len(positionals):])}")
    if len(positionals) > len(names):
        raise ToolError(f"unrecognized arguments: {' '.join(positionals[len(names):])}")
    return positionals


def shell_words(call, names):
    return [str(call.arguments[name]) for name in names if name in call.arguments]


def truthy(value):
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def create_arguments(call):
    """(title, assign) from MCP arguments, or from coop-task-create's words:
    `"<title>"` or `--assign <agent> "<title>"`."""
    if "words" not in call.arguments:
        return str(call.arg("title", required=True)), str(call.arg("assign") or "")
    found, title_words = read_words(str(call.arguments["words"]).split(" "), CREATE_OPTIONS)
    title = " ".join(title_words).strip()
    if not title:
        raise ToolError("the following arguments are required: title")
    return title, found.get("assign", "")


def update_arguments(call):
    """(task_id, status, note) from MCP arguments, or from coop-task-update's
    words: `<task_id> <status> [-n "<note>"]`, the option anywhere."""
    if "flag" not in call.arguments:
        note = call.arg("note")
        return (str(call.arg("task_id", required=True)), str(call.arg("status", required=True)),
                None if note is None else str(note))
    found, positionals = read_words(shell_words(call, UPDATE_ARGV), UPDATE_OPTIONS)
    task_id, status = exactly(positionals, ("task_id", "status"))
    return task_id, status, found.get("note")


def list_filters(call):
    """(mine, open) from MCP arguments, or from coop-task-list's --mine and
    --open."""
    words = shell_words(call, LIST_ARGV)
    if not words:
        return truthy(call.arg("mine")), truthy(call.arg("open"))
    found, extra = read_words(words, LIST_OPTIONS)
    exactly(extra, ())
    return found.get("mine", False), found.get("open", False)


def request_arguments(call):
    """(recipient, kind, body, wait) from MCP arguments, or from
    coop-request's words: `<recipient> <kind> [body] [--wait N]`. The body
    is every positional word after the kind, as the shell gave them."""
    if "words" not in call.arguments:
        return (str(call.arg("recipient", required=True)), str(call.arg("kind", required=True)),
                str(call.arg("body") or ""), call.arg("wait"))
    words = shell_words(call, ("recipient", "kind")) + str(call.arguments["words"]).split(" ")
    found, positionals = read_words(words, REQUEST_OPTIONS)
    recipient, kind = exactly(positionals[:2], ("recipient", "kind"))
    return recipient, kind, " ".join(positionals[2:]), found.get("wait")


def wait_seconds(value):
    """protocol.py's clamp from below, and this hub's cap from above."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ToolError(f"argument --wait: invalid float value: {value!r}") from None
    return min(LONGEST_WAIT_SECONDS, max(0.05, seconds))


# The handlers. Each prints what coop_task.py prints: an id, JSON, or
# nothing at all on success.

def task_create(call):
    title, assign = create_arguments(call)
    return board_of(call.run).create(title, call.agent, owner=assign)


def task_claim(call):
    board_of(call.run).claim(str(call.arg("task_id", required=True)), call.agent)
    return ""


def task_update(call):
    task_id, status, note = update_arguments(call)
    board_of(call.run).update(task_id, call.agent, status, note)
    return ""


def task_list(call):
    mine, open_only = list_filters(call)
    tasks, log = board_of(call.run).snapshot()
    chosen = [task for task in tasks
              if (not mine or task["owner"] == call.agent) and (not open_only or task["status"] == "open")]
    mirror_if_shared(call.run, tasks, log)
    return json.dumps(chosen, indent=2)


def send_request(call):
    recipient, kind, body, wait = request_arguments(call)
    # A bad --wait is refused before anything is sent, as argparse would.
    seconds = None if wait is None else wait_seconds(wait)
    board = board_of(call.run)
    request_id = board.request(call.agent, recipient, kind, body)
    if seconds is None:
        return request_id
    response = board.await_response(request_id, seconds)
    if response is None:
        raise ToolError(f"timed out waiting for response to {request_id}")
    return json.dumps(response)


def answer_request(call):
    board_of(call.run).respond(call.agent, str(call.arg("request_id", required=True)),
                               str(call.arg("body") or ""))
    return ""


def pending_requests(call):
    return json.dumps(board_of(call.run).pending(call.agent), indent=2)


def task_tools():
    statuses = {"type": "string", "enum": sorted(VALID_STATUSES)}
    assign = {"type": "string", "description": "an agent id to pre-assign it to; that agent still claims it"}
    return [
        Tool("task_create",
             "Create a task on the team's shared list and print its id. It starts open; assign "
             "pre-sets its owner, who must still claim it. "
             "Shell: coop-task-create [--assign <agent>] \"<title>\".",
             task_create, kit.schema(["title"], title=TEXT, assign=assign),
             alias="coop-task-create", argv=("words...",)),
        Tool("task_claim",
             "Claim a task: you become its owner and it moves to in_progress. Refused when another "
             "agent owns it, by claim or by assignment; claiming your own again is fine. "
             "Shell: coop-task-claim <task_id>.",
             task_claim, kit.schema(["task_id"], task_id=TEXT),
             alias="coop-task-claim", argv=("task_id",)),
        Tool("task_update",
             "Set the status of a task you own (open, in_progress, blocked or done), with an "
             "optional note the others read as its last_note. "
             "Shell: coop-task-update <task_id> <status> [-n \"<note>\"].",
             task_update, kit.schema(["task_id", "status"], task_id=TEXT, status=statuses, note=TEXT),
             alias="coop-task-update", argv=UPDATE_ARGV),
        Tool("task_list",
             "Every task on the team's list, as JSON; mine keeps the tasks you own, open keeps "
             "those whose status is open. Shell: coop-task-list [--mine] [--open].",
             task_list, kit.schema(mine=BOOLEAN, open=BOOLEAN),
             alias="coop-task-list", argv=LIST_ARGV),
    ]


def protocol_tools():
    wait = {"type": "number", "minimum": 0, "maximum": LONGEST_WAIT_SECONDS,
            "description": "seconds to block for the response; leave out to return the request id at once"}
    return [
        Tool("request",
             "Send a typed request (a kind, such as plan_approval, and a body) to one agent and "
             "print its id; with wait, block up to that many seconds for the response and print it "
             "instead. Shell: coop-request <recipient> <kind> [body] [--wait N].",
             send_request, kit.schema(["recipient", "kind"], recipient=TEXT, kind=TEXT, body=TEXT, wait=wait),
             alias="coop-request", argv=("recipient", "kind", "words...")),
        Tool("respond",
             "Answer an open request addressed to you; the response goes to the requester waiting "
             "on it, and the request closes. Shell: coop-respond <request_id> [body].",
             answer_request, kit.schema(["request_id"], request_id=TEXT, body=TEXT),
             alias="coop-respond", argv=("request_id", "body...")),
        Tool("pending",
             "The open requests addressed to you, as JSON. Shell: coop-pending.",
             pending_requests, alias="coop-pending"),
    ]


# The list's mirror on the shared scratchpad.

def write_atomically(path, text):
    """Write through a temporary file and a rename, so a reader sees the old
    file or the new one, never half of one (fs_mirror.py)."""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def mirror_task_list(target, tasks, log):
    """coop_task.py's _snapshot_to_directory: one JSON file per task, stale
    ones removed, an index and a copy of the log."""
    target.mkdir(parents=True, exist_ok=True)
    live = {task["id"] for task in tasks}
    for task in tasks:
        write_atomically(target / f"{task['id']}.json", json.dumps(task, indent=2, default=str))
    for stale in target.glob("*.json"):
        if not stale.name.startswith("_") and stale.stem not in live:
            stale.unlink(missing_ok=True)
    index = {"updated_at": time.time(), "ids": sorted(live)}
    write_atomically(target / "_index.json", json.dumps(index, indent=2))
    write_atomically(target / "_log.jsonl", "".join(json.dumps(entry) + "\n" for entry in log))


def mirror_if_shared(run, tasks, log):
    """The side effect of coop-task-list, best effort as CooperBench's is."""
    shared = run.options.get("shared_dir_host")
    if not shared:
        return
    try:
        mirror_task_list(Path(shared) / "tasks", tasks, log)
    except OSError:
        pass


# The prompt: team_harness/prompt.py, word for word.

TEAM_LIST_USAGE = """Available shell commands (Redis-backed, atomic):

```bash
coop-task-create "<title>"                 # creates an open, unassigned task; prints task_id
coop-task-create --assign <agent> "<title>" # creates and pre-assigns; agent must still claim
coop-task-claim <task_id>                   # exit 0 if you got it, 2 if someone else owns it
coop-task-update <task_id> <status> [-n "<note>"]
                                            # statuses: open | in_progress | blocked | done
coop-task-list                              # JSON list of every task in the team
coop-task-list --mine                       # tasks you own
coop-task-list --open                       # open, unassigned tasks
```

Messaging (`coop-send` / `coop-recv` / `coop-broadcast` / `coop-peek`)
also works and is the right tool for short questions that don't
warrant a new task."""


def lead_block(agent_id, members, usage):
    """_lead_block, with the command list passed in so the MCP sentence can
    follow it."""
    member_list = ", ".join(f"`{m}`" for m in members)
    return f"""## You are the team-lead

You are **{agent_id}** {DASH} the team-lead.  Members reporting to you:
{member_list}.

### The integration step is the WHOLE point of your job {DASH} DO NOT SKIP IT

The benchmark scores the **merged** team output, not your individual
contribution.  If you submit `patch.txt` containing only your own
feature, the team **fails** {DASH} even if your feature works perfectly.
The single most common failure mode here is "lead ran `git diff > patch.txt`
before pulling in the member's work."  Avoid this.

Workflow:

1. **Plan to avoid conflicts.** Read the feature spec.  If features
   touch the same file/function, divide the work so changes occupy
   disjoint regions (e.g., one feature owns the function signature,
   the other owns helper additions).  Drop a one-paragraph plan in
   `/workspace/shared/PLAN.md` so members see your decomposition.
2. Break the spec into 2-4 concrete tasks and assign one per member
   with `coop-task-create --assign <agent> "..."`.
3. You **may also implement a task yourself** in parallel.
4. Poll progress: `coop-task-list` periodically.  If a member's task
   is `blocked`, read the `last_note` and either reassign or
   unblock it with `coop-send`.

### Before submitting {DASH} MANDATORY integration checklist

You may submit ONLY after **all five** of these are true:

- [ ] `coop-task-list` shows every team task as `done`.
- [ ] Every member has dropped their patch at
      `/workspace/shared/<agent_id>.patch`.  (Run
      `ls /workspace/shared/*.patch` to verify.)
- [ ] You have read each member's patch (`cat /workspace/shared/<agent>.patch`)
      and applied it to your working tree (`git apply
      /workspace/shared/<agent>.patch`, fixing any conflicts
      manually with Edit until the tree builds).
- [ ] The merged tree builds / compiles.  Run the project's build
      or import test before continuing.
- [ ] `git diff` now shows BOTH features {DASH} your own and every
      member's {DASH} present in the working tree.

Only when ALL five boxes are checked, write `/workspace/repo/patch.txt`
via `git diff` and exit {DASH} this is REQUIRED:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

If `wc -l patch.txt` looks too small to contain everyone's work, you
skipped the integration step {DASH} go back to step 3 of the checklist.

{usage}

A shared scratchpad volume is mounted at `/workspace/shared/` for
coordination {DASH} files there are NOT evaluated.  Drop design notes,
interface contracts, or member-produced patches there so the whole
team can see them with `ls /workspace/shared/`."""


def member_block(agent_id, lead, usage):
    """_member_block, with the command list passed in as lead_block's is."""
    return f"""## You are a team member

You are **{agent_id}**.  The team-lead is **{lead}**, who will
organize work into tasks for you to claim.

### Stay in your lane

The lead may have left a plan at `/workspace/shared/PLAN.md`.  Read it
first {DASH} it tells you which file regions your feature owns, vs. those
reserved for your peers.  Edit ONLY the regions your task owns.

Workflow:

1. Run `coop-task-list --open` to see what needs doing.  If your
   `agent_id` appears as a pre-assigned `owner` on a task, that's the
   one the lead expects you to take.  You can also `ls
   /workspace/shared/tasks/` to browse without the CLI.
2. `coop-task-claim <task_id>`.  If you lose the race (exit code 2),
   pick another.
3. Read `/workspace/shared/PLAN.md` if it exists, then implement.
   Stay within your region.  When you hit a blocker, run
   `coop-task-update <task_id> blocked -n "<what you need>"` and
   `coop-send {lead} "blocked on task <id>: ..."`.
4. When done, ALWAYS export your diff for the lead to consume:

   ```bash
   cd /workspace/repo && git diff > /workspace/shared/{agent_id}.patch
   ```

   Then `coop-task-update <task_id> done -n "patch at
   /workspace/shared/{agent_id}.patch"`.  The lead will integrate.

### Final submission {DASH} REQUIRED

The bench scores per-agent.  Before exiting you MUST write your own
`/workspace/repo/patch.txt`:

```bash
cd /workspace/repo && git diff > patch.txt && wc -l patch.txt
```

Your `/workspace/repo/patch.txt` should reflect your final working
tree.  If the lead asked you to merge in their plan, do so first.

{usage}

A shared scratchpad volume is mounted at `/workspace/shared/` for
coordination {DASH} files there are NOT evaluated.  Use it for anything
your peers might need to see {DASH} partial diffs, interface sketches,
error logs from your reproduction script."""


def joined_names(pairs):
    """`a` is `b`, `c` is `d`, and `e` is `f`: the pairs as one clause."""
    phrases = [f"`{alias}` is `{name}`" for alias, name in pairs]
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + ", and " + phrases[-1]


def mcp_sentence(tools):
    """The one sentence this arm adds to CooperBench's prompt, built from the
    tools themselves so it names whatever the hub really serves."""
    pairs = [(tool.alias, tool.name) for tool in tools if tool.alias.startswith("coop-")]
    if not pairs:
        return ""
    return ("Every coop-* command is also a tool of the MCP server `coordbench`, with the same "
            f"task list and inbox behind it: {joined_names(pairs)}.")


@functools.cache
def coop_git_module():
    """The built-in coop-git arm's module, which holds CooperBench's git block
    (_coop/prompt.py's _git_block, which build_team_instruction also
    imports). Loaded only for a run with a shared remote, so a team run
    without git never depends on it."""
    path = builtin_arms_dir() / "coop-git" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_coop_git_for_team", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_block(agent_id, partners):
    return coop_git_module().git_block(agent_id, partners)


def task_text(run, agent, specs):
    """The agent's spec as CooperBench passes it: the feature file's text,
    unstripped, since team.py hands read_text() straight to the prompt."""
    return SECTION_RULE.join(specs[number] for number in run.agents[agent].features)


# team_harness/metrics.py.

def compute_metrics(events, final_tasks):
    """compute_metrics from the task-log and the tasks at the end, unchanged."""
    creates = [e for e in events if e.get("kind") == "create"]
    claims = [e for e in events if e.get("kind") == "claim"]
    updates = [e for e in events if e.get("kind") == "update"]
    first_create = min((e.get("ts", 0.0) for e in creates), default=None)
    first_claim = min((e.get("ts", 0.0) for e in claims), default=None)
    time_to_first_claim = None
    if first_create is not None and first_claim is not None:
        time_to_first_claim = round(first_claim - first_create, 3)
    return {
        "tasks_total": len(final_tasks),
        "tasks_done": sum(1 for t in final_tasks if t.get("status") == "done"),
        "unowned_at_end": sum(1 for t in final_tasks if not t.get("owner")),
        "time_to_first_claim_seconds": time_to_first_claim,
        "claims_per_agent": dict(Counter(e.get("by", "?") for e in claims)),
        "updates_per_agent": dict(Counter(e.get("by", "?") for e in updates)),
    }


def tool_call_counts(run):
    """How often each tool was called, from the hub's "tool" events, as the
    coop arms count them."""
    return dict(Counter(event["tool"] for event in run.events if event["kind"] == "tool"))


class TeamArm(Arm):
    name = "team"
    description = ("CooperBench's team mode, the best arm of the authors' ablation: a lead and "
                   "members, one per feature, with a shared task list, typed requests, messages "
                   "and a shared scratchpad.")
    needs = ("shared_volume",)

    def layout(self, features):
        # runner/team.py zips agent1, agent2 and so on with the sorted
        # features and makes the first the lead.
        return [AgentSpec(f"agent{i}", "lead" if i == 1 else "member", (number,))
                for i, number in enumerate(sorted(features), 1)]

    def setup(self, run):
        """runner/team.py's seeding: each member's feature as a task assigned
        to it, then the lead's task, in that order and in that wording."""
        board = board_of(run)
        lead = lead_of(run)
        for agent, spec in run.agents.items():
            if agent != lead:
                feature = spec.features[0]
                board.create(f"Implement feature {feature}", BENCH_RUNNER, owner=agent,
                             metadata={"feature_id": feature, "assigned_to": agent})
        feature = run.agents[lead].features[0]
        board.create(f"Lead-only: integrate and submit feature {feature}", BENCH_RUNNER, owner=lead,
                     metadata={"feature_id": feature, "assigned_to": lead, "lead_task": True})

    def tools(self, run, agent):
        return task_tools() + protocol_tools() + kit.messaging_tools(wait=True)

    def prompt(self, run, agent, specs):
        return SECTION_RULE.join(self.sections(run, agent, specs))

    def sections(self, run, agent, specs):
        """build_team_instruction's sections in its order: the task, the
        submission block, the role's block, and the git block when the run
        has a shared remote. A team of one gets the first two alone, as
        CooperBench falls back to the solo prompt."""
        sections = [task_text(run, agent, specs), SUBMISSION_BLOCK]
        partners = run.peers(agent)
        if not partners:
            return sections
        sections.append(self.role_block(run, agent))
        if run.options.get("git_remote"):
            sections.append(git_block(agent, partners))
        return sections

    def role_block(self, run, agent):
        note = mcp_sentence(self.tools_for(run, agent).values())
        usage = TEAM_LIST_USAGE + (f"\n\n{note}" if note else "")
        if run.role_of(agent) == "lead":
            return lead_block(agent, run.peers(agent), usage)
        return member_block(agent, lead_of(run), usage)

    def metrics(self, run):
        tasks, log = board_of(run).snapshot()
        return {**super().metrics(run), **compute_metrics(log, tasks), **kit.message_metrics(run),
                "tool_calls": tool_call_counts(run)}
