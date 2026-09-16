"""ADA 2010 304.3. Room to turn around where the route doubles back.

304 is scoped by the sections that call for it rather than applying everywhere,
so this runs at dead ends: a stop the customer has to reverse out of. Turning
around in the middle of an open floor is not a requirement and reporting it as
one would put a card in front of the owner that no section backs.
"""

from __future__ import annotations

from standardphysics_contracts import to_meters
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


@traced("checks.turning_space")
def turning_space(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    stops = ctx.scenario.stops
    return [_at_stop(ctx, rule, index) for index in reversal_stops(stops)]


def _at_stop(ctx: CheckContext, rule: RuleSpec, stop_index: int) -> Observation:
    stops = ctx.scenario.stops
    at = setback_point(stops, stop_index, to_meters(APPROACH_SETBACK_INCHES))
    space = ctx.measure.turning_space(ctx.graph, at)
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
