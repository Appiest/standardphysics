"""The combine math: moving a whole room as one rigid group."""

from __future__ import annotations

import math
import uuid

from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

from standardphysics_api.combine import RoomPlacement, apply_room_placements, placement_since_capture


def _node(identifier: str, x: float, y: float) -> tuple[uuid.UUID, SceneNode]:
    node_id = uuid.uuid5(uuid.NAMESPACE_OID, identifier)
    node = SceneNode(
        id=node_id,
        kind="object",
        label="table",
        raw_category="table",
        dimensions=Vec3(x=1.0, y=1.0, z=1.0),
        transform=Mat4(m=[1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
    )
    return node_id, node


def _graph(*items: tuple[uuid.UUID, SceneNode]) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), revision=0, nodes=[node for _, node in items])


def test_a_room_rotates_about_its_centroid() -> None:
    centre, centre_node = _node("centre", 10.0, 0.0)
    edge, edge_node = _node("edge", 12.0, 0.0)
    graph = _graph((centre, centre_node), (edge, edge_node))

    moved = apply_room_placements(
        graph,
        [RoomPlacement(node_ids=[centre, edge], yaw_degrees=90.0, tx=0.0, ty=0.0, cx=10.0, cy=0.0)],
    )
    by_id = {node.id: node for node in moved.nodes}

    centre_m = by_id[centre].transform.m
    assert abs(centre_m[3] - 10.0) < 1e-9 and abs(centre_m[7] - 0.0) < 1e-9

    edge_m = by_id[edge].transform.m
    assert abs(edge_m[3] - 10.0) < 1e-9
    assert abs(edge_m[7] - 2.0) < 1e-9


def test_a_room_slides_as_one() -> None:
    a, a_node = _node("a", 0.0, 0.0)
    b, b_node = _node("b", 2.0, 0.0)
    graph = _graph((a, a_node), (b, b_node))

    moved = apply_room_placements(
        graph,
        [RoomPlacement(node_ids=[a, b], yaw_degrees=0.0, tx=3.0, ty=-4.0, cx=1.0, cy=0.0)],
    )
    by_id = {node.id: node for node in moved.nodes}
    assert by_id[a].transform.m[3] == 3.0 and by_id[a].transform.m[7] == -4.0
    assert by_id[b].transform.m[3] == 5.0 and by_id[b].transform.m[7] == -4.0


def test_a_room_keeps_each_node_upright() -> None:
    a, a_node = _node("a", 1.0, 2.0)
    graph = _graph((a, a_node))

    moved = apply_room_placements(
        graph,
        [RoomPlacement(node_ids=[a], yaw_degrees=30.0, tx=0.0, ty=0.0, cx=0.0, cy=0.0)],
    )
    m = moved.nodes[0].transform.m
    # The bottom row stays [0, 0, 0, 1]: pure rotation plus translation.
    assert m[12] == 0.0 and m[13] == 0.0 and m[14] == 0.0 and m[15] == 1.0
    # Rotation stays a unit rotation: determinant of the upper 3x3 is 1.
    det = m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8]) + m[2] * (m[4] * m[9] - m[5] * m[8])
    assert abs(det - 1.0) < 1e-9


def test_an_unknown_node_is_refused() -> None:
    a, a_node = _node("a", 0.0, 0.0)
    graph = _graph((a, a_node))
    gone = uuid.uuid4()
    try:
        apply_room_placements(
            graph,
            [RoomPlacement(node_ids=[gone], yaw_degrees=0.0, tx=0.0, ty=0.0, cx=0.0, cy=0.0)],
        )
    except Exception as error:  # ApiProblem, but the imported path differs per test config
        assert "unknown node" in str(error)
    else:
        raise AssertionError("an unknown node should be refused")


def test_combine_endpoint_revises_a_scan(make_client):
    client = make_client(seed=True)
    scan_id = client.get("/api/scans").json()["scans"][0]["id"]
    assert client.get(f"/api/scans/{scan_id}/rooms").json() == {"rooms": []}
    response = client.post(f"/api/scans/{scan_id}/combine", json={"base_revision": 0, "rooms": []})
    assert response.status_code == 201, response.text
    assert response.json()["revision"] == 1


def test_a_walk_is_found_where_it_was_placed_after_several_saves() -> None:
    """The capture frame's motion is the whole way across: the merge's spread and every save since."""
    captured = [_node(name, x, y) for name, x, y in (("desk", 0.0, 0.0), ("shelf", 4.0, 0.0), ("chair", 1.0, 3.0))]
    ids = [node_id for node_id, _ in captured]
    spread = apply_room_placements(
        _graph(*captured), [RoomPlacement(node_ids=ids, yaw_degrees=0.0, tx=20.0, ty=6.0, cx=0.0, cy=0.0)]
    )
    placed = apply_room_placements(
        spread, [RoomPlacement(node_ids=ids, yaw_degrees=92.0, tx=-3.0, ty=1.5, cx=21.0, cy=7.0)]
    )

    motion = placement_since_capture(_graph(*captured), placed, [str(node_id) for node_id in ids])

    assert abs(math.degrees(motion.yaw) - 92.0) < 1e-9
    for (_, node), moved in zip(captured, placed.nodes):
        x, y = motion.apply((node.transform.m[3], node.transform.m[7]))
        assert abs(x - moved.transform.m[3]) < 1e-9 and abs(y - moved.transform.m[7]) < 1e-9
