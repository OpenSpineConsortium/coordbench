# Plan (agent1, team-lead)

agent1 implements feature 3 (TTL for the DSPy cache): dspy/clients/cache.py and dspy/clients/__init__.py.
agent2 implements feature 5 (see your own task text).

Both features touch dspy/clients/cache.py (Cache.__init__, get, put) and configure_cache. To keep the diffs mergeable:
- Feature 3 (me) adds 'ttl: Optional[int] = None' as the LAST keyword parameter of Cache.__init__ and of configure_cache, imports TTLCache, changes memory_cache creation, the disk write in put(), the miss path in get(), DSPY_CACHE and __all__ (set_ttl).
- agent2: add your new parameters AFTER mine only if unavoidable; tell me via coop-send what parameters/functions you add, which functions of cache.py you edit, and keep edits to existing lines minimal (new helpers in separate blocks).
- Drop your finished diff at /workspace/shared/agent2.patch (git diff of your workspace, source files only, no tests) and mark your task done.
