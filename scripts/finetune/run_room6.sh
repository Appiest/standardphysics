#!/bin/bash
# Room 6 fine-tuning, end to end, safe to rerun: SFT -> RL -> promote -> download.
#
#   nohup caffeinate -s scripts/finetune/run_room6.sh > runs/finetune/room6/orchestrator.log 2>&1 &
#
# Needs $SP_FINETUNE_HOME/venv (cookbook + fireworks-ai[training]) and
# $SP_FINETUNE_HOME/fireworks.env (chmod 600, FIREWORKS_API_KEY=...).
# Each attempt resumes from runs/finetune/progress.json. A budget stop is final.
set -u
cd "$(dirname "$0")/../.."

HOME_DIR="${SP_FINETUNE_HOME:-$HOME/sp-finetune}"
PYTHON="$HOME_DIR/venv/bin/python"
set -a
. "$HOME_DIR/fireworks.env"
set +a
export PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:scripts/finetune
export PYTHONUNBUFFERED=1

DATA=runs/finetune/room6/data
RUN_DIR=runs/finetune/room6/qwen3p8-27b
MAX_ATTEMPTS=8

for attempt in $(seq 1 $MAX_ATTEMPTS); do
  echo "$(date -u +%FT%TZ) training attempt $attempt"
  "$PYTHON" scripts/finetune/serverless_train.py --data "$DATA" --run-dir "$RUN_DIR"
  status=$?
  if [ $status -eq 0 ]; then break; fi
  if [ $status -eq 3 ]; then echo "budget stop; not retrying"; exit 3; fi
  if [ $attempt -eq $MAX_ATTEMPTS ]; then echo "giving up after $attempt attempts"; exit $status; fi
  sleep $((attempt * 60))
done

for attempt in $(seq 1 $MAX_ATTEMPTS); do
  echo "$(date -u +%FT%TZ) download attempt $attempt"
  "$PYTHON" scripts/finetune/download_adapter.py --progress runs/finetune/progress.json "$RUN_DIR/adapters" && break
  sleep $((attempt * 60))
done
echo "$(date -u +%FT%TZ) finished"
