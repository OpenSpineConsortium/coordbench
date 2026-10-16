# Plan (agent1, team-lead)
- Feature 6 (url type): agent1 adds a url = Regex(...) block in outlines/types/__init__.py, placed directly AFTER the isbn definition at the end of the file (a comment block + one assignment).
- Feature 7: agent2 implements it. agent2: please tell me which files/regions you touch. If you also edit outlines/types/__init__.py, put your change ABOVE the isbn block (or elsewhere not adjacent to the end of the file) so our hunks do not overlap.
- When done, agent2 writes git diff (source files only, no tests/scratch) to /workspace/shared/agent2.patch, marks the task done, and sends me a message. I apply it on top of my tree, run tests, and write the final patch.txt.
