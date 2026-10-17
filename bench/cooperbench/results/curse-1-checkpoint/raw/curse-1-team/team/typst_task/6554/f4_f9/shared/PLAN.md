# Plan (agent1, lead)
- agent1 owns feature 4: repeat param on str.first / str.last. Edits ONLY the two fns `first` and `last` (plus a new error helper next to string_is_empty) in crates/typst/src/foundations/str.rs.
- agent2 owns feature 9. Keep edits out of fns `first` and `last`; if you touch str.rs, add new items elsewhere and tell me which region via coop-send.
- When done: agent2 writes the diff to /workspace/shared/agent2.patch (git diff of own workspace, source files only, no tests/scratch) and marks task 444bdfa7d5 done. I then git apply it, build, and write patch.txt.
