"""room_usdz receipt validation (negative G02 case, K/C request).

The server used to accept any bytes as a usdz export and only learn the truth
deep inside a later job. Now the staging path parses the archive, so a bad
capture is a specific 400 at upload time and a pristine one still lands.
"""

import io
import zipfile

from conftest import create_scan, put_artifact, usdz_fixture


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_valid_usdz_is_accepted_and_uploaded(client):
    scan_id = create_scan(client)
    response = put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "room_usdz"


def test_malformed_usdz_garbage_is_rejected_at_upload(client):
    scan_id = create_scan(client)
    response = put_artifact(client, scan_id, "room-usdz", b"usdz", "room_usdz")
    assert response.status_code == 400, response.text
    assert "invalid usdz" in response.text


def test_zip_without_a_usd_payload_entry_is_rejected(client):
    scan_id = create_scan(client)
    payload = _zip_with({"photo.png": b"\x89PNG\r\n\x1a\n"})
    response = put_artifact(client, scan_id, "room-usdz", payload, "room_usdz")
    assert response.status_code == 400, response.text
    assert "invalid usdz archive" in response.text


def test_usdz_with_unsafe_entry_paths_is_rejected(client):
    scan_id = create_scan(client)
    payload = _zip_with({"../model.usdc": b"#usda 1.0\n"})
    response = put_artifact(client, scan_id, "room-usdz", payload, "room_usdz")
    assert response.status_code == 400, response.text
    assert "invalid usdz archive" in response.text


def test_accepted_usdz_archive_parses_and_reaches_completion(client):
    scan_id = create_scan(client)
    payload = _zip_with({"model.usdc": b"#usda 1.0\n", "textures/tex.png": b"\x89PNG\r\n\x1a\n"})
    assert put_artifact(client, scan_id, "room-usdz", payload, "room_usdz").status_code == 201
    assert put_artifact(client, scan_id, "room-json", b'{"walls": []}', "room_json").status_code == 201


def _bomb(expanded_mib: int) -> bytes:
    """A usd entry of zeros that deflates a thousandfold, so a small upload claims a huge room."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        with archive.open("model.usdc", "w", force_zip64=True) as entry:
            megabyte = bytes(1 << 20)
            for _ in range(expanded_mib):
                entry.write(megabyte)
    return buffer.getvalue()


def test_usdz_that_expands_past_the_limit_is_rejected(client):
    scan_id = create_scan(client)
    payload = _bomb(257)
    assert len(payload) < 1 << 20
    response = put_artifact(client, scan_id, "room-usdz", payload, "room_usdz")
    assert response.status_code == 400, response.text
    assert "invalid usdz archive" in response.text


def test_usdz_with_too_many_entries_is_rejected(client):
    scan_id = create_scan(client)
    payload = _zip_with({"model.usdc": b"#usda 1.0\n"} | {f"textures/{n}.png": b"" for n in range(1000)})
    response = put_artifact(client, scan_id, "room-usdz", payload, "room_usdz")
    assert response.status_code == 400, response.text
    assert "invalid usdz archive" in response.text
