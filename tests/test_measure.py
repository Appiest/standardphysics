"""Lane B's measurements, against the fixture shop.

The fixture's only route to the counter pinches to exactly 31 inches and opens
to exactly 36 when one display case moves 5. If these numbers drift, Lane C's
checks are measuring something other than what they think.
"""

import pytest
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, to_inches, to_meters
from standardphysics_fixtures import (
    FIX_SHIFT_INCHES,
    PINCH_INCHES,
    build_graph,
    build_scenario,
    node_id,
)
from standardphysics_pipeline.footprints import (
    contains_point,
    floor_polygon,
    footprint,
    gap_between,
    gap_between_nodes,
    polygon_bounds,
)
from standardphysics_pipeline.measure import PipelineMeasurements
from standardphysics_pipeline.occupancy import BLOCKING_HEIGHT, OUTSIDE_MARGIN, blocks_floor, build_grid
from standardphysics_pipeline.routes import clearance_map, widest_path


@pytest.fixture
def shop():
    return build_graph(), build_scenario(), PipelineMeasurements()


def test_route_measures_the_pinch_exactly(shop):
    graph, scenario, measure = shop
    assert measure.route_clear_width(graph, scenario, 0).inches == pytest.approx(
        PINCH_INCHES, abs=1e-6
    )


def test_route_names_both_display_cases(shop):
    graph, scenario, measure = shop
    result = measure.route_clear_width(graph, scenario, 0)
    assert set(result.blocking_node_ids) == {node_id("case_west"), node_id("case_east")}


def test_pinch_sits_between_the_cases(shop):
    graph, scenario, measure = shop
    point = measure.route_clear_width(graph, scenario, 0).pinch_point
    assert abs(point.x) < 0.05


def test_the_documented_fix_reaches_exactly_thirty_six(shop):
    graph, scenario, measure = shop
    graph.by_id(node_id("case_east")).transform.m[3] += to_meters(FIX_SHIFT_INCHES)
    assert measure.route_clear_width(graph, scenario, 0).inches == pytest.approx(
        36.0, abs=1e-6
    )


def test_route_returns_a_drawable_path(shop):
    graph, scenario, measure = shop
    assert len(measure.route_clear_width(graph, scenario, 0).path) > 2


def test_endpoints_do_not_set_the_bottleneck(shop):
    """Standing at a counter puts you within reach of it. That is the counter
    approach check's business, not the route width's."""
    graph, scenario, measure = shop
    assert measure.route_clear_width(graph, scenario, 0).inches > 20.0


def test_counter_height_comes_from_the_top_not_the_centre(shop):
    graph, _, measure = shop
    counter = graph.by_id(node_id("counter"))
    expected = counter.transform.position.z + counter.dimensions.z / 2
    assert measure.counter_height(graph, node_id("counter")).inches == pytest.approx(
        to_inches(expected)
    )


def test_a_blocked_route_reports_unreachable(shop):
    """Stretch the east case wall to wall, sealing every way through. Each case
    stops short of its wall, so meeting the west case alone leaves a gap."""
    graph, scenario, measure = shop
    east = graph.by_id(node_id("case_east"))
    east.dimensions.x = 6.0
    east.transform.m[3] = 0.0
    result = measure.route_clear_width(graph, scenario, 0)
    assert not result.reachable


def test_low_objects_do_not_block(shop):
    graph, _, _ = shop
    mat = graph.by_id(node_id("table_1")).model_copy(deep=True)
    mat.dimensions.z = BLOCKING_HEIGHT / 2
    mat.transform.m[11] = mat.dimensions.z / 2
    assert not blocks_floor(mat)


def test_doors_are_openings_not_obstacles(shop):
    graph, _, _ = shop
    assert not blocks_floor(graph.by_id(node_id("door_front")))


def test_grid_marks_the_walls(shop):
    graph, _, _ = shop
    grid = build_grid(graph)
    assert grid.occupied.any()


def _test1_floor() -> SceneNode:
    """The actual rotated zero-depth floor returned by browser rebuild test1."""
    return SceneNode(
        id=node_id("test1_floor"), kind="floor", label="Floor", raw_category="floor",
        dimensions=Vec3(x=11.220307, y=0.0, z=10.557267),
        transform=Mat4(m=[
            0.9996333, 0.0, -0.027079854, -3.1393082,
            0.027079882, 0.0, 0.9996333, 0.77995247,
            0.0, -1.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]),
    )


def test_rotated_zero_depth_floor_bounds_the_grid_by_its_world_polygon():
    floor = _test1_floor()
    polygon = floor_polygon(floor)
    min_x, min_y, max_x, max_y = polygon_bounds(polygon)
    aabb_corner = (min_x + 0.02, min_y + 0.02)
    assert not contains_point(polygon, aabb_corner)

    grid = build_grid(SceneGraph(scan_id=node_id("test1_grid"), nodes=[floor]))
    assert grid.origin_x == pytest.approx(min_x - OUTSIDE_MARGIN)
    assert grid.origin_y == pytest.approx(min_y - OUTSIDE_MARGIN)
    assert grid.to_cell(max_x + OUTSIDE_MARGIN - 0.01, max_y + OUTSIDE_MARGIN - 0.01) <= (grid.shape[0] - 1, grid.shape[1] - 1)


def test_a_degenerate_floor_fails_closed_instead_of_opening_a_walkable_strip():
    floor = _test1_floor().model_copy(update={"dimensions": Vec3(x=0.0, y=0.0, z=0.0)})
    grid = build_grid(SceneGraph(scan_id=node_id("degenerate_floor"), nodes=[floor]))
    assert grid.occupied.all()


def test_occupied_cells_name_an_object_or_are_the_world_edge(shop):
    """Ground beyond the building is closed off but belongs to no node, so a
    finding can never blame the edge of the world for a pinch."""
    graph, _, _ = shop
    grid = build_grid(graph)
    owned = grid.owner[grid.occupied]
    assert (owned >= 0).any()


def test_a_doorway_is_open_floor(shop):
    """A wall runs the full length of its side and the door sits inside it, so
    skipping the door is not enough: it has to be cut out of the wall."""
    graph, _, _ = shop
    grid = build_grid(graph)
    door = graph.by_id(node_id("door_front"))
    centre = door.transform.position
    row, col = grid.to_cell(centre.x, centre.y)
    assert grid.contains(row, col)
    assert not grid.occupied[row, col]


def test_a_route_can_come_in_from_the_street(shop):
    from standardphysics_contracts import Scenario, Stop, Vec3

    graph, _, measure = shop
    street = Scenario(
        name="From the street",
        stops=[
            Stop(name="Street", position=Vec3(x=0.0, y=-5.0, z=0.0)),
            Stop(name="Counter", position=Vec3(x=-0.8, y=3.1, z=0.0)),
        ],
    )
    result = measure.route_clear_width(graph, street, 0)
    assert result.reachable
    assert result.inches == pytest.approx(PINCH_INCHES, abs=1e-6)


def _open_a_wall(graph):
    """Take a side wall out, the way a scan that missed one leaves the room."""
    walls = [node for node in graph.nodes if node.kind == "wall"]
    west = min(walls, key=lambda node: node.transform.position.x)
    graph.nodes.remove(west)
    return west


def test_a_gap_in_the_walls_does_not_send_the_route_outside(shop):
    """Open ground beyond the floor is the widest corridor in any capture, so
    a bottleneck search offered a way out will take it and report the width of
    the garden. Both of these stops are in the room, so the trip is too."""
    graph, scenario, measure = shop
    _open_a_wall(graph)
    polygon = floor_polygon(next(n for n in graph.nodes if n.kind == "floor"))
    path = measure.route_clear_width(graph, scenario, 0).path
    assert path
    assert all(contains_point(polygon, (step.x, step.y), 0.05) for step in path)


def test_a_stop_outside_still_reaches_the_ground_it_stands_on(shop):
    """Holding the outside back cannot strand a customer arriving from the
    street: the floor only confines a trip whose two stops are both on it."""
    from standardphysics_contracts import Scenario, Stop, Vec3

    graph, _, measure = shop
    polygon = floor_polygon(next(n for n in graph.nodes if n.kind == "floor"))
    street = Scenario(
        name="From the street",
        stops=[
            Stop(name="Street", position=Vec3(x=0.0, y=-5.0, z=0.0)),
            Stop(name="Counter", position=Vec3(x=-0.8, y=3.1, z=0.0)),
        ],
    )
    path = measure.route_clear_width(graph, street, 0).path
    assert any(not contains_point(polygon, (step.x, step.y)) for step in path)


def test_the_search_does_not_wander_off_into_the_padding(shop):
    """An open door lets the search leave the building. Beyond the floor the
    grid is empty space with excellent clearance, so an unbounded search
    explores all of it before reaching the goal."""
    import time

    graph, scenario, measure = shop
    started = time.time()
    measure.route_clear_width(graph, scenario, 0)
    assert time.time() - started < 5.0


def test_touching_footprints_have_no_gap(shop):
    graph, _, _ = shop
    case = graph.by_id(node_id("case_west"))
    assert gap_between_nodes(case, case) == 0.0


def test_footprint_follows_rotation(shop):
    graph, _, _ = shop
    table = graph.by_id(node_id("table_1"))
    corners = footprint(table)
    assert len(corners) == 4
    width = max(x for x, _ in corners) - min(x for x, _ in corners)
    assert width == pytest.approx(table.dimensions.x)


def test_separated_rectangles_measure_their_gap():
    a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    b = [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0), (2.0, 1.0)]
    assert gap_between(a, b) == pytest.approx(1.0)


def test_widest_path_prefers_the_wide_way_round(shop):
    graph, scenario, _ = shop
    grid = build_grid(graph)
    clearance = clearance_map(grid)
    start = grid.to_cell(scenario.stops[0].position.x, scenario.stops[0].position.y)
    goal = grid.to_cell(scenario.stops[1].position.x, scenario.stops[1].position.y)
    result = widest_path(grid, clearance, start, goal)
    assert result.reachable
    assert result.clearance_radius > 0


def test_a_run_below_a_threshold_is_measured_along_the_route(shop):
    """403.5.1 permits 32 in for a run of 24 in at most, and a bottleneck says
    nothing about how long the route stays narrow."""
    graph, scenario, measure = shop
    run = measure.route_run_below(graph, scenario, 0, 36.0)
    assert run > 24.0


def test_nothing_runs_below_a_threshold_the_route_never_reaches(shop):
    graph, scenario, measure = shop
    assert measure.route_run_below(graph, scenario, 0, 1.0) == 0.0


def test_the_threshold_is_an_argument_not_a_constant(shop):
    """It belongs to the rule pack, where a person checked it against source."""
    graph, scenario, measure = shop
    wide = measure.route_run_below(graph, scenario, 0, 36.0)
    narrow = measure.route_run_below(graph, scenario, 0, 32.0)
    assert wide >= narrow


def test_counter_approach_reports_what_it_measured(shop):
    """Not a restatement of the 48 by 30 the rule asks for."""
    graph, _, measure = shop
    result = measure.counter_approach(graph, node_id("counter"))
    assert result.inches_wide != 48.0 or result.inches_deep != 30.0
    assert result.fits


def test_per_point_clearance_runs_parallel_to_the_path(shop):
    graph, scenario, measure = shop
    result = measure.route_clear_width(graph, scenario, 0)
    assert len(measure.route_path_clearances(graph, scenario, 0)) == len(result.path)


def test_exempt_points_report_nothing_rather_than_a_number(shop):
    """Inside the exemption the route wanders, so a clearance there measures
    nothing and would paint the doorway as the tightest part of the trip."""
    graph, scenario, measure = shop
    values = measure.route_path_clearances(graph, scenario, 0)
    assert any(value is None for value in values)


def test_measured_points_never_fall_below_the_bottleneck(shop):
    graph, scenario, measure = shop
    result = measure.route_clear_width(graph, scenario, 0)
    measured = [v for v in measure.route_path_clearances(graph, scenario, 0) if v]
    assert min(measured) >= result.inches - 1.0


def _seal_the_aisle(graph):
    case = graph.by_id(node_id("case_east"))
    case.dimensions.x = 6.0
    case.transform.m[3] = 0.0
    return case


def test_a_sealed_route_names_what_sealed_it(shop):
    """A blocked route with no named obstacle is a dead end for everyone
    downstream: nothing to highlight, nothing to frame, nothing to try moving."""
    graph, scenario, measure = shop
    _seal_the_aisle(graph)
    result = measure.route_clear_width(graph, scenario, 0)
    assert not result.reachable
    assert result.blocking_node_ids


def test_it_names_furniture_rather_than_the_building(shop):
    """With a wall gone you could step outside and come back through the front
    door, so walls technically open the route. The owner needs to hear about
    what they can move."""
    graph, scenario, measure = shop
    _seal_the_aisle(graph)
    result = measure.route_clear_width(graph, scenario, 0)
    assert all(graph.by_id(node).movable for node in result.blocking_node_ids)


def test_an_open_route_still_names_its_pinch(shop):
    graph, scenario, measure = shop
    result = measure.route_clear_width(graph, scenario, 0)
    assert result.reachable
    assert len(result.blocking_node_ids) == 2


def test_a_run_ignores_the_exempt_stretches(shop):
    """Inside the exemption the route wanders and brushes whatever is nearby.
    On leg 2, 75 of the 124 sub-36 inch cells were exempt ones."""
    graph, scenario, measure = shop
    assert measure.route_run_below(graph, scenario, 3, 36.0) == 0.0


def test_a_leg_that_is_never_narrow_reports_no_run(shop):
    graph, scenario, measure = shop
    wide = measure.route_clear_width(graph, scenario, 3).inches
    assert wide > 36.0
    assert measure.route_run_below(graph, scenario, 3, 36.0) == 0.0


def test_repeated_measurements_reuse_paths_but_moves_invalidate_them(shop, monkeypatch):
    import standardphysics_pipeline.measure as module
    graph, scenario, _ = shop
    original = module.widest_path
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'widest_path', counted)
    measure = module.PipelineMeasurements()
    before = measure.route_clear_width(graph, scenario, 1)
    measure.route_path_clearances(graph, scenario, 1)
    measure.route_run_below(graph, scenario, 1, 36)
    assert len(calls) == 1
    from standardphysics_agents.fix import apply_moves
    from standardphysics_contracts import NodeMove, Vec3
    moved = apply_moves(graph, [NodeMove(node_id=graph.movable()[0].id, delta_translation=Vec3(x=0.05, y=0, z=0))])
    measure.route_clear_width(moved, scenario, 1)
    assert len(calls) == 2
    assert measure.route_clear_width(graph, scenario, 1) == before
    assert len(calls) == 2


def test_exhaustive_customer_route_matrix_stays_in_the_path_cache(shop, monkeypatch):
    import standardphysics_pipeline.measure as module
    from standardphysics_pipeline.routes import PathResult

    graph, _, _ = shop
    calls = []

    def counted(grid, clearance, start, goal):
        calls.append((start, goal))
        return PathResult(1.0, start, [start, goal], reachable=True)

    monkeypatch.setattr(module, "widest_path", counted)
    measure = module.PipelineMeasurements()
    scenarios = [
        Scenario(
            name=f"route {index}",
            stops=[
                Stop(name="Door", position=Vec3(x=0.0, y=-3.7, z=0.0)),
                Stop(
                    name=f"Furniture {index}",
                    position=Vec3(
                        x=-2.8 + (index % 17) * 0.35,
                        y=-3.2 + (index // 17) * 0.4,
                        z=0.0,
                    ),
                ),
            ],
        )
        for index in range(257)
    ]

    for scenario in scenarios:
        measure.route_clear_width(graph, scenario, 0)
    measure.route_clear_width(graph, scenarios[0], 0)

    assert len(calls) == len(scenarios)
