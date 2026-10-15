# Plan (agent1, team-lead)

Both features likely touch InternationalizationExtension.parse() and _make_node()
in src/jinja2/ext.py. To keep our diffs in disjoint hunks:

agent1 (feature 4, metadata note/created/modified):
  - parse(): adds `metadata: t.Dict[str, str] = {}` right after `variables`/`trimmed`
    declarations, and ONE new branch inside the parameter `while` loop, placed
    right after `token = parser.stream.expect("name")` and BEFORE the
    "defined twice" check (so my hunk is the lines just after `expect("name")`).
  - parse(): the `self._make_node(...)` call gets `metadata` as a NEW LAST argument.
  - _make_node(): adds `metadata: t.Dict[str, str]` as the LAST signature parameter
    and sets `output_node.trans_metadata` just before `return`.

agent2 (feature 10):
  - Please keep your hunks away from the lines above. If you need a new _make_node
    parameter, put it BEFORE mine in the signature and call (I will merge by hand
    anyway), or tell me via coop-send which lines you change.
  - Drop your final diff at /workspace/shared/agent2.patch (git diff of src/ only,
    no tests/scratch files) and mark your task done with coop-task-update.
