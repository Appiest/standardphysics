"""Patching the holes a real phone scan left in its ceiling and across its table tops.

The capture is datasets/phone/test1, a classroom whose ceiling sits more than
four metres up. The LiDAR reached that ceiling in stretches, and the tables it
did see are pocked with gaps where the phone measured nothing, while some of
the chairs were never seen from above at all. Where patches belong is decided
by the scan itself, so the checks below read the scan rather than a hand-built
room.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from scipy.spatial import cKDTree
from standardphysics_contracts import SceneNode, bounds_the_room, measured_as, stands_upright
from standardphysics_pipeline import parse_room_json
from standardphysics_pipeline.textures.hole_patches import HOLE_DISTANCE, with_holes_patched
from standardphysics_pipeline.textures.scan_colour import scan_geometry, vertex_normals

SCAN = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "phone" / "test1"
ABOVE_THE_FLOOR = 0.3
TOP_BAND = 0.25
FACING = 0.99
UNSEEN_TOP_VERTICES = 10


@pytest.fixture(scope="module")
def classroom():
    graph = parse_room_json(json.loads((SCAN / "room.json").read_bytes()))
    vertices, triangles = scan_geometry(SCAN / "lidar-mesh.json", graph.capture_to_room)
    return graph, vertices, triangles, with_holes_patched(vertices, triangles, graph)


def patch_faces(patched) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Centre, unit normal and area of every triangle made only of patch vertices."""
    faces = patched.triangles[patched.inferred[patched.triangles].all(axis=1)]
    corners = patched.vertices[faces]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(cross, axis=1)
    return corners.mean(axis=1), cross / np.maximum(length, 1e-12)[:, None], length / 2


def wall_tops(graph) -> np.ndarray:
    return np.array([
        np.asarray(node.transform.m).reshape(4, 4)[2, 3] + measured_as(node).z / 2
        for node in graph.nodes if stands_upright(node) and node.parent_id is None
    ])


def in_footprint_near_top(node: SceneNode, points: np.ndarray) -> np.ndarray:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    upright = int(np.argmax(np.abs(matrix[2, :3])))
    flat = [axis for axis in range(3) if axis != upright]
    local = (points - matrix[:3, 3]) @ matrix[:3, :3]
    top = matrix[2, 3] + half[upright]
    return np.all(np.abs(local[:, flat]) <= half[flat], axis=1) & (np.abs(points[:, 2] - top) <= TOP_BAND)


def test_the_lid_closes_the_ceiling_the_scanner_missed(classroom):
    graph, vertices, _, patched = classroom
    centres, normals, areas = patch_faces(patched)
    lid = normals[:, 2] < -FACING
    assert areas[lid].sum() > 2.0
    assert centres[lid, 2].min() >= wall_tops(graph).min() - 0.5
    assert centres[lid, 2].max() <= vertices[:, 2].max()


def test_the_lid_never_lies_over_measured_ceiling(classroom):
    _, vertices, _, patched = classroom
    centres, normals, _ = patch_faces(patched)
    lid = centres[normals[:, 2] < -FACING]
    assert len(lid) and (cKDTree(vertices).query(lid)[0] > HOLE_DISTANCE / 2).all()


def test_measured_table_tops_gain_patches_only_on_solids(classroom):
    graph, _, _, patched = classroom
    centres, normals, areas = patch_faces(patched)
    on_tops = (normals[:, 2] > FACING) & (centres[:, 2] > ABOVE_THE_FLOOR)
    assert areas[on_tops].sum() > 0.2
    solids = [node for node in graph.nodes if not bounds_the_room(node)]
    on_some_solid = np.zeros(int(on_tops.sum()), dtype=bool)
    for node in solids:
        on_some_solid |= in_footprint_near_top(node, centres[on_tops])
    assert on_some_solid.all()


def test_a_top_the_scanner_never_saw_gets_no_patches(classroom):
    graph, vertices, triangles, patched = classroom
    facing_up = vertex_normals(vertices, triangles)[:, 2] > 0.9
    centres, normals, _ = patch_faces(patched)
    top_patches = centres[(normals[:, 2] > FACING) & (centres[:, 2] > ABOVE_THE_FLOOR)]
    solids = [node for node in graph.nodes if not bounds_the_room(node)]
    unseen = [node for node in solids if (in_footprint_near_top(node, vertices) & facing_up).sum() < UNSEEN_TOP_VERTICES]
    assert unseen
    for node in unseen:
        assert not in_footprint_near_top(node, top_patches).any()


def test_no_wall_patch_hides_surface_the_scan_saw_behind_the_wall_plane(classroom):
    """Where the real wall sits back from RoomPlan's flat box, the scan's own surface shows, not a patch in front of it."""
    graph, vertices, triangles, patched = classroom
    centres, normals, _ = patch_faces(patched)
    upright = np.abs(normals[:, 2]) < 0.2
    facing_room = vertex_normals(vertices, triangles)
    tree = cKDTree(vertices)
    covered = 0
    for centre, normal in zip(centres[upright], normals[upright]):
        behind = [centre - normal * depth for depth in (0.15, 0.3, 0.5)]
        for point in behind:
            near = tree.query_ball_point(point, 0.02)
            if near and (facing_room[near] @ normal > 0.8).any():
                covered += 1
                break
    assert covered == 0
