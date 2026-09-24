"""Comparable per-attempt and best-of-k evaluation for the same real held-out prompts."""

from __future__ import annotations

import argparse
import json
import pathlib

from multiroom_data import _rows, _write_json
from multiroom_results import metrics


def _share(groups: dict[str, list[dict]], predicate) -> float | None:
    if not groups:
        return None
    return round(sum(any(predicate(row) for row in attempts) for attempts in groups.values()) / len(groups), 4)


def _group(records: list[dict], key) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return groups


def _metrics(records: list[dict], variants: dict[str, dict]) -> dict:
    variants_by_id = _group(records, lambda row: row["variant"])
    rooms = _group(records, lambda row: variants[row["variant"]]["window_id"])
    return {**metrics(records),
            "heldout_variants": len(variants_by_id), "physical_rooms": len(rooms),
            "variant_best_of_k_accepted": _share(variants_by_id, lambda row: row.get("gate_accepts")),
            "room_best_of_k_accepted": _share(rooms, lambda row: row.get("gate_accepts")),
            "variant_best_of_k_fully_cleared": _share(variants_by_id, lambda row: row.get("gate_accepts")
                                                      and row.get("fixable_left") == 0),
            "room_best_of_k_fully_cleared": _share(rooms, lambda row: row.get("gate_accepts")
                                                   and row.get("fixable_left") == 0)}


def _model(records: list[dict], variants: dict[str, dict], ravida: set[str]) -> dict | None:
    if not records:
        return None
    alone = [row for row in records if variants[row["variant"]]["window_id"] in ravida]
    return {"heldout": _metrics(records, variants), "ravida": _metrics(alone, variants)}


def _progress(path: pathlib.Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def build(real: pathlib.Path, synthetic: pathlib.Path) -> dict:
    variant_ids = {row["variant"] for row in _rows(real / "dataset/heldout.jsonl")}
    variants = {row["variant_id"]: row for row in _rows(real / "variants.jsonl")
                if row["variant_id"] in variant_ids}
    report = json.loads((real / "report.json").read_text())
    ravida = {row["window_id"] for row in report["windows"] if row["scan"] == "ravida"}
    first_evals = real / "qwen3p8-27b/eval"
    second_evals = synthetic / "qwen3p8-27b/eval"
    locations = {"base": first_evals / "base.jsonl", "run1_best": first_evals / "rl.jsonl",
                 "run2_sft": second_evals / "sft.jsonl", "run2_rl": second_evals / "rl.jsonl"}
    first, second = _progress(real / "PROGRESS_MULTIROOM.json"), _progress(synthetic / "PROGRESS_SYNTHETIC.json")
    ceiling = _progress(synthetic / "ceiling.json")
    return {"models": {name: _model(_rows(path), variants, ravida) for name, path in locations.items()},
            "ceiling": {"variants": ceiling.get("searched"), "fixable": ceiling.get("fixable"),
                        "share_fixable": round(ceiling["fixable"] / ceiling["searched"], 4)
                        if ceiling.get("searched") else None, "per_variant": ceiling.get("rooms", [])},
            "costs": {"run1_estimated": first.get("spend"),
                      "run1_pessimistic_preflight": first.get("plan", {}).get("expected_cost"),
                      "run2_estimated": second.get("spend"),
                      "run2_pessimistic_preflight": second.get("plan", {}).get("expected_cost"),
                      "actual": "Fireworks serverless API does not expose billed totals; consult billing"},
            "arkit": "Converted annotations are evaluation-only. Without observed walls, doors or routes, "
                     "model gate scores would be invented and are not reported."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", type=pathlib.Path, required=True)
    parser.add_argument("--synthetic", type=pathlib.Path, required=True)
    args = parser.parse_args()
    _write_json(args.synthetic / "results.json", build(args.real, args.synthetic))


if __name__ == "__main__":
    main()
