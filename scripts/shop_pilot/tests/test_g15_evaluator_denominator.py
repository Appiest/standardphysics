"""Deterministic denominator test for the SHIPPED G15 evaluator (K head).

Loads the actual c9/c11 denominator entry points from the checkout named by
EVALUATOR_DIR (default: this repository root) and requires EVALUATOR_DIR to
name a checkout whose scripts/evaluate_photo_mesh_500.py exposes
covered_fraction and hole_metrics. There is deliberately NO skip wrapper: an
import failure is a visible error, never a hidden pass.

Run against the integrated K tree like:

  EVALUATOR_DIR=<K-tree> \
  <venv>/python -m pytest scripts/shop_pilot/tests/test_g15_evaluator_denominator.py \
    -o pythonpath="<K-tree>/packages/pipeline:<K-tree>/packages/contracts:<K-tree>/packages/fixtures:<K-tree>/scripts"
"""

from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
from PIL import Image

EVALUATOR_DIR = pathlib.Path(
    os.environ.get(
        "EVALUATOR_DIR",
        str(pathlib.Path(__file__).resolve().parents[3]),
    )
)

for entry in (
    EVALUATOR_DIR / "scripts",
    EVALUATOR_DIR / "packages" / "pipeline",
    EVALUATOR_DIR / "packages" / "contracts",
    EVALUATOR_DIR / "packages" / "fixtures",
):
    sys.path.insert(0, str(entry))

from evaluate_photo_mesh_500 import covered_fraction, hole_metrics  # noqa: E402


def _pass_with_hole(path: pathlib.Path) -> None:
    raster = np.full((10, 10), 255, dtype=np.uint8)
    raster[0:2, 0:2] = 0
    Image.fromarray(raster, mode="L").save(path)


def test_denominator_counts_only_photographed_pixels(tmp_path):
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
