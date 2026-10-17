# Plan
Both features edit only outlines/types/__init__.py and each adds one definition.
To keep the hunks disjoint:
- agent1 (feature 7, credit_card): appended at the very END of the file, after the isbn definition.
- agent2 (feature 10, hash_sha256): insert it right AFTER the email definition and BEFORE the "# Matches any ISBN number" comment block. Do NOT append at the end of the file.
Keep a blank line between definitions, no other edits to the file.
When done, save your patch (git diff) to /workspace/shared/agent2.patch, mark the task done, and send agent1 a message.
