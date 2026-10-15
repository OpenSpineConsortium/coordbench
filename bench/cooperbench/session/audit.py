"""What the session route did to a run, beside what the arm did, read
from the pair directory and the subagents' transcripts once run.py finish
has scored the pair, and kept in audit.json, which summary.py reads.

    python3 audit.py RUN_DIR [RUN_DIR ...]      write audit.json in each

lane.py finish writes it for every run; run by hand it fills in runs
finished before it existed, as long as the session's transcripts are still
on disk (attempts needs them; the rest reads the pair's own copies).

For each agent:
- calls: the API calls its copied transcript holds, one per message id,
  and first_context: the input, cache read and cache write of the first
  call, the prompt every call sends again before any work (the session's
  system prompt, tools and instructions, which a bench agent inherits, and
  its spawn prompt). calls times first_context is the part of the agent's
  input tokens the route fixed; what is left is what the work took, about.
- efforts: the effort levels its calls ran at, which a subagent inherits
  from the orchestrating session.
- refusals: tool calls the session refused, as against the command's own
  failure: a permission decision other than accept, or Claude Code's own
  guard ("Blocked: ..."), with up to three examples. A claude-cli agent
  meets neither, so they are the route's, not the arm's.
- disk_full: tool results that say the disk was full.
- attempts: how many subagent transcripts carry the agent's run token in
  a reply or in the prompt. More than one is a respawn: the copied
  transcript is the newest, so the agent's token count misses the others.

For the pair, disk_full also counts the lines of finish.out and the
agents' logs that say the disk was full, and harness_error says why the
run measured the harness rather than the arm, or is None: an agent the
harness lost, a full disk, or a respawn. No transcript path is kept, since
the file is copied into the repository's results.
"""

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent / "coordbench"))
import drivers  # noqa: E402 - coordbench's own module, found by its directory

# A full disk as the error reaches a command's output: "<path>: no space
# left on device", "[Errno 28] No space left on device", Claude Code's own
# "... is full (0MB free)" for its output directory, a quota. A bare
# ENOSPC is left out, since source code an agent reads names it.
DISK_FULL = re.compile(r"(?:: |\] )no space left on device|is full \(0MB free\)|disk quota exceeded",
                       re.IGNORECASE)
GUARD = re.compile(r"^\s*(<tool_use_error>)?\s*Blocked:")
EXAMPLES = 3


def json_events(path):
    """The JSON objects of a JSON-lines file, skipping lines that are not
    one; nothing when the file cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def transcript_facts(path):
    """Calls, first_context, efforts, refusals and disk_full from one
    copied transcript; None when there is none."""
    if not Path(path).is_file():
        return None
    calls, first_context, efforts = set(), None, []
    refusals, examples, disk_full = 0, [], 0
    for event in json_events(path):
        effort = event.get("effort")
        if isinstance(effort, str) and effort not in efforts:
            efforts.append(effort)
        decision = event.get("permissionDecision")
        if isinstance(decision, dict) and decision.get("decision") not in (None, "accept"):
            refusals += 1
            examples.append(f"permission {decision.get('decision')}: {decision.get('reasonType') or ''}".strip())
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if event.get("type") == "assistant" and message.get("id"):
            if first_context is None:
                usage = message.get("usage") or {}
                first_context = sum(int(usage.get(name) or 0) for name in
                                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            calls.add(message["id"])
        for block in message.get("content") if isinstance(message.get("content"), list) else ():
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            text = result_text(block)
            if block.get("is_error") and GUARD.match(text):
                refusals += 1
                examples.append(text.replace("<tool_use_error>", "").strip()[:160])
            if DISK_FULL.search(text):
                disk_full += 1
    return {"calls": len(calls), "first_context": first_context, "efforts": efforts,
            "refusals": refusals, "refusal_examples": examples[:EXAMPLES], "disk_full": disk_full}


def attempts_of(handle, roots=None):
    """How many subagents were spawned as this agent: the transcripts the
    session driver would take as its whose prompt names the agent's prompt
    file, as a bench agent's spawn prompt does and a lane's relay, which
    may echo the token, does not. None when the handle has no run token."""
    token = handle.get("run_token")
    if not token:
        return None
    roots = drivers.transcript_roots() if roots is None else roots
    since = float(handle.get("started") or 0) - drivers.TRANSCRIPT_CLOCK_SLACK_SECONDS
    prompt_file = handle.get("prompt_file")
    matches = drivers.find_transcripts(token, roots, since, prompt_file)
    if not prompt_file:
        return len(matches)
    return sum(1 for path in matches if drivers.names_file(prompt_of(path), prompt_file))


def prompt_of(path):
    """The user lines of a transcript before its first reply."""
    prompt = []
    for kind, message in drivers.opening_messages(path):
        if kind == "assistant":
            break
        prompt.append(drivers.content_blocks_to_text(message.get("content")))
    return " ".join(prompt)


def disk_full_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            return sum(1 for line in stream if DISK_FULL.search(line))
    except OSError:
        return 0


def agent_audit(handle, roots=None):
    copied = drivers.log_sibling(handle["log_path"], "_session.jsonl") if handle.get("log_path") else None
    facts = transcript_facts(copied) if copied else None
    record = facts or {"calls": None, "first_context": None, "efforts": [], "refusals": None,
                       "refusal_examples": [], "disk_full": None}
    record["transcript_copied"] = facts is not None
    record["attempts"] = attempts_of(handle, roots)
    return record


def harness_error(agents, pair_disk_full, harness):
    """Why the run measured the harness rather than the arm, or None."""
    lost = list((harness or {}).get("expired") or [])
    if lost:
        return f"the harness lost {', '.join(lost)}"
    if pair_disk_full or any(a.get("disk_full") for a in agents.values()):
        return "the run met a full disk"
    respawned = [name for name, a in agents.items() if (a.get("attempts") or 0) > 1]
    if respawned:
        return f"{', '.join(respawned)} ran more than once"
    return None


def audit_run(run_dir, roots=None):
    run_dir = Path(run_dir)
    plan = json.loads((run_dir / "plan.json").read_text())
    agents = {}
    for agent in plan.get("agents") or []:
        handle = agent.get("handle") or {}
        agents[agent["id"]] = agent_audit(handle, roots)
    logs = [run_dir / "finish.out", *sorted(run_dir.glob("*_stream.jsonl")), *sorted(run_dir.glob("*.log"))]
    pair_disk_full = sum(disk_full_lines(path) for path in logs)
    harness = read_json(run_dir / "harness.json")
    return {"agents": agents, "disk_full_log_lines": pair_disk_full,
            "harness_error": harness_error(agents, pair_disk_full, harness)}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def write_audit(run_dir, roots=None):
    record = audit_run(run_dir, roots)
    (Path(run_dir) / "audit.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dirs", nargs="+")
    args = parser.parse_args(argv)
    for run_dir in args.run_dirs:
        record = write_audit(run_dir)
        print(json.dumps({"run_dir": run_dir, "harness_error": record["harness_error"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
