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
# What could not be read, and what this dispatch planned that has no
# artifact at all (a job that ended before run.py wrote one), first, so
# a report over fewer pairs says so: the rates count the pairs with a
# record, and these are the attempted pairs they leave out.
printf '%s' "${PLAN_MATRIX:-}" > "$REPORT_DIR/plan_matrix.json"
python3 -I - "$REPORT_DIR/artifacts.jsonl" "$REPORT_DIR/plan_matrix.json" > "$REPORT_DIR/report.md" <<'PY'
import json, sys
lines = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
problems = [f"- {line['name']}: {line['error']}" for line in lines if "error" in line]
problems += [f"- {line['warning']}" for line in lines if "warning" in line]
if problems:
    print("## Artifacts left out\n")
    print("\n".join(problems) + "\n")
text = open(sys.argv[2], encoding="utf-8").read().strip()
planned = json.loads(text).get("include", []) if text else []
found = {line["name"] for line in lines if "name" in line}
missing = [item["artifact"] for item in planned if item.get("artifact") not in found]
if missing:
    print("## Planned items with no artifact\n")
    print("These (pair, arm) items were planned by this dispatch and left no artifact, so no rate counts them; "
          "the next dispatch of the tag runs them.\n")
    print("\n".join(f"- {name}" for name in missing) + "\n")
PY
if [ "$MODE" = survey ]; then
    printf '%s' "${SURVEY_PLAN:-}" > "$REPORT_DIR/survey_plan.json"
    plan_file=""
    [ -s "$REPORT_DIR/survey_plan.json" ] && plan_file="$REPORT_DIR/survey_plan.json"
    python3 bench/cooperbench/actions/survey_report.py "$logs" "$TAG" $plan_file >> "$REPORT_DIR/report.md"
else
    runs=()
    # Each unpacked artifact under its arm, which the download reads from
    # the second part of the name (the double underscores keep it apart
    # from the dashes in a tag or an arm such as coop-git); a line with
    # no run_name is an error line, and a survey artifact is no run.
    while IFS= read -r line; do
        entry=$(python3 -c 'import json,sys
line = json.loads(sys.argv[1])
if line.get("run_name") and line.get("arm") != "survey":
    print(line["arm"] + "=" + line["run_name"])' "$line")
        [ -z "$entry" ] && continue
        runs+=(--run "$entry")
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
