"""The benchmark's artifacts on GitHub, for .github/workflows/bench.yml:
one artifact per pair and arm, named as plan.py names it, kept across
dispatches so a run resumes, and beside each finished one a marker
<name>__done that the plan job reads without downloading anything.

- `finished NAME [--mode MODE]` says what the artifacts of that name hold,
  the newest first: exit 0 when one holds a finished pair (scored, no
  agent in Error but one that ran out its time, and in real mode no fake
  agent), so the job skips the pair; 3 when the newest holds a pair
  whose agents did not fail but that is unscored, so the job fetches it
  and scores it without running the agents again; 1 when there is none
  or the newest is unfinished, so the pair runs; 2 when the artifacts
  could not be read, which a real job treats as a failure rather than a
  reason to spend the pair's usage.
- `fetch NAME DIR [--mode MODE]` unpacks the attempt `finished` chose
  (the newest finished, else the newest unscored, else the newest) into
  DIR/<run name>/, for that rescore.
- `check-dir DIR [--mode MODE]` says whether a pair directory on this
  runner holds a finished pair, by the same rule: exit 0 or 1.
- `list-finished TAG` prints the name of every pair artifact of the tag
  whose marker is there, one per line, for plan.py --finished.
- `download TAG DIR [--survey-tag TAG2]` fetches every artifact whose name
  starts with TAG__ (of each name the attempt `finished` would choose)
  and the survey artifacts of TAG2 (TAG2__survey__*), unpacks each into
  DIR/<run name>/, the run name being the artifact's parts joined by
  dashes, as plan.py made it, and prints one JSON line per artifact, an
  "error" line for one it could not fetch or unpack, and a "warning" line
  when the listing was cut short; it goes on past one bad artifact.

GitHub answers the zip request with a redirect to its blob store, and
Python's urllib would send the redirected request every header of the
first, the token among them, so the redirect is not followed: the
location is read from the 302 and fetched with no token. The API is
read with GITHUB_TOKEN on GITHUB_REPOSITORY at GITHUB_API_URL, the
names the runner sets.
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PAGE = 100
MOST_PAGES = 50
DONE_SUFFIX = "__done"
FAKE_VERSION = "0.0.0-fake"
# The start of the end_reason coordbench/drivers.py CliRun writes for an
# agent that ran out the pair's time limit (drivers.TIMED_OUT, kept the
# same here since this script imports nothing of coordbench).
TIMED_OUT = "timed out after "
READ_PROBLEMS = (urllib.error.URLError, OSError, ValueError, KeyError, zipfile.BadZipFile)


class StopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(StopRedirect)


def settings(environ=None):
    environ = os.environ if environ is None else environ
    return {"api": environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            "repository": environ["GITHUB_REPOSITORY"], "token": environ["GITHUB_TOKEN"]}


def api_get(conf, path, opener=None):
    request = urllib.request.Request(f"{conf['api']}/repos/{conf['repository']}{path}",
                                     headers={"Authorization": f"Bearer {conf['token']}",
                                              "Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28"})
    with (opener or OPENER).open(request, timeout=60) as answer:
        return json.loads(answer.read().decode("utf-8"))


def list_artifacts(conf, name=None, opener=None):
    """Every artifact not expired, newest first as GitHub lists them; by
    exact name when one is given. Returns the list and whether it was cut
    short at MOST_PAGES pages, since the API filters by exact name only
    and a tag's artifacts may lie past the newest 5000."""
    found = []
    for page in range(1, MOST_PAGES + 1):
        query = f"?per_page={PAGE}&page={page}" + (f"&name={urllib.parse.quote(name)}" if name else "")
        answer = api_get(conf, f"/actions/artifacts{query}", opener)
        found.extend(a for a in answer.get("artifacts", []) if not a.get("expired"))
        if len(answer.get("artifacts", [])) < PAGE:
            return found, False
    return found, True


def by_name(artifacts, prefix=""):
    """The artifacts under each name, newest first."""
    named = {}
    for artifact in artifacts:
        if artifact["name"].startswith(prefix):
            named.setdefault(artifact["name"], []).append(artifact)
    for found in named.values():
        found.sort(key=lambda a: a["created_at"], reverse=True)
    return named


def newest_by_name(artifacts, prefix=""):
    return {name: found[0] for name, found in by_name(artifacts, prefix).items()}


def fetch_zip(conf, artifact_id, opener=None):
    """The artifact's zip, through the redirect, as bytes."""
    request = urllib.request.Request(f"{conf['api']}/repos/{conf['repository']}/actions/artifacts/{artifact_id}/zip",
                                     headers={"Authorization": f"Bearer {conf['token']}",
                                              "Accept": "application/vnd.github+json"})
    try:
        with (opener or OPENER).open(request, timeout=120) as answer:
            return answer.read()
    except urllib.error.HTTPError as redirect:
        if redirect.code not in (301, 302, 303, 307, 308):
            raise
        location = redirect.headers["Location"]
    with (opener or OPENER).open(urllib.request.Request(location), timeout=600) as blob:
        return blob.read()


def agent_failed(agent):
    """run.py's rule: an agent in Error is no sample (a usage limit, a
    refused sign-in, a container gone), unless it ran out the pair's time
    limit, which is the benchmark's own limit reached, an outcome whose
    patch is scored; so such a pair is kept, not run again."""
    return agent.get("status") == "Error" and not str(agent.get("end_reason") or "").startswith(TIMED_OUT)


def pair_state(result, evaluation, mode="fake"):
    """What a pair's result.json and eval.json say, as run.py's
    pair_finished reads a pair directory: "finished" when the eval has no
    error beside a result none of whose agents failed (agent_failed),
    "unscored" when no agent failed but the eval is missing or errored, so
    the pair is scored again without running the agents, else
    "unfinished" (an agent in Error, a usage limit among the causes, is
    run again by the next dispatch, as PLAN.md says). In real mode a pair
    any of whose agents was the fake agent is unfinished, so a fake
    artifact never stands in for a real pair."""
    if not isinstance(result, dict) or result.get("error"):
        return "unfinished"
    agents = result.get("agents") if isinstance(result.get("agents"), dict) else {"solo": result.get("agent")}
    if not agents or any(not isinstance(a, dict) or agent_failed(a) for a in agents.values()):
        return "unfinished"
    if mode == "real" and any(str(a.get("claude_code_version") or "").startswith(FAKE_VERSION) for a in agents.values()):
        return "unfinished"
    if not isinstance(evaluation, dict) or evaluation.get("error"):
        return "unscored"
    return "finished"


def zip_state(data, mode="fake"):
    with zipfile.ZipFile(io.BytesIO(data)) as packed:
        names = packed.namelist()
        results = [name for name in names if name.endswith("result.json")]
        evals = [name for name in names if name.endswith("eval.json")]
        if not results:
            return "unfinished"
        result = json.loads(packed.read(results[0]))
        evaluation = json.loads(packed.read(evals[0])) if evals else None
    return pair_state(result, evaluation, mode)


def holds_finished_pair(data, mode="fake"):
    return zip_state(data, mode) == "finished"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def directory_state(directory, mode="fake"):
    """The state of the pair directory at or below directory (the run
    directory a job uploads holds one), "unfinished" when there is none."""
    results = sorted(Path(directory).rglob("result.json")) if Path(directory).is_dir() else []
    if not results:
        return "unfinished"
    return pair_state(read_json(results[0]), read_json(results[0].parent / "eval.json"), mode)


def run_name_of(artifact_name):
    return artifact_name.replace("__", "-")


def unpack(data, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as packed:
        for member in packed.infolist():
            target = (directory / member.filename).resolve()
            if directory.resolve() not in target.parents and target != directory.resolve():
                raise ValueError(f"the artifact names a path outside its directory: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(packed.read(member))


def artifact_states(conf, artifacts, mode, opener=None):
    """Each artifact with its state and its bytes, newest first, stopping
    at the first finished one, since that is the one a download keeps;
    the bytes are kept so the chosen one is not fetched twice."""
    states = []
    for artifact in artifacts:
        data = fetch_zip(conf, artifact["id"], opener)
        state = zip_state(data, mode)
        states.append((artifact, state, data))
        if state == "finished":
            break
    return states


def chosen_state(states):
    """The attempt of a name that stands for the pair: the newest finished
    one, whatever came after it; else the newest unscored one, whose
    agents did not fail and whose files are scored again for free rather
    than the pair run again on usage; else the newest, which runs again."""
    for wanted in ("finished", "unscored"):
        for entry in states:
            if entry[1] == wanted:
                return entry
    return states[0]


def run_of(artifact):
    return f"run {artifact.get('workflow_run', {}).get('id')} ({artifact['created_at']})"


def command_finished(conf, name, mode="fake", opener=None):
    try:
        artifacts, _ = list_artifacts(conf, name, opener)
        found = by_name(artifacts).get(name, [])
        if not found:
            print(f"{name}: no artifact yet")
            return 1
        states = artifact_states(conf, found, mode, opener)
    except READ_PROBLEMS as problem:
        print(f"{name}: could not read the artifacts ({problem})")
        return 2
    artifact, state, _ = chosen_state(states)
    if state == "finished":
        print(f"{name}: finished in {run_of(artifact)}")
        return 0
    if state == "unscored":
        print(f"{name}: the agents did not fail in {run_of(artifact)} but the pair is unscored; it is scored again")
        return 3
    print(f"{name}: an agent failed or the pair is not a real one; it runs again")
    return 1


def command_fetch(conf, name, directory, mode="fake", opener=None):
    """Unpack the attempt `finished` chose into DIR/<run name>/, for the
    rescore: the same rule, so the files scored are the ones it named."""
    try:
        artifacts, _ = list_artifacts(conf, name, opener)
        found = by_name(artifacts).get(name, [])
        if not found:
            print(f"{name}: no artifact to fetch")
            return 1
        artifact, state, data = chosen_state(artifact_states(conf, found, mode, opener))
        target = Path(directory) / run_name_of(name)
        unpack(data, target)
    except READ_PROBLEMS as problem:
        print(f"{name}: could not fetch the artifact ({problem})")
        return 2
    print(f"{name}: fetched {run_of(artifact)} ({state}) into {target}")
    return 0


def command_check_dir(directory, mode="fake"):
    state = directory_state(directory, mode)
    print(f"{directory}: {state}")
    return 0 if state == "finished" else 1


def command_list_finished(conf, tag, opener=None):
    artifacts, cut = list_artifacts(conf, opener=opener)
    for name in sorted(by_name(artifacts, f"{tag}__")):
        if name.endswith(DONE_SUFFIX):
            print(name[:-len(DONE_SUFFIX)])
    if cut:
        print(f"the artifact listing was cut at {PAGE * MOST_PAGES}; older markers of {tag} may be missing",
              file=sys.stderr)
    return 0


def chosen_artifact(conf, found, mode, opener=None):
    """The artifact of a name that stands for the pair (chosen_state), with
    its bytes, so a failed rerun never hides a scored sample."""
    artifact, _, data = chosen_state(artifact_states(conf, found, mode, opener))
    return artifact, data


def wanted_names(artifacts, tag, survey_tag=None):
    named = {name: found for name, found in by_name(artifacts, f"{tag}__").items() if not name.endswith(DONE_SUFFIX)}
    if survey_tag and survey_tag != tag:
        named.update(by_name(artifacts, f"{survey_tag}__survey__"))
    return named


def command_download(conf, tag, directory, mode="fake", survey_tag=None, opener=None):
    artifacts, cut = list_artifacts(conf, opener=opener)
    if cut:
        print(json.dumps({"warning": f"the artifact listing was cut at {PAGE * MOST_PAGES}; older artifacts of "
                                     f"{tag} may be missing from this report"}))
    named = wanted_names(artifacts, tag, survey_tag)
    for name, found in sorted(named.items()):
        target = Path(directory) / run_name_of(name)
        try:
            artifact, data = chosen_artifact(conf, found, mode, opener)
            unpack(data, target)
        except READ_PROBLEMS as problem:
            print(json.dumps({"name": name, "error": f"{type(problem).__name__}: {problem}"}))
            continue
        print(json.dumps({"name": name, "arm": name.split("__")[1], "run_name": run_name_of(name),
                          "path": str(target), "created_at": artifact["created_at"],
                          "size": artifact.get("size_in_bytes"), "attempts": len(found)}))
    if not named:
        print(f"no artifact named {tag}__*", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    finished = commands.add_parser("finished", help="exit 0 when the named artifact holds a finished pair")
    finished.add_argument("name")
    finished.add_argument("--mode", choices=("fake", "real"), default="fake")
    fetch = commands.add_parser("fetch", help="unpack the attempt `finished` chose into the directory")
    fetch.add_argument("name")
    fetch.add_argument("directory")
    fetch.add_argument("--mode", choices=("fake", "real"), default="fake")
    check = commands.add_parser("check-dir", help="exit 0 when the directory holds a finished pair")
    check.add_argument("directory")
    check.add_argument("--mode", choices=("fake", "real"), default="fake")
    listing = commands.add_parser("list-finished", help="print the pair artifacts of the tag that have a marker")
    listing.add_argument("tag")
    download = commands.add_parser("download", help="unpack every artifact of the tag into the directory")
    download.add_argument("tag")
    download.add_argument("directory")
    download.add_argument("--mode", choices=("fake", "real", "survey"), default="fake")
    download.add_argument("--survey-tag", help="a survey dispatch's tag whose reports are wanted too")
    args = parser.parse_args(argv)
    if args.command == "check-dir":
        return command_check_dir(args.directory, args.mode)
    conf = settings()
    if args.command == "finished":
        return command_finished(conf, args.name, args.mode)
    if args.command == "fetch":
        return command_fetch(conf, args.name, args.directory, args.mode)
    if args.command == "list-finished":
        return command_list_finished(conf, args.tag)
    return command_download(conf, args.tag, args.directory, args.mode, args.survey_tag)


if __name__ == "__main__":
    raise SystemExit(main())
