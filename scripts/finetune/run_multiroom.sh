#!/bin/bash
# Multi-room fine-tuning, end to end, safe to rerun: base eval -> SFT -> eval -> RL -> eval -> promote -> results.
#
#   nohup caffeinate -i -s scripts/finetune/run_multiroom.sh > runs/finetune/multiroom/orchestrator.log 2>&1 &
#
# Needs $SP_FINETUNE_HOME/venv (cookbook + fireworks-ai[training]) and
# $SP_FINETUNE_HOME/fireworks.env (chmod 600, FIREWORKS_API_KEY=...).
# Each attempt resumes from PROGRESS_MULTIROOM.json. A budget stop is final.
set -u
cd "$(dirname "$0")/../.."

HOME_DIR="${SP_FINETUNE_HOME:-$HOME/sp-finetune}"
PYTHON="$HOME_DIR/venv/bin/python"
set -a
. "$HOME_DIR/fireworks.env"
set +a
export PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:scripts/finetune
export PYTHONUNBUFFERED=1

DATA=runs/finetune/multiroom/v2
RUN_DIR="$DATA/qwen3p8-27b"
PROGRESS="$DATA/PROGRESS_MULTIROOM.json"
PLAN="${MULTIROOM_PLAN:-{\"sft_epochs\":3,\"rl_steps\":24,\"budget_dollars\":40,\"sft_model_id\":\"multiroom-qwen3p8-27b-sft\",\"rl_model_id\":\"multiroom-qwen3p8-27b-rl\"}}"
MAX_ATTEMPTS=8
STATUS="$DATA/OVERNIGHT_STATUS.txt"

while [ ! -f "$DATA/report.json" ]; do
  if ! pgrep -f '[m]ultiroom_data.py all --run runs/finetune/multiroom/v2' >/dev/null; then
    echo "Data generation ended without report.json; no Fireworks training launched." > "$STATUS"
    exit 1
  fi
  sleep 30
done

echo "Data complete. Measuring pessimistic run cost before any paid request." > "$STATUS"
"$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
  --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate 38 --measure-tokens --estimate-only
status=$?
if [ $status -ne 0 ]; then
  echo "Preflight failed (exit $status); no Fireworks training launched. See orchestrator.log." > "$STATUS"
  exit "$status"
fi
"$PYTHON" - "$PROGRESS" "$STATUS" <<'PY'
import json
import sys

estimate = json.load(open(sys.argv[1]))["plan"]["expected_cost"]
with open(sys.argv[2], "w") as handle:
    handle.write(f"Running base evaluation, three SFT epochs, SFT evaluation, RL, RL evaluation and adapter promotions.\n"
                 f"Pessimistic full-run estimate: ${estimate['estimated_dollars']:.4f}; effective stop $40, absolute stop $47.\n"
                 "Prior room 6 estimate $9.76; the effective cap preserves the under-$50 total.\n"
                 "Check orchestrator.log, PROGRESS_MULTIROOM.json, and results.json here in the morning.\n")
PY

for attempt in $(seq 1 $MAX_ATTEMPTS); do
  echo "$(date -u +%FT%TZ) training attempt $attempt"
  "$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
    --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate 38 --measure-tokens
  status=$?
  if [ $status -eq 0 ]; then break; fi
  if [ $status -ne 75 ]; then echo "Non-transient error or budget stop ($status); not retrying"; break; fi
  if [ $attempt -eq $MAX_ATTEMPTS ]; then echo "giving up after $attempt attempts"; break; fi
  sleep $((attempt * 60))
done

"$PYTHON" scripts/finetune/multiroom_results.py --data "$DATA" --run-dir "$RUN_DIR" --progress "$PROGRESS" \
  --out "$DATA/results.json"
if [ $status -eq 0 ]; then
  echo "Finished. See results.json and qwen3p8-27b/answers/ for the held-out answers." >> "$STATUS"
else
  echo "Stopped at exit $status. Partial results.json contains completed evaluations; see orchestrator.log." >> "$STATUS"
fi
echo "$(date -u +%FT%TZ) finished with training exit $status"
exit "$status"
