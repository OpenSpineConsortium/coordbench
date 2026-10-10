"""The sdlc-watch arm: the sdlc arm, with every agent watching its peers' pull requests.

In CooperBench's coop setting the agents cannot see each other's code, and
the authors' own later ablation found a shared git remote far ahead of
messages alone (56% against 26%, docs/research.md). The sdlc arm already
has the remote: every branch claude/<issue> and its pull request are on
`team` while the work goes on. What it does not do is put them in front of
the agents. Its board and pull_request_status set each branch beside main
only, so two drafts that change the same lines, or that each write the
same helper, find out at the merge gate, after both are done, when the
second is answered "main moved".

This arm keeps everything the sdlc arm has, loaded from that arm's own
directory as coop-git loads coop, so the two arms differ by exactly this:

- a tool, peer_pull_requests (sdlc-peers in the shell), that sets each
  peer's pull request beside the caller's own branch, read live from the
  remote in one fetch: the files the peer's branch changes, the files both
  branches change and the lines each touched there, whether the two
  branches merge cleanly as they stand (git merge-tree, as the gate's own
  standing is read against main), the lines both added, which is how two
  agents writing the same thing look in a diff, and whether the peer
  pushed since the caller last looked;
- a section of the prompt asking for the habits that make the tool worth
  calling, the same ones that make open drafts useful on GitHub: open the
  draft as soon as there is a first commit, push after each step that
  works, look at the peers' pull requests after every push and before
  marking ready, and settle an overlap while both pull requests are drafts.

Each branch's change is read from where it left main, not from the base
ref: once one pull request has merged and the other has brought main in,
the base ref would count the first one's work as the second's. Beside a
peer, each side's change is read from where it left both main and the
other branch (git merge-base A B C takes the merge base of A and a merge
of B and C), so an agent that took its peer's work by merging the peer's
branch, as the prompt suggests, is not then told that everything it took
overlaps.

Every look is recorded (a peer_check event, with what it showed), so a
run says how often the agents looked, when overlap was first seen, and
whether a conflict between two branches was seen before the gate met it.

The world's lock is taken outside the run's lock and never while holding
it, as the sdlc arm requires.
"""

import importlib.util
import re
from dataclasses import dataclass, field

import kit
from arm import SUBMISSION_BLOCK, Tool, builtin_arms_dir


def load_sdlc_module():
    """The built-in sdlc arm's module, found through coordbench's own arms
    directory, so a copy of this directory elsewhere still finds it."""
    path = builtin_arms_dir() / "sdlc" / "arm.py"
    spec = importlib.util.spec_from_file_location("coordbench_arm_sdlc", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sdlc = load_sdlc_module()
POLICY = sdlc.POLICY
SECTION_RULE = sdlc.SECTION_RULE

SEEN_KEY = "sdlc_watch_seen"
# An added line shorter than this ("}", "return x", a blank) says nothing
# about two agents writing the same thing, so it is not counted among the
# lines both added.
SHORTEST_SHARED_LINE = 16
EXAMPLES_SHOWN = 3
RANGES_SHOWN = 6
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


# One branch's own change, read from the remote.

@dataclass
class Change:
    """What a branch changed since it left main: lines added and removed
    per file, the ranges of main's lines it touched (main as the branch
    left it), and the lines it added, stripped."""

    files: dict = field(default_factory=dict)
    ranges: dict = field(default_factory=dict)
    added: set = field(default_factory=set)


def fork_point(world, tip, *others):
    """Where tip left the others, taken together: git merge-base with three
    or more commits gives the merge base of the first and a merge of the
    rest. "" when they share no history."""
    result = world.git("merge-base", tip, *others, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def change_since(world, since, tip):
    """What tip changed after since."""
    if not tip or not since:
        return Change()
    numstat = world.git("diff", "--numstat", "-z", "--no-renames", since, tip).stdout
    hunks = world.git("diff", "-U0", "--no-color", "--no-renames", "--no-ext-diff", since, tip).stdout
    ranges, added = parse_unified_diff(hunks)
    return Change(parse_numstat(numstat), ranges, added)


def own_change(world, tip, *others):
    """What tip changed since it left the others, taken together."""
    return change_since(world, fork_point(world, tip, *others) if tip else "", tip)


def parse_numstat(text):
    """{path: (added, removed)} from `git diff --numstat -z`; a binary
    file's counts are None."""
    files = {}
    for entry in text.split("\0"):
        parts = entry.split("\t", 2)
        if len(parts) == 3:
            added, removed, path = parts
            files[path] = (int(added) if added.isdigit() else None, int(removed) if removed.isdigit() else None)
    return files


def parse_unified_diff(text):
    """The touched ranges per file, in the old side's numbering, and every
    added line long enough to mean something, from a diff made with -U0.
    A pure insertion after line n is the range (n, n)."""
    ranges, added = {}, set()
    path, in_header = None, False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            path, in_header = None, True
        elif in_header and not line.startswith("@@"):
            path = header_path(line, path)
        elif line.startswith("@@"):
            in_header = False
            hunk = HUNK.match(line)
            if hunk and path:
                ranges.setdefault(path, []).append(hunk_range(hunk))
        elif line.startswith("+") and path:
            text_added = line[1:].strip()
            if len(text_added) >= SHORTEST_SHARED_LINE:
                added.add(text_added)
    return ranges, added


def header_path(line, path):
    """The file a diff's header names: the new side's, or the old side's for
    a deleted file, whose new side is /dev/null."""
    if line.startswith("--- a/"):
        return line[len("--- a/"):]
    if line.startswith("+++ b/"):
        return line[len("+++ b/"):]
    return path


def hunk_range(hunk):
    start = int(hunk.group(1))
    count = int(hunk.group(2)) if hunk.group(2) is not None else 1
    return (start, start + count - 1) if count else (start, start)


def trial_merge(world, mine, theirs):
    """The files two branches conflict in if merged as they stand: () when
    they merge cleanly, None when git could not tell."""
    probe = world.git("merge-tree", "--write-tree", "--name-only", "--no-messages", mine, theirs, check=False)
    if probe.returncode == 0:
        return ()
    if probe.returncode == 1:
        return tuple(sorted({name for name in probe.stdout.splitlines()[1:] if name.strip()}))
    return None


# What one look shows.

@dataclass
class PeerView:
    issue: int
    author: str
    state: str
    tip: str = ""
    moved: bool = False
    change: Change = field(default_factory=Change)
    mine: Change = field(default_factory=Change)
    entangled: bool = False
    shared: tuple = ()
    conflicts: tuple = None
    both_added: tuple = ()


def my_issue(queue, me):
    """The issue the caller claimed, or else the one it was given."""
    claimed = [n for n, issue in queue.issues.items() if issue.claimant == me]
    given = queue.given_to(me)
    return (claimed or given or [None])[0]


def peer_pulls(queue, me):
    """(issue, author, state) for every issue another agent holds: its pull
    request, or its claim when no pull request is open yet."""
    peers = []
    for number, issue in sorted(queue.issues.items()):
        pull = queue.pulls.get(number)
        author = pull.author if pull else issue.claimant
        if author and author != me:
            peers.append((number, author, pull.state if pull else "claimed, no pull request yet"))
    return peers


def look(world, mine, peers, seen):
    """My branch's tip and change, and a PeerView per peer, from one fetch.
    The caller holds the world."""
    world.refresh()
    main = world.main_tip()
    my_tip = world.branch_tip(POLICY.branch_of(mine)) if mine is not None else ""
    views = [peer_view(world, main, my_tip, peer, seen) for peer in peers]
    return my_tip, own_change(world, my_tip, main), views


def peer_view(world, main, my_tip, peer, seen):
    """One peer beside me: each side's change from where it left main and
    the other branch, so what one took from the other by merging is not
    counted as overlap."""
    number, author, state = peer
    view = PeerView(number, author, state, tip=world.branch_tip(POLICY.branch_of(number)))
    if not view.tip or state == sdlc.gate.MERGED:
        return view
    view.moved = seen.get(number) != view.tip
    if not my_tip:
        view.change = own_change(world, view.tip, main)
        return view
    view.change = own_change(world, view.tip, main, my_tip)
    view.mine = own_change(world, my_tip, main, view.tip)
    view.entangled = shares_work(world, main, my_tip, view.tip)
    view.shared = tuple(sorted(set(view.change.files) & set(view.mine.files)))
    if view.shared:
        view.conflicts = trial_merge(world, my_tip, view.tip)
    view.both_added = tuple(sorted(view.change.added & view.mine.added))
    return view


def shares_work(world, main, mine, theirs):
    """Whether one branch has merged commits of the other that main lacks."""
    common = fork_point(world, mine, theirs)
    return bool(common) and not world.is_ancestor(common, main)


# The answer, in words.

def describe(mine, my_tip, my_change, views):
    lines = [describe_mine(mine, my_tip, my_change)]
    if not views:
        lines.append("No peer has claimed an issue yet. Look again after your next push.")
    for view in views:
        lines.append("")
        lines.extend(describe_peer(view, bool(my_tip)))
    if any(view.shared or view.both_added for view in views):
        lines += ["", "Settle overlap while both pull requests are drafts: build on the version that exists "
                      "rather than write a second one (merge their branch into yours, or agree by message who "
                      "keeps it), and agree who changes which lines of a file you both change."]
    return "\n".join(lines)


def describe_mine(mine, my_tip, my_change):
    if mine is None:
        return "You hold no issue, so nothing of yours is compared."
    branch = POLICY.branch_of(mine)
    if not my_tip:
        return f"You have not pushed {branch} yet, so nothing of yours is compared; push it and look again."
    return (f"Your branch {branch} at {my_tip[:7]} changes {count(len(my_change.files), 'file')} "
            f"since it left main{listed(sorted(my_change.files))}.")


def describe_peer(view, compared):
    branch = POLICY.branch_of(view.issue)
    head = f"#{view.issue} ({view.author}, {view.state}): {branch}"
    if view.state == sdlc.gate.MERGED:
        return [f"{head} has merged into main; your pull request's status (sdlc-status) says whether your "
                f"branch still merges cleanly with main."]
    if not view.tip:
        return [f"{head} is not pushed yet."]
    news = "pushed since your last look" if view.moved else "nothing new since your last look"
    lines = [f"{head} at {view.tip[:7]}, {news}."]
    if view.entangled and not view.change.files:
        return lines + ["  Everything on it is already in your branch or in main."]
    lines.append(f"  It changes {count(len(view.change.files), 'file')}{listed(file_counts(view.change.files))}.")
    if view.entangled:
        lines.append("  One of your branches has merged work of the other; only what the two do not share "
                     "is set side by side.")
    if compared:
        lines.extend(describe_overlap(view))
    if view.change.files:
        target = view.shared[0] if view.shared else "<file>"
        lines.append(f"  Read their change to a file: git fetch team && git diff team/main...team/{branch} -- "
                     f"{target}")
    return lines


def describe_overlap(view):
    if not view.shared:
        lines = ["  You change none of the same files."]
    else:
        lines = [f"  You both change {shared_file(path, view)}." for path in view.shared]
        lines.append("  " + merge_words(view.conflicts))
    if view.both_added:
        examples = ", ".join(f'"{line}"' for line in view.both_added[:EXAMPLES_SHOWN])
        lines.append(f"  Lines you both added: {len(view.both_added)}, such as {examples}.")
    return lines


def shared_file(path, view):
    return (f"{path}: their lines {ranges_text(view.change.ranges.get(path))}, "
            f"yours {ranges_text(view.mine.ranges.get(path))}")


def merge_words(conflicts):
    if conflicts is None:
        return "Whether your branch and theirs merge cleanly could not be told."
    if conflicts:
        return f"Your branch and theirs conflict, as they stand, in {', '.join(conflicts)}."
    return "Your branch and theirs merge cleanly as they stand."


def ranges_text(ranges):
    if not ranges:
        return "none"
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    shown = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in merged[:RANGES_SHOWN])
    more = len(merged) - RANGES_SHOWN
    return shown + (f" and {more} more" if more > 0 else "")


def file_counts(files):
    return [f"{path} ({stat_text(*files[path])})" for path in sorted(files)]


def stat_text(added, removed):
    return "binary" if added is None else f"+{added} -{removed}"


def count(number, noun):
    return f"{number} {noun}" + ("" if number == 1 else "s")


def listed(items):
    return f": {', '.join(items)}" if items else ""


# The tool.

def record_of(view):
    """What a look showed of one peer, for the peer_check event."""
    return {"issue": view.issue, "agent": view.author, "state": view.state, "pushed": bool(view.tip),
            "moved": view.moved, "files": len(view.change.files), "entangled": view.entangled,
            "shared": list(view.shared),
            "conflicts": None if view.conflicts is None else list(view.conflicts),
            "both_added": len(view.both_added)}


@sdlc.refusals_as_tool_errors
def peer_pull_requests(call):
    run, me = call.run, call.agent
    with run.lock:
        queue = sdlc.queue_of(run)
        mine, peers = my_issue(queue, me), peer_pulls(queue, me)
        seen = dict(run.state.setdefault(SEEN_KEY, {}).get(me, {}))
    world = sdlc.world_of(run)
    with world:
        my_tip, my_change, views = look(world, mine, peers, seen)
    with run.lock:
        run.state[SEEN_KEY][me] = {**seen, **{view.issue: view.tip for view in views if view.tip}}
    run.emit("peer_check", me, issue=mine, pushed=bool(my_tip), files=len(my_change.files),
             peers=[record_of(view) for view in views])
    return describe(mine, my_tip, my_change, views)


def peer_tool():
    return Tool("peer_pull_requests", "Every peer's pull request beside your own branch, read from the "
                "shared remote now: the files each changes, the files you both change and the lines each of "
                "you touched there, whether your branch and theirs merge cleanly as they stand, the lines "
                "you both added, and whether they pushed since you last looked.",
                peer_pull_requests, alias="sdlc-peers")


# The prompt.

WATCH_BLOCK = """## Watching your peers' pull requests

Your peers work at the same time as you, on features that may touch the
same code, and their branches and pull requests are on `team` while they
work. `sdlc-peers` (the MCP tool `peer_pull_requests`) sets every peer's
pull request beside your own branch: the files it changes, the files you
both change and the lines each of you touched there, whether your branch
and theirs merge cleanly as they stand, and the lines you both added,
which usually means you are both writing the same thing.

1. Open your draft early. As soon as `claude/{issue}` has a first commit,
   even a small one, push it and open the pull request with
   `sdlc-pr {issue}`; then push again after each step that works. Your
   peers see only what you have pushed.
2. Look often: run `sdlc-peers` once your pull request is open, again
   after each push, and again before `sdlc-ready {issue}`. It says which
   peers pushed since your last look.
3. Act on overlap while both pull requests are drafts:
   - Lines you both added, or a helper you are both writing: build on the
     version that exists instead of writing a second one. Merge their
     branch into yours (`git fetch team && git merge team/claude/<their issue>`)
     or agree with them by message which of you keeps it.
   - A conflict between your branch and theirs: read their change to the
     file it names (the command `sdlc-peers` prints) and settle with them
     now who changes which lines, so neither of you meets it at the merge
     gate.
   - Files you both change without a conflict: read their change anyway,
     since two edits that merge cleanly can still break each other.
4. Never push to their branch: merging theirs into yours is how you take
   their work, and the merge gate lands both."""


def watch_block(run, agent):
    mine = sdlc.queue_of(run).given_to(agent)
    return WATCH_BLOCK.format(issue=mine[0] if mine else "<issue>")


# The metrics.

def peer_checks(run):
    return [event for event in run.events if event["kind"] == "peer_check"]


def first_time(checks, showed):
    times = [check["t"] for check in checks if any(showed(peer) for peer in check["peers"])]
    return min(times) if times else None


def checks_by_agent(checks):
    counts = {}
    for check in checks:
        counts[check["agent"]] = counts.get(check["agent"], 0) + 1
    return counts


def pull_request_opened_seconds(run):
    """When each agent opened its pull request, seconds from the start: how
    early the drafts went up."""
    opened = {}
    for event in run.events:
        if event["kind"] == "pr_opened":
            opened.setdefault(event["agent"], event["t"])
    return opened


def watch_metrics(run):
    checks = peer_checks(run)
    both_added = [peer["both_added"] for check in checks for peer in check["peers"]]
    return {"peer_checks": len(checks), "peer_checks_by_agent": checks_by_agent(checks),
            "overlap_first_seen_seconds": first_time(checks, lambda peer: peer["shared"] or peer["both_added"]),
            "conflict_first_seen_seconds": first_time(checks, lambda peer: peer["conflicts"]),
            "most_lines_both_added": max(both_added, default=0),
            "pr_opened_seconds": pull_request_opened_seconds(run)}


class SdlcWatchArm(sdlc.SdlcArm):
    name = "sdlc-watch"
    description = ("The sdlc arm with each agent watching its peers' open pull requests while it works: "
                   "early drafts, frequent pushes, and a tool that sets every peer's branch beside the "
                   "agent's own, so overlap and duplicated work show up before the merge gate.")
    needs = ("git_remote",)

    def tools(self, run, agent):
        # The peer tool sits with the protocol's tools, before the comments,
        # so the prompt's table reads in the order the work meets them.
        return sdlc.sdlc_tools() + [peer_tool()] + kit.messaging_tools(wait=True)

    def prompt(self, run, agent, specs):
        spec = SECTION_RULE.join(specs[number] for number in run.agents[agent].features)
        block = sdlc.protocol_block(run, agent, list(self.tools_for(run, agent).values()))
        return SECTION_RULE.join([spec, block, watch_block(run, agent), SUBMISSION_BLOCK])

    def metrics(self, run):
        return {**super().metrics(run), **watch_metrics(run)}
