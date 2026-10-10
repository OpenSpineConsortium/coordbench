"""Several runs side by side, in one HTML file that opens offline.

    python3 coordbench/compare.py <run>... [-o report.html] [--label TEXT]... [--baseline N]
                                  [--cooperbench-dir DIR]

Each run is a coordbench pair directory, exported on the fly by
export.py beside this file (nothing is written into the directory), or a
trajectory file: coordbench's trajectory.json.gz, or one of CooperBench's
published files (.json or .json.gz, any of the shapes the page reads).
A published file inside the website's tree
(static/data/<coop|coop_git>/trajectories/<model>/<repo>_<task>_<features>.json.gz)
brings along what the site says of it: the index.json row's outcome for
that model (a row the index lacks is "not published", never a fail), the
cause labels of causes/index.json and the feature titles of tasks.json.

The report is a copy of viewer/compare.html with the runs embedded as
JSON in its embedded-runs block, so it is one file that opens from disk
with no server and no network, which is what a report sent to someone
needs. The page also loads more files by drag and drop. --label names
the runs in order; --baseline is the run the relative speeds divide by
(0, the first, by default).
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import export  # noqa: E402

PAGE = HERE / "viewer" / "compare.html"
PLACEHOLDER = '<script id="embedded-runs" type="application/json">null</script>'
PATCH_LIMIT = 2_000_000


def read_bundle_file(path):
    """A trajectory file's JSON, gzip undone by its magic number as the
    stock viewer does it."""
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def pair_patches(pair_dir):
    """The pair directory's patches and merged diffs, for the page's pair
    focus."""
    patches = {}
    for path in sorted(Path(pair_dir).iterdir()):
        if path.suffix in (".patch", ".diff") and path.is_file() and path.stat().st_size <= PATCH_LIMIT:
            patches[path.name] = path.read_text(encoding="utf-8", errors="replace")
    return patches


def site_parts(path):
    """(root, setting dir, model, repo, task, features) for a file in the
    website's layout, or None."""
    parts = Path(path).resolve().parts
    for index in range(len(parts) - 5):
        if parts[index:index + 2] == ("static", "data") and parts[index + 3] == "trajectories":
            name = parts[-1]
            pair = export_pair_name(name)
            if pair and index + 5 == len(parts) - 1:
                return (Path(*parts[:index]), parts[index + 2], parts[index + 4]) + pair
    return None


def export_pair_name(name):
    stem = name[:-len(".json.gz")] if name.endswith(".json.gz") else name[:-len(".json")] if name.endswith(".json") else None
    if stem is None or "_task" not in stem:
        return None
    repo, _, rest = stem.rpartition("_task")
    task_number, _, features = rest.partition("_")
    if not task_number.isdigit() or not features.startswith("feature"):
        return None
    return repo, f"task{task_number}", features


def site_extras(path):
    found = site_parts(path)
    if found is None:
        return {}
    root, setting, model, repo, task, features = found
    data = root / "static" / "data"
    extras = {"model": model, "setting": setting, "repo": repo, "task": task, "features": features}
    index = export.read_json(data / setting / "index.json")
    if isinstance(index, dict):
        result, _ = export.index_result(index, repo, task, features, model)
        extras.update({"index_result": result} if result else {"index_missing": True})
    causes = export.read_json(data / "causes" / "index.json")
    if isinstance(causes, dict):
        extras["causes"] = cause_entries(causes, model, repo, task, features)
    tasks = export.read_json(data / "tasks.json")
    if isinstance(tasks, list):
        extras["feature_titles"] = feature_titles(tasks, repo, task, features)
    return extras


def cause_entries(causes, model, repo, task, features):
    entries = []
    for cause in export.CAUSES:
        for entry in causes.get(cause) or []:
            if not isinstance(entry, dict):
                continue
            same_model = str(entry.get("model", "")).rsplit("_k", 1)[0] == model
            same_pair = entry.get("task_id") == task and entry.get("features") == features
            same_repo = export.canonical_repo(entry.get("project")) == export.canonical_repo(repo) \
                or entry.get("project") == "cause_annotation"
            if same_model and same_pair and same_repo:
                entries.append(entry)
    return entries


def feature_titles(tasks, repo, task, features):
    wanted = features.split("_")
    for entry in tasks:
        if isinstance(entry, dict) and export.canonical_repo(entry.get("repoKey")) == export.canonical_repo(repo) \
                and entry.get("taskId") == task:
            return [{"feature": f.get("id"), "title": f.get("title")} for f in entry.get("features") or []
                    if isinstance(f, dict) and f.get("id") in wanted]
    return None


def default_label(trajectory, fallback):
    block = (trajectory.get("metadata") or {}).get("coordbench") if isinstance(trajectory, dict) else None
    if block:
        return " ".join(str(part) for part in (block.get("arm"), block.get("driver"), block.get("run_name")) if part)
    return fallback


def run_entry(path, label, cooperbench_dir):
    """One run of the bundle: {label, source, data, extras}."""
    path = Path(path)
    if path.is_dir() and (path / "result.json").is_file():
        data = export.build_trajectory(path, cooperbench_dir)
        return {"label": label or default_label(data, path.name), "source": str(path), "data": data,
                "extras": {"patches": pair_patches(path)}}
    if path.is_dir() and (path / "trajectory.json.gz").is_file():
        data = read_bundle_file(path / "trajectory.json.gz")
        return {"label": label or default_label(data, path.name), "source": str(path), "data": data,
                "extras": {"patches": pair_patches(path)}}
    data = read_bundle_file(path)
    name = path.name[:-len(".gz")] if path.name.endswith(".gz") else path.name
    extras = site_extras(path)
    fallback = f"{extras['model']} {name[:-len('.json')]}" if extras.get("model") else name[:-len(".json")]
    return {"label": label or default_label(data, fallback), "source": str(path), "data": data, "extras": extras}


def embedded_json(bundle):
    """The bundle as JSON safe inside a script element: every < escaped,
    so no text in a run can close the element or open a comment."""
    return json.dumps(bundle, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")


def report(paths, labels=(), baseline=0, cooperbench_dir=None):
    page = PAGE.read_text(encoding="utf-8")
    if PLACEHOLDER not in page:
        raise ValueError(f"{PAGE} has no embedded-runs block to fill")
    runs = [run_entry(path, labels[i] if i < len(labels) else None, cooperbench_dir) for i, path in enumerate(paths)]
    bundle = {"runs": runs, "baseline": baseline if 0 <= baseline < len(runs) else 0}
    filled = '<script id="embedded-runs" type="application/json">' + embedded_json(bundle) + "</script>"
    return page.replace(PLACEHOLDER, filled, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Several runs side by side in one HTML file that opens offline.")
    parser.add_argument("runs", nargs="+", help="pair directories or trajectory files")
    parser.add_argument("-o", "--output", default="coordbench-compare.html", help="the report to write")
    parser.add_argument("--label", action="append", default=[], help="a run's label, in the order of the runs")
    parser.add_argument("--baseline", type=int, default=0, help="the run relative speeds divide by (0 is the first)")
    parser.add_argument("--cooperbench-dir", help="a CooperBench checkout, for feature titles and the spec audit")
    args = parser.parse_args(argv)
    try:
        html = report(args.runs, args.label, args.baseline, args.cooperbench_dir)
    except (OSError, ValueError) as error:
        print(f"compare: {error}", file=sys.stderr)
        return 1
    Path(args.output).write_text(html, encoding="utf-8")
    print(f"compare: {len(args.runs)} run(s) in {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
