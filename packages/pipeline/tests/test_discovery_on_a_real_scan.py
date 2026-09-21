"""Discovery run against a capture from the phone, with the model's answers replayed.

Everything above this file is geometry with an answer worked out by hand. This
one takes a real 110-second walk through a real room: its LiDAR, its camera
poses, and the detections a vision model returned for its photos, recorded once
and committed so the run is repeatable and costs nothing.

What it pins is the part a synthetic room cannot: that a real mesh, real poses
and real rectangles produce objects, that the objects are the size of things
rather than of rooms, and that people who walked through the scan leave it.

Skipped when the capture is not present, because the photos in it show people.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest
from standardphysics_contracts import PoseRecord
from standardphysics_pipeline import lidar, parse_room_json
from standardphysics_pipeline.discovery.boxes import claimed_by_any
from standardphysics_pipeline.discovery.carve import FrameView, carve
from standardphysics_pipeline.discovery.detect import Detection
from standardphysics_pipeline.discovery.merge import Candidate, merge_candidates
from standardphysics_pipeline.discovery.people import without_people
from standardphysics_pipeline.textures.camera import camera_from_pose
from standardphysics_pipeline.textures.project import depth_buffer

CAPTURE = pathlib.Path(__file__).resolve().parents[3] / "datasets" / "replays" / "living-room"
FRAME_LIMIT = 40

pytestmark = pytest.mark.skipif(
    not (CAPTURE / "detections.json").is_file(),
    reason="the recorded capture is not in this checkout",
)


@pytest.fixture(scope="module")
def replay():
    graph = parse_room_json(json.loads((CAPTURE / "room.json").read_bytes()))
    detections = json.loads((CAPTURE / "detections.json").read_text())
    points = lidar.room_cloud(CAPTURE / "lidar-mesh.json", graph.capture_to_room).astype(np.float64)
    cameras = []
    for item in json.loads((CAPTURE / "poses.json").read_bytes()):
        record = PoseRecord.model_validate(item)
        if record.projectable and record.frame_id in detections:
            cameras.append(camera_from_pose(record, graph.capture_to_room))
    step = max(1, len(cameras) // FRAME_LIMIT)
    return graph, points, cameras[::step], detections


def seen_in(detections, frame_id) -> list[Detection]:
    return [
        Detection(frame_id, item["name"], tuple(item["box"]), item["movable"], item["confidence"])
        for item in detections[frame_id]
    ]


class TestTheCaptureItself:
    def test_the_scan_carries_what_discovery_needs(self, replay):
        graph, points, cameras, _ = replay
        assert graph.capture_to_room is not None
        assert len(points) > 50_000
        assert len(cameras) > 10

    def test_the_room_stands_on_the_floor(self, replay):
        """Ingest drops the floor to zero, so heights are heights above it."""
        _, points, _, _ = replay
        assert points[:, 2].min() == pytest.approx(0.0, abs=0.5)


class TestPeopleLeaveTheMesh:
    def test_somebody_walked_through_this_scan_and_is_taken_out(self, replay):
        graph, points, cameras, detections = replay
        views = [(camera, seen_in(detections, camera.frame_id), None) for camera in cameras]
        removal = without_people(points, graph, views)
        assert removal.frames_with_people > 0
        assert 0 < removal.removed < len(points) * 0.25

    def test_taking_people_out_never_takes_the_room_with_them(self, replay):
        graph, points, cameras, detections = replay
        views = [(camera, seen_in(detections, camera.frame_id), None) for camera in cameras]
        kept = without_people(points, graph, views).points
        for axis in range(3):
            assert kept[:, axis].min() == pytest.approx(points[:, axis].min(), abs=0.35)
            assert kept[:, axis].max() == pytest.approx(points[:, axis].max(), abs=0.35)


@pytest.fixture(scope="module")
def found(replay):
    """Every object carved out of the real walk, merged across the frames that saw it."""
    graph, points, cameras, detections = replay
    unclaimed = points[~claimed_by_any(points, graph)]
    candidates = []
    for camera in cameras:
        wanted = [one for one in seen_in(detections, camera.frame_id) if not one.is_person]
        if not wanted:
            continue
        view = FrameView.of(unclaimed, camera, depth_buffer(camera, points))
        for detection in wanted:
            box = carve(view, detection)
            if box is not None:
                candidates.append(Candidate(detection, box))
    return merge_candidates(candidates)


class TestObjectsComeOutOfARealRoom:
    def test_the_walk_finds_objects_roomplan_never_boxed(self, found):
        assert len(found) > 5

    def test_more_than_one_frame_agrees_on_something(self, found):
        assert max(one.views for one in found) >= 2

    def test_nothing_found_is_the_size_of_a_room(self, found):
        for one in found:
            assert max(one.box.dimensions) <= 4.0, f"{one.name} is {one.box.dimensions}"

    def test_nothing_found_is_floating_near_the_ceiling(self, found):
        assert min(one.box.floor_clearance for one in found) < 1.0

    def test_every_object_keeps_the_points_it_was_measured_from(self, found):
        for one in found:
            assert len(one.box.points) >= 25
