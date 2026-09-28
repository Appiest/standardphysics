#!/bin/bash
# Overnight run on compute-box: amenity-rich generated rooms, snap-to-legal reward, one budgeted SFT + RL.
#
#   SP_FINETUNE_HOME=~/sp-finetune scripts/finetune/run_harness.sh
#
# Every stage resumes. Paid training starts only when the pessimistic estimate is under MAX_ESTIMATE, and the
# trainer itself stops at its hard ceiling.
set -eu
cd "$(dirname "$0")/../.."

HOME_DIR="${SP_FINETUNE_HOME:-$HOME/sp-finetune}"
PYTHON="$HOME_DIR/venv/bin/python"
export PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:scripts/finetune
export PYTHONUNBUFFERED=1
export SP_SCAN_EXPORTS="$HOME_DIR/shells"

REAL="$HOME_DIR/real"
DATA="runs/finetune/harness"
RUN_DIR="$DATA/qwen3p8-27b"
PROGRESS="$DATA/PROGRESS_HARNESS_TRAINING.json"
STATUS="$DATA/STATUS.txt"
ROOMS="${ROOMS:-700}"
HELDOUT_ROOMS="${HELDOUT_ROOMS:-60}"
WORKERS="${WORKERS:-7}"
MAX_SFT="${MAX_SFT:-900}"
MAX_ESTIMATE="${MAX_ESTIMATE:-45}"
mkdir -p "$DATA"

note() { echo "$(date -u +%FT%TZ) $*" | tee -a "$STATUS"; }

if [ -f "$DATA/report.json" ]; then
  note "dataset already built; skipping data stages"
else
  note "rooms: $ROOMS training, $HELDOUT_ROOMS held out"
  "$PYTHON" scripts/finetune/synthetic_data.py rooms --run "$DATA" --count "$ROOMS" --heldout "$HELDOUT_ROOMS" > "$DATA/rooms.log"
  note "scrambles"
  "$PYTHON" scripts/finetune/synthetic_data.py scrambles --run "$DATA" --workers "$WORKERS" > "$DATA/scrambles.log"
  note "targets"
  "$PYTHON" scripts/finetune/synthetic_data.py targets --run "$DATA" --workers "$WORKERS" > "$DATA/targets.log"
  note "dataset"
  "$PYTHON" scripts/finetune/synthetic_data.py dataset --run "$DATA" --real "$REAL" --max-sft "$MAX_SFT"
  note "dataset: $(cat "$DATA/progress.json" | tr -d '\n ')"
fi
if [ ! -f "$DATA/dataset/fit_report.json" ]; then
  "$PYTHON" scripts/finetune/fit_dataset.py --data "$DATA" > /dev/null
  note "fitted to prompt length: $(cat "$DATA/dataset/fit_report.json" | tr -d '\n ')"
fi

PLAN='{"sft_epochs": 1, "rl_steps": 12, "rl_prompts_per_step": 6, "rl_group_size": 8, "eval_samples": 2,
       "budget_dollars": 45, "sft_model_id": "harness-qwen3p8-27b-sft", "rl_model_id": "harness-qwen3p8-27b-rl"}'

"$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
  --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate "$MAX_ESTIMATE" --measure-tokens --estimate-only || {
    note "paid training blocked by the pessimistic \$$MAX_ESTIMATE preflight; no paid request was made"
    exit 1
  }
note "preflight: $("$PYTHON" -c "import json;print(json.load(open('$PROGRESS'))['plan']['expected_cost'])")"

set -a
. "$HOME_DIR/fireworks.env"
set +a
outcome=1
for attempt in $(seq 1 6); do
  set +e
  "$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
    --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate "$MAX_ESTIMATE" --measure-tokens
  outcome=$?
  set -e
  if [ "$outcome" -ne 75 ]; then break; fi
  note "transient failure, retry $attempt"
  sleep $((attempt * 60))
done
note "training finished with exit $outcome; spend $("$PYTHON" -c "import json;print(json.load(open('$PROGRESS')).get('spend'))")"
exit "$outcome"
