"""Photographic surface visibility: correct depth, occluders, and missing views."""
import numpy as np
import pytest

pytest.importorskip("numba", reason="offline photographic bake requires reconstruction extra")

from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.surface_photos import choose_views, surface_depth, visible_points


def camera():
    return PhotoCamera("frame-0000", np.eye(4), 50, 50, 31.5, 23.5, 64, 48, 0)


def square(z, half):
    return np.array([[-half, -half, z], [half, -half, z], [half, half, z], [-half, half, z]], dtype=float)


def test_foreground_triangle_interior_blocks_background_even_without_vertices_on_ray():
    front, back = square(2, .4), square(3, 1.2)
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    buffer = surface_depth(camera(), np.vstack([front, back]), np.vstack([faces, faces+4]))
    visible, _, _ = visible_points(camera(), np.array([[0, 0, 2], [0, 0, 3], [.9, 0, 3]]), buffer)
    assert visible.tolist() == [True, False, True]


def test_sloping_surface_has_perspective_correct_depth_without_artificial_erosion():
    vertices = square(2, 2)
    vertices[:, 2] += vertices[:, 0]*.4
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    cam = camera()
    buffer = surface_depth(cam, vertices, faces)
    # Intersect each pixel ray with z = 2 + 0.4*x.
    for column in [10, 30, 50]:
        expected = 2/(1-.4*(column-cam.cx)/cam.fx)
        assert buffer[24, column] == pytest.approx(expected, abs=1e-5)


def test_unobserved_surface_has_no_assigned_photograph():
    vertices = square(-2, .4)
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    assignments, areas = choose_views(vertices, faces, [camera()])
    assert assignments.tolist() == [-1, -1]
    assert areas.sum() == pytest.approx(.64)


def test_matching_front_surface_can_receive_a_view():
    vertices = square(2, .4)
    faces = np.array([[0, 2, 1], [0, 3, 2]])
    assignments, _ = choose_views(vertices, faces, [camera()])
    assert assignments.tolist() == [0, 0]
