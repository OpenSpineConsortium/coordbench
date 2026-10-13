#!/bin/bash
# Make one task's image ready under the name CooperBench expects: pull it
# from mirror.gcr.io (Docker Hub rate-limits a cloud session's host), from
# Docker Hub itself where the mirror has no copy and the host may reach it,
# or build it from the task's Dockerfile when neither has it, then add the
# overlay (overlay_task_image.sh). An image already overlaid with the
# host's Claude Code version is left as it is; one overlaid with another
# version is overlaid again, so a pinned version change reaches every
# image. Then check that its features are gradeable here: each feature's
# tests fail on the base commit and pass with the gold patch.
# Usage: prepare_task.sh <repo, e.g. pallets_click_task> <task id>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
repo=$1
task=$2
image="akhatua/cooperbench-$(echo "${repo%_task}" | tr _ -):task$task"
mirror=${COORDBENCH_MIRROR:-mirror.gcr.io}

wanted=$("${COORDBENCH_CLAUDE_BIN:-claude}" --version | awk '{print $1}')
carried=$(docker image inspect "$image" --format '{{index .Config.Labels "cooperbench.claude"}}' 2>/dev/null || true)
if [ "$carried" = "$wanted" ]; then
    echo "$image is ready"
else
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        if docker pull -q "$mirror/$image" >/dev/null 2>&1; then
            docker tag "$mirror/$image" "$image"
            docker rmi "$mirror/$image" >/dev/null
        elif docker pull -q "$image" >/dev/null 2>&1; then
            echo "pulled $image from Docker Hub, which the mirror lacks"
        else
            echo "no registry has $image; building it from the task's Dockerfile"
            "$here/build_task_image.sh" "$work/dataset/$repo/task$task" "$image"
        fi
    fi
    "$here/overlay_task_image.sh" "$image" >/dev/null
fi
(cd "$work" && LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python scripts/check_gradeable.py "$repo/task$task" --backend docker --workers 2)
