"""Approach endpoint, frozen by K around R's conservative evaluator.

The owner confirms a route, picks a target, and gets one measured screening
answer. Nothing passes by default: without a person-provided horizontal reach
the report says unmeasured and the status stays bounded.
"""

from conftest import drain, put_artifact
from test_review_safety import (
    OUTLET_ID,
    TABLE_ID,
    _ready_scan,
    _room_payload,
    _stages,
    _two_targets,
)


def _confirm_route(client, scan_id) -> None:
    scenario = {
        "name": "Order a drink",
        "stops": [
            {"name": "Entrance", "position": {"x": 0.0, "y": -2.0, "z": 0.0}, "anchor_node_id": None},
            {"name": "Counter", "position": {"x": 0.0, "y": 1.0, "z": 0.0}, "anchor_node_id": str(TABLE_ID)},
        ],
    }
    confirmed = client.put(f"/api/scans/{scan_id}/scenario", json=scenario)
    assert confirmed.status_code == 200, confirmed.text


def _post(client, scan_id, revision, **overrides):
    body = {"target_node_id": str(OUTLET_ID), "occupant_profile": "manual-wheelchair", **overrides}
    return client.post(f"/api/scans/{scan_id}/revisions/{revision}/approach", json=body)


def test_approach_needs_a_confirmed_route(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        response = _post(client, scan_id, 0)
        assert response.status_code == 409
        assert "route" in response.json()["error"]


def test_unmeasured_horizontal_reach_never_reads_clear(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        _confirm_route(client, scan_id)
        response = _post(client, scan_id, 0)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["target_id"] == str(OUTLET_ID)
        assert report["status"] in ("blocked", "needs_verification")
        reaches = report["reaches"]
        assert reaches, "a reach row must exist"
        assert reaches[0]["horizontal_status"] == "unmeasured"
        assert reaches[0]["horizontal_reach_provenance"] is None
        assert report["unverified"], "the unmeasured remainder stays visible"


def test_a_person_provided_reach_rides_with_its_provenance(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        _confirm_route(client, scan_id)
        response = _post(
            client, scan_id, 0,
            horizontal_reach_inches=18.0,
            horizontal_reach_provenance="owner measured sideways grasp",
            approach_stop={"x": 0.0, "y": 0.0, "z": 0.0},
        )
        assert response.status_code == 200, response.text
        reaches = response.json()["reaches"]
        assert reaches[0]["horizontal_reach_inches"] == 18.0
        assert reaches[0]["horizontal_reach_provenance"] == "owner measured sideways grasp"
        assert reaches[0]["horizontal_status"] in ("within_horizontal_reach", "exceeded_horizontal_reach")


def test_a_reach_without_provenance_is_a_clear_400(make_client):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        _confirm_route(client, scan_id)
        response = _post(client, scan_id, 0, horizontal_reach_inches=18.0)
        assert response.status_code == 400
        assert "provenance" in response.json()["error"]


def test_stale_revision_and_unknown_target_are_explicit(make_client, stranger):
    client, scan_id = _ready_scan(make_client, _two_targets)
    with client:
        _confirm_route(client, scan_id)
        stale = _post(client, scan_id, 1)
        assert stale.status_code == 409
        missing = _post(client, scan_id, 0, target_node_id="00000000-0000-0000-0000-000000000099")
        assert missing.status_code == 404
        with stranger as other:
            assert _post(other, scan_id, 0).status_code == 404
