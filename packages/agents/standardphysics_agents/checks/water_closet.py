"""ADA 2010 604.2. How far the toilet's centreline sits from the wall beside it.

A toilet's box is longer front to back than it is wide, so its longer
horizontal side gives the direction it faces without trusting which local axis
the scanner called the front. The side wall is the nearest standing wall that
runs parallel to that side, and the distance is from the box's centre, which is
the centreline, to that wall's face. A box too square to tell front from side,
or no such wall close enough to be the one the grab bar goes on, becomes a
request for a tape measurement instead of a guess.

A scan that shows no toilet at all is asked about by `questions.scan_cannot_see`.
"""

from __future__ import annotations

import math

from standardphysics_contracts import SceneGraph, SceneNode, to_inches
from standardphysics_pipeline.footprints import Point, closest_point, floor_polygon, rotation_about_z

from ..rules import RuleSpec
from ..tracing import traced
from .context import CheckContext
from .heights import band_limit, band_reason
from .observation import Observation
from .restroom import toilets
from .vertical import mounted_locus
from .walls import standing_walls

RULE_ID = "water_closet_location"

SIDE_WALL_REACH_INCHES = 30.0
"""Past this, the nearest parallel wall is across the room rather than beside the toilet."""

PARALLEL_WITHIN_DEGREES = 15.0

DEPTH_OVER_WIDTH_MIN = 1.2
"""A toilet runs about 28 inches front to back and 18 across. A box closer to square than this says neither."""

Direction = tuple[float, float]


def _local_axes(node: SceneNode) -> tuple[Direction, Direction]:
    cos_t, sin_t = rotation_about_z(node)
    return (cos_t, sin_t), (-sin_t, cos_t)


def long_axis(node: SceneNode) -> Direction:
    """The horizontal direction the box extends furthest along."""
    x_axis, y_axis = _local_axes(node)
    return y_axis if node.dimensions.y > node.dimensions.x else x_axis


def depth_axis(node: SceneNode) -> Direction | None:
    """The toilet's front-to-back direction, or None when the box is too square to say."""
    longer = max(node.dimensions.x, node.dimensions.y)
    shorter = min(node.dimensions.x, node.dimensions.y)
    if longer < DEPTH_OVER_WIDTH_MIN * shorter:
        return None
    return long_axis(node)


def across(direction: Direction) -> Direction:
    return (-direction[1], direction[0])


def parallel(a: Direction, b: Direction) -> bool:
    return abs(a[0] * b[0] + a[1] * b[1]) >= math.cos(math.radians(PARALLEL_WITHIN_DEGREES))


def _wall_direction(outline: list[Point]) -> Direction:
    start, end = max(((a, b) for a in outline for b in outline), key=lambda pair: math.dist(*pair))
    length = math.dist(start, end) or 1.0
    return ((end[0] - start[0]) / length, (end[1] - start[1]) / length)


def side_wall(graph: SceneGraph, toilet: SceneNode) -> tuple[float, SceneNode] | None:
    """The nearest wall running alongside the toilet, and its face's distance from the centreline in inches."""
    depth = depth_axis(toilet)
    if depth is None:
        return None
    centre = (toilet.transform.position.x, toilet.transform.position.y)
    found = []
    for wall in standing_walls(graph):
        outline = floor_polygon(wall)
        if len(outline) < 2 or not parallel(_wall_direction(outline), depth):
            continue
        found.append((to_inches(math.dist(centre, closest_point(outline, centre))), wall))
    nearest = min(found, key=lambda pair: pair[0], default=None)
    if nearest is None or nearest[0] > SIDE_WALL_REACH_INCHES:
        return None
    return nearest


@traced("checks.water_closet_location")
def water_closet_location(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    return [_located(ctx, rule, toilet) for toilet in toilets(ctx.graph)]


def _located(ctx: CheckContext, rule: RuleSpec, toilet: SceneNode) -> Observation:
    found = side_wall(ctx.graph, toilet)
    if found is None:
        return _asks_for_the_tape(toilet)
    distance, wall = found
    low = rule.parameter("centerline_min_inches")
    reason = band_reason(distance, low, rule.threshold)
    return Observation(
        rule_id=RULE_ID,
        satisfied=reason == "measured",
        measured_inches=distance,
        required_inches=band_limit(reason, low, rule.threshold),
        relied_on=(toilet.id, wall.id),
        locus=mounted_locus(toilet),
        facts={"low": low, "high": rule.threshold},
        dedupe_key=(RULE_ID, str(toilet.id)),
        reason=reason,
        seen_directly=True,
    )


def _asks_for_the_tape(toilet: SceneNode) -> Observation:
    return Observation(
        rule_id=RULE_ID,
        satisfied=False,
        relied_on=(toilet.id,),
        locus=mounted_locus(toilet),
        dedupe_key=(RULE_ID, str(toilet.id)),
        reason="side_wall_unseen",
        seen_directly=True,
        asks_for="measurement",
    )
