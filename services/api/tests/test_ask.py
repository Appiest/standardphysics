"""The ask box, through the API, on the sample shop and on a real scan."""

import threading
import time

from conftest import REPO, create_scan, drain, put_artifact


def _sample(make_client):
    client = make_client(seed=True, team=True).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def test_a_question_gets_a_sentence_and_a_place_to_look(make_client):
    client, scan_id = _sample(make_client)
    answer = client.post(f"/api/scans/{scan_id}/ask", json={"base_revision": 0, "text": "how many chairs do I have?"}).json()
    assert answer["understood"] is True
    assert answer["text"] == "You have six chairs."


def test_a_question_it_cannot_read_says_what_it_can_answer(make_client):
    client, scan_id = _sample(make_client)
    answer = client.post(f"/api/scans/{scan_id}/ask", json={"base_revision": 0, "text": "sing me a song"}).json()
    assert answer["understood"] is False
    assert answer["text"]


def test_an_empty_question_is_refused(make_client):
    client, scan_id = _sample(make_client)
    assert client.post(f"/api/scans/{scan_id}/ask", json={"base_revision": 0, "text": ""}).status_code == 400


def test_a_real_scan_without_a_route_can_still_be_asked_about(team_client):
    phone = REPO / "datasets/phone/ravida"
    scan_id = create_scan(team_client)
    put_artifact(team_client, scan_id, "room-json", (phone / "room.json").read_bytes(), "room_json")
    put_artifact(team_client, scan_id, "room-usdz", (phone / "room.usdz").read_bytes(), "room_usdz")
    team_client.post(f"/api/scans/{scan_id}/complete")
    drain(team_client)
    answer = team_client.post(f"/api/scans/{scan_id}/ask", json={"base_revision": 0, "text": "how many tables are there?"}).json()
    assert answer["understood"] is True
    assert answer["text"] == "You have three tables."


def test_a_fix_search_does_not_hold_up_a_layout_check(make_client):
    """Audit A-45: the fix search has its own measurement cache and lock."""
    client, scan_id = _sample(make_client)
    stages = client.app.state.worker.stages
    release = threading.Event()
    stages._search_lock.acquire()
    try:
        started = time.perf_counter()
        checked = client.post(f"/api/scans/{scan_id}/layout-checks", json={"base_revision": 0, "sequence": 1, "moves": []})
        assert checked.status_code == 200
        assert time.perf_counter() - started < 60
        assert stages.search_measure is not stages.measure
    finally:
        stages._search_lock.release()
        release.set()
