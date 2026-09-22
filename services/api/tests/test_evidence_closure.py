"""Evidence closure contract, frozen by K (coordinator plan 7a).

Production-path synthetic tests (L2): real HTTP, real queue, real persistence,
with only the provider-side discovery labelled. B owns hardening these into its
lifecycle proofs; Q re-evaluates them independently.
"""

import json

from conftest import create_scan, drain, put_artifact, usdz_fixture
from standardphysics_pipeline.discovery import DiscoveryResult


def _closing_stages():
    from conftest import no_blender_stages

    calls = {"discover": 0}

    def label(graph, **kwargs):
        return graph

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    return no_blender_stages(label=label, discover=discover), calls


def _identity() -> list[float]:
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.5, 0, 1]


def _room_payload() -> bytes:
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


def _mesh_bytes() -> bytes:
    return json.dumps({
        "parts": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
            "triangles": [0, 1, 2],
        }],
    }).encode()


def _complete_geometry(client, scan_id) -> None:
    put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")


def _complete_semantics(client, scan_id) -> None:
    put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
    put_artifact(client, scan_id, "poses", b"{}", "poses")
    put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")


def _process_jobs(client, scan_id) -> list[tuple[str, int]]:
    with client.app.state.database.connect() as connection:
        rows = connection.execute(
            "SELECT state, attempts FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id",
            (scan_id,),
        ).fetchall()
    return [(row["state"], row["attempts"]) for row in rows]


def test_legacy_geometry_complete_stays_browsable_with_semantics_blocked(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        completed = client.post(f"/api/scans/{scan_id}/complete")
        assert completed.status_code == 200
        drain(client)

        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["geometry_state"] == "ready"
        assert status["evidence_state"] == "awaiting"
        assert status["semantic_state"] == "blocked_incomplete_evidence"
        assert sorted(status["missing_semantic_kinds"]) == ["frames", "lidar_mesh", "poses"]
        assert status["complete_evidence"] is False
        assert any("blocked" in reason for reason in status["reasons"])

        assert client.get(f"/api/scans/{scan_id}/scene").status_code == 200
        assert calls["discover"] == 0


def test_late_evidence_closes_exactly_one_new_bundle_and_one_job(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]

        _complete_semantics(client, scan_id)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["bundle_version"] == 4
        assert status["complete_evidence"] is True
        assert status["semantic_job_pending"] is True

        drain(client)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "complete"
        assert status["semantic_job_pending"] is False
        assert status["latest_bundle"]["semantic_processed_hash"] == status["manifest_hash"]
        assert calls["discover"] == 1
        assert _process_jobs(client, scan_id) == [("done", 2)]


def test_identical_replay_after_closure_adds_no_bundle_or_job(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]

        for _ in range(3):
            attempted = client.post(f"/api/scans/{scan_id}/complete")
            assert attempted.status_code == 200
        drain(client)

        assert _process_jobs(client, scan_id) == [("done", 1)]
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["bundle_version"] == 1
        assert calls["discover"] == 1


def test_duplicate_upload_of_same_semantic_bytes_adds_no_bundle(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]

        again = put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        assert again.status_code == 200, again.text
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]
        assert client.get(f"/api/scans/{scan_id}/evidence").json()["bundle_version"] == 1


import hashlib


def _frame_bytes() -> bytes:
    return b"frame-bytes"


def _poses_bytes() -> bytes:
    records = [{
        "metadata_version": 2,
        "frame_id": "frame-0000",
        "image": "frame-0000.jpg",
        "timestamp": 10.0,
        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1.0, 0, 1],
        "intrinsics": [1000, 0, 0, 0, 1000, 0, 960, 720, 1],
        "orientation": "sensor",
        "image_width": 1920,
        "image_height": 1440,
        "calibration_width": 1920,
        "calibration_height": 1440,
        "image_orientation": "sensor",
    }]
    return json.dumps(records).encode()


def _valid_manifest_bytes() -> bytes:
    frame = _frame_bytes()
    return json.dumps({
        "manifest_version": 1,
        "poses_sha256": hashlib.sha256(_poses_bytes()).hexdigest(),
        "frames": [{
            "frame_id": "frame-0000",
            "sha256": hashlib.sha256(frame).hexdigest(),
            "bytes": len(frame),
        }],
    }).encode()


def test_settling_complete_evidence_waits_for_quiet_or_explicit_complete(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=3600.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]

        _complete_semantics(client, scan_id)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["bundle_version"] == 4
        assert status["complete_evidence"] is True
        assert status["semantic_state"] == "settling"
        assert status["semantic_job_pending"] is False
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 1)]
        assert calls["discover"] == 0

        client.post(f"/api/scans/{scan_id}/complete")
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_job_pending"] is True
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 2)]
        assert calls["discover"] == 1
        assert client.get(f"/api/scans/{scan_id}/evidence").json()["semantic_state"] == "complete"


def test_closing_manifest_settles_the_evidence_immediately(make_client):
    stages, calls = _closing_stages()
    with make_client(stages=stages, evidence_settle_seconds=3600.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        put_artifact(client, scan_id, "frame-0000", _frame_bytes(), "frames")
        put_artifact(client, scan_id, "poses", _poses_bytes(), "poses")
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
        stored = put_artifact(client, scan_id, "photo-manifest", _valid_manifest_bytes(), "photo_manifest")
        assert stored.status_code == 201, stored.text
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "queued"
        assert status["semantic_job_pending"] is True
        drain(client)
        assert _process_jobs(client, scan_id) == [("done", 2)]
        assert calls["discover"] == 1
