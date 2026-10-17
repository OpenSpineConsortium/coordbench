# Plan (agent1, lead)
Both features edit src/datasets/features/features.py only. To avoid conflicts:
- agent1 (feature 2, custom require_decoding criteria) owns: the require_decoding() function
  (signature + body, ~line 1181), Features.__init__ / __setitem__ (the _column_requires_decoding lines),
  and a NEW method Features.set_custom_decoding_criteria placed right after Features.update().
- agent2 (feature 4): please keep your edits OUT of those regions. Tell me (coop-send agent1) which
  functions/lines you touch; put new code elsewhere (e.g. after __reduce__ or near the end of Features).
- When done, agent2 writes git diff to /workspace/shared/agent2.patch and marks task done.
- agent1 applies it on top of own work, runs import test, writes final patch.txt.
