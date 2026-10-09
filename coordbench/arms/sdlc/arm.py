"""The sdlc arm: this repository's own claim protocol as a coordination arm.

The template's way of working (docs/agents/PROTOCOL.md, with the merge
habits of docs/agents/TEAM.md) is one more way of wiring agents together,
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
scripts/check.sh are left out: in a run of two or three agents on one task
pair there is nothing for them to act on.

The gate acts on the real repository, named by the run option
"git_remote_host" (the shared remote as the hub's host reaches it). It
keeps one scratch clone per run in a temporary directory, runs git through
subprocess with a timeout, serialises every git operation with its own
lock, and never force-pushes, so main only ever moves forward. Its lock is
always taken before the run's lock and never while holding it, which is
what keeps the hub's metrics (read under the run's lock) from deadlocking
against a merge in progress. When the run closes, teardown frees the clone
and shuts the gate, so a late call is refused rather than cloning again.

A refusal's shell command exits with a code that says what kind of
refusal it is (the EXIT_ constants below, listed for the agents in
SKILL.md), so a script can tell losing a race from main having moved.
"""

import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass

import kit
from arm import AgentSpec, Arm, SUBMISSION_BLOCK, Tool, ToolError

# The same rule the coop arms put between a prompt's sections, so the
# prompts of every arm read alike.
SECTION_RULE = "\n\n---\n\n"

MAIN = "main"
MAIN_REF = f"refs/remotes/origin/{MAIN}"
GIT_TIMEOUT_SECONDS = 300

# The board's own words for a pull request main has moved past, in the
# merge-not-rebase form TEAM.md gives members.
MAIN_MOVED = ("main moved: merge team/main into your branch, resolve the conflicts, push, "
              "and call mark_ready again")

# The scratch clone is the gate's own: a signing key, a hook or a pager in
# the host's git configuration must not decide whether a merge lands.
GIT_OPTIONS = ("-c", "user.name=coordbench merge gate", "-c", "user.email=gate@coordbench.local",
               "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}")

QUEUE_KEY, GATE_KEY = "sdlc_queue", "sdlc_gate"

# What a refused call's shell command exits with, beyond the 1 of any
# refusal the caller fixes by acting (a branch not pushed yet, nothing to
# merge). 2 is the code CooperBench's coop-task-claim gives a task someone
# else owns, kept so a script written for it reads this arm the same way.
EXIT_SOMEONE_ELSES = 2   # another agent holds the claim, the issue or the pull request
EXIT_MAIN_MOVED = 3      # the gate left main as it was; merge main in and try again
EXIT_GATE_FAILED = 4     # the gate itself could not act: git failed, or the run closed

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


def branch_of(number):
    return f"claude/{number}"


def short(sha):
    return sha[:7] if sha else "unknown"


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


@dataclass
class Issue:
    """One feature's issue: its title, who was given it, who claimed it,
    and whether its pull request has merged, which closes it."""

    number: int
    given_to: str
    title: str
    claimant: str = ""
    claim_released: bool = False
    closed: bool = False

    def label(self):
        """The issue's state in the template's label words."""
        if self.closed:
            return "closed"
        if self.claim_released:
            return "in-review"
        return "claimed" if self.claimant else "todo"


@dataclass
class PullRequest:
    """One issue's pull request. State is draft, ready (marked ready and at
    or past the gate), main moved (the gate refused it for conflicts) or
    merged."""

    issue: int
    author: str
    title: str
    state: str = "draft"
    attempts: int = 0
    merge_commit: str = ""
    conflicts: tuple = ()


class Queue:
    """The issues and pull requests of one run, which the board shows.
    Every method expects the caller to hold run.lock."""

    def __init__(self, given, titles, open_queue, base_ref):
        self.issues = {number: Issue(number, agent, titles[number]) for number, agent in sorted(given.items())}
        self.pulls = {}
        self.open_queue = open_queue
        self.base_ref = base_ref

    def issue(self, number):
        if number not in self.issues:
            known = ", ".join(f"#{n}" for n in self.issues)
            raise ToolError(f"there is no issue #{number}; the issues are {known}")
        return self.issues[number]

    def given_to(self, agent):
        return [issue.number for issue in self.issues.values() if issue.given_to == agent]

    def pull(self, number):
        self.issue(number)
        if number not in self.pulls:
            raise ToolError(f"#{number} has no pull request; open it with open_pull_request "
                            f"(sdlc-pr {number}) once {branch_of(number)} is pushed")
        return self.pulls[number]


@dataclass
class Standing:
    """How a branch stands to main on the remote right now, in GitHub's
    words where GitHub has one: clean, behind, conflicting."""

    word: str
    main: str = ""
    conflicts: tuple = ()


@dataclass
class MergeOutcome:
    """What one merge attempt did: merged, or why not, and main's commit
    after it (the merge commit, or main as it was)."""

    merged: bool
    main: str
    conflicts: tuple = ()
    main_moved: bool = False
    reason: str = ""


def git_environment():
    """The host's environment, with git told never to wait for a password
    and to speak English, since the gate quotes its messages."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def run_git(args, cwd=None):
    """One git command with the gate's options and a timeout. A git that
    hangs or is missing becomes a ToolError the agent can read."""
    try:
        return subprocess.run(["git", *GIT_OPTIONS, *args], cwd=cwd, capture_output=True, text=True,
                              errors="replace", timeout=GIT_TIMEOUT_SECONDS, env=git_environment(),
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolError(f"git {args[0]} took longer than {GIT_TIMEOUT_SECONDS} seconds and was stopped",
                        EXIT_GATE_FAILED)
    except FileNotFoundError:
        raise ToolError("git is not installed where the coordbench hub runs", EXIT_GATE_FAILED)


def last_line(result):
    lines = [line.strip() for line in (result.stderr + "\n" + result.stdout).splitlines() if line.strip()]
    return lines[-1] if lines else f"git exited with {result.returncode}"


def remote_main(remote):
    """main's commit on the remote, or "" when it cannot be read."""
    try:
        result = run_git(["ls-remote", remote, f"refs/heads/{MAIN}"])
    except ToolError:
        return ""
    words = result.stdout.split()
    return words[0] if result.returncode == 0 and words else ""


class Gate:
    """The merge gate: a scratch clone of the shared remote, made at first
    use, and the lock every git operation on it holds."""

    def __init__(self, remote):
        self.remote = remote
        self.lock = threading.Lock()
        self.scratch = None
        self.closed = False

    @property
    def repo(self):
        return os.path.join(self.scratch.name, "repo")

    def git(self, *args, check=True):
        result = run_git(list(args), cwd=self.repo)
        if check and result.returncode != 0:
            raise ToolError(f"the merge gate's git {args[0]} failed: {last_line(result)}", EXIT_GATE_FAILED)
        return result

    def ensure_clone(self):
        """Clone the remote without checking anything out; every merge
        checks out the main it merges into."""
        if self.scratch is not None:
            return
        if self.closed:
            raise ToolError("the run has closed, and the merge gate with it: nothing more merges into "
                            f"{MAIN}; submit what you have", EXIT_GATE_FAILED)
        scratch = tempfile.TemporaryDirectory(prefix="coordbench-sdlc-gate-")
        result = run_git(["clone", "--quiet", "--no-checkout", self.remote,
                          os.path.join(scratch.name, "repo")])
        if result.returncode != 0:
            scratch.cleanup()
            raise ToolError(f"the merge gate cannot clone the shared remote: {last_line(result)}",
                            EXIT_GATE_FAILED)
        self.scratch = scratch

    def close(self):
        """Free the scratch clone for good. A call that reaches the gate
        later is refused rather than cloning again, since nothing would free
        a second clone; one in progress finishes first, holding the lock."""
        with self.lock:
            self.closed = True
            if self.scratch is not None:
                self.scratch.cleanup()
                self.scratch = None

    def fetch(self):
        self.ensure_clone()
        self.git("fetch", "--quiet", "--prune", "origin")

    def commit_of(self, ref):
        result = self.git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def branch_tip(self, branch):
        return self.commit_of(f"refs/remotes/origin/{branch}")

    def main_tip(self):
        main = self.commit_of(MAIN_REF)
        if not main:
            raise ToolError(f"the shared remote has no branch {MAIN}; the run was not set up as this arm needs",
                            EXIT_GATE_FAILED)
        return main

    def is_ancestor(self, older, newer):
        return self.git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def tidy(self):
        """Leave the clone with no merge in progress and nothing stray, so a
        refused or interrupted merge never leaks into the next one."""
        self.git("merge", "--abort", check=False)
        self.git("reset", "--hard", "--quiet", check=False)
        self.git("clean", "-ffdq", check=False)

    def unmerged_paths(self):
        result = self.git("diff", "--name-only", "--diff-filter=U", check=False)
        return tuple(sorted(set(result.stdout.splitlines())))

    def prepare(self, branch):
        """Fetch, and the branch's tip and main's, refusing a branch that is
        not on the remote or holds nothing main lacks."""
        self.fetch()
        tip = self.branch_tip(branch)
        if not tip:
            raise ToolError(f"{branch} is not on team; push it first: git push -u team {branch}")
        main = self.main_tip()
        if self.is_ancestor(tip, main):
            raise ToolError(f"nothing to merge: every commit on {branch} is already in {MAIN}; "
                            f"commit your work, push it, and call mark_ready again")
        return tip, main

    def merge(self, tip, main, message):
        """Merge tip into main as `git merge --no-ff --no-edit` does and push
        main without force; a conflict or a refused push leaves main as it
        was on the remote."""
        self.tidy()
        self.git("checkout", "--quiet", "--force", "--detach", main)
        attempt = self.git("merge", "--no-ff", "--no-edit", "-m", message, tip, check=False)
        if attempt.returncode != 0:
            conflicts = self.unmerged_paths()
            self.tidy()
            return MergeOutcome(False, main, conflicts, main_moved=bool(conflicts), reason=last_line(attempt))
        merged = self.commit_of("HEAD")
        push = self.git("push", "--quiet", "origin", f"{merged}:refs/heads/{MAIN}", check=False)
        if push.returncode != 0:
            # Someone moved main on the remote while the gate merged; the
            # answer is the same as for a conflict, since merging the new
            # main in is again what the branch needs.
            return MergeOutcome(False, main, main_moved=True,
                                reason=f"the push of {MAIN} was refused: {last_line(push)}")
        return MergeOutcome(True, merged)

    def standing(self, branch):
        """How the branch stands to main now; the caller has fetched."""
        main = self.commit_of(MAIN_REF)
        tip = self.branch_tip(branch)
        if not tip or not main:
            return Standing("no branch" if not tip else "unknown", main)
        if self.is_ancestor(tip, main):
            return Standing("nothing new", main)
        if self.is_ancestor(main, tip):
            return Standing("clean", main)
        probe = self.git("merge-tree", "--write-tree", "--name-only", "--no-messages", main, tip, check=False)
        if probe.returncode == 0:
            return Standing("behind", main)
        if probe.returncode == 1:
            return Standing("conflicting", main, tuple(sorted(set(probe.stdout.splitlines()[1:]))))
        return Standing("unknown", main)


def queue_of(run):
    return run.state[QUEUE_KEY]


def gate_of(run):
    gate = run.state.get(GATE_KEY)
    if gate is None:
        raise ToolError("this run has no git_remote_host, so the merge gate cannot reach the shared "
                        "remote; tell the operator", EXIT_GATE_FAILED)
    return gate


def issue_number(call):
    """The issue argument as a number; "#3" and "3" are read as 3, since an
    agent may write an issue the way the board does."""
    value = call.arg("issue", required=True)
    text = str(value).strip().lstrip("#")
    if isinstance(value, bool) or not text.isdigit():
        raise ToolError(f"issue must be an issue number, such as 1, not {value!r}")
    return int(text)


def standings(run, branches):
    """Each branch's standing and main's commit, read live from the remote;
    unknown for all when the run has no remote or git fails."""
    gate = run.state.get(GATE_KEY)
    if gate is None:
        return "", {}
    with gate.lock:
        try:
            gate.fetch()
            return gate.commit_of(MAIN_REF), {branch: gate.standing(branch) for branch in branches}
        except ToolError:
            return "", {}


# The tools.

def claim_issue(call):
    run, me = call.run, call.agent
    number = issue_number(call)
    with run.lock:
        issue = claimable(queue_of(run), number, me)
        issue.claimant = me
    run.emit("claim", me, issue=number)
    branch = branch_of(number)
    return (f"claimed #{number}: it is yours until your pull request is marked ready. Next: start "
            f"{branch} from the base ref {queue_of(run).base_ref}, commit, push it "
            f"(git push -u team {branch}), and open the pull request with sdlc-pr {number}.")


def claimable(queue, number, me):
    """The issue, when `me` may claim it now; otherwise the refusal says why."""
    issue = queue.issue(number)
    if not queue.open_queue and issue.given_to != me:
        mine = ", ".join(f"#{n}" for n in queue.given_to(me)) or "none"
        raise ToolError(f"#{number} was given to {issue.given_to}; in this run each agent claims only "
                        f"its own issue, and yours is {mine}", EXIT_SOMEONE_ELSES)
    if issue.closed:
        raise ToolError(f"#{number} is closed: its pull request has merged")
    if issue.claimant == me:
        raise ToolError(f"you already hold #{number}; carry on with {branch_of(number)}")
    if issue.claimant:
        raise ToolError(f"#{number} is claimed by {issue.claimant}: a claim is first come, first served, "
                        f"so another agent has it; pick another issue", EXIT_SOMEONE_ELSES)
    return issue


def open_pull_request(call):
    run, me = call.run, call.agent
    number = issue_number(call)
    with run.lock:
        check_can_open(queue_of(run), number, me)
    gate = gate_of(run)
    branch = branch_of(number)
    with gate.lock:
        gate.fetch()
        if not gate.branch_tip(branch):
            raise ToolError(f"{branch} is not on team yet; push it first (git push -u team {branch}), "
                            f"then open the pull request")
    with run.lock:
        check_can_open(queue_of(run), number, me)
        title = str(call.arg("title") or queue_of(run).issues[number].title)
        queue_of(run).pulls[number] = PullRequest(number, me, title)
    run.emit("pr_opened", me, issue=number)
    return (f"opened the pull request for #{number} from {branch} to {MAIN}, as a draft. Push often; "
            f"when the feature is done, mark it ready with sdlc-ready {number}.")


def check_can_open(queue, number, me):
    issue = queue.issue(number)
    if not issue.claimant:
        raise ToolError(f"only the agent holding the claim on #{number} opens its pull request, and "
                        f"nobody has claimed it; claim it first with sdlc-claim {number}")
    if issue.claimant != me:
        raise ToolError(f"only the agent holding the claim on #{number} opens its pull request, and "
                        f"it is claimed by {issue.claimant}", EXIT_SOMEONE_ELSES)
    if number in queue.pulls:
        raise ToolError(f"#{number} already has a pull request ({queue.pulls[number].state}); "
                        f"one issue, one pull request")


def mark_ready(call):
    run, me = call.run, call.agent
    number = issue_number(call)
    gate = gate_of(run)
    with gate.lock:
        with run.lock:
            pull = pull_to_ready(queue_of(run), number, me)
        tip, main = gate.prepare(branch_of(number))
        with run.lock:
            pull.state = "ready"
            pull.attempts += 1
            queue_of(run).issues[number].claim_released = True
        run.emit("ready", me, issue=number)
        run.emit("merge_attempt", me, issue=number, branch_commit=tip, main_before=main)
        try:
            outcome = gate.merge(tip, main, f"Merge {branch_of(number)}: closes #{number}")
        except ToolError as problem:
            run.emit("merge_refused", me, issue=number, reason=str(problem))
            raise
        # Recorded before the gate's lock is let go, so the next call to
        # reach the gate sees this pull request as it now is.
        return record_outcome(run, me, pull, outcome)


def pull_to_ready(queue, number, me):
    pull = queue.pull(number)
    if pull.author != me:
        raise ToolError(f"the pull request for #{number} is {pull.author}'s; only its author marks it ready",
                        EXIT_SOMEONE_ELSES)
    if pull.state == "merged":
        raise ToolError(f"the pull request for #{number} has already merged, as {short(pull.merge_commit)}")
    return pull


def record_outcome(run, me, pull, outcome):
    """Write what the gate did into the queue and the events, and answer."""
    number, branch = pull.issue, branch_of(pull.issue)
    if outcome.merged:
        with run.lock:
            pull.state, pull.merge_commit, pull.conflicts = "merged", outcome.main, ()
            queue_of(run).issues[number].closed = True
        run.emit("merge", me, issue=number, commit=outcome.main)
        return merged_text(run, pull)
    if outcome.main_moved:
        with run.lock:
            pull.state, pull.conflicts = "main moved", outcome.conflicts
        kind = "merge_conflict" if outcome.conflicts else "merge_refused"
        run.emit(kind, me, issue=number, files=list(outcome.conflicts), reason=outcome.reason)
        raise ToolError(main_moved_text(outcome), EXIT_MAIN_MOVED)
    run.emit("merge_refused", me, issue=number, reason=outcome.reason)
    raise ToolError(f"the merge gate could not merge {branch} into {MAIN}: {outcome.reason}. {MAIN} was "
                    f"left as it was; fix what git names, push, and call mark_ready again.")


def merged_text(run, pull):
    base = queue_of(run).base_ref
    return (f"merged: {branch_of(pull.issue)} is in {MAIN} as {short(pull.merge_commit)}, and #{pull.issue} "
            f"is closed. Build your patch from the integrated main: git fetch team && "
            f"git diff {base} team/{MAIN} > patch.txt")


def main_moved_text(outcome):
    lines = [MAIN_MOVED + "."]
    if outcome.conflicts:
        lines.append(f"Conflicting files: {', '.join(outcome.conflicts)}.")
    else:
        lines.append(f"Why: {outcome.reason}.")
    lines.append(f"{MAIN} is still at {short(outcome.main)}; the gate left it as it was.")
    return "\n".join(lines)


def pull_request_status(call):
    run = call.run
    number = issue_number(call)
    with run.lock:
        issue = queue_of(run).issue(number)
        if number not in queue_of(run).pulls:
            holder = f"claimed by {issue.claimant}" if issue.claimant else "not claimed yet"
            return f"#{number} has no pull request yet; the issue is {holder} ({issue.label()})."
    main, live = standings(run, [branch_of(number)])
    with run.lock:
        pull = queue_of(run).pulls[number]
        return status_text(pull, live.get(branch_of(number)), main)


def status_text(pull, standing, main):
    lines = [f"Pull request for #{pull.issue}: {branch_of(pull.issue)} -> {MAIN}, by {pull.author}, "
             f"{pull.state}. Merge attempts: {pull.attempts}."]
    if pull.state == "merged":
        lines.append(f"Merged into {MAIN} as {short(pull.merge_commit)}.")
        return "\n".join(lines)
    if pull.state == "main moved" and pull.conflicts:
        lines.append(f"The gate's last answer was main moved, with conflicts in {', '.join(pull.conflicts)}.")
    lines.append(standing_sentence(standing, main, pull.issue))
    return "\n".join(lines)


def standing_sentence(standing, main, number):
    branch = branch_of(number)
    if standing is None:
        return f"Against {MAIN}: unknown; the gate could not read the shared remote."
    at = f"{MAIN} at {short(main)}"
    sentences = {
        "clean": f"Against {at}: clean, up to date with {MAIN} and merging cleanly.",
        "behind": f"Against {at}: behind; {MAIN} moved since {branch} branched, but it still merges "
                  f"cleanly, so the gate can merge it as it is.",
        "conflicting": f"Against {at}: conflicting; main moved and {branch} no longer merges cleanly with it "
                       f"(conflicts in {', '.join(standing.conflicts) or 'some files'}). Merge team/{MAIN} "
                       f"into your branch, resolve the conflicts and push before mark_ready.",
        "nothing new": f"Against {at}: nothing new; every commit on {branch} is already in {MAIN}.",
        "no branch": f"Against {at}: {branch} is not on team; push it.",
    }
    return sentences.get(standing.word, f"Against {at}: unknown.")


def board(call):
    run = call.run
    with run.lock:
        branches = [branch_of(n) for n, pull in queue_of(run).pulls.items() if pull.state != "merged"]
    main, live = standings(run, branches)
    with run.lock:
        return board_text(queue_of(run), live, main)


def table(header, rows):
    """A markdown table with the template's "(none)" row when empty. A pipe
    in a cell is escaped, since a title comes from a spec and may hold one."""
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    escaped = [[cell.replace("|", "\\|") for cell in row] for row in rows]
    lines += ["| " + " | ".join(row) + " |" for row in escaped] or ["| (none) |"]
    return "\n".join(lines)


def board_text(queue, live, main):
    issues = queue.issues.values()
    pulls = [queue.pulls[n] for n in sorted(queue.pulls)]
    sections = [
        "# Board",
        f"As of commit {short(main)} on {MAIN}; the run began at the base ref {short(queue.base_ref)}. "
        f"One issue, one branch claude/<issue>, one pull request; only the merge gate moves {MAIN}.",
        "## Issues (one per feature)",
        table(["Issue", "Title", "Given to", "Label"],
              [[f"#{i.number}", i.title, i.given_to, i.label()] for i in issues]),
        "## Claims (held until the pull request is marked ready)",
        table(["Issue", "Branch", "Agent", "Pull request"],
              [[f"#{i.number}", branch_of(i.number), i.claimant, queue.pulls[i.number].state
                if i.number in queue.pulls else "none yet"]
               for i in issues if i.claimant and not i.claim_released]),
        "## Pull requests",
        table(["Issue", "Branch", "Author", "State", "Against main", "Merge attempts"],
              [pull_row(pull, live) for pull in pulls]),
        "## Queue (open issues nobody has claimed)",
        table(["Issue", "Title", "Given to"],
              [[f"#{i.number}", i.title, i.given_to] for i in issues if i.label() == "todo"]),
    ]
    return "\n\n".join(sections)


def pull_row(pull, live):
    if pull.state == "merged":
        against = f"merged as {short(pull.merge_commit)}"
    else:
        standing = live.get(branch_of(pull.issue))
        against = standing.word if standing else "unknown"
        if standing and standing.conflicts:
            against += f" ({', '.join(standing.conflicts)})"
    return [f"#{pull.issue}", branch_of(pull.issue), pull.author, pull.state, against, str(pull.attempts)]


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
        main = remote_main(str(options["git_remote_host"]))
        if main:
            return main
    return f"team/{MAIN}"


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
    description = ("This template's claim protocol: one agent per feature, each claiming its issue, "
                   "working on claude/<issue> on a shared remote and landing it by pull request through "
                   "a merge gate.")
    needs = ("git_remote",)

    def layout(self, features):
        # Numbered as the coop arms number them, lowest feature first, so a
        # task pair has the same agent1 under every arm.
        return [AgentSpec(f"agent{i}", "peer", (number,)) for i, number in enumerate(sorted(features), 1)]

    def setup(self, run):
        given = {number: spec.id for spec in run.agents.values() for number in spec.features}
        titles = {number: issue_title(run.specs.get(number), number) for number in given}
        run.state[QUEUE_KEY] = Queue(given, titles, bool(run.options.get("open_queue")),
                                     resolve_base_ref(run.options))
        remote = run.options.get("git_remote_host")
        run.state[GATE_KEY] = Gate(str(remote)) if remote else None

    def tools(self, run, agent):
        return sdlc_tools() + kit.messaging_tools(wait=True)

    def prompt(self, run, agent, specs):
        # The spec as CooperBench hands it over, unstripped, as the coop arms do.
        spec = SECTION_RULE.join(specs[number] for number in run.agents[agent].features)
        block = protocol_block(run, agent, list(self.tools_for(run, agent).values()))
        return SECTION_RULE.join([spec, block, SUBMISSION_BLOCK])

    def teardown(self, run):
        # The hub calls this outside run.lock, so waiting here for a merge in
        # progress, which holds the gate's lock, cannot deadlock.
        gate = run.state.get(GATE_KEY)
        if gate is not None:
            gate.close()

    def metrics(self, run):
        return {**super().metrics(run), **kit.message_metrics(run), "tool_calls": tool_call_counts(run),
                "claims": count_events(run, "claim"), "prs_opened": count_events(run, "pr_opened"),
                "merge_attempts": count_events(run, "merge_attempt"), "merges": count_events(run, "merge"),
                "merge_conflicts": count_events(run, "merge_conflict"),
                "time_to_first_merge_seconds": first_merge_seconds(run)}
