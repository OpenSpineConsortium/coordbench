# Plan (agent1, lead)
- Feature 2 (agent1): Context(on_exit=...), Command.make_context(on_exit=...), Context.close() fires on_exit once per use.
- Feature 7 (agent2): validate_nesting (Context.__init__ last kwarg, __exit__, close, _make_sub_context, _resolve_context).
- Shared hot spots: Context.__init__ signature (on_exit first, validate_nesting last), Context.close(), Context.__exit__, shell_completion._resolve_context.
- Agent2 delivers a clean git diff vs pristine HEAD in /workspace/shared/agent2.patch; agent1 applies it on top of own work, resolves hunks by hand, runs tests, writes patch.txt.
