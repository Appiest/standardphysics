import hashlib
import json

from conftest import create_scan, put_artifact, unknown_scan, usdz_fixture


def mesh_bytes(**overrides) -> bytes:
    mesh = {
        "parts": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
            "triangles": [0, 1, 2],
        }],
        **overrides,
    }
    return json.dumps(mesh, separators=(",", ":")).encode()


def test_lidar_mesh_upload_and_get_preserve_original_bytes(client):
    scan_id = create_scan(client)
    body = mesh_bytes(peopleFilteringEnabled=True, floorY=-0.25)
    uploaded = put_artifact(client, scan_id, "lidar-mesh", body, "lidar_mesh")

    assert uploaded.status_code == 201
    fetched = client.get(f"/api/scans/{scan_id}/lidar-mesh")
    assert fetched.status_code == 200
    assert fetched.content == body
    assert uploaded.json()["sha256"] == hashlib.sha256(body).hexdigest()


def test_invalid_lidar_mesh_is_rejected_before_storage(client):
    scan_id = create_scan(client)
    malformed = json.dumps({
        "parts": [{
            "id": "00000000-0000-0000-0000-000000000001",
            "transform": [1] * 16,
            "vertices": [0, 0, 0],
            "triangles": [0, 1, 2],
        }]
    }).encode()
    response = put_artifact(client, scan_id, "lidar-mesh", malformed, "lidar_mesh")

    assert response.status_code == 400
    assert client.get(f"/api/scans/{scan_id}").json()["artifacts"] == []


def test_missing_lidar_mesh_is_not_ready(client):
    scan_id = create_scan(client)
    assert client.get(f"/api/scans/{scan_id}/lidar-mesh").status_code == 404
    assert client.get(f"/api/scans/{unknown_scan()}/lidar-mesh").status_code == 404


def test_lidar_mesh_can_arrive_after_scan_completion(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200

    body = mesh_bytes()
    assert put_artifact(client, scan_id, "lidar-mesh", body, "lidar_mesh").status_code == 201
    assert client.get(f"/api/scans/{scan_id}/lidar-mesh").content == body


def test_reusing_artifact_id_with_a_different_kind_conflicts(client):
    scan_id = create_scan(client)
    body = mesh_bytes()
    put_artifact(client, scan_id, "same", body, "lidar_mesh")
    response = put_artifact(client, scan_id, "same", body, "frames")
    assert response.status_code == 409
