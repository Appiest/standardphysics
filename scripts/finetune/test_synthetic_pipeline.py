"""Bounded bookkeeping checks; generation and evaluation run only on compute-box."""

from synthetic_results import _metrics


def test_best_of_k_counts_variants_and_physical_rooms_separately():
    variants = {"a": {"window_id": "room-1"}, "b": {"window_id": "room-1"},
                "c": {"window_id": "room-2"}}
    records = [
        {"variant": "a", "parsed": True, "hard_constraints_pass": True, "gate_accepts": False,
         "shortfall_recovered": 0, "fixable_left": 1, "reward": 0},
        {"variant": "a", "parsed": True, "hard_constraints_pass": True, "gate_accepts": True,
         "shortfall_recovered": 1, "fixable_left": 0, "reward": 0.8, "usability": 0.9,
         "quality": {"q": 0.7}},
        {"variant": "b", "parsed": False, "hard_constraints_pass": False, "gate_accepts": False,
         "shortfall_recovered": 0, "fixable_left": None, "reward": 0},
        {"variant": "c", "parsed": True, "hard_constraints_pass": True, "gate_accepts": False,
         "shortfall_recovered": 0, "fixable_left": 1, "reward": 0},
    ]

    result = _metrics(records, variants)

    assert result["gate_acceptance"] == 0.25
    assert result["variant_best_of_k_accepted"] == 0.3333
    assert result["room_best_of_k_accepted"] == 0.5
    assert result["room_best_of_k_fully_cleared"] == 0.5
    assert result["mean_usability_accepted"] == 0.9
    assert result["mean_quality_accepted_logged_only"] == 0.7
