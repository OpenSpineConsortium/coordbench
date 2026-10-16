# Plan (agent1, team-lead)

agent1 implements feature 4 (search path priority for FileSystemLoader): only
FileSystemLoader.__init__ (signature type annotation + searchpath processing)
and the FileSystemLoader class docstring in src/jinja2/loaders.py.

agent2 implements feature 10 (your task). Please keep your edits outside the
FileSystemLoader.__init__ body and the FileSystemLoader class docstring where
possible. If you must touch FileSystemLoader (e.g. get_source, list_templates,
or a new method), tell me which lines via coop-send so we keep disjoint hunks.
When done, write your diff (git diff, source files only, no tests) to
/workspace/shared/agent2.patch and mark your task done with coop-task-update.
