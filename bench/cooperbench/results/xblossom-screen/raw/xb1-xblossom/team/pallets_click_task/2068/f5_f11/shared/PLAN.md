# PLAN (agent1, lead)

Each agent edits its own workspace copy; the lead joins the two patches at integration.
Both features touch src/click/termui.py and src/click/_termui_impl.py, so regions:

## agent1 / feature 5 (lock_files), task 7dcfc23d30
- _termui_impl.py: three NEW Editor methods _get_lock_filename, _create_lock_file,
  _remove_lock_file, placed at the END of class Editor (after Editor.edit). Owned by agent1 alone.
- SHARED: Editor.__init__ (agent1 adds lock_files: bool = False).
- SHARED: Editor.edit_file (agent1 takes the lock before the editor starts and releases it in a finally).
- SHARED: termui.edit() signature, docstring params, and the Editor(...) call (agent1 adds lock_files).

## agent2 / feature 11 (monitor_process), task 9ea4963926
- _termui_impl.py: NEW class _ProcessMonitor, placed ABOVE class Editor. Owned by agent2 alone.
- SHARED: Editor.__init__ (agent2 adds monitor_process).
- SHARED: Editor.edit_file Popen/wait block (agent2 changes only that block).
- SHARED: termui.edit() signature, docstring params, and the Editor(...) call (agent2 adds monitor_process).

## Rules for the shared regions
- Make ONLY your own change there; the lead joins the two at integration.
- Keyword order: filename, lock_files, monitor_process (lock_files first, then monitor_process),
  in the signature, in the docstring params and in the Editor(...) call.
- No new top-level imports (they would collide at the top of _termui_impl.py): import inside the method.
- Do not touch tests/ or docs/ in the patch (patch = src only).
- When done: write `git diff -- src > /workspace/shared/agent2.patch` (agent2) from your workspace,
  set your task to done with a certificate note (diff --stat, test command, exit code), and tell agent1.
