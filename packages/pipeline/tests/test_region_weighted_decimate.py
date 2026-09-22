"""Region-weighted decimation tests: wall-erasing simplification is prevented."""

import numpy as np
import pytest
from standardphysics_pipeline.textures.surface_photos import region_weighted_decimate


def test_region_weighted_decimate_preserves_region_face_budget():
    rng = np.random.default_rng(0)
    vertices = rng.uniform(0, 2, (2000, 3)).astype(np.float64)
    # two floor-ish regions: left half region, right half rest
    left = vertices[:, 0] < 1.0
    triangles = []
    for i in range(0, 1999, 3):
        if i + 2 < 2000:
            triangles.append([i, i + 1, i + 2])
    triangles = np.asarray(triangles)
    region_faces = left[triangles].all(axis=1)
    assert region_faces.any() and (~region_faces).any()
    display_v, display_t, region_count = region_weighted_decimate(vertices, triangles, region_faces,
                                                    region_budget=60, rest_budget=40)
    assert 0 < region_count <= 60
    assert 0 < len(display_t) <= 100
    assert display_t.min() >= 0 and display_t.max() < len(display_v)


def test_region_weighted_decimate_refuses_empty_region():
    rng = np.random.default_rng(1)
    vertices = rng.uniform(0, 2, (60, 3))
    triangles = np.arange(0, 60).reshape(20, 3)
    with pytest.raises(ValueError):
        region_weighted_decimate(vertices, triangles, np.zeros(20, dtype=bool), 10, 40)


def test_region_weighted_decimate_refuses_tiny_budgets():
    rng = np.random.default_rng(2)
    vertices = rng.uniform(0, 2, (60, 3))
    triangles = np.arange(0, 60).reshape(20, 3)
    mask = np.ones(20, dtype=bool)
    with pytest.raises(ValueError):
        region_weighted_decimate(vertices, triangles, mask, 2, 40)


def test_region_weighted_decimate_keeps_wall_sheet():
    """A thin double-wall sheet: the front wall must survive with budget to spare."""
    # front wall at z=1 (region), back wall at z=0 (rest), both 1x1 grids
    front, back = [], []
    for x in np.linspace(0, 1, 20):
        for y in np.linspace(0, 1, 20):
            front.append([x, y, 1.0])
            back.append([x, y, 0.0])
    front = np.asarray(front)
    back = np.asarray(back)
    vertices = np.concatenate([front, back])
    triangles = []
    n = len(front)
    for i in range(18):
        for j in range(18):
            a = i * 19 + j
            triangles.append([a, a + 1, a + 19])
            triangles.append([a + 1, a + 20, a + 19])
            b = n + i * 19 + j
            triangles.append([b, b + 1, b + 19])
            triangles.append([b + 1, b + 20, b + 19])
    triangles = np.asarray(triangles)
    region_faces = np.zeros(len(triangles), dtype=bool)
    region_faces[: 2 * 18 * 18] = True
    display_v, display_t, region_count = region_weighted_decimate(vertices, triangles, region_faces,
                                                    region_budget=300, rest_budget=100)
    assert 0 < region_count <= 648
    front_faces = (display_t < n).all(axis=1).sum()
    back_faces = (display_t >= n).any(axis=1).sum()
    half_of_sheet = (2 * 18 * 18) // 2
    assert front_faces > half_of_sheet
    assert 0 <= back_faces <= 100
    assert display_t.max() < len(display_v)
