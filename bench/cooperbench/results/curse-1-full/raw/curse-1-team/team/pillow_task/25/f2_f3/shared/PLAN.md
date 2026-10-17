# Plan (agent1, lead)
Feature 2 (mine): add keyword param preserve_readonly=False to Image.save() in src/PIL/Image.py.
I edit ONLY: the def save( signature line(s), the save() docstring (add a :param preserve_readonly: entry),
and the single "self._ensure_mutable()" call inside save() (replaced by an if/else on preserve_readonly).
Tests/test_image.py: I add one test near the other save tests.
Agent2 (feature 3): please tell me which files/functions your feature touches. If you must change save(), keep your
edits to other lines than the above, and send me a message first so we do not collide. Drop your final diff at
/workspace/shared/agent2.patch (git diff of your work only) and mark your task done.
