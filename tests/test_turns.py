"""ADA 2010 403.5.2, the 180 degree turn.

The fixture shop has a straight route, so these build a U-shaped one: a
partition running most of the way across the room, forcing customers around
its end to reach the counter.
"""

import uuid

import pytest
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3
from standardphysics_pipeline.measure import PipelineMeasurements
from standardphysics_pipeline.turns import Turn, find_turn


def node(name, centre, dims, kind="object", movable=False):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, name),
        kind=kind,
        label=name,
        raw_category=name,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=movable,
    )


def u_shaped_shop(gap_from_east_wall: float) -> SceneGraph:
    """A partition from the west wall, leaving a gap at the east end."""
    width, depth, height = 6.0, 8.0, 3.0
    partition_length = width - 0.05 - gap_from_east_wall
    partition_centre = -width / 2 + 0.05 + partition_length / 2

    nodes = [
        node("floor", (0, 0, 0), (width, depth, 0.01), kind="floor"),
        node("wall_s", (0, -depth / 2, height / 2), (width, 0.1, height), kind="wall"),
        node("wall_n", (0, depth / 2, height / 2), (width, 0.1, height), kind="wall"),
        node("wall_w", (-width / 2, 0, height / 2), (0.1, depth, height), kind="wall"),
        node("wall_e", (width / 2, 0, height / 2), (0.1, depth, height), kind="wall"),
        node("partition", (partition_centre, 0.0, 0.6), (partition_length, 0.3, 1.2)),
    ]
    return SceneGraph(scan_id=uuid.uuid4(), nodes=nodes)


def scenario_across_the_partition() -> Scenario:
    return Scenario(
        name="Order a drink",
        stops=[
            Stop(name="Entrance", position=Vec3(x=-2.0, y=-3.4, z=0.0)),
            Stop(name="Counter", position=Vec3(x=-2.0, y=3.4, z=0.0)),
        ],
    )


@pytest.fixture
def narrow_turn():
    graph = u_shaped_shop(gap_from_east_wall=1.0)
    return graph, scenario_across_the_partition(), PipelineMeasurements()


def test_a_straight_run_has_no_turn():
    points = [Vec3(x=0.0, y=y / 10, z=0.0) for y in range(40)]
    assert find_turn(points) is None


def test_a_route_round_a_partition_has_one(narrow_turn):
    graph, scenario, measure = narrow_turn
    assert measure.turn_detail(graph, scenario, 0) is not None


def test_the_turn_reports_three_zones(narrow_turn):
    graph, scenario, measure = narrow_turn
    turn = measure.turn_detail(graph, scenario, 0)
    assert turn.approach_inches > 0
    assert turn.at_turn_inches > 0
    assert turn.leaving_inches > 0


def test_the_turn_names_what_it_goes_around(narrow_turn):
    graph, scenario, measure = narrow_turn
    turn = measure.turn_detail(graph, scenario, 0)
    assert turn.pivot_id is not None







def test_turn_clear_width_falls_back_on_a_straight_leg():
    """No 180 means the rule does not apply, so return a true number rather
    than a zero a caller might read as a failure."""
    from standardphysics_fixtures import build_graph, build_scenario

    measure = PipelineMeasurements()
    graph, scenario = build_graph(), build_scenario()
    assert measure.turn_clear_width(graph, scenario, 0).inches == pytest.approx(31.0)


def test_the_turn_type_carries_no_thresholds():
    """403.5.2's numbers live in Lane C's rule pack, where a person has checked
    them against the source. A second copy here could only drift."""
    import standardphysics_pipeline.turns as turns

    assert not hasattr(Turn, "passes")
    assert not hasattr(Turn, "in_scope")
    assert not [name for name in vars(turns) if name.endswith("_REQUIRED")]


def test_turn_clear_width_reports_the_width_at_the_turn(narrow_turn):
    graph, scenario, measure = narrow_turn
    turn = measure.turn_detail(graph, scenario, 0)
    result = measure.turn_clear_width(graph, scenario, 0)
    assert result.inches == pytest.approx(turn.at_turn_inches)


def test_an_unmeasurable_zone_reports_nothing_not_zero():
    """A zone that runs off the end of the route has no width. Calling that
    0.0 in turns "we could not measure this" into the most severe finding in
    the report."""
    from standardphysics_fixtures import build_graph, build_scenario

    measure = PipelineMeasurements()
    graph, scenario = build_graph(), build_scenario()
    turn = measure.turn_detail(graph, scenario, 1, require_measured=False)
    assert turn is not None
    assert turn.approach_inches is None
    assert not turn.fully_measured


def test_a_partly_measured_turn_is_withheld_by_default():
    """A caller comparing three widths against thresholds cannot do anything
    with a missing one, so the default is to say there is no turn to assess
    rather than hand over a None to trip over."""
    from standardphysics_fixtures import build_graph, build_scenario

    measure = PipelineMeasurements()
    graph, scenario = build_graph(), build_scenario()
    assert measure.turn_detail(graph, scenario, 1) is None


def test_a_measured_turn_says_so(narrow_turn):
    graph, scenario, measure = narrow_turn
    assert measure.turn_detail(graph, scenario, 0).fully_measured


def test_a_route_too_short_to_hold_a_turn_reports_none():
    """403.5.2 measures approaching, at, and leaving. Below that much route
    there are no zones, and a trimmed stub is all noise."""
    from standardphysics_contracts import Scenario, Stop, Vec3
    from standardphysics_fixtures import build_graph

    measure = PipelineMeasurements()
    graph = build_graph()
    hop = Scenario(
        name="A step sideways",
        stops=[
            Stop(name="Here", position=Vec3(x=-0.4, y=-2.0, z=0.0)),
            Stop(name="There", position=Vec3(x=0.4, y=-2.0, z=0.0)),
        ],
    )
    assert measure.turn_detail(graph, hop, 0) is None


def test_turn_clear_width_falls_back_when_the_turn_was_not_measured():
    from standardphysics_fixtures import build_graph, build_scenario

    measure = PipelineMeasurements()
    graph, scenario = build_graph(), build_scenario()
    result = measure.turn_clear_width(graph, scenario, 1)
    assert result.inches > 0
