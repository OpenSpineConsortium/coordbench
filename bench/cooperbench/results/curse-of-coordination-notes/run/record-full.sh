#!/bin/bash
# The full run's record, the commands of bench/cooperbench/session/README.md
# ("How much to run") over every pair of the 43: subset.py copies each
# finished run of the sample into logs of their own, then report.py,
# summary.py and bundle.py read those. The step's 40 items carry the
# step's sample record and the other 175 none, so a copy of the queue
# gives those 175 the same draw with sample 43, the live queue unchanged.
# The arms' config.json are the pilot's, as in ../../curse-1-step-8/raw.
# Usage: record-full.sh CHECKOUT OUT LOGS_SAMPLE PUBLISHED, LOGS_SAMPLE new
# (its path is written into pairs.csv, so not a scratch path), PUBLISHED
# cooperbench/website's public/static/data/coop/index.json, whose sha256
# begins d72e4b7399ff83ec, as the queue's sample record says.
set -u
R=$1; OUT=$2; L=$3; PUBLISHED=$4
LOGS=$HOME/cooperbench/logs
STEP=$R/bench/cooperbench/results/curse-1-step-8
[ -e "$L" ] && { echo "stop: $L exists"; exit 1; }
mkdir -p "$OUT/q"
python3 -I - "$LOGS/curse-1-queue.jsonl" "$OUT/q/curse-1-queue.jsonl" <<'PY'
import json, sys
items = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
step = next(item["sample"] for item in items if item.get("sample"))
record = dict(step, sample=43, note="the full run: the order's other 35 pairs, released batch by batch; the record set by hand")
with open(sys.argv[2], "w") as out:
    for item in items:
        item.setdefault("sample", record)
        out.write(json.dumps(item) + "\n")
PY
cd "$R" || exit 1
python3 bench/cooperbench/session/subset.py "$OUT/q/curse-1-queue.jsonl" "$LOGS" "$L" > "$OUT/subset.out" 2>&1; echo "subset rc=$?"
for arm in solo coop sdlc coop-git team; do cp "$STEP/raw/curse-1-$arm/config.json" "$L/curse-1-$arm/config.json"; done
python3 bench/cooperbench/report.py --logs "$L" --run solo=curse-1-solo --run coop=curse-1-coop \
  --run sdlc=curse-1-sdlc --run coop-git=curse-1-coop-git --run team=curse-1-team \
  --cooperbench-dir "$HOME/cooperbench" --published "$PUBLISHED" \
  --difficulty-out "$OUT/difficulty.json" > "$OUT/report.md" 2> "$OUT/report.err"; echo "report rc=$?"
python3 bench/cooperbench/session/summary.py --logs "$L" --tag curse-1 --out "$OUT/summary.json" \
  --csv "$OUT/pairs.csv" --md "$OUT/summary.md" > "$OUT/summary.out" 2>&1; echo "summary rc=$?"
python3 bench/cooperbench/session/bundle.py --logs "$L" --tag curse-1 --out "$OUT/bundle" > "$OUT/bundle.out" 2>&1; echo "bundle rc=$?"
tail -1 "$OUT/bundle.out"
