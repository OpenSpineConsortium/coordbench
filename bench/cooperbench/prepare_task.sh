#!/bin/bash
# Make one task's image ready under the name CooperBench expects: pull it
# from mirror.gcr.io (Docker Hub rate-limits this host), or build it from
# the task's Dockerfile when the mirror lacks it, then add the overlay
# (overlay_task_image.sh). Then check that its features are gradeable here:
# each feature's tests fail on the base commit and pass with the gold patch.
# Usage: prepare_task.sh <repo, e.g. pallets_click_task> <task id>
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
repo=$1
task=$2
image="akhatua/cooperbench-$(echo "${repo%_task}" | tr _ -):task$task"

if docker image inspect "$image" --format '{{index .Config.Labels "cooperbench.overlay"}}' 2>/dev/null | grep -q cloud-session; then
    echo "$image is ready"
else
    if docker pull -q "mirror.gcr.io/$image" >/dev/null 2>&1; then
        docker tag "mirror.gcr.io/$image" "$image"
        docker rmi "mirror.gcr.io/$image" >/dev/null
    else
        echo "the mirror has no $image; building it from the task's Dockerfile"
        "$here/build_task_image.sh" "$work/dataset/$repo/task$task" "$image"
    fi
    "$here/overlay_task_image.sh" "$image" >/dev/null
fi
(cd "$work" && LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python scripts/check_gradeable.py "$repo/task$task" --backend docker --workers 2)
