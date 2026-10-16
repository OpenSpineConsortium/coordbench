#!/bin/bash
# For the session's Monitor tool: one line per change of a curse-1 queue
# item that is not paused, the image loop's new log lines (but its waits),
# the moment no pending item is left, and the image loop's exit. It reads
# the loop's pid when it starts, so start it again after each new loop.
LOGS=$HOME/cooperbench/logs
Q=$LOGS/curse-1-queue.jsonl; L=$LOGS/images-full.log
snap() { python3 -I - "$Q" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    if not line.strip():
        continue
    item = json.loads(line)
    if item["status"] == "paused":
        continue
    stage = "finishing" if item.get("finishing_at") else ("prepared" if item.get("prepared_at") else "")
    print(item["id"], item["status"], stage, item.get("worker", "-"), item.get("verdict", ""),
          str(item.get("harness_error") or "")[:60], str(item.get("error") or "")[:80])
PY
}
pending() { grep -c '"status": "pending"' "$Q"; }
prev=$(snap); n=$(wc -l < "$L"); gone=0; had=$(pending)
pid=$(sed 's/[^0-9]//g' "$LOGS/images-full.pid")
while true; do
  sleep 30
  cur=$(snap); comm -13 <(echo "$prev" | sort) <(echo "$cur" | sort); prev=$cur
  m=$(wc -l < "$L"); if [ "$m" -gt "$n" ]; then sed -n "$((n+1)),${m}p" "$L" | grep -v "^.\{8\} waiting for" | sed 's/^/image loop: /'; n=$m; fi
  now=$(pending); if [ "$had" -gt 0 ] && [ "$now" -eq 0 ]; then echo "queue: no pending item left"; fi; had=$now
  if [ "$gone" = 0 ] && ! kill -0 "$pid" 2>/dev/null; then echo "image loop process exited"; gone=1; fi
done
