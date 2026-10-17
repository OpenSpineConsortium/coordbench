# Plan (agent1 = lead)
Feature 1 (agent1, done in my tree): Editor.edit_file(filename) is renamed Editor.edit_files(filenames: Iterable[str]);
the Popen call becomes subprocess.Popen(args=f"{editor} {quoted_filenames}", env=environ, shell=True);
Editor.edit() calls self.edit_files((name,)); termui.edit gets overloads, filename: str | Iterable[str] | None,
a str is wrapped in a tuple, then ed.edit_files(filenames=filename).
Feature 6 (agent2): process_priority. Edit against the ORIGINAL tree; do not rename edit_file yourself.
Merge rule: the lead applies agent2.patch by hand where hunks overlap (Popen line, edit() signature, Editor(...) call)
and adds process_priority to the edit() overloads. Source files only; no test files in the patches.
