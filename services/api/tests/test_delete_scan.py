"""Deleting a scan removes its rows and its files."""
import hashlib
import uuid

import pytest

from conftest import REPO, create_scan, drain, put_artifact, usdz_fixture
from standardphysics_api import repository as repo
from standardphysics_api import repository_jobs as jobs_repo


def test_delete_removes_the_scan(client):
    scan_id = create_scan(client)
    client.put(
        f"/api/scans/{scan_id}/artifacts/room.json",
        content=b"{}",
        headers={"X-Artifact-Kind": "room_json", "X-Checksum-SHA256": hashlib.sha256(b"{}").hexdigest()},
    )
    assert client.get(f"/api/scans/{scan_id}").status_code == 200
    assert client.delete(f"/api/scans/{scan_id}").status_code == 204
    assert client.get(f"/api/scans/{scan_id}").status_code == 404
    assert scan_id not in [s["id"] for s in client.get("/api/scans").json()["scans"]]


def test_deleting_twice_is_not_found(client):
    scan_id = create_scan(client)
    assert client.delete(f"/api/scans/{scan_id}").status_code == 204
    assert client.delete(f"/api/scans/{scan_id}").status_code == 404


def test_deleting_a_scan_that_never_existed(client):
    assert client.delete(f"/api/scans/{uuid.uuid4()}").status_code == 404


def test_a_processed_scan_can_be_deleted(client):
    """Processing leaves job attempts behind, and they must not hold the scan in place."""
    phone = REPO / "datasets/phone/test1"
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", (phone / "room.json").read_bytes(), "room_json")
    put_artifact(client, scan_id, "room-usdz", (phone / "room.usdz").read_bytes(), "room_usdz")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)

    assert client.delete(f"/api/scans/{scan_id}").status_code == 204
    assert client.get(f"/api/scans/{scan_id}").status_code == 404


def _measuring_scan(client) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert client.post(f"/api/scans/{scan_id}/complete").json()["state"] == "measuring"
    return scan_id


def _stored(client, scan_id: str) -> bool:
    with client.app.state.database.connect() as connection:
        return repo.scan_exists(connection, uuid.UUID(scan_id))


def test_a_shop_deleted_while_it_is_measured_goes_when_the_job_ends(client, monkeypatch):
    scan_id = _measuring_scan(client)
    seen_during_the_job = {}

    def measure_while_the_owner_deletes(scan_id, revision, job=None):
        seen_during_the_job["delete"] = client.delete(f"/api/scans/{scan_id}").status_code
        seen_during_the_job["open"] = client.get(f"/api/scans/{scan_id}").status_code
        seen_during_the_job["listed"] = str(scan_id) in [s["id"] for s in client.get("/api/scans").json()["scans"]]
        seen_during_the_job["kept_for_the_worker"] = _stored(client, str(scan_id))
        return True

    monkeypatch.setattr(client.app.state.worker, "_process", measure_while_the_owner_deletes)
    drain(client)

    assert seen_during_the_job == {"delete": 204, "open": 404, "listed": False, "kept_for_the_worker": True}
    assert not _stored(client, scan_id)


def test_a_deleted_shop_whose_job_was_cut_off_is_deleted_when_the_job_comes_back(client, monkeypatch):
    scan_id = _measuring_scan(client)
    with client.app.state.database.transaction() as connection:
        jobs_repo.claim_job(connection)
    assert client.delete(f"/api/scans/{scan_id}").status_code == 204
    assert _stored(client, scan_id)

    with client.app.state.database.transaction() as connection:
        jobs_repo.requeue_interrupted_jobs(connection)
    monkeypatch.setattr(client.app.state.worker, "_process", lambda **_: pytest.fail("a deleted shop was measured"))
    drain(client)

    assert not _stored(client, scan_id)
