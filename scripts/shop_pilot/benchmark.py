"""G12 automatic-semantics benchmark evaluator (lane Q acceptance area).

Protocol frozen from 05-adversarial-tests.txt: human-reviewed ground truth of
unique physical objects with per-frame boxes and negative regions, declared
before predictions; one-to-one class matching with IoU >= 0.5; one physical
socket seen in 20 frames is one object; manual corrections never enter the
automatic numerator; per-class precision/recall with Wilson binomial lower
bounds; every class meets its own threshold from 04-hard-gates.json.

Without real human-reviewed held-out inventories this evaluator returns
``invalid_protocol`` or ``insufficient_evidence``. That is the honest state
of G12, not a failure to be hidden by a synthetic pass.
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_2D_MATCH_IOU_MIN = 0.5
CLASS_LIMITS = {
    "outlet": {"positives_min": 30, "negatives_min": 30, "precision_min": 0.95, "recall_min": 0.9},
    "television": {"positives_min": 10, "negatives_min": 30, "precision_min": 0.9, "recall_min": 0.9},
    "service_counter": {"positives_min": 10, "negatives_min": 30, "precision_min": 0.9, "recall_min": 0.9},
    "restroom_entrance": {"positives_min": 10, "negatives_min": 30, "precision_min": 0.9, "recall_min": 0.9},
}
SUPPORTED_CLASSES = tuple(CLASS_LIMITS)


def box_iou(box_a: list[float], box_b: list[float]) -> float:
    a_x1, a_y1, a_x2, a_y2 = box_a
    b_x1, b_y1, b_x2, b_y2 = box_b
    a_area = max(a_x2 - a_x1, 0.0) * max(a_y2 - a_y1, 0.0)
    b_area = max(b_x2 - b_x1, 0.0) * max(b_y2 - b_y1, 0.0)
    if a_area <= 0 or b_area <= 0:
        return 0.0
    inter = (
        max(min(a_x2, b_x2) - max(a_x1, b_x1), 0.0)
        * max(min(a_y2, b_y2) - max(a_y1, b_y1), 0.0)
    )
    return inter / (a_area + b_area - inter)


def wilson_interval(tp: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson binomial CI; n == 0 is the insufficient marker, never 100%."""
    if n <= 0:
        return (math.nan, math.nan)
    phat = tp / n
    denom = 1.0 + (z * z) / n
    centre = (phat + (z * z) / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + (z * z) / (4 * n * n)) / denom
    return (max(centre - half, 0.0), min(centre + half, 1.0))


def _validate_annotation(inventory: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    annotation = inventory.get("annotation")
    if not isinstance(annotation, dict):
        return ["ground truth must be human-reviewed with an annotation record"]
    if str(annotation.get("actor", "")).lower() in ("self_review", "agent", "auto", ""):
        problems.append("ground truth must be human-reviewed with attributable actor")
    if not annotation.get("fixed_before_predictions"):
        problems.append("ground truth must record that it was fixed before predictions")
    return problems


def _validate_site_declaration(inventory: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    sites = set(inventory.get("sites", []))
    if len(sites) < 3:
        problems.append("fewer than 3 distinct held-out sites declared")
    if not inventory.get("excludes_tuning_sites"):
        problems.append("inventory does not confirm exclusion of sites used for tuning")
    return problems


def _automatic_predictions(predictions: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []
    for index, pred in enumerate(predictions.get("predictions", [])):
        if not isinstance(pred, dict):
            problems.append(f"prediction[{index}] is not an object")
            continue
        if pred.get("provenance") != "automatic":
            problems.append(f"prediction[{index}] is not automatic; cannot enter the numerator")
            continue
        if pred.get("class") not in SUPPORTED_CLASSES:
            problems.append(f"prediction[{index}] has unsupported class {pred.get('class')!r}")
            continue
        if not isinstance(pred.get("box"), list):
            problems.append(f"prediction[{index}] has no source box")
            continue
        if not pred.get("object_id"):
            problems.append(f"prediction[{index}] has no object identity; unique-object counting impossible")
            continue
        grouped.setdefault(pred["class"], []).append(pred)
    return grouped, problems


def _frame_key(entry: dict[str, Any]) -> str:
    return str(entry.get("frame", entry.get("image_id", "")))


def _object_matches(
    grouped: dict[str, list[dict[str, Any]]], label: str, inventory: dict[str, Any], iou_min: float
) -> dict[str, str]:
    """Map distinct predicted objects to at most one ground-truth object each.

    A pair matches when any of its frames share a class box with IoU >= min.
    """
    gt_boxes = [box for box in inventory.get("target_boxes", []) if box.get("class") == label]
    matches: dict[str, str] = {}
    for pred in grouped.get(label, []):
        pred_object = str(pred["object_id"])
        if pred_object in matches:
            continue
        pred_frame = _frame_key(pred)
        for gt in gt_boxes:
            if _frame_key(gt) != pred_frame:
                continue
            if str(gt.get("object_id", gt.get("id", f"gt{id(gt)}"))) in matches.values():
                continue
            if box_iou(pred["box"], gt["box"]) >= iou_min:
                matches[pred_object] = str(gt.get("object_id", gt.get("id", f"gt{id(gt)}")))
                break
    return matches


def _inside_negative(pred: dict[str, Any], negatives: list[dict[str, Any]], iou_min: float) -> bool:
    for negative in negatives:
        if _frame_key(negative) == _frame_key(pred) and box_iou(pred["box"], negative["box"]) >= iou_min:
            return True
    return False


def _label_metrics(
    label: str,
    grouped: dict[str, list[dict[str, Any]]],
    inventory: dict[str, Any],
    iou_min: float,
) -> dict[str, Any]:
    gt_objects = {
        str(box.get("object_id", box.get("id", f"gt{index}")))
        for index, box in enumerate(inventory.get("target_boxes", []))
        if box.get("class") == label
    }
    negatives = [box for box in inventory.get("negative_regions", []) if box.get("class") == label]
    matches = _object_matches(grouped, label, inventory, iou_min)

    matched_pred_objects = set(matches)
    matched_gt_objects = set(matches.values())
    in_negative = sum(
        1
        for pred_object in {str(p["object_id"]) for p in grouped.get(label, [])}
        if pred_object not in matched_pred_objects
        and any(
            _inside_negative(p, negatives, iou_min)
            for p in grouped.get(label, [])
            if str(p["object_id"]) == pred_object
        )
    )
    unmatched_pred_objects = {str(p["object_id"]) for p in grouped.get(label, [])} - matched_pred_objects
    fp_objects = len(unmatched_pred_objects)
    tp_objects = len(matched_gt_objects)
    fn_objects = len(gt_objects) - len(matched_gt_objects)
    pred_objects = len({str(p["object_id"]) for p in grouped.get(label, [])})
    return {
        "class": label,
        "gt_objects": len(gt_objects),
        "predicted_objects": pred_objects,
        "distinct_true_positives": tp_objects,
        "false_positive_objects": fp_objects,
        "false_positives_in_negative_regions": in_negative,
        "missed_objects": max(fn_objects, 0),
        "negative_regions": len(negatives),
        "insufficient_reasons": _insufficient_reasons(label, len(gt_objects), len(negatives)),
    }


def _insufficient_reasons(label: str, gt_objects: int, negatives: int) -> list[str]:
    limits = CLASS_LIMITS[label]
    reasons = []
    if gt_objects < limits["positives_min"]:
        reasons.append(f"only {gt_objects} held-out objects, need >= {limits['positives_min']}")
    if negatives < limits["negatives_min"]:
        reasons.append(f"only {negatives} negative regions, need >= {limits['negatives_min']}")
    return reasons


def _decide_label(metrics: dict[str, Any]) -> dict[str, Any]:
    limits = CLASS_LIMITS[metrics["class"]]
    tp = metrics["distinct_true_positives"]
    precision = tp / (tp + metrics["false_positive_objects"]) if (tp + metrics["false_positive_objects"]) else None
    recall = tp / metrics["gt_objects"] if metrics["gt_objects"] else None
    precision_ok = precision is not None and precision >= limits["precision_min"]
    recall_ok = recall is not None and recall >= limits["recall_min"]
    wilson_low, _ = wilson_interval(tp, tp + metrics["false_positive_objects"])
    wilson_recall, _ = wilson_interval(tp, metrics["gt_objects"])
    passes = precision_ok and recall_ok and not metrics["insufficient_reasons"]
    return {
        "precision": precision,
        "recall": recall,
        "precision_ok": precision_ok,
        "recall_ok": recall_ok,
        "wilson_precision_low": wilson_low,
        "wilson_recall_low": wilson_recall,
        "sample_sufficient": not metrics["insufficient_reasons"],
        "passes": passes,
        "limits": limits,
    }


def evaluate_g12(
    inventory: dict[str, Any], predictions: dict[str, Any], *, iou_min: float = DEFAULT_2D_MATCH_IOU_MIN
) -> dict[str, Any]:
    """Evaluate the frozen G12 benchmark against human-reviewed ground truth."""
    problems: list[str] = []
    problems.extend(_validate_annotation(inventory))
    problems.extend(_validate_site_declaration(inventory))
    grouped, prediction_problems = _automatic_predictions(predictions)
    problems.extend(prediction_problems)

    per_class: dict[str, Any] = {}
    for label in SUPPORTED_CLASSES:
        metrics = _label_metrics(label, grouped, inventory, iou_min)
        metrics.update(_decide_label(metrics))
        per_class[label] = metrics

    has_identifiable_objects = any(m["gt_objects"] for m in per_class.values())
    if problems:
        status = "invalid_protocol"
    elif not has_identifiable_objects:
        status = "insufficient_evidence"
    elif all(metrics.get("passes") for metrics in per_class.values()):
        status = "passed"
    else:
        status = "failed"
    return {
        "status": status,
        "protocol_problems": problems,
        "abstentions_reported": bool(predictions.get("abstentions")),
        "manually_corrected_predictions_in_automatic_numerator": 0,
        "per_class": per_class,
        "note": "benchmark limits are engineering entry targets, not universal accuracy claims",
    }
