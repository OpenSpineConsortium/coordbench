"""How much reading the two agents of a pair repeat. The agents read
the repository through docker exec (cat, sed -n, grep, head), so the
files are taken from those commands; line ranges from sed -n A,Bp."""
import json, re, statistics
from pathlib import Path

LOGS = Path("/root/cooperbench/logs")
SAMPLE = set()
for l in open(LOGS / "curse-1-queue.jsonl"):
    if l.strip():
        r = json.loads(l)
        if r.get("sample"):
            SAMPLE.add((r["repo"], str(r["task"])))
SRC = re.compile(r"(?<![\w/.-])((?:[\w-]+/)*[\w-]+\.(?:py|go|rs|js|ts|toml|cfg|c|h|typ|pyi))\b")
SED = re.compile(r"sed -n '?(\d+),(\d+)p'? ((?:[\w-]+/)*[\w-]+\.\w+)")

def scan(session):
    files, lines, usage = set(), {}, [0, 0]
    for line in open(session):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") != "assistant":
            continue
        u = r["message"].get("usage") or {}
        usage[0] += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        usage[1] += u.get("cache_read_input_tokens", 0)
        for b in r["message"].get("content") or []:
            if b.get("type") != "tool_use" or b["name"] != "Bash":
                continue
            cmd = b["input"].get("command", "")
            if "docker exec" not in cmd:
                continue
            inner = cmd.split("bash -lc", 1)[-1]
            files |= set(SRC.findall(inner))
            for a, z, f in SED.findall(inner):
                lines.setdefault(f, set()).update(range(int(a), int(z) + 1))
    return files, lines, usage

rows = []
for arm in ("coop", "sdlc", "coop-git", "team"):
    for a1 in sorted((LOGS / f"curse-1-{arm}").rglob("agent1_session.jsonl")):
        parts = a1.parent.relative_to(LOGS / f"curse-1-{arm}").parts
        if (parts[1], parts[2]) not in SAMPLE or not (a1.parent / "agent2_session.jsonl").exists():
            continue
        f1, l1, u1 = scan(a1); f2, l2, u2 = scan(a1.parent / "agent2_session.jsonl")
        both, union = f1 & f2, f1 | f2
        lb = sum(len(l1.get(f, set()) & l2.get(f, set())) for f in set(l1) | set(l2))
        lu = sum(len(l1.get(f, set()) | l2.get(f, set())) for f in set(l1) | set(l2))
        rows.append((arm, parts[1][:22], len(f1), len(f2), len(both), len(union),
                     len(both) / len(union) if union else 0, lb, lu,
                     (u1[1] + u2[1]) / (u1[0] + u2[0])))
for r in rows:
    print("%-8s %-22s files a1=%2d a2=%2d both=%2d union=%2d (%.2f) sed-lines both=%4d union=%4d cached=%.2f" % r)
for arm in ("coop", "sdlc", "coop-git", "team"):
    rs = [r for r in rows if r[0] == arm]
    lb, lu = sum(r[7] for r in rs), sum(r[8] for r in rs)
    print(f"{arm:8} pairs {len(rs)} files read by both / either: median {statistics.median(r[6] for r in rs):.2f};"
          f" sed lines both/either {lb}/{lu} = {lb/lu if lu else 0:.2f}; cached median {statistics.median(r[9] for r in rs):.2f}")
