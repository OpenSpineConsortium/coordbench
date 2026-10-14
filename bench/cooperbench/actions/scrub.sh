#!/bin/bash
# After pair.sh, however it ended: scrub the pair's run directory once
# more and say whether it may be published. run.py scrubs the agents'
# credential out of a pair's files when the pair finishes, and nothing
# scrubs a pair that run.py was killed in the middle of; so this runs
# `run.py scrub` over RUN_DIR (the files given back to this user after,
# as pair.sh gives them), which rewrites what still holds the credential
# and reads every file, archives and git objects included, to see that
# none does. First whatever may still be writing there is stopped: a
# run.py that outlived pair.sh and the agent containers it did not tear
# down (every one carries the label coordbench.run), which stream to
# the host until their own timeout and would write after the rewrite.
# Exit 0 when the directory is clean and may be published; 1 when it is
# not, or a file could not be read, in which case the directory is
# deleted so no later step can upload it. RUN_DIR comes in the
# environment, and in a real run CLAUDE_CODE_OAUTH_TOKEN, which run.py
# reads by name only; in a fake run no credential is set and there is
# nothing to find.
set -uo pipefail
: "${RUN_DIR:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
as_root=()
if [ "$(id -u)" != 0 ]; then
    as_root=(sudo -E env "PATH=$PATH" "HOME=$HOME")
fi
# The bracket keeps the pattern out of its own command line, which
# pkill -f would otherwise match in the sudo above it and kill.
"${as_root[@]}" pkill -f "[c]oordbench/run.py run" && echo "stopped a run.py still running" || true
containers=$("${as_root[@]}" docker ps -q --filter label=coordbench.run 2>/dev/null || true)
if [ -n "$containers" ]; then
    echo "removing agent containers still up: $containers"
    # shellcheck disable=SC2086
    "${as_root[@]}" docker rm -f $containers > /dev/null || true
fi
if [ ! -d "$RUN_DIR" ]; then
    echo "nothing to publish: $RUN_DIR was not written"
    exit 1
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
