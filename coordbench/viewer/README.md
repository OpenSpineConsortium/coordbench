# The comparison viewer

compare.html sets several agent runs side by side: coordbench's own runs
and CooperBench's published trajectories, so a coordbench arm can be read
against the authors' models on the same pair. It is one file with its CSS
and JavaScript inside, no CDN and no build, because a report has to open
anywhere, offline, and nothing it shows may leave the machine.

## Getting runs into it

- Open it from disk and choose or drop files: coordbench's
  trajectory.json.gz, or any of the website's files (the legacy OpenHands
  event lists, the OpenHands SDK and mini-swe `{metadata, steps}` files,
  the cause and curated 7-key step lists). A pair directory picked with
  "Pair directory" reads its trajectory.json.gz, or, before one was
  exported, builds a run from result.json, eval.json, events.jsonl,
  conversation.json and judge.json, with the hub's calls as its steps.
- Serve a directory (`python3 -m http.server`) and name the runs in the
  URL: `?run=<url>` (repeatable), `?manifest=<url>` of
  `{"runs": [{"label", "url"} | {"label", "site", "model", "repo", "task", "features"}]}`,
  or the website's own layout with
  `?site=<root>&repo=<repo>&task=task<N>&features=feature<a>_feature<b>&model=<key>&model=<key>`
  (add `setting=coopGit` for coop_git). With a site, the page also reads
  index.json for each run's outcome (a pair the index lacks is "not
  published", never a fail), causes/index.json for the cause labels and
  tasks.json for the feature titles.
- `python3 coordbench/compare.py <pair_dir or file>... -o report.html`
  writes a copy with the runs embedded, one file that opens by itself.

`python3 coordbench/export.py <pair_dir>... --site <out>` writes
coordbench runs into the website's layout, so the same URLs, and
CooperBench's stock viewer, open them beside the published ones.

## What it shows

The runs table; relative speed (wall time, active span, steps, steps per
minute, time per step, tokens, cost, time to the first message, edit and
test run and to submit, the longest idle gap, the communication share of
tool calls, and for coordbench runs the time blocked on peers and the
hub's own time), each as a value or a ratio to a chosen baseline; the
failure-mode presence matrix, each cell marked by its source (judge.json,
human cause labels, a heuristic computed in the page, or the outcome);
aligned timelines with a lane per agent, message arrows and hub events;
two or three step lists side by side with the stock viewer's filters; the
messages; and, when the runs share a pair, its feature titles and the
coordbench runs' patches and merged diffs. Runs whose step times are
interpolated (the SDK files) or come only from the hub's record are
hatched and left out of speed ranks unless included.
