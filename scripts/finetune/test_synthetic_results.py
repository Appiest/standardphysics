"""Five-loop reporting keeps correlated variants under their physical shop."""

import json

import pytest
from synthetic_results import _ceiling, five_loop_metrics

VARIANTS = {"a": {"scan_id": "shop-1"}, "b": {"scan_id": "shop-1"},
            "c": {"scan_id": "shop-2"}}
SHOP_IDS = {"shop-1": "physical-a", "shop-2": "physical-b"}


def attempt(*, accepted=True, clear=True, usable=1.0, gate=True):
    return {"accepted": accepted, "step_usability": usable,
            "verdict": {"gate_accepts": gate, "fixable_left": 0 if clear else 1}}


def record(variant, attempts, final_usability=1.0):
    accepted = [item for item in attempts if item["accepted"]]
    final_left = accepted[-1]["verdict"]["fixable_left"] if accepted else 1
    return {"variant": variant, "scan_id": VARIANTS[variant]["scan_id"], "attempts": attempts,
            "evaluation_policy": "checker-full-clear-v1",
            "final_fixable_left": final_left, "final_baseline_usability": final_usability}


def test_full_clear_uses_all_variants_per_independent_shop_and_no_regression():
    records = [record("a", [attempt()]), record("b", [attempt(clear=False), attempt()]),
               record("c", [attempt(accepted=False, gate=False)])]
    ceiling = [{"variant_id": "a", "all_clear": True, "usable_all_clear": True},
               {"variant_id": "b", "all_clear": True}, {"variant_id": "c", "all_clear": False}]
    result = five_loop_metrics(records, VARIANTS, ceiling, shop_ids=SHOP_IDS)

    assert result["coverage"]["complete"] is True
    assert result["full_clear_within_five"]["variants_descriptive"]["successes"] == 2
    assert result["full_clear_within_five"]["shops_all_variants"]["successes"] == 1
    assert result["full_clear_within_five"]["shops_all_variants"]["denominator"] == 2
    assert result["full_clear_within_five"]["shops_all_variants"]["confidence_interval"]["high"] < 1
    assert result["per_physical_shop"]["physical-a"]["all_variants_pass"] is True
    assert result["per_physical_shop"]["physical-b"]["all_variants_pass"] is False
    assert result["feasibility"]["variants"] == {"feasible": 2, "unknown": 1}
    assert result["feasibility"]["physical_shops"] == {"feasible": 1, "unknown": 1}
    assert result["feasibility"]["checker_full_clear_witnesses"] == 2
    assert result["abstention"]["successes"] == 1
    assert result["full_usability_preserved"]["variants_descriptive"]["successes"] == 2
    assert result["no_regression"] == {"applied_moves": 3, "applied_without_gate_pass": 0,
                                       "rate": 1.0, "intermediate_usability_drops": 0}


def test_missing_variant_suppresses_rate_and_interval():
    result = five_loop_metrics([record("a", [attempt()])], VARIANTS, [], shop_ids=SHOP_IDS)

    assert result["coverage"]["missing_variants"] == ["b", "c"]
    assert result["full_clear_within_five"]["shops_all_variants"]["rate"] is None
    assert result["full_clear_within_five"]["shops_all_variants"]["confidence_interval"] is None
    assert result["per_physical_shop"]["physical-a"]["all_variants_pass"] is None


def test_ineligible_variant_is_removed_from_denominator():
    ineligible = {**record("c", []), "eligible": False, "baseline_fixable_left": 0}
    ceiling = [{"variant_id": "c", "baseline_fixable_findings": 0}]
    result = five_loop_metrics([record("a", [attempt()]), record("b", [attempt()]), ineligible],
                               VARIANTS, ceiling, shop_ids=SHOP_IDS)

    assert result["coverage"]["ineligible_variants"] == 1
    assert result["coverage"]["eligible_variants"] == 2
    assert result["full_clear_within_five"]["shops_all_variants"]["denominator"] == 1


def test_shorter_attempt_limit_does_not_claim_five_loop_rate():
    records = [{**record(variant, [attempt()]), "attempt_limit": 3} for variant in VARIANTS]
    result = five_loop_metrics(records, VARIANTS, [], shop_ids=SHOP_IDS)

    assert result["coverage"]["five_attempt_budget"] is False
    assert result["full_clear_within_five"]["shops_all_variants"]["rate"] is None


def test_intermediate_usability_drop_does_not_block_checker_clear():
    records = [record("a", [attempt(clear=False, usable=0.8), attempt()], final_usability=1.0),
               record("b", []), record("c", [])]
    result = five_loop_metrics(records, VARIANTS, [])

    assert result["full_clear_within_five"]["variants_descriptive"]["successes"] == 1
    assert result["full_usability_preserved"]["variants_descriptive"]["successes"] == 1
    assert result["no_regression"]["applied_without_gate_pass"] == 0
    assert result["no_regression"]["intermediate_usability_drops"] == 1
    assert result["abstention"]["successes"] == 2


def test_final_usability_loss_is_separate_from_primary_checker_clear():
    result = five_loop_metrics([record("a", [attempt(usable=0.7)], final_usability=0.7)], VARIANTS, [])

    assert result["full_clear_within_five"]["variants_descriptive"]["successes"] == 1
    assert result["full_usability_preserved"]["variants_descriptive"]["successes"] == 0
    assert result["full_usability_preserved"]["internal_final_usability_distribution"]["below_one"] == 1
    assert result["no_regression"]["rate"] == 1.0


def test_bounded_search_failure_does_not_certify_unfixable():
    result = five_loop_metrics([], VARIANTS, [{"variant_id": "a", "all_clear": False}])
    assert result["feasibility"]["variants"] == {"unknown": 3}

    with pytest.raises(ValueError, match="needs evidence"):
        five_loop_metrics([], VARIANTS, [], adjudicated_rows=[{"variant_id": "a", "status": "unfixable"}])


def test_five_loop_budget_duplicate_and_shop_identity_are_validated():
    with pytest.raises(ValueError, match="five attempts"):
        five_loop_metrics([record("a", [attempt()] * 6)], VARIANTS, [])
    with pytest.raises(ValueError, match="duplicate"):
        five_loop_metrics([record("a", []), record("a", [])], VARIANTS, [])
    with pytest.raises(ValueError, match="scan_id mismatch"):
        five_loop_metrics([{**record("a", []), "scan_id": "wrong"}], VARIANTS, [])


def test_historical_baseline_is_labeled_checker_only():
    baseline = {"sft": [{"variant": "a", "gate_accepts": True, "fixable_left": 0},
                        {"variant": "b", "gate_accepts": True, "fixable_left": 0},
                        {"variant": "c", "gate_accepts": False, "fixable_left": 1}]}
    result = five_loop_metrics([], VARIANTS, [], baseline, shop_ids=SHOP_IDS)

    assert result["one_shot_best_of_k"]["sft"]["scan_or_shop_groups_all_variants"]["successes"] == 1
    assert "final baseline usability was not recorded" in result["one_shot_best_of_k"]["sft"]["criterion"]


def test_ceiling_falls_back_to_archived_results_without_claiming_usability(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({"ceiling": {
        "variants": 1, "fixable": 1, "per_variant": [{"variant_id": "a", "all_clear": True}]}}))
    ceiling = _ceiling(tmp_path)

    assert ceiling["searched"] == 1
    result = five_loop_metrics([], VARIANTS, ceiling["rooms"])
    assert result["feasibility"]["variants"] == {"feasible": 1, "unknown": 2}
    assert result["feasibility"]["strict_usable_full_clear_witnesses"] == 0


def test_scan_id_without_physical_shop_map_has_no_independence_interval():
    records = [record(variant, [attempt()]) for variant in VARIANTS]
    result = five_loop_metrics(records, VARIANTS, [])

    assert result["per_physical_shop"] is None
    assert result["shops_by_feasibility"] is None
    assert result["full_clear_within_five"]["shops_all_variants"] is None
    assert result["full_clear_within_five"]["scan_groups_all_variants_descriptive"]["rate"] == 1.0
    assert result["coverage"]["physical_shop_mapping_complete"] is False

    with pytest.raises(ValueError, match="missing physical shop IDs"):
        five_loop_metrics(records, VARIANTS, [], shop_ids={"shop-1": "physical-a"})
