"""Where each shop is in the owner's journey, and the checklist behind it."""

import uuid
from datetime import UTC, datetime

from standardphysics_contracts import Assessment, Checklist, Citation, Finding, Scan

from conftest import REPO, create_scan, drain, put_artifact
from standardphysics_api.journey import ShopState, journey


def _sample(make_client):
    client = make_client(seed=True).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _journey(client, scan_id):
    return client.get(f"/api/scans/{scan_id}/journey").json()


def _answer_everything(client, scan_id):
    for request in client.get(f"/api/scans/{scan_id}/requests").json()["requests"]:
        if request["timing"] == "in_shop":
            client.post(f"/api/scans/{scan_id}/requests/{request['id']}/skip")


def test_a_walk_still_uploading_is_in_the_walk_stage(client):
    scan_id = create_scan(client)
    journey = _journey(client, scan_id)
    assert journey["stage"] == "walk"
    assert journey["next_step"] == {"kind": "upload", "title": "Finish uploading your walk", "count": None}
    assert journey["tools_unlocked"] is False


def test_the_quick_questions_come_first(make_client):
    client, scan_id = _sample(make_client)
    journey = _journey(client, scan_id)
    assert journey["stage"] == "fill_in_the_gaps"
    assert journey["next_step"]["title"] == "Answer 2 quick questions"
    client.put(f"/api/scans/{scan_id}/requests/restroom/answer", json={"yes": False})
    client.put(f"/api/scans/{scan_id}/requests/inside_doors/answer", json={"yes": False})
    assert _journey(client, scan_id)["next_step"]["title"] == "Take 3 more photos"


def test_the_results_are_ready_once_the_gaps_are_filled(make_client):
    client, scan_id = _sample(make_client)
    _answer_everything(client, scan_id)
    for request in client.get(f"/api/scans/{scan_id}/requests").json()["requests"]:
        if request["timing"] == "follow_up" and request["kind"] == "number":
            client.put(f"/api/scans/{scan_id}/requests/{request['id']}/answer", json={"number": 34})
    journey = _journey(client, scan_id)
    assert journey["stage"] == "results"
    assert journey["next_step"]["title"].startswith("Your results are ready.")
    assert journey["tools_unlocked"] is True


def test_the_checklist_counts_what_the_owner_has_dealt_with(make_client):
    client, scan_id = _sample(make_client)
    checklist = client.get(f"/api/scans/{scan_id}/checklist").json()
    assert checklist["done"] == 0
    assert checklist["total"] == len(checklist["items"]) > 1
    first = checklist["items"][0]["finding_id"]
    marked = client.put(f"/api/scans/{scan_id}/checklist/{first}", json={"status": "needs_pro"})
    assert marked.json()["status"] == "needs_pro"
    assert client.get(f"/api/scans/{scan_id}/checklist").json()["done"] == 1


def test_marking_something_that_is_not_a_problem_is_refused(make_client):
    client, scan_id = _sample(make_client)
    passing = next(f for f in client.get(f"/api/scans/{scan_id}/assessment").json()["findings"] if f["outcome"] == "passes")
    assert client.put(f"/api/scans/{scan_id}/checklist/{passing['id']}", json={"status": "done"}).status_code == 404


def test_fixing_says_how_far_along_and_what_is_next(make_client):
    client, scan_id = _sample(make_client)
    _answer_everything(client, scan_id)
    for request in client.get(f"/api/scans/{scan_id}/requests").json()["requests"]:
        if request["timing"] == "follow_up" and request["kind"] == "number":
            client.put(f"/api/scans/{scan_id}/requests/{request['id']}/answer", json={"number": 34})
    items = client.get(f"/api/scans/{scan_id}/checklist").json()["items"]
    client.put(f"/api/scans/{scan_id}/checklist/{items[0]['finding_id']}", json={"status": "done"})
    journey = _journey(client, scan_id)
    assert journey["stage"] == "fix"
    assert journey["next_step"]["title"].startswith(f"1 of {len(items)} done. Next: ")
    for item in items:
        client.put(f"/api/scans/{scan_id}/checklist/{item['finding_id']}", json={"status": "not_doing"})
    assert _journey(client, scan_id)["next_step"] == {"kind": "done", "title": "Everything on your list is done", "count": None}


def test_the_home_card_lists_every_shop_on_the_account(client, stranger):
    create_scan(client)
    create_scan(client)
    assert len(client.get("/api/journeys").json()["journeys"]) == 2
    assert stranger.get("/api/journeys").json()["journeys"] == []


def test_the_journey_list_needs_a_sign_in(make_client):
    with make_client(sign_in_as_owner=False) as anonymous:
        assert anonymous.get("/api/journeys").status_code == 401


def _state(**changes):



    scan = Scan(id=uuid.uuid4(), name="Corner cafe", created_at=datetime.now(UTC), device_model="iPhone",
                duration_seconds=60, state="checking", artifacts=[], coverage=[], content_hash=None)
    base = dict(scan=scan, shop_name="Corner cafe", requests=[], assessment=None, measured=True,
                counter_marked=False, path_confirmed=False, checklist=Checklist(items=[], done=0, total=0))
    return ShopState(**(base | changes))


def test_the_path_comes_straight_after_the_counter_while_the_checks_rerun():
    assert journey(_state()).next_step.kind == "counter"
    assert journey(_state(counter_marked=True)).next_step.kind == "path"
    waiting = journey(_state(counter_marked=True, path_confirmed=True)).next_step
    assert (waiting.kind, waiting.title) == ("measuring", "We're checking your shop")
    assert journey(_state(measured=False)).next_step.title == "We're measuring your shop"


def test_two_scans_of_the_same_room_keep_their_own_results(client):
    phone = REPO / "datasets/phone/ravida"
    scans = []
    for _ in range(2):
        scan_id = create_scan(client)
        put_artifact(client, scan_id, "room-json", (phone / "room.json").read_bytes(), "room_json")
        put_artifact(client, scan_id, "room-usdz", (phone / "room.usdz").read_bytes(), "room_usdz")
        client.post(f"/api/scans/{scan_id}/complete")
        scans.append(scan_id)
    drain(client)
    first, second = (client.get(f"/api/scans/{scan_id}/assessment").json() for scan_id in scans)
    assert (first["scan_id"], second["scan_id"]) == tuple(scans)
    assert first["id"] != second["id"]


def test_no_problems_is_not_an_all_clear_while_checks_are_waiting():
    question = Finding(id=uuid.uuid4(), check_id="route_clear_width", outcome="question", title="Point the phone at the table again",
                       detail="", citation=Citation(authority="ADA_2010", edition="2010 ADA Standards", section="403.5.1"))
    assessment = Assessment(id=uuid.uuid4(), scan_id=uuid.uuid4(), graph_revision=0, graph_hash="h", rulepack_version="1",
                            pass_number=1, created_at=datetime.now(UTC), findings=[question])
    waiting = journey(_state(assessment=assessment, counter_marked=True, path_confirmed=True)).next_step
    assert waiting.title == "Nothing to fix so far. 1 spot still needs checking"
    clear = journey(_state(assessment=assessment.model_copy(update={"findings": []}), counter_marked=True, path_confirmed=True))
    assert clear.next_step.title == "There's nothing on your list to fix"


def test_the_team_sees_how_far_owners_get(make_client):
    client = make_client(seed=True, team_emails=frozenset({"demo@standardphysics.app"})).__enter__()
    drain(client)
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    item = client.get(f"/api/scans/{scan_id}/checklist").json()["items"][0]["finding_id"]
    client.put(f"/api/scans/{scan_id}/checklist/{item}", json={"status": "done"})
    funnel = client.get("/api/team/funnel").json()
    counts = {step["key"]: step["shops"] for step in funnel["steps"]}
    assert counts["walked"] == counts["results"] == counts["path"] == counts["fixed"] == 1
    assert counts["shared"] == 0
    assert funnel["median_minutes_to_results"] is not None
    assert funnel["median_hours_to_first_fix"] is not None


def test_owners_cannot_read_the_funnel(client):
    assert client.get("/api/team/funnel").status_code == 403
