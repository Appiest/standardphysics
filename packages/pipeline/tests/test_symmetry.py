"""Completing half-seen furniture by reflecting its scanned half.

The object is two upright side panels, like the sides of a chair, at x = -0.4
and x = +0.4. The scan has the left panel whole and the right panel's lower half,
so the right panel's upper half is what reflection should supply.
"""

from __future__ import annotations

import uuid

import numpy as np
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.textures.camera import PhotoCamera
from standardphysics_pipeline.textures.scan_colour import (
    ColouredScan,
    unused_vertices_removed,
    vertex_normals,
    with_mirrored_colours,
)
from standardphysics_pipeline.textures.symmetry import mirrored_completion, seen_through_by


def chair_box() -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind="object", label="Chair", raw_category="chair",
        dimensions=Vec3(x=0.9, y=0.6, z=0.9), transform=Mat4.translation(0.0, 0.0, 0.45),
    )


def panel(x: float, z_top: float, facing: float, spacing: float = 0.03):
    """An upright grid at x from the floor to z_top, facing +x or -x."""
    ys = np.arange(-0.27, 0.27 + 1e-9, spacing)
    zs = np.arange(0.1, z_top + 1e-9, spacing)
    grid_y, grid_z = np.meshgrid(ys, zs)
    vertices = np.stack([np.full(grid_y.size, x), grid_y.ravel(), grid_z.ravel()], axis=1)
    row = len(ys)
    triangles = []
    for j in range(len(zs) - 1):
        for i in range(row - 1):
            corner = j * row + i
            first, second = [corner, corner + 1, corner + row + 1], [corner, corner + row + 1, corner + row]
            triangles += [first, second] if facing > 0 else [first[::-1], second[::-1]]
    return vertices, np.asarray(triangles)


def half_seen_chair():
    left, left_faces = panel(-0.4, 0.85, facing=-1.0)
    right, right_faces = panel(0.4, 0.45, facing=1.0)
    vertices = np.concatenate([left, right])
    triangles = np.concatenate([left_faces, right_faces + len(left)])
    return vertices, triangles


def nothing_seen_through(points):
    return np.zeros(len(points), dtype=bool)


def graph_of(node: SceneNode) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[node])


def upper_right(points):
    return (points[:, 0] > 0.3) & (points[:, 2] > 0.5)


def test_the_unseen_half_of_a_symmetric_object_is_reflected_in():
    vertices, triangles = half_seen_chair()
    completed = mirrored_completion(vertices, triangles, graph_of(chair_box()), nothing_seen_through)
    added = completed.vertices[completed.added]
    assert completed.planes and completed.planes[0].axis == 0
    assert upper_right(added).sum() > 50
    assert (completed.source[completed.added] >= 0).all()


def test_nothing_is_reflected_into_space_a_camera_saw_through():
    vertices, triangles = half_seen_chair()

    def upper_right_is_empty(points):
        return upper_right(points)

    completed = mirrored_completion(vertices, triangles, graph_of(chair_box()), upper_right_is_empty)
    assert not upper_right(completed.vertices[completed.added]).any()


def test_reflected_surface_faces_outward_like_its_source():
    vertices, triangles = half_seen_chair()
    completed = mirrored_completion(vertices, triangles, graph_of(chair_box()), nothing_seen_through)
    normals = vertex_normals(completed.vertices, completed.triangles)
    added_upper_right = completed.added & upper_right(completed.vertices)
    assert (normals[added_upper_right][:, 0] > 0.9).all()


def test_a_side_never_scanned_at_all_is_not_invented():
    left, faces = panel(-0.4, 0.85, facing=-1.0)
    completed = mirrored_completion(left, faces, graph_of(chair_box()), nothing_seen_through)
    assert not (completed.vertices[completed.added][:, 0] > 0.0).any()


def test_reflected_vertices_take_their_source_colour():
    vertices = np.zeros((4, 3))
    triangles = np.array([[0, 1, 2], [1, 3, 2]])
    colours = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.0, 0.0, 0.0], [0.9, 0.9, 0.9]])
    scan = ColouredScan(vertices, triangles, colours, np.ones(4, dtype=bool), mirror_source=np.array([-1, -1, 0, 1]))
    painted = with_mirrored_colours(scan)
    np.testing.assert_allclose(painted.colours[2], colours[0])
    np.testing.assert_allclose(painted.colours[3], colours[1])


def test_mirror_links_survive_dropping_unused_vertices():
    vertices = np.arange(15, dtype=float).reshape(5, 3)
    triangles = np.array([[1, 2, 4], [2, 3, 4]])
    scan = ColouredScan(
        vertices, triangles, np.zeros((5, 3)), np.ones(5, dtype=bool),
        mirror_source=np.array([-1, -1, -1, 1, 2]),
    )
    kept = unused_vertices_removed(scan)
    np.testing.assert_array_equal(kept.mirror_source, [-1, -1, 0, 1])


def straight_ahead() -> PhotoCamera:
    """A camera one metre from the origin, looking at it through the middle of a ten-pixel frame."""
    room_to_camera = np.eye(4)
    room_to_camera[2, 3] = 1.0
    return PhotoCamera("ahead", room_to_camera, fx=10.0, fy=10.0, cx=5.0, cy=5.0, width=10, height=10, timestamp=0.0)


def test_one_camera_seeing_past_a_point_is_not_enough_to_call_it_empty():
    beyond = np.full((10, 10), 3.0)
    point = np.zeros((1, 3))
    assert not seen_through_by([straight_ahead()], [beyond])(point).any()
    assert seen_through_by([straight_ahead(), straight_ahead()], [beyond, beyond])(point).all()
