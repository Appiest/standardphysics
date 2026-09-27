"""Asking each photo only about the part of the scan in its frame gives the answers asking about all of it gave.

A merged library floor has thousands of photos, and projecting the whole floor
through every one of them was most of a texture build's time on a small server.
Culling by the cubes a frame reaches is only allowed to be faster, never
different, so these compare it with the whole-scan answer on a room really
scanned on this machine.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.textures import symmetry
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.project import PointBlocks, depth_buffer
from standardphysics_pipeline.textures.scan_colour import scan_geometry

ROOT = pathlib.Path(__file__).resolve().parents[3]
CAMERAS_COMPARED = 40


def _smallest_scan():
    found = []
    for artifacts in sorted(ROOT.glob("services/api/var/scans/*/artifacts")):
        mesh, poses, room = artifacts / "lidar-mesh", artifacts / "poses", artifacts / "room-json"
        if mesh.is_file() and poses.is_file() and room.is_file() and any(artifacts.glob("frame-*")):
            found.append((mesh.stat().st_size, artifacts))
    return min(found)[1] if found else None


@pytest.fixture(scope="module")
def vertices_and_cameras(scanned):
    vertices, _, cameras = scanned
    return vertices, cameras


@pytest.fixture(scope="module")
def scanned():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room with photos on this machine")
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    vertices, triangles = scan_geometry(artifacts / "lidar-mesh", graph.capture_to_room)
    frames = [path.name for path in sorted(artifacts.glob("frame-*"))]
    cameras = load_cameras(artifacts / "poses", frames, graph.capture_to_room)
    step = max(1, len(cameras) // CAMERAS_COMPARED)
    return vertices, triangles, cameras[::step]


def test_a_depth_buffer_from_the_framed_cubes_is_the_whole_scans_buffer(vertices_and_cameras):
    vertices, cameras = vertices_and_cameras
    blocks = PointBlocks(vertices)
    for camera in cameras:
        whole = depth_buffer(camera, vertices)
        culled = depth_buffer(camera, vertices[blocks.seen_by(camera)])
        assert np.array_equal(whole, culled), camera.frame_id


def test_the_photos_really_frame_only_part_of_the_scan(vertices_and_cameras):
    """Culling that kept every point would pass the comparison and save nothing."""
    vertices, cameras = vertices_and_cameras
    blocks = PointBlocks(vertices)
    shares = [len(blocks.seen_by(camera)) / len(vertices) for camera in cameras]
    assert np.isfinite(depth_buffer(cameras[0], vertices)).any()
    assert np.mean(shares) < 0.9


def test_seen_through_asks_only_the_cameras_that_frame_the_points_and_agrees(vertices_and_cameras):
    vertices, cameras = vertices_and_cameras
    buffers = [depth_buffer(camera, vertices) for camera in cameras]
    culled = symmetry.seen_through_by(cameras, buffers, min_views=1)
    for start in range(0, len(vertices), max(1, len(vertices) // 25)):
        region = vertices[start:start + 400]
        every_camera = sum(symmetry._seen_beyond(region, camera, buffer) for camera, buffer in zip(cameras, buffers))
        assert np.array_equal(culled(region), every_camera >= 1)


def _sorted_depth_buffer(camera, points):
    """The depth buffer as it was built before: every point written in far-to-near order, so the nearest lands last."""
    from standardphysics_pipeline.textures import project

    small = camera.resized(max(1, camera.width // project.DEPTH_BUFFER_DIVISOR), max(1, camera.height // project.DEPTH_BUFFER_DIVISOR))
    u, v, depth = small.project(points)
    columns, rows = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
    valid = (depth > project.NEAR_LIMIT) & (columns >= 0) & (columns < small.width) & (rows >= 0) & (rows < small.height)
    buffer = np.full(small.width * small.height, np.inf, dtype=np.float32)
    order = np.argsort(-depth[valid])
    buffer[(rows[valid] * small.width + columns[valid])[order]] = depth[valid][order]
    return project._erode(buffer.reshape(small.height, small.width))


def test_keeping_the_nearest_depth_per_pixel_matches_writing_every_point_far_to_near(vertices_and_cameras):
    """Sorting a photo's millions of texels by depth was a quarter of a second per photo, eight hundred photos a pass."""
    vertices, cameras = vertices_and_cameras
    for camera in cameras:
        assert np.array_equal(depth_buffer(camera, vertices), _sorted_depth_buffer(camera, vertices)), camera.frame_id


def _weights_of_every_point(camera, vertices, normals, buffer, slope_aware):
    """View weights as they were computed before: every point through every step, framed or not."""
    from standardphysics_pipeline.textures import scan_colour as sc

    columns, rows, depth = camera.project(vertices)
    toward = camera.position[None, :] - vertices
    distance = np.linalg.norm(toward, axis=1)
    facing = np.einsum("ij,ij->i", normals, toward) / np.maximum(distance, 1e-9)
    inside = (depth > 0.2) & (facing > sc.MIN_FACING) & (columns >= 0) & (columns <= camera.width - 1) & (rows >= 0) & (rows <= camera.height - 1)
    height, width = buffer.shape
    nearest = buffer[np.clip(np.rint(rows * height / camera.height).astype(np.int64), 0, height - 1),
                     np.clip(np.rint(columns * width / camera.width).astype(np.int64), 0, width - 1)]
    unhidden = ~np.isfinite(nearest) | (depth <= nearest + sc._seen_tolerance(camera, width, depth, facing, slope_aware))
    border = np.clip(np.minimum.reduce([columns, rows, camera.width - 1 - columns, camera.height - 1 - rows]) / sc.BORDER_FALLOFF_PIXELS, 0.0, 1.0)
    return np.where(inside & unhidden, facing ** 2 / np.maximum(distance, 0.5) * border, 0.0)


def test_weighing_only_the_framed_points_gives_every_point_the_weight_it_had(scanned):
    from standardphysics_pipeline.textures.scan_colour import _weights_from
    from standardphysics_pipeline.textures.surface_materials import vertex_normals

    vertices, triangles, cameras = scanned
    normals = vertex_normals(vertices, triangles)
    for camera in cameras:
        buffer = depth_buffer(camera, vertices)
        for slope_aware in (False, True):
            weight, _, _ = _weights_from(camera, vertices, normals, buffer, slope_aware=slope_aware)
            reference = _weights_of_every_point(camera, vertices, normals, buffer, slope_aware)
            assert (weight > 0).any() or not (reference > 0).any()
            assert np.array_equal(weight, reference), camera.frame_id


@pytest.fixture(scope="module")
def room_graph():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room with photos on this machine")
    return parse_room_json(json.loads((artifacts / "room-json").read_text()))


def test_testing_only_the_boxes_near_the_sight_lines_blocks_what_testing_every_box_blocks(scanned, room_graph):
    """Hundreds of object boxes on a library floor, each tested against every sight line, were a third of a photo bake."""
    from standardphysics_pipeline.textures.hole_patches import ObjectBoxes, _segments_enter_box

    vertices, _, cameras = scanned
    boxes = ObjectBoxes(room_graph)
    assert boxes.nodes
    blocked_somewhere = 0
    for camera in cameras:
        every_box = np.zeros(len(vertices), dtype=bool)
        for node in boxes.nodes:
            every_box |= _segments_enter_box(camera.position, vertices, node)
        assert np.array_equal(boxes.blocking(camera.position, vertices), every_box), camera.frame_id
        blocked_somewhere += int(every_box.any())
    assert blocked_somewhere


def test_a_cube_found_wholly_behind_the_depth_buffer_holds_only_points_that_weigh_nothing(scanned):
    """Skipping hidden cubes must never skip a point a photo would have painted."""
    from standardphysics_pipeline.textures.project import PointBlocks
    from standardphysics_pipeline.textures.scan_atlas import unhidden_members
    from standardphysics_pipeline.textures.scan_colour import _weights_from
    from standardphysics_pipeline.textures.surface_materials import vertex_normals

    vertices, triangles, cameras = scanned
    normals = vertex_normals(vertices, triangles)
    blocks = PointBlocks(vertices)
    skipped_somewhere = 0
    for camera in cameras:
        buffer = depth_buffer(camera, vertices[blocks.seen_by(camera)])
        kept = np.zeros(len(vertices), dtype=bool)
        kept[unhidden_members(blocks, camera, buffer)] = True
        weight, _, _ = _weights_from(camera, vertices, normals, buffer, slope_aware=True)
        assert not (weight[~kept] > 0).any(), camera.frame_id
        skipped_somewhere += int((~kept).sum())
    assert skipped_somewhere


def test_asking_each_object_about_every_rim_at_once_finds_the_rims_asking_one_by_one_found(scanned, room_graph):
    from standardphysics_contracts import bounds_the_room
    from standardphysics_pipeline.textures import object_holes

    vertices, triangles, _ = scanned
    objects = [node for node in room_graph.nodes if not bounds_the_room(node)]
    rims = [rim for rim in object_holes._rims(triangles) if object_holes._perimeter(vertices[rim]) <= object_holes.MAX_RIM_METRES]
    centres = np.array([vertices[rim].mean(axis=0) for rim in rims])

    def one_by_one(point):
        for node in objects:
            matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
            half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
            if np.all(np.abs((point - matrix[:3, 3]) @ matrix[:3, :3]) <= half + object_holes.OBJECT_REACH):
                return True
        return False

    expected = np.array([one_by_one(centre) for centre in centres])
    assert expected.any()
    assert np.array_equal(object_holes._inside_objects(centres, objects), expected)
