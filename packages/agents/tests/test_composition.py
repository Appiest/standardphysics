"""Ranking rearrangements by how the room looks, and planning the seating as a whole room."""

import json
from pathlib import Path

import pytest

from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.composition import (
    Composition,
    carried,
    expand_moves_with_seating,
    lost_seating,
    room_axis_degrees,
    seating_groups,
    squaring_turn,
    tables_by_chair,
)
from standardphysics_agents.fix.furnishing import arrangements
from standardphysics_agents.fix.moves import move_node
from standardphysics_contracts import Mat4, NodeMove, SceneGraph, SceneNode, Scenario, Vec3
from standardphysics_fixtures.shop import node_id


def _piece(name, label, centre, dims, heading=0.0, kind="object"):
    node = SceneNode(
        id=node_id(name), kind=kind, label=label, raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre), movable=kind == "object",
    )
    return move_node(node, _slide(node, 0.0, 0.0, heading))


def _slide(node, dx, dy, turn=0.0):
    return NodeMove(node_id=node.id, delta_translation=Vec3(x=dx, y=dy, z=0.0), delta_rotation_z_degrees=turn)


@pytest.fixture
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"]), Scenario.model_validate(data["scenario"])


def test_the_room_axis_follows_walls_that_are_slightly_off_square():
    walls = [
        _piece("north_wall", "Wall", (0.0, 3.0, 1.5), (6.0, 0.0, 3.0), heading=1.6, kind="wall"),
        _piece("west_wall", "Wall", (-3.0, 0.0, 1.5), (4.0, 0.0, 3.0), heading=91.6, kind="wall"),
    ]
    axis = room_axis_degrees(SceneGraph(scan_id=node_id("axis_room"), nodes=walls))
    crooked = _piece("crooked_chair", "Chair", (0.0, 0.0, 0.45), (0.6, 0.6, 0.9), heading=-72.8)
    assert axis == pytest.approx(1.6, abs=0.01)
    assert squaring_turn(crooked, axis) == pytest.approx(-15.6, abs=0.01)


def test_a_table_carries_its_chairs_through_a_quarter_turn():
    table = _piece("carry_table", "Table", (1.0, 1.0, 0.4), (1.6, 0.8, 0.75))
    chair = _piece("carry_chair", "Chair", (1.0, 0.3, 0.45), (0.5, 0.5, 0.9))
    [move] = carried(table, _slide(table, 2.0, 0.0, 90.0), [chair])
    seated = move_node(chair, move).transform.position
    assert (seated.x, seated.y) == pytest.approx((3.7, 1.0))
    assert move.delta_rotation_z_degrees == 90.0


def test_expanding_a_table_move_carries_unnamed_chairs_and_detects_stranding():
    table = _piece("group_table", "Table", (1.0, 1.0, 0.4), (1.6, 0.8, 0.75))
    chair = _piece("group_chair", "Chair", (1.0, 0.3, 0.45), (0.5, 0.5, 0.9))
    graph = SceneGraph(scan_id=node_id("group_room"), nodes=[table, chair])
    assert seating_groups(graph) == [{"table_id": str(table.id), "chair_ids": [str(chair.id)]}]
    slide = _slide(table, 2.0, 0.0)
    expanded = expand_moves_with_seating(graph, [slide])
    assert len(expanded) == 2
    together = apply_moves(graph, expanded)
    assert not lost_seating(graph, together)
    assert tables_by_chair(together)[chair.id] == table.id
    stranded = apply_moves(graph, [slide, _slide(chair, 0.01, 0.0)])
    assert lost_seating(graph, stranded)


def test_parking_a_table_against_another_table_costs_more_than_open_floor():
    parked = _piece("parked_table", "Table", (0.0, 0.0, 0.4), (1.2, 0.8, 0.75))
    moving = _piece("moving_table", "Table", (3.0, 0.0, 0.4), (1.2, 0.8, 0.75))
    composition = Composition.of(SceneGraph(scan_id=node_id("cram_room"), nodes=[parked, moving]))
    jammed = composition.cost([_slide(moving, -1.75, 0.0)])
    open_floor = composition.cost([_slide(moving, 1.75, 0.0)])
    assert jammed > open_floor


def test_a_chair_left_inside_a_table_may_be_nudged_but_is_never_proposed():
    table = _piece("stack_table", "Table", (0.0, 0.0, 0.4), (1.6, 0.8, 0.75))
    chair = _piece("stack_chair", "Chair", (0.0, 0.3, 0.45), (0.5, 0.5, 0.9))
    graph = SceneGraph(scan_id=node_id("stacked_room"), nodes=[table, chair])
    composition = Composition.of(graph)
    nudge = [_slide(chair, 0.05, 0.0)]
    assert not violations(graph, apply_moves(graph, nudge))
    assert composition.leaves_overlaps(nudge)
    assert not composition.leaves_overlaps([_slide(chair, 0.0, 0.7)])


def test_whole_room_plans_are_square_unstacked_and_cheapest_first(room, pack):
    graph, scenario = room
    plans = arrangements(graph, scenario, pack)
    composition = Composition.of(graph)
    assert plans
    for plan in plans:
        after = apply_moves(graph, plan.moves)
        moved = {move.node_id for move in plan.moves}
        assert not violations(graph, after)
        assert not composition.leaves_overlaps(plan.moves)
        assert all(abs(squaring_turn(node, composition.axis_degrees)) < 0.5 for node in after.nodes if node.id in moved)
    assert [plan.disruption for plan in plans] == sorted(plan.disruption for plan in plans)
