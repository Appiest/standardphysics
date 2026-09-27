"""ADA 2010 304.3. Room to turn around where the route doubles back.

304 is scoped by the sections that call for it rather than applying everywhere,
so this runs at dead ends: a stop the customer has to reverse out of. Turning
around in the middle of an open floor is not a requirement and reporting it as
one would put a card in front of the owner that no section backs.

304.3.1 fixes the circle's size, not where its centre sits. The circle is
first tried at the stop backed off by a clear-floor depth, the spot a
customer arriving head-on turns in. A customer who reaches a counter moving
along it, as at a pickup point, stands beside the counter with that spot
still beside the counter, so the fixed spot would fail every such counter
however the room is laid out. When it fails, any centre within one radius of
the stop counts. The stop is then inside the circle, and because the whole
circle has to be clear, so is the way from the stop to the centre.
"""

from __future__ import annotations

import math

from standardphysics_contracts import Vec3, to_meters
from standardphysics_pipeline import region_locus

from ..rules import RuleSpec
from ..tracing import traced
from .clear_floor import fits_turning_circle, square_side
from .context import CheckContext
from .observation import Observation
from .rectangles import intruders, rectangle
from .route_geometry import reversal_stops, setback_point

RULE_ID = "turning_space"

APPROACH_SETBACK_INCHES = 30.0
"""The depth of a clear floor space, ADA 2010 305.3."""

SEARCH_STEP_INCHES = 6.0
"""Spacing of the candidate centres tried around the stop."""


@traced("checks.turning_space")
def turning_space(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    stops = ctx.scenario.stops
    return [_at_stop(ctx, rule, index) for index in reversal_stops(stops)]


def _centres_near(stop: Vec3, radius: float) -> list[Vec3]:
    """Lattice points within one radius of the stop, nearest first."""
    step = to_meters(SEARCH_STEP_INCHES)
    reach = int(radius // step)
    offsets = [(i * step, j * step) for i in range(-reach, reach + 1) for j in range(-reach, reach + 1)
               if math.hypot(i * step, j * step) <= radius]
    return [Vec3(x=stop.x + dx, y=stop.y + dy, z=stop.z) for dx, dy in sorted(offsets, key=lambda o: math.hypot(*o))]


def _turning_space_near(ctx: CheckContext, rule: RuleSpec, stop: Vec3, setback: Vec3):
    """The fixed setback spot if a circle fits there, else the nearest spot to the stop where one fits.

    With no fit anywhere, the setback spot is reported, so the finding keeps pointing where a customer
    arriving head-on would turn.
    """
    space = ctx.measure.turning_space(ctx.graph, setback)
    if fits_turning_circle(space, rule):
        return space, setback
    radius = to_meters(rule.parameter("circle_diameter_inches")) / 2
    for centre in _centres_near(stop, radius):
        candidate = ctx.measure.turning_space(ctx.graph, centre)
        if fits_turning_circle(candidate, rule):
            return candidate, centre
    return space, setback


def _at_stop(ctx: CheckContext, rule: RuleSpec, stop_index: int) -> Observation:
    stops = ctx.scenario.stops
    setback = setback_point(stops, stop_index, to_meters(APPROACH_SETBACK_INCHES))
    space, at = _turning_space_near(ctx, rule, stops[stop_index].position, setback)
    satisfied = fits_turning_circle(space, rule)
    return Observation(
        rule_id=RULE_ID,
        satisfied=satisfied,
        measured_inches=square_side(space),
        required_inches=rule.parameter("circle_diameter_inches"),
        locus=region_locus(space, intruders(ctx.graph, rectangle(
            at, to_meters(rule.parameter("circle_diameter_inches")),
            to_meters(rule.parameter("circle_diameter_inches")),
        )) if not satisfied else [], circle=True),
        facts={"stop": stops[stop_index].name},
        dedupe_key=(RULE_ID, stops[stop_index].name),
        reason="measured" if satisfied else "too_tight",
    )
