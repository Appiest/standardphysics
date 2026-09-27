"""Report links, the example shop, planned layouts and paths for any business."""

from datetime import UTC, datetime, timedelta

from standardphysics_contracts import to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, node_id

from conftest import create_scan, drain

CASE_EAST = str(node_id("case_east"))
COUNTER = str(node_id("counter"))


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _move(node: str, dx: float) -> dict:
    return {"node_id": node, "delta_translation": {"x": dx, "y": 0.0, "z": 0.0}, "delta_rotation_z_degrees": 0.0}


def test_a_shared_link_opens_the_report_without_an_account(make_client):
    client, scan_id = _sample(make_client)
    client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": False})
    link = client.post(f"/api/scans/{scan_id}/shares")
    assert link.status_code == 201
    path = link.json()["path"]
    assert path.startswith("/r/")
    expires = datetime.fromisoformat(link.json()["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(UTC) <= timedelta(days=30)
    token = path.removeprefix("/r/")
    with make_client(sign_in_as_owner=False) as contractor:
        report = contractor.get(f"/api/shared/{token}")
        assert report.status_code == 200
        body = report.json()
        assert body["scan"]["id"] == scan_id
        assert body["assessment"]["scope"] is None
        assert not any(f["check_id"] == "restroom_turning_space" for f in body["assessment"]["findings"])
        pictured = [f["locus"]["render_url"] for f in body["assessment"]["findings"] if f["locus"] and f["locus"]["render_url"]]
        assert all(url.startswith(f"/api/shared/{token}/renders/") for url in pictured)


def test_a_link_stops_working_once_it_expires_or_is_revoked(make_client):
    client, scan_id = _sample(make_client)
    token = client.post(f"/api/scans/{scan_id}/shares").json()["path"].removeprefix("/r/")
    with client.app.state.database.connect() as connection:
        connection.execute("UPDATE share_links SET expires_at = ?", ((datetime.now(UTC) - timedelta(days=1)).isoformat(),))
    expired = client.get(f"/api/shared/{token}")
    assert expired.status_code == 404
    assert expired.json()["error"] == "This link has expired. Ask the shop for a new one."
    fresh = client.post(f"/api/scans/{scan_id}/shares").json()["path"].removeprefix("/r/")
    assert client.delete(f"/api/scans/{scan_id}/shares").status_code == 204
    assert client.get(f"/api/shared/{fresh}").status_code == 404


def test_a_made_up_link_opens_nothing(client):
    assert client.get("/api/shared/made-up-token").status_code == 404


def test_a_stranger_cannot_share_someone_else_s_shop(client, stranger):
    scan_id = create_scan(client)
    assert stranger.post(f"/api/scans/{scan_id}/shares").status_code == 404


def test_the_example_shop_is_the_sample_and_needs_no_account(make_client):
    client, scan_id = _sample(make_client)
    with make_client(sign_in_as_owner=False) as visitor:
        example = visitor.get("/api/shared/example")
        assert example.status_code == 200
        assert example.json()["scan"]["id"] == scan_id
        assert visitor.head("/api/shared/example/scene.glb").status_code == 200


def test_there_is_no_example_on_a_server_without_the_sample(client):
    assert client.get("/api/shared/example").status_code == 404


def test_a_planned_layout_is_checked_and_leaves_the_scan_alone(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, to_meters(FIX_SHIFT_INCHES))
    plan = client.post(f"/api/scans/{scan_id}/plans", json={"base_revision": 0, "moves": [fix]})
    assert plan.status_code == 201
    assert plan.json()["name"] == "Layout 1"
    route = [f for f in plan.json()["findings"] if f["check_id"] == "route_clear_width" and f["measured_inches"]
             and round(f["measured_inches"]) == 36]
    assert route and route[0]["outcome"] == "passes"
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0
    assert [p["id"] for p in client.get(f"/api/scans/{scan_id}/plans").json()["plans"]] == [plan.json()["id"]]
    assert client.delete(f"/api/scans/{scan_id}/plans/{plan.json()['id']}").status_code == 204
    assert client.get(f"/api/scans/{scan_id}/plans").json()["plans"] == []


def test_a_plan_that_moves_something_fixed_is_refused(make_client):
    client, scan_id = _sample(make_client)
    refused = client.post(f"/api/scans/{scan_id}/plans", json={"base_revision": 0, "moves": [_move(COUNTER, 0.5)]})
    assert refused.status_code == 409


def test_a_path_goes_through_the_places_the_owner_picked(make_client):
    client, scan_id = _sample(make_client)
    path = client.get(f"/api/scans/{scan_id}/scenario/suggestion", params={"destinations": "seating,restroom"}).json()
    assert path["name"] == "Customer path"
    assert [stop["name"] for stop in path["stops"]] == ["Entrance", "Counter", "Seats", "Restroom", "Exit"]
    cafe = client.get(f"/api/scans/{scan_id}/scenario/suggestion").json()
    assert cafe["name"] == "Order a drink"
    assert client.get(f"/api/scans/{scan_id}/scenario/suggestion", params={"destinations": "moon"}).status_code == 400


def test_a_path_with_no_extra_places_goes_in_to_the_counter_and_out(make_client):
    client, scan_id = _sample(make_client)
    path = client.get(f"/api/scans/{scan_id}/scenario/suggestion", params={"destinations": ""}).json()
    assert [stop["name"] for stop in path["stops"]] == ["Entrance", "Counter", "Exit"]


def test_the_path_is_drawn_the_way_a_customer_walks_it(make_client):
    client, scan_id = _sample(make_client)
    path = client.get(f"/api/scans/{scan_id}/scenario/suggestion", params={"destinations": "seating"}).json()
    legs = client.post(f"/api/scans/{scan_id}/scenario/legs", json=path).json()["legs"]
    assert [(leg["from_stop"], leg["to_stop"]) for leg in legs] == [("Entrance", "Counter"), ("Counter", "Seats"), ("Seats", "Exit")]
    assert all(leg["reachable"] and len(leg["path"]) > 2 for leg in legs)
