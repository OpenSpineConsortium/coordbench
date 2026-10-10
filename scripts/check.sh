#!/usr/bin/env bash
# The repository's checks, run by CI (.github/workflows/ci.yml, the job
# "check") and by hand before a pull request. It runs from the repository's
# root whatever the caller's directory, since the tests find the package
# and the kit by their place under it.
set -eu
cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests

echo "checks ok"
