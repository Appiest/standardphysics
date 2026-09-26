"""Job lifecycle proofs (G02): binding, crash recovery, deferral, visibility.

Production-path synthetic tests: real HTTP, real queue, real persistence, with
only the provider-side discovery stubbed, exactly as K's closure contract
prescribes. No finished SceneNode is injected into a database fixture; every
graph here is produced by the worker running the uploaded bytes.
"""

import json
import threading
import time

from fastapi.testclient import TestClient
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import create_scan, drain, put_artifact, usdz_fixture


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


def _poses() -> bytes:
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


def _stages(discover):
    from conftest import no_blender_stages

    return no_blender_stages(label=lambda graph, **kwargs: graph, discover=discover)


def _job_states(client, scan_id) -> list[tuple[str, int]]:
    with client.app.state.database.connect() as connection:
        rows = connection.execute(
            "SELECT state, attempts, input_hash, note FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id",
            (scan_id,),
        ).fetchall()
    return [(row["state"], row["attempts"], row["input_hash"], row["note"]) for row in rows]


def _complete_geometry(client, scan_id) -> None:
    put_artifact(client, scan_id, "room-json", _room_payload(), "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")


def _complete_semantics(client, scan_id, frame_id="frames", frame=b"frame-bytes") -> None:
    put_artifact(client, scan_id, frame_id, frame, "frames")
    put_artifact(client, scan_id, "poses", _poses(), "poses")
    put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")


def _manifest(matching_poses: bytes, declared_frame: str, frame: bytes) -> bytes:
    import hashlib

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


def test_out_of_order_and_duplicate_uploads_schedule_exactly_one_job(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
        put_artifact(client, scan_id, "poses", _poses(), "poses")
        put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        duplicate = put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        assert duplicate.status_code == 200, duplicate.text
        conflict = put_artifact(client, scan_id, "frames", b"different bytes", "frames")
        assert conflict.status_code == 409, conflict.text
        _complete_geometry(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        drain(client)

        assert calls["discover"] == 1
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 1)]
        assert client.get(f"/api/scans/{scan_id}/scene").status_code == 200


def test_late_upload_during_a_run_never_marks_the_newer_bundle_processed(make_client):
    calls = {"discover": 0}
    release = threading.Event()

    def discover(inputs):
        calls["discover"] += 1
        release.wait(timeout=10)
        return DiscoveryResult()

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
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


def _restarted_client(tmp_path, stages, owner_email, owner_password, sign_in: bool = True, settle_seconds: float = 0.0):
    """A second app on the same database, with its worker really running.

    Entering the context starts the worker, which re-queues any job a crashed
    process left running, exactly as a restart does.
    """
    from contextlib import contextmanager

    from standardphysics_api.app import create_app
    from standardphysics_api.settings import Settings

    @contextmanager
    def build():
        settings = Settings(
            data_dir=tmp_path / "var",
            max_artifact_bytes=5_000_000,
            evidence_settle_seconds=settle_seconds,
        )
        test_client = TestClient(create_app(settings, stages, run_worker=True))
        with test_client:
            if sign_in:
                response = test_client.post(
                    "/api/auth/sign-in",
                    json={"email": owner_email, "password": owner_password},
                )
                assert response.status_code == 200, response.text
            yield test_client

    return build()


def test_crash_after_claim_recovers_with_one_coherent_revision(make_client, tmp_path):
    with make_client(stages=_stages(lambda inputs: DiscoveryResult()), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        with client.app.state.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state = 'running', attempts = 1 WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            )

    import conftest

    restarted = _restarted_client(
        tmp_path,
        _stages(lambda inputs: DiscoveryResult()),
        conftest.OWNER_EMAIL,
        conftest.OWNER_PASSWORD,
    )
    with restarted as client:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if status["semantic_state"] == "complete":
                break
            time.sleep(0.05)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "complete", status
        with client.app.state.database.connect() as connection:
            revisions = connection.execute("SELECT revision FROM revisions WHERE scan_id = ?", (scan_id,)).fetchall()
            processed = connection.execute(
                "SELECT semantic_processed_hash FROM evidence_bundles WHERE scan_id = ?",
                (scan_id,),
            ).fetchone()
        assert [row["revision"] for row in revisions] == [0]
        assert processed["semantic_processed_hash"] == status["manifest_hash"]


def test_declared_evidence_mismatch_is_a_visible_failure_and_new_evidence_repairs(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        geometry_scene = client.get(f"/api/scans/{scan_id}/scene")
        assert geometry_scene.status_code == 200

        poses = _poses()
        put_artifact(client, scan_id, "frame-0007", b"frame-bytes", "frames")
        put_artifact(client, scan_id, "poses", poses, "poses")
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
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

        repaired = _manifest(poses, "frame-0007", b"frame-bytes")
        assert put_artifact(client, scan_id, "photo-manifest-2", repaired, "photo_manifest").status_code == 201
        drain(client)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "complete"
        assert calls["discover"] == 1
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 3)]


def test_manifest_declaring_missing_photos_defers_discovery_until_all_arrive(make_client):
    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        return DiscoveryResult()

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        poses = _poses()
        put_artifact(client, scan_id, "poses", poses, "poses")
        put_artifact(client, scan_id, "lidar-mesh", _mesh_bytes(), "lidar_mesh")
        put_artifact(client, scan_id, "frame-a", b"a-second-frame", "frames")
        manifest = _manifest(poses, "frame-0007", b"frame-bytes")
        put_artifact(client, scan_id, "photo-manifest", manifest, "photo_manifest")
        drain(client)

        rows = _job_states(client, scan_id)
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
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 3)]


def test_provider_failure_categories_are_visible_and_secret_free(make_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-do-not-appear-anywhere")
    failure = "frame-0001: the vision model rejected authentication: HTTP 401"
    transient = "frame-0002: the vision model had a transient HTTP error: HTTP 429"

    def failing_discover(inputs):
        return DiscoveryResult(frames_read=1, failures=[failure, transient])

    def empty_discover(inputs):
        return DiscoveryResult(frames_read=3)

    with make_client(stages=_stages(failing_discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        text = json.dumps(status)
        assert "2 frame(s) unread" in text
        assert "rejected authentication" in text
        assert "sk-do-not-appear-anywhere" not in text
        assert status["semantic_state"] == "complete"

        client.app.state.worker.stages.discover = empty_discover
        second = create_scan(client)
        _complete_geometry(client, second)
        _complete_semantics(client, second, frame_id="frames-b", frame=b"other-bytes")
        client.post(f"/api/scans/{second}/complete")
        drain(client)
        text = json.dumps(client.get(f"/api/scans/{second}/evidence").json())
        assert "read 3 photos" in text
        assert "found 0 objects" in text
        assert "unread" not in text


def test_background_worker_settles_late_evidence_without_a_manual_drain(tmp_path):
    import conftest

    client_ctx = _restarted_client(
        tmp_path,
        _stages(lambda inputs: DiscoveryResult()),
        conftest.OWNER_EMAIL,
        conftest.OWNER_PASSWORD,
        sign_in=False,
    )
    with client_ctx as client:
        assert (
            client.post(
                "/api/auth/sign-up",
                json={"email": "late@example.com", "password": "late-evidence-password", "shop_name": "late"},
            ).status_code
            == 201
        )
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200

        # The job that marks the evidence complete goes on to run the checks, so
        # "complete" arrives a moment before the job stops being pending. Settled
        # means both.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if status["semantic_state"] == "complete" and not status["semantic_job_pending"]:
                break
            time.sleep(0.1)
        first = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert first["semantic_state"] == "complete", first
        assert first["semantic_job_pending"] is False

        put_artifact(client, scan_id, "frames-late", b"late evidence bytes", "frames")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if (
                status["semantic_job_pending"] is False
                and status["semantic_state"] == "complete"
                and status["manifest_hash"] != first["manifest_hash"]
            ):
                break
            time.sleep(0.1)
        settled = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert settled["semantic_job_pending"] is False
        assert settled["semantic_state"] == "complete"
        assert settled["manifest_hash"] != first["manifest_hash"]
        assert settled["latest_bundle"]["semantic_processed_hash"] == settled["manifest_hash"]
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 2)]


def test_quiet_late_evidence_settles_by_worker_sweep_across_restart(tmp_path):
    import conftest

    stages_for = _stages(lambda inputs: DiscoveryResult())
    sign_up = {
        "json": {"email": "quiet@example.com", "password": "quiet-owner-password", "shop_name": "quiet"},
    }
    first = _restarted_client(
        tmp_path,
        stages_for,
        conftest.OWNER_EMAIL,
        conftest.OWNER_PASSWORD,
        sign_in=False,
        settle_seconds=30.0,
    )
    with first as client:
        assert client.post("/api/auth/sign-up", **sign_up).status_code == 201
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if status["semantic_state"] == "complete":
                break
            time.sleep(0.1)
        first_hash = client.get(f"/api/scans/{scan_id}/evidence").json()["manifest_hash"]

        put_artifact(client, scan_id, "frames-late", b"quiet-window evidence", "frames")
        unquiet = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert unquiet["semantic_job_pending"] is False
        assert unquiet["latest_bundle"]["semantic_processed_hash"] is None
        assert unquiet["manifest_hash"] != first_hash

    second = _restarted_client(
        tmp_path,
        stages_for,
        "quiet@example.com",
        "quiet-owner-password",
        sign_in=True,
        settle_seconds=1.0,
    )
    with second as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if (
                status["semantic_state"] == "complete"
                and status["semantic_job_pending"] is False
                and status["manifest_hash"] != first_hash
            ):
                break
            time.sleep(0.1)
        settled = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert settled["semantic_state"] == "complete", settled
        assert settled["semantic_job_pending"] is False
        assert settled["manifest_hash"] != first_hash
        assert settled["latest_bundle"]["semantic_processed_hash"] == settled["manifest_hash"]
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 2)]


def test_sweep_never_retries_a_failed_input_until_new_evidence(tmp_path):
    import conftest

    calls = {"discover": 0}

    def flaky(inputs):
        calls["discover"] += 1
        if calls["discover"] == 1:
            raise RuntimeError("provider died")
        return DiscoveryResult()

    client_ctx = _restarted_client(
        tmp_path,
        _stages(flaky),
        conftest.OWNER_EMAIL,
        conftest.OWNER_PASSWORD,
        sign_in=False,
        settle_seconds=0.0,
    )
    with client_ctx as client:
        assert (
            client.post(
                "/api/auth/sign-up",
                json={"email": "storm@example.com", "password": "storm-owner-password", "shop_name": "storm"},
            ).status_code
            == 201
        )
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if status["semantic_state"] == "failed":
                break
            time.sleep(0.1)
        failed = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert failed["semantic_state"] == "failed", failed
        with client.app.state.database.connect() as connection:
            attempts_after_failure = connection.execute(
                "SELECT attempts FROM jobs WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            ).fetchone()[0]
        assert attempts_after_failure == 1

        # Let the idle sweep tick several times; nothing may re-queue the same input.
        time.sleep(6)
        with client.app.state.database.connect() as connection:
            row = connection.execute(
                "SELECT state, attempts FROM jobs WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            ).fetchone()
        assert tuple(row) == ("failed", 1), tuple(row)

        put_artifact(client, scan_id, "frames-fresh", b"evidence that changes the input", "frames")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            if status["semantic_state"] == "complete":
                break
            time.sleep(0.1)
        settled = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert settled["semantic_state"] == "complete", settled
        assert calls["discover"] == 2
        assert [row[:2] for row in _job_states(client, scan_id)] == [("done", 2)]


def test_provider_request_metadata_persists_on_the_job(make_client):
    from standardphysics_pipeline.discovery import ModelRequestInfo

    def discover(inputs):
        return DiscoveryResult(
            frames_read=2,
            model_requests=[
                ModelRequestInfo(
                    frame_id="frame-a",
                    provider="api.example.test",
                    model="test-model",
                    orientation="landscape_right",
                    request_id="req-1",
                    usage={"total_tokens": 100},
                ),
                ModelRequestInfo(
                    frame_id="frame-b",
                    provider="api.example.test",
                    model="test-model",
                    orientation="portrait",
                    request_id="req-2",
                    usage={"total_tokens": 80},
                ),
            ],
        )

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        with client.app.state.database.connect() as connection:
            payload = connection.execute(
                "SELECT model_requests_json FROM jobs WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            ).fetchone()["model_requests_json"]
        requests = json.loads(payload)
        assert [request["frame_id"] for request in requests] == ["frame-a", "frame-b"]
        assert requests[0]["provider"] == "api.example.test"
        assert requests[0]["request_id"] == "req-1"
        assert requests[0]["usage"]["total_tokens"] == 100
        logged = json.dumps(requests)
        assert "token" not in logged.replace("total_tokens", "").lower()


def _attempt_rows(client, scan_id):
    with client.app.state.database.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT a.attempt, a.input_hash, a.state, a.error, a.note, a.model_requests_json"
                " FROM job_attempts a JOIN jobs j ON j.id = a.job_id"
                " WHERE a.scan_id = ? AND j.kind = 'process' ORDER BY a.job_id, a.attempt",
                (scan_id,),
            ).fetchall()
        ]


def test_attempt_history_binds_requests_to_their_input_and_the_latest_view_stays_empty(make_client):
    from standardphysics_pipeline.discovery import ModelRequestInfo

    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        if calls["discover"] == 1:
            return DiscoveryResult(
                frames_read=1,
                model_requests=[
                    ModelRequestInfo(
                        frame_id="frame-a",
                        provider="api.example.test",
                        model="test-model",
                        orientation="landscape_right",
                        request_id="old-request",
                        usage={"total_tokens": 12},
                    )
                ],
            )
        return DiscoveryResult(frames_read=1)

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        tries = _attempt_rows(client, scan_id)
        assert [row["attempt"] for row in tries] == [1]
        old_requests = json.loads(tries[0]["model_requests_json"])
        assert old_requests[0]["request_id"] == "old-request"
        first_hash = tries[0]["input_hash"]

        put_artifact(client, scan_id, "frames-fresh", b"evidence that changes the input", "frames")
        drain(client)

        with client.app.state.database.connect() as connection:
            latest_view = connection.execute(
                "SELECT model_requests_json FROM jobs WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            ).fetchone()["model_requests_json"]
        assert latest_view is None

        tries = _attempt_rows(client, scan_id)
        assert [row["attempt"] for row in tries] == [1, 2]
        assert tries[0]["input_hash"] == first_hash
        assert tries[1]["input_hash"] != first_hash
        assert json.loads(tries[0]["model_requests_json"])[0]["request_id"] == "old-request"
        assert tries[1]["model_requests_json"] is None
        assert tries[1]["state"] == "done"


def test_failed_second_attempt_records_itself_without_inheriting_old_requests(make_client):
    from standardphysics_pipeline.discovery import ModelRequestInfo

    calls = {"discover": 0}

    def discover(inputs):
        calls["discover"] += 1
        if calls["discover"] == 1:
            return DiscoveryResult(
                frames_read=1,
                model_requests=[
                    ModelRequestInfo(
                        frame_id="frame-a",
                        provider="api.example.test",
                        model="test-model",
                        orientation="landscape_right",
                        request_id="first-request",
                        usage={"total_tokens": 12},
                    )
                ],
            )
        raise RuntimeError("provider quota exhausted")

    with make_client(stages=_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)

        put_artifact(client, scan_id, "frames-fresh", b"evidence that changes the input", "frames")
        drain(client)

        tries = _attempt_rows(client, scan_id)
        assert [row["attempt"] for row in tries] == [1, 2]
        assert tries[0]["state"] == "done"
        assert json.loads(tries[0]["model_requests_json"])[0]["request_id"] == "first-request"
        assert tries[1]["state"] == "failed"
        assert "provider quota" in tries[1]["error"]
        assert tries[1]["model_requests_json"] is None
        with client.app.state.database.connect() as connection:
            latest_view = connection.execute(
                "SELECT model_requests_json FROM jobs WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            ).fetchone()["model_requests_json"]
        assert latest_view is None
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["semantic_state"] == "failed"


def test_discovery_inputs_point_crops_at_the_scan_crop_dir(tmp_path):
    import uuid as uuid_module

    from standardphysics_contracts import SceneGraph

    from standardphysics_api.stages import _discovery_inputs

    scan_root = tmp_path / "scans" / str(uuid_module.uuid4())
    artifacts = scan_root / "artifacts"
    artifacts.mkdir(parents=True)
    frame = artifacts / "frame-0007"
    poses = artifacts / "poses"
    lidar = artifacts / "lidar_mesh"
    for path, payload in ((frame, b"jpeg"), (poses, b"{}"), (lidar, b"mesh")):
        path.write_bytes(payload)

    graph = SceneGraph(scan_id=uuid_module.uuid4(), nodes=[])
    inputs = _discovery_inputs(graph, [frame], poses, lidar)
    assert inputs is not None
    assert inputs.crop_dir == scan_root / "crops"
    assert inputs.cache_dir == scan_root / "detections"

    missing = _discovery_inputs(graph, [frame], poses, artifacts / "nope_mesh")
    assert missing is None


def test_failed_job_is_not_rerun_without_new_inputs(make_client):
    def broken(inputs):
        raise RuntimeError("provider died")

    with make_client(stages=_stages(broken), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        _complete_geometry(client, scan_id)
        _complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _job_states(client, scan_id)[0][:2] == ("failed", 1)

        drain(client)
        assert _job_states(client, scan_id)[0][:2] == ("failed", 1)

        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert _job_states(client, scan_id)[0][:2] == ("failed", 2)
