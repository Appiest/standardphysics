"""Occluding a camera's view in chunks sees exactly what one pass would.

A long walk builds a mesh too large to transform into a camera's frame all at
once, and the bake used to refuse rather than allocate for it. It works through
the faces in batches now, which is only allowed to be faster, never different:
the buffer keeps the nearest of whatever it is shown, so the batch size must
not change a single texel.

Checked against rooms really scanned on this machine. A mesh written here would
be a mesh arranged to survive the test.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.lidar import load_mesh, triangles_in_arkit_world
from standardphysics_pipeline.textures import project
from standardphysics_pipeline.textures.camera import load_cameras

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _smallest_scan():
    """The least mesh with cameras, so the comparison stays quick."""
    found = []
    for artifacts in sorted(ROOT.glob("services/api/var/scans/*/artifacts")):
        mesh, poses, room = artifacts / "lidar-mesh", artifacts / "poses", artifacts / "room-json"
        if mesh.is_file() and poses.is_file() and room.is_file():
            found.append((mesh.stat().st_size, artifacts))
    return min(found)[1] if found else None


@pytest.fixture(scope="module")
def camera_and_faces():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room on this machine")
    triangles = triangles_in_arkit_world(load_mesh(artifacts / "lidar-mesh"))
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    frames = [path.name for path in sorted(artifacts.glob("frame-*"))]
    if not frames:
        pytest.skip("that scan kept no photos")
    cameras = load_cameras(artifacts / "poses", frames, graph.capture_to_room)
    return cameras[len(cameras) // 2], triangles


def _buffer_with_chunk(camera, triangles, chunk: int) -> np.ndarray:
    was = project.DEPTH_TRIANGLE_CHUNK
    project.DEPTH_TRIANGLE_CHUNK = chunk
    try:
        return project.triangle_depth_buffer(camera, triangles)
    finally:
        project.DEPTH_TRIANGLE_CHUNK = was


def test_the_batch_size_changes_nothing(camera_and_faces):
    camera, triangles = camera_and_faces
    one_pass = _buffer_with_chunk(camera, triangles, len(triangles) + 1)
    in_batches = _buffer_with_chunk(camera, triangles, 5_000)
    assert np.array_equal(one_pass, in_batches)


def test_the_camera_saw_something(camera_and_faces):
    """A buffer of nothing but infinity would pass the comparison and mean nothing."""
    camera, triangles = camera_and_faces
    assert np.isfinite(_buffer_with_chunk(camera, triangles, 5_000)).any()


def test_a_mesh_larger_than_any_old_limit_is_not_refused(camera_and_faces):
    """Four million faces is what a walk down one library floor comes to.

    The bake used to stop at two million, so three of four captures of the same
    building never got a texture at all.
    """
    camera, triangles = camera_and_faces
    repeated = np.concatenate([triangles] * max(1, 2_100_000 // max(1, len(triangles))))
    assert len(repeated) > 2_000_000
    assert np.isfinite(_buffer_with_chunk(camera, repeated, 250_000)).any()


def test_drawing_small_faces_together_matches_drawing_them_one_by_one(camera_and_faces):
    """The batched path must write the buffer the per-face loop writes, pixel for pixel, for faces of every size."""
    camera, triangles = camera_and_faces
    fast = project.triangle_depth_buffer(camera, triangles)
    was = project.SMALL_TRIANGLE_SIDES
    project.SMALL_TRIANGLE_SIDES = (0,)
    try:
        one_by_one = project.triangle_depth_buffer(camera, triangles)
    finally:
        project.SMALL_TRIANGLE_SIDES = was
    assert np.isfinite(fast).any()
    assert np.array_equal(fast, one_by_one)


def test_drawing_the_nearest_cubes_first_and_skipping_hidden_ones_draws_the_same_buffer(camera_and_faces):
    """Dozens of layers of shelving were drawn into each photo's buffer; skipping the hidden ones must change no pixel."""
    camera, triangles = camera_and_faces
    triangles = triangles.astype(np.float32)
    blocks = project.TriangleBlocks(triangles)
    everything = project.triangle_depth_buffer(camera, triangles)
    nearest_first = project.occluder_depth_buffer(camera, triangles, blocks)
    assert np.isfinite(everything).any()
    assert np.array_equal(everything, nearest_first)
