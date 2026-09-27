"""The MeasurementProvider Lane C calls.

Grid for topology, geometry for the number. The occupancy grid finds the route
and names what pinches it; the width we report is the exact distance between
those two footprints, because 25 mm cells carry about an inch of error and a
threshold check cannot afford that.
"""

from __future__ import annotations

from collections import OrderedDict
from uuid import UUID

import numpy as np
from standardphysics_contracts import (
    ClearFloorResult,
    HeightResult,
    Scenario,
    SceneGraph,
    SceneNode,
    Vec3,
    WidthResult,
    to_inches,
    to_meters,
)

from .footprints import (
    Polygon,
    footprint,
    gap_between,
    gap_between_nodes,
    rotation_about_z,
)
from .occupancy import CELL_SIZE, Grid, blocks_floor, build_grid
from .routes import (
    PathResult,
    blockers_at,
    clearance_map,
    longest_run_below,
    path_clearances,
    runs_below,
    straddling_blockers,
    what_sealed_the_route,
    widest_path,
    world_path,
)
from .turns import Turn, measure_turn  # noqa: F401  (Turn is part of the API)

COUNTER_CLEAR_WIDTH = to_meters(48.0)
COUNTER_CLEAR_DEPTH = to_meters(30.0)
"""ADA 2010 305.3 clear floor space, laid out for a parallel approach with the
48 in side running along the counter."""
COUNTER_SLIDE_STEP = to_meters(2.0)
"""Spacing of the positions tried along a counter face for its clear floor space."""
MAX_CACHED_ROUTE_PATHS = 5_000

MAX_SQUARE = 3.0
"""Metres of side past which a clear square stops being measured."""

SQUARE_SEARCH_STEPS = 12
"""Halvings of `MAX_SQUARE`, which leaves under a millimetre of uncertainty,
well inside the grid's own 25 mm."""


def _square_is_clear(grid: Grid, at: Vec3, heading: tuple[float, float], side: float) -> bool:
    """Whether a `side` metre square centred on `at` and turned to `heading`
    covers no occupied cell, and stays on the grid."""
    half = side / 2
    reach = half * 2**0.5 + grid.cell_size
    row0, col0 = grid.to_cell(at.x - reach, at.y - reach)
    row1, col1 = grid.to_cell(at.x + reach, at.y + reach)
    if not (grid.contains(row0, col0) and grid.contains(row1, col1)):
        return False
    rows = np.arange(row0, row1 + 1)
    cols = np.arange(col0, col1 + 1)
    xs = grid.origin_x + (cols + 0.5) * grid.cell_size - at.x
    ys = grid.origin_y + (rows + 0.5) * grid.cell_size - at.y
    world_x, world_y = np.meshgrid(xs, ys)
    along = world_x * heading[0] + world_y * heading[1]
    across = -world_x * heading[1] + world_y * heading[0]
    inside = (np.abs(along) <= half) & (np.abs(across) <= half)
    return not grid.occupied[row0 : row1 + 1, col0 : col1 + 1][inside].any()


def _signature(graph: SceneGraph) -> tuple:
    """Everything the grid depends on, so moving, turning or resizing any node
    rebuilds it, and asking three questions about one layout does not."""
    return tuple(
        (str(node.id), node.kind, tuple(node.transform.m), node.dimensions.as_tuple())
        for node in graph.nodes
    )


def _outward_normal(node: SceneNode) -> tuple[float, float]:
    """Which way a customer stands, away from the node's local minus-Y face."""
    cos_t, sin_t = rotation_about_z(node)
    return (sin_t, -cos_t)


def _front_face_centre(node: SceneNode, outward: tuple[float, float]) -> Vec3:
    centre = node.transform.position
    reach = node.dimensions.y / 2
    return Vec3(
        x=centre.x + outward[0] * reach, y=centre.y + outward[1] * reach, z=0.0
    )


def _distance_to(point: Vec3, node: SceneNode) -> float:
    """Shortest distance from a point on the floor to a node's footprint."""
    speck = 1e-6
    probe = [
        (point.x - speck, point.y - speck), (point.x + speck, point.y - speck),
        (point.x + speck, point.y + speck), (point.x - speck, point.y + speck),
    ]
    return gap_between(footprint(node), probe)


def _rectangle(
    centre: Vec3, width: float, depth: float, cos_t: float, sin_t: float
) -> Polygon:
    """A width by depth rectangle turned to match the object it sits against."""
    half_w, half_d = width / 2, depth / 2
    corners = [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]
    return [
        (centre.x + x * cos_t - y * sin_t, centre.y + x * sin_t + y * cos_t)
        for x, y in corners
    ]


def _offsets_from_centre(slide: float) -> list[float]:
    """Offsets along a counter face out to `slide` either way, nearest the centre first."""
    steps = int(slide / COUNTER_SLIDE_STEP)
    offsets = [0.0]
    for index in range(1, steps + 1):
        offsets += [index * COUNTER_SLIDE_STEP, -index * COUNTER_SLIDE_STEP]
    if slide > steps * COUNTER_SLIDE_STEP:
        offsets += [slide, -slide]
    return offsets


def _slid(origin: Vec3, along: tuple[float, float], offset: float) -> Vec3:
    return Vec3(x=origin.x + along[0] * offset, y=origin.y + along[1] * offset, z=0.0)


def _cells_free(grid: Grid, xs: np.ndarray, ys: np.ndarray) -> bool:
    """Whether every sampled point lands on a free cell of the grid."""
    cols = np.trunc((xs - grid.origin_x) / grid.cell_size).astype(int)
    rows = np.trunc((ys - grid.origin_y) / grid.cell_size).astype(int)
    rows_count, cols_count = grid.shape
    inside = (rows >= 0) & (rows < rows_count) & (cols >= 0) & (cols < cols_count)
    return bool(inside.all()) and not grid.occupied[rows, cols].any()


def _band_clear(grid: Grid, origin: Vec3, outward, along, depth: float, half: float) -> bool:
    """Whether a band `2 * half` wide stays clear from the face out to `depth`.

    Sampling starts one cell out: the face itself is the counter, which is
    solid by definition, so sampling from zero always fails on the object whose
    space is being measured.
    """
    start = grid.cell_size
    if depth < start:
        return True
    columns = max(int(half * 2 / grid.cell_size), 1)
    rungs = max(int((depth - start) / grid.cell_size), 1)
    offsets = np.linspace(-half, half, columns + 1)[:, None]
    reaches = np.linspace(start, depth, rungs + 1)[None, :]
    xs = origin.x + along[0] * offsets + outward[0] * reaches
    ys = origin.y + along[1] * offsets + outward[1] * reaches
    return _cells_free(grid, xs, ys)


def _clear_depth(grid: Grid, origin: Vec3, outward, along) -> float:
    """How far out the required-width band stays clear."""
    step = grid.cell_size
    half = COUNTER_CLEAR_WIDTH / 2
    depth = 0.0
    while depth < COUNTER_CLEAR_DEPTH * 2:
        if not _band_clear(grid, origin, outward, along, depth + step, half):
            break
        depth += step
    return depth


def _clear_width(grid: Grid, origin: Vec3, outward, along) -> float:
    """How wide the band stays clear across the required depth."""
    step = grid.cell_size
    half = 0.0
    while half < COUNTER_CLEAR_WIDTH * 1.5:
        if not _band_clear(grid, origin, outward, along, COUNTER_CLEAR_DEPTH, half + step):
            break
        half += step
    return half * 2


def _approach_at(grid: Grid, origin: Vec3, outward, along) -> ClearFloorResult:
    """The clear floor measured out from one point on a counter face."""
    depth = _clear_depth(grid, origin, outward, along)
    width = _clear_width(grid, origin, outward, along)
    return ClearFloorResult(
        inches_wide=to_inches(width),
        inches_deep=to_inches(depth),
        center=Vec3(
            x=origin.x + outward[0] * COUNTER_CLEAR_DEPTH / 2,
            y=origin.y + outward[1] * COUNTER_CLEAR_DEPTH / 2,
            z=0.0,
        ),
        fits=width >= COUNTER_CLEAR_WIDTH and depth >= COUNTER_CLEAR_DEPTH,
    )


class PipelineMeasurements:
    """Implements MeasurementProvider against real geometry."""

    def __init__(self, cell_size: float = CELL_SIZE) -> None:
        self.cell_size = cell_size
        self._cache: dict[tuple, tuple[Grid, np.ndarray]] = {}
        self._paths: OrderedDict[tuple, PathResult] = OrderedDict()

    def _field(self, graph: SceneGraph) -> tuple[Grid, np.ndarray]:
        key = _signature(graph)
        if key not in self._cache:
            grid = build_grid(graph, self.cell_size)
            self._cache = {key: (grid, clearance_map(grid))}
        return self._cache[key]

    def _widest(self, graph, grid, clearance, start, goal, anchors) -> PathResult:
        key = (_signature(graph), start, goal, anchors)
        if key not in self._paths:
            self._paths[key] = widest_path(grid, clearance, start, goal, anchors=anchors)
            if len(self._paths) > MAX_CACHED_ROUTE_PATHS:
                self._paths.popitem(last=False)
        self._paths.move_to_end(key)
        return self._paths[key]

    def _leg(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> tuple[Grid, PathResult]:
        """The route for one leg, and the grid it was found on."""
        grid, clearance = self._field(graph)
        start, goal = scenario.stops[leg_index], scenario.stops[leg_index + 1]
        result = self._widest(
            graph, grid, clearance,
            grid.to_cell(start.position.x, start.position.y),
            grid.to_cell(goal.position.x, goal.position.y),
            (start.anchor_node_id, goal.anchor_node_id),
        )
        return grid, result

    def route_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult:
        grid, result = self._leg(graph, scenario, leg_index)
        start = scenario.stops[leg_index].position
        goal = scenario.stops[leg_index + 1].position
        if not result.reachable or result.pinch_cell is None:
            return WidthResult(
                inches=0.0,
                pinch_point=goal,
                blocking_node_ids=what_sealed_the_route(
                    grid,
                    grid.to_cell(start.x, start.y),
                    grid.to_cell(goal.x, goal.y),
                    graph=graph,
                ),
                reachable=False,
            )

        radius_cells = int(result.clearance_radius / grid.cell_size)
        straddling = straddling_blockers(grid, result.path, result.pinch_cell)
        blockers = list(straddling) if straddling else blockers_at(
            grid, result.pinch_cell, radius_cells
        )
        return WidthResult(
            inches=self._exact_width(
                graph,
                blockers,
                result.width_meters,
                pinch=grid.to_world(*result.pinch_cell),
            ),
            pinch_point=grid.to_world(*result.pinch_cell),
            blocking_node_ids=blockers,
            path=world_path(grid, result.path),
            reachable=True,
        )

    def _exact_width(
        self,
        graph: SceneGraph,
        blockers: list[UUID],
        grid_width: float,
        pinch: Vec3 | None = None,
    ) -> float:
        """The corridor width where the route actually passes.

        Not the gap between the two footprints. That is their closest approach
        anywhere, which is the corridor only when the route runs through it: on
        the fixture's counter leg the counter and a table come within 29.79 in
        of each other diagonally, while the route passes through 57 in of open
        floor several feet away.

        Measuring out from the pinch to each side gives the width at the point
        the bottleneck was found, which is the number the check is asking for.
        """
        if len(blockers) != 2:
            return to_inches(grid_width)
        a, b = (graph.by_id(node_id) for node_id in blockers)
        exact = gap_between_nodes(a, b)
        if pinch is None:
            return to_inches(exact)

        across = _distance_to(pinch, a) + _distance_to(pinch, b)
        if abs(across - exact) <= 2 * self.cell_size:
            # The pinch sits on the line between them, so their closest
            # approach is the corridor and the analytic gap is exact. The grid
            # answer carries about an inch of quantisation; this does not.
            return to_inches(exact)
        return to_inches(across)

    def turn_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult:
        """The clear width at a 180 degree turn.

        ADA 2010 403.5.2 sets three requirements, so this reports the one the
        section names for the turn itself and `turn_detail` carries all three
        plus the pivot. Deciding whether they pass belongs to the rule pack,
        not here.

        When the leg has no 180 degree turn the rule does not apply, and this
        returns the plain route width so a caller that ignores applicability
        still gets a true number rather than a zero.
        """
        turn = self.turn_detail(graph, scenario, leg_index)
        if turn is None or turn.at_turn_inches is None:
            return self.route_clear_width(graph, scenario, leg_index)

        return WidthResult(
            inches=turn.at_turn_inches,
            pinch_point=turn.apex,
            blocking_node_ids=[turn.pivot_id] if turn.pivot_id else [],
            path=self.route_clear_width(graph, scenario, leg_index).path,
            reachable=True,
        )

    def route_run_below(
        self,
        graph: SceneGraph,
        scenario: Scenario,
        leg_index: int,
        threshold_inches: float,
    ) -> float:
        """Longest unbroken stretch of this leg narrower than the threshold.

        ADA 2010 403.5.1 lets a route narrow to 32 inches for a run of 24
        inches at most, and a bottleneck alone cannot settle that. Pass the
        threshold from the rule pack so the number a person verified stays the
        only copy.
        """
        grid, result = self._leg(graph, scenario, leg_index)
        if not result.reachable:
            return 0.0
        return longest_run_below(
            grid, result.clearance, result.path, threshold_inches, exempt=result.exempt
        )

    def route_runs_below(
        self,
        graph: SceneGraph,
        scenario: Scenario,
        leg_index: int,
        threshold_inches: float,
    ) -> list[tuple[float, float]]:
        """Every stretch of this leg narrower than the threshold, as inches
        along the leg where each starts and ends.

        403.5.1's exception also asks that narrow stretches be separated by
        48 inches of full width route, which the longest run cannot answer.
        """
        grid, result = self._leg(graph, scenario, leg_index)
        if not result.reachable:
            return []
        return runs_below(
            grid, result.clearance, result.path, threshold_inches, exempt=result.exempt
        )

    def route_path_clearances(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> list[float | None]:
        """Corridor width in inches at each point of the drawn route.

        Runs parallel to `route_clear_width(...).path`, point for point, so a
        viewer can colour the line by how tight it is there. Both come from the
        same sampling, so they cannot drift apart.

        `None` marks a point inside the endpoint exemption, where the route
        wanders and its clearance means nothing. Feed the list straight to
        `Annotation.point_inches`.
        """
        grid, result = self._leg(graph, scenario, leg_index)
        if not result.reachable:
            return []
        return path_clearances(
            grid, result.clearance, result.path, exempt=result.exempt
        )

    def turn_detail(
        self,
        graph: SceneGraph,
        scenario: Scenario,
        leg_index: int,
        require_measured: bool = True,
    ) -> Turn | None:
        """All three widths and the element being turned around.

        None when the leg runs through without doubling back, and by default
        also when a zone ran off the end of the route. A caller comparing three
        widths against thresholds cannot do anything sensible with a missing
        one, and handing it a `None` to trip over is worse than saying there is
        no turn here to assess.

        Pass `require_measured=False` for the partial turn, when you would
        rather ask the owner about a turn we could not measure than say nothing
        about it.
        """
        grid, result = self._leg(graph, scenario, leg_index)
        if not result.reachable or not result.path:
            return None

        turn = measure_turn(graph, grid, result.clearance, world_path(grid, result.path))
        if turn is None or (require_measured and not turn.fully_measured):
            return None
        return turn

    def turning_space(self, graph: SceneGraph, at: Vec3) -> ClearFloorResult:
        _, clearance = self._field(graph)
        grid, _ = self._field(graph)
        row, col = grid.to_cell(at.x, at.y)
        if not grid.contains(row, col):
            return ClearFloorResult(
                inches_wide=0.0, inches_deep=0.0, center=at, fits=False
            )
        diameter = to_inches(float(clearance[row, col]) * 2)
        return ClearFloorResult(
            inches_wide=diameter,
            inches_deep=diameter,
            center=at,
            fits=diameter >= 60.0,
        )

    def largest_square(
        self, graph: SceneGraph, at: Vec3, heading: tuple[float, float]
    ) -> ClearFloorResult:
        """The largest clear square centred on `at`, two of its sides along
        `heading`.

        403.5.3's passing space is a 60 by 60 inch square, and the widest clear
        circle at a point is not that question. A 60 inch circle cannot hold the
        square, whose corners sit 42 inches from its centre, and a 60 inch wide
        aisle that holds the square has only a 60 inch circle in it. Aligning
        the square with the route is how a passing space sits in an aisle.

        Saturates at `MAX_SQUARE`, past which the answer describes the room
        rather than the space. `fits` says the reported square is clear, so the
        size is compared against the rule pack's number by the caller.
        """
        grid, _ = self._field(graph)
        if not grid.contains(*grid.to_cell(at.x, at.y)):
            return ClearFloorResult(inches_wide=0.0, inches_deep=0.0, center=at, fits=False)
        low, high = 0.0, MAX_SQUARE
        for _ in range(SQUARE_SEARCH_STEPS):
            middle = (low + high) / 2
            if _square_is_clear(grid, at, heading, middle):
                low = middle
            else:
                high = middle
        side = to_inches(low)
        return ClearFloorResult(inches_wide=side, inches_deep=side, center=at, fits=low > 0)

    def door_clear_width(self, graph: SceneGraph, door_id: UUID) -> WidthResult:
        """The doorway's opening, flagged as something a scan cannot settle.

        ADA 2010 404.2.3 measures between the door face and the stop with the
        door open 90 degrees. RoomPlan reports the leaf in the plane of the
        wall, which is the hole in the wall and not the width you can pass
        through: the open leaf, its hardware and the stop all eat into it.

        The number is still useful as an upper bound, so it is returned with
        `needs_measurement` set and Lane C turns it into a request rather than
        a pass.
        """
        door = graph.by_id(door_id)
        opening = max(door.dimensions.x, door.dimensions.y)
        return WidthResult(
            inches=to_inches(opening),
            pinch_point=door.transform.position,
            blocking_node_ids=[door_id],
            needs_measurement=True,
        )

    def counter_height(self, graph: SceneGraph, counter_id: UUID) -> HeightResult:
        counter = graph.by_id(counter_id)
        top = counter.transform.position.z + counter.dimensions.z / 2
        return HeightResult(
            inches=to_inches(top),
            node_id=counter_id,
            measured_at=counter.transform.position,
        )

    def counter_approach(
        self, graph: SceneGraph, counter_id: UUID, slide_meters: float = 0.0
    ) -> ClearFloorResult:
        """The clear floor space actually available in front of a counter.

        `inches_wide` and `inches_deep` are measurements of what is there, not
        restatements of what the rule wants; `fits` says whether they satisfy
        the 48 by 30 inch parallel approach in ADA 2010 305.3.

        Both saturate at twice the requirement. Past that the answer stops
        being about this counter and starts describing the room, and a check
        only needs to know the space is ample.

        `slide_meters` lets the space sit up to that far off the face centre,
        along the face, in steps of `COUNTER_SLIDE_STEP`. The clear position
        nearest the centre is reported; when none is clear, the centred one,
        where a repair clears the space whatever else stands along the face.
        How far is the caller's to say, from the rule.

        `center` is where the required rectangle sits, against the counter face
        and rotated with it, so it does not move as the measurement grows.
        """
        counter = graph.by_id(counter_id)
        grid, _ = self._field(graph)
        outward = _outward_normal(counter)
        along = (-outward[1], outward[0])
        face = _front_face_centre(counter, outward)
        positions = [_slid(face, along, offset) for offset in _offsets_from_centre(slide_meters)]
        promising = (
            origin for origin in positions
            if _band_clear(grid, origin, outward, along, COUNTER_CLEAR_DEPTH, COUNTER_CLEAR_WIDTH / 2)
        )
        for origin in promising:
            result = _approach_at(grid, origin, outward, along)
            if result.fits:
                return result
        return _approach_at(grid, face, outward, along)

    def _intruders(
        self, graph: SceneGraph, counter_id: UUID, space: Polygon
    ) -> list[UUID]:
        found = []
        for node in graph.nodes:
            if node.id == counter_id or not blocks_floor(node):
                continue
            if gap_between(footprint(node), space) == 0.0:
                found.append(node.id)
        return found
