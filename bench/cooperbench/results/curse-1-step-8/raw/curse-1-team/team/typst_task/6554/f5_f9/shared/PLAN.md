Plan (agent1, lead)
- Feature 5 (agent1): add named count: Option<usize> to Str::first and Str::last in crates/typst/src/foundations/str.rs. I own ONLY those two functions (doc comments + bodies of first and last) and, if needed, a small helper placed right after last. Return type becomes StrResult<Value>.
- Feature 9 (agent2): implement your own spec. Please keep your edits out of first and last. If you must touch them, or anything near them, message me first with coop-send.
- When done, agent2: write your diff to /workspace/shared/agent2.patch (git diff, source files only, no tests) and mark your task done with coop-task-update.
- I will apply agent2.patch on top of my tree, build, and write the final patch.txt.
