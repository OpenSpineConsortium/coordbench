#!/bin/bash
# Prepare every task of the flash subset in turn and check that its gold
# patches grade here (prepare_task.sh), keeping each task's report and
# freeing its image afterwards, since the disk holds only a few task
# images at once. Finished tasks are skipped, so after a stop (a session's
# background command ends after two hours) the same command carries on.
# It stops before a task when less than 20 GB is free.
# Usage: survey.sh [out-dir]   (default ~/cooperbench-survey)
set -u
here=$(cd "$(dirname "$0")" && pwd)
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
export COOPERBENCH_DIR=$work
out=${1:-$HOME/cooperbench-survey}
mkdir -p "$out"
python3 -c "
import json
for t in json.load(open('$work/dataset/subsets/flash.json'))['tasks']:
    print(t['repo'], t['task_id'])
" | while read -r repo task; do
    key=${repo}_$task
    [ -f "$out/$key.done" ] && continue
    free=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
    if [ "$free" -lt 20 ]; then echo "stopping: only ${free}G free" | tee -a "$out/summary.txt"; break; fi
    start=$(date +%s)
    "$here/prepare_task.sh" "$repo" "$task" > "$out/$key.log" 2>&1
    rc=$?
    cp "$work/dataset/gradeable_report_docker.json" "$out/$key.json" 2>/dev/null
    echo "$repo $task rc=$rc $(( $(date +%s) - start ))s $(grep -E '^(OK|GOLD|BASE)' "$out/$key.log" | tr '\n' ' ')" | tee -a "$out/summary.txt"
    image="akhatua/cooperbench-$(echo "${repo%_task}" | tr _ -):task$task"
    docker rmi -f "$image" >/dev/null 2>&1
    docker image prune -f >/dev/null 2>&1
    docker builder prune -af >/dev/null 2>&1
    touch "$out/$key.done"
done
echo "survey finished: $out/summary.txt"
