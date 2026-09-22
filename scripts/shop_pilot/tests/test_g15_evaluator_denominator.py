"""Deterministic denominator test for the SHIPPED G15 evaluator (K head).

Import the actual c9/c11 denominator entry points: covered_fraction and
hole_metrics from scripts/evaluate_photo_mesh_500.py. Fixture: a 10x10 fixed
measured mask with a photographed pass missing exactly 4 pixels. Baseline
must report 0.96 photographed/critical-ROI fraction and a 0.04 hole; an
injection that counts unphotographed/transparent pixels as photographed
flips the fractions to 1.0/0.0 and fails these assertions behaviorally.

Set EVALUATOR_DIR to the checkout whose evaluator should be exercised
(default: this repository's own scripts directory).
"""

from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
from PIL import Image

EVALUATOR_DIR = pathlib.Path(os.environ.get("EVALUATOR_DIR", str(pathlib.Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(EVALUATOR_DIR / "scripts"))

try:
    from evaluate_photo_mesh_500 import covered_fraction, hole_metrics  # noqa: E402
    HAS_EVALUATOR = True
except Exception:  # evaluator not importable in this checkout
    HAS_EVALUATOR = False


def _pass_with_hole(path: pathlib.Path) -> None:
    raster = np.full((10, 10), 255, dtype=np.uint8)
    raster[0:2, 0:2] = 0
    Image.fromarray(raster, mode="L").save(path)


def test_denominator_counts_only_photographed_pixels(tmp_path):
    if not HAS_EVALUATOR:
        import pytest

        pytest.skip(f"shipped evaluator unavailable under {EVALUATOR_DIR}")
    photographed_path = tmp_path / "coverage.png"
    _pass_with_hole(photographed_path)
    fixed_mask = np.ones((10, 10), dtype=bool)
    roi_mask = fixed_mask.copy()
    result = covered_fraction(photographed_path, fixed_mask, roi_mask)
    assert result["target_pixels"] == 100
    assert result["photographed_fraction"] == 0.96
    assert result["critical_roi_fraction"] == 0.96
    holes, _untextured = hole_metrics(photographed_path, fixed_mask)
    assert holes["largest_hole_fraction"] == 0.04
    assert holes["hole_fraction_total"] == 0.04
