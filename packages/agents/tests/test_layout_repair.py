"""Deterministic seating repair prefers intact table-chair groups."""

from standardphysics_agents.fix import apply_moves
from standardphysics_agents.fix.composition import tables_by_chair
from standardphysics_agents.fix.moves import move_node
from standardphysics_agents.layout_repair import (
    aesthetics_cost,
    moves_between,
    prefer_layout,
    seating_broken,
    try_deterministic_repair,
)
from standardphysics_contracts import Mat4, NodeMove, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures.shop import node_id


def _piece(name, label, centre, dims, heading=0.0):
    node = SceneNode(
        id=node_id(name),
        kind="object",
        label=label,
        raw_category="furniture",
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=True,
    )
    if heading:
        return move_node(
            node,
            NodeMove(
                node_id=node.id,
                delta_translation=Vec3(x=0.0, y=0.0, z=0.0),
                delta_rotation_z_degrees=heading,
            ),
        )
    return node


def test_prefer_layout_picks_the_cheaper_seating_preserving_move():
    table = _piece("pref_table", "Table", (0.0, 0.0, 0.4), (1.2, 0.8, 0.75))
    chair = _piece("pref_chair", "Chair", (0.0, -0.7, 0.45), (0.5, 0.5, 0.9))
    base = SceneGraph(scan_id=node_id("pref_room"), nodes=[table, chair])
    near = apply_moves(
        base,
        [
            NodeMove(
                node_id=table.id,
                delta_translation=Vec3(x=0.2, y=0.0, z=0.0),
                delta_rotation_z_degrees=0.0,
            ),
            NodeMove(
                node_id=chair.id,
                delta_translation=Vec3(x=0.2, y=0.0, z=0.0),
                delta_rotation_z_degrees=0.0,
            ),
        ],
    )
    far = apply_moves(
        base,
        [
            NodeMove(
                node_id=table.id,
                delta_translation=Vec3(x=1.8, y=0.0, z=0.0),
                delta_rotation_z_degrees=90.0,
            ),
            NodeMove(
                node_id=chair.id,
                delta_translation=Vec3(x=1.8, y=0.0, z=0.0),
                delta_rotation_z_degrees=90.0,
            ),
        ],
    )
    assert aesthetics_cost(base, near) < aesthetics_cost(base, far)
    assert prefer_layout(base, [far, near]) == near
    assert not seating_broken(base, near)
    assert tables_by_chair(near)[chair.id] == table.id


def test_moves_between_recovers_slide_and_turn():
    table = _piece("move_table", "Table", (1.0, 1.0, 0.4), (1.2, 0.8, 0.75))
    before = SceneGraph(scan_id=node_id("move_room"), nodes=[table])
    after = apply_moves(
        before,
        [
            NodeMove(
                node_id=table.id,
                delta_translation=Vec3(x=0.5, y=-0.25, z=0.0),
                delta_rotation_z_degrees=90.0,
            )
        ],
    )
    [move] = moves_between(before, after)
    assert move.node_id == table.id
    assert (move.delta_translation.x, move.delta_translation.y) == (0.5, -0.25)
    assert move.delta_rotation_z_degrees == 90.0


def test_deterministic_repair_returns_none_without_rearrangeable_work(
    graph, scenario, pipeline, pack, ledger
):
    # Empty workflows/profiles still exercise the arrangements path safely.
    result = try_deterministic_repair(
        graph,
        scenario=scenario,
        workflows=[],
        profiles=[],
        measure=pipeline,
        rules=pack,
        ledger=ledger,
    )
    assert result is None or not seating_broken(graph, result)
