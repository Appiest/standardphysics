"""While a deploy drains the API, nothing new is queued or started, and the uploads already admitted carry on.

scripts/deploy.sh turns the drain on with `python -m standardphysics_api.drain on`
before it reads the queue, so the job it counts is the last one a restart could
interrupt.
"""

from conftest import create_scan, drain, put_artifact, usdz_fixture
from standardphysics_api import drain as deploy_drain
from standardphysics_api import repository as repo
from standardphysics_api.worker import ASSESS

UPDATING = "Standard Physics is updating; try again in a minute."


def _start_draining(client) -> None:
    deploy_drain.turn_on(client.app.state.worker.settings.data_dir)


def _uploaded_walk(client) -> str:
    scan_id = create_scan(client)
    assert put_artifact(client, scan_id, "room-json", b"{}", "room_json").status_code == 201
    assert put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz").status_code == 201
    return scan_id


def _job_states(client) -> list[str]:
    with client.app.state.database.connect() as connection:
        return [row["state"] for row in connection.execute("SELECT state FROM jobs ORDER BY id")]


def test_finishing_a_walk_is_refused_with_a_retry_while_draining(make_client):
    with make_client() as client:
        scan_id = _uploaded_walk(client)
        _start_draining(client)
        refused = client.post(f"/api/scans/{scan_id}/complete")
        queued = _job_states(client)
        scan = client.get(f"/api/scans/{scan_id}").json()
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"] == UPDATING
    assert int(refused.headers["retry-after"]) > 0
    assert queued == []
    assert scan["state"] == "uploading"


def test_every_request_that_queues_a_job_is_refused_while_draining(make_client):
    with make_client(seed=True, team=True) as client:
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        _start_draining(client)
        refused = client.post(f"/api/scans/{scan_id}/simulations", json={"base_revision": 0, "samples": 2})
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"] == UPDATING


def test_artifact_uploads_carry_on_while_draining(make_client):
    with make_client() as client:
        scan_id = create_scan(client)
        _start_draining(client)
        uploaded = put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert uploaded.status_code == 201, uploaded.text


def test_the_worker_starts_no_queued_job_while_draining_and_does_once_it_ends(make_client):
    with make_client() as client:
        scan_id = create_scan(client)
        with client.app.state.database.transaction() as connection:
            repo.enqueue_job(connection, scan_id, ASSESS, 0)
        _start_draining(client)
        drain(client)
        while_draining = _job_states(client)
        deploy_drain.turn_off(client.app.state.worker.settings.data_dir)
        drain(client)
        after = _job_states(client)
    assert while_draining == ["queued"]
    assert after != ["queued"]


def test_health_details_says_when_the_api_is_draining(make_client):
    with make_client() as client:
        before = client.get("/health/details").json()["draining"]
        _start_draining(client)
        during = client.get("/health/details").json()["draining"]
    assert (before, during) == (False, True)


def test_the_command_turns_the_drain_on_and_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SP_DATA_DIR", str(tmp_path))
    assert deploy_drain.main(["on"]) == 0
    assert deploy_drain.is_draining(tmp_path)
    assert deploy_drain.main(["status"]) == 0
    assert deploy_drain.main(["off"]) == 0
    assert not deploy_drain.is_draining(tmp_path)
    printed = capsys.readouterr().out.splitlines()
    assert "not draining" not in printed[1] and printed[2].endswith("not draining")
