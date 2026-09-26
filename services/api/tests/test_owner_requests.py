"""What the app asks the owner for, what they answer, and what that changes."""

import io

from PIL import Image

from conftest import create_scan, drain

IN_SHOP = ["restroom", "inside_doors", "entrance_threshold", "door_hardware", "floor_surface",
           "restroom_turning_space", "door_opening_force"]


def _sample(make_client, **settings):
    client = make_client(seed=True, **settings).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _requests(client, scan_id):
    return {request["id"]: request for request in client.get(f"/api/scans/{scan_id}/requests").json()["requests"]}


def _findings(client, scan_id):
    return client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]


def _finding(client, scan_id, check_id):
    return next(finding for finding in _findings(client, scan_id) if finding["check_id"] == check_id)


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_the_in_shop_questions_exist_before_the_shop_is_measured(client):
    scan_id = create_scan(client)
    requests = client.get(f"/api/scans/{scan_id}/requests").json()["requests"]
    assert [request["id"] for request in requests] == IN_SHOP
    assert {request["status"] for request in requests} == {"open"}
    assert requests[-1]["unit"] == "lb"


def test_a_no_to_the_restroom_closes_the_restroom_photo_and_its_finding(make_client):
    client, scan_id = _sample(make_client)
    assert any(f["check_id"] == "restroom_turning_space" for f in _findings(client, scan_id))
    answer = client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": False})
    assert answer.status_code == 200
    assert answer.json()["status"] == "checked"
    assert _requests(client, scan_id)["restroom_turning_space"]["status"] == "not_applicable"
    assert not any(f["check_id"] == "restroom_turning_space" for f in _findings(client, scan_id))


def test_a_door_push_over_the_limit_is_a_problem_and_under_it_passes(make_client):
    client, scan_id = _sample(make_client)
    client.put(f"/api/scans/{scan_id}/requests/door_opening_force/answer", json={"number": 8})
    heavy = _finding(client, scan_id, "door_opening_force")
    assert heavy["outcome"] == "problem"
    assert heavy["title"] == "The inside doors are too hard to push open"
    assert heavy["fix"]
    client.put(f"/api/scans/{scan_id}/requests/door_opening_force/answer", json={"number": 4.5})
    assert _finding(client, scan_id, "door_opening_force")["outcome"] == "passes"


def test_a_measured_doorway_is_checked_against_the_rule(make_client):
    client, scan_id = _sample(make_client)
    follow_up = next(r for r in _requests(client, scan_id).values() if r["timing"] == "follow_up" and r["kind"] == "number")
    assert follow_up["unit"] == "in"
    client.put(f"/api/scans/{scan_id}/requests/{follow_up['id']}/answer", json={"number": 30})
    doorway = next(f for f in _findings(client, scan_id) if f["id"] == follow_up["finding_id"])
    assert doorway["outcome"] == "problem"
    assert doorway["measured_inches"] == 30
    assert "too narrow" in doorway["title"]


def test_a_photo_waits_for_the_team_then_becomes_a_result(make_client):
    client, scan_id = _sample(make_client, team_emails=frozenset({"demo@standardphysics.app"}))
    sent = client.put(f"/api/scans/{scan_id}/requests/door_hardware/photo", content=_jpeg(),
                      headers={"content-type": "image/jpeg"})
    assert sent.status_code == 200
    assert sent.json()["status"] == "answered"
    assert client.get(sent.json()["answer"]["photo_url"]).headers["content-type"] == "image/jpeg"
    waiting = _finding(client, scan_id, "door_hardware")
    assert (waiting["title"], waiting["asks"]) == ("We have your photo", "review")

    queue = client.get("/api/team/reviews").json()["reviews"]
    assert [(review["scan_id"], review["request"]["id"]) for review in queue] == [(scan_id, "door_hardware")]
    assert client.get(queue[0]["request"]["answer"]["photo_url"]).status_code == 200
    reviewed = client.put(f"/api/team/reviews/{scan_id}/door_hardware", json={"outcome": "problem"})
    assert reviewed.json()["status"] == "checked"
    handle = _finding(client, scan_id, "door_hardware")
    assert handle["outcome"] == "problem"
    assert handle["title"] == "The front door handle needs a tight grip"
    assert client.get("/api/team/reviews").json()["reviews"] == []


def test_only_the_team_sees_the_photo_queue(client):
    assert client.get("/api/team/reviews").status_code == 403


def test_a_photo_must_be_a_picture(client):
    scan_id = create_scan(client)
    refused = client.put(f"/api/scans/{scan_id}/requests/door_hardware/photo", content=b"not a picture")
    assert refused.status_code == 415


def test_the_wrong_kind_of_answer_is_refused(client):
    scan_id = create_scan(client)
    assert client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"number": 3}).status_code == 400
    assert client.put(f"/api/scans/{scan_id}/requests/door_hardware/answer", json={"yes": True}).status_code == 400
    assert client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": True, "number": 2}).status_code == 400
    assert client.put(f"/api/scans/{scan_id}/requests/nothing/answer", json={"yes": True}).status_code == 404


def test_skipping_stops_the_asking_but_keeps_it_open_in_the_report(make_client):
    client, scan_id = _sample(make_client)
    skipped = client.post(f"/api/scans/{scan_id}/requests/floor_surface/skip")
    assert skipped.json()["status"] == "skipped"
    assert _finding(client, scan_id, "floor_surface")["outcome"] == "question"


def test_a_stranger_cannot_answer_for_another_shop(client, stranger):
    scan_id = create_scan(client)
    assert stranger.get(f"/api/scans/{scan_id}/requests").status_code == 404
    assert stranger.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": False}).status_code == 404


def test_a_shop_with_answers_can_still_be_deleted(client):
    scan_id = create_scan(client)
    client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": True})
    client.put(f"/api/scans/{scan_id}/requests/door_hardware/photo", content=_jpeg())
    assert client.delete(f"/api/scans/{scan_id}").status_code == 204


def test_a_new_walk_of_the_shop_keeps_the_answers_and_replaces_the_old_scan(client):
    first = create_scan(client)
    client.put(f"/api/scans/{first}/requests/restroom/answer", json={"yes": False})
    client.put(f"/api/scans/{first}/requests/door_hardware/photo", content=_jpeg())
    made = client.post("/api/scans", json={"name": "Corner cafe", "device_model": "iPhone17,1", "duration_seconds": 90,
                                           "replaces": first})
    assert made.status_code == 201
    second = made.json()["id"]
    carried = _requests(client, second)
    assert carried["restroom"]["answer"]["yes"] is False
    assert carried["restroom_turning_space"]["status"] == "not_applicable"
    assert carried["door_hardware"]["status"] == "answered"
    assert client.get(carried["door_hardware"]["answer"]["photo_url"]).status_code == 200
    listed = [scan["id"] for scan in client.get("/api/scans").json()["scans"]]
    assert listed == [second, first]
    with client.app.state.database.connect() as connection:
        connection.execute("UPDATE scans SET state = 'ready' WHERE id = ?", (second,))
    assert [scan["id"] for scan in client.get("/api/scans").json()["scans"]] == [second]


def test_a_walk_cannot_replace_someone_else_s_shop(client, stranger):
    theirs = create_scan(client)
    refused = stranger.post("/api/scans", json={"name": "Mine", "device_model": "iPhone", "duration_seconds": 1, "replaces": theirs})
    assert refused.status_code == 404
