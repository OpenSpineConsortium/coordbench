#!/bin/bash
# The report job of .github/workflows/bench.yml: every artifact of the tag
# unpacked into one logs tree (this dispatch's and the earlier ones' that
# the resume kept), then bench/cooperbench/report.py over them, one --run
# per artifact under its arm's label so the arms pool, the free judge
# heuristics, and the report written to REPORT_DIR for the upload and to
# the job summary. In survey mode the gradeability table is written
# instead (survey_report.py). TAG, MODE and REPORT_DIR come in the
# environment, with GITHUB_TOKEN and GITHUB_REPOSITORY for the API.
set -euo pipefail
: "${TAG:?}" "${MODE:?}" "${REPORT_DIR:?}"
root=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$root"
export COOPERBENCH_DIR=${COOPERBENCH_DIR:-$HOME/cooperbench}
logs="$COOPERBENCH_DIR/logs"
mkdir -p "$logs" "$REPORT_DIR"
python3 bench/cooperbench/actions/artifacts.py download "$TAG" "$logs" > "$REPORT_DIR/artifacts.jsonl"
# The survey jobs' gradeability reports, this dispatch's and earlier ones',
# in one flat directory as survey.sh keeps them, for report.py's dataset
# section; a summary.json beside each is the survey's own and stays out.
mkdir -p "$logs/survey"
for report in "$logs/$TAG"-survey-*/*.json; do
    case $report in *.summary.json) ;; *) [ -f "$report" ] && cp "$report" "$logs/survey/" ;; esac
done
if [ "$MODE" = survey ]; then
    python3 bench/cooperbench/actions/survey_report.py "$logs" "$TAG" > "$REPORT_DIR/report.md"
else
    runs=()
    while IFS= read -r line; do
        name=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$line")
        arm=$(python3 -c 'import sys; print(sys.argv[1].split("__")[1])' "$name")
        [ "$arm" = survey ] && continue
        runs+=(--run "$arm=$(python3 -c 'import sys; print(sys.argv[1].replace("__", "-"))' "$name")")
    done < "$REPORT_DIR/artifacts.jsonl"
    if [ ${#runs[@]} -eq 0 ]; then
        echo "no run artifact named ${TAG}__*; nothing to report" | tee "$REPORT_DIR/report.md"
    else
        python3 bench/cooperbench/report.py --logs "$logs" --cooperbench-dir "$COOPERBENCH_DIR" \
            --survey-dir "$logs/survey" "${runs[@]}" > "$REPORT_DIR/report.md"
        python3 coordbench/judge.py heuristics "$logs" > "$REPORT_DIR/heuristics.txt" 2>&1 || true
    fi
fi
cat "$REPORT_DIR/report.md" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "report: $REPORT_DIR/report.md"
