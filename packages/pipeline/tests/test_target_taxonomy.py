"""Target taxonomy, malformed-input rejection, correction wiring and crop evidence.

Pins the semantics lane's guarantees:
- A detector's free-text word resolves to one fixed class; look-alikes are never
  promoted (TV vs monitor vs whiteboard, outlet vs switch/sign, sofa vs table).
- Counter and restroom candidates always need owner confirmation.
- Malformed, NaN and infinite model or cache values are rejected, never coerced.
- The production discovery path runs secondary semantic corrections.
- Surface targets carry source-resolution crops whose id is stable per replay.
"""

from __future__ import annotations

import json
import math
import pathlib
import uuid

import numpy as np
import pytest
from PIL import Image
from standardphysics_contracts import (
    LidarMesh,
    LidarMeshPart,
    Mat4,
    PoseRecord,
    SceneGraph,
    SceneNode,
    Vec3,
)
from standardphysics_pipeline.coords import capture_to_room
from standardphysics_pipeline.discovery import taxonomy
from standardphysics_pipeline.discovery.cache import DetectionCache
from standardphysics_pipeline.discovery.crops import crop_box_of, crop_id_for, save_crop
from standardphysics_pipeline.discovery.detect import (
    Detection,
    EncodedFrame,
    _detections_from,
)
from standardphysics_pipeline.discovery.discover import DiscoveryInputs, discover_objects
from standardphysics_pipeline.discovery.surface_attach import attach_detection_to_surface
from standardphysics_pipeline.textures.camera import PhotoCamera


class TestTaxonomyResolution:
    def test_television_monitor_and_whiteboard_never_collapse(self):
        assert taxonomy.classify("tv") == taxonomy.TELEVISION
        assert taxonomy.classify("television set") == taxonomy.TELEVISION
        assert taxonomy.classify("flat screen") == taxonomy.TELEVISION
        assert taxonomy.classify("monitor") == taxonomy.MONITOR
        assert taxonomy.classify("computer monitor") == taxonomy.MONITOR
        assert taxonomy.classify("whiteboard") == taxonomy.WHITEBOARD
        assert taxonomy.classify("chalkboard") == taxonomy.WHITEBOARD
        assert taxonomy.classify("screen") == taxonomy.OBJECT
        assert taxonomy.classify("display") == taxonomy.OBJECT

    def test_outlet_versus_switch_and_sign(self):
        assert taxonomy.classify("outlet") == taxonomy.OUTLET
        assert taxonomy.classify("power strip") == taxonomy.OUTLET
        assert taxonomy.classify("extension lead") == taxonomy.OUTLET
        assert taxonomy.classify("light switch") == taxonomy.SWITCH
        assert taxonomy.classify("dimmer switch") == taxonomy.SWITCH
        assert taxonomy.classify("exit sign") == taxonomy.SIGN
        assert taxonomy.classify("ethernet port") == taxonomy.SIGN
        assert taxonomy.classify("usb port") == taxonomy.SIGN
        assert taxonomy.classify("fire alarm") == taxonomy.SIGN

    def test_sofa_versus_table(self):
        assert taxonomy.classify("sofa") == taxonomy.SOFA
        assert taxonomy.classify("couch") == taxonomy.SOFA
        assert taxonomy.classify("sectional") == taxonomy.SOFA
        assert taxonomy.classify("armchair") == taxonomy.SOFA
        assert taxonomy.classify("table") == taxonomy.TABLE
        assert taxonomy.classify("coffee table") == taxonomy.TABLE
        assert taxonomy.classify("desk") == taxonomy.TABLE
        assert taxonomy.classify("bench") == taxonomy.OBJECT

    def test_counter_and_restroom_need_owner_confirmation(self):
        assert taxonomy.classify("counter") == taxonomy.SERVICE_COUNTER
        assert taxonomy.classify("checkout counter") == taxonomy.SERVICE_COUNTER
        assert taxonomy.classify("restroom") == taxonomy.RESTROOM_ENTRANCE
        assert taxonomy.classify("bathroom door") == taxonomy.RESTROOM_ENTRANCE
        assert taxonomy.needs_owner_confirmation("counter")
        assert taxonomy.needs_owner_confirmation("restroom")
        assert not taxonomy.needs_owner_confirmation("outlet")
        assert not taxonomy.needs_owner_confirmation("tv")

    def test_counter_lookalikes_are_not_service_counters(self):
        assert taxonomy.classify("table") != taxonomy.SERVICE_COUNTER
        assert taxonomy.classify("desk") != taxonomy.SERVICE_COUNTER
        assert taxonomy.classify("pos terminal") == taxonomy.OBJECT
        assert taxonomy.classify("cash register") == taxonomy.OBJECT
        assert taxonomy.classify("shelf") == taxonomy.OBJECT
        assert taxonomy.classify("storage room") != taxonomy.RESTROOM_ENTRANCE
        assert taxonomy.classify("restroom sign") != taxonomy.RESTROOM_ENTRANCE

    def test_unknown_and_ambiguous_words_stay_neutral(self):
        assert taxonomy.classify("contraption") == taxonomy.OBJECT
        assert taxonomy.classify("") == taxonomy.OBJECT
        assert taxonomy.classify("screen") == taxonomy.OBJECT
        assert taxonomy.classify("projector screen") == taxonomy.OBJECT

    def test_confusers_of_specific_targets(self):
        assert taxonomy.is_confuser_of("monitor", taxonomy.TELEVISION)
        assert taxonomy.is_confuser_of("whiteboard", taxonomy.TELEVISION)
        assert taxonomy.is_confuser_of("television", taxonomy.WHITEBOARD)
        assert taxonomy.is_confuser_of("light switch", taxonomy.OUTLET)
        assert taxonomy.is_confuser_of("exit sign", taxonomy.OUTLET)
        assert taxonomy.is_confuser_of("bench", taxonomy.SOFA)
        assert taxonomy.is_confuser_of("sofa", taxonomy.TABLE)
        assert taxonomy.is_confuser_of("pos terminal", taxonomy.SERVICE_COUNTER)
        assert taxonomy.is_confuser_of("sign", taxonomy.RESTROOM_ENTRANCE)
        assert not taxonomy.is_confuser_of("outlet", taxonomy.OUTLET)


class TestDetectionClassProperties:
    def test_detection_resolves_target_classes(self):
        tv = Detection("f", "television", (1.0, 2.0, 3.0, 4.0), False, 0.9)
        assert tv.is_television
        assert tv.is_attachable_target
        assert not tv.needs_owner_confirmation

        monitor = Detection("f", "monitor", (1.0, 2.0, 3.0, 4.0), False, 0.9)
        assert not monitor.is_television
        assert not monitor.is_attachable_target

        counter = Detection("f", "counter", (1.0, 2.0, 3.0, 4.0), False, 0.9)
        assert counter.is_service_counter
        assert counter.needs_owner_confirmation
        assert not counter.is_attachable_target

        restroom = Detection("f", "restroom", (1.0, 2.0, 3.0, 4.0), False, 0.9)
        assert restroom.is_restroom_entrance
        assert restroom.needs_owner_confirmation
        assert not restroom.is_attachable_target

        switch = Detection("f", "light switch", (1.0, 2.0, 3.0, 4.0), False, 0.9)
        assert switch.is_confuser
        assert not switch.is_outlet
        assert not switch.is_attachable_target


class TestModelRequestRecording:
    def test_real_request_metadata_is_recorded_through_discovery(self, tmp_path: pathlib.Path):
        builder = TestDiscoveryWiresSecondaryCorrections()
        payload = [{"name": "outlet", "box_2d": [450, 450, 550, 550], "movable": False, "confidence": 0.95}]
        inputs, _, _, _ = builder.discovery_fixture(tmp_path, [], payload)

        def fixture_transport(url, body, headers):
            return {
                "id": "chatcmpl-test-123",
                "model": body["model"],
                "usage": {"prompt_tokens": 12, "completion_tokens": 34},
                "choices": [{"message": {"content": json.dumps({"objects": payload})}}],
            }

        result = discover_objects(inputs, transport=fixture_transport)
        assert len(result.model_requests) == 1
        request = result.model_requests[0]
        assert request.frame_id == "frame-0001"
        assert request.request_id == "chatcmpl-test-123"
        assert request.model
        assert request.usage == {"prompt_tokens": 12, "completion_tokens": 34}
        assert request.provider != ""

    def test_cached_frames_record_no_new_requests(self, tmp_path: pathlib.Path):
        builder = TestDiscoveryWiresSecondaryCorrections()
        payload = [{"name": "outlet", "box_2d": [450, 450, 550, 550], "movable": False, "confidence": 0.95}]
        inputs, _, _, _ = builder.discovery_fixture(tmp_path, [], payload)
        inputs = DiscoveryInputs(
            graph=inputs.graph,
            poses_path=inputs.poses_path,
            frame_paths=inputs.frame_paths,
            lidar_mesh_path=inputs.lidar_mesh_path,
            cache_dir=tmp_path / "cache",
        )

        calls = 0

        def fixture_transport(url, body, headers):
            nonlocal calls
            calls += 1
            return {
                "id": f"chatcmpl-{calls}",
                "choices": [{"message": {"content": json.dumps({"objects": payload})}}],
            }

        first = discover_objects(inputs, transport=fixture_transport)
        assert len(first.model_requests) == 1
        assert calls == 1

        second = discover_objects(inputs, transport=fixture_transport)
        assert calls == 1
        assert second.model_requests == []
        assert {n.id for n in second.nodes} == {n.id for n in first.nodes}


class TestMalformedInputsRejected:
    def test_nonfinite_confidence_drops_the_detection(self):
        frame = EncodedFrame(jpeg=b"", width=640, height=480, turns=0)
        payload = {
            "choices": [{"message": {"content": json.dumps({"objects": [
                {"name": "outlet", "box_2d": [100, 100, 300, 300], "movable": False, "confidence": float("nan")},
            ]})}}]
        }
        assert _detections_from(payload, frame, "frame-0001") == []

        payload_inf = {
            "choices": [{"message": {"content": json.dumps({"objects": [
                {"name": "outlet", "box_2d": [100, 100, 300, 300], "movable": False, "confidence": float("inf")},
            ]})}}]
        }
        assert _detections_from(payload_inf, frame, "frame-0001") == []

    def test_finite_confidence_is_clipped(self):
        frame = EncodedFrame(jpeg=b"", width=640, height=480, turns=0)
        payload = {
            "choices": [{"message": {"content": json.dumps({"objects": [
                {"name": "outlet", "box_2d": [100, 100, 300, 300], "movable": False, "confidence": 7.0},
            ]})}}]
        }
        found = _detections_from(payload, frame, "frame-0001")
        assert len(found) == 1
        assert found[0].confidence == pytest.approx(1.0)

    def test_malformed_cache_entry_is_a_miss_not_a_smaller_answer(self, tmp_path: pathlib.Path):
        cache = DetectionCache(tmp_path / "cache", model="test-model")
        img = Image.new("RGB", (64, 48), color=(10, 10, 10))
        img_path = tmp_path / "frame.jpg"
        img.save(img_path)

        good = Detection("frame.jpg", "outlet", (5.0, 5.0, 20.0, 20.0), False, 0.8, category="outlet")
        cache.put(img_path, [good], orientation="landscape_right")

        cached = cache.get(img_path, "frame.jpg", "landscape_right")
        assert cached is not None and len(cached) == 1

        entry = cache._entry(img_path, "landscape_right")
        entry.write_text(json.dumps([
            {"name": "outlet", "box": [5.0, 5.0, math.nan, 20.0], "movable": False, "confidence": 0.8},
            {"name": "tv", "box": [1.0, 1.0, 3.0, 3.0], "movable": False, "confidence": 0.9},
        ]))
        assert cache.get(img_path, "frame.jpg", "landscape_right") is None

        entry.write_text(json.dumps({"name": "outlet", "not": "a list"}))
        assert cache.get(img_path, "frame.jpg", "landscape_right") is None


def camera_at(position, looking_at, width=640, height=480, focal=500.0) -> PhotoCamera:
    forward = np.asarray(looking_at, dtype=float) - np.asarray(position, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return PhotoCamera(
        frame_id="frame-0001",
        room_to_camera=np.vstack([
            np.hstack([rotation, (-rotation @ np.asarray(position, dtype=float)).reshape(3, 1)]),
            [0.0, 0.0, 0.0, 1.0],
        ]),
        fx=focal, fy=focal, cx=width / 2 - 0.5, cy=height / 2 - 0.5,
        width=width, height=height, timestamp=0.0,
    )


def wall_node(centre=(0.0, 2.0, 1.2), size=(4.0, 0.1, 2.4)) -> SceneNode:
    return SceneNode(
        id=uuid.uuid4(), kind="wall", label="Wall", raw_category="wall",
        dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4(m=[
            1.0, 0.0, 0.0, centre[0],
            0.0, 1.0, 0.0, centre[1],
            0.0, 0.0, 1.0, centre[2],
            0.0, 0.0, 0.0, 1.0,
        ]),
        quality="measured", movable=False,
    )


class TestSurfaceAttachmentTargetKinds:
    def test_television_attaches_as_television(self):
        wall = wall_node()
        graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[wall], capture_to_room=capture_to_room(0.0))
        cam = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        detection = Detection("frame-0001", "television", (280.0, 200.0, 360.0, 280.0), False, 0.92, category="object")
        attachment, node = attach_detection_to_surface(
            detection, cam, graph, depth_buffer=np.full((480, 640), 1.95)
        )
        assert node.kind == "television"
        assert node.parent_id == wall.id
        assert node.relation == "mounted_on"
        assert attachment.review_status == "detected"

    def test_counter_candidate_never_confirmed_by_detector(self):
        graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[], capture_to_room=capture_to_room(0.0))
        cam = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        detection = Detection("frame-0001", "counter", (280.0, 200.0, 360.0, 280.0), False, 0.92)
        attachment, node = attach_detection_to_surface(detection, cam, graph)
        assert node.kind == "candidate_service_counter"
        assert attachment.review_status == "candidate"
        assert attachment.localization_quality == "unanchored"

    def test_restroom_entrance_candidate_never_confirmed_by_detector(self):
        graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[], capture_to_room=capture_to_room(0.0))
        cam = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        detection = Detection("frame-0001", "restroom door", (280.0, 200.0, 360.0, 280.0), False, 0.92)
        attachment, node = attach_detection_to_surface(detection, cam, graph)
        assert node.kind == "candidate_restroom_entrance"
        assert attachment.review_status == "candidate"

    def test_confuser_is_never_an_outlet(self):
        wall = wall_node()
        graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[wall], capture_to_room=capture_to_room(0.0))
        cam = camera_at((0.0, 0.0, 1.0), (0.0, 2.0, 1.0))
        detection = Detection("frame-0001", "light switch", (280.0, 200.0, 360.0, 280.0), False, 0.92)
        _, node = attach_detection_to_surface(
            detection, cam, graph, depth_buffer=np.full((480, 640), 1.95)
        )
        assert node.kind == "confuser"
        assert node.kind != "outlet"


class TestCropEvidence:
    def test_crop_id_is_stable_for_same_frame_and_box(self):
        box = (12.0, 34.0, 56.0, 78.0)
        assert crop_id_for("frame-0007", box) == crop_id_for("frame-0007", box)
        assert crop_id_for("frame-0007", box) != crop_id_for("frame-0008", box)
        assert crop_id_for("frame-0007", box) != crop_id_for("frame-0007", (13.0, 34.0, 56.0, 78.0))

    def test_crop_box_clips_to_image_and_covers_the_sensor_box(self, tmp_path: pathlib.Path):
        img = Image.new("RGB", (640, 480), color=(0, 0, 0))
        img_path = tmp_path / "frame.jpg"
        img.save(img_path)

        box = (10.0, 10.0, 80.0, 60.0)
        padded = crop_box_of(img_path, box)
        assert padded is not None
        left, top, right, bottom = padded
        assert left <= box[0] and right >= box[2] and top <= box[1] and bottom >= box[3]
        assert left >= 0.0 and top >= 0.0 and right <= 640.0 and bottom <= 480.0

        edge_box = (600.0, 20.0, 660.0, 60.0)
        clipped = crop_box_of(img_path, edge_box)
        assert clipped is not None
        assert clipped[2] == 640.0
        assert clipped[0] >= 0.0

    def test_saved_crop_decodes_and_contains_the_sensor_region(self, tmp_path: pathlib.Path):
        image = Image.new("RGB", (640, 480), color=(120, 120, 120))
        box = (200, 160, 300, 240)
        for x in range(int(box[0]), int(box[2])):
            for y in range(int(box[1]), int(box[3])):
                image.putpixel((x, y), (255, 0, 128))
        img_path = tmp_path / "frame.jpg"
        image.save(img_path)

        crop_id = save_crop(img_path, "frame-0001", box, tmp_path / "crops", padding_fraction=0.0)
        assert crop_id == crop_id_for("frame-0001", box)

        with Image.open(tmp_path / "crops" / f"{crop_id}.jpg") as crop:
            assert crop.size == (int(box[2] - box[0]), int(box[3] - box[1]))
            assert crop.getpixel((int(box[2] - box[0]) // 2, int(box[3] - box[1]) // 2)) == (255, 0, 128)

    def test_degenerate_box_produces_no_crop(self, tmp_path: pathlib.Path):
        img = Image.new("RGB", (64, 48), color=(0, 0, 0))
        img_path = tmp_path / "frame.jpg"
        img.save(img_path)
        assert save_crop(img_path, "f", (10.0, 10.0, 10.0, 20.0), tmp_path / "crops") is None
        assert crop_box_of(img_path, (10.0, 10.0, 10.0, 20.0)) is None


class TestDiscoveryWiresSecondaryCorrections:
    """The production entry point runs the correction pass and crop evidence."""

    def discovery_fixture(self, tmp_path, extra_nodes, payload_objects):
        scan_id = uuid.uuid4()
        wall = wall_node()
        graph = SceneGraph(
            scan_id=scan_id,
            nodes=[wall, *extra_nodes],
            capture_to_room=capture_to_room(0.0),
            revision=1,
        )

        xs = np.linspace(-1.0, 1.0, 51)
        ys = np.linspace(0.0, 2.0, 51)
        grid_x, grid_y = np.meshgrid(xs, ys)
        points_arkit = np.stack([grid_x.ravel(), grid_y.ravel(), np.full(grid_x.size, -1.95)], axis=1)
        vertices = [float(v) for v in points_arkit.ravel()]
        triangles = []
        for j in range(50):
            for i in range(50):
                idx0 = j * 51 + i
                triangles.extend([idx0, idx0 + 1, idx0 + 51, idx0 + 1, idx0 + 52, idx0 + 51])
        mesh = LidarMesh(parts=[
            LidarMeshPart(id=uuid.uuid4(), transform=[
                1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
            ], vertices=vertices, triangles=triangles)
        ])
        mesh_path = tmp_path / "lidar-mesh.json"
        mesh_path.write_text(mesh.model_dump_json())

        pose = PoseRecord(
            metadata_version=2,
            frame_id="frame-0001",
            image="frame-0001.jpg",
            timestamp=100.0,
            transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1.2, 0, 1],
            intrinsics=[500.0, 0.0, 0.0, 0.0, 500.0, 0.0, 320.0, 240.0, 1.0],
            orientation="landscape_right",
            image_width=640,
            image_height=480,
            calibration_width=640,
            calibration_height=480,
            image_orientation="sensor",
        )
        poses_path = tmp_path / "poses.json"
        poses_path.write_text(json.dumps([pose.model_dump(mode="json")]))

        img = Image.new("RGB", (640, 480), color=(180, 180, 180))
        img_path = tmp_path / "frame-0001.jpg"
        img.save(img_path)

        def fixture_transport(url, body, headers):
            return {"choices": [{"message": {"content": json.dumps({"objects": payload_objects})}}]}

        inputs = DiscoveryInputs(
            graph=graph,
            poses_path=poses_path,
            frame_paths={"frame-0001": img_path},
            lidar_mesh_path=mesh_path,
            crop_dir=tmp_path / "crops",
        )
        return inputs, graph, img_path, fixture_transport

    def test_sofa_relabel_survives_the_full_discovery_path(self, tmp_path):
        sofa = SceneNode(
            id=uuid.uuid4(), kind="storage", label="Storage", raw_category="storage",
            dimensions=Vec3(x=1.5, y=0.8, z=0.5),
            transform=Mat4(m=[
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 1.0,
                0.0, 0.0, 1.0, 1.2,
                0.0, 0.0, 0.0, 1.0,
            ]),
            quality="measured", movable=True,
        )
        payload = [{"name": "sofa", "box_2d": [0, 0, 1000, 1000], "movable": True, "confidence": 0.9}]
        inputs, _, _, transport = self.discovery_fixture(tmp_path, [sofa], payload)

        result = discover_objects(inputs, transport=transport)

        changed = {node.id: node for node in result.nodes}
        assert sofa.id in changed
        assert changed[sofa.id].label == "Sofa"
        assert changed[sofa.id].raw_category == "storage"
        assert changed[sofa.id].dimensions == sofa.dimensions

    def test_whiteboard_attached_through_the_full_discovery_path(self, tmp_path):
        payload = [{"name": "whiteboard", "box_2d": [300, 300, 700, 600], "movable": False, "confidence": 0.8}]
        inputs, graph, _, transport = self.discovery_fixture(tmp_path, [], payload)

        result = discover_objects(inputs, transport=transport)

        boards = [n for n in result.nodes if n.kind == "whiteboard"]
        assert len(boards) == 1
        wall = graph.nodes[0]
        assert boards[0].parent_id == wall.id
        assert boards[0].attachment is not None
        assert boards[0].attachment.observations[0].frame_id == "frame-0001"

    def test_every_affirmed_whiteboard_has_an_observation(self, tmp_path):
        payload = [{"name": "whiteboard", "box_2d": [300, 300, 700, 600], "movable": False, "confidence": 0.8}]
        inputs, _, _, transport = self.discovery_fixture(tmp_path, [], payload)
        result = discover_objects(inputs, transport=transport)
        boards = [n for n in result.nodes if n.kind == "whiteboard"]
        assert len(boards) == 1
        for board in boards:
            assert board.attachment is not None
            assert board.attachment.identity_confidence > 0
            assert board.attachment.observations

    def test_outlet_crop_evidence_and_image_url_flow(self, tmp_path):
        payload = [
            {"name": "outlet", "box_2d": [450, 450, 550, 550], "movable": False, "confidence": 0.95},
        ]
        inputs, _, img_path, transport = self.discovery_fixture(tmp_path, [], payload)

        result = discover_objects(inputs, transport=transport)

        outlets = [n for n in result.nodes if n.kind == "outlet"]
        assert len(outlets) == 1
        attachment = outlets[0].attachment
        assert attachment is not None
        image_url = attachment.observations[0].image_url
        assert image_url is not None

        stored = tmp_path / "crops" / f"{image_url}.jpg"
        assert stored.is_file()

        with Image.open(stored) as crop:
            assert crop is not None

        source = Image.open(img_path)
        box = outlets[0].attachment.observations[0].sensor_box
        source_crop = source.crop(tuple(int(round(v)) for v in box))
        assert source_crop.size[0] >= 1
        source.close()

        replay = discover_objects(inputs, transport=transport)
        replay_outlets = [n for n in replay.nodes if n.kind == "outlet"]
        assert len(replay_outlets) == 1
        assert replay_outlets[0].id == outlets[0].id
        assert replay_outlets[0].attachment.observations[0].image_url == image_url
        crop_files = list((tmp_path / "crops").glob("*.jpg"))
        assert len(crop_files) == 1

        replays = discover_objects(inputs, transport=transport)
        assert {n.id for n in replays.nodes} == {n.id for n in result.nodes}
