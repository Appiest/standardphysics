"""The kitchen behind the counter is staff floor, so the customer checks leave it alone."""

from types import SimpleNamespace

import pytest
from standardphysics_agents.staff_areas import guess_staff_areas, in_staff_area, staff_areas
from standardphysics_contracts import Mat4, StaffArea, Vec3
from standardphysics_fixtures import build_graph, build_scenario, node_id

KITCHEN_DEPTH = 2.15
"""From the moved counter's back face at y=1.85 to the north wall's centre line at y=4."""


def _counter_mid_room():
    """The fixture shop with its counter pulled 2.1 m off the north wall, leaving a kitchen behind it."""
    graph = build_graph()
    counter = graph.by_id(node_id("counter"))
    moved = counter.model_copy(update={"transform": Mat4.translation(0.0, 1.5, counter.transform.position.z)})
    nodes = [moved if node.id == counter.id else node for node in graph.nodes]
    scenario = build_scenario()
    stops = [
        stop.model_copy(update={"position": Vec3(x=stop.position.x, y=1.0, z=0.0)})
        if stop.anchor_node_id == counter.id else stop
        for stop in scenario.stops
    ]
    return graph.model_copy(update={"nodes": nodes}), scenario.model_copy(update={"stops": stops})


def test_the_floor_behind_a_counter_is_guessed_to_be_the_kitchen():
    graph, scenario = _counter_mid_room()
    [kitchen] = guess_staff_areas(graph, scenario)
    assert kitchen.depth == pytest.approx(KITCHEN_DEPTH, abs=0.05)
    assert kitchen.holds(0.0, 3.0)
    assert not kitchen.holds(0.0, 1.0)
    assert not any(kitchen.holds(stop.position.x, stop.position.y) for stop in scenario.stops)


def test_a_counter_against_the_wall_has_no_kitchen_behind_it():
    assert guess_staff_areas(build_graph(), build_scenario()) == []


def test_the_owner_saying_there_is_none_overrides_the_guess():
    graph, scenario = _counter_mid_room()
    assert staff_areas(scenario.model_copy(update={"staff_only": []}), graph) == []


def test_a_finding_in_the_kitchen_is_left_out_and_one_on_the_shop_floor_is_kept():
    kitchen = StaffArea(centre=Vec3(x=0.0, y=3.0, z=0.0), width=6.0, depth=2.0)
    behind = SimpleNamespace(locus=SimpleNamespace(point=Vec3(x=1.0, y=3.5, z=0.0)))
    in_front = SimpleNamespace(locus=SimpleNamespace(point=Vec3(x=1.0, y=1.5, z=0.0)))
    assert in_staff_area(behind, [kitchen])
    assert not in_staff_area(in_front, [kitchen])


def test_a_turned_area_holds_points_in_its_own_frame():
    area = StaffArea(centre=Vec3(x=0.0, y=0.0, z=0.0), width=4.0, depth=1.0, rotation_z_degrees=90.0)
    assert area.holds(0.0, 1.9)
    assert not area.holds(1.9, 0.0)
