"""The benchmark's artifacts on GitHub, for .github/workflows/bench.yml:
one artifact per pair and arm, named as plan.py names it, kept across
dispatches so a run resumes.

- `finished NAME` says whether an artifact of that name holds a finished
  pair (scored, no agent in Error): exit 0 when it does, 1 when there is
  none or it is unfinished, so the job skips the pair or runs it.
- `download TAG DIR` fetches every artifact whose name starts with TAG__
  (the newest of each name) and unpacks each into DIR/<run name>/, the
  run name being the artifact's parts joined by dashes, as plan.py made
  it; it prints one JSON line per artifact.

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
    exact name when one is given."""
    found = []
    for page in range(1, MOST_PAGES + 1):
        query = f"?per_page={PAGE}&page={page}" + (f"&name={urllib.parse.quote(name)}" if name else "")
        answer = api_get(conf, f"/actions/artifacts{query}", opener)
        found.extend(a for a in answer.get("artifacts", []) if not a.get("expired"))
        if len(answer.get("artifacts", [])) < PAGE:
            break
    return found


def newest_by_name(artifacts, prefix=""):
    newest = {}
    for artifact in artifacts:
        name = artifact["name"]
        if not name.startswith(prefix):
            continue
        if name not in newest or artifact["created_at"] > newest[name]["created_at"]:
            newest[name] = artifact
    return newest


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


def holds_finished_pair(data):
    """True when the zip holds a scored pair whose agents all finished: an
    eval.json without an error beside a result.json none of whose agents
    ended in Error, as run.py's pair_finished reads a pair directory. A
    pair an agent left in Error (a usage limit among the causes) is run
    again by the next dispatch, as PLAN.md says."""
    with zipfile.ZipFile(io.BytesIO(data)) as packed:
        names = packed.namelist()
        evals = [name for name in names if name.endswith("eval.json")]
        results = [name for name in names if name.endswith("result.json")]
        if not evals or not results:
            return False
        evaluation = json.loads(packed.read(evals[0]))
        result = json.loads(packed.read(results[0]))
    if not isinstance(evaluation, dict) or evaluation.get("error") or not isinstance(result, dict) or result.get("error"):
        return False
    agents = result.get("agents") if isinstance(result.get("agents"), dict) else {"solo": result.get("agent")}
    return bool(agents) and not any(not isinstance(a, dict) or a.get("status") == "Error" for a in agents.values())


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


def command_finished(conf, name, opener=None):
    try:
        newest = newest_by_name(list_artifacts(conf, name, opener)).get(name)
        if newest is None:
            print(f"{name}: no artifact yet")
            return 1
        if holds_finished_pair(fetch_zip(conf, newest["id"], opener)):
            print(f"{name}: finished in run {newest.get('workflow_run', {}).get('id')} ({newest['created_at']})")
            return 0
        print(f"{name}: the pair is unscored or an agent ended in Error; it runs again")
        return 1
    except (urllib.error.URLError, OSError, ValueError, KeyError) as problem:
        print(f"{name}: could not read the artifacts ({problem}); the pair runs")
        return 1


def command_download(conf, tag, directory, opener=None):
    newest = newest_by_name(list_artifacts(conf, opener=opener), prefix=f"{tag}__")
    for name, artifact in sorted(newest.items()):
        target = Path(directory) / run_name_of(name)
        unpack(fetch_zip(conf, artifact["id"], opener), target)
        print(json.dumps({"name": name, "run_name": run_name_of(name), "path": str(target),
                          "created_at": artifact["created_at"], "size": artifact.get("size_in_bytes")}))
    if not newest:
        print(f"no artifact named {tag}__*", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    finished = commands.add_parser("finished", help="exit 0 when the named artifact holds a finished pair")
    finished.add_argument("name")
    download = commands.add_parser("download", help="unpack every artifact of the tag into the directory")
    download.add_argument("tag")
    download.add_argument("directory")
    args = parser.parse_args(argv)
    conf = settings()
    if args.command == "finished":
        return command_finished(conf, args.name)
    return command_download(conf, args.tag, args.directory)


if __name__ == "__main__":
    raise SystemExit(main())
