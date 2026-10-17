# Plan (agent1, team-lead)

Feature 1 (agent1): FileSystemLoader fallback_searchpath, in src/jinja2/loaders.py:
  - FileSystemLoader.__init__ signature (new optional kwarg fallback_searchpath, added LAST, after followlinks),
    self.fallback_searchpath attribute
  - FileSystemLoader.get_source (primary paths, then fallback paths; shared lookup helper)
  - FileSystemLoader.list_templates (union of primary + fallback, sorted, unique)
  - FileSystemLoader class docstring (param doc + versionchanged)

Feature 6 (agent2): please tell me (coop-send agent1 ...) which files/functions/regions your spec touches.
  If you touch loaders.py, please keep your edits OUT of FileSystemLoader.__init__ signature/get_source/list_templates
  where possible; if you must, tell me the exact lines and I will merge by hand at integration time.

Integration: when done, write your diff to /workspace/shared/agent2.patch (git diff of ONLY your source changes, no tests/scratch),
mark your task done with coop-task-update, and message me.
