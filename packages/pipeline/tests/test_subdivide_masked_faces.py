"""Midpoint subdivision of masked faces: coplanar split, rest unchanged."""

import numpy as np
from standardphysics_pipeline.textures.surface_photos import snap_to_measured, subdivide_masked_faces


def square_sheet():
    vertices = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]])
    triangles = np.array([[0, 1, 2], [0, 2, 3]])
    return vertices, triangles


def test_unmasked_part_untouched():
    vertices, triangles = square_sheet()
    new_v, new_t = subdivide_masked_faces(vertices, triangles, np.array([True, False]))
    assert len(new_t) == 1 + 4
    assert np.allclose(new_v[:4], vertices)
    assert np.array_equal(new_t[0], triangles[1])


def test_area_preserved():
    vertices, triangles = square_sheet()
    mask = np.array([True, False])
    new_v, new_t = subdivide_masked_faces(vertices, triangles, mask)
    old_area = 0.5 + 0.5
    corners = new_v[new_t]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    new_area = np.linalg.norm(cross, axis=1).sum() / 2
    assert abs(new_area - old_area) < 1e-12


def test_midpoints_and_snap():
    vertices, triangles = square_sheet()
    mask = np.ones(2, dtype=bool)
    new_v, new_t = subdivide_masked_faces(vertices, triangles, mask)
    assert len(new_v) == 4 + 6
    assert len(new_t) == 8
    measured = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0.5, 0, 0], [1, 0.5, 0], [0.5, 1, 0], [0, 0.5, 0],
                         [0.5, 0.5, 0], [0.5, 0.5, 0]])
    snapped, count, _ = snap_to_measured(new_v, measured, max_distance=0.15)
    assert count >= 6


def test_thin_wall_sheet_subdivision_size():
    rng = np.random.default_rng(7)
    wall = rng.uniform(0, 2, (400, 3))
    wall[:, 2] = 1.0
    triangles = []
    for i in range(0, 398, 2):
        triangles.append([i, i + 1, i + 2])
    triangles = np.asarray(triangles)
    mask = np.ones(len(triangles), dtype=bool)
    new_v, new_t = subdivide_masked_faces(wall, triangles, mask)
    assert len(new_t) == 4 * len(triangles)
