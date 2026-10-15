# Plan (agent1, team-lead)

Two features on pallets/jinja, one repo. agent1 owns feature 2 (template path
normalization); agent2 owns feature 9 (task a587df7b1f).

agent1 edits ONLY these regions of src/jinja2/loaders.py:
  - FileSystemLoader.get_source(): one line added before split_template_path()
  - PackageLoader.get_source(): one line added before split_template_path()
agent1 touches no other file and no other function.

agent2: please keep your edits out of those two get_source() bodies. If feature 9
must edit loaders.py, say which function/lines via coop-send before editing, so we
keep disjoint hunks. When done: write your diff to /workspace/shared/agent2.patch
(git diff of your own tree, source files only, no tests or scratch files), mark
your task done with coop-task-update, and send me a short message.
agent1 applies agent2.patch on top of own change, runs the tests, and submits patch.txt.
