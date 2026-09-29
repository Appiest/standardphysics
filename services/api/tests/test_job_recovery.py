"""Job lifecycle proofs (G02), recovery: a restart or the idle sweep settles what a stopped process left.

A restart here is a second app on the same database with its worker really
running, so it queues again whatever a crashed process left running.
"""

import time

from evidence_uploads import (
    complete_geometry,
    complete_semantics,
    discovering_stages,
    job_of_kind,
    process_job_states,
)
from fastapi.testclient import TestClient
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import create_scan, drain, put_artifact


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
    with make_client(stages=discovering_stages(lambda inputs: DiscoveryResult()), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        with client.app.state.database.transaction() as connection:
            connection.execute(
                "UPDATE jobs SET state = 'running', attempts = 1 WHERE scan_id = ? AND kind = 'process'",
                (scan_id,),
            )

    import conftest

    restarted = _restarted_client(
        tmp_path,
        discovering_stages(lambda inputs: DiscoveryResult()),
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


def test_background_worker_settles_late_evidence_without_a_manual_drain(tmp_path):
    import conftest

    client_ctx = _restarted_client(
        tmp_path,
        discovering_stages(lambda inputs: DiscoveryResult()),
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
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 2)]


def test_quiet_late_evidence_settles_by_worker_sweep_across_restart(tmp_path):
    import conftest

    stages_for = discovering_stages(lambda inputs: DiscoveryResult())
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
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = client.get(f"/api/scans/{scan_id}/evidence").json()
            # The state reads complete a moment before the job's row settles, and a
            # slow runner saw the late upload land inside that moment.
            if status["semantic_state"] == "complete" and status["semantic_job_pending"] is False:
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
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 2)]


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
        discovering_stages(flaky),
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
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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
        assert [row[:2] for row in process_job_states(client, scan_id)] == [("done", 2)]


def test_failed_job_is_not_rerun_without_new_inputs(make_client):
    def broken(inputs):
        raise RuntimeError("provider died")

    with make_client(stages=discovering_stages(broken), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert process_job_states(client, scan_id)[0][:2] == ("failed", 1)

        drain(client)
        assert process_job_states(client, scan_id)[0][:2] == ("failed", 1)

        client.post(f"/api/scans/{scan_id}/complete")
        drain(client)
        assert process_job_states(client, scan_id)[0][:2] == ("failed", 2)


def test_a_restart_queues_interrupted_jobs_again_but_fails_an_interrupted_simulation(make_client):
    import uuid

    from standardphysics_api import repository_jobs as jobs_repo

    with make_client() as client:
        scan_id = create_scan(client)
        with client.app.state.database.transaction() as connection:
            for kind in ("process", "display", "simulate"):
                jobs_repo.enqueue_job(connection, uuid.UUID(scan_id), kind, 1)
            connection.execute("UPDATE jobs SET state = 'running' WHERE scan_id = ?", (scan_id,))
        client.app.state.worker._recover_interrupted_jobs()
        states = {kind: job_of_kind(client, scan_id, kind) for kind in ("process", "display", "simulate")}
    assert states["simulate"]["state"] == "failed"
    assert states["simulate"]["error"] == jobs_repo.INTERRUPTED_SIMULATION
    assert states["process"]["state"] == "queued"
    assert states["display"]["state"] == "queued"
