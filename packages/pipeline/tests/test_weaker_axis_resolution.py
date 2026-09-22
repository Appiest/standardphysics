"""Anisotropic resolution tests: the weaker axis bounds sampling, mip included."""

import numpy as np
from standardphysics_pipeline.render_efficiency.photo_mesh_500 import (
    resolution_gate_stats,
    weaker_axis_resolution,
)


def build_probe(slope_x=1.0, slope_y=1.0, size=64, palette=5):
    """Flat interior: uv = (x * slope_x, y * slope_y) in [0,1] over the probe."""
    rows = []
    for y in range(size):
        for x in range(size):
            rows.append((x / (size - 1) * slope_x, y / (size - 1) * slope_y))
    uv = np.asarray(rows, dtype=np.float64).reshape(size, size, 2)
    texi = np.full((size, size), 0, dtype=np.int32)
    depth = np.full((size, size), 2.0, dtype=np.float64)
    tex_dims = {0: (palette * 8, palette * 8)}
    return uv, texi, depth, tex_dims


def test_isotropic_weaker_matches_rate():
    uv, texi, depth, dims = build_probe(1.0, 1.0)
    weaker, stronger, mip, valid = weaker_axis_resolution(uv, texi, depth, dims)
    interior = valid.sum()
    assert interior > 1000
    expected = (1.0 / 63.0) * 40.0  # uv gradient (1/63 per pixel) x 40 texels across
    assert np.allclose(np.median(weaker[valid]), expected, rtol=0.15)


def test_anisotropic_weaker_axis_detected():
    uv, texi, depth, dims = build_probe(1.0, 0.25, size=64, palette=4)
    weaker, stronger, mip, valid = weaker_axis_resolution(uv, texi, depth, dims)
    assert np.median(stronger[valid]) > 2.0 * np.median(weaker[valid])
    stats = resolution_gate_stats(weaker, stronger, mip, valid)
    assert stats["anisotropy_p95_ratio"] > 2.0


def test_mip_reduces_source_samples():
    uv, texi, depth, dims = build_probe(4.0, 4.0, size=64, palette=32)  # heavy minification
    weaker, stronger, mip, valid = weaker_axis_resolution(uv, texi, depth, dims)
    stats = resolution_gate_stats(weaker, stronger, mip, valid)
    source = stats["median_source_samples_per_px_at_mip"]
    texels = stats["median_weaker_texels_per_px"]
    assert stats["median_active_mip"] >= 1.0
    assert source <= texels
    assert stats["fraction_meeting_both_1"] <= 1.0


def test_untextured_pixels_not_counted():
    uv, texi, depth, dims = build_probe(1.0, 1.0, size=32)
    texi[8:24, 8:24] = -1
    weaker, stronger, mip, valid = weaker_axis_resolution(uv, texi, depth, dims)
    assert not valid[8:24, 8:24].any()


def test_roi_gate_reporting():
    uv, texi, depth, dims = build_probe(1.0, 1.0, size=32)
    weaker, stronger, mip, valid = weaker_axis_resolution(uv, texi, depth, dims)
    roi = np.zeros((32, 32), dtype=bool)
    roi[8:24, 8:24] = True
    stats = resolution_gate_stats(weaker, stronger, mip, valid, roi)
    assert stats["critical_roi_pixels"] == roi[valid].sum()
    assert stats["critical_roi_source_meeting_2"] is not None
