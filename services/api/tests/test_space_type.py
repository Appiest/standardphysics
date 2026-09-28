"""The owner says what kind of space a scan is, and the fix paths pass it to the directive veto."""

from standardphysics_agents import LocalPolicyRouter
from standardphysics_contracts import SpaceTypology

from conftest import drain, no_blender_stages

BOBA = SpaceTypology.QSR_BEVERAGE.value


def _sample(make_client):
    client = make_client(seed=True, team=True, stages=no_blender_stages(router_factory=LocalPolicyRouter)).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def test_a_new_scan_can_say_what_it_is(client):
    body = {"name": "Shop", "device_model": "iPhone16,1", "duration_seconds": 60, "space_typology": BOBA}
    created = client.post("/api/scans", json=body).json()
    assert created["space_typology"] == BOBA
    assert client.get(f"/api/scans/{created['id']}").json()["space_typology"] == BOBA


def test_a_scan_says_nothing_until_told(client):
    created = client.post("/api/scans", json={"name": "Shop", "device_model": "iPhone16,1", "duration_seconds": 60}).json()
    assert created["space_typology"] is None


def test_the_space_type_can_be_set_and_cleared(make_client):
    client, scan_id = _sample(make_client)
    assert client.put(f"/api/scans/{scan_id}/space-type", json={"space_typology": BOBA}).json()["space_typology"] == BOBA
    assert client.put(f"/api/scans/{scan_id}/space-type", json={"space_typology": None}).json()["space_typology"] is None


def test_an_unknown_space_type_is_refused(make_client):
    client, scan_id = _sample(make_client)
    assert client.put(f"/api/scans/{scan_id}/space-type", json={"space_typology": "castle"}).status_code == 400


def _record_typologies(monkeypatch) -> list:
    import standardphysics_api.stages as stages_module

    seen = []
    real = stages_module.rejection_for_space

    def recording(typology, graph, directives=None):
        seen.append(typology)
        return real(typology, graph, directives)

    monkeypatch.setattr(stages_module, "rejection_for_space", recording)
    return seen


def test_the_loop_asks_for_the_scans_directives(make_client, monkeypatch):
    client, scan_id = _sample(make_client)
    client.put(f"/api/scans/{scan_id}/space-type", json={"space_typology": BOBA})
    seen = _record_typologies(monkeypatch)
    assert client.post(f"/api/scans/{scan_id}/loop", json={"base_revision": 0}).status_code == 200
    assert seen == [SpaceTypology.QSR_BEVERAGE]


def test_a_proposal_asks_for_the_scans_directives(make_client, monkeypatch):
    client, scan_id = _sample(make_client)
    client.put(f"/api/scans/{scan_id}/space-type", json={"space_typology": BOBA})
    finding = client.get(f"/api/scans/{scan_id}/assessment").json()["findings"][0]
    seen = _record_typologies(monkeypatch)
    client.post(f"/api/scans/{scan_id}/proposals", json={"base_revision": 0, "finding_ids": [finding["id"]]})
    assert seen == [SpaceTypology.QSR_BEVERAGE]
