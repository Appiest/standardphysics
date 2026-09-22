"""Scenario suggestion degenerate rooms: a target exactly at the reference
point, and a tiny room that only holds an outlet."""

import uuid

import pytest
from standardphysics_api.scenario import suggest_scenario
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

NAMESPACE = uuid.UUID("6f1d2f9a-0d3f-4a1e-9b2c-7ef00a0a0002")


def node_id(name: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, name)


def _box(name, kind, label, category, center, dims, movable, parent=None):
    node = SceneNode(
        id=node_id(name),
        kind=kind,
        label=label,
        raw_category=category,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*center),
        movable=movable,
    )
    if parent is not None:
        node = node.model_copy(update={"parent_id": parent, "relation": "rests_on"})
    return node


def _room(side: float = 5.0, name: str = "room") -> list[SceneNode]:
    half = side / 2
    return [
        _box(f"{name}_south", "wall", "Wall", "wall", (0.0, -half, 1.5), (side, 0.1, 3.0), False),
        _box(f"{name}_north", "wall", "Wall", "wall", (0.0, half, 1.5), (side, 0.1, 3.0), False),
        _box(f"{name}_west", "wall", "Wall", "wall", (-half, 0.0, 1.5), (0.1, side, 3.0), False),
        _box(f"{name}_east", "wall", "Wall", "wall", (half, 0.0, 1.5), (0.1, side, 3.0), False),
        _box(f"{name}_floor", "floor", "Floor", "floor", (0.0, 0.0, 0.0), (side, side, 0.01), False),
        _box(f"{name}_door", "door", "Door", "door", (0.0, -half, 1.05), (0.9, 0.1, 2.1), False),
    ]


def _with_centre_table(graph: SceneGraph) -> SceneGraph:
    table = _box("centre_table", "object", "Table", "table", (0.0, 0.0, 0.375), (0.6, 0.6, 0.75), True)
    return graph.model_copy(update={"nodes": [*graph.nodes, table]})


def _with_centre_counter(graph: SceneGraph) -> SceneGraph:
    counter = _box("centre_counter", "object", "Counter", "storage", (0.0, 0.0, 0.5), (1.2, 0.7, 1.0), False)
    return graph.model_copy(update={"nodes": [*graph.nodes, counter]})


def test_a_table_at_the_room_centre_still_gets_a_side_stop():
    """The table sits exactly on the reference point the seat-side heuristic
    aims from, which used to leave `min(exits)` with nothing to pick from."""
    graph = SceneGraph(
        scan_id=node_id("centre_table_scan"),
        revision=0,
        nodes=_room() + [_box("centre_table", "object", "Table", "table", (0.0, 0.0, 0.375), (0.6, 0.6, 0.75), True)],
    )
    scenario = suggest_scenario(graph)
    seat = next(stop for stop in scenario.stops if stop.name == "Seat")
    assert seat.position.z == 0.0
    assert abs(seat.position.x) + abs(seat.position.y) > 0.5


def test_a_counter_at_the_room_centre_still_gets_a_beside_stop():
    graph = SceneGraph(
        scan_id=node_id("centre_counter_scan"),
        revision=0,
        nodes=_room() + [_box("centre_counter", "object", "Ordering counter", "storage", (0.0, 0.0, 0.5), (1.2, 0.7, 1.0), False)],
    )
    scenario = suggest_scenario(graph)
    counter_stop = next(stop for stop in scenario.stops if stop.name == "Counter")
    assert abs(counter_stop.position.x) + abs(counter_stop.position.y) > 0.5


def test_a_tiny_room_with_only_an_outlet_suggests_without_crashing():
    """Two metres square, nothing to aim at but an outlet: the suggestion
    must degrade rather than raise."""
    nodes = _room(side=2.0, name="tiny")
    outlet = _box(
        "outlet", "outlet", "Outlet", "outlet",
        (-0.95, 0.0, 0.45), (0.08, 0.03, 0.12), False, parent=node_id("tiny_west"),
    )
    graph = SceneGraph(
        scan_id=node_id("tiny_outlet_scan"), revision=0, nodes=[*nodes, outlet]
    )
    scenario = suggest_scenario(graph)
    assert len(scenario.stops) == 5
    positions = [stop.position for stop in scenario.stops]
    assert positions[0] == positions[-1], "entrance and exit are the same doorway"
    middle = {(round(p.x, 3), round(p.y, 3)) for p in positions[1:-1]}
    assert len(middle) == 3, "counter, pickup and seat each got their own spot"
