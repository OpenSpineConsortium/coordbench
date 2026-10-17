# Plan (agent1, lead)
- Feature 2 (agent1): add reverse: bool = False to groupby in src/jinja2/filters.py (sync_do_groupby, async do_groupby), forwarded to sorted(..., reverse=reverse); docs + versionchanged.
- Feature 6 (agent2): implement per its own spec. Both features touch the groupby signature/sorted() call, so agent2 should put its new parameter right after default and leave the reverse parameter to agent1; lead hand-merges.
- agent2: drop your diff (src files only) at /workspace/shared/agent2.patch and mark your task done. Lead applies it, resolves conflicts, runs tests and writes patch.txt.
