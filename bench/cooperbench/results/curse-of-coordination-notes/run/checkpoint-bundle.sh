#!/bin/bash
# How ../../curse-1-checkpoint/raw was made at the checkpoint of 2026-10-16,
# and ../../curse-1-full/raw once the full run was done on 2026-10-17:
# every finished run of the full run, through subset.py and bundle.py, from
# a copy of the queue that marks the full run's items alone (the step's
# 40 left to ../../curse-1-step-8/), with the pilot's config.json for each
# arm. The arms' summary.json were then deleted from raw/, since
# restore-bundles.py never copies one. Usage: checkpoint-bundle.sh
# CHECKOUT OUT LOGS_SAMPLE
set -u
R=$1; OUT=$2; L=$3
LOGS=$HOME/cooperbench/logs
STEP=$R/bench/cooperbench/results/curse-1-step-8
[ -e "$L" ] && { echo "stop: $L exists"; exit 1; }
mkdir -p "$OUT/q"
python3 -I - "$LOGS/curse-1-queue.jsonl" "$OUT/q/curse-1-queue.jsonl" <<'PY'
import json, sys
items = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
step = next(item["sample"] for item in items if item.get("sample"))
record = dict(step, sample=43, note="the full run's checkpoint: its items alone, the step's left to its own bundle")
with open(sys.argv[2], "w") as out:
    for item in items:
        if item.pop("sample", None) is None:
            item["sample"] = record
        out.write(json.dumps(item) + "\n")
PY
cd "$R" || exit 1
python3 bench/cooperbench/session/subset.py "$OUT/q/curse-1-queue.jsonl" "$LOGS" "$L" > "$OUT/subset.out" 2>&1; echo "subset rc=$?"
for arm in solo coop sdlc coop-git team; do cp "$STEP/raw/curse-1-$arm/config.json" "$L/curse-1-$arm/config.json"; done
python3 bench/cooperbench/session/bundle.py --logs "$L" --tag curse-1 --out "$OUT/bundle" > "$OUT/bundle.out" 2>&1; echo "bundle rc=$?"
tail -1 "$OUT/bundle.out"
