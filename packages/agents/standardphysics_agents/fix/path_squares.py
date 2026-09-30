"""Guesses that open a clear square somewhere along a route by pushing every piece standing in it out at once.

A passing space (ADA 2010 403.5.3) is a 60 inch square anywhere on a route
narrower than 60 inches. When none fits, the finding has nothing to measure,
so its measured width is zero and its locus is the route itself. No single
slide can show an improvement: until a whole square is clear the check still
finds none, and the gate refuses every piece moved on its own. On a real scan
with a sofa dragged into a room's middle, the menu found no option for it.

So this file walks every leg of the route, since a passing space anywhere on
it counts, and at each point where only movable pieces
stand in the square it pushes all of them sideways out of it, across the
route, to one side, the other, or each to its own nearer side. The squares
with the fewest pieces in them are tried first. A push that lands a piece on
another is nudged to the nearest legal floor outside the square, as
`clearing.py` does for a counter's clear floor. Nothing here is measured.
"""

from __future__ import annotations

import math

from standardphysics_contracts import (
    Finding,
    MeasurementProvider,
    NodeMove,
    Scenario,
    SceneGraph,
    SceneNode,
    Vec3,
    lies_flat,
    to_meters,
)
from standardphysics_pipeline import blocks_floor, footprint
from standardphysics_pipeline.footprints import Polygon, touching

from ..checks.rectangles import rectangle
from ..checks.route_geometry import sample_path
from .clearing import PUSH_MARGINS_INCHES, _landed, _rounded
from .strategies import Candidate

SAMPLE_METERS = 0.3
"""How far apart along the route the squares are tried."""
SQUARES_TRIED = 6

Heading = tuple[float, float]


def _heading(behind: Vec3, ahead: Vec3) -> Heading:
    length = math.hypot(ahead.x - behind.x, ahead.y - behind.y)
    return (1.0, 0.0) if length < 1e-9 else ((ahead.x - behind.x) / length, (ahead.y - behind.y) / length)


def _along(routes: list[list[Vec3]]) -> list[tuple[tuple[float, float], Heading]]:
    """Points every `SAMPLE_METERS` along each route, each with the direction the route runs there."""
    found = []
    for route in routes:
        points = sample_path(route, SAMPLE_METERS)
        for index, point in enumerate(points):
            heading = _heading(points[max(index - 1, 0)], points[min(index + 1, len(points) - 1)])
            found.append(((point.x, point.y), heading))
    return found


def _standing_in(graph: SceneGraph, square: Polygon) -> list[SceneNode]:
    return [node for node in graph.nodes if not lies_flat(node) and blocks_floor(node)
            and touching(footprint(node), square)]


def _span(node: SceneNode, centre: tuple[float, float], axis: Heading) -> tuple[float, float]:
    reach = [(x - centre[0]) * axis[0] + (y - centre[1]) * axis[1] for x, y in footprint(node)]
    return min(reach), max(reach)


def _slide(node: SceneNode, axis: Heading, meters: float) -> NodeMove:
    return NodeMove(node_id=node.id, delta_translation=Vec3(x=axis[0] * meters, y=axis[1] * meters, z=0.0))


def _pushes(blockers: list[SceneNode], centre: tuple[float, float], heading: Heading,
            reach: float) -> list[list[NodeMove]]:
    """Every piece across the route to one side just past `reach`, to the other side, or to its own nearer side."""
    across = (-heading[1], heading[0])
    sides = []
    for node in blockers:
        low, high = _span(node, centre, across)
        sides.append((node, reach - low, -reach - high))
    left = [_slide(node, across, plus) for node, plus, _ in sides]
    right = [_slide(node, across, minus) for node, _, minus in sides]
    nearer = [_slide(node, across, min(plus, minus, key=abs)) for node, plus, minus in sides]
    return [left, right, nearer]


def _squares(graph: SceneGraph, routes: list[list[Vec3]], side: float, pinned) -> list[tuple]:
    """The squares along the routes that only movable, unpinned pieces stand in, fewest pieces first."""
    found = []
    for centre, heading in _along(routes):
        square = rectangle(Vec3(x=centre[0], y=centre[1], z=0.0), side, side, heading)
        inside = _standing_in(graph, square)
        if inside and all(node.movable and node.id not in pinned for node in inside):
            found.append((len(inside), sum(node.dimensions.x * node.dimensions.y for node in inside),
                          centre, heading, inside, square))
    found.sort(key=lambda spot: spot[:2])
    return found[:SQUARES_TRIED]


def route_paths(graph: SceneGraph, scenario: Scenario, measure: MeasurementProvider) -> list[list[Vec3]]:
    """The drawn path of every leg of the route that can be walked."""
    legs = (measure.route_clear_width(graph, scenario, index) for index in range(len(scenario.stops) - 1))
    return [leg.path for leg in legs if leg.reachable]


def on_a_route(finding: Finding) -> bool:
    """Whether the finding's locus is a route rather than a spot, as a passing space none was found for is."""
    return finding.locus is not None and finding.locus.annotation.kind == "path" and finding.required_inches is not None


def square_clearing_moves(graph: SceneGraph, finding: Finding, routes: list[list[Vec3]],
                          pinned=frozenset()) -> list[Candidate]:
    """For a finding whose locus is a route, the pieces in a square of its required width somewhere on `routes`
    pushed out of it, the squares with fewest pieces in them first."""
    if not on_a_route(finding):
        return []
    assert finding.required_inches is not None
    side = to_meters(finding.required_inches)
    found: dict[tuple, Candidate] = {}
    for _, _, centre, heading, inside, square in _squares(graph, routes, side, pinned):
        for margin in PUSH_MARGINS_INCHES:
            for moves in _pushes(inside, centre, heading, side / 2 + to_meters(margin)):
                push = Candidate("open_a_square", moves, sum(math.hypot(m.delta_translation.x, m.delta_translation.y)
                                                             for m in moves))
                landed = _landed(graph, push, square)
                if landed is not None:
                    found.setdefault(_rounded(landed), landed)
    return list(found.values())
