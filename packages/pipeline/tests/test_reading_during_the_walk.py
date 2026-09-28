"""Reading a walk's photos while it is still being walked.

Pinned here: the walk sampler keeps the same photos whether it sees a real walk
one pose at a time or all at once afterwards, a walk that has ended always gets
a detector slot before one still in progress, photos read during the walk are
answered from the cache when discovery runs, and two walks at once take turns.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest
from PIL import Image
from standardphysics_contracts import PoseRecord
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery.cache import DetectionCache
from standardphysics_pipeline.discovery.detect import (
    Detection,
    DetectionAuthError,
    DetectorSlots,
    answer_identity,
)
from standardphysics_pipeline.discovery.discover import _detect_all
from standardphysics_pipeline.discovery.live import LiveReader
from standardphysics_pipeline.discovery.walk_sampling import (
    MAX_STILL_SECONDS,
    WalkSampler,
    viewpoint_of_pose,
    worth_reading,
)
from standardphysics_pipeline.textures.camera import camera_from_pose

REPO = pathlib.Path(__file__).resolve().parents[3]
REAL_WALK = REPO / "datasets/phone/test1/poses.json"
STANDING = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def real_walk() -> list[PoseRecord]:
    """The poses of a real 238-second walk, given the image size the phone records today."""
    records = json.loads(REAL_WALK.read_text())
    return [
        PoseRecord.model_validate({
            **record, "metadata_version": 2, "frame_id": f"frame-{index:04d}",
            "image_width": 1920, "image_height": 1440, "calibration_width": 1920,
            "calibration_height": 1440, "image_orientation": "sensor",
        })
        for index, record in enumerate(records)
    ]


def pose_at(timestamp: float, frame_id: str = "frame-0000", transform=STANDING) -> PoseRecord:
    return PoseRecord(
        metadata_version=2, frame_id=frame_id, image=f"frames/{frame_id}.jpg", timestamp=timestamp,
        transform=transform, intrinsics=[1000, 0, 0, 0, 1000, 0, 960, 720, 1], orientation="portrait",
        image_width=1920, image_height=1440, calibration_width=1920, calibration_height=1440,
        image_orientation="sensor",
    )


def moved(metres: float) -> list[float]:
    return [*STANDING[:12], metres, 0.0, 0.0, 1.0]


class TestTheWalkSampler:
    def test_a_real_walk_keeps_the_same_photos_live_and_afterwards(self):
        walk = real_walk()
        sampler = WalkSampler()
        live = [pose.frame_id for pose in walk if sampler.keep(viewpoint_of_pose(pose))]
        cameras = [camera_from_pose(pose, capture_to_room(-1.2)) for pose in walk]
        afterwards = [camera.frame_id for camera in worth_reading(cameras)]
        assert live == afterwards
        assert 0.6 * len(walk) < len(live) < len(walk)

    def test_standing_still_is_read_again_only_after_a_while(self):
        sampler = WalkSampler()
        kept = [sampler.keep(viewpoint_of_pose(pose_at(0.5 * step))) for step in range(8)]
        assert kept == [step * 0.5 % MAX_STILL_SECONDS == 0 for step in range(8)]

    def test_a_step_is_read_but_not_twice_in_half_a_second(self):
        sampler = WalkSampler()
        assert sampler.keep(viewpoint_of_pose(pose_at(0.0)))
        assert not sampler.keep(viewpoint_of_pose(pose_at(0.2, transform=moved(0.3))))
        assert sampler.keep(viewpoint_of_pose(pose_at(0.5, transform=moved(0.3))))
        assert not sampler.keep(viewpoint_of_pose(pose_at(1.0, transform=moved(0.35))))


class TestDetectorSlots:
    def test_an_ended_walk_gets_the_next_slot_before_a_walk_in_progress(self):
        slots = DetectorSlots(size=1)
        slots.acquire(urgent=True)
        order: list[str] = []

        def ask(name: str, urgent: bool) -> None:
            slots.acquire(urgent)
            order.append(name)
            slots.release()

        in_progress = threading.Thread(target=ask, args=("in progress", False))
        in_progress.start()
        time.sleep(0.05)
        ended = threading.Thread(target=ask, args=("ended", True))
        ended.start()
        time.sleep(0.05)
        slots.release()
        in_progress.join(2)
        ended.join(2)
        assert order == ["ended", "in progress"]


@pytest.fixture
def photos(tmp_path) -> dict[str, pathlib.Path]:
    paths = {}
    for index in range(6):
        path = tmp_path / f"frame-{index:04d}"
        Image.new("RGB", (32, 24), (index * 40, 90, 60)).save(path, format="JPEG")
        paths[path.name] = path
    return paths


def read_through(reader: LiveReader, walk: str):
    deadline = time.monotonic() + 5
    while reader.pending(walk) and time.monotonic() < deadline:
        time.sleep(0.01)
    return reader.finish(walk)


def walked(frame_ids) -> list[PoseRecord]:
    return [pose_at(2.0 * index, frame_id, moved(0.5 * index)) for index, frame_id in enumerate(frame_ids)]


def terminal(path, frame_id, **_) -> list[Detection]:
    return [Detection(frame_id=frame_id, name="payment terminal", box=(1, 1, 9, 9), movable=True, confidence=0.9)]


class TestReadingDuringTheWalk:
    def test_discovery_asks_nothing_about_photos_read_during_the_walk(self, photos, tmp_path):
        reader = LiveReader(workers=2, read_photo=terminal)
        cache_dir = tmp_path / "detections"
        poses = walked(sorted(photos))
        for pose in poses:
            reader.offer("walk", pose, photos[pose.frame_id], cache_dir)
        report = read_through(reader, "walk")
        asked = []

        def transport(url, body, headers):
            asked.append(url)
            raise AssertionError("discovery asked about a photo read during the walk")

        cameras = [camera_from_pose(pose, capture_to_room(0.0)) for pose in poses]
        found, failures = _detect_all(
            cameras, photos, transport, DetectionCache(cache_dir, answer_identity()),
            {pose.frame_id: pose.orientation for pose in poses},
        )
        assert (report.read, report.left_for_discovery, asked, failures) == (len(photos), 0, [], [])
        assert all(one[0].name == "payment terminal" for one in found.values())

    def test_two_walks_take_turns(self, photos, tmp_path):
        release = threading.Event()
        order: list[str] = []

        def slow(path, frame_id, **_):
            release.wait(2)
            order.append(path.parent.name)
            return []

        reader = LiveReader(workers=1, read_photo=slow)
        for walk in ("first", "second"):
            (tmp_path / walk).mkdir()
            for pose in walked(sorted(photos)[:3]):
                copy = tmp_path / walk / pose.frame_id
                copy.write_bytes(photos[pose.frame_id].read_bytes())
                reader.offer(walk, pose, copy, tmp_path / walk / "detections")
        release.set()
        read_through(reader, "first")
        read_through(reader, "second")
        last_of_the_first = max(index for index, walk in enumerate(order) if walk == "first")
        assert order.count("second") == 3
        assert order.index("second") < last_of_the_first

    def test_the_end_of_a_walk_leaves_what_is_still_queued_to_discovery(self, photos, tmp_path):
        release = threading.Event()

        def held(path, frame_id, **_):
            release.wait(2)
            return []

        reader = LiveReader(workers=1, read_photo=held)
        for pose in walked(sorted(photos)):
            reader.offer("walk", pose, photos[pose.frame_id], tmp_path / "detections")
        threading.Timer(0.1, release.set).start()
        report = reader.finish("walk")
        assert (report.kept, report.read, report.left_for_discovery) == (6, 1, 5)

    def test_a_refused_key_stops_reading_instead_of_failing_every_photo(self, photos, tmp_path):
        def refused(path, frame_id, **_):
            raise DetectionAuthError("no key")

        reader = LiveReader(workers=1, read_photo=refused)
        poses = walked(sorted(photos))
        reader.offer("walk", poses[0], photos[poses[0].frame_id], tmp_path / "detections")
        read_through(reader, "walk")
        assert not reader.offer("walk", poses[1], photos[poses[1].frame_id], tmp_path / "detections")
