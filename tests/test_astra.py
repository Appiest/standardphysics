"""Astra can annotate measured geometry, but cannot rewrite it."""

import base64
import io
import json
import time
import uuid

import numpy as np
import pytest
import standardphysics_pipeline.astra as astra
from PIL import Image
from standardphysics_pipeline.astra import (
    MAX_IMAGE_BYTES,
    MAX_OUTPUT_TOKENS,
    LabelPatch,
    _chat_body,
    _PoseEvidence,
    _project_score,
    apply_patches,
    reconstruct,
    reconstruct_result,
    select_keyframes,
)
from standardphysics_pipeline.ingest import parse_room_json


def column_major(x: float, y: float, z: float) -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def element(category, confidence="high", dims=(1.0, 2.0, 0.5), at=(0.0, 0.0, 0.0)):
    return {
        "identifier": str(uuid.uuid4()), "dimensions": list(dims),
        "transform": column_major(*at), "category": category, "confidence": confidence,
    }


def shop_payload():
    return {
        "floors": [element("floor", dims=(8.0, 0.02, 6.0))],
        "walls": [element("wall", dims=(8.0, 2.4, 0.15), at=(0.0, 1.2, -3.0))],
        "doors": [element("door", dims=(1.0, 2.1, 0.08), at=(-3.0, 1.05, 0.0))],
        "objects": [
            element("storage", dims=(3.2, 1.1, 0.7), at=(0.0, 0.55, -2.65)),
            element("chair", dims=(0.5, 0.9, 0.5), at=(1.5, 0.45, 1.0)),
            element("storage", dims=(1.2, 0.9, 0.5), at=(-2.0, 0.45, 1.2)),
        ],
    }


def model_response(*patches):
    return {"choices": [{"message": {"content": '{"nodes":[' + ",".join(patches) + "]}"}}]}


def patch_for(node, label="Display case", movable=True, quality="measured", appearance=None):
    raw = '{"id":"%s","label":"%s","movable":%s,"quality":"%s"' % (
        node.id, label, str(movable).lower(), quality,
    )
    if appearance is not None:
        raw += ',"appearance":' + json.dumps(appearance, separators=(",", ":"))
    return raw + "}"


def reconstruction_for(node, frame_id="frame-0000", *, center=(0, 0, 0), size=(0.9, 0.9, 0.1)):
    return {
        "summary": "A thin rounded tabletop on metal legs.",
        "confidence": 0.82,
        "evidence_frame_ids": [frame_id],
        "parts": [{
            "name": "top", "primitive": "box", "center": list(center), "size": list(size),
            "axis": "z", "bevel": 0.04, "base_color": "#8d6847", "material": "wood",
        }],
    }


def patch_with_reconstruction(node, reconstruction):
    return patch_for(node)[:-1] + ',"reconstruction":' + json.dumps(reconstruction, separators=(",", ":")) + "}"


def calibrated_pose(index=0, camera_x=0.0):
    return {
        "metadata_version": 2,
        "frame_id": f"frame-{index:04d}",
        "image": f"frames/frame_{index:04d}.jpg",
        "timestamp": float(index),
        "transform": column_major(camera_x, 0.0, 0.0),
        "intrinsics": [50, 0, 50, 0, 50, 40, 50, 40, 1],
        "orientation": "portrait",
        "image_width": 100,
        "image_height": 80,
        "calibration_width": 100,
        "calibration_height": 80,
        "image_orientation": "sensor",
    }


def test_local_rebuild_keeps_roomplan_provenance_and_measured_identity():
    graph = parse_room_json(shop_payload())
    before = {node.id: (node.dimensions, node.transform) for node in graph.nodes}

    rebuilt = reconstruct(graph)

    counter = next(node for node in rebuilt.nodes if node.label == "Ordering counter")
    assert counter.labeled_by == "roomplan"
    assert not counter.movable
    assert {node.id: (node.dimensions, node.transform) for node in rebuilt.nodes} == before
    assert reconstruct_result(graph).source == "roomplan"


def test_complete_model_response_marks_only_model_labeled_objects_as_astra():
    graph = parse_room_json({"objects": [element("storage"), element("chair")]})
    objects = [node for node in graph.nodes if node.kind == "object"]

    result = reconstruct_result(graph, transport=lambda *_: model_response(
        patch_for(objects[0], "Display case"), patch_for(objects[1], "Chair"),
    ))

    assert result.used_model
    assert [node.labeled_by for node in result.graph.nodes] == ["astra", "astra"]


def test_partial_or_malformed_model_response_falls_back_without_partial_mutation():
    graph = parse_room_json(shop_payload())
    objects = [node for node in graph.nodes if node.kind == "object"]

    partial = reconstruct_result(graph, transport=lambda *_: model_response(patch_for(objects[0], "Wrong partial label")))
    malformed = reconstruct_result(graph, transport=lambda *_: {"choices": [{"message": {"content": "not json"}}]})

    assert partial.source == "roomplan"
    assert all(node.label != "Wrong partial label" for node in partial.graph.nodes)
    assert malformed.source == "roomplan"
    assert any(node.label == "Ordering counter" for node in malformed.graph.nodes)


def test_fixed_owner_locked_and_confirmed_nodes_stay_locked_and_confirmed():
    graph = parse_room_json({"objects": [element("refrigerator"), element("chair")]})
    fridge, chair = graph.nodes
    owner_locked = chair.model_copy(update={"labeled_by": "owner", "movable": False, "quality": "confirmed"})
    graph = graph.model_copy(update={"nodes": [fridge, owner_locked]})

    rebuilt = apply_patches(graph, [
        LabelPatch(fridge.id, "Fridge", True, "measured"),
        LabelPatch(owner_locked.id, "Chair", True, "needs_another_look"),
    ], source="astra")

    assert not rebuilt.by_id(fridge.id).movable
    assert not rebuilt.by_id(owner_locked.id).movable
    assert rebuilt.by_id(owner_locked.id).quality == "confirmed"
    assert rebuilt.by_id(owner_locked.id).labeled_by == "owner"


def test_low_quality_evidence_is_never_promoted_by_a_model_patch():
    graph = parse_room_json({"objects": [element("chair", confidence="low")]})
    chair = graph.nodes[0]

    rebuilt = apply_patches(graph, [LabelPatch(chair.id, "Chair", True, "measured")], source="astra")

    assert rebuilt.by_id(chair.id).quality == "needs_another_look"


def test_valid_display_appearance_is_optional_and_keeps_model_source_truthful(tmp_path):
    graph = parse_room_json({"objects": [element("chair")]})
    chair = graph.nodes[0]
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    result = reconstruct_result(graph, transport=lambda *_: model_response(patch_for(
        chair,
        appearance={"base_color": "#aabbcc", "material": "wood"},
    )), frame_paths=[frame])

    assert result.source == "astra"
    assert result.graph.nodes[0].appearance.model_dump(mode="json") == {
        "base_color": "#aabbcc", "material": "wood", "source": "astra",
    }


def test_invalid_appearance_rejects_the_complete_remote_response():
    graph = parse_room_json({"objects": [element("chair")]})
    chair = graph.nodes[0]
    result = reconstruct_result(graph, transport=lambda *_: model_response(patch_for(
        chair,
        appearance={"base_color": "not-a-color", "material": "wood"},
    )))

    assert result.source == "roomplan"
    assert result.graph.nodes[0].appearance is None
    assert result.graph.nodes[0].label == "Chair"


def test_appearance_is_not_accepted_without_valid_photo_evidence():
    graph = parse_room_json({"objects": [element("chair")]})
    chair = graph.nodes[0]
    result = reconstruct_result(graph, transport=lambda *_: model_response(patch_for(
        chair,
        appearance={"base_color": "#aabbcc", "material": "wood"},
    )))

    assert result.source == "roomplan"
    assert result.graph.nodes[0].appearance is None


def test_context_tells_astra_when_photo_evidence_is_available(tmp_path):
    graph = parse_room_json({"objects": [element("chair")]})
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")

    with_photos = _chat_body(graph, frame_paths=[frame])["messages"][1]["content"]
    without_photos = _chat_body(graph)["messages"][1]["content"]

    assert json.loads(with_photos[0]["text"])["images_provided"] is True
    assert json.loads(without_photos)["images_provided"] is False


def test_label_request_bounds_reasoning_and_completion():
    graph = parse_room_json({"objects": [element("chair")]})

    body = _chat_body(graph)

    assert body["reasoning"] == {"effort": "low"}
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS
    assert MAX_OUTPUT_TOKENS == 8_192


def test_response_reader_stops_after_a_slow_heartbeat(monkeypatch):
    clock = [0.0]

    class HeartbeatResponse:
        def read1(self, _size):
            clock[0] += 0.75
            return b"heartbeat"

    monkeypatch.setattr(astra.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError, match="response_deadline"):
        astra._read_response(HeartbeatResponse(), deadline=1.0)


def test_response_reader_rejects_a_body_over_the_cap(monkeypatch):
    class OversizedResponse:
        def read1(self, size):
            return b"x" * size

    monkeypatch.setattr(astra, "MAX_RESPONSE_BYTES", 4)
    with pytest.raises(ValueError, match="response_too_large"):
        astra._read_response(OversizedResponse(), deadline=float("inf"))


def test_poses_project_in_the_ar_camera_forward_direction():
    graph = parse_room_json({
        "objects": [
            element("chair", at=(0.0, 0.0, -2.0)),
            element("table", at=(0.0, 0.0, 2.0)),
        ],
    })
    pose = _PoseEvidence(
        frame_key="frame-0", orientation="portrait",
        transform=tuple(column_major(0.0, 0.0, 0.0)),
        intrinsics=(100, 0, 50, 0, 100, 50, 50, 50, 1), order=0,
    )

    assert _project_score(graph.nodes[0], pose)[1]
    assert not _project_score(graph.nodes[1], pose)[1]


def test_frame_evidence_is_projected_bounded_normalized_and_embedded(tmp_path):
    graph = parse_room_json({"objects": [element("chair", at=(0.0, 0.0, -2.0))]})
    frame_paths = []
    poses = []
    for index in range(8):
        path = tmp_path / f"frame-{index:04d}"
        image = Image.new("RGB", (1600, 900), (index * 10, 40, 80))
        image.save(path, format="JPEG")
        frame_paths.append(path)
        poses.append({
            "image": f"frames/frame_{index:04d}.jpg",
            "orientation": "portrait",
            "intrinsics": [900, 0, 800, 0, 900, 450, 800, 450, 1],
            "transform": column_major(0.0, 0.0, 0.0),
        })
    poses_path = tmp_path / "poses.json"
    poses_path.write_text(json.dumps(poses))

    selected = select_keyframes(graph, frame_paths, poses_path)
    body = _chat_body(graph, frame_paths=frame_paths, poses_path=poses_path)
    content = body["messages"][1]["content"]

    assert len(selected) <= 6
    assert isinstance(content, list)
    images = content[1:]
    assert 1 <= len(images) <= 6
    encoded = [base64.b64decode(item["image_url"]["url"].split(",", 1)[1]) for item in images]
    assert sum(len(item) for item in encoded) <= MAX_IMAGE_BYTES
    assert all(Image.open(io.BytesIO(item)).height > Image.open(io.BytesIO(item)).width for item in encoded)
    assert all(max(Image.open(io.BytesIO(item)).size) <= 1024 for item in encoded)


def test_invalid_frame_bytes_are_skipped_without_breaking_label_request(tmp_path):
    graph = parse_room_json({"objects": [element("chair")]})
    invalid = tmp_path / "frame-0000"
    invalid.write_bytes(b"not an image")
    valid = tmp_path / "frame-0001"
    Image.new("RGB", (100, 80), "red").save(valid, format="JPEG")

    body = _chat_body(graph, frame_paths=[invalid, valid])
    content = body["messages"][1]["content"]
    assert isinstance(content, list)
    assert len(content) == 2


def test_calibrated_object_crop_association_allows_a_photo_reconstruction(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    table = graph.nodes[0]
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose()]))

    body = _chat_body(graph, frame_paths=[frame], poses_path=poses)
    context = json.loads(body["messages"][1]["content"][0]["text"])
    assert context["photo_evidence"] == [{
        "frame_id": "frame-0000", "object_ids": [str(table.id)], "camera_local": [0.0, -4.0, 0.0],
    }]

    result = reconstruct_result(
        graph,
        frame_paths=[frame],
        poses_path=poses,
        transport=lambda *_: model_response(patch_with_reconstruction(table, reconstruction_for(table))),
    )
    assert result.source == "astra"
    assert result.graph.nodes[0].reconstruction.evidence_frame_ids == ["frame-0000"]
    assert result.graph.nodes[0].reconstruction.parts[0].name == "top"


def test_reconstruction_rejects_frames_not_associated_with_its_object(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    table = graph.nodes[0]
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose()]))

    result = reconstruct_result(
        graph,
        frame_paths=[frame],
        poses_path=poses,
        transport=lambda *_: model_response(patch_with_reconstruction(table, reconstruction_for(table, "frame-9999"))),
    )
    assert result.source == "astra"
    assert result.graph.nodes[0].reconstruction is None
    assert result.graph.nodes[0].label == "Display case"


def test_reconstruction_rejects_parts_outside_measured_bounds(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    table = graph.nodes[0]
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose()]))

    malformed = reconstruction_for(table, center=(0.48, 0, 0), size=(0.2, 0.2, 0.2))
    result = reconstruct_result(
        graph,
        frame_paths=[frame],
        poses_path=poses,
        transport=lambda *_: model_response(patch_with_reconstruction(table, malformed)),
    )
    assert result.source == "astra"
    assert result.graph.nodes[0].reconstruction is None
    assert result.graph.nodes[0].label == "Display case"


def test_reconstruction_never_accepts_unassociated_full_frames():
    graph = parse_room_json({"objects": [element("table")]})
    table = graph.nodes[0]

    result = reconstruct_result(
        graph,
        transport=lambda *_: model_response(patch_with_reconstruction(table, reconstruction_for(table))),
    )
    assert result.source == "astra"
    assert result.graph.nodes[0].reconstruction is None


def test_mismatched_photo_dimensions_cannot_enable_calibrated_reconstruction(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    table = graph.nodes[0]
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    pose = calibrated_pose()
    pose["image_width"] = 101
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([pose]))

    result = reconstruct_result(
        graph,
        frame_paths=[frame],
        poses_path=poses,
        transport=lambda *_: model_response(patch_with_reconstruction(table, reconstruction_for(table))),
    )
    assert result.source == "astra"
    assert result.graph.nodes[0].reconstruction is None


def test_photo_reconstruction_can_enrich_a_confirmed_owner_node_without_relabeling_it(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    table = graph.nodes[0].model_copy(update={"label": "Owner table", "labeled_by": "owner", "quality": "confirmed", "movable": False})
    graph = graph.model_copy(update={"nodes": [table]})
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose()]))

    result = reconstruct_result(
        graph,
        frame_paths=[frame],
        poses_path=poses,
        transport=lambda *_: model_response(patch_with_reconstruction(table, reconstruction_for(table))),
    )
    rebuilt = result.graph.nodes[0]
    assert rebuilt.label == "Owner table"
    assert rebuilt.labeled_by == "owner"
    assert rebuilt.quality == "confirmed"
    assert not rebuilt.movable
    assert rebuilt.reconstruction is not None


def test_photo_reconstruction_batches_more_than_six_objects_without_starving_later_objects(tmp_path):
    graph = parse_room_json({"objects": [
        element("table", at=(0.0, 0.0, -2.0)) for _ in range(7)
    ]})
    frame = tmp_path / "frame-0000"
    Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose()]))
    batch_sizes = []

    def transport(_url, body, _headers):
        content = body["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        batch_sizes.append(len(context["objects"]))
        frames_by_object = {
            object_id: evidence["frame_id"]
            for evidence in context["photo_evidence"]
            for object_id in evidence["object_ids"]
        }
        nodes = [{
            "id": item["id"], "label": "Table", "movable": True, "quality": "measured",
            "appearance": None,
            "reconstruction": reconstruction_for(None, frames_by_object[item["id"]]),
        } for item in context["objects"]]
        return {"choices": [{"message": {"content": json.dumps({"nodes": nodes})}}]}

    result = reconstruct_result(graph, frame_paths=[frame], poses_path=poses, transport=transport)
    assert result.source == "astra"
    assert sorted(batch_sizes) == [1, 3, 3]
    assert all(node.reconstruction is not None for node in result.graph.nodes)


def test_calibrated_evidence_includes_a_separated_second_view_for_each_object(tmp_path):
    graph = parse_room_json({"objects": [element("table", at=(0.0, 0.0, -2.0))]})
    frames = []
    for index in range(2):
        frame = tmp_path / f"frame-{index:04d}"
        Image.new("RGB", (100, 80), "red").save(frame, format="JPEG")
        frames.append(frame)
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps([calibrated_pose(0), calibrated_pose(1, camera_x=0.3)]))

    body = _chat_body(graph, frame_paths=frames, poses_path=poses)
    context = json.loads(body["messages"][1]["content"][0]["text"])
    assert [item["frame_id"] for item in context["photo_evidence"]] == ["frame-0000", "frame-0001"]
    assert len(body["messages"][1]["content"]) == 3


def test_object_crop_rejects_near_plane_and_mostly_clipped_boxes():
    graph = parse_room_json({"objects": [element("table")]})
    table = graph.nodes[0]

    class NearPlaneCamera:
        width, height = 100, 80
        def project(self, points):
            return np.full(len(points), 50.0), np.full(len(points), 40.0), np.array([0.05] + [1.0] * (len(points) - 1))

    class ClippedCamera:
        width, height = 100, 80
        def project(self, points):
            if len(points) == 1:
                return np.array([50.0]), np.array([40.0]), np.array([1.0])
            return np.array([-300.0] * 7 + [50.0]), np.array([10.0] * 8), np.ones(8)

    assert astra._projected_object_crop(table, NearPlaneCamera())[0] is None
    assert astra._projected_object_crop(table, ClippedCamera())[0] is None


def test_calibrated_crop_rotates_after_sensor_coordinate_crop_for_portrait_model_input(tmp_path):
    frame = tmp_path / "sensor.jpg"
    Image.new("RGB", (100, 60), "red").save(frame, format="JPEG")

    # The crop is already tall, but the original sensor image is wide. Rotate
    # based on the sensor image before cropping, not the crop's own aspect.
    encoded = astra._encode_crop(frame, (10, 5, 40, 55), "portrait")
    assert Image.open(io.BytesIO(encoded)).size == (50, 30)


def test_reconstruction_wall_deadline_does_not_wait_for_stalled_workers(monkeypatch):
    graph = parse_room_json({"objects": [element("table") for _ in range(7)]})
    monkeypatch.setattr(astra, "RECONSTRUCTION_WALL_TIMEOUT_SECONDS", 0.05)

    def stalled_transport(*_args):
        time.sleep(0.25)
        return model_response()

    started = time.monotonic()
    result = reconstruct_result(graph, transport=stalled_transport)
    assert time.monotonic() - started < 0.18
    assert result.source == "roomplan"
