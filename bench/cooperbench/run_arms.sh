#!/bin/bash
# Run the four arms of the authors' ablation on one task of the flash subset,
# one after another so the arms are interleaved in time, each scored by
# CooperBench's own evaluation as it finishes:
#   solo       one agent implements both features
#   coop       two agents, each in its own container, messages only
#   coop-git   the same, plus a shared git remote they push to and merge from
#   team       a lead and a member with a shared task list and scratchpad
# The agents are Claude Code, on the subscription token in
# CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`); an API key, if one is
# set, would win over it and bill the API, so it is unset here. A pair whose
# agents stopped on an error (a usage limit, say) is run again by the next
# invocation of the same command; finished pairs are kept.
# Usage: run_arms.sh <repo> <task id> [arm ...]
set -euo pipefail
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
model=${MODEL:-claude-sonnet-5-5}
tag=${RUN_TAG:-cc}
repo=$1
task=$2
shift 2
arms=("$@")
[ ${#arms[@]} -gt 0 ] || arms=(solo coop coop-git team)
: "${CLAUDE_CODE_OAUTH_TOKEN:?set CLAUDE_CODE_OAUTH_TOKEN in the environment settings first}"

run() {
    local name=$1
    shift
    (cd "$work" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL LITELLM_LOCAL_MODEL_COST_MAP=True \
        .venv/bin/cooperbench run -n "$name" -s flash -r "$repo" -t "$task" \
        -a claude_code -m "$model" --backend docker -c "${CONCURRENCY:-1}" --eval-concurrency 1 "$@")
}

for arm in "${arms[@]}"; do
    case $arm in
        solo) run "flash-solo-$tag" --setting solo ;;
        coop) run "flash-coop-$tag" --setting coop ;;
        coop-git) run "flash-coop-git-$tag" --setting coop --git ;;
        team) run "flash-team-$tag" --setting team ;;
        *) echo "unknown arm: $arm" >&2; exit 2 ;;
    esac
done
