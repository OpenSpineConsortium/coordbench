#!/bin/bash
# After pair.sh, however it ended: scrub the pair's run directory once
# more and say whether it may be published. run.py scrubs the agents'
# credential out of a pair's files when the pair finishes, and nothing
# scrubs a pair that run.py was killed in the middle of; so this runs
# `run.py scrub` over RUN_DIR (the files given back to this user after,
# as pair.sh gives them), which rewrites what still holds the credential
# and reads every file, archives included, to see that none does. Exit 0
# when the directory is clean and may be published; 1 when it is not, or
# a file could not be read, in which case the directory is deleted so no
# later step can upload it. RUN_DIR comes in the environment, and in a
# real run CLAUDE_CODE_OAUTH_TOKEN, which run.py reads by name only; in a
# fake run no credential is set and there is nothing to find.
set -uo pipefail
: "${RUN_DIR:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
if [ ! -d "$RUN_DIR" ]; then
    echo "nothing to publish: $RUN_DIR was not written"
    exit 1
fi
as_root=()
if [ "$(id -u)" != 0 ]; then
    as_root=(sudo -E env "PATH=$PATH" "HOME=$HOME")
fi
"${as_root[@]}" env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL python3 coordbench/run.py scrub --run-dir "$RUN_DIR"
status=$?
if [ ${#as_root[@]} -gt 0 ]; then
    sudo chown -R "$(id -u):$(id -g)" "$RUN_DIR" || true
fi
if [ "$status" -ne 0 ]; then
    echo "::error::a file of the pair holds the agents' credential or could not be checked; the pair is not published"
    "${as_root[@]}" rm -rf "$RUN_DIR"
    exit 1
fi
exit 0
