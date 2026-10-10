# coordbench

This repository is coordbench, a harness that benchmarks ways of
coordinating coding agents (arms) on CooperBench's task pairs.
coordbench/ is the package and coordbench/README.md its contract;
bench/cooperbench/ is the kit that runs CooperBench itself; tests/ holds
the tests. README.md says the rest.

## Checks

Run `bash scripts/check.sh` before opening or updating a pull request. CI
runs the same script as the check named "check".

## Writing

- Plain prose that says why, in comments, commit messages, pull requests
  and documents alike; what the code does is read from the code.
- Short functions; names that read as English.
- No emoji. ASCII and LF in every file, each ending with a newline.
- No new dependencies: Python 3.12 or later with its standard library,
  plain bash, and GitHub's own actions pinned to a major tag. A proposal
  to add one is an issue first.
- Nothing refers to a file that does not exist.

## Pushing

main takes only pull requests: never push to main and never force-push
it. Work on a branch of your own and open a pull request from it.
