#!/bin/bash
# One job of the survey mode of .github/workflows/bench.yml: bring
# CooperBench up on this runner and run CooperBench's gradeability check
# on one task (prepare_task.sh), with the fake agent in the image since no
# agent runs, then keep the check's report and the log under OUT for the
# upload. REPO and TASK come in the environment. check_gradeable.py
# exits 0 whatever it found, so the exit status is read from its report:
# 0 when every feature's verdict is OK, 1 when one is not or the report
# is missing, so a task that does not grade here shows as a failed job;
# the report job reads the JSON either way.
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
if [ "$status" -eq 0 ]; then
    python3 -I - "$OUT/$REPO-$TASK.json" <<'PY' || status=1
import json, sys
try:
    report = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(1)
rows = [row for row in report if isinstance(row, dict)] if isinstance(report, list) else []
sys.exit(0 if rows and all(row.get("verdict") == "OK" for row in rows) else 1)
PY
fi
image="akhatua/cooperbench-$(echo "${REPO%_task}" | tr _ -):task$TASK"
size=$(docker image inspect "$image" --format '{{.Size}}' 2>/dev/null || echo 0)
printf '{"repo": "%s", "task": %s, "status": %s, "seconds": %s, "image_bytes": %s, "disk_free_bytes": %s}\n' \
    "$REPO" "$TASK" "$status" "$(( $(date +%s) - started ))" "$size" "$(df --output=avail -B1 / | tail -1 | tr -dc 0-9)" \
    > "$OUT/$REPO-$TASK.summary.json"
tail -n 30 "$OUT/$REPO-$TASK.log"
exit $status
