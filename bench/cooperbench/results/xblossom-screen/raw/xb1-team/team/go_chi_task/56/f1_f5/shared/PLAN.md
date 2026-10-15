# Plan (agent1, lead)
- agent1: feature 1 (Allow header on 405): context.go, tree.go (reverseMethodMap, findRoute 405 branches), mux.go (405 handler plumbing), middleware/allowed_methods.go (new).
- agent2: feature 5 as given in its task; keep edits in regions of shared files that avoid the 405 plumbing above. Drop a git diff (source files only, no test scratch) at /workspace/shared/agent2.patch and mark the task done.
- agent1 then applies agent2.patch on top, builds, runs tests, writes patch.txt from the merged tree.
