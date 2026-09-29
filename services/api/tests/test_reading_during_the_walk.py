"""Frames uploaded during the walk with their pose are read before the walk ends.

Real HTTP and the real worker; only the detector is a stand-in. The job record
of the walk's discovery counts the photos the walk already read, and a pose
that could not be the frame's own is refused before anything is stored.
"""

import hashlib
import json
import time

from evidence_uploads import complete_geometry, lidar_mesh_bytes, process_job_states
from standardphysics_pipeline.discovery import Detection, DiscoveryResult

from conftest import create_scan, drain, no_blender_stages


def pose(frame_id: str, timestamp: float, metres: float) -> dict:
    return {
        "metadata_version": 2,
        "frame_id": frame_id,
        "image": f"frames/{frame_id.replace('-', '_')}.jpg",
        "timestamp": timestamp,
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, metres, 0, 0, 1],
        "intrinsics": [100, 0, 0, 0, 100, 0, 50, 50, 1],
        "orientation": "portrait",
        "image_width": 100,
        "image_height": 100,
        "calibration_width": 100,
        "calibration_height": 100,
        "image_orientation": "sensor",
    }


WALK = [pose("frame-0000", 0.0, 0.0), pose("frame-0001", 0.5, 0.02), pose("frame-0002", 1.0, 0.6)]
"""The middle photo is a step of two centimetres half a second on: the same view again."""


def put_frame(client, scan_id, frame_id: str, pose_header: str | None, kind: str = "frames"):
    body = f"jpeg bytes of {frame_id}".encode()
    headers = {"X-Checksum-SHA256": hashlib.sha256(body).hexdigest(), "X-Artifact-Kind": kind}
    if pose_header is not None:
        headers["X-Frame-Pose"] = pose_header
    return client.put(f"/api/scans/{scan_id}/artifacts/{frame_id}", content=body, headers=headers)


def walk_then_finish(client, scan_id) -> None:
    for record in WALK:
        assert put_frame(client, scan_id, record["frame_id"], json.dumps(record)).status_code == 201
    reader = client.app.state.worker.live_reader
    deadline = time.monotonic() + 5
    while reader.pending(scan_id) and time.monotonic() < deadline:
        time.sleep(0.01)


def reading_stages(read: list[str]):
    def read_photo(path, frame_id, **_):
        read.append(frame_id)
        return [Detection(frame_id=frame_id, name="card reader", box=(1, 1, 9, 9), movable=True, confidence=0.9)]

    return no_blender_stages(
        label=lambda graph, **kwargs: graph,
        discover=lambda inputs: DiscoveryResult(frames_read=len(inputs.frame_paths)),
        read_photo=read_photo,
    )


def test_frames_sent_with_their_pose_are_read_while_the_walk_goes_on(make_client):
    read: list[str] = []
    with make_client(stages=reading_stages(read), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        walk_then_finish(client, scan_id)
        assert read == ["frame-0000", "frame-0002"]
        cached = list((client.app.state.store.scan_dir(scan_id) / "detections").glob("*.json"))
        assert len(cached) == 2

        complete_geometry(client, scan_id)
        poses = json.dumps(WALK).encode()
        client.put(
            f"/api/scans/{scan_id}/artifacts/poses", content=poses,
            headers={"X-Checksum-SHA256": hashlib.sha256(poses).hexdigest(), "X-Artifact-Kind": "poses"},
        )
        mesh = lidar_mesh_bytes()
        client.put(
            f"/api/scans/{scan_id}/artifacts/lidar-mesh", content=mesh,
            headers={"X-Checksum-SHA256": hashlib.sha256(mesh).hexdigest(), "X-Artifact-Kind": "lidar_mesh"},
        )
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        notes = [note for *_, note in process_job_states(client, scan_id) if note]
        assert any("2 photos read during the walk" in note for note in notes), notes


def test_a_frame_sent_without_a_pose_waits_for_discovery(make_client):
    read: list[str] = []
    with make_client(stages=reading_stages(read)) as client:
        scan_id = create_scan(client)
        assert put_frame(client, scan_id, "frame-0000", None).status_code == 201
        time.sleep(0.05)
        assert read == []


def test_a_pose_that_is_not_the_frames_own_is_refused_and_nothing_is_stored(make_client):
    read: list[str] = []
    with make_client(stages=reading_stages(read)) as client:
        scan_id = create_scan(client)
        refusals = [
            put_frame(client, scan_id, "frame-0000", "{not json"),
            put_frame(client, scan_id, "frame-0000", json.dumps(pose("frame-0009", 0.0, 0.0))),
            put_frame(client, scan_id, "frame-0000", json.dumps(pose("frame-0000", 0.0, 0.0)), kind="poses"),
        ]
        assert [response.status_code for response in refusals] == [400, 400, 400]
        assert client.get(f"/api/scans/{scan_id}").json()["artifacts"] == []
        assert read == []
