"""The owner renames, regroups, removes and restores found pieces, and each change leaves a correction."""

import uuid

from standardphysics_contracts import Mat4, SceneNode, Vec3

from conftest import REPO, create_scan, drain, put_artifact
from standardphysics_api import repository_revisions as revisions_repo
from standardphysics_api.label_corrections import corrections_for

PHONE = REPO / "datasets/phone"


def _phone_scan(client, name: str = "ravida") -> str:
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room-json", (PHONE / name / "room.json").read_bytes(), "room_json")
    put_artifact(client, scan_id, "room-usdz", (PHONE / name / "room.usdz").read_bytes(), "room_usdz")
    client.post(f"/api/scans/{scan_id}/complete")
    drain(client)
    return scan_id


def _nodes_of_kind(scene: dict, kind: str) -> list[dict]:
    return [node for node in scene["nodes"] if node["kind"] == kind]


def _object_path(scan_id: str, revision: int, node_id: str) -> str:
    return f"/api/scans/{scan_id}/revisions/{revision}/objects/{node_id}"


def _corrections(client, scan_id: str) -> list[dict]:
    with client.app.state.database.connect() as connection:
        return corrections_for(connection, uuid.UUID(scan_id))


def _add_resting_child(client, scan_id: str, parent_id: str) -> tuple[int, str]:
    """Save a new revision with a small object resting on `parent_id`, the way another test's fixture does."""
    database = client.app.state.database
    scan_uuid = uuid.UUID(scan_id)
    with database.connect() as connection:
        base_rev = revisions_repo.latest_revision_number(connection, scan_uuid)
        graph = revisions_repo.graph_of(revisions_repo.get_revision(connection, scan_uuid, base_rev))

    child = SceneNode(
        id=uuid.uuid4(),
        kind="object",
        label="Cup",
        raw_category="cup",
        dimensions=Vec3(x=0.08, y=0.08, z=0.1),
        transform=Mat4.translation(0.0, 0.0, 0.5),
        parent_id=uuid.UUID(parent_id),
        relation="rests_on",
    )
    updated = graph.model_copy(update={"nodes": [*graph.nodes, child], "revision": base_rev + 1})
    with database.transaction() as connection:
        revisions_repo.save_revision(connection, updated, source="owner", base_revision=base_rev)
    return base_rev + 1, str(child.id)


def test_renaming_a_found_object_writes_an_edited_correction(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.put(_object_path(scan_id, 0, target["id"]), json={"label": "Espresso machine"})
    assert response.status_code == 201
    body = response.json()
    assert body["revision"] == 1
    edited = next(n for n in body["nodes"] if n["id"] == target["id"])
    assert (edited["label"], edited["labeled_by"]) == ("Espresso machine", "owner")

    [correction] = _corrections(client, scan_id)
    assert correction["kind"] == "edited"
    assert correction["before"]["label"] == target["label"]
    assert correction["after"]["label"] == "Espresso machine"


def test_regrouping_a_found_object_leaves_its_label_and_labeler_alone(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.put(_object_path(scan_id, 0, target["id"]), json={"group": "Seating"})
    assert response.status_code == 201
    edited = next(n for n in response.json()["nodes"] if n["id"] == target["id"])
    assert edited["group"] == "Seating"
    assert edited["label"] == target["label"]
    assert edited["labeled_by"] == target["labeled_by"]


def test_renaming_and_regrouping_together_applies_both(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.put(
        _object_path(scan_id, 0, target["id"]), json={"label": "Espresso machine", "group": "Equipment"}
    )
    assert response.status_code == 201
    edited = next(n for n in response.json()["nodes"] if n["id"] == target["id"])
    assert (edited["label"], edited["group"], edited["labeled_by"]) == ("Espresso machine", "Equipment", "owner")


def test_a_body_that_changes_nothing_saves_no_revision_or_correction(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.put(_object_path(scan_id, 0, target["id"]), json={"label": target["label"]})
    assert response.status_code == 201
    assert response.json()["revision"] == 0
    assert _corrections(client, scan_id) == []


def test_an_empty_or_whitespace_label_is_rejected(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]
    path = _object_path(scan_id, 0, target["id"])

    assert client.put(path, json={"label": ""}).status_code == 400
    assert client.put(path, json={"label": "   "}).status_code == 400


def test_an_unknown_field_is_rejected(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.put(_object_path(scan_id, 0, target["id"]), json={"label": "New name", "color": "red"})
    assert response.status_code == 400


def test_a_body_with_neither_label_nor_group_is_rejected(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    assert client.put(_object_path(scan_id, 0, target["id"]), json={}).status_code == 400


def test_editing_from_a_stale_revision_is_refused(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]
    path = _object_path(scan_id, 0, target["id"])

    assert client.put(path, json={"label": "First name"}).status_code == 201
    assert client.put(path, json={"label": "Second name"}).status_code == 409


def test_editing_an_unknown_node_is_not_found(client):
    scan_id = _phone_scan(client)
    path = _object_path(scan_id, 0, str(uuid.uuid4()))
    assert client.put(path, json={"label": "Anything"}).status_code == 404


def test_a_wall_cannot_be_renamed(client):
    scan_id = _phone_scan(client)
    wall = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "wall")[0]
    assert client.put(_object_path(scan_id, 0, wall["id"]), json={"label": "Not a wall"}).status_code == 400


def test_removing_a_found_object_writes_a_removed_correction(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]

    response = client.delete(_object_path(scan_id, 0, target["id"]))
    assert response.status_code == 201
    assert target["id"] not in {n["id"] for n in response.json()["nodes"]}

    [correction] = _corrections(client, scan_id)
    assert correction["kind"] == "removed"
    assert correction["before"]["label"] == target["label"]
    assert correction["after"] is None


def test_removing_a_found_object_detaches_what_rested_on_it(client):
    scan_id = _phone_scan(client)
    scene = client.get(f"/api/scans/{scan_id}/scene").json()
    target = next(n for n in _nodes_of_kind(scene, "object") if n["parent_id"] is None)
    revision, child_id = _add_resting_child(client, scan_id, target["id"])

    response = client.delete(_object_path(scan_id, revision, target["id"]))
    assert response.status_code == 201
    child = next(n for n in response.json()["nodes"] if n["id"] == child_id)
    assert child["parent_id"] is None
    assert child.get("relation") is None


def test_restoring_a_removed_object_brings_back_what_rested_on_it(client):
    scan_id = _phone_scan(client)
    scene = client.get(f"/api/scans/{scan_id}/scene").json()
    target = next(n for n in _nodes_of_kind(scene, "object") if n["parent_id"] is None)
    revision, child_id = _add_resting_child(client, scan_id, target["id"])

    removed = client.delete(_object_path(scan_id, revision, target["id"]))
    assert removed.status_code == 201
    removed_revision = removed.json()["revision"]

    response = client.put(f"{_object_path(scan_id, removed_revision, target['id'])}/restore?from_revision={revision}")
    assert response.status_code == 201
    nodes = {n["id"]: n for n in response.json()["nodes"]}
    assert nodes[target["id"]]["label"] == target["label"]
    assert (nodes[child_id]["parent_id"], nodes[child_id]["relation"]) == (target["id"], "rests_on")

    corrections = _corrections(client, scan_id)
    assert corrections[-1]["kind"] == "restored"
    assert corrections[-1]["before"] is None
    assert corrections[-1]["after"]["label"] == target["label"]


def test_restoring_a_node_that_is_already_there_is_refused(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]
    path = f"{_object_path(scan_id, 0, target['id'])}/restore?from_revision=0"
    assert client.put(path).status_code == 409


def test_deleting_a_scan_with_corrections_succeeds(client):
    scan_id = _phone_scan(client)
    target = _nodes_of_kind(client.get(f"/api/scans/{scan_id}/scene").json(), "object")[0]
    client.put(_object_path(scan_id, 0, target["id"]), json={"label": "Renamed"})

    assert client.delete(f"/api/scans/{scan_id}").status_code == 204
    assert client.get(f"/api/scans/{scan_id}").status_code == 404
