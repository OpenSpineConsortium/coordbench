#!/bin/bash
# Task images for the full run, one at a time, ahead of the lanes: walk
# the tasks of the open items (pending or running) of the queues in their
# order; before each pull, remove the images of tasks no queue still
# needs and wait until the disk has room for the image and a run; after
# each, put back the items a lane failed for want of that image. Ends
# when every open item's task is ready and the queues hold no pending item.
# Usage: images-full.sh CHECKOUT QUEUE [QUEUE ...]
set -u
checkout=$1; shift
queues=("$@")
export COORDBENCH_SKIP_GRADEABLE=1
cd "$checkout" || exit 1
log() { echo "$(date -u +%H:%M:%S) $*"; }

# Gigabytes an image takes once pulled and overlaid; others about 3.
size_of() {
  case "$1" in
    typst_task:*) echo 11 ;;
    huggingface_datasets_task:*) echo 14 ;;
    dottxt_ai_outlines_task:*) echo 13 ;;
    *) echo 3 ;;
  esac
}

open_tasks() {
  python3 - "${queues[@]}" <<'EOF'
import json, sys
seen = []
for queue in sys.argv[1:]:
    for line in open(queue):
        if not line.strip():
            continue
        item = json.loads(line)
        key = f"{item['repo']}:{item['task']}"
        if item["status"] in ("pending", "running") and key not in seen:
            seen.append(key)
print(" ".join(seen))
EOF
}

image_of() {
  local repo=${1%%:*} task=${1##*:}
  echo "akhatua/cooperbench-$(echo "${repo%_task}" | tr _ -):task$task"
}

ready() {
  [ -n "$(docker image inspect "$(image_of "$1")" --format '{{index .Config.Labels "cooperbench.claude"}}' 2>/dev/null)" ]
}

free_gb() { df -BG --output=avail / | tail -1 | tr -dc 0-9; }

remove_unneeded() {
  # Local, since the caller loops over task too: a global task here
  # leaves the caller holding the last open task, whose image it then
  # prepares again and again in place of the one it found missing.
  local image task
  local needed=" $(open_tasks) "
  for image in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^akhatua/cooperbench-'); do
    local keep=no
    for task in $needed; do
      [ "$(image_of "$task")" = "$image" ] && keep=yes
    done
    if [ "$keep" = no ]; then
      log "removing $image, which no open item needs"
      docker rmi "$image" >/dev/null 2>&1 || log "could not remove $image (a container still uses it)"
    fi
  done
  docker image prune -f >/dev/null 2>&1; docker builder prune -f >/dev/null 2>&1
}

requeue_image_failures() {
  local repo=${1%%:*} task=${1##*:}
  for queue in "${queues[@]}"; do
    ids=$(python3 -c "
import json, sys
items = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(' '.join(i['id'] for i in items if i['status'] == 'failed' and i['repo'] == sys.argv[2]
               and str(i['task']) == sys.argv[3] and 'no task image' in str(i.get('error', ''))))" "$queue" "$repo" "$task")
    [ -n "$ids" ] && python3 bench/cooperbench/session/queue.py requeue "$queue" --status failed --ids $ids
  done
}

while :; do
  pending=0
  for task in $(open_tasks); do
    pending=1
    ready "$task" && continue
    need=$(( $(size_of "$task") + 8 ))
    remove_unneeded
    while [ "$(free_gb)" -lt "$need" ]; do
      log "waiting for $need GB free to pull $task ($(free_gb) GB free)"
      sleep 120
      remove_unneeded
    done
    log "preparing $task"
    bash bench/cooperbench/prepare_task.sh "${task%%:*}" "${task##*:}" 2>&1 | tail -2
    rc=${PIPESTATUS[0]}
    log "done $task rc=$rc, $(free_gb) GB free"
    [ "$rc" = 0 ] && requeue_image_failures "$task"
    # An image a prepare left unready (a failed pull, no overlay label)
    # is tried again after a pause, so a lasting failure cannot spin.
    ready "$task" || sleep 120
    continue 2
  done
  [ "$pending" = 0 ] && { log "no open item left; the image loop ends"; break; }
  sleep 120
done
