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
CooperBench's stock viewer, open them beside the published ones. `<out>`
may be the website's public/ tree, whose index files the export merges
into, or a fresh directory served as the viewer's public root: the
export then writes an empty causes/index.json, which the stock viewer
fetches before anything else and without which it loads no run.

## What it shows

The runs table, whose outcome keeps each scoring rule under its own name:
63b9d44's verdict with the merge strategy that decided it ("none" where
the naive merge conflicted and the lead's patch alone failed), the
paper-era cascade's verdict with its route ("undecided" where naive and
union failed and the learned resolver, which coordbench does not run,
was needed), or, for a published run, the website index's own verdict; relative speed (wall time, active span, steps, steps
per minute, time per step, tokens, cost, time to the first message, edit
and test run and to submit, the longest idle gap, the communication share
of tool calls, and for coordbench runs the time blocked on peers and the
hub's own time), each as a value or a ratio to a chosen baseline, which
stays the run chosen when another run is removed, its bars as long as
the values they print; the failure-mode presence matrix, each cell marked
by its source (judge.json's model labels, App G's rule where judge.json
says it decided a symptom level with no model or over the judge's, human
cause labels, a heuristic computed in the page, or the outcome), failures
in red and green and the coordination behaviours (role division,
resource division, negotiation), which are not failures, in their own
colour; aligned timelines with a lane per agent, message arrows and hub
events, which keep their text size on a phone and scroll in their own
box, saying which stretch of the axis is in view; two or three step lists side by side with the stock viewer's
filters; the messages; and, when the runs share a pair, its feature
titles and the coordbench runs' patches and merged diffs.

"Steps" is the stock viewer's count: every entry of the file its five
prompt filters keep, an OpenHands event list's observations included.
The one other count, the page's own step cards without the message
receipts, is labelled "steps without observations and receipts" wherever it
shows. Each run's clock starts at its first agent's own start when one
is known, else, for a coordbench run with no run_started event (the
session-driver pilots), at result.json's started_at, where its agents'
spans then start too, else at the file's first event, and the runs table
and the timelines say which; the wall time's title gives the stock
viewer's stats-bar duration where the two differ. A token total is marked
as a lower bound (">=", its reason in the title) when it leaves out a
count nobody measured, or when an agent's output counts were summed from
a transcript whose messages lack the line holding the final count; a
cost is marked the same way when it leaves an agent's out or holds the
session driver's cost_lower_bound, and says where it came from (Claude
Code's own total, the estimate from tokens, OpenHands' accumulated_cost,
the website file's metadata). Runs whose step times are interpolated
(the SDK files) or come only from the hub's record are hatched and left
out of speed ranks unless included. A run with no transcript has only
the hub's record of its coordination calls: its step counts are floors
("hub calls only"), its steps per minute, time per step, communication
share, idle gap and times to the first edit and test run are not
measured, and its timeline draws each hub call as long as it took over a
dashed band for the agent's unrecorded span. A run whose agent logs do
not overlap, or one with an agent whose own log covers under a tenth of
the run and does not end with its finish step, is flagged beside its
times, the second as possibly truncated.
