#!/bin/bash
# The six-arm pilot on one task: solo, coop, coop-nocomm, coop-git, team and
# sdlc, each over the task's flash pairs with the flags the click 2068 and
# jinja 1559 pilots used, then the side-by-side report and the free
# heuristics on each run. Every arm runs even when an earlier one ends in
# error, since a pair's error is data; the exit status says whether any did.
# Usage: pilot.sh <repo, e.g. pallets_jinja_task> <task id> <run name prefix>
# The runs land in <cooperbench>/logs/<prefix>-<arm>, the report in
# <cooperbench>/logs/<prefix>-report.md. Needs COORDBENCH_CLAUDE_TOKEN (or
# CLAUDE_CODE_OAUTH_TOKEN) set; ANTHROPIC_API_KEY is unset for the agents.
# The judge is run only in its heuristics mode, which calls no model.
set -uo pipefail
if [ $# -ne 3 ]; then
    echo "usage: $0 <repo> <task id> <run name prefix>" >&2
    exit 2
fi
repo=$1
task=$2
prefix=$3
root=$(cd "$(dirname "$0")/../.." && pwd)
work=${COOPERBENCH_DIR:-$HOME/cooperbench}
logs="$work/logs"
cd "$root" || exit 2

# setup.sh is cheap once everything is there, and a host with Docker up
# may still lack claude or CooperBench's checkout.
bash bench/cooperbench/setup.sh || exit 1
bash bench/cooperbench/prepare_task.sh "$repo" "$task" || exit 1

status=0
arm() {
    local name=$1
    shift
    env -u ANTHROPIC_API_KEY python3 coordbench/run.py run --arm "$name" --subset flash \
        --repo "$repo" --task "$task" --driver claude-cli "$@" --name "$prefix-$name" || status=1
}
arm solo
arm coop --option tool_surface=bash
arm coop-nocomm
arm coop-git --option tool_surface=bash
arm team --option tool_surface=bash
arm sdlc

labels=()
for name in solo coop coop-nocomm coop-git team sdlc; do
    labels+=(--run "$name=$logs/$prefix-$name")
done
python3 bench/cooperbench/report.py "${labels[@]}" >"$logs/$prefix-report.md" || status=1
echo "report: $logs/$prefix-report.md"

for name in solo coop coop-nocomm coop-git team sdlc; do
    python3 coordbench/judge.py heuristics "$logs/$prefix-$name" || status=1
done
exit $status
