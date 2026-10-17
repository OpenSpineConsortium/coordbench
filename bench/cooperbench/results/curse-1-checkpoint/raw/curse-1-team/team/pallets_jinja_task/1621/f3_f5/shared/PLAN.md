# Plan (agent1, lead)
Feature 3 (template path validation) is mine. It touches ONLY src/jinja2/loaders.py:
  - a new function validate_template_path() inserted directly ABOVE split_template_path (before line 24)
  - one added call "validate_template_path(template)" as the first statement in split_template_path body
Agent2 (feature 5): please do not edit those two spots; put your changes elsewhere in the file
(e.g. inside classes, or below split_template_path) and tell me which files/regions you touch via coop-send.
When done, drop your diff at /workspace/shared/agent2.patch and mark your task done.
