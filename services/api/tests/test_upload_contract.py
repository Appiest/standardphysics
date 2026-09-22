"""The upload contract Lane A codes against, as services/api/openapi.json states it."""

import json
import pathlib
import threading

from conftest import REPO, create_scan, drain, put_artifact, unknown_scan, usdz_fixture

OPENAPI = json.loads((REPO / "services/api/openapi.json").read_text())


def test_every_documented_operation_exists(client):
    served = {(route.path, method.lower()) for route in client.app.routes for method in getattr(route, "methods", [])}
    for path, operations in OPENAPI["paths"].items():
        for method in operations:
            assert (path, method) in served, f"{method.upper()} {path} is documented but not served"


def test_creating_a_scan_returns_it_uploading(client):
    scan_id = create_scan(client)
    scan = client.get(f"/api/scans/{scan_id}").json()
    assert scan["state"] == "uploading"
    assert scan["artifacts"] == []
    assert scan["id"] in [s["id"] for s in client.get("/api/scans").json()["scans"]]


def test_an_upload_is_stored_once(client):
    scan_id = create_scan(client)
    first = put_artifact(client, scan_id, "room-json", b'{"walls": []}', "room_json")
    again = put_artifact(client, scan_id, "room-json", b'{"walls": []}', "room_json")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == again.json()
    assert len(client.get(f"/api/scans/{scan_id}").json()["artifacts"]) == 1


def test_a_wrong_checksum_is_rejected_and_nothing_is_kept(client):
    scan_id = create_scan(client)
    response = put_artifact(client, scan_id, "room-json", b"{}", "room_json", checksum="0" * 64)
    assert response.status_code == 400
    assert response.json() == {"error": "checksum mismatch"}
    assert client.get(f"/api/scans/{scan_id}").json()["artifacts"] == []


def test_the_same_id_with_different_bytes_conflicts(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    assert put_artifact(client, scan_id, "room-json", b'{"x": 1}', "room_json").status_code == 409


def test_an_artifact_id_cannot_climb_out_of_the_store(client):
    scan_id = create_scan(client)
    for artifact_id in ["..", ".hidden", "a b", "%2E%2E"]:
        response = put_artifact(client, scan_id, artifact_id, b"x", "frames")
        assert response.status_code in (400, 404, 405), artifact_id
    store_root = client.app.state.store.root
    assert not any(p.name == "x" for p in pathlib.Path(store_root).parent.iterdir())


def test_an_artifact_over_the_cap_is_refused(client):
    scan_id = create_scan(client)
    assert put_artifact(client, scan_id, "walkthrough", b"0" * 5_000_001, "walkthrough_mp4").status_code == 413


def test_uploading_to_an_unknown_scan_is_not_found(client):
    assert put_artifact(client, unknown_scan(), "room-json", b"{}", "room_json").status_code == 404


def test_missing_headers_are_a_bad_request(client):
    scan_id = create_scan(client)
    response = client.put(f"/api/scans/{scan_id}/artifacts/room-json", content=b"{}")
    assert response.status_code == 400


def test_finalizing_without_the_room_names_what_is_missing(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    response = client.post(f"/api/scans/{scan_id}/complete")
    assert response.status_code == 409
    assert response.json() == {"error": "missing artifacts", "need": ["room_usdz"]}


def _ready_to_finalize(client) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", b"{}", "room_json")
    put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
    return scan_id


def _job_count(client, scan_id: str) -> int:
    with client.app.state.database.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM jobs WHERE scan_id = ?", (scan_id,)).fetchone()[0]


def test_finalizing_twice_queues_one_job(client):
    scan_id = _ready_to_finalize(client)
    first = client.post(f"/api/scans/{scan_id}/complete")
    second = client.post(f"/api/scans/{scan_id}/complete")
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["state"] == "measuring"
    assert _job_count(client, scan_id) == 1


def test_concurrent_finalize_calls_queue_one_job(client):
    scan_id = _ready_to_finalize(client)
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(client.post(f"/api/scans/{scan_id}/complete").status_code))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert codes == [200] * 6
    assert _job_count(client, scan_id) == 1


def test_the_walkthrough_can_keep_uploading_after_finalize(client):
    scan_id = _ready_to_finalize(client)
    client.post(f"/api/scans/{scan_id}/complete")
    assert put_artifact(client, scan_id, "walkthrough", b"mp4", "walkthrough_mp4").status_code == 201


def test_lane_a_coverage_dictionary_becomes_scan_coverage(client):
    scan_id = _ready_to_finalize(client)
    node = "0EEA0751-0F43-5356-B188-22A6DA702457"
    coverage = {node: {"observed_fraction": 0.82, "viewpoint_count": 3}}
    put_artifact(client, scan_id, "coverage", json.dumps(coverage).encode(), "coverage")
    scan = client.post(f"/api/scans/{scan_id}/complete").json()
    assert scan["coverage"] == [{"node_id": node.lower(), "observed_fraction": 0.82, "viewpoint_count": 3}]
    assert scan["content_hash"]


def test_a_failed_job_marks_the_scan_failed(client):
    scan_id = _ready_to_finalize(client)
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "failed"


def test_no_response_carries_a_server_path(client):
    scan_id = _ready_to_finalize(client)
    body = json.dumps(client.get(f"/api/scans/{scan_id}").json())
    assert str(client.app.state.store.root) not in body
