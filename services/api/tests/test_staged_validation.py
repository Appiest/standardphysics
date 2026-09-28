"""A checked upload is sized before it is read and never outlives a refusal.

The kinds whose bytes are checked at upload (the LiDAR mesh, the photo
manifest and room.usdz) are compared against their own caps from the staged
file's size, and the archive is opened from the file rather than loaded. Any
way the upload can fail after staging leaves no temp file behind.
"""

import io
import pathlib
import sqlite3
import zipfile

import pytest

from conftest import create_scan, put_artifact, usdz_fixture
from standardphysics_api import repository
from standardphysics_api.textures import MAX_METADATA_BYTES
from standardphysics_api.usdz_validation import InvalidUsdz, validate_room_usdz


def staged_leftovers(test_client, scan_id: str) -> list[pathlib.Path]:
    artifacts = test_client.app.state.store.scan_dir(scan_id) / "artifacts"
    return sorted(artifacts.glob(".upload-*")) if artifacts.exists() else []


def refuse_to_read_whole_files(monkeypatch) -> None:
    def refuse(self):
        raise AssertionError(f"read {self.name} whole")

    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse)


def test_an_oversized_photo_manifest_is_refused_without_being_read(client, monkeypatch):
    scan_id = create_scan(client)
    refuse_to_read_whole_files(monkeypatch)
    response = put_artifact(client, scan_id, "photo-manifest", b" " * (MAX_METADATA_BYTES + 1), "photo_manifest")
    assert response.status_code == 413, response.text
    assert staged_leftovers(client, scan_id) == []


def test_a_usdz_is_inspected_from_the_file_not_loaded(client, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model.usdc", "#usda 1.0\n")
    scan_id = create_scan(client)
    refuse_to_read_whole_files(monkeypatch)
    assert put_artifact(client, scan_id, "room-usdz", buffer.getvalue(), "room_usdz").status_code == 201


def test_a_usdz_path_that_is_not_an_archive_is_refused(tmp_path):
    path = tmp_path / "room.usdz"
    path.write_bytes(b"PK not really")
    with pytest.raises(InvalidUsdz):
        validate_room_usdz(path)


def test_a_mesh_nested_too_deep_to_parse_is_a_400_and_leaves_nothing(client):
    scan_id = create_scan(client)
    response = put_artifact(client, scan_id, "lidar-mesh", b"[" * 200_000, "lidar_mesh")
    assert response.status_code == 400, response.text
    assert staged_leftovers(client, scan_id) == []


def test_a_staged_file_is_removed_when_storing_it_fails(client, monkeypatch):
    scan_id = create_scan(client)

    def database_gone(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repository, "insert_artifact", database_gone)
    with pytest.raises(sqlite3.OperationalError):
        put_artifact(client, scan_id, "room-json", b'{"walls": []}', "room_json")
    assert staged_leftovers(client, scan_id) == []


def test_a_staged_file_is_removed_after_a_validation_failure(client):
    scan_id = create_scan(client)
    response = put_artifact(client, scan_id, "room-usdz", b"PK but not a zip", "room_usdz")
    assert response.status_code == 400, response.text
    assert staged_leftovers(client, scan_id) == []


def test_an_oversized_coverage_file_is_skipped_without_being_read(client, monkeypatch):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert put_artifact(client, scan_id, "coverage", b" " * (MAX_METADATA_BYTES + 1), "coverage").status_code == 201
    refuse_to_read_whole_files(monkeypatch)
    response = client.post(f"/api/scans/{scan_id}/complete")
    assert response.status_code == 200, response.text
    assert response.json()["coverage"] == []
