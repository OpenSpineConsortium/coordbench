# Plan (agent1, lead)
Feature 3 (IsEmail) is mine, feature 7 is agent2's. I do not know feature 7's spec; agent2 has it.
To keep our diffs in disjoint regions:
- agent1 (IsEmail): adds 'import re' at the top of dirty_equals/_other.py, appends class IsEmail at the END of
  dirty_equals/_other.py (after FunctionCheck), adds a separate new line 'from ._other import IsEmail' after the
  existing _other import line in __init__.py, and adds 'IsEmail' to __all__ right after 'IsUUID'.
- agent2 (feature 7): put new code elsewhere: if you add a class in _other.py, insert it directly AFTER IsUUID
  (before 'AnyJson = object'), not at the end; edit the existing 'from ._other import ...' line freely; add your
  name to __all__ before 'FunctionCheck' (or another section).
- Drop your patch (git diff against the pristine tree) at /workspace/shared/agent2.patch, mark your task done and
  tell me with coop-send. I apply it and resolve any conflict myself.
