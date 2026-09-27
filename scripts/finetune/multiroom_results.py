"""Morning results for the multi-room run: every model's held-out numbers, the search's, cost and time.

Reads the evaluation records the orchestrator wrote (RUN_DIR/eval/{base,sft,rl}.jsonl),
the dataset's search records for the same held-out variants, and the progress file.
Writes results.json and, per model, the held-out answers with their window and scan
under RUN_DIR/answers/, ready to load into the web app.

    python scripts/finetune/multiroom_results.py --data runs/finetune/multiroom/v2 \\
        --run-dir runs/finetune/multiroom/qwen3p8-27b --progress runs/finetune/multiroom/PROGRESS_MULTIROOM.json \\
        --out runs/finetune/multiroom/results.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from collections import Counter

from multiroom_data import _rows, _write_json

MODELS = ("base", "sft", "rl")
HELDOUT_SMALL_SCAN = "ravida"
SEARCH_FOUND_NOTHING = {"parsed": True, "hard_constraints_pass": True, "gate_accepts": False, "reward": 0.0,
                        "shortfall_recovered": 0.0, "fixable_left": None, "usability": None, "quality": None}
"""How a variant the search could not improve counts: an answer that changes nothing."""


def _share(records: list[dict], test) -> float | None:
    return round(sum(1 for r in records if test(r)) / len(records), 4) if records else None


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def metrics(records: list[dict]) -> dict:
    accepted = [r for r in records if r.get("gate_accepts")]
    count = len(records)
    return {
        "samples": count,
        "parse_rate": _share(records, lambda r: r.get("parsed")),
        "hard_constraint_pass_rate": _share(records, lambda r: r.get("hard_constraints_pass")),
        "gate_acceptance": _share(records, lambda r: r.get("gate_accepts")),
        "mean_shortfall_recovered": round(sum(r["shortfall_recovered"] for r in accepted) / count, 4) if count else None,
        "share_clearing_every_fixable": _share(records, lambda r: r.get("gate_accepts") and r.get("fixable_left") == 0),
        "mean_usability_accepted": _mean([r.get("usability") for r in accepted]),
        "mean_quality_accepted_logged_only": _mean([(r.get("quality") or {}).get("q") for r in accepted]),
        "mean_reward": _mean([r.get("reward", 0.0) for r in records]),
    }


def _by_place(records: list[dict], variants: dict[str, dict], ravida: set[str]) -> dict:
    alone = [r for r in records if variants[r["variant"]]["window_id"] in ravida]
    return {"heldout": metrics(records), "ravida": metrics(alone)}


def composition(data: pathlib.Path) -> dict:
    report = json.loads((data / "report.json").read_text())
    window_log = _rows(data / "windows_log.jsonl")
    return {
        "windows_kept": len(report["windows"]),
        "windows_not_kept_by_reason": dict(Counter(row.get("why") or "unknown" for row in window_log
                                                if not row["kept"])),
        "windows_dropped_for_split": report["split"]["totals"].get("dropped_shares_heldout_furniture", 0),
        "split_by_scan": report["split"]["by_scan"],
        "held_out": report["split"]["held_out"],
        "variants": report["variants"],
        "targets": report["targets"],
        "rows": {name: len(_rows(data / "dataset" / f"{name}.jsonl"))
                 for name in ("sft", "rl", "heldout")},
    }


def search_records(data: pathlib.Path, heldout_ids: list[str]) -> list[dict]:
    targets = {row["variant_id"]: row for row in _rows(data / "targets.jsonl")}
    found = []
    for variant_id in heldout_ids:
        search = (targets.get(variant_id) or {}).get("search") or {}
        found.append({"variant": variant_id, **(search.get("verdict") or SEARCH_FOUND_NOTHING)})
    return found


def write_answers(run_dir: pathlib.Path, label: str, records: list[dict], variants: dict[str, dict]) -> str:
    out = run_dir / "answers" / f"{label}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"model": label, "variant_id": r["variant"], "window_id": variants[r["variant"]]["window_id"],
             "scan_id": variants[r["variant"]]["scan_id"], "sample": r.get("sample"), "edits": r.get("completion"),
             "reward": r.get("reward"), "gate_accepts": r.get("gate_accepts"), "reason": r.get("reason")}
            for r in records]
    out.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return str(out)


def _battery() -> str:
    try:
        return subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _timeline(progress: dict) -> dict:
    return {step: {"status": entry.get("status"), "updated_at": entry.get("updated_at"),
                   "started_at": entry.get("started_at"), "finished_at": entry.get("finished_at"),
                   "wall_seconds": round(entry["finished_at"] - entry["started_at"], 1)
                   if entry.get("finished_at") and entry.get("started_at") else None}
            for step, entry in progress.get("steps", {}).items()}


def build(data: pathlib.Path, run_dir: pathlib.Path, progress_path: pathlib.Path) -> dict:
    variants = {row["variant_id"]: row for row in _rows(data / "variants.jsonl") if row["variant_id"]}
    report = json.loads((data / "report.json").read_text())
    ravida = {row["window_id"] for row in report["windows"] if row["scan"] == HELDOUT_SMALL_SCAN}
    heldout_ids = [row["variant"] for row in _rows(data / "dataset" / "heldout.jsonl")]
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    results = {"models": {}, "answers": {}}
    for label in MODELS:
        records = _rows(run_dir / "eval" / f"{label}.jsonl")
        if records:
            results["models"][label] = _by_place(records, variants, ravida)
            results["answers"][label] = write_answers(run_dir, label, records, variants)
    results["free_search_reference"] = _by_place(search_records(data, heldout_ids), variants, ravida)
    results.update({
        "heldout_variants": len(heldout_ids),
        "data_composition": composition(data),
        "promoted_models": {step: progress["steps"][step].get("model") for step in ("promote_sft", "promote_rl")
                            if step in progress.get("steps", {})},
        "jobs": progress.get("jobs", []),
        "plan": progress.get("plan"),
        "spend_estimated_pessimistic": progress.get("spend"),
        "observed_sample_tokens": progress.get("observed_sample_tokens"),
        "spend_actual": "not exposed by the serverless training API; check the Fireworks billing page",
        "budget_stop": progress.get("steps", {}).get("budget"),
        "timeline": _timeline(progress),
        "power_at_results_time": _battery(),
    })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=pathlib.Path, required=True)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    results = build(args.data, args.run_dir, args.progress)
    _write_json(args.out, results)
    print(json.dumps({label: value["heldout"] for label, value in results["models"].items()}, indent=2))


if __name__ == "__main__":
    main()
