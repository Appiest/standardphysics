#!/bin/bash
# The feedback experiment, end to end: Fireworks arms in one queue, OpenRouter arms in another, then results.
#
#   nohup caffeinate -i -s scripts/finetune/run_feedback.sh > runs/finetune/feedback/run.log 2>&1 &
#
# Needs $SP_FINETUNE_HOME/fireworks.env and ./openrouter.env (both chmod 600). An arm whose
# transcript exists is skipped; an arm whose worst case no longer fits the budget exits 4 and is skipped.
# The repeats and the OpenRouter arms run past a worst case that does not fit, and stop on the per-call guard.
set -u
cd "$(dirname "$0")/../.."
HOME_DIR="${SP_FINETUNE_HOME:-$HOME/sp-finetune}"
PYTHON="$HOME_DIR/venv/bin/python"
set -a
. "$HOME_DIR/fireworks.env"
. ./openrouter.env
set +a
export PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:scripts/finetune
export PYTHONUNBUFFERED=1
OUT=runs/finetune/feedback

run_arms() {
  for arm in "$@"; do
    if [ -f "$OUT/transcripts/$arm.jsonl" ]; then continue; fi
    echo "$(date -u +%FT%TZ) start $arm"
    "$PYTHON" scripts/finetune/feedback_eval.py run --arm "$arm" ${ARM_FLAGS:-}
    status=$?
    echo "$(date -u +%FT%TZ) end $arm (exit $status)"
  done
}

mkdir -p "$OUT"
"$PYTHON" scripts/finetune/feedback_eval.py run --arm A
(run_arms B C E A_fresh; ARM_FLAGS=--allow-over-estimate run_arms B_repeat C_repeat) >> "$OUT/fireworks.log" 2>&1 &
ARM_FLAGS=--allow-over-estimate run_arms D_B D_C D_B_rest >> "$OUT/openrouter.log" 2>&1 &
wait
"$PYTHON" scripts/finetune/feedback_eval.py results
echo "$(date -u +%FT%TZ) done"
