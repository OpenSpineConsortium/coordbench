# Plan (agent1, lead; feature 5 = lock_files for click.edit)

Both features likely touch Editor (src/click/_termui_impl.py) and edit()
(src/click/termui.py). To keep hunks disjoint:

agent1 (feature 5, lock_files) owns:
  - new methods _get_lock_filename / _create_lock_file / _remove_lock_file,
    placed right AFTER Editor.get_editor() and BEFORE Editor.edit_file().
  - Editor.__init__: appends `lock_files: bool = False` as the LAST parameter
    and `self.lock_files = lock_files` as the LAST line of the body.
  - Editor.edit_file: wraps the existing Popen block in lock handling.
  - termui.edit(): appends `lock_files: bool = False` as the LAST parameter,
    one `:param lock_files:` docstring entry at the END of the params list,
    and passes lock_files=lock_files in the Editor(...) call.

agent2 (feature 11): please keep your changes away from those exact lines.
If you must add a parameter, add it BEFORE lock_files is not needed: put yours
right after `extension` (before lock_files), I will merge by hand. Tell me
(coop-send agent1) which lines/functions your feature touches and I will adapt.
Drop your final patch at /workspace/shared/agent2.patch (git diff of src only,
no tests) and mark your task done.
