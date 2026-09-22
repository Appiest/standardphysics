"""Frame split freezing for benchmark moffett-photo-mesh-500.

The split rule from the benchmark policy: frames used for evaluation may never
be used for bake source selection or tuning, and whole reviewed duplicate
groups are withheld together. Frames whose inspection history is disclosed as
"exposed" are withheld from the bake and labelled diagnostic. Frames with no
recorded inspection are labelled held-out-from-bake with that disclosure.

Split v2 changes versus v1 (runs/moffett/photo-mesh-500/r001/stageA/splits.json):
- frame-0088 was previously in test_views_captured but had been inspected by
  the camera-unblock diagnostic: moved to exposed validation.
- frame-0081/frame-0083 form a duplicate group (1.0s, 0.184m, 4.13deg); the
  whole group now sits in exposed validation instead of test.
- test_views_captured is re-selected from frames with no recorded inspection,
  separated from every other split frame by the thresholds below.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

DUPLICATE_REVIEW_TRIGGER = {"dt_s": 1.0, "distance_m": 0.15, "angle_deg": 10.0}
"""Review trigger: pairs closer than these are a duplicate group (kept together)."""

SELECTION_SEPARATION = {"dt_s": 3.0, "distance_m": 0.5, "angle_deg": 15.0}
"""Split independence: a test frame must satisfy at least one of
(dt > 3s, distance > 0.5m, angle > 15deg) against every other split frame.
Stricter than the review trigger so no near-duplicate pair can span groups."""


@dataclasses.dataclass(frozen=True)
class FrameSpec:
    frame_id: str
    timestamp: float
    position: np.ndarray
    forward: np.ndarray


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    return float(math.degrees(math.acos(float(np.clip(a @ b, -1.0, 1.0)))))


def duplicate_review_group(a: FrameSpec, b: FrameSpec) -> bool:
    """True when a pair meets all review-trigger thresholds: a duplicate group."""
    return (
        abs(a.timestamp - b.timestamp) <= DUPLICATE_REVIEW_TRIGGER["dt_s"]
        and float(np.linalg.norm(a.position - b.position)) <= DUPLICATE_REVIEW_TRIGGER["distance_m"]
        and _angle_deg(a.forward, b.forward) <= DUPLICATE_REVIEW_TRIGGER["angle_deg"]
    )


def separated(a: FrameSpec, b: FrameSpec) -> bool:
    """True when at least one separation axis clears its threshold."""
    return (
        abs(a.timestamp - b.timestamp) > SELECTION_SEPARATION["dt_s"]
        or float(np.linalg.norm(a.position - b.position)) > SELECTION_SEPARATION["distance_m"]
        or _angle_deg(a.forward, b.forward) > SELECTION_SEPARATION["angle_deg"]
    )


def select_test_frames(specs: list[FrameSpec], validation: list[FrameSpec],
                       tolerance: dict | None = None) -> list[str]:
    """Choose up to ``tolerance`` frames separated from validation and each other.

    Deterministic: frames are ordered by timestamp, ties by frame_id.
    """
    target_count = (tolerance or {}).get("frame_count", 6)
    ordered = sorted(specs, key=lambda s: (s.timestamp, s.frame_id))
    picked: list[FrameSpec] = []
    for spec in ordered:
        if any(spec.frame_id == v.frame_id for v in validation):
            continue
        if not all(separated(spec, v) for v in validation):
            continue
        if not all(separated(spec, p) for p in picked):
            continue
        picked.append(spec)
        if len(picked) >= target_count:
            break
    return [s.frame_id for s in picked]


def _verify_membership(validation: list[FrameSpec], test: list[FrameSpec], exposed_ids: set[str]) -> None:
    if len({v.frame_id for v in validation}) != len(validation):
        raise ValueError("duplicate frame in validation")
    if len({t.frame_id for t in test}) != len(test):
        raise ValueError("duplicate frame in test")
    ids = {v.frame_id for v in validation} & {t.frame_id for t in test}
    if ids:
        raise ValueError(f"frames in both validation and test: {sorted(ids)}")
    leaks = [t.frame_id for t in test if t.frame_id in exposed_ids]
    if leaks:
        raise ValueError(f"exposed frames may not be untouched test: {sorted(leaks)}")


def _verify_separation(validation: list[FrameSpec], test: list[FrameSpec]) -> None:
    for t in test:
        if not all(separated(t, v) for v in validation):
            raise ValueError(f"test frame {t.frame_id} not separated from a validation frame")
        for other in test:
            if other.frame_id != t.frame_id and not separated(t, other):
                raise ValueError(f"test frame {t.frame_id} not separated from {other.frame_id}")


def _duplicate_groups(validation: list[FrameSpec]) -> list[list[str]]:
    return [sorted((a.frame_id, b.frame_id))
            for i, a in enumerate(validation) for b in validation[i + 1:]
            if duplicate_review_group(a, b)]


def verify_split_identity(validation: list[FrameSpec], test: list[FrameSpec],
                          exposed_ids: set[str]) -> dict:
    """Return diagnostics; raise when the split violates its own rules."""
    _verify_membership(validation, test, exposed_ids)
    _verify_separation(validation, test)
    return {"validation_count": len(validation), "test_count": len(test),
            "pairs_within_validation_duplicate_groups": _duplicate_groups(validation),
            "rule": "whole reviewed duplicate groups withheld; test untouched and separated"}


def freeze_splits(specs_by_id: dict[str, FrameSpec],
                  exposed_validation_ids: list[str],
                  exposed_non_split_ids: set[str],
                  tolerance: dict | None = None,
                  eligible_ids: set[str] | None = None) -> dict:
    """Freeze split v2: exposed diagnostic validation plus untouched separated test.

    ``eligible_ids`` restricts the untouched test pool (for example a sharpness
    prefilter applied before the freeze); validation frames never need it.
    """

    for fid in exposed_validation_ids:
        if fid not in specs_by_id:
            raise ValueError(f"validation frame {fid} missing from capture")
    validation = [specs_by_id[fid] for fid in exposed_validation_ids]
    eligible = [spec for fid, spec in specs_by_id.items()
                if fid not in exposed_validation_ids and fid not in exposed_non_split_ids
                and (eligible_ids is None or fid in eligible_ids)]
    test_ids = select_test_frames(eligible, validation, tolerance)
    test = [specs_by_id[fid] for fid in test_ids]
    diagnostics = verify_split_identity(validation, test,
                                        set(exposed_validation_ids) | set(exposed_non_split_ids))
    return {
        "validation_views_captured": [v.frame_id for v in validation],
        "test_views_captured": [t.frame_id for t in test],
        "exposed_validation_notes": {
            fid: "previously inspected; diagnostic, held-out-from-bake"
            for fid in exposed_validation_ids},
        "test_exposure_notes": {
            fid: "no recorded inspection by any agent; held-out-from-bake; "
                 "exposure disclosure bounded by recorded history"
            for fid in test_ids},
        "selection": {
            "code": "packages/pipeline/standardphysics_pipeline/render_efficiency/splits.py",
            "split_version": 2,
            "v1_to_v2": [
                "frame-0088 moved from test to exposed validation (camera-unblock diagnostic inspected it)",
                "frame-0081/frame-0083 duplicate group (1.0s, 0.184m, 4.13deg) moved from test to exposed validation",
                "test_views_captured re-selected from frames with no recorded inspection"],
            "duplicate_review_trigger": DUPLICATE_REVIEW_TRIGGER,
            "selection_separation": SELECTION_SEPARATION,
            "target_test_count": (tolerance or {}).get("frame_count", 6),
        },
        "diagnostics": diagnostics,
        "rule": "no frame in either split group may be used for bake selection or tuning",
    }


def freeze_splits_from_docs(poses: list[dict], exposed_validation_ids: list[str],
                            exposed_non_split_ids: set[str],
                            tolerance: dict | None = None,
                            positions: dict[str, tuple] | None = None,
                            eligible_ids: set[str] | None = None) -> dict:
    """Build :class:`FrameSpec` per pose, using optional precomputed positions."""
    specs: dict[str, FrameSpec] = {}
    for pose in poses:
        fid = pose["frame_id"]
        if fid in specs:
            continue
        if positions is not None and fid in positions:
            position = np.asarray(positions[fid][0], dtype=np.float64)
            forward = np.asarray(positions[fid][1], dtype=np.float64)
        else:
            transform = np.asarray(pose["transform"], dtype=np.float64).reshape(4, 4)
            rotation, translation = transform[:3, :3], transform[:3, 3]
            position = -rotation.T @ translation
            forward = -rotation[2, :]
        specs[fid] = FrameSpec(fid, float(pose["timestamp"]), position, forward)
    return freeze_splits(specs, exposed_validation_ids, exposed_non_split_ids, tolerance,
                         eligible_ids)
