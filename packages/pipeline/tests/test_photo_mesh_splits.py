"""Split-freeze logic tests: duplicate groups stay together, test stays untouched."""

import numpy as np
import pytest
from standardphysics_pipeline.render_efficiency.splits import (
    FrameSpec,
    duplicate_review_group,
    freeze_splits,
    select_test_frames,
    separated,
)


def spec(fid, t, pos, fwd=(0.0, 0.0, 1.0)):
    return FrameSpec(fid, t, np.asarray(pos, dtype=np.float64), np.asarray(fwd, dtype=np.float64))


def test_duplicate_group_detection():
    a = spec("a", 10.0, (0, 0, 0), (0, 0, 1))
    close = spec("b", 10.5, (0.1, 0, 0), (0.01, 0, 1))
    far = spec("c", 14.0, (0.9, 0, 0), (0.01, 0, 1))
    turned = spec("d", 10.5, (0.1, 0, 0), (0.5, 0, 1))
    assert duplicate_review_group(a, close)
    assert not duplicate_review_group(a, far)
    assert not duplicate_review_group(a, turned)


def test_separation_any_axis_clears():
    a = spec("a", 10.0, (0, 0, 0), (0, 0, 1))
    assert separated(a, spec("b", 14.0, (0.1, 0, 0), (0.01, 0, 1)))
    assert separated(a, spec("c", 10.5, (0.9, 0, 0), (0.01, 0, 1)))
    assert separated(a, spec("d", 10.5, (0.1, 0, 0), (0.5, 0, 1)))
    assert not separated(a, spec("e", 10.5, (0.1, 0, 0), (0.01, 0, 1)))


def test_whole_duplicate_group_withheld_together():
    a, b = spec("a", 10.0, (0, 0, 0)), spec("b", 10.5, (0.1, 0, 0))
    c = spec("c", 30.0, (4, 0, 0))
    frozen = freeze_splits({"a": a, "b": b, "c": c}, ["a", "b"], set(), {"frame_count": 6})
    assert frozen["test_views_captured"] == ["c"]
    pairs = frozen["diagnostics"]["pairs_within_validation_duplicate_groups"]
    assert ["a", "b"] in pairs


def test_test_pool_skips_exposed_ids():
    specs = {}
    for i in range(12):
        specs[f"f{i:02d}"] = spec(f"f{i:02d}", i * 10.0, (i * 2, 0, 0))
    frozen = freeze_splits(specs, ["f00"], {"f01"}, {"frame_count": 6})
    assert "f00" in frozen["validation_views_captured"]
    test = frozen["test_views_captured"]
    assert "f01" not in test and "f00" not in test


def test_elligible_pool_restriction():
    specs = {}
    for i in range(12):
        specs[f"f{i:02d}"] = spec(f"f{i:02d}", i * 10.0, (i * 2, 0, 0))
    eligibility = {f"f{i:02d}" for i in range(5, 12)}
    frozen = freeze_splits(specs, ["f00"], set(), {"frame_count": 6}, eligibility)
    assert set(frozen["test_views_captured"]).issubset(eligibility)


def test_exposed_frame_in_test_rejected():
    specs = {}
    for i in range(8):
        specs[f"f{i:02d}"] = spec(f"f{i:02d}", i * 10.0, (i * 2, 0, 0))
    with pytest.raises(ValueError):
        from standardphysics_pipeline.render_efficiency.splits import verify_split_identity
        verify_split_identity([specs[f"f{i:02d}"] for i in range(3)],
                              [specs["f03"]], {"f00", "f01", "f02", "f03"})


def test_deterministic_timestamp_order_and_skip_by_separation():
    specs = {}
    for i in range(10):
        specs[f"f{i:02d}"] = spec(f"f{i:02d}", i * 10.0, (i * 2, 0, 0))
    first = select_test_frames(list(specs.values()), [])
    second = select_test_frames(list(specs.values()), [])
    assert first == second
    pair = select_test_frames([specs["f00"], specs["f09"]], [], {"frame_count": 2})
    assert pair[0] == "f00" and pair[1] == "f09"
