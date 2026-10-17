# Plan (agent1 lead)
- agent1 (feature 7): edits Str::first and Str::last in crates/typst/src/foundations/str.rs, adds a pattern enum + helper right below those methods and an error fn next to string_is_empty at the bottom of the file.
- agent2 (feature 8): tell agent1 which regions you touch; keep edits disjoint from first/last unless coordinated. Deliver /workspace/shared/agent2.patch (git diff vs clean HEAD) when done.
- agent1 applies agent2.patch, builds, runs tests, writes patch.txt.
