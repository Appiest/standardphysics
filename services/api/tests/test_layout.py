"""Dragging furniture: re-checks while moving, and saving a layout."""

from conftest import drain
from standardphysics_contracts import to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, node_id

CASE_EAST = str(node_id("case_east"))
COUNTER = str(node_id("counter"))


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _move(node: str, dx: float = 0.0, dy: float = 0.0, degrees: float = 0.0) -> dict:
    return {"node_id": node, "delta_translation": {"x": dx, "y": dy, "z": 0.0}, "delta_rotation_z_degrees": degrees}


def _aisle(findings):
    return next(f for f in findings if f["title"] == "The path to the counter is too narrow" or "counter is too narrow" in f["title"])


def test_the_documented_fix_clears_the_aisle(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    result = client.post(f"/api/scans/{scan_id}/layout-checks", json={"base_revision": 0, "sequence": 3, "moves": [fix]})
    body = result.json()
    assert result.status_code == 200
    assert body["sequence"] == 3
    assert body["blocked"] == []
    route = next(f for f in body["findings"] if f["check_id"] == "route_clear_width" and f["measured_inches"] and round(f["measured_inches"]) == 36)
    assert route["outcome"] == "passes"


def test_moving_the_counter_is_blocked(make_client):
    client, scan_id = _sample(make_client)
    body = client.post(
        f"/api/scans/{scan_id}/layout-checks",
        json={"base_revision": 0, "sequence": 1, "moves": [_move(COUNTER, dy=-0.2)]},
    ).json()
    assert {b["reason"] for b in body["blocked"]} >= {"moved_something_fixed"}


def test_an_unknown_node_is_a_bad_request(make_client):
    client, scan_id = _sample(make_client)
    response = client.post(
        f"/api/scans/{scan_id}/layout-checks",
        json={"base_revision": 0, "sequence": 1, "moves": [_move("00000000-0000-0000-0000-000000000000")]},
    )
    assert response.status_code == 400


def test_saving_a_layout_makes_a_new_revision_and_reassesses(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    saved = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [fix]})
    assert saved.status_code == 201
    assert saved.json()["revision"] == 1
    drain(client)
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 1
    assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
    assert assessment["graph_revision"] == 1


def test_saving_on_a_stale_base_conflicts(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [fix]})
    stale = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [fix]})
    assert stale.status_code == 409


def test_a_blocked_layout_cannot_be_saved(make_client):
    client, scan_id = _sample(make_client)
    response = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [_move(COUNTER, dy=-0.2)]})
    assert response.status_code == 409
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0


def test_each_revision_keeps_its_own_assessment(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [fix]})
    drain(client)
    before = client.get(f"/api/scans/{scan_id}/assessment?revision=0").json()
    after = client.get(f"/api/scans/{scan_id}/assessment?revision=1").json()
    assert (before["graph_revision"], after["graph_revision"]) == (0, 1)
    assert before["graph_hash"] != after["graph_hash"]
