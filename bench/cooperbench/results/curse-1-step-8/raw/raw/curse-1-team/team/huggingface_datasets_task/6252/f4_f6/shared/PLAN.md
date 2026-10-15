# PLAN (agent1, team-lead)

Both features edit src/datasets/features/image.py only. To keep our hunks disjoint:

Feature 4 (agent1): `crop_margin: Optional[int] = None` on the Image dataclass.
- Dataclass field: one line directly AFTER `mode: Optional[str] = None` (before `decode`).
- Docstring: one `crop_margin` entry in Args, directly AFTER the `mode` entry (before `decode`).
- decode_example: a crop block directly AFTER `image.load()  # to avoid "Too many open files" errors`
  and BEFORE the `if self.mode and self.mode != image.mode:` conversion.

Feature 6 (agent2): please keep your edits OUT of those three places:
- Your dataclass field: AFTER `decode: bool = True` (before `id`), not next to `mode`.
- Your docstring Args entry: AFTER the `decode` entry (end of the Args list).
- Your decode_example work: AFTER the mode-conversion block, before `return image`
  (or elsewhere not touching the lines above).
If you need a different place, tell me with coop-send before editing.

When done, write `git diff > /workspace/shared/agent2.patch` (diff of your tree only, relative to the
repo root) and mark your task done with coop-task-update. I will git apply it onto my tree.
