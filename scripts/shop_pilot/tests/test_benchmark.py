"""Tests for the G12 semantic benchmark evaluator protocol."""

from __future__ import annotations

import math

from scripts.shop_pilot.benchmark import DEFAULT_2D_MATCH_IOU_MIN, box_iou, evaluate_g12, wilson_interval


def _inventory() -> dict:
    return {
        "sites": ["shop-a", "shop-b", "shop-c"],
        "classes": ["outlet", "television", "service_counter", "restroom_entrance"],
        "excludes_tuning_sites": True,
        "annotation": {
            "actor": "Human Reviewer 1",
            "attestation": "inventory fixed before any model output was shown",
            "fixed_before_predictions": True,
        },
        "target_boxes": [],
        "negative_regions": [],
    }


def _pred(pred_object: str, frame: str, box: list[float], label: str) -> dict:
    return {"object_id": pred_object, "frame": frame, "box": box, "class": label, "provenance": "automatic"}


def test_box_iou_matches_only_at_threshold():
    box = [0, 0, 100, 100]
    assert box_iou(box, [0, 0, 50, 50]) == 0.25
    assert box_iou(box, [100, 100, 200, 200]) == 0.0


def test_wilson_interval_zero_denominator_is_insufficient():
    low, high = wilson_interval(0, 0)
    assert math.isnan(low) and math.isnan(high)


def test_fifty_seventy_five_interval_sane():
    low, high = wilson_interval(75, 100)
    assert 0.65 < low < high < 0.85


def test_same_object_in_twenty_frames_is_one_true_positive():
    inventory = _inventory()
    inventory["target_boxes"] = [
        {"object_id": "o1", "frame": f"f{i}", "box": [0, 0, 100, 100], "class": "outlet"}
        for i in range(20)
    ]
    predictions = {"predictions": [
        _pred("p1", f"f{i}", [0, 0, 100, 100], "outlet") for i in range(20)
    ]}
    result = evaluate_g12(inventory, predictions)
    metrics = result["per_class"]["outlet"]
    assert metrics["gt_objects"] == 1
    assert metrics["distinct_true_positives"] == 1
    assert metrics["predicted_objects"] == 1
    assert metrics["false_positive_objects"] == 0


def test_object_without_identity_is_protocol_error():
    inventory = _inventory()
    inventory["target_boxes"] = [
        {"object_id": "o1", "frame": "f0", "box": [0, 0, 100, 100], "class": "outlet"}
    ]
    predictions = {"predictions": [{"frame": "f0", "box": [0, 0, 100, 100], "class": "outlet", "provenance": "automatic"}]}
    result = evaluate_g12(inventory, predictions)
    assert result["status"] == "invalid_protocol"
    assert any("object identity" in problem for problem in result["protocol_problems"])


def test_manual_correction_cannot_enter_numerator():
    inventory = _inventory()
    inventory["target_boxes"] = [
        {"object_id": "o1", "frame": "f0", "box": [0, 0, 100, 100], "class": "outlet"}
    ]
    predictions = {"predictions": [
        {"object_id": "p1", "frame": "f0", "box": [0, 0, 100, 100], "class": "outlet", "provenance": "manual"}
    ]}
    result = evaluate_g12(inventory, predictions)
    assert any("not automatic" in problem for problem in result["protocol_problems"])
    assert result["manually_corrected_predictions_in_automatic_numerator"] == 0


def test_missing_human_review_is_protocol_error():
    inventory = _inventory()
    inventory["annotation"]["actor"] = "agent"
    result = evaluate_g12(inventory, {"predictions": []})
    assert result["status"] == "invalid_protocol"


def test_fewer_than_three_sites_is_protocol_error():
    inventory = _inventory()
    inventory["sites"] = ["shop-a"]
    result = evaluate_g12(inventory, {"predictions": []})
    assert any("3 distinct" in problem for problem in result["protocol_problems"])


def test_zero_objects_is_insufficient_not_hundred_percent():
    inventory = _inventory()
    result = evaluate_g12(inventory, {"predictions": [], "abstentions": []})
    assert result["status"] == "insufficient_evidence"
    assert result["per_class"]["outlet"]["precision"] is None


def test_insufficient_sample_counts_do_not_pass():
    inventory = _inventory()
    inventory["target_boxes"] = [
        {"object_id": "o1", "frame": "f0", "box": [0, 0, 100, 100], "class": "outlet"}
    ]
    predictions = {"predictions": [_pred("p1", "f0", [0, 0, 100, 100], "outlet")]}
    result = evaluate_g12(inventory, predictions)
    outlet = result["per_class"]["outlet"]
    assert outlet["distinct_true_positives"] == 1
    assert outlet["sample_sufficient"] is False
    assert outlet["passes"] is False


def test_false_positive_object_misses_all_targets():
    inventory = _inventory()
    inventory["target_boxes"] = [
        {"object_id": "o1", "frame": "f0", "box": [0, 0, 100, 100], "class": "outlet"}
    ]
    predictions = {"predictions": [_pred("p1", "f0", [500, 500, 600, 600], "outlet")]}
    result = evaluate_g12(inventory, predictions)
    outlet = result["per_class"]["outlet"]
    assert outlet["false_positive_objects"] == 1
    assert outlet["distinct_true_positives"] == 0
    assert outlet["missed_objects"] == 1


def test_prediction_in_negative_region_is_false_positive():
    inventory = _inventory()
    inventory["negative_regions"] = [
        {"frame": "f0", "box": [0, 0, 100, 100], "class": "outlet"}
    ]
    predictions = {"predictions": [_pred("p1", "f0", [0, 0, 100, 100], "outlet")]}
    result = evaluate_g12(inventory, predictions)
    outlet = result["per_class"]["outlet"]
    assert outlet["false_positive_objects"] == 1
    assert outlet["false_positives_in_negative_regions"] == 1


def test_default_iou_threshold_rejects_half_overlap():
    assert box_iou([0, 0, 100, 100], [50, 0, 150, 100]) < DEFAULT_2D_MATCH_IOU_MIN
