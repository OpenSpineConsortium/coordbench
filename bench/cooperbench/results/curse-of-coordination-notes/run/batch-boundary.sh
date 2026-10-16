#!/bin/bash
# Between two batches of the curse-1 full run: check that nothing runs,
# move the bench checkout to origin/main, put back harness errors, list
# timed-out suites, refresh the logs archive when one is named, release
# the next pairs and start the image loop again. The checkout moves only
# while the image loop is stopped, since bash reads a running script from
# its file. Usage: batch-boundary.sh CHECKOUT PAIRS [ARCHIVE], PAIRS comma
# separated as queue.py resume --pairs takes them, ARCHIVE a .tar.gz for
# the logs without workspaces (the project's shared folder held one).
set -u
B=$1; PAIRS=$2; ARCHIVE=${3:-}
LOGS=$HOME/cooperbench/logs
Q=$LOGS/curse-1-queue.jsonl
LOOP=$B/bench/cooperbench/results/curse-of-coordination-notes/run/images-full.sh
open=$(grep -cE '"status": "(pending|running)"' "$Q")
[ "$open" -eq 0 ] || { echo "stop: $open items still pending or running"; exit 1; }
old=$(sed 's/[^0-9]//g' "$LOGS/images-full.pid" 2>/dev/null)
if [ -n "$old" ] && kill -0 "$old" 2>/dev/null; then
  echo "stop: the image loop $old still runs"; exit 1
fi
[ -z "$(git -C "$B" status --porcelain --untracked-files=no)" ] || { echo "stop: the checkout has changes"; exit 1; }
git -C "$B" fetch -q origin main && git -C "$B" checkout -q --detach origin/main
echo "bench checkout at $(git -C "$B" log --oneline -1)"
cd "$B" || exit 1
cp -p "$Q" "$LOGS/curse-1-queue.before-batch-$(date -u +%Y%m%dT%H%M).jsonl"
python3 bench/cooperbench/session/queue.py requeue "$Q" --harness-only
echo "timed-out suites:"; grep -l "Command timed out after" "$LOGS"/curse-1-*/*/*/*/*/eval.json || echo "  none"
if [ -n "$ARCHIVE" ]; then
  (cd "$LOGS/.." && tar --exclude='*/workspaces' --exclude='*.lock' -czf "$ARCHIVE.new" logs) \
    && mv "$ARCHIVE.new" "$ARCHIVE" && echo "archive refreshed: $(du -h "$ARCHIVE" | cut -f1)"
fi
python3 bench/cooperbench/session/queue.py resume "$Q" --pairs "$PAIRS"
setsid nohup bash "$LOOP" "$B" "$Q" >> "$LOGS/images-full.log" 2>&1 < /dev/null &
sleep 2
pid=$(pgrep -f "^bash $LOOP" | head -1)
echo "$pid" > "$LOGS/images-full.pid"
echo "image loop pid $pid"
grep -cE '"status": "pending"' "$Q" | sed 's/^/pending items: /'
df -h / | tail -1
