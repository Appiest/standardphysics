"""Job lifecycle proofs (G02), visibility: provider failures and requests are recorded per attempt, without secrets."""

import json

from evidence_uploads import complete_geometry, complete_semantics, discovering_stages
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import create_scan, drain, put_artifact


def test_provider_failure_categories_are_visible_and_secret_free(make_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-do-not-appear-anywhere")
    failure = "frame-0001: the vision model rejected authentication: HTTP 401"
    transient = "frame-0002: the vision model had a transient HTTP error: HTTP 429"

    def failing_discover(inputs):
        return DiscoveryResult(frames_read=1, failures=[failure, transient])

    def empty_discover(inputs):
        return DiscoveryResult(frames_read=3)

    with make_client(stages=discovering_stages(failing_discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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
        complete_geometry(client, second)
        complete_semantics(client, second, frame_id="frames-b", frame=b"other-bytes")
        client.post(f"/api/scans/{second}/complete")
        drain(client)
        text = json.dumps(client.get(f"/api/scans/{second}/evidence").json())
        assert "read 3 photos" in text
        assert "found 0 objects" in text
        assert "unread" not in text


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

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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

    with make_client(stages=discovering_stages(discover), evidence_settle_seconds=0.0) as client:
        scan_id = create_scan(client)
        complete_geometry(client, scan_id)
        complete_semantics(client, scan_id)
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
