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
def vertices_and_cameras():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room with photos on this machine")
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    vertices, _ = scan_geometry(artifacts / "lidar-mesh", graph.capture_to_room)
    frames = [path.name for path in sorted(artifacts.glob("frame-*"))]
    cameras = load_cameras(artifacts / "poses", frames, graph.capture_to_room)
    step = max(1, len(cameras) // CAMERAS_COMPARED)
    return vertices, cameras[::step]


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
