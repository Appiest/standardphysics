"""Uploads and job-row readers the job lifecycle tests share.

The payloads are the smallest real room, LiDAR mesh, poses and photo manifest
the worker accepts, so every graph a test reads was produced by the worker
running uploaded bytes rather than injected into the database.
"""

import hashlib
import json

from conftest import no_blender_stages, put_artifact, usdz_fixture


def _identity() -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.5, 0, 1]


def room_payload() -> bytes:
    payload = {
        "version": 1,
        "story": "ground",
        "captureMetadata": {"source": "pilot"},
        "walls": [
            {
                "identifier": "11111111-1111-1111-1111-111111111111",
                "dimensions": [4.0, 2.4, 0.2],
                "transform": _identity(),
                "confidence": "high",
            }
        ],
        "floors": [
            {
                "identifier": "22222222-2222-2222-2222-222222222222",
                "dimensions": [4.0, 0.1, 4.0],
                "transform": _identity(),
                "confidence": "high",
            }
        ],
        "objects": [
            {
                "identifier": "33333333-3333-3333-3333-333333333333",
                "category": "table",
                "dimensions": [1.0, 0.8, 1.0],
                "transform": _identity(),
                "confidence": "medium",
            }
        ],
    }
    return json.dumps(payload).encode()


def lidar_mesh_bytes() -> bytes:
    return json.dumps(
        {
            "parts": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                    "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                    "triangles": [0, 1, 2],
                }
            ]
        }
    ).encode()


def poses_bytes() -> bytes:
    pose = {
        "metadata_version": 2,
        "frame_id": "frame-0007",
        "image": "frames/frame_0007.jpg",
        "timestamp": 1,
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
        "intrinsics": [100, 0, 0, 0, 100, 0, 50, 50, 1],
        "image_width": 100,
        "image_height": 100,
        "calibration_width": 100,
        "calibration_height": 100,
        "image_orientation": "sensor",
    }
    return json.dumps([pose]).encode()


def discovering_stages(discover):
    return no_blender_stages(label=lambda graph, **kwargs: graph, discover=discover)


def process_job_states(client, scan_id) -> list[tuple[str, int]]:
    with client.app.state.database.connect() as connection:
        rows = connection.execute(
            "SELECT state, attempts, input_hash, note FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id",
            (scan_id,),
        ).fetchall()
    return [(row["state"], row["attempts"], row["input_hash"], row["note"]) for row in rows]


def complete_geometry(client, scan_id) -> None:
    put_artifact(client, scan_id, "room-json", room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")


def complete_semantics(client, scan_id, frame_id="frames", frame=b"frame-bytes") -> None:
    put_artifact(client, scan_id, frame_id, frame, "frames")
    put_artifact(client, scan_id, "poses", poses_bytes(), "poses")
    put_artifact(client, scan_id, "lidar-mesh", lidar_mesh_bytes(), "lidar_mesh")


def photo_manifest(matching_poses: bytes, declared_frame: str, frame: bytes) -> bytes:
    return json.dumps(
        {
            "manifest_version": 1,
            "poses_sha256": hashlib.sha256(matching_poses).hexdigest(),
            "frames": [
                {
                    "frame_id": declared_frame,
                    "sha256": hashlib.sha256(frame).hexdigest(),
                    "bytes": len(frame),
                }
            ],
        }
    ).encode()


def job_of_kind(client, scan_id, kind):
    with client.app.state.database.connect() as connection:
        return connection.execute(
            "SELECT state, error FROM jobs WHERE scan_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (scan_id, kind)
        ).fetchone()
