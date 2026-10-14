#!/bin/bash
# The report job of .github/workflows/bench.yml: every artifact of the tag
# unpacked into one logs tree (this dispatch's and the earlier ones' that
# the resume kept), then bench/cooperbench/report.py over them, one --run
# per artifact under its arm's label so the arms pool, the free judge
# heuristics, and the report written to REPORT_DIR for the upload, to the
# job log, which a reader with no way to an artifact still has, and to
# the job summary. In survey mode the gradeability table is written
# instead (survey_report.py), with a row for every planned task whose job
# left no summary. An artifact that could not be fetched or unpacked is
# listed at the top of the report and the rest are reported. TAG, MODE
# and REPORT_DIR come in the environment, SURVEY_TAG (the survey
# dispatch's tag, this tag when unset) and SURVEY_PLAN (the plan's survey
# matrix) may, with GITHUB_TOKEN and GITHUB_REPOSITORY for the API.
set -euo pipefail
: "${TAG:?}" "${MODE:?}" "${REPORT_DIR:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
export COOPERBENCH_DIR=${COOPERBENCH_DIR:-$HOME/cooperbench}
logs="$COOPERBENCH_DIR/logs"
survey_tag=${SURVEY_TAG:-$TAG}
mkdir -p "$logs" "$REPORT_DIR"
python3 bench/cooperbench/actions/artifacts.py download "$TAG" "$logs" --mode "$MODE" --survey-tag "$survey_tag" \
    > "$REPORT_DIR/artifacts.jsonl"
# The survey jobs' gradeability reports, this tag's and the survey tag's,
# in one flat directory as survey.sh keeps them, for report.py's dataset
# section; a summary.json beside each is the survey's own and stays out.
mkdir -p "$logs/survey"
for report in "$logs/$TAG"-survey-*/*.json "$logs/$survey_tag"-survey-*/*.json; do
    case $report in *.summary.json) ;; *) [ -f "$report" ] && cp "$report" "$logs/survey/" ;; esac
done
# What could not be read, first, so a report over fewer pairs says so.
python3 -I - "$REPORT_DIR/artifacts.jsonl" > "$REPORT_DIR/report.md" <<'PY'
import json, sys
lines = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
problems = [f"- {line['name']}: {line['error']}" for line in lines if "error" in line]
problems += [f"- {line['warning']}" for line in lines if "warning" in line]
if problems:
    print("## Artifacts left out\n")
    print("\n".join(problems) + "\n")
PY
if [ "$MODE" = survey ]; then
    printf '%s' "${SURVEY_PLAN:-}" > "$REPORT_DIR/survey_plan.json"
    plan_file=""
    [ -s "$REPORT_DIR/survey_plan.json" ] && plan_file="$REPORT_DIR/survey_plan.json"
    python3 bench/cooperbench/actions/survey_report.py "$logs" "$TAG" $plan_file >> "$REPORT_DIR/report.md"
else
    runs=()
    while IFS= read -r line; do
        name=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("run_name", ""))' "$line")
        [ -z "$name" ] && continue
        arm=$(python3 -c 'import sys; print(sys.argv[1].split("-")[1])' "${name#"$TAG"-}")
        [ "$arm" = survey ] && continue
        runs+=(--run "$arm=$name")
    done < "$REPORT_DIR/artifacts.jsonl"
    if [ ${#runs[@]} -eq 0 ]; then
        echo "no run artifact named ${TAG}__*; nothing to report" | tee -a "$REPORT_DIR/report.md"
    else
        python3 bench/cooperbench/report.py --logs "$logs" --cooperbench-dir "$COOPERBENCH_DIR" \
            --survey-dir "$logs/survey" "${runs[@]}" >> "$REPORT_DIR/report.md"
        python3 coordbench/judge.py heuristics "$logs" > "$REPORT_DIR/heuristics.txt" 2>&1 || true
    fi
fi
echo "artifacts of $TAG:"
cat "$REPORT_DIR/artifacts.jsonl"
echo
cat "$REPORT_DIR/report.md"
cat "$REPORT_DIR/report.md" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "report: $REPORT_DIR/report.md"
