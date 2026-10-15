"""One lane of a session-driven run: take the next item, prepare it, and
later finish it.

    python3 lane.py prepare QUEUE --worker NAME [--log-dir DIR] [--wait-image-min 30]
    python3 lane.py finish RUN_DIR --item ITEM_ID --queue QUEUE
        [--returned agent1 agent2 ...] [--dead agent3 ...]

prepare pops the first pending item of the queue, waits until its task
image is overlaid (the images are prepared by a separate loop, task by
task, so a lane never pulls one itself), runs coordbench's run.py prepare
for it with the session driver, and prints one JSON object: the plan the
orchestrating session spawns its subagents from, with each agent's prompt
file and run token, or {"done": true} when the queue is empty. A prepare
that fails marks the item failed and takes the next.

finish is run once every subagent of the pair has returned. A subagent
that returned without creating its marker is given one now, so the run
ends as it did rather than at the deadline, and the fact is kept in
harness.json beside the pair; one the harness lost (--dead) has its
deadline brought forward, so run.py finish records it as an Error at
once instead of waiting it out. Then run.py finish waits, scores and
tears down, judge.py's free heuristics run on the pair, and the queue
item takes the verdict.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
RUN_PY = ROOT / "coordbench" / "run.py"
JUDGE_PY = ROOT / "coordbench" / "judge.py"
QUEUE_PY = HERE / "queue.py"
MARKER = ".coordbench-done"


def say(text):
    print(text, file=sys.stderr, flush=True)


def queue(*args):
    """queue.py's stdout, or None when it failed, which a caller never
    reads as an empty queue."""
    out = subprocess.run([sys.executable, str(QUEUE_PY), *args], capture_output=True, text=True)
    if out.returncode != 0:
        say(out.stderr.strip())
        return None
    return out.stdout.strip()


def image_name(repo, task):
    return f"akhatua/cooperbench-{repo.removesuffix('_task').replace('_', '-')}:task{task}"


def image_ready(repo, task):
    out = subprocess.run(["docker", "image", "inspect", image_name(repo, task), "--format",
                          '{{index .Config.Labels "cooperbench.claude"}}'], capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.strip() != ""


def wait_for_image(item, minutes):
    deadline = time.time() + minutes * 60
    while not image_ready(item["repo"], item["task"]):
        if time.time() > deadline:
            return False
        time.sleep(20)
    return True


def run_token(prompt_file):
    for line in Path(prompt_file).read_text().splitlines():
        if "coordbench-run-token" in line:
            start = line.index("coordbench-run-token")
            rest = line[start:].split("`")[0]
            return rest.strip()
    return None


def clean_environment():
    env = dict(os.environ)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        env.pop(name, None)
    return env


def prepare_item(item, log_dir):
    command = [sys.executable, str(RUN_PY), "prepare", "--arm", item["arm"], "--repo", item["repo"],
               "--task", str(item["task"]), "--features", item["features"], "--driver", "session",
               "--model", item["model"], "--timeout-min", str(item["timeout_min"]),
               "--disallowed-tools", "WebFetch,WebSearch", "--name", item["run_name"]]
    if log_dir:
        command += ["--log-dir", log_dir]
    out = subprocess.run(command, capture_output=True, text=True, env=clean_environment(), cwd=str(ROOT))
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    try:
        plan = json.loads(last)
    except json.JSONDecodeError:
        plan = None
    if out.returncode != 0 or not plan or "run_dir" not in plan:
        return None, (out.stderr.strip()[-2000:] or out.stdout.strip()[-2000:] or f"exit {out.returncode}")
    for agent in plan["agents"]:
        agent["token"] = run_token(agent["prompt_file"])
    return plan, None


MAX_IMAGE_WAITS = 6


def plan_from_disk(item):
    """The plan a prepare printed earlier, rebuilt from the run directory,
    so a lane that lost the printed plan (a relay that dropped it, a
    restart) takes the same item up again instead of leaving it running
    with its containers and preparing another."""
    run_dir = item["run_dir"]
    plan = read_plan(run_dir)
    agents = []
    for agent in plan["agents"]:
        prompt_file = str(Path(run_dir) / f"{agent['id']}.session-prompt.md")
        agents.append({"id": agent["id"], "prompt_file": prompt_file, "model": item["model"],
                       "token": run_token(prompt_file)})
    return {"run_dir": run_dir, "agents": agents}


def announce(plan, item):
    plan.update({"done": False, "item": item["id"], "arm": item["arm"], "repo": item["repo"],
                 "task": item["task"], "features": item["features"], "timeout_min": item["timeout_min"]})
    print(json.dumps(plan))


def command_prepare(args):
    for _ in range(5):
        raw = queue("next", args.queue, "--worker", args.worker)
        if raw is None:
            print(json.dumps({"done": False, "error": "the queue could not be read or written"}))
            return 1
        item = json.loads(raw) if raw else {"done": True}
        if item.get("done"):
            print(json.dumps({"done": True}))
            return 0
        if item.get("prepared_at") and item.get("run_dir"):
            announce(plan_from_disk(item), item)
            return 0
        if not wait_for_image(item, args.wait_image_min):
            # The item stays the worker's; the next call takes it up again,
            # so a caller with a short command timeout can wait in steps,
            # up to a limit, after which the item fails rather than hold
            # the lane for an image that never comes.
            waits = int(item.get("image_waits") or 0) + 1
            if waits >= MAX_IMAGE_WAITS:
                queue("set", args.queue, item["id"], "status=failed",
                      f"error={json.dumps('no task image after ' + str(waits) + ' waits')}")
                continue
            queue("set", args.queue, item["id"], f"image_waits={waits}")
            print(json.dumps({"done": False, "waiting": True, "item": item["id"],
                              "image": image_name(item["repo"], item["task"])}))
            return 0
        plan, error = prepare_item(item, args.log_dir)
        if plan is None:
            say(f"prepare failed for {item['id']}: {error}")
            queue("set", args.queue, item["id"], "status=failed", f"error={json.dumps(error[-500:])}")
            continue
        queue("set", args.queue, item["id"], f"run_dir={json.dumps(plan['run_dir'])}",
              f"prepared_at={time.time()}")
        announce(plan, item)
        return 0
    print(json.dumps({"done": True, "note": "five items in a row failed to prepare"}))
    return 0


def read_plan(run_dir):
    return json.loads((Path(run_dir) / "plan.json").read_text())


def write_plan(run_dir, plan):
    path = Path(run_dir) / "plan.json"
    path.write_text(json.dumps(plan, indent=2, default=str) + "\n")
    os.chmod(path, 0o600)


def place_missing_markers(run_dir, returned, dead):
    """Give a returned subagent its marker when it left none, unless its
    deadline has passed, when it overran and is left to expire as the
    driver expires it; bring a lost one's deadline forward; say what was
    done in harness.json."""
    plan = read_plan(run_dir)
    notes = {"placed_markers": [], "overran": [], "expired": [], "at": time.time()}
    changed = False
    for agent in plan["agents"]:
        handle = agent.get("handle") or {}
        workspace = handle.get("workspace")
        if not workspace:
            continue
        marker = Path(workspace) / MARKER
        if agent["id"] in returned and not marker.exists():
            if time.time() > float(handle.get("deadline") or 0):
                notes["overran"].append(agent["id"])
            else:
                marker.touch()
                notes["placed_markers"].append(agent["id"])
        if agent["id"] in dead:
            handle["deadline"] = time.time() - 1
            notes["expired"].append(agent["id"])
            changed = True
    if changed:
        write_plan(run_dir, plan)
    (Path(run_dir) / "harness.json").write_text(json.dumps(notes, indent=2) + "\n")
    return notes


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def detach(args):
    """Run the finish in a process of its own, so a caller whose command
    is cut short (a tool's timeout) does not cut the scoring short; the
    verdict lands in <run_dir>/finish.verdict.json, which wait reads, and
    finish.pid says which process is at it, so a second detach of the
    same run, a retry after a timeout, starts nothing."""
    run_dir = Path(args.run_dir)
    pid_file = run_dir / "finish.pid"
    if (run_dir / "finish.verdict.json").exists():
        print(json.dumps({"detached": False, "run_dir": args.run_dir, "note": "a verdict exists already"}))
        return 0
    if pid_file.exists() and pid_alive(int(pid_file.read_text().strip() or 0)):
        print(json.dumps({"detached": False, "run_dir": args.run_dir, "note": "a finish is running already"}))
        return 0
    command = [sys.executable, str(Path(__file__).resolve()), "finish", args.run_dir]
    if args.item:
        command += ["--item", args.item]
    if args.queue:
        command += ["--queue", args.queue]
    if args.returned:
        command += ["--returned", *args.returned]
    if args.dead:
        command += ["--dead", *args.dead]
    with open(run_dir / "finish.out", "a") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True, cwd=str(ROOT))
    pid_file.write_text(f"{child.pid}\n")
    print(json.dumps({"detached": True, "run_dir": args.run_dir, "pid": child.pid}))
    return 0


def command_wait(args):
    """Poll for the detached finish's verdict for up to --seconds; a
    finish whose process is gone with no verdict is reported dead, so
    the caller does not wait on it again."""
    run_dir = Path(args.run_dir)
    path = run_dir / "finish.verdict.json"
    pid_file = run_dir / "finish.pid"
    deadline = time.time() + args.seconds
    while not path.exists() and time.time() < deadline:
        if pid_file.exists() and not pid_alive(int(pid_file.read_text().strip() or 0)):
            time.sleep(2)
            if not path.exists():
                print(json.dumps({"verdict": "error", "dead": True, "run_dir": args.run_dir,
                                  "note": "the detached finish ended without a verdict; see finish.out"}))
                return 0
        time.sleep(10)
    if path.exists():
        print(path.read_text().strip())
    else:
        print(json.dumps({"pending": True, "run_dir": args.run_dir}))
    return 0


def finish_pair(args):
    notes = place_missing_markers(args.run_dir, set(args.returned or ()), set(args.dead or ()))
    out = subprocess.run([sys.executable, str(RUN_PY), "finish", "--run-dir", args.run_dir],
                         capture_output=True, text=True, env=clean_environment(), cwd=str(ROOT))
    last = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    try:
        verdict = json.loads(last)
    except json.JSONDecodeError:
        verdict = {"verdict": "error", "note": (out.stderr.strip() or out.stdout.strip())[-1500:]}
    heuristics = subprocess.run([sys.executable, str(JUDGE_PY), "heuristics", args.run_dir],
                                capture_output=True, text=True, cwd=str(ROOT))
    verdict["heuristics_exit"] = heuristics.returncode
    verdict["harness"] = notes
    verdict["finish_exit"] = out.returncode
    verdict["summary"] = [line for line in out.stderr.splitlines() if "timings:" in line][-1:]
    return verdict


def command_finish(args):
    """Score the pair and record the verdict in the run directory and the
    queue; a finish that fails for any reason still leaves a verdict of
    error and a failed item, so no run waits on it forever."""
    if args.detach:
        return detach(args)
    if args.queue and args.item:
        queue("set", args.queue, args.item, f"finishing_at={time.time()}")
    try:
        verdict = finish_pair(args)
    except BaseException as error:  # noqa: BLE001 - the verdict must land whatever failed
        import traceback
        verdict = {"verdict": "error", "note": f"finish raised {type(error).__name__}: {error}",
                   "traceback": traceback.format_exc()[-2000:]}
    if args.queue and args.item:
        status = "done" if verdict.get("verdict") in ("pass", "fail") else "failed"
        queue("set", args.queue, args.item, f"status={status}", f"verdict={json.dumps(verdict.get('verdict'))}",
              f"finished_at={time.time()}")
    (Path(args.run_dir) / "finish.verdict.json").write_text(json.dumps(verdict) + "\n")
    print(json.dumps(verdict))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("queue")
    prep.add_argument("--worker", required=True)
    prep.add_argument("--log-dir")
    prep.add_argument("--wait-image-min", type=float, default=30)
    prep.set_defaults(func=command_prepare)
    fin = sub.add_parser("finish")
    fin.add_argument("run_dir")
    fin.add_argument("--item")
    fin.add_argument("--queue")
    fin.add_argument("--returned", nargs="*")
    fin.add_argument("--dead", nargs="*")
    fin.add_argument("--detach", action="store_true")
    fin.set_defaults(func=command_finish)
    wait = sub.add_parser("wait")
    wait.add_argument("run_dir")
    wait.add_argument("--seconds", type=float, default=540)
    wait.set_defaults(func=command_wait)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
