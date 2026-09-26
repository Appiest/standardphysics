"""The faster geometry steps of a texture build give exactly the answers the slower ones gave.

A merged library floor spent minutes finding hole rims edge by edge in Python
and voting on people one photo at a time. The rims are now found with whole
arrays and the photos vote side by side, which is only allowed to be faster,
never different. Checked on a room really scanned on this machine, with the
people discovery really found in its photos.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_pipeline.discovery.boxes import structure_points
from standardphysics_pipeline.discovery.cache import _detection
from standardphysics_pipeline.discovery.people import (
    MIN_PERSON_SHARE,
    MIN_PERSON_VIEWS,
    _in_a_person,
    _visible,
    mostly_people,
)
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.textures.camera import load_cameras
from standardphysics_pipeline.textures.object_holes import MAX_RIM_VERTICES, _rims
from standardphysics_pipeline.textures.project import PointBlocks, depth_buffer, evenly_spread
from standardphysics_pipeline.textures.scan_colour import scan_geometry

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _smallest_scan_with_people():
    found = []
    for scan in sorted(ROOT.glob("services/api/var/scans/*")):
        artifacts, detections = scan / "artifacts", scan / "detections"
        needed = [artifacts / "lidar-mesh", artifacts / "poses", artifacts / "room-json"]
        if all(path.is_file() for path in needed) and any(artifacts.glob("frame-*")) and any(detections.glob("*.json")):
            found.append(((artifacts / "lidar-mesh").stat().st_size, scan))
    return min(found)[1] if found else None


def _people_by_frame(detections_dir: pathlib.Path) -> dict[str, list]:
    people: dict[str, list] = {}
    for entry in sorted(detections_dir.glob("*.json")):
        for item in json.loads(entry.read_text()):
            detection = _detection(item, item["frame_id"])
            if detection is not None:
                people.setdefault(detection.frame_id, []).append(detection)
    return people


@pytest.fixture(scope="module")
def scan():
    found = _smallest_scan_with_people()
    if found is None:
        pytest.skip("no scanned room with photos and discovery results on this machine")
    artifacts = found / "artifacts"
    graph = parse_room_json(json.loads((artifacts / "room-json").read_text()))
    vertices, triangles = scan_geometry(artifacts / "lidar-mesh", graph.capture_to_room)
    frames = [path.name for path in sorted(artifacts.glob("frame-*"))]
    cameras = load_cameras(artifacts / "poses", frames, graph.capture_to_room)
    return vertices, triangles, graph, cameras, _people_by_frame(found / "detections")


def _rims_edge_by_edge(triangles: np.ndarray) -> list[list[int]]:
    directed = triangles[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    owned = {(int(a), int(b)) for a, b in directed}
    following: dict[int, int] = {}
    for a, b in directed:
        a, b = int(a), int(b)
        if (b, a) not in owned and b not in following:
            following[b] = a
    rims, visited = [], set()
    for start in following:
        if start in visited:
            continue
        rim, current = [], start
        while current not in visited and current in following and len(rim) <= MAX_RIM_VERTICES:
            visited.add(current)
            rim.append(current)
            current = following[current]
        if current == start and len(rim) >= 3:
            rims.append(rim)
    return rims


def test_rims_found_with_whole_arrays_are_the_rims_found_edge_by_edge(scan):
    _, triangles, _, _, _ = scan
    expected = _rims_edge_by_edge(triangles)
    assert expected, "a scan with no holes would pass the comparison and prove nothing"
    assert _rims(triangles) == expected


def _people_one_photo_at_a_time(vertices, graph, cameras, people):
    blocks = PointBlocks(vertices)
    seen_count = np.zeros(len(vertices), dtype=np.int32)
    person_count = np.zeros(len(vertices), dtype=np.int32)
    for camera in cameras:
        indices = blocks.seen_by(camera)
        near = vertices[indices]
        buffer = depth_buffer(camera, near)
        seen_count[indices] += _visible(near, camera, buffer)
        person_count[indices] += _in_a_person(near, camera, people.get(camera.frame_id, []), buffer)
    share = person_count / np.maximum(seen_count, 1)
    voted = (share >= MIN_PERSON_SHARE) & (person_count >= MIN_PERSON_VIEWS)
    return voted & ~structure_points(vertices, graph)


def test_photos_voting_side_by_side_find_the_people_one_at_a_time_found(scan):
    vertices, _, graph, cameras, people = scan
    framed = [camera for camera in cameras if any(d.is_person for d in people.get(camera.frame_id, []))]
    assert framed, "photos with nobody in them would pass the comparison and prove nothing"
    blocks = PointBlocks(vertices)
    views = [(camera, people.get(camera.frame_id, []), None) for camera in cameras]
    together = mostly_people(vertices, graph, views, visible_to=blocks.seen_by, depth_buffer_of=depth_buffer)
    assert np.array_equal(together, _people_one_photo_at_a_time(vertices, graph, cameras, people))


def test_spreading_photos_keeps_the_ends_and_the_count(scan):
    _, _, _, cameras, _ = scan
    limit = max(2, len(cameras) // 4)
    spread = evenly_spread(cameras, limit)
    assert len(spread) == limit
    assert spread[0] is cameras[0] and spread[-1] is cameras[-1]
    assert evenly_spread(cameras, len(cameras)) is cameras
