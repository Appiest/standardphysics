"""Patching LiDAR holes in walls and floors with the planes they lie on.

The room is a four metre floor with one wall along its far edge. The scan covers
only part of each, so where patches must go is plain geometry.
"""

from __future__ import annotations

import uuid

import numpy as np
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.textures.hole_patches import CELL, hidden_behind_objects, patches_for, with_holes_patched
from standardphysics_pipeline.textures.scan_colour import ColouredScan, vertex_normals
from standardphysics_pipeline.textures.surface_materials import room_owners

FLOOR_INDEX = 1


def node(label: str, size, at, parent: SceneNode | None = None, relation: str = "cut_into") -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind=label.lower(), label=label, raw_category=label.lower(),
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]), transform=Mat4.translation(*at),
        parent_id=parent.id if parent else None, relation=relation if parent else None,
    )


def room(*extra_nodes: SceneNode, wall: SceneNode | None = None) -> SceneGraph:
    wall = wall or node("Wall", (4.0, 0.0, 3.0), (0.0, 2.0, 1.5))
    floor = node("Floor", (4.0, 4.0, 0.0), (0.0, 0.0, 0.0))
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[wall, floor, *extra_nodes])


def scanned_grid(x_range, y_range, z: float, spacing: float = 0.03) -> np.ndarray:
    xs = np.arange(*x_range, spacing)
    ys = np.arange(*y_range, spacing)
    grid_x, grid_y = np.meshgrid(xs, ys)
    return np.stack([grid_x.ravel(), grid_y.ravel(), np.full(grid_x.size, z)], axis=1)


def left_half_of_floor() -> np.ndarray:
    return scanned_grid((-2.0, 0.0), (-2.0, 2.0), 0.0)


def floor_patch_centres(vertices, triangles) -> np.ndarray:
    corners = vertices[triangles]
    centres = corners.mean(axis=1)
    return centres[np.abs(centres[:, 2]) < 1e-6]


def test_patches_cover_the_missing_half_of_the_floor_and_nothing_scanned():
    vertices, triangles = patches_for(left_half_of_floor(), room())
    floor = floor_patch_centres(vertices, triangles)
    assert len(floor) > 0
    assert (floor[:, 0] > -0.1).all()
    missing_area = 2.0 * 4.0
    assert np.isclose(len(floor) / 2 * CELL * CELL, missing_area, rtol=0.1)


def test_a_door_cut_into_the_wall_stays_open():
    wall = node("Wall", (4.0, 0.0, 3.0), (0.0, 2.0, 1.5))
    door = node("Door", (0.9, 0.0, 2.1), (0.5, 2.0, 1.05), parent=wall)
    vertices, triangles = patches_for(left_half_of_floor(), room(door, wall=wall))
    centres = vertices[triangles].mean(axis=1)
    on_wall = centres[np.abs(centres[:, 1] - 2.0) < 1e-6]
    in_door = (np.abs(on_wall[:, 0] - 0.5) < 0.4) & (on_wall[:, 2] < 2.0)
    assert len(on_wall) > 0 and not in_door.any()


def test_a_whiteboard_attached_to_the_wall_does_not_leave_a_hole():
    wall = node("Wall", (4.0, 0.0, 3.0), (0.0, 2.0, 1.5))
    board = node("Whiteboard", (2.0, 0.0, 0.8), (0.5, 2.0, 1.5), parent=wall, relation="attached_to")
    vertices, triangles = patches_for(left_half_of_floor(), room(board, wall=wall))
    centres = vertices[triangles].mean(axis=1)
    on_wall = centres[np.abs(centres[:, 1] - 2.0) < 1e-6]
    behind_board = (np.abs(on_wall[:, 0] - 0.5) < 0.9) & (np.abs(on_wall[:, 2] - 1.5) < 0.3)
    assert behind_board.any()


def test_patches_face_into_the_room():
    patched = with_holes_patched(left_half_of_floor(), np.empty((0, 3), dtype=np.int64), room())
    normals = vertex_normals(patched.vertices, patched.triangles)[patched.inferred]
    points = patched.vertices[patched.inferred]
    on_wall_plane = np.abs(points[:, 1] - 2.0) < 1e-6
    on_floor_plane = np.abs(points[:, 2]) < 1e-6
    assert (normals[on_floor_plane & ~on_wall_plane][:, 2] > 0.99).all()
    assert (normals[on_wall_plane & ~on_floor_plane][:, 1] < -0.99).all()


def test_patches_are_marked_and_never_count_as_photographed_coverage():
    scanned = left_half_of_floor()
    patched = with_holes_patched(scanned, np.empty((0, 3), dtype=np.int64), room())
    assert not patched.inferred[: len(scanned)].any() and patched.inferred[len(scanned):].all()
    everything_seen = ColouredScan(
        patched.vertices, patched.triangles, np.zeros((len(patched.vertices), 3)),
        seen=~patched.inferred, inferred=patched.inferred,
    )
    assert everything_seen.painted_fraction == 1.0


def test_a_floor_patch_under_a_chair_is_still_floor():
    chair = node("Chair", (0.6, 0.6, 0.9), (1.0, 0.0, 0.45))
    graph = room(chair)
    patched = with_holes_patched(left_half_of_floor(), np.empty((0, 3), dtype=np.int64), graph)
    normals = vertex_normals(patched.vertices, patched.triangles)
    owners = room_owners(patched.vertices, normals, graph, patches=patched.inferred)
    under_chair = patched.inferred & (np.abs(patched.vertices[:, 0] - 1.0) < 0.25) & (np.abs(patched.vertices[:, 1]) < 0.25)
    assert under_chair.any() and (owners[under_chair] == FLOOR_INDEX).all()


def test_a_fully_scanned_room_gets_no_patches():
    floor = scanned_grid((-2.0, 2.0), (-2.0, 2.0), 0.0)
    wall = scanned_grid((-2.0, 2.0), (0.0, 3.0), 0.0)[:, [0, 2, 1]] + np.array([0.0, 2.0, 0.0])
    vertices, _ = patches_for(np.concatenate([floor, wall]), room())
    assert len(vertices) == 0


class CameraAt:
    def __init__(self, x: float, y: float, z: float):
        self.position = np.array([x, y, z])


def test_a_chair_hides_the_floor_patch_under_it_from_a_camera_above():
    chair = node("Chair", (0.6, 0.6, 0.9), (1.0, 0.0, 0.45))
    graph = room(chair)
    patched = with_holes_patched(left_half_of_floor(), np.empty((0, 3), dtype=np.int64), graph)
    hidden = hidden_behind_objects(graph, patched.vertices, patched.inferred)(CameraAt(1.0, 0.0, 2.5))
    points = patched.vertices
    under_chair = patched.inferred & (np.abs(points[:, 0] - 1.0) < 0.2) & (np.abs(points[:, 1]) < 0.2) & (points[:, 2] < 0.01)
    open_floor = patched.inferred & (np.abs(points[:, 0] - 1.0) > 0.6) & (points[:, 2] < 0.01)
    assert hidden[under_chair].all()
    assert not hidden[open_floor].any()


def test_scanned_vertices_are_never_marked_hidden():
    chair = node("Chair", (0.6, 0.6, 0.9), (-1.0, 0.0, 0.45))
    graph = room(chair)
    patched = with_holes_patched(left_half_of_floor(), np.empty((0, 3), dtype=np.int64), graph)
    hidden = hidden_behind_objects(graph, patched.vertices, patched.inferred)(CameraAt(-1.0, 0.0, 2.5))
    assert not hidden[~patched.inferred].any()


def test_a_camera_too_far_for_its_lidar_cannot_paint_a_patch():
    """Past the LiDAR's reach nothing checked the line of sight to a patch.

    On a library floor the walk never came within six metres of one corner, so
    the floor there was patched in, and photos from across the room painted it
    with the benches and the ceiling lights that really stood along those lines.
    """
    patched = with_holes_patched(left_half_of_floor(), np.empty((0, 3), dtype=np.int64), room())
    floor_patch = patched.inferred & (patched.vertices[:, 2] < 0.01)
    from_across_the_room = hidden_behind_objects(room(), patched.vertices, patched.inferred)(CameraAt(9.0, 0.0, 1.5))
    from_close_by = hidden_behind_objects(room(), patched.vertices, patched.inferred)(CameraAt(1.0, 0.0, 1.5))
    assert from_across_the_room[floor_patch].all()
    assert not from_close_by[floor_patch].any()
    assert not from_across_the_room[~patched.inferred].any()


def test_floor_running_past_the_walls_is_not_patched_outside_them():
    walls = [
        node("Wall", (4.0, 0.0, 3.0), (0.0, 2.0, 1.5)),
        node("Wall", (4.0, 0.0, 3.0), (0.0, -2.0, 1.5)),
        SceneNode(
            id=uuid.uuid4(), kind="wall", label="Wall", raw_category="wall",
            dimensions=Vec3(x=0.0, y=4.0, z=3.0), transform=Mat4.translation(2.0, 0.0, 1.5),
        ),
        SceneNode(
            id=uuid.uuid4(), kind="wall", label="Wall", raw_category="wall",
            dimensions=Vec3(x=0.0, y=4.0, z=3.0), transform=Mat4.translation(-2.0, 0.0, 1.5),
        ),
    ]
    long_floor = node("Floor", (6.0, 4.0, 0.0), (1.0, 0.0, 0.0))
    graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[*walls, long_floor])
    vertices, triangles = patches_for(left_half_of_floor(), graph)
    floor = floor_patch_centres(vertices, triangles)
    assert len(floor) > 0
    assert floor[:, 0].max() < 2.1
