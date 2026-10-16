# Plan (agent1, lead)

Two features, both against /workspace/repo (pallets jinja, src/jinja2/ext.py most likely).

- agent1 (me), feature 6 (i18n translation LRU cache): edits ONLY
  InternationalizationExtension.__init__ (adds self._init_cache() and
  i18n_cache_size=128 to the environment.extend call) and inserts one new block of
  methods (_init_cache, _update_cache_size, _get_from_cache, _put_in_cache, clear_cache, bind)
  directly after __init__, before _install. I add one import (threading) at the top of ext.py.
- agent2, feature 9: please keep your edits out of those two spots. If you must touch
  InternationalizationExtension.__init__ or the environment.extend(...) call, tell me first
  (coop-send agent1) and add your keyword on its own line so the hunks merge cleanly.
- When done: write your diff (git diff, no tests or scratch files) to /workspace/shared/agent2.patch,
  mark your task done with coop-task-update, and message me. I apply it to my tree and submit the merge.
