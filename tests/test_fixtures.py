"""The fixture shop is the contract three lanes develop against.

If these fail, Lane C's checks and Lane B's measurements are testing against
different geometry than they think.
"""

from standardphysics_contracts import to_inches, to_meters
from standardphysics_fixtures import (
    FIX_SHIFT_INCHES,
    PINCH_INCHES,
    FixtureMeasurements,
    build_graph,
    build_scenario,
    build_street_scenario,
    node_id,
)


def test_graph_builds():
    graph = build_graph()
    assert len(graph.nodes) == 19
    assert len(graph.movable()) == 12


def test_counter_and_walls_are_fixed():
    graph = build_graph()
    assert not graph.by_id(node_id("counter")).movable
    assert not graph.by_id(node_id("wall_north")).movable


def test_display_cases_leave_exactly_the_pinch():
    graph = build_graph()
    west = graph.by_id(node_id("case_west"))
    east = graph.by_id(node_id("case_east"))
    gap = (east.transform.position.x - east.dimensions.x / 2) - (
        west.transform.position.x + west.dimensions.x / 2
    )
    assert round(to_inches(gap), 6) == PINCH_INCHES


def test_route_measures_the_pinch_and_names_both_cases():
    graph, scenario = build_graph(), build_scenario()
    result = FixtureMeasurements().route_clear_width(graph, scenario, 0)
    assert round(result.inches, 6) == PINCH_INCHES
    assert set(result.blocking_node_ids) == {node_id("case_west"), node_id("case_east")}


def test_pinch_point_sits_between_the_cases():
    graph, scenario = build_graph(), build_scenario()
    point = FixtureMeasurements().route_clear_width(graph, scenario, 0).pinch_point
    assert abs(point.x) < 1e-9
    assert abs(point.y) < 1e-9


def test_the_documented_fix_reaches_thirty_six_inches():
    graph, scenario = build_graph(), build_scenario()
    graph.by_id(node_id("case_east")).transform.m[3] += to_meters(FIX_SHIFT_INCHES)
    result = FixtureMeasurements().route_clear_width(graph, scenario, 0)
    assert round(result.inches, 6) == 36.0


def test_overlapping_obstacles_do_not_read_as_a_passage():
    """Tables sit inside the display cases' span. A naive gap search finds a
    phantom 85-inch opening there."""
    graph, scenario = build_graph(), build_scenario()
    result = FixtureMeasurements().route_clear_width(graph, scenario, 0)
    assert result.inches < 40.0


def _inside_footprint(node, x: float, y: float) -> bool:
    p = node.transform.position
    return (abs(x - p.x) < node.dimensions.x / 2
            and abs(y - p.y) < node.dimensions.y / 2)


def test_every_stop_stands_on_open_floor():
    graph = build_graph()
    solid = [n for n in graph.obstacles() if n.kind not in ("floor", "door")]
    for stop in build_scenario().stops + build_street_scenario().stops:
        inside = [n.label for n in solid
                  if _inside_footprint(n, stop.position.x, stop.position.y)]
        assert inside == [], f"{stop.name} stands inside {inside}"


def test_every_anchor_names_a_node_in_the_shop():
    graph = build_graph()
    for stop in build_scenario().stops:
        assert stop.anchor_node_id is not None
        graph.by_id(stop.anchor_node_id)


def test_the_street_stop_is_outside_the_front_door():
    graph = build_graph()
    door = graph.by_id(node_id("door_front"))
    wall = graph.by_id(node_id("wall_south"))
    street = build_street_scenario().stops[0]
    assert street.anchor_node_id is None
    assert street.position.y < wall.transform.position.y - wall.dimensions.y / 2
    assert abs(street.position.x - door.transform.position.x) < door.dimensions.x / 2


def test_the_documented_fix_keeps_the_case_clear_of_the_wall():
    graph = build_graph()
    case = graph.by_id(node_id("case_east"))
    wall = graph.by_id(node_id("wall_east"))
    moved_face = case.transform.position.x + case.dimensions.x / 2 + to_meters(FIX_SHIFT_INCHES)
    wall_face = wall.transform.position.x - wall.dimensions.x / 2
    assert moved_face < wall_face


def test_the_counter_stands_as_high_as_the_one_in_the_lawsuit():
    from standardphysics_fixtures import COUNTER_HEIGHT_INCHES

    counter = build_graph().by_id(node_id("counter"))
    top = counter.transform.position.z + counter.dimensions.z / 2
    assert COUNTER_HEIGHT_INCHES == 47.0
    assert round(to_inches(top), 6) == 47.0


def test_the_lawsuit_variant_matches_the_complaint():
    """A lowered section exists, and the card reader sits on the 47 inch part."""
    from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario

    graph = build_lawsuit_graph()
    assert len(graph.nodes) == 21
    counter_stop = next(stop for stop in build_lawsuit_scenario().stops if stop.name == "Counter")
    high, low = graph.by_id(node_id("counter")), graph.by_id(node_id("counter_lowered"))
    reader = graph.by_id(node_id("card_reader"))
    top = lambda node: to_inches(node.transform.position.z + node.dimensions.z / 2)
    assert round(top(high), 6) == 47.0
    assert round(top(low), 6) == 36.0 and round(to_inches(low.dimensions.x), 6) == 36.0
    assert round(to_inches(reader.transform.position.z - reader.dimensions.z / 2), 6) == 47.0
    high_west = high.transform.position.x - high.dimensions.x / 2
    assert reader.transform.position.x - reader.dimensions.x / 2 > high_west
    assert reader.movable and not high.movable and not low.movable
    assert high_west < counter_stop.position.x < high.transform.position.x + high.dimensions.x / 2
