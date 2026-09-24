#!/bin/bash
set -eu
cd "$(dirname "$0")/../.."

HOME_DIR="${SP_FINETUNE_HOME:-$HOME/sp-finetune}"
PYTHON="$HOME_DIR/venv/bin/python"
export PYTHONPATH=packages/contracts:packages/fixtures:packages/pipeline:packages/agents:scripts/finetune
export PYTHONUNBUFFERED=1

FIRST=runs/finetune/multiroom/v2
DATA=runs/finetune/synthetic
RUN_DIR="$DATA/qwen3p8-27b"
PROGRESS="$DATA/PROGRESS_SYNTHETIC.json"
STATUS="$DATA/STATUS.txt"

"$PYTHON" - "$FIRST" "$DATA" "$STATUS" <<'PY'
import json
import pathlib
import sys

first, data, status = map(pathlib.Path, sys.argv[1:])
progress = json.loads((first / "PROGRESS_MULTIROOM.json").read_text())
results = json.loads((first / "results.json").read_text()) if (first / "results.json").exists() else {}
if progress["steps"].get("promote_rl", {}).get("status") != "done" or "rl" not in results.get("models", {}):
    status.write_text("Paid training blocked: first RL run has not finished and reported results.\n")
    raise SystemExit(1)
if not (data / "report.json").exists():
    status.write_text("Paid training blocked: synthetic dataset is incomplete.\n")
    raise SystemExit(1)
heldout = [json.loads(line)["variant"] for line in (data / "dataset/heldout.jsonl").read_text().splitlines()]
original = [json.loads(line)["variant"] for line in (first / "dataset/heldout.jsonl").read_text().splitlines()]
if heldout != original:
    status.write_text("Paid training blocked: real held-out prompts do not match run 1.\n")
    raise SystemExit(1)
PY

PLAN="$("$PYTHON" - "$FIRST" <<'PY'
import json
import pathlib
import sys

first = json.loads((pathlib.Path(sys.argv[1]) / "PROGRESS_MULTIROOM.json").read_text())
print(json.dumps({"initial_state": first["steps"]["rl"]["state_ref"], "sft_epochs": 1,
                  "rl_steps": 12, "budget_dollars": 45,
                  "sft_model_id": "synthetic-qwen3p8-27b-sft",
                  "rl_model_id": "synthetic-qwen3p8-27b-rl"}))
PY
)"

"$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
  --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate 45 --measure-tokens --estimate-only || {
    echo "Paid training blocked by the pessimistic $45 preflight. No paid request was made." >> "$STATUS"
    exit 1
  }

"$PYTHON" - "$FIRST" "$PROGRESS" "$STATUS" <<'PY'
import json
import pathlib
import sys

first = json.loads((pathlib.Path(sys.argv[1]) / "PROGRESS_MULTIROOM.json").read_text())
second = json.loads(pathlib.Path(sys.argv[2]).read_text())
status = pathlib.Path(sys.argv[3])
first_estimate = first["spend"]["estimated_dollars"]
reserved_first = max(first_estimate, first["plan"]["expected_cost"]["estimated_dollars"])
second_ceiling = second["plan"]["expected_cost"]["estimated_dollars"]
with status.open("a") as handle:
    handle.write(f"Run 1 final pessimistic spend: ${first_estimate:.4f}; conservative reserve: ${reserved_first:.4f}.\n")
    handle.write(f"Run 2 pessimistic preflight: ${second_ceiling:.4f}; combined reserve ${reserved_first + second_ceiling:.4f}.\n")
    handle.write("Run 2 runtime cap: $45; combined conservative hard-stop exposure remains below $97.\n")
if reserved_first + second_ceiling > 90 or reserved_first + 45 >= 97:
    with status.open("a") as handle:
        handle.write("Paid training blocked by combined $90 planning or $97 hard-stop budget.\n")
    raise SystemExit(1)
PY

set -a
. "$HOME_DIR/fireworks.env"
set +a
for attempt in $(seq 1 8); do
  set +e
  "$PYTHON" scripts/finetune/serverless_train.py --dataset multiroom --data "$DATA" --run-dir "$RUN_DIR" \
    --progress "$PROGRESS" --plan-overrides "$PLAN" --max-estimate 45 --measure-tokens
  outcome=$?
  set -e
  if [ "$outcome" -eq 0 ]; then break; fi
  if [ "$outcome" -ne 75 ]; then
    echo "Paid training stopped at exit $outcome; no automatic restart." >> "$STATUS"
    break
  fi
  if [ "$attempt" -eq 8 ]; then break; fi
  sleep $((attempt * 60))
done

"$PYTHON" scripts/finetune/synthetic_results.py --real "$FIRST" --synthetic "$DATA"
if [ "$outcome" -eq 0 ]; then
  echo "Run 2 SFT, RL and comparable held-out evaluations completed; see results.json." >> "$STATUS"
else
  echo "Run 2 is incomplete; results.json contains only available evaluations." >> "$STATUS"
fi
exit "$outcome"
