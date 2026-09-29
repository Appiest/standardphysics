"""Job lifecycle proofs (G02), binding: each process job runs once per evidence bundle it consumed.

Production-path synthetic tests: real HTTP, real queue, real persistence, with
only the provider-side discovery stubbed, exactly as K's closure contract
prescribes.
"""

import json
import threading
import time

from evidence_uploads import (
    complete_geometry,
    complete_semantics,
    discovering_stages,
    lidar_mesh_bytes,
    photo_manifest,
    poses_bytes,
    process_job_states,
)
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import create_scan, drain, put_artifact


def test_out_of_order_and_duplicate_uploads_schedule_exactly_one_job(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "lidar-mesh", lidar_mesh_bytes(), "lidar_mesh")
        put_artifact(client, scan_id, "poses", poses_bytes(), "poses")
        put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        duplicate = put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        assert duplicate.status_code == 200, duplicate.text
        conflict = put_artifact(client, scan_id, "frames", b"different bytes", "frames")
        assert conflict.status_code == 409, conflict.text
        complete_geometry(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        drain(client)

        assert calls["discover"] == 1
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 1)]
        assert client.get(f"/api/scans/{scan_id}/scene").status_code == 200


def test_late_upload_during_a_run_never_marks_the_newer_bundle_processed(make_client):
    calls = {"discover": 0}
    release = threading.Event()

    def discover(inputs):
        calls["discover"] += 1
        release.wait(timeout=10)
        return DiscoveryResult()

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert calls["discover"] == 1

        put_artifact(client, scan_id, "frames-2", b"newer frame bytes", "frames")
        worker = client.app.state.worker
        thread = threading.Thread(target=worker.drain)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with client.app.state.database.connect() as connection:
                running = connection.execute(
                    "SELECT state FROM jobs WHERE scan_id = ? AND kind = 'process'",
                    (scan_id,),
                ).fetchone()
            if running is not None and running["state"] == "running":
                break
            time.sleep(0.02)
        assert running is not None and running["state"] == "running"
        put_artifact(client, scan_id, "frames-3", b"newest frame bytes", "frames")
        mid_run = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert mid_run["semantic_job_pending"] is True
        assert mid_run["latest_bundle"]["semantic_processed_hash"] is None
        release.set()
        thread.join(timeout=20)
        assert not thread.is_alive()

        final = client.get(f"/api/scans/{scan_id}/evidence").json()
        if final["semantic_job_pending"]:
            drain(client)
            final = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert final["semantic_state"] == "complete"
        assert final["latest_bundle"]["semantic_processed_hash"] == final["manifest_hash"]
        assert calls["discover"] == 3
        with client.app.state.database.connect() as connection:
            marks = [
                row["semantic_processed_hash"]
                for row in connection.execute(
                    "SELECT semantic_processed_hash FROM evidence_bundles WHERE scan_id = ? ORDER BY version",
                    (scan_id,),
                ).fetchall()
            ]
        assert all(mark is None or mark in marks for mark in marks)
        assert marks[-1] == final["manifest_hash"]


def test_declared_evidence_mismatch_is_a_visible_failure_and_new_evidence_repairs(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        geometry_scene = client.get(f"/api/scans/{scan_id}/scene")
        assert geometry_scene.status_code == 200

        poses = poses_bytes()
        put_artifact(client, scan_id, "frame-0007", b"frame-bytes", "frames")
        put_artifact(client, scan_id, "poses", poses, "poses")
        put_artifact(client, scan_id, "lidar-mesh", lidar_mesh_bytes(), "lidar_mesh")
        wrong = json.dumps(
            {
                "manifest_version": 1,
                "poses_sha256": "0" * 64,
                "frames": [{"frame_id": "frame-0007", "sha256": "0" * 64, "bytes": 1}],
            }
        ).encode()
        put_artifact(client, scan_id, "photo-manifest", wrong, "photo_manifest")
        drain(client)

        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "failed"
        assert any("last processing job failed" in reason for reason in status["reasons"])
        assert any("does not match" in reason for reason in status["reasons"])
        assert status["latest_bundle"]["semantic_processed_hash"] is None
        assert calls["discover"] == 0
        assert client.get(f"/api/scans/{scan_id}/scene").status_code == 200

        repaired = photo_manifest(poses, "frame-0007", b"frame-bytes")
        assert put_artifact(client, scan_id, "photo-manifest-2", repaired, "photo_manifest").status_code == 201
        drain(client)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "complete"
        assert calls["discover"] == 1
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 3)]


def test_manifest_declaring_missing_photos_defers_discovery_until_all_arrive(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        poses = poses_bytes()
        put_artifact(client, scan_id, "poses", poses, "poses")
        put_artifact(client, scan_id, "lidar-mesh", lidar_mesh_bytes(), "lidar_mesh")
        put_artifact(client, scan_id, "frame-a", b"a-second-frame", "frames")
        manifest = photo_manifest(poses, "frame-0007", b"frame-bytes")
        put_artifact(client, scan_id, "photo-manifest", manifest, "photo_manifest")
        drain(client)

        rows = process_job_states(client, scan_id)
        assert rows[0][0] == "done"
        assert rows[0][3] and "deferred" in rows[0][3], rows
        assert calls["discover"] == 0
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["latest_bundle"]["semantic_processed_hash"] is None
        assert status["complete_evidence"] is True
        with client.app.state.database.connect() as connection:
            marked = connection.execute(
                "SELECT COUNT(*) FROM evidence_bundles WHERE scan_id = ? AND semantic_processed_hash IS NOT NULL",
                (scan_id,),
            ).fetchone()[0]
        assert marked == 0

        put_artifact(client, scan_id, "frame-0007", b"frame-bytes", "frames")
        drain(client)
        assert calls["discover"] == 1
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "complete"
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 3)]
