"""Dragging furniture: re-checks while moving, and saving a layout."""

import uuid

from standardphysics_agents.fix import apply_moves
from standardphysics_contracts import NodeMove, Vec3, to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, node_id

from conftest import drain
from standardphysics_api import repository_revisions as revisions_repo

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


def test_a_check_changes_nothing_the_shop_has_saved(make_client):
    client, scan_id = _sample(make_client)
    scene_before = client.get(f"/api/scans/{scan_id}/scene").json()
    assessment_before = client.get(f"/api/scans/{scan_id}/assessment").json()
    into_the_aisle = _move(CASE_EAST, dx=-0.3)
    for sequence in range(1, 4):
        body = {"base_revision": 0, "sequence": sequence, "moves": [into_the_aisle]}
        assert client.post(f"/api/scans/{scan_id}/layout-checks", json=body).status_code == 200
    drain(client)
    assert client.get(f"/api/scans/{scan_id}/scene").json() == scene_before
    assert client.get(f"/api/scans/{scan_id}/assessment").json() == assessment_before


def test_moving_a_piece_back_gives_the_scanned_answer(make_client):
    client, scan_id = _sample(make_client)

    def check(moves: list[dict]) -> dict:
        body = {"base_revision": 0, "sequence": 1, "moves": moves}
        return client.post(f"/api/scans/{scan_id}/layout-checks", json=body).json()

    scanned = check([])
    moved = check([_move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))])
    back = check([_move(CASE_EAST, dx=0.0)])
    assert moved["findings"] != scanned["findings"]
    assert back["findings"] == scanned["findings"]


def _blocked(client, scan_id, moves) -> set[str]:
    body = client.post(f"/api/scans/{scan_id}/layout-checks", json={"base_revision": 0, "sequence": 1, "moves": moves}).json()
    return {b["reason"] for b in body["blocked"]}


def test_moving_the_counter_is_construction_not_a_locked_move(make_client):
    client, scan_id = _sample(make_client)
    assert "moved_something_fixed" not in _blocked(client, scan_id, [_move(COUNTER, dy=-0.2)])


def _onto(client, scan_id, node: str, target: str) -> dict:
    graph = client.get(f"/api/scans/{scan_id}/scene").json()
    at = {each["id"]: (each["transform"]["m"][3], each["transform"]["m"][7]) for each in graph["nodes"]}
    return _move(node, dx=at[target][0] - at[node][0], dy=at[target][1] - at[node][1])


def test_a_moved_counter_still_cannot_land_on_furniture(make_client):
    client, scan_id = _sample(make_client)
    assert _blocked(client, scan_id, [_onto(client, scan_id, COUNTER, CASE_EAST)])


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
    response = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [_onto(client, scan_id, COUNTER, CASE_EAST)]})
    assert response.status_code == 409
    assert client.get(f"/api/scans/{scan_id}/scene").json()["revision"] == 0


def test_saving_refuses_a_counter_move_because_the_construction_has_not_happened(make_client):
    client, scan_id = _sample(make_client)
    response = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [_move(COUNTER, dy=-0.2)]})
    assert response.status_code == 409


def test_each_revision_keeps_its_own_assessment(make_client):
    client, scan_id = _sample(make_client)
    fix = _move(CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [fix]})
    drain(client)
    before = client.get(f"/api/scans/{scan_id}/assessment?revision=0").json()
    after = client.get(f"/api/scans/{scan_id}/assessment?revision=1").json()
    assert (before["graph_revision"], after["graph_revision"]) == (0, 1)
    assert before["graph_hash"] != after["graph_hash"]


def test_small_saved_moves_add_up_against_where_the_scan_found_a_piece(make_client):
    client, scan_id = _sample(make_client)
    step = _move(CASE_EAST, dy=-0.8)
    saved = client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [step]})
    assert saved.status_code == 201
    drain(client)
    body = client.post(
        f"/api/scans/{scan_id}/layout-checks", json={"base_revision": 1, "sequence": 1, "moves": [step]}
    ).json()
    assert "moved_too_far" in {b["reason"] for b in body["blocked"]}


def _save_as_before_origins_were_recorded(client, scan_id: str, step: dict) -> Vec3:
    """Save revision 1 with the move and no `measured_position`, as layouts were saved before it existed.

    Returns where the scan found the moved piece.
    """
    with client.app.state.database.transaction() as connection:
        scanned = revisions_repo.graph_of(revisions_repo.get_revision(connection, uuid.UUID(scan_id), 0))
        moved = apply_moves(scanned, [NodeMove.model_validate(step)])
        unrecorded = moved.model_copy(update={
            "revision": 1,
            "nodes": [node.model_copy(update={"measured_position": None}) for node in moved.nodes],
        })
        revisions_repo.save_revision(connection, unrecorded, source="owner", base_revision=0)
        return scanned.by_id(uuid.UUID(step["node_id"])).transform.position


def test_a_move_saved_before_origins_were_recorded_still_counts_against_the_cap(make_client):
    client, scan_id = _sample(make_client)
    step = _move(CASE_EAST, dy=-0.8)
    _save_as_before_origins_were_recorded(client, scan_id, step)
    body = client.post(
        f"/api/scans/{scan_id}/layout-checks", json={"base_revision": 1, "sequence": 1, "moves": [step]}
    ).json()
    assert "moved_too_far" in {b["reason"] for b in body["blocked"]}


def test_saving_from_an_unrecorded_revision_writes_the_recovered_origin(make_client):
    client, scan_id = _sample(make_client)
    found = _save_as_before_origins_were_recorded(client, scan_id, _move(CASE_EAST, dy=-0.8))
    saved = client.post(
        f"/api/scans/{scan_id}/revisions", json={"base_revision": 1, "moves": [_move(CASE_EAST, dy=0.1)]}
    )
    assert saved.status_code == 201, saved.text
    case = next(node for node in saved.json()["nodes"] if node["id"] == CASE_EAST)
    assert (case["measured_position"]["x"], case["measured_position"]["y"]) == (found.x, found.y)


def _mark_unsure(client, scan_id: str, node: str) -> None:
    """What RoomPlan does to a piece it saw poorly: the report asks for another look instead of calling it."""
    with client.app.state.database.transaction() as connection:
        row = revisions_repo.get_revision(connection, uuid.UUID(scan_id), 0)
        graph = revisions_repo.graph_of(row)
        unsure = graph.model_copy(update={"nodes": [
            n.model_copy(update={"quality": "needs_another_look"}) if str(n.id) == node else n for n in graph.nodes]})
        connection.execute("UPDATE revisions SET graph_json = ? WHERE scan_id = ? AND revision = 0",
                           (unsure.model_dump_json(), scan_id))


def _at(findings, node):
    return [f for f in findings if f["check_id"] == "route_clear_width" and node in (f["locus"] or {}).get("node_ids", [])]


def test_a_piece_the_scan_saw_poorly_stays_a_question_until_the_owner_moves_it_on_the_plan(make_client):
    client, scan_id = _sample(make_client)
    _mark_unsure(client, scan_id, CASE_EAST)

    def check(moves):
        body = {"base_revision": 0, "sequence": 1, "moves": moves}
        return client.post(f"/api/scans/{scan_id}/layout-checks", json=body).json()["findings"]

    untouched = _at(check([]), CASE_EAST)
    assert untouched and all(f["outcome"] == "question" for f in untouched)
    closer = _at(check([_move(CASE_EAST, dx=-to_meters(2))]), CASE_EAST)
    assert closer and all(f["outcome"] == "problem" for f in closer)
