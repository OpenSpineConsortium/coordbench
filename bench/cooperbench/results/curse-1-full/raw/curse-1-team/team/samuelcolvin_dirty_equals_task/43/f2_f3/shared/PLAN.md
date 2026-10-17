# Plan (agent1, lead)
Feature 2 (IsMac, agent1): I add class IsMac at the END of dirty_equals/_other.py (after FunctionCheck) and
add IsMac to the "from ._other import" line and to __all__ right after IsUUID in dirty_equals/__init__.py.
Feature 3 (agent2): please put your new code in a DIFFERENT region: new class directly after IsJson (before
FunctionCheck) in _other.py or in another module, and in __init__.py add your name to __all__ after FunctionCheck/IsJson
(not on the IsUUID line). If you must edit the import line, keep the names alphabetical-ish; I will resolve conflicts by hand.
When done: write your diff (git diff, source files only) to /workspace/shared/agent2.patch, mark your task done, and coop-send me.
