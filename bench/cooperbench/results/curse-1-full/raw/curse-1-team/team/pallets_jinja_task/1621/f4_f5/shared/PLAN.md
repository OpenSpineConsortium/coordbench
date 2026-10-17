# Plan (agent1, lead)

Both features touch FileSystemLoader in src/jinja2/loaders.py. To keep the diffs in disjoint regions:

- agent1 (feature 4, search path priority) owns ONLY:
  * the "searchpath" parameter annotation line in FileSystemLoader.__init__,
  * the lines that normalise "searchpath" into self.searchpath (the "if not isinstance(searchpath, abc.Iterable)" block and the "self.searchpath = ..." line),
  * the docstring paragraphs about the path / list of paths, and the ":param searchpath:" entry.
- agent2 (feature 5) owns everything else in the class: any NEW __init__ parameter (add it AFTER followlinks, do not reorder existing params), its assignment placed AFTER "self.followlinks = followlinks", its own ":param" entry placed after ":param followlinks:", its own versionchanged note after the existing one, and any change to get_source / list_templates.
- Do not reformat or touch lines you do not own. self.searchpath stays a plain list of str.

Integration: agent2 drops a git diff at /workspace/shared/agent2.patch and marks the task done; agent1 applies it and writes patch.txt.
