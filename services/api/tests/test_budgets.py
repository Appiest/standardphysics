"""Limits past a single scan: each owner, the job queue and the disk itself.

The per-scan quota stops one scan filling the volume, but not an account that
opens scan after scan, and not a hundred guests each opening one. These are the
limits that come after it. The team is exempt from the per-owner ones, because
its account holds every Moffitt-scale test walk.
"""

import pytest

from conftest import create_scan, put_artifact, usdz_fixture
from standardphysics_api.settings import Settings
from standardphysics_api.store import ArtifactStore

MOFFITT_FULL_FLOOR_BYTES = 2_519_287_146
"""The largest walk on file: 3,526 artifacts of the Moffitt library's full floor."""


def fill(client, scan_id: str, artifact_id: str, size: int):
    return put_artifact(client, scan_id, artifact_id, b"x" * size, "frames")


def finalize(client) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    return scan_id


def free_space(monkeypatch, free: int) -> None:
    monkeypatch.setattr(ArtifactStore, "free_bytes", lambda self: free)


def test_an_owner_can_open_only_so_many_scans(make_client):
    with make_client(max_owner_scans=2) as client:
        create_scan(client)
        create_scan(client)
        response = client.post("/api/scans", json={"name": "Third", "device_model": "iPhone17,1", "duration_seconds": 60.0})
    assert response.status_code == 403, response.text
    assert "2 scans" in response.json()["error"]


def test_the_team_is_not_held_to_the_owner_scan_limit(make_client):
    with make_client(team=True, max_owner_scans=1) as client:
        create_scan(client)
        create_scan(client)


def test_an_owner_byte_budget_spans_every_scan_they_hold(make_client):
    with make_client(max_owner_bytes=1000) as client:
        first, second = create_scan(client), create_scan(client)
        assert fill(client, first, "frame-0001", 600).status_code == 201
        response = fill(client, second, "frame-0001", 600)
        assert response.status_code == 413, response.text
        assert list((client.app.state.store.scan_dir(second) / "artifacts").glob(".upload-*")) == []


def test_an_owner_over_budget_is_refused_before_the_body_is_read(make_client):
    with make_client(max_owner_bytes=1000) as client:
        scan_id = create_scan(client)
        assert fill(client, scan_id, "frame-0001", 1000).status_code == 201
        response = fill(client, scan_id, "frame-0002", 1)
    assert response.status_code == 413, response.text
    assert "this account" in response.json()["error"]


def test_the_team_is_not_held_to_the_owner_byte_budget(make_client):
    with make_client(team=True, max_owner_bytes=1000) as client:
        first, second = create_scan(client), create_scan(client)
        assert fill(client, first, "frame-0001", 600).status_code == 201
        assert fill(client, second, "frame-0001", 600).status_code == 201


def test_a_full_queue_refuses_finalizing_with_a_retry(make_client):
    with make_client(max_queued_jobs=1) as client:
        waiting, blocked = finalize(client), finalize(client)
        assert client.post(f"/api/scans/{waiting}/complete").status_code == 200
        finalizing = client.post(f"/api/scans/{blocked}/complete")
        still_uploading = client.get(f"/api/scans/{blocked}").json()["state"]
    assert finalizing.status_code == 503, finalizing.text
    assert int(finalizing.headers["retry-after"]) > 0
    assert still_uploading == "uploading"


def test_low_disk_refuses_new_scans_and_uploads_with_507(make_client, monkeypatch):
    with make_client(min_free_disk_bytes=1_000_000) as client:
        scan_id = create_scan(client)
        free_space(monkeypatch, 999_999)
        creating = client.post("/api/scans", json={"name": "Another", "device_model": "iPhone17,1", "duration_seconds": 60.0})
        uploading = fill(client, scan_id, "frame-0001", 10)
        reading = client.get(f"/api/scans/{scan_id}")
    assert creating.status_code == 507, creating.text
    assert uploading.status_code == 507, uploading.text
    assert reading.status_code == 200


def test_an_upload_that_would_take_the_disk_below_the_floor_is_refused(make_client, monkeypatch):
    with make_client(min_free_disk_bytes=1_000_000) as client:
        scan_id = create_scan(client)
        free_space(monkeypatch, 1_000_050)
        assert fill(client, scan_id, "frame-0001", 10).status_code == 201
        assert fill(client, scan_id, "frame-0002", 100).status_code == 507


def test_the_defaults_fit_the_walks_on_file():
    settings = Settings()
    assert settings.max_owner_bytes >= 2 * MOFFITT_FULL_FLOOR_BYTES
    assert settings.max_owner_scans >= 10
    assert settings.min_free_disk_bytes >= settings.max_artifact_bytes


@pytest.mark.parametrize(
    ("variable", "field", "value"),
    [
        ("SP_MAX_OWNER_SCANS", "max_owner_scans", 7),
        ("SP_MAX_OWNER_BYTES", "max_owner_bytes", 123_456_789),
        ("SP_MAX_QUEUED_JOBS", "max_queued_jobs", 11),
        ("SP_MIN_FREE_DISK_BYTES", "min_free_disk_bytes", 2_000_000_000),
    ],
)
def test_each_budget_is_configurable(monkeypatch, variable, field, value):
    monkeypatch.setenv(variable, str(value))
    assert getattr(Settings.from_environment(), field) == value
