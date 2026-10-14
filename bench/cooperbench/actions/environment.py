"""The gate before a real run, for .github/workflows/bench.yml's plan job:
`environment.py NAME` exits 0 when the repository's environment NAME
exists and has a required reviewer who may approve this dispatch, 1
otherwise. The approve job waits in the environment bench for a
reviewer's approval, but GitHub makes an environment a workflow names
before anyone has made it, with no rule, and the run-real jobs behind
that job would then run unapproved on the accounts' tokens; so a real
dispatch stops here, before any job of the matrix, until the owner has
made the environment with the owner as its required reviewer. A rule
that forbids self-review while the dispatching actor (GITHUB_ACTOR) is
its only reviewer stops it too, since the run would wait for an approval
nobody may give. Read with GITHUB_TOKEN (actions: read) as artifacts.py
reads the artifacts."""

import os
import sys
import urllib.error

import artifacts


def reviewer_rules(environment):
    rules = environment.get("protection_rules", []) if isinstance(environment, dict) else []
    return [rule for rule in rules if isinstance(rule, dict) and rule.get("type") == "required_reviewers"]


def has_required_reviewer(environment):
    return any(rule.get("reviewers") for rule in reviewer_rules(environment))


def only_the_actor_reviews(environment, actor):
    """True when a rule forbids the reviewer's own approval and every
    reviewer it names is the actor, a user by login; a team among the
    reviewers may hold someone else, so it lets the run through."""
    for rule in reviewer_rules(environment):
        if not rule.get("prevent_self_review"):
            continue
        reviewers = [r for r in rule.get("reviewers") or [] if isinstance(r, dict)]
        logins = [(r.get("reviewer") or {}).get("login") for r in reviewers if r.get("type") == "User"]
        if reviewers and len(logins) == len(reviewers) and all(login == actor for login in logins):
            return True
    return False


def command_check(conf, name, opener=None, actor=None):
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
    if actor and only_the_actor_reviews(environment, actor):
        print(f"the environment {name} forbids self-review and {actor}, who dispatched this run, is its only "
              f"reviewer, so nobody could approve it; untick Prevent self-review or add another reviewer")
        return 1
    print(f"the environment {name} has a required reviewer")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.splitlines()[1].strip(), file=sys.stderr)
        return 2
    return command_check(artifacts.settings(), argv[0], actor=os.environ.get("GITHUB_ACTOR"))


if __name__ == "__main__":
    raise SystemExit(main())
