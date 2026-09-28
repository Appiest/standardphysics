"""Policy-driven experiment evaluator for benchmark version 3.

This is the binding that makes ``score.compute_only_if_all_gates_pass`` true:
one entry point reads the supplied policy JSON, validates the run records and
the evidence artifacts on disk, derives every hard gate from the policy's own
numbers, and only then emits a selection score.  Arithmetic helpers in
``scoring`` do not implement the policy on their own; this module does the
policy enforcement, never substitutes a missing value for zero, and never
turns unknown into pass.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from typing import Any

from .manifest import sha256_file, validate_view_list_against_expected

NUMERIC_GATE_KEYS = (
    "max_mean_psnr_drop_db",
    "max_mean_full_frame_psnr_drop_db",
    "max_mean_ssim_drop",
    "max_each_critical_roi_psnr_drop_db",
    "max_each_view_psnr_drop_db",
    "max_supported_uncovered_fraction_increase",
    "max_frame_p95_ratio_candidate_to_baseline",
)

_DIVISOR_PATTERN = re.compile(r"/\s*([0-9.eE+-]+)")


class PolicyError(ValueError):
    """Raised when evidence or the policy itself forbids selection scoring."""


def _finite(value: Any, name: str) -> float:
    """NaN/Inf (either sign) and bool-as-number are invalid evidence (E01)."""
    if isinstance(value, bool):
        raise PolicyError(f"{name} is a boolean, not a number")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise PolicyError(f"{name} is not numeric: {value!r}") from None
    if not math.isfinite(numeric):
        raise PolicyError(f"{name} must be finite, got {numeric}")
    return numeric


def _in_range(value: Any, name: str, low: float, high: float) -> float:
    numeric = _finite(value, name)
    if not low <= numeric <= high:
        raise PolicyError(f"{name} must lie in [{low},{high}], got {numeric}")
    return numeric


def load_policy(path: pathlib.Path | str) -> dict:
    """Read the policy JSON and verify it is a v3 revised experiment policy."""
    policy_path = pathlib.Path(path)
    raw = json.loads(policy_path.read_text())
    if raw.get("kind") != "revised_experiment_policy_not_measured_results":
        raise PolicyError(f"not a revised experiment policy: {policy_path}")
    if raw.get("benchmark_version") != 3:
        raise PolicyError(f"unexpected benchmark version: {raw.get('benchmark_version')}")
    for key in NUMERIC_GATE_KEYS:
        if key not in raw.get("hard_numeric_gates", {}):
            raise PolicyError(f"policy lacks hard numeric gate {key!r}")
    if not raw.get("hard_non_numeric_gates"):
        raise PolicyError("policy defines no non-numeric gates")
    if raw.get("score", {}).get("minimum_selection_score") is None:
        raise PolicyError("policy lacks minimum_selection_score")
    raw["_policy_path"] = str(policy_path.resolve())
    return raw


def policy_file_hash(policy_path: pathlib.Path | str) -> str:
    return sha256_file(policy_path)


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))


def _scale_of(policy: dict, name: str) -> float:
    """Numeric constant inside a policy formula string (never hardcoded here)."""
    formula = policy.get("score", {}).get(name, "")
    matches = _DIVISOR_PATTERN.findall(formula)
    if not matches:
        raise PolicyError(f"policy score formula {name!r} has no numeric constant")
    return _finite(matches[-1], f"{name} constant")


def _quality_weights(policy: dict) -> tuple[float, float, float]:
    """(w_psnr, w_ssim, w_critical) read out of the policy's quality formula."""
    formula = policy.get("score", {}).get("quality", "")
    weights = [float(token) for token in re.findall(r"([0-9]+\.[0-9]+)\*q_", formula)]
    if len(weights) != 3:
        raise PolicyError(f"policy quality formula yields {len(weights)} weights, expected 3")
    return weights[0], weights[1], weights[2]


def _expected_views(run: dict) -> set[str]:
    expected = run.get("expected_views")
    if not expected:
        raise PolicyError("run record lacks expected_views")
    return {str(entry) for entry in expected}


def _validate_view_records(run: dict, expected: set[str]) -> list[dict]:
    """Exact set equality (no missing, extra or duplicate) and per-view metrics."""
    views = run.get("views") or []
    validate_view_list_against_expected(
        [{"file_path": view["id"]} for view in views], expected
    )
    return views


def _aggregates(views: list[dict]) -> dict[str, float]:
    for view in views:
        for key in ("psnr_db", "full_psnr_db", "ssim"):
            if key not in view or view[key] is None:
                raise PolicyError(f"view {view.get('id')} lacks metric {key!r}")
        for key in ("psnr_db", "full_psnr_db"):
            _finite(view[key], key)
        _in_range(view["ssim"], "ssim", -1.0, 1.0)
    means = {
        key: float(sum(_finite(view[key], key) for view in views) / len(views))
        for key in ("psnr_db", "full_psnr_db", "ssim")
    }
    means["worst_psnr_db"] = min(_finite(view["psnr_db"], "psnr_db") for view in views)
    return means


def _view_deltas(baseline: dict, candidate: dict, expected: set[str]) -> list[float]:
    base_by_id = {view["id"]: _finite(view["psnr_db"], "psnr_db") for view in baseline["views"]}
    cand_by_id = {view["id"]: _finite(view["psnr_db"], "psnr_db") for view in candidate["views"]}
    sorted_ids = sorted(expected)
    return [cand_by_id[i] - base_by_id[i] for i in sorted_ids]


def _roi_deltas(baseline: dict, candidate: dict, expected_rois: set[str]) -> list[float]:
    """Validation ROIs must match the preregistered set exactly on both runs."""
    candidate_rois = candidate.get("rois") or {}
    base_rois = baseline.get("rois") or {}
    present_c = set(candidate_rois)
    present_b = set(base_rois)
    if present_c != expected_rois:
        missing = sorted(expected_rois - present_c)
        extra = sorted(present_c - expected_rois)
        raise PolicyError(f"candidate ROI set mismatch: missing={missing}, extra={extra}")
    if present_b != expected_rois:
        raise PolicyError("baseline ROI set does not match the preregistered set")
    deltas = []
    for roi_id in sorted(expected_rois):
        base_psnr = _in_range(base_rois[roi_id].get("psnr_db"), f"baseline roi {roi_id}", 0.0, 100.0)
        cand_psnr = _in_range(candidate_rois[roi_id].get("psnr_db"), f"candidate roi {roi_id}", 0.0, 100.0)
        deltas.append(cand_psnr - base_psnr)
    return deltas


def _coverage_of(run: dict) -> tuple[float | None, str | None]:
    coverage = run.get("coverage")
    if coverage is None:
        return None, "coverage record absent"
    if coverage.get("alpha_available") is not True:
        return None, "genuine accumulated alpha unavailable; opaque PNG or brightness inference cannot count"
    return _in_range(coverage.get("uncovered_fraction"), "uncovered_fraction", 0.0, 1.0), None


def _frame_eligibility(run: dict) -> tuple[float | None, str]:
    session = run.get("viewer_sessions")
    if not session:
        return None, "viewer sessions absent"
    required = int(session.get("required", 3))
    recorded = int(session.get("recorded", 0))
    if recorded < required:
        return None, f"recorded {recorded} viewer sessions, required {required}"
    identity = session.get("gpu_identity")
    if identity in (None, "", "software_GL", "swiftshader"):
        return None, "no hardware GPU identity; software GL cannot supply eligible frame timing"
    if int(session.get("completed_frame_samples", 0)) < 300:
        return None, "fewer than 300 completed presented frames per required session"
    return _finite(session.get("worst_session_p95_ms"), "worst_session_p95_ms"), "ok"


def _resource_eligibility(run: dict) -> str | None:
    resources = run.get("resources") or {}
    repeats = run.get("repeats") or []
    if len(repeats) != 3:
        return f"resource eligibility needs 3 repeats, record has {len(repeats)}"
    method = resources.get("footprint_method")
    if method in (None, "rss", "resident_set_size"):
        return "peak RSS is not comparable process footprint; the policy requires peak footprint bytes"
    for key in ("cold_end_to_end_seconds", "peak_footprint_bytes", "compressed_spz_bytes"):
        value = resources.get(key)
        if value is None:
            return f"missing resource measurement {key!r} (preprocessing through compressed export)"
        _finite(value, key)
    if resources.get("includes_compressed_export") is not True:
        return "timing excludes compressed export; the policy requires preprocessing through export"
    if not run.get("resource_scope_matches_baseline", False):
        return "resource scope does not match the baseline input workload"
    backend = run.get("backend")
    if not backend or not backend.get("runtime"):
        return "runtime backend settings not recorded"
    return None


def _check_hashes_on_disk(run: dict) -> str | None:
    hashes = run.get("hashes") or {}
    for kind in ("inputs", "outputs"):
        for path, expected_sha in (hashes.get(kind) or {}).items():
            if not pathlib.Path(path).is_file():
                return f"{kind} artifact missing on disk: {path}"
            actual = sha256_file(path)
            if actual != expected_sha:
                return f"artifact hash mismatch for {path}"
    return None


def _non_numeric_verdicts(policy: dict, run: dict) -> dict[str, bool]:
    verdicts: dict[str, bool] = {}
    recorded = run.get("non_numeric_gates") or {}
    for gate_id in policy["hard_non_numeric_gates"]:
        entry = recorded.get(gate_id)
        if entry is None or entry.get("passed") is not True:
            verdicts[gate_id] = False
            continue
        evidence = entry.get("evidence_paths") or []
        if not evidence or not all(pathlib.Path(p).is_file() for p in evidence):
            verdicts[gate_id] = False
            continue
        if gate_id == "frozen_input_split_evaluator_and_artifact_hashes_validate":
            verdicts[gate_id] = _check_hashes_on_disk(run) is None
        else:
            verdicts[gate_id] = True
    return verdicts



def _collect_pellets(
    policy: dict,
    cand_agg: dict,
    base_agg: dict,
    baseline: dict,
    candidate: dict,
    expected: set[str],
) -> tuple[dict[str, tuple[bool, str]], list[str], bool]:
    """Numeric gate verdicts, missing-evidence list, and whether uncovered
    coverage was actually measured (E01-E06 rules)."""
    gates = policy["hard_numeric_gates"]
    psnr_delta = cand_agg["psnr_db"] - base_agg["psnr_db"]
    full_delta = cand_agg["full_psnr_db"] - base_agg["full_psnr_db"]
    ssim_delta = cand_agg["ssim"] - base_agg["ssim"]
    worst_delta = min(_view_deltas(baseline, candidate, expected))

    roi_blocked = None
    try:
        roi_deltas = _roi_deltas(baseline, candidate, policy["_roi_ids"])
    except PolicyError as error:
        roi_deltas = []
        roi_blocked = str(error)
    min_roi_delta = min(roi_deltas) if roi_deltas else None

    uncovered_base, base_cov_reason = _coverage_of(baseline)
    uncovered_cand, cand_cov_reason = _coverage_of(candidate)
    coverage_block: str | None = base_cov_reason or cand_cov_reason
    coverage_delta: float | None = None
    if uncovered_base is not None and uncovered_cand is not None:
        coverage_block, coverage_delta = None, uncovered_cand - uncovered_base

    frame_base, frame_base_reason = _frame_eligibility(baseline)
    frame_cand, frame_cand_reason = _frame_eligibility(candidate)
    frame_ratio = frame_cand / frame_base if frame_base is not None and frame_cand is not None else None
    frame_problem = None
    if frame_base is None:
        frame_problem = frame_base_reason
    elif frame_cand is None:
        frame_problem = frame_cand_reason

    pellets: dict[str, tuple[bool, str]] = {}
    pellets["psnr_mean"] = (psnr_delta >= -gates["max_mean_psnr_drop_db"], f"delta {psnr_delta:.6f} dB")
    pellets["full_psnr_mean"] = (full_delta >= -gates["max_mean_full_frame_psnr_drop_db"], f"delta {full_delta:.6f} dB")
    pellets["ssim_mean"] = (ssim_delta >= -gates["max_mean_ssim_drop"], f"delta {ssim_delta:.6f}")
    pellets["worst_view"] = (worst_delta >= -gates["max_each_view_psnr_drop_db"], f"delta {worst_delta:.6f} dB")
    pellets["critical_roi"] = (
        min_roi_delta is not None and min_roi_delta >= -gates["max_each_critical_roi_psnr_drop_db"],
        roi_blocked if roi_blocked else (f"min roi delta {min_roi_delta:.6f} dB" if min_roi_delta is not None else "no ROI evidence"),
    )
    pellets["uncovered"] = (
        coverage_delta is not None and coverage_delta <= gates["max_supported_uncovered_fraction_increase"],
        coverage_block if coverage_block else (f"delta {coverage_delta:.6f}" if coverage_delta is not None else "unavailable"),
    )
    pellets["frame_p95"] = (
        frame_ratio is not None and frame_ratio <= gates["max_frame_p95_ratio_candidate_to_baseline"],
        frame_problem if frame_problem else f"ratio {frame_ratio:.6f}",
    )
    resource_base = _resource_eligibility(baseline)
    resource_cand = _resource_eligibility(candidate)
    pellets["resources_base"] = (resource_base is None, resource_base or "ok")
    pellets["resources_candidate"] = (resource_cand is None, resource_cand or "ok")

    missing_bits = []
    if roi_blocked is not None:
        missing_bits.append("validation ROI")
    if coverage_block is not None:
        missing_bits.append("alpha coverage")
    if frame_problem is not None:
        missing_bits.append("viewer frame timing")
    if resource_base is not None or resource_cand is not None:
        missing_bits.append("resource eligibility")
    uncovered_measured = coverage_block is None
    return pellets, missing_bits, uncovered_measured


def _frozen_policy_problem(policy: dict, candidate: dict) -> str | None:
    recorded = candidate.get("hashes", {}).get("policy_sha256")
    if recorded is None:
        return "candidate record does not carry the policy hash"
    if recorded != policy_file_hash(policy["_policy_path"]):
        return "frozen policy hash mismatch; change requires a new benchmark version"
    return None


def _score_and_lane(
    policy: dict,
    baseline: dict,
    candidate: dict,
    cand_agg: dict,
    base_agg: dict,
    min_roi_delta: float,
    frame_base: float,
    frame_cand: float,
) -> tuple[float, str | None]:
    """Combined score plus the one selection lane that applies, if any."""
    w_psnr, w_ssim, w_critical = _quality_weights(policy)
    q_psnr = _clip(0.5 + (cand_agg["psnr_db"] - base_agg["psnr_db"]) / _scale_of(policy, "q_psnr"))
    q_ssim = _clip(0.5 + (cand_agg["ssim"] - base_agg["ssim"]) / _scale_of(policy, "q_ssim"))
    q_critical = _clip(0.5 + min_roi_delta / _scale_of(policy, "q_critical"))
    quality = 100.0 * (w_psnr * q_psnr + w_ssim * q_ssim + w_critical * q_critical)

    base_resources = baseline["resources"]
    cand_resources = candidate["resources"]
    gain_scale = _scale_of(policy, "resource_gain")

    def _gain(key: str) -> float:
        base_value = _finite(base_resources[key], f"baseline {key}")
        cand_value = _finite(cand_resources[key], f"candidate {key}")
        if not (base_value > 0 and cand_value > 0):
            raise PolicyError(f"positive finite {key} required")
        return 100.0 * _clip(0.5 + math.log2(base_value / cand_value) / gain_scale)

    gains = {key: _gain(key) for key in ("cold_end_to_end_seconds", "peak_footprint_bytes", "compressed_spz_bytes")}
    frame_gain = 100.0 * _clip(0.5 + math.log2(frame_base / frame_cand) / gain_scale)
    weights = policy["score"]["weights"]
    score = (
        weights["quality"] * quality
        + weights["cold_end_to_end_seconds"] * gains["cold_end_to_end_seconds"]
        + weights["peak_footprint_bytes"] * gains["peak_footprint_bytes"]
        + weights["compressed_spz_bytes"] * gains["compressed_spz_bytes"]
        + weights["rendered_frame_p95_ms"] * frame_gain
    )
    lanes = policy["selection_lanes"]
    quality_lane = (
        cand_agg["psnr_db"] - base_agg["psnr_db"] >= lanes["quality"]["min_mean_psnr_improvement_db"]
        and cand_agg["ssim"] - base_agg["ssim"] >= lanes["quality"]["min_mean_ssim_improvement"]
        and cand_resources["cold_end_to_end_seconds"] / base_resources["cold_end_to_end_seconds"] <= lanes["quality"]["max_time_ratio_candidate_to_baseline"]
        and cand_resources["peak_footprint_bytes"] / base_resources["peak_footprint_bytes"] <= lanes["quality"]["max_footprint_ratio_candidate_to_baseline"]
        and cand_resources["compressed_spz_bytes"] / base_resources["compressed_spz_bytes"] <= lanes["quality"]["max_spz_size_ratio_candidate_to_baseline"]
    )
    efficiency_lane = (
        base_resources["cold_end_to_end_seconds"] / cand_resources["cold_end_to_end_seconds"] >= lanes["efficiency"]["min_time_ratio_baseline_to_candidate"]
        and cand_resources["peak_footprint_bytes"] / base_resources["peak_footprint_bytes"] <= lanes["efficiency"]["max_footprint_ratio_candidate_to_baseline"]
        and cand_resources["compressed_spz_bytes"] / base_resources["compressed_spz_bytes"] <= lanes["efficiency"]["max_spz_size_ratio_candidate_to_baseline"]
    )
    lane = "quality" if quality_lane else ("efficiency" if efficiency_lane else None)
    return score, lane


def evaluate(
    policy: dict,
    baseline: dict,
    candidate: dict,
    expected_roi_ids: set[str],
) -> dict:
    """Evaluate baseline vs candidate strictly against the supplied policy.

    Returns a result with ``status`` in the policy's allowed states and either
    a numeric ``selection_score`` or JSON null plus a structured reason.
    """
    policy["_roi_ids"] = set(expected_roi_ids)
    expected = _expected_views(baseline)
    if _expected_views(candidate) != expected:
        raise PolicyError("baseline and candidate disagree on the expected view set")
    baseline_views = _validate_view_records(baseline, expected)
    candidate_views = _validate_view_records(candidate, expected)
    try:
        base_agg = _aggregates(baseline_views)
        cand_agg = _aggregates(candidate_views)
    except PolicyError as error:
        return _refused(f"per-view evidence incomplete: {error}")

    pellets, missing_bits, uncovered_measured = _collect_pellets(
        policy, cand_agg, base_agg, baseline, candidate, expected
    )
    non_numeric = _non_numeric_verdicts(policy, candidate)
    frozen_problem = _frozen_policy_problem(policy, candidate)

    gate_results = {name: {"passed": ok, "detail": detail} for name, (ok, detail) in pellets.items()}
    for gate_id, ok in non_numeric.items():
        gate_results[f"non_numeric:{gate_id}"] = {"passed": ok, "detail": "evidence verified" if ok else "no verified verdict/evidence"}
    gate_results["frozen_policy_hash"] = {"passed": frozen_problem is None, "detail": frozen_problem or "verified"}
    all_ok = all(ok for ok, _ in pellets.values()) and all(non_numeric.values()) and frozen_problem is None
    if not all_ok:
        measured_failures = any(
            not pellets[name][0] for name in ("psnr_mean", "full_psnr_mean", "ssim_mean", "worst_view")
        ) or (uncovered_measured and not pellets["uncovered"][0])
        if measured_failures:
            return {
                "status": "completed_rejected",
                "selection_score": None,
                "score_reason": "a measured hard-quality gate failed under complete image evidence; see gate_results",
                "gate_results": gate_results,
            }
        if missing_bits:
            return {
                "status": "blocked_missing_evidence",
                "selection_score": None,
                "score_reason": f"missing evidence prevents selection: {', '.join(missing_bits)}",
                "gate_results": gate_results,
            }
        return {
            "status": "completed_rejected",
            "selection_score": None,
            "score_reason": "a hard gate failed under complete evidence; see gate_results",
            "gate_results": gate_results,
        }

    try:
        roi_deltas = _roi_deltas(baseline, candidate, policy["_roi_ids"])
    except PolicyError:
        roi_deltas = []
    min_roi_delta = min(roi_deltas) if roi_deltas else 0.0
    frame_base, _ = _frame_eligibility(baseline)
    frame_cand, _ = _frame_eligibility(candidate)
    assert frame_base is not None and frame_cand is not None
    return _adjudicate(
        policy, baseline, candidate, cand_agg, base_agg,
        min_roi_delta, frame_base, frame_cand, gate_results,
    )


def _adjudicate(
    policy: dict,
    baseline: dict,
    candidate: dict,
    cand_agg: dict,
    base_agg: dict,
    min_roi_delta: float,
    frame_base: float,
    frame_cand: float,
    gate_results: dict,
) -> dict:
    """Score, selection lane and terminal state once gates passed."""
    try:
        score, lane = _score_and_lane(
            policy, baseline, candidate, cand_agg, base_agg,
            min_roi_delta, frame_base, frame_cand,
        )
    except PolicyError as error:
        return {
            "status": "blocked_missing_evidence",
            "selection_score": None,
            "score_reason": f"score inputs invalid: {error}",
            "gate_results": gate_results,
        }
    minimum = policy["score"]["minimum_selection_score"]
    if score < minimum:
        return {
            "status": "completed_rejected",
            "selection_score": round(score, 6),
            "score_reason": f"score {score:.4f} below minimum_selection_score {minimum}",
            "selection_lane": lane,
            "gate_results": gate_results,
        }
    if lane is None:
        return {
            "status": "completed_rejected",
            "selection_score": round(score, 6),
            "score_reason": "gates and minimum score pass but no unchanged selection lane applies",
            "selection_lane": None,
            "gate_results": gate_results,
        }
    return {
        "status": "selected_for_review",
        "selection_score": round(score, 6),
        "selection_lane": lane,
        "score_reason": "all required gates, minimum score and one selection lane pass",
        "gate_results": gate_results,
    }


def _refused(reason: str) -> dict:
    return {
        "status": "blocked_missing_evidence",
        "selection_score": None,
        "score_reason": reason,
        "gate_results": {},
    }


__all__ = [
    "NUMERIC_GATE_KEYS",
    "PolicyError",
    "evaluate",
    "load_policy",
    "policy_file_hash",
]
