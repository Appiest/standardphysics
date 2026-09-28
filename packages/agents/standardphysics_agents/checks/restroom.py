"""ADA 2010 603.2.1. Room to turn around inside a restroom, measured where the scan shows one.

A restroom is found by its water closet. The room it stands in is the floor the
toilet can see without looking through a wall, within `SEARCH_REACH_METERS`;
the check asks the clearance field for the widest clear circle at points on a
`GRID_STEP_METERS` grid there and passes when one reaches the rule's 60 inches.
A trash can, cart or chair left in the middle of the floor is what usually
takes the circle away, so this is something a rearrangement can fix.

Restrooms with no toilet in the graph are still asked about by
`questions.scan_cannot_see`, because a phone scan rarely shows the fixtures.
"""

from __future__ import annotations

import math

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import region_locus
from standardphysics_pipeline.footprints import contains_point, floor_polygon

from ..tracing import traced
from . import roles
from .clear_floor import fits_square, square_side
from .context import CheckContext
from .observation import Observation
from .rectangles import intruders, rectangle
from .walls import wall_faces

RULE_ID = "restroom_turning_space"
TOILET_LABELS = frozenset({"toilet", "water closet"})
SEARCH_REACH_METERS = 2.6
GRID_STEP_METERS = 0.1
WALL_SEAL_METERS = 0.1


def toilets(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if node.label.strip().casefold() in TOILET_LABELS]


def _crosses(a, b, c, d) -> bool:
    def side(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def _sealed(start, end, by: float = WALL_SEAL_METERS):
    """The wall stretched past both ends, so a sight line cannot slip between two walls at a corner."""
    length = math.dist(start, end) or 1.0
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    return (start[0] - ux * by, start[1] - uy * by), (end[0] + ux * by, end[1] + uy * by)


def same_room(origin, point, walls) -> bool:
    return not any(_crosses(origin, point, *_sealed(start, end)) for start, end in walls)


def _grid(origin):
    steps = int(SEARCH_REACH_METERS / GRID_STEP_METERS)
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            point = (origin[0] + i * GRID_STEP_METERS, origin[1] + j * GRID_STEP_METERS)
            if math.dist(point, origin) <= SEARCH_REACH_METERS:
                yield point


def _floor(graph: SceneGraph):
    floors = roles.floors(graph)
    return floor_polygon(floors[0]) if floors else None


def widest_turn(ctx: CheckContext, toilet: SceneNode):
    origin = (toilet.transform.position.x, toilet.transform.position.y)
    walls = wall_faces(ctx.graph)
    floor = _floor(ctx.graph)
    best = None
    for x, y in _grid(origin):
        if not (floor is None or contains_point(floor, (x, y))) or not same_room(origin, (x, y), walls):
            continue
        space = ctx.measure.turning_space(ctx.graph, Vec3(x=x, y=y, z=0.0))
        if best is None or square_side(space) > square_side(best):
            best = space
    return best


def _labels(graph: SceneGraph, node_ids) -> list[str]:
    wanted = set(node_ids)
    return [node.label for node in graph.nodes if node.id in wanted and node.movable]


@traced("checks.restroom_turning_space")
def restroom_turning_space(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    return [_in_restroom(ctx, rule, toilet) for toilet in toilets(ctx.graph)]


def _in_restroom(ctx: CheckContext, rule, toilet: SceneNode) -> Observation:
    space = widest_turn(ctx, toilet)
    circle = to_meters(rule.threshold)
    satisfied = space is not None and fits_square(space, rule.threshold)
    centre = space.center if space is not None else toilet.transform.position
    blockers = [] if satisfied else intruders(ctx.graph, rectangle(centre, circle, circle))
    return Observation(
        rule_id=RULE_ID,
        satisfied=satisfied,
        measured_inches=square_side(space) if space is not None else 0.0,
        required_inches=rule.threshold,
        locus=region_locus(space, blockers, circle=True) if space is not None else None,
        facts={"toilet": str(toilet.id), "blocking": _labels(ctx.graph, blockers)},
        dedupe_key=(RULE_ID, str(toilet.id)),
        seen_directly=True,
    )
