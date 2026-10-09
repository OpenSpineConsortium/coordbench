---
name: coordbench-solo
description: The solo protocol of a coordbench run, CooperBench's baseline setting. Use when you alone implement every feature of a task in /workspace/repo and need to know how to hand in the result.
---

# Working solo

You are the only agent of this run, and every feature the task names is
yours. There is nobody to coordinate with, so this arm has no
coordination tools: the MCP server `coordbench` lists none for it, and
no `coop-*` shell command is installed.

## The protocol

1. Read every feature's spec; the prompt gives each under its own
   "## Feature <n>" heading.
2. Implement all of them in `/workspace/repo`. You are free to read
   files, run shell commands, and run tests as needed.

## Submitting

Before you exit, write your final unified diff to
`/workspace/repo/patch.txt`; the evaluator reads that file and nothing
else.

```bash
cd /workspace/repo
git diff > patch.txt
cat patch.txt
```

The diff holds only the source files you changed to implement the
features: no reproduction scripts, no scratch tests, no helper files, and
no changes to test files unless the task asks for them.
