"""Comparable per-attempt and best-of-k evaluation for the same real held-out prompts."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
from collections import Counter

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


def _ceiling(synthetic: pathlib.Path) -> dict:
    current = _progress(synthetic / "ceiling.json")
    if current:
        return current
    archived = _progress(synthetic / "results.json").get("ceiling", {})
    return {"searched": archived.get("variants"), "fixable": archived.get("fixable"),
            "rooms": archived.get("per_variant", [])}


def _wilson(successes: int, total: int) -> dict | None:
    """Two-sided 95% interval for independent shop-level binary outcomes."""
    if not total:
        return None
    z = 1.959963984540054
    share = successes / total
    denominator = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denominator
    return {"low": round(max(0.0, centre - radius), 4), "high": round(min(1.0, centre + radius), 4),
            "method": "Wilson two-sided 95%; independent scan/shop groups"}


def _rate(successes: int, total: int, complete: bool) -> dict:
    return {"successes": successes, "denominator": total,
            "rate": round(successes / total, 4) if total and complete else None,
            "lower_bound_rate": round(successes / total, 4) if total else None}


def _feasibility(variant_id: str, ceiling: dict[str, dict], adjudicated: dict[str, dict]) -> str:
    decision = adjudicated.get(variant_id)
    if decision:
        status = decision.get("status")
        if status not in {"feasible", "unfixable", "unknown"}:
            raise ValueError(f"invalid feasibility status for {variant_id}: {status}")
        if status in {"feasible", "unfixable"} and not decision.get("evidence"):
            raise ValueError(f"certified feasibility status needs evidence for {variant_id}")
        return status
    witness = ceiling.get(variant_id, {})
    return "feasible" if witness.get("all_clear") is True else "unknown"


def _shop_feasibility(statuses: list[str]) -> str:
    if "unfixable" in statuses:
        return "unfixable"
    return "feasible" if statuses and all(status == "feasible" for status in statuses) else "unknown"


def _loop_outcome(record: dict) -> dict:
    if record.get("evaluation_policy") != "checker-full-clear-v1":
        raise ValueError(f"unexpected evaluation policy for {record['variant']}")
    attempts = record.get("attempts", [])
    if len(attempts) > 5:
        raise ValueError(f"more than five attempts for {record['variant']}")
    if record.get("attempt_limit", 5) not in range(1, 6):
        raise ValueError(f"invalid attempt limit for {record['variant']}")
    accepted = [attempt for attempt in attempts if attempt.get("accepted")]
    unsafe = sum(not attempt.get("verdict", {}).get("gate_accepts") for attempt in accepted)
    last_left = accepted[-1]["verdict"].get("fixable_left") if accepted else None
    final_left = record.get("final_fixable_left", last_left)
    if accepted and last_left != final_left:
        raise ValueError(f"final finding count disagrees with attempts for {record['variant']}")
    success = bool(accepted) and final_left == 0 and unsafe == 0
    final_usability = record.get("final_baseline_usability")
    strict = success and final_usability == 1.0
    abstained = not success
    if record.get("success", success) != success or record.get("abstained", abstained) != abstained:
        raise ValueError(f"record outcome disagrees with attempts for {record['variant']}")
    if record.get("checker_full_clear_within_five", success) != success:
        raise ValueError(f"checker outcome disagrees with attempts for {record['variant']}")
    if record.get("full_usability_preserved", strict) != strict:
        raise ValueError(f"strict usability outcome disagrees for {record['variant']}")
    return {"success": success, "strict": strict, "abstained": abstained, "applied": len(accepted),
            "applied_without_gate": unsafe,
            "intermediate_usability_drops": sum(attempt.get("step_usability") is not None and
                                                 attempt["step_usability"] < 1.0 for attempt in accepted),
            "final_usability": final_usability}


def _shop_groups(variants: dict[str, dict], shop_ids: dict[str, str] | None = None) -> dict[str, list[str]]:
    shops: dict[str, list[str]] = {}
    for variant_id, variant in variants.items():
        scan_id = variant["scan_id"]
        shop_id = shop_ids[scan_id] if shop_ids is not None else scan_id
        shops.setdefault(shop_id, []).append(variant_id)
    return shops


def _validate_shop_ids(variants: dict[str, dict], shop_ids: dict[str, str] | None) -> None:
    if shop_ids is None:
        return
    missing = {variant["scan_id"] for variant in variants.values()} - shop_ids.keys()
    if missing:
        raise ValueError(f"missing physical shop IDs for scans: {sorted(missing)}")
    if any(not isinstance(shop_ids[variant["scan_id"]], str) or not shop_ids[variant["scan_id"]].strip()
           for variant in variants.values()):
        raise ValueError("physical shop IDs must be nonempty strings")


def _eligible_variants(variants: dict[str, dict], records: dict[str, dict], ceiling: dict[str, dict]) -> dict[str, dict]:
    eligible = {}
    for variant_id, variant in variants.items():
        prior = ceiling.get(variant_id, {}).get("baseline_fixable_findings")
        observed = records.get(variant_id, {}).get("baseline_fixable_left")
        declared = records.get(variant_id, {}).get("eligible")
        if prior is not None and observed is not None and (prior > 0) != (observed > 0):
            raise ValueError(f"eligibility disagreement for {variant_id}")
        if declared is not None and observed is not None and declared != (observed > 0):
            raise ValueError(f"eligibility disagreement for {variant_id}")
        if prior == 0 or observed == 0 or declared is False:
            continue
        eligible[variant_id] = variant
    return eligible


def _baseline(records: list[dict], variants: dict[str, dict], shop_ids: dict[str, str] | None) -> dict:
    by_variant = _group(records, lambda row: row["variant"])
    successful = {variant_id for variant_id in variants if any(
        row.get("gate_accepts") and row.get("fixable_left") == 0 for row in by_variant.get(variant_id, []))}
    shops = _shop_groups(variants, shop_ids)
    complete = all(variant_id in by_variant for variant_id in variants)
    shop_successes = sum(all(variant_id in successful for variant_id in members) for members in shops.values())
    return {"samples": len(records), "criterion": "checker full clear; final baseline usability was not recorded",
            "variants": _rate(len(successful), len(variants), complete),
            "scan_or_shop_groups_all_variants": {**_rate(shop_successes, len(shops), complete),
                                                 "unit": "physical shop" if shop_ids is not None else "scan group",
                                                 "confidence_interval": _wilson(shop_successes, len(shops))
                                                 if complete and shop_ids is not None else None}}


def _shop_rate(shops: dict[str, list[str]], successful: set[str], complete: bool) -> dict:
    successes = sum(all(variant_id in successful for variant_id in members) for members in shops.values())
    return {**_rate(successes, len(shops), complete),
            "confidence_interval": _wilson(successes, len(shops)) if complete else None}


def _usability_distribution(outcomes: dict[str, dict]) -> dict:
    values = [outcome["final_usability"] for outcome in outcomes.values()
              if outcome["final_usability"] is not None]
    return {"measured": len(values), "missing": len(outcomes) - len(values),
            "mean": round(sum(values) / len(values), 4) if values else None,
            "min": round(min(values), 4) if values else None,
            "below_one": sum(value < 1.0 for value in values)}


def five_loop_metrics(records: list[dict], variants: dict[str, dict], ceiling_rows: list[dict],
                      baseline_records: dict[str, list[dict]] | None = None,
                      adjudicated_rows: list[dict] | None = None,
                      shop_ids: dict[str, str] | None = None) -> dict:
    """Score held-out variants; infer independent shops only from explicit IDs."""
    ceiling = {row["variant_id"]: row for row in ceiling_rows}
    adjudicated = {row["variant_id"]: row for row in adjudicated_rows or []}
    by_variant = {row["variant"]: row for row in records}
    if len(by_variant) != len(records):
        raise ValueError("duplicate five-loop variant records")
    unknown = by_variant.keys() - variants.keys()
    if unknown:
        raise ValueError(f"five-loop records outside held-out set: {sorted(unknown)}")
    for variant_id, row in by_variant.items():
        if row.get("scan_id") != variants[variant_id]["scan_id"]:
            raise ValueError(f"scan_id mismatch for {variant_id}")
    eligible = _eligible_variants(variants, by_variant, ceiling)
    _validate_shop_ids(eligible, shop_ids)
    outcomes = {variant_id: _loop_outcome(row) for variant_id, row in by_variant.items() if variant_id in eligible}
    statuses = {variant_id: _feasibility(variant_id, ceiling, adjudicated) for variant_id in eligible}
    shops = _shop_groups(eligible, shop_ids)
    shop_statuses = {shop: _shop_feasibility([statuses[variant_id] for variant_id in members])
                     for shop, members in shops.items()}
    successful = {variant_id for variant_id, outcome in outcomes.items() if outcome["success"]}
    strict = {variant_id for variant_id, outcome in outcomes.items() if outcome["strict"]}
    abstained = {variant_id for variant_id, outcome in outcomes.items() if outcome["abstained"]}
    five_attempt_budget = all(by_variant[variant_id].get("attempt_limit", 5) == 5 for variant_id in outcomes)
    complete = len(outcomes) == len(eligible) and five_attempt_budget
    accepted_moves = sum(outcome["applied"] for outcome in outcomes.values())
    unsafe_moves = sum(outcome["applied_without_gate"] for outcome in outcomes.values())
    intermediate_drops = sum(outcome["intermediate_usability_drops"] for outcome in outcomes.values())
    per_shop = {shop: {"feasibility": shop_statuses[shop], "eligible_variants": len(members),
                       "evaluated_variants": sum(variant_id in outcomes for variant_id in members),
                       "fully_cleared_variants": sum(variant_id in successful for variant_id in members),
                       "full_usability_preserved_variants": sum(variant_id in strict for variant_id in members),
                       "abstained_variants": sum(variant_id in abstained for variant_id in members),
                       "all_variants_pass": all(variant_id in successful for variant_id in members)
                       if all(variant_id in outcomes for variant_id in members) else None,
                       "all_variants_preserve_full_usability": all(variant_id in strict for variant_id in members)
                       if all(variant_id in outcomes for variant_id in members) else None}
                for shop, members in shops.items()}
    return {
        "unit": "physical shop" if shop_ids is not None else "scan group; independence unverified",
        "coverage": {"heldout_variants": len(variants), "ineligible_variants": len(variants) - len(eligible),
                     "eligible_variants": len(eligible), "evaluated_variants": len(outcomes),
                     "missing_variants": sorted(eligible.keys() - outcomes.keys()),
                     "five_attempt_budget": five_attempt_budget, "complete": complete,
                     "physical_shop_mapping_complete": shop_ids is not None},
        "feasibility": {"variants": dict(Counter(statuses.values())),
                        "physical_shops": dict(Counter(shop_statuses.values())) if shop_ids is not None else None,
                        "scan_groups": dict(Counter(shop_statuses.values())) if shop_ids is None else None,
                        "checker_full_clear_witnesses": sum(row.get("all_clear") is True for row in ceiling.values()),
                        "strict_usable_full_clear_witnesses": sum(
                            row.get("usable_all_clear") is True for row in ceiling.values()),
                        "basis": "checker full-clear witness or independent adjudication; bounded-search failure is unknown"},
        "per_physical_shop": per_shop if shop_ids is not None else None,
        "per_scan_group": per_shop if shop_ids is None else None,
        "full_clear_within_five": {"criterion": "checker full clear after gate-accepted moves",
                                   "variants_descriptive": _rate(len(successful), len(eligible), complete),
                                   "shops_all_variants": _shop_rate(shops, successful, complete)
                                   if shop_ids is not None else None,
                                   "scan_groups_all_variants_descriptive": _rate(
                                       sum(all(variant_id in successful for variant_id in members)
                                           for members in shops.values()), len(shops), complete)
                                   if shop_ids is None else None},
        "full_usability_preserved": {"criterion": "checker full clear and final U=1 against original baseline",
                                     "variants_descriptive": _rate(len(strict), len(eligible), complete),
                                     "shops_all_variants": _shop_rate(shops, strict, complete)
                                     if shop_ids is not None else None,
                                     "internal_final_usability_distribution": _usability_distribution(outcomes)},
        "abstention": {**_rate(len(abstained), len(eligible), complete),
                       "definition": "no final checker full-clear recommendation; partial moves stay internal"},
        "groups_all_variants_abstained": {**_rate(sum(all(variant_id in abstained for variant_id in members)
                                                     for members in shops.values()), len(shops), complete),
                                          "unit": "physical shop" if shop_ids is not None else "scan group"},
        "no_regression": {"applied_moves": accepted_moves, "applied_without_gate_pass": unsafe_moves,
                          "rate": round((accepted_moves - unsafe_moves) / accepted_moves, 4)
                          if accepted_moves else None,
                          "intermediate_usability_drops": intermediate_drops},
        "by_feasibility": {status: _rate(sum(variant_id in successful for variant_id in eligible
                                             if statuses[variant_id] == status),
                                         sum(value == status for value in statuses.values()), complete)
                           for status in ("feasible", "unknown", "unfixable")},
        "shops_by_feasibility": {status: _shop_rate(
            {shop: members for shop, members in shops.items() if shop_statuses[shop] == status},
            successful, complete) for status in ("feasible", "unknown", "unfixable")}
            if shop_ids is not None else None,
        "one_shot_best_of_k": {name: _baseline(rows, eligible, shop_ids)
                               for name, rows in (baseline_records or {}).items()},
        "comparison_note": "One-shot best-of-k uses its recorded sample count, not five sequential feedback loops.",
    }


def build(real: pathlib.Path, synthetic: pathlib.Path, five_loop: pathlib.Path | None = None,
          feasibility: pathlib.Path | None = None, shop_map: pathlib.Path | None = None) -> dict:
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
    ceiling = _ceiling(synthetic)
    results = {"models": {name: _model(_rows(path), variants, ravida) for name, path in locations.items()},
            "ceiling": {"variants": ceiling.get("searched"), "fixable": ceiling.get("fixable"),
                        "share_fixable": round(ceiling["fixable"] / ceiling["searched"], 4)
                        if ceiling.get("searched") else None, "per_variant": ceiling.get("rooms", []),
                        "interpretation": "bounded search reference; failure does not prove impossible"},
            "costs": {"run1_estimated": first.get("spend"),
                      "run1_pessimistic_preflight": first.get("plan", {}).get("expected_cost"),
                      "run2_estimated": second.get("spend"),
                      "run2_pessimistic_preflight": second.get("plan", {}).get("expected_cost"),
                      "actual": "Fireworks serverless API does not expose billed totals; consult billing"},
            "arkit": "Converted annotations are evaluation-only. Without observed walls, doors or routes, "
                     "model gate scores would be invented and are not reported."}
    if five_loop is not None:
        results["five_loop"] = five_loop_metrics(
            _rows(five_loop), variants, ceiling.get("rooms", []),
            {name: _rows(path) for name, path in locations.items()},
            _rows(feasibility) if feasibility else None,
            json.loads(shop_map.read_text()) if shop_map else None)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", type=pathlib.Path, required=True)
    parser.add_argument("--synthetic", type=pathlib.Path, required=True)
    parser.add_argument("--five-loop", type=pathlib.Path, help="one JSONL record per held-out variant")
    parser.add_argument("--feasibility", type=pathlib.Path, help="independently adjudicated variant statuses JSONL")
    parser.add_argument("--shop-map", type=pathlib.Path,
                        help="JSON object mapping every held-out scan_id to a physical shop_id")
    args = parser.parse_args()
    _write_json(args.synthetic / "results.json",
                build(args.real, args.synthetic, args.five_loop, args.feasibility, args.shop_map))


if __name__ == "__main__":
    main()
