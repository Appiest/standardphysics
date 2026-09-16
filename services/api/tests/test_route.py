"""A route for a real scan: suggested from its geometry, then confirmed by the owner."""

import json

from conftest import FIXTURE_DATA, REPO, create_scan, drain, put_artifact

PHONE = REPO / "datasets/phone"


def _real_scan(client, name: str) -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", (PHONE / name / "room.json").read_bytes(), "room_json")
    put_artifact(client, scan_id, "room-usdz", (PHONE / name / "room.usdz").read_bytes(), "room_usdz")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return scan_id


def test_a_phone_scan_without_a_route_is_checked_for_everything_but_its_route(client):
    from standardphysics_agents import load_pack

    from standardphysics_api.stages import ROUTE_SUBJECTS

    route_rules = {rule.id for rule in load_pack().rules if ROUTE_SUBJECTS.intersection(rule.applies_to)}
    scan_id = _real_scan(client, "ravida")
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "ready"
    assert client.get(f"/api/scans/{scan_id}/scenario").status_code == 404
    assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
    tier_one_without_route = [rule for rule in load_pack().within_tier(1) if rule.id not in route_rules]
    assert assessment["rules_checked"] == len(tier_one_without_route)
    assert not {finding["check_id"] for finding in assessment["findings"]} & route_rules


def test_the_suggestion_has_five_stops_on_open_floor(client):
    from standardphysics_contracts import SceneGraph
    from standardphysics_pipeline import build_grid

    scan_id = _real_scan(client, "test1")
    suggestion = client.get(f"/api/scans/{scan_id}/scenario/suggestion").json()
    assert [stop["name"] for stop in suggestion["stops"]] == ["Entrance", "Counter", "Pickup", "Seat", "Exit"]
    grid = build_grid(SceneGraph.model_validate(client.get(f"/api/scans/{scan_id}/scene").json()))
    for stop in suggestion["stops"]:
        assert not grid.occupied[grid.to_cell(stop["position"]["x"], stop["position"]["y"])], stop["name"]
    assert client.get(f"/api/scans/{scan_id}/scenario").status_code == 404


def test_confirming_a_route_checks_the_scan(client):
    scan_id = _real_scan(client, "ravida")
    route = client.get(f"/api/scans/{scan_id}/scenario/suggestion").json()
    confirmed = client.put(f"/api/scans/{scan_id}/scenario", json=route)
    assert confirmed.status_code == 200
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "checking"
    drain(client)
    assessment = client.get(f"/api/scans/{scan_id}/assessment").json()
    assert assessment["graph_revision"] == 0
    assert client.get(f"/api/scans/{scan_id}").json()["state"] == "ready"


def test_moving_a_stop_checks_again(make_client):
    with make_client(seed=True) as client:
        drain(client)
        scan_id = client.get("/api/scans").json()["scans"][0]["id"]
        first = client.get(f"/api/scans/{scan_id}/assessment").json()["created_at"]
        route = client.get(f"/api/scans/{scan_id}/scenario").json()
        route["stops"][3]["position"]["x"] += 0.3
        client.put(f"/api/scans/{scan_id}/scenario", json=route)
        drain(client)
        assert client.get(f"/api/scans/{scan_id}/scenario").json() == route
        assert client.get(f"/api/scans/{scan_id}/assessment").json()["created_at"] != first


def test_a_route_needs_two_stops(client):
    scan_id = _real_scan(client, "ravida")
    response = client.put(
        f"/api/scans/{scan_id}/scenario",
        json={"name": "Order a drink", "stops": [{"name": "Entrance", "position": {"x": 0, "y": 0, "z": 0}}]},
    )
    assert response.status_code == 400


def test_the_fixture_data_is_used_for_the_sample_only():
    assert (FIXTURE_DATA / "shop.scene_graph.json").exists()
    assert json.loads((PHONE / "ravida" / "scan.json").read_text())
