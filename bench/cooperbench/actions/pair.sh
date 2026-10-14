#!/bin/bash
# One job of .github/workflows/bench.yml: bring CooperBench up on this
# runner, prepare the pair's task image, run one arm on one pair with the
# claude-cli driver, and run the free judge heuristics on what it left.
# The settings come in the environment: MODE (fake or real), ARM, REPO,
# TASK, FEATURES (as f1_f6), RUN_NAME, MODEL, RESCORE_DIR (where the
# workflow put an unscored pair's files, if it did), and in a real run
# CLAUDE_CODE_OAUTH_TOKEN, which run.py hands to the agents by name only.
# In a fake run the agents are bench/cooperbench/fake_claude, so no token
# is wanted and none is read.
# run.py runs as root where this user is not: the runner copies the
# team arm's shared volume from docker's own directory and writes the
# task list's mirror there, which only root reads and writes. The files
# it leaves are given back to this user, so the upload step can read them.
# The pair's verdict is the exit status (1 when an agent ended in Error),
# after the heuristics ran; the artifact is uploaded either way.
set -euo pipefail
: "${MODE:?}" "${ARM:?}" "${REPO:?}" "${TASK:?}" "${FEATURES:?}" "${RUN_NAME:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
export COOPERBENCH_DIR=${COOPERBENCH_DIR:-$HOME/cooperbench}
export PATH="$HOME/.local/bin:$PATH"
if [ "$MODE" != real ]; then
    export COORDBENCH_CLAUDE_BIN="$root/bench/cooperbench/fake_claude"
    unset CLAUDE_CODE_OAUTH_TOKEN COORDBENCH_CLAUDE_TOKEN
fi
bash bench/cooperbench/setup.sh
# A pair scored again from its artifact's files: the workflow fetched
# them beside the clone (RESCORE_DIR), since setup.sh clones CooperBench
# into an empty directory only; now they go where run.py finds a
# finished pair and scores it without starting its agents.
if [ -n "${RESCORE_DIR:-}" ] && [ -d "$RESCORE_DIR/$RUN_NAME" ]; then
    mkdir -p "$COOPERBENCH_DIR/logs"
    rm -rf "${COOPERBENCH_DIR:?}/logs/$RUN_NAME"
    mv "$RESCORE_DIR/$RUN_NAME" "$COOPERBENCH_DIR/logs/$RUN_NAME"
fi
COORDBENCH_SKIP_GRADEABLE=1 bash bench/cooperbench/prepare_task.sh "$REPO" "$TASK"

features=${FEATURES//_f/,}
features=${features#f}
as_root=()
if [ "$(id -u)" != 0 ]; then
    as_root=(sudo -E env "PATH=$PATH" "HOME=$HOME")
fi
status=0
"${as_root[@]}" env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL python3 coordbench/run.py run \
    --arm "$ARM" --repo "$REPO" --task "$TASK" --features "$features" --driver claude-cli \
    --model "${MODEL:-claude-sonnet-5-5}" --timeout-min "${TIMEOUT_MIN:-120}" \
    --disallowed-tools WebFetch,WebSearch --name "$RUN_NAME" || status=$?
"${as_root[@]}" python3 coordbench/judge.py heuristics "$COOPERBENCH_DIR/logs/$RUN_NAME" || true
if [ ${#as_root[@]} -gt 0 ]; then
    sudo chown -R "$(id -u):$(id -g)" "$COOPERBENCH_DIR/logs"
fi
exit $status
