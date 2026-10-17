# Plan (agent1, lead)
- Feature 1 (agent1): optional context string right after {% trans %}; edits only
  InternationalizationExtension.parse (head + _make_node call) and _make_node in src/jinja2/ext.py.
- Feature 8 (agent2): see its own spec; keep changes in regions disjoint from the above; reply with the regions you touch.
- Integration: agent2 drops `git diff > /workspace/shared/agent2.patch` (source only, no tests); agent1 applies it on top
  of own work, checks the merged tree imports and works, writes patch.txt, then touches .coordbench-done.
