#!/usr/bin/env bash
# The repository's checks, run by CI (.github/workflows/ci.yml, the job
# "check") and by hand before a pull request. It runs from the repository's
# root whatever the caller's directory, since the tests find the package
# and the kit by their place under it.
set -eu
cd "$(dirname "$0")/.."

# The sdlc arm's copy of sdlc-protocol must be byte for byte what
# coordbench/vendor/VENDORED records. A change to it is made in
# OpenSpineConsortium/sdlc-protocol and arrives here by that repository's
# sync pull request, so a hand edit fails here, before any test runs.
python3 coordbench/vendor/sdlc_protocol/vendoring.py verify coordbench/vendor

python3 -m unittest discover -s tests

echo "checks ok"
