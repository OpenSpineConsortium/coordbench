# Plan (agent1, lead)

I (agent1) implement feature 6: process_priority (CPU priority of the editor process) in
src/click/_termui_impl.py (Editor.__init__ new kwarg, a small helper placed right before
class Editor, and the Popen call in Editor.edit_file) and src/click/termui.py (edit()
signature, docstring, and the Editor(...) call).

To keep our changes in disjoint regions:
- Feature 6 adds ONE new keyword parameter `process_priority` at the END of both
  Editor.__init__ and click.edit() signatures, and one `self.process_priority = ...` line
  after `self.extension = extension`. The helper goes right above `class Editor`.
  It edits only the Popen line in edit_file.
- Agent2: put any new parameter of yours AFTER process_priority only if needed (or tell me and
  I place mine after yours), and put your helpers elsewhere than directly above `class Editor`.
  Please send me a short message with the regions of Editor/edit() you touch.
- Drop your patch at /workspace/shared/agent2.patch (git diff against the original tree) when done.
