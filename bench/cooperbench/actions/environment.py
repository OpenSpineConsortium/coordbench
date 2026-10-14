"""The gate before a real run, for .github/workflows/bench.yml's plan job:
`environment.py NAME` exits 0 when the repository's environment NAME
exists and has a required reviewer, 1 otherwise. The run-real jobs wait
in the environment bench for a reviewer's approval, but GitHub makes an
environment a workflow names before anyone has made it, with no rule, and
such a job would run unapproved on the accounts' tokens; so a real
dispatch stops here, before any job of the matrix, until the owner has
made the environment with the owner as its required reviewer. Read with
GITHUB_TOKEN (actions: read) as artifacts.py reads the artifacts."""

import sys
import urllib.error

import artifacts


def has_required_reviewer(environment):
    rules = environment.get("protection_rules", []) if isinstance(environment, dict) else []
    return any(isinstance(rule, dict) and rule.get("type") == "required_reviewers" and rule.get("reviewers")
               for rule in rules)


def command_check(conf, name, opener=None):
    try:
        environment = artifacts.api_get(conf, f"/environments/{name}", opener)
    except urllib.error.HTTPError as problem:
        if problem.code == 404:
            print(f"the environment {name} does not exist yet; make it with a required reviewer before a real run")
            return 1
        print(f"could not read the environment {name} ({problem}); a real run does not start on a guess")
        return 1
    except (urllib.error.URLError, OSError, ValueError) as problem:
        print(f"could not read the environment {name} ({problem}); a real run does not start on a guess")
        return 1
    if not has_required_reviewer(environment):
        print(f"the environment {name} has no required reviewer, so its jobs would run unapproved; add one first")
        return 1
    print(f"the environment {name} has a required reviewer")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.splitlines()[1].strip(), file=sys.stderr)
        return 2
    return command_check(artifacts.settings(), argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
