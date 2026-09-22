"""Nonidentity room placements: geometry, attachments, collision and export.

Card G item 3: a saved alignment must move a whole room as one rigid group,
keep floors on the ground, move attached objects with their parents, feed the
same transforms to collision as to export, and never merge or drop nodes.
"""

from __future__ import annotations

import math
import uuid

import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.blender import display_graph
from standardphysics_pipeline.footprints import gap_between_nodes

from standardphysics_api.combine import RoomPlacement, apply_room_placements


def _node(identifier: str, x: float, y: float, z: float = 0.0, kind: str = "object") -> tuple[uuid.UUID, SceneNode]:
    node_id = uuid.uuid5(uuid.NAMESPACE_OID, identifier)
    node = SceneNode(
        id=node_id,
        kind=kind,
        label=kind,
        raw_category=kind,
        dimensions=Vec3(x=1.0, y=1.0, z=1.0),
        transform=Mat4(m=[1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0]),
    )
    return node_id, node


def _graph(*items: tuple[uuid.UUID, SceneNode]) -> SceneGraph:
    return SceneGraph(scan_id=uuid.uuid4(), revision=0, nodes=[node for _, node in items])


def _placement(node_ids: list[uuid.UUID], yaw: float, tx: float, ty: float) -> RoomPlacement:
    return RoomPlacement(node_ids=node_ids, yaw_degrees=yaw, tx=tx, ty=ty, cx=0.0, cy=0.0)


def test_nonidentity_placement_is_rigid_about_the_centroid() -> None:
    at = [(0.0, 0.0), (2.0, 0.0), (0.0, 1.5), (3.0, -1.0)]
    graph = _graph(*[_node(f"n{index}", x, y) for index, (x, y) in enumerate(at)])
    ids = [node.id for node in graph.nodes]
    before = {node.id: node.transform.position for node in graph.nodes}

    moved = apply_room_placements(graph, [_placement(ids, yaw=37.0, tx=1.25, ty=-0.75)])
    after = {node.id: node.transform.position for node in moved.nodes}

    for left_id in ids:
        for right_id in ids:
            span_before = ((before[left_id].x - before[right_id].x) ** 2
                           + (before[left_id].y - before[right_id].y) ** 2
                           + (before[left_id].z - before[right_id].z) ** 2) ** 0.5
            span_after = ((after[left_id].x - after[right_id].x) ** 2
                          + (after[left_id].y - after[right_id].y) ** 2
                          + (after[left_id].z - after[right_id].z) ** 2) ** 0.5
            assert span_after == pytest.approx(span_before, abs=1e-9)


def test_placement_never_changes_heights() -> None:
    a_id, a_node = _node("a", 0.0, 0.0, z=0.75)
    floor_id, floor_node = _node("floor", 1.0, 1.0, z=0.0, kind="floor")
    graph = _graph((a_id, a_node), (floor_id, floor_node))
    ids = [node.id for node in graph.nodes]

    moved = apply_room_placements(graph, [_placement(ids, yaw=137.0, tx=4.0, ty=5.0)])
    by_id = {node.id: node for node in moved.nodes}
    assert by_id[a_id].transform.position.z == pytest.approx(0.75)
    assert by_id[floor_id].transform.position.z == pytest.approx(0.0)


def test_an_attached_object_moves_with_its_parent_frame() -> None:
    wall_id, wall = _node("wall", 1.0, 2.0, kind="wall")
    child = _node("shelf", 1.0, 2.0, z=1.2)[1].model_copy(
        update={"parent_id": wall_id, "relation": "attached"},
    )
    graph = _graph((wall_id, wall), (child.id, child))
    ids = [node.id for node in graph.nodes]
    before_offset = (
        child.transform.position.x - wall.transform.position.x,
        child.transform.position.y - wall.transform.position.y,
        child.transform.position.z - wall.transform.position.z,
    )

    moved = apply_room_placements(graph, [_placement(ids, yaw=63.0, tx=-2.0, ty=1.0)])
    by_id = {node.id: node for node in moved.nodes}
    placed_wall = by_id[wall_id].transform.position
    placed_child = by_id[child.id].transform.position
    after_offset = (placed_child.x - placed_wall.x, placed_child.y - placed_wall.y, placed_child.z - placed_wall.z)

    yaw = math.radians(63.0)
    rotated = (before_offset[0] * math.cos(yaw) - before_offset[1] * math.sin(yaw),
               before_offset[0] * math.sin(yaw) + before_offset[1] * math.cos(yaw))
    assert after_offset[0] == pytest.approx(rotated[0], abs=1e-9)
    assert after_offset[1] == pytest.approx(rotated[1], abs=1e-9)
    assert after_offset[2] == pytest.approx(before_offset[2], abs=1e-9)


def test_collision_sees_the_same_geometry_after_a_rigid_move() -> None:
    a_id, a_node = _node("a", 0.0, 0.0)
    b_id, b_node = _node("b", 2.0, 0.0)
    graph = _graph((a_id, a_node), (b_id, b_node))
    ids = [node.id for node in graph.nodes]
    before = gap_between_nodes(graph.by_id(a_id), graph.by_id(b_id))

    moved = apply_room_placements(graph, [_placement(ids, yaw=90.0, tx=3.0, ty=0.0)])
    after = gap_between_nodes(moved.by_id(a_id), moved.by_id(b_id))
    assert after == pytest.approx(before, abs=1e-9)


def test_export_consumes_the_placed_transforms_exactly_once() -> None:
    a_id, a_node = _node("a", 0.0, 0.0)
    b_id, b_node = _node("b", 2.0, 1.0)
    graph = _graph((a_id, a_node), (b_id, b_node))
    ids = [node.id for node in graph.nodes]

    moved = apply_room_placements(graph, [_placement(ids, yaw=-20.0, tx=0.5, ty=0.5)])
    exported = display_graph(moved)
    for placed, drawn in zip(moved.nodes, exported.nodes):
        assert drawn.transform.m == placed.transform.m
    assert [node.id for node in exported.nodes] == [node.id for node in moved.nodes]


def test_overlapping_rooms_stay_distinct_nodes() -> None:
    first = _node("first", 1.0, 1.0)
    second = _node("second", 1.0, 1.0)
    graph = _graph(first, second)
    ids = [node.id for node in graph.nodes]
    assert len(ids) == 2

    moved = apply_room_placements(graph, [
        _placement([first[0]], yaw=0.0, tx=0.0, ty=0.0),
        _placement([second[0]], yaw=0.0, tx=0.0, ty=0.0),
    ])
    assert len(moved.nodes) == 2
    by_id = {node.id: node for node in moved.nodes}
    assert by_id[first[0]].transform.m == by_id[second[0]].transform.m
    assert first[0] != second[0]


def test_unknown_nodes_stay_refused_under_nonidentity_placement() -> None:
    a_id, a_node = _node("a", 0.0, 0.0)
    graph = _graph((a_id, a_node))
    foreign = uuid.uuid4()
    with pytest.raises(Exception) as error:
        apply_room_placements(graph, [_placement([foreign], yaw=45.0, tx=1.0, ty=1.0)])
    assert "unknown node" in str(error.value)
