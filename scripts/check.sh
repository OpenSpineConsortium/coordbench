#!/usr/bin/env bash
# The repository's checks, run by CI (.github/workflows/ci.yml, the job
# "check") and by hand before a pull request. It runs from the repository's
# root whatever the caller's directory, since the tests find the package
# and the kit by their place under it. Every file comes first, held to
# CLAUDE.md's rule (ASCII, LF endings, a final newline) as
# symphony-protocol's check holds its own, since a rule no check enforces
# drifts; then the vendored copy of symphony-protocol, held to its record;
# then the whole test suite.
set -eu
cd "$(dirname "$0")/.."

python3 - <<'PY'
"""One line per file that is not plain ASCII with LF endings and a final
newline; exit 1 if there is any."""
import subprocess
import sys

listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                        capture_output=True, check=True).stdout
problems = []
for name in sorted({n.decode() for n in listed.split(b"\0") if n}):
    try:
        data = open(name, "rb").read()
    except FileNotFoundError:
        continue  # removed from the working tree, still in the index
    if any(byte > 127 for byte in data):
        problems.append(f"{name}: a byte outside ASCII")
    if b"\r" in data:
        problems.append(f"{name}: a carriage return; use LF line endings")
    if data and not data.endswith(b"\n"):
        problems.append(f"{name}: no newline at the end")
print("\n".join(problems) or "plain text ok")
sys.exit(1 if problems else 0)
PY

# The sdlc arm's copy of symphony-protocol must be byte for byte what
# coordbench/vendor/VENDORED records. A change to it is made in
# OpenSpineConsortium/symphony-protocol and arrives here by that repository's
# sync pull request, so a hand edit fails here, before any test runs.
# One that rewrote VENDORED to match passes this line and fails
# tests/test_coordbench_vendor.py, which compares the copy with the library
# itself in the clone SDLC_PROTOCOL names, which CI makes
# (.github/workflows/ci.yml).
python3 coordbench/vendor/sdlc_protocol/vendoring.py verify coordbench/vendor

python3 -m unittest discover -s tests

echo "checks ok"
