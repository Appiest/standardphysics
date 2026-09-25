import json
import uuid

from standardphysics_agents import LocalPolicyRouter
from standardphysics_contracts import NodeMove, Vec3
from standardphysics_fixtures import build_lawsuit_graph, node_id

from conftest import drain, no_blender_stages
from standardphysics_api.loop_run import combine_moves


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
    assert moved, "the loop cleared the aisle without reporting what it moved"
    # Whatever it moved is furniture. A loop that widens an aisle by shifting a
    # wall, the floor or the ordering counter has not fixed this shop, it has
    # described a different one, so that is the line the test holds. It used to
    # name the two display cases exactly, which failed the moment the loop got
    # better at its job and started moving a table and a chair out of the way
    # as well.
    movable = {str(node.id) for node in build_lawsuit_graph().nodes if node.movable}
    assert moved <= movable
    # And it moved the things that were actually in the way.
    assert moved & {str(node_id("case_east")), str(node_id("case_west"))}
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


def _streamed_events(client, scan_id, base_revision=0):
    with client.stream("POST", f"/api/scans/{scan_id}/loop/stream", json={"base_revision": base_revision}) as response:
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.iter_lines() if line]


def test_the_streamed_loop_reports_each_pass_then_the_same_result(make_client):
    client, scan_id = _sample(make_client)
    events = _streamed_events(client, scan_id)
    kinds = [event["kind"] for event in events]
    assert kinds[0] == "started" and events[0]["decided_by"] == "local_policy"
    assert kinds[-1] == "finished"
    assert kinds[1:-1] and set(kinds[1:-1]) == {"pass"}
    result = events[-1]["result"]
    assert result["passes"] == [event["loop_pass"] for event in events[1:-1]]
    assert result == client.post(f"/api/scans/{scan_id}/loop", json={"base_revision": 0}).json()


def test_the_streamed_loop_refuses_a_missing_layout_before_it_starts(make_client):
    client, scan_id = _sample(make_client)
    assert client.post(f"/api/scans/{scan_id}/loop/stream", json={"base_revision": 9}).status_code == 404


def test_a_loop_that_breaks_partway_ends_the_stream_with_a_failure(make_client, monkeypatch):
    def breaks_before_the_first_pass(*args, **kwargs):
        raise RuntimeError("measurement cache went away")
        yield

    monkeypatch.setattr("standardphysics_api.stages.loop_steps", breaks_before_the_first_pass)
    client, scan_id = _sample(make_client)
    events = _streamed_events(client, scan_id)
    assert [event["kind"] for event in events] == ["started", "failed"]
    assert "Nothing was changed" in events[1]["error"]
