import uuid

from conftest import drain, no_blender_stages
from standardphysics_agents import LocalPolicyRouter
from standardphysics_api.loop_run import combine_moves
from standardphysics_contracts import NodeMove, Vec3

from standardphysics_fixtures import node_id


def _sample(make_client):
    client = make_client(seed=True, stages=no_blender_stages(router_factory=LocalPolicyRouter)).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def test_the_loop_moves_the_display_cases_and_the_gate_keeps_it(make_client):
    client, scan_id = _sample(make_client)
    result = client.post(f"/api/scans/{scan_id}/loop", json={"base_revision": 0}).json()
    assert result["decided_by"] == "local_policy"
    first = result["passes"][0]
    assert first["action"] == "FIX"
    assert first["kept"] is True
    assert first["inches_short_after"] < first["inches_short_before"]
    moved = {move["node_id"] for move in result["moves"]}
    assert moved and moved <= {str(node_id("case_east")), str(node_id("case_west"))}
    assert len(result["passes"]) <= 6


def test_the_loop_needs_a_layout_that_exists(make_client):
    client, scan_id = _sample(make_client)
    assert client.post(f"/api/scans/{scan_id}/loop", json={"base_revision": 9}).status_code == 404


def test_kept_moves_on_the_same_piece_add_up():
    piece = uuid.uuid4()
    moves = [
        NodeMove(node_id=piece, delta_translation=Vec3(x=0.1, y=0.0, z=0.0), delta_rotation_z_degrees=15),
        NodeMove(node_id=piece, delta_translation=Vec3(x=0.05, y=0.2, z=0.0), delta_rotation_z_degrees=-5),
    ]
    [combined] = combine_moves(moves)
    assert (round(combined.delta_translation.x, 6), combined.delta_translation.y) == (0.15, 0.2)
    assert combined.delta_rotation_z_degrees == 10
