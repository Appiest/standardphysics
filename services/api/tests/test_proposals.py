from conftest import drain
from standardphysics_fixtures import node_id


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    findings = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]
    return client, scan_id, findings


def test_the_fix_agent_proposes_moving_the_display_cases(make_client):
    client, scan_id, findings = _sample(make_client)
    aisle = next(f for f in findings if f["title"] == "The path to the counter is too narrow")
    result = client.post(f"/api/scans/{scan_id}/proposals", json={"base_revision": 0, "finding_ids": [aisle["id"]]}).json()
    assert result["proposal"] is not None
    moved = {move["node_id"] for move in result["proposal"]["moves"]}
    assert moved <= {str(node_id("case_east")), str(node_id("case_west"))}
    assert result["proposal"]["inventory_before"] == result["proposal"]["inventory_after"]
    assert result["message"]


def test_the_lowered_section_passes_and_the_register_is_the_counter_finding(make_client):
    _, _, findings = _sample(make_client)
    outcomes = {f["title"]: f["outcome"] for f in findings}
    assert outcomes["The ordering counter has a section you can order from"] == "passes"
    assert outcomes["People pay at the high counter"] == "problem"


def test_an_unknown_finding_is_a_bad_request(make_client):
    client, scan_id, _ = _sample(make_client)
    response = client.post(
        f"/api/scans/{scan_id}/proposals",
        json={"base_revision": 0, "finding_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert response.status_code == 400
