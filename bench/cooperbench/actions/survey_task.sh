#!/bin/bash
# One job of the survey mode of .github/workflows/bench.yml: bring
# CooperBench up on this runner and run CooperBench's gradeability check
# on one task (prepare_task.sh), with the fake agent in the image since no
# agent runs, then keep the check's report and the log under OUT for the
# upload. REPO and TASK come in the environment. The exit status is the
# check's, so a task that does not grade here shows as a failed job; the
# report job reads the JSON either way.
set -uo pipefail
: "${REPO:?}" "${TASK:?}" "${OUT:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
export COOPERBENCH_DIR=${COOPERBENCH_DIR:-$HOME/cooperbench}
export PATH="$HOME/.local/bin:$PATH"
export COORDBENCH_CLAUDE_BIN="$root/bench/cooperbench/fake_claude"
mkdir -p "$OUT"
bash bench/cooperbench/setup.sh || exit 1
started=$(date +%s)
bash bench/cooperbench/prepare_task.sh "$REPO" "$TASK" > "$OUT/$REPO-$TASK.log" 2>&1
status=$?
cp "$COOPERBENCH_DIR/dataset/gradeable_report_docker.json" "$OUT/$REPO-$TASK.json" 2>/dev/null || true
image="akhatua/cooperbench-$(echo "${REPO%_task}" | tr _ -):task$TASK"
size=$(docker image inspect "$image" --format '{{.Size}}' 2>/dev/null || echo 0)
printf '{"repo": "%s", "task": %s, "status": %s, "seconds": %s, "image_bytes": %s, "disk_free_bytes": %s}\n' \
    "$REPO" "$TASK" "$status" "$(( $(date +%s) - started ))" "$size" "$(df --output=avail -B1 / | tail -1 | tr -dc 0-9)" \
    > "$OUT/$REPO-$TASK.summary.json"
tail -n 30 "$OUT/$REPO-$TASK.log"
exit $status
