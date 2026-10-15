"""Copy a tag's results into a results directory for the repository: for
each pair, every file but the session transcripts, the trajectories and
the message stores, which are large and hold the agents' whole turns;
plus the queue file. The whole logs directory, transcripts included, is
kept outside the repository as an archive.

The copy is held to the repository's plain-text rule (ASCII, LF endings,
a final newline), since scripts/check.sh reads every file: a JSON file is
written again with its non-ASCII characters escaped, which keeps its
value; another text file gets the final newline it lacks; a file that
still holds a byte outside ASCII or a carriage return is left out and
named on stdout, so nothing in the copy is silently changed.

    python3 bundle.py --logs LOG_DIR --tag TAG --out DIR [--arms solo,team,xblossom]
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

LEFT_OUT = ("_session.jsonl", "_traj.json", "trajectory.json.gz", "conversation.json",
            "conversation_store.json", "finish.out", "finish.pid", ".session-prompt.md", "_prompt.md",
            ".out", "plan.json", "_session_outcome.json", ".tar.gz")
# The wrappers under bin/ and plan.json carry the hub's run tokens, the
# outcome files the orchestrating session's transcript paths, the hub
# directory a second copy of each run's events, and a previous-*.tar.gz
# an earlier attempt that finish set aside (the attempt that counts is
# the one in the directory): all left out.
LEFT_OUT_DIRS = ("bin", "hub")
ARMS = ("solo", "coop", "coop-nocomm", "sdlc", "sdlc-watch", "coop-git", "team", "xblossom")


def wanted(path):
    return path.is_file() and not any(path.name.endswith(suffix) for suffix in LEFT_OUT) \
        and not any(part in LEFT_OUT_DIRS for part in path.parts)


def plain_text(data, name):
    """The bytes to write for a file, or None when the file cannot be
    made plain text without changing what it says."""
    if name.endswith((".json", ".jsonl")) and (any(b > 127 for b in data) or not data.endswith(b"\n")):
        try:
            lines = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()] \
                if name.endswith(".jsonl") else [json.loads(data.decode("utf-8"))]
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        data = "".join(json.dumps(value, ensure_ascii=True) + "\n" for value in lines).encode()
    if any(b > 127 for b in data) or b"\r" in data:
        return None
    if data and not data.endswith(b"\n"):
        data += b"\n"
    return data


def copy_plain(path, target):
    """True when the file was copied; False when it was left out."""
    data = plain_text(path.read_bytes(), path.name)
    if data is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    shutil.copystat(path, target)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arms", default=",".join(ARMS),
                        help="the arms by name, an arm given by its directory by the directory's name")
    args = parser.parse_args(argv)
    copied, left_out = 0, []
    for run_dir in sorted(args.logs / f"{args.tag}-{arm}" for arm in args.arms.split(",")):
        if not run_dir.is_dir():
            continue
        for path in sorted(run_dir.rglob("*")):
            if wanted(path):
                if copy_plain(path, args.out / "raw" / path.relative_to(args.logs)):
                    copied += 1
                else:
                    left_out.append(path.relative_to(args.logs))
    queue = args.logs / f"{args.tag}-queue.jsonl"
    if queue.exists():
        args.out.mkdir(parents=True, exist_ok=True)
        if not copy_plain(queue, args.out / "queue.jsonl"):
            left_out.append(queue.name)
    for name in left_out:
        print(f"left out, not plain text: {name}")
    print(f"copied {copied} files to {args.out}; {len(left_out)} left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
