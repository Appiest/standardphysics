"""The owner says what to keep, every later proposal is held to it, and each proposal explains itself."""

from conftest import drain


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    findings = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]
    aisle = next(f for f in findings if f["title"] == "The path to the counter is too narrow")
    return client, scan_id, aisle["id"]


def _propose(client, scan_id, finding_id):
    return client.post(f"/api/scans/{scan_id}/proposals", json={"base_revision": 0, "finding_ids": [finding_id]}).json()


def test_a_scan_has_no_wishes_until_the_owner_saves_some(make_client):
    client, scan_id, _ = _sample(make_client)
    assert client.get(f"/api/scans/{scan_id}").json()["owner_wishes"] == []
    wish = {"kind": "stays_put", "node_id": "00000000-0000-0000-0000-000000000001", "text": "Keep the case"}
    saved = client.put(f"/api/scans/{scan_id}/owner-wishes", json={"wishes": [wish]}).json()
    assert saved["owner_wishes"][0]["kind"] == "stays_put" and saved["owner_wishes"][0]["text"] == "Keep the case"
    assert client.put(f"/api/scans/{scan_id}/owner-wishes", json={"wishes": []}).json()["owner_wishes"] == []


def test_a_near_wish_without_an_anchor_is_refused(make_client):
    client, scan_id, _ = _sample(make_client)
    wish = {"kind": "stays_near", "node_id": "00000000-0000-0000-0000-000000000001", "inches": 30}
    assert client.put(f"/api/scans/{scan_id}/owner-wishes", json={"wishes": [wish]}).status_code in (400, 422)


def test_a_proposal_explains_what_moved_and_what_it_fixed(make_client):
    client, scan_id, finding_id = _sample(make_client)
    result = _propose(client, scan_id, finding_id)
    explanation = result["explanation"]
    assert result["proposal"] is not None and explanation is not None
    assert len(explanation["moves"]) == len(result["proposal"]["moves"])
    assert any("ADA" in sentence for sentence in explanation["fixed"])
    assert all("[" not in sentence for sentence in explanation["moves"] + explanation["kept"])


def test_a_piece_the_owner_keeps_is_never_moved_by_the_next_proposal(make_client):
    client, scan_id, finding_id = _sample(make_client)
    first = _propose(client, scan_id, finding_id)
    held = first["proposal"]["moves"][0]["node_id"]
    wish = {"kind": "stays_put", "node_id": held, "text": "Keep this case where it is"}
    client.put(f"/api/scans/{scan_id}/owner-wishes", json={"wishes": [wish]})
    second = _propose(client, scan_id, finding_id)
    moved = {move["node_id"] for move in (second["proposal"] or {"moves": []})["moves"]}
    assert held not in moved


def test_every_bent_wish_comes_with_a_wish_the_owner_can_save_as_is(make_client):
    client, scan_id, finding_id = _sample(make_client)
    bent = _propose(client, scan_id, finding_id)["explanation"]["bent"]
    keeps = [item["keep"] for item in bent if item["keep"]]
    response = client.put(f"/api/scans/{scan_id}/owner-wishes", json={"wishes": keeps})
    assert response.status_code == 200 and len(response.json()["owner_wishes"]) == len(keeps)
