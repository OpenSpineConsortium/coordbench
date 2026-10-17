# Plan (agent1, lead)
- agent1 (feature 6, path_transform): edits src/jinja2/loaders.py only. Adds BaseLoader.path_transform class attr + _apply_path_transform helper; adds path_transform kwarg (last param) to FileSystemLoader, DictLoader, FunctionLoader, PackageLoader, PrefixLoader, ChoiceLoader; applies it at the top of get_source/load.
  In FileSystemLoader touches: signature, one line after self.followlinks, one :param: docstring line, a versionchanged note at end of docstring, get_source body.
- agent2 (feature 7, searchpath expansion): edits only the self.searchpath line (+ intro docstring) in FileSystemLoader.__init__ and tests/test_loader.py. Do not touch signature.
- Integration: agent2 drops git diff at /workspace/shared/agent2.patch; agent1 applies it, runs tests, writes final patch.txt.
