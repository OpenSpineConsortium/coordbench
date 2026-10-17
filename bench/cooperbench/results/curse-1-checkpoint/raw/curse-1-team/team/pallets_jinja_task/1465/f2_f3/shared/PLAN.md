# Plan (agent1, lead)

Both features (f2 = reverse param for groupby, f3 = agent2's task) are expected to touch
sync_do_groupby and do_groupby in src/jinja2/filters.py (signature, docstring, body).

Split to keep regions disjoint:
- agent1 (f2): adds a new keyword parameter "reverse: bool = False" as the parameter after
  "default" in both sync_do_groupby and do_groupby; passes reverse=reverse to sorted(...);
  adds a paragraph on reverse in the docstring (after the default paragraph) and a
  versionchanged note.
- agent2 (f3): tell me (coop-send agent1) what your feature changes, and avoid reformatting
  unrelated lines.
- When done, each writes the diff of src/jinja2/filters.py to /workspace/shared/<agent>.patch
  (git diff, only your own feature) and marks the task done. agent1 merges by hand.
