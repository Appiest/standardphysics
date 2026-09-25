"""The quicker box bake chooses, sizes and occludes exactly as the slower one did.

The box bake of a library floor ranks hundreds of photos, measures the chosen
ones and draws a depth buffer of millions of LiDAR faces for each. Each of
those is now done with less work, which is only allowed to be faster, never
different, so these compare them with the old way on a room really scanned on
this machine.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.lidar import load_mesh, triangles_in_arkit_world
from standardphysics_pipeline.textures import bake
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.project import TriangleBlocks, triangle_depth_buffer

ROOT = pathlib.Path(__file__).resolve().parents[3]
CAMERAS_COMPARED = 20


def _smallest_scan():
    found = []
    for artifacts in sorted(ROOT.glob("services/api/var/scans/*/artifacts")):
        mesh, poses, room = artifacts / "lidar-mesh", artifacts / "poses", artifacts / "room-json"
        if mesh.is_file() and poses.is_file() and room.is_file() and any(artifacts.glob("frame-*")):
            found.append((mesh.stat().st_size, artifacts))
    return min(found)[1] if found else None


@pytest.fixture(scope="module")
def scan():
    artifacts = _smallest_scan()
    if artifacts is None:
        pytest.skip("no scanned room with photos on this machine")
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    paths = {path.name: path for path in sorted(artifacts.glob("frame-*"))}
    cameras = [camera for camera in load_cameras(artifacts / "poses", list(paths), graph.capture_to_room) if camera.frame_id in paths]
    matrix = np.asarray(graph.capture_to_room.m, dtype=np.float32).reshape(4, 4)
    triangles = triangles_in_arkit_world(load_mesh(artifacts / "lidar-mesh"))
    return cameras, paths, triangles @ matrix[:3, :3].T + matrix[:3, 3]


def _every_pair_ranking(cameras, scores, limit):
    """The ranking as it was, comparing every candidate with every pick each round."""
    chosen, remaining = [], list(zip(cameras, scores))
    while remaining and len(chosen) < limit:
        def value(candidate):
            camera, score = candidate
            if not chosen:
                return score
            positions = [np.linalg.norm(camera.position - prior.position) for prior, _ in chosen]
            directions = [1.0 - float(np.dot(camera.forward, prior.forward)) for prior, _ in chosen]
            novelty = min(max(position / 1.0, direction) for position, direction in zip(positions, directions))
            return score * (0.35 + min(novelty, 1.0))

        best = max(remaining, key=value)
        chosen.append(best)
        remaining.remove(best)
    return [camera.frame_id for camera, _ in chosen]


def test_the_running_novelty_picks_the_views_every_pair_comparison_picked(scan):
    cameras, paths, _ = scan
    scores = [bake._view_score(camera, paths[camera.frame_id]) for camera in cameras]
    limit = max(2, len(cameras) // 3)
    picked = [cameras[index].frame_id for index in bake._most_novel(cameras, scores, limit)]
    assert len(picked) == limit
    assert picked == _every_pair_ranking(cameras, scores, limit)


def test_the_header_gives_the_size_decoding_gives(scan):
    cameras, paths, _ = scan
    for camera in cameras[:: max(1, len(cameras) // CAMERAS_COMPARED)]:
        height, width = bake._decoded(camera, paths[camera.frame_id]).shape[:2]
        assert bake._decoded_size(camera, paths[camera.frame_id]) == (width, height)


def test_a_face_depth_buffer_from_the_framed_cubes_is_the_whole_meshs_buffer(scan):
    cameras, _, triangles = scan
    blocks = TriangleBlocks(triangles)
    culled_away = []
    for camera in cameras[:: max(1, len(cameras) // CAMERAS_COMPARED)]:
        kept = blocks.seen_by(camera)
        culled_away.append(1 - len(kept) / len(triangles))
        whole = triangle_depth_buffer(camera, triangles)
        assert np.isfinite(whole).any()
        assert np.array_equal(whole, triangle_depth_buffer(camera, triangles[kept])), camera.frame_id
    assert max(culled_away) > 0
