# Plan (agent1, team-lead)

Feature 6 (url type) is a single Regex definition in outlines/types/__init__.py.
agent1 inserts it directly AFTER the email definition and BEFORE the "Matches any ISBN number" comment block.
agent2 (feature 7): please put any new definitions at the END of outlines/types/__init__.py (after isbn),
and keep other edits away from the email/isbn region, so the two patches apply without conflicts.
If feature 7 needs other files, tell me which (coop-send agent1 ...).
When done: save your diff to /workspace/shared/agent2.patch (git diff, source files only, no scratch tests)
and set your task done with coop-task-update <id> done.
