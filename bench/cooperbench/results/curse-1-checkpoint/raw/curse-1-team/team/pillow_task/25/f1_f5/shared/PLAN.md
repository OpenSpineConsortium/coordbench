# Plan (agent1, lead)
Feature 1 (agent1): in Image.save(), call _ensure_mutable() only when self.filename == the filename being saved; otherwise just self.load(). Touches the "# may mutate self!" spot in src/PIL/Image.py and adds tests after test_readonly_save in Tests/test_image.py.
Feature 5 (agent2): implement per its own spec in regions that do not overlap the above. Drop final diff at /workspace/shared/agent2.patch and mark task done.
Integration (agent1): git apply agent2.patch onto the lead tree, run tests, then git diff > patch.txt.
