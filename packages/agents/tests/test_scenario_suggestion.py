"""Scenario suggestion degenerate rooms: a target exactly at the reference
point, and a tiny room that only holds an outlet."""

import uuid

from standardphysics_agents.scenario_suggestion import suggest_path, suggest_scenario
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
    assert [stop.name for stop in scenario.stops] == ["Entrance", "Middle of the room", "Exit"], (
        "a room with no counter, bed or table gets no counter either"
    )
    positions = [stop.position for stop in scenario.stops]
    assert positions[0] == positions[-1], "entrance and exit are the same doorway"
    assert positions[1] != positions[0], "the middle of the room is its own spot"


def _shop(*extra: SceneNode, side: float = 6.0) -> SceneGraph:
    """A square shop, front door in the south wall, with whatever else is standing in it."""
    return SceneGraph(scan_id=node_id("shop_scan"), revision=0, nodes=[*_room(side=side, name="shop"), *extra])


def _counter(label="Service counter", at=(0.0, 1.2), size=(2.0, 0.6)):
    return _box(label, "object", label, "storage", (at[0], at[1], 0.5), (size[0], size[1], 1.0), False)


def _stop(scenario, name):
    return next(stop for stop in scenario.stops if stop.name == name).position


def test_pickup_stands_along_the_counter_not_inside_it():
    """Pickup was the counter stop moved a metre east, whichever way the
    counter ran, so a counter along the east wall had it standing inside the
    counter, between it and the wall."""
    counter = _counter(at=(2.2, 0.5), size=(0.6, 2.4))
    scenario = suggest_path(_shop(counter), ["pickup"])
    for name in ("Counter", "Pickup"):
        position = _stop(scenario, name)
        assert 1.2 < position.x < 1.6, f"{name} stands half a metre off the counter's customer side"
        assert -0.7 < position.y < 1.7, f"{name} stands alongside the counter"
    assert abs(_stop(scenario, "Counter").y - _stop(scenario, "Pickup").y) >= 0.9


def test_stools_in_front_do_not_move_the_counter_stop_off_the_counter():
    """Aimed at the middle of the room and snapped to the nearest open floor,
    the stop ended up past the end of the counter instead of in front of it."""
    counter = _counter(at=(-1.8, 1.2), size=(2.0, 0.6))
    stools = [
        _box(f"stool_{index}", "object", "Bar stool", "chair", (-2.6 + index * 0.55, 0.55, 0.4), (0.45, 0.45, 0.8), True)
        for index in range(4)
    ]
    position = _stop(suggest_path(_shop(counter, *stools), []), "Counter")
    assert -2.8 < position.x < -0.8, "in front of the counter, not beside its end"
    assert position.y < 0.9


def test_a_crowded_customer_side_does_not_send_the_counter_stop_behind_it():
    counter = _counter()
    case = _box("case", "object", "Display case", "storage", (0.0, -0.18, 0.45), (5.8, 2.1, 0.9), False)
    position = _stop(suggest_path(_shop(counter, case), []), "Counter")
    assert position.y < 0.9, "the staff side behind the counter is nearer, but it is not where customers order"


def test_a_counter_against_the_front_wall_is_served_from_its_open_side():
    counter = _counter(at=(1.6, -2.6), size=(1.6, 0.6))
    position = _stop(suggest_path(_shop(counter), []), "Counter")
    assert -2.2 < position.y < -1.6, "the side against the wall has nowhere to stand"


def test_a_named_pickup_counter_gets_the_pickup_stop():
    ordering = _counter()
    handoff = _counter(label="Pickup counter", at=(-2.2, -0.5), size=(1.2, 0.5))
    scenario = suggest_path(_shop(ordering, handoff), ["pickup"])
    pickup = next(stop for stop in scenario.stops if stop.name == "Pickup")
    assert pickup.anchor_node_id == handoff.id
    assert _stop(scenario, "Counter").y > 0.3
