"""ADA 2010 604.5 and 609.4. The grab bars at a toilet: long enough on both walls, and at the right height.

604.5 wants a bar on the side wall and one on the rear wall. A bar near the
toilet that runs front to back, parallel to the toilet's longer side, is the
side bar; one that runs across is the rear bar. Each is as long as its box's
longest side. A toilet missing either bar in the scan is asked about with a
photo, never failed, because detection misses thin bars; the same holds for
the ramp handrails in `ramps`.

609.4 reads the top of each grab bar's box as the top of its gripping surface.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, SceneNode, to_inches, to_meters
from standardphysics_pipeline.footprints import gap_between_nodes

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .clear_floor import at_least
from .context import CheckContext
from .heights import band_limit, band_reason, top_inches
from .observation import Observation
from .restroom import toilets
from .vertical import mounted_locus
from .water_closet import across, depth_axis, long_axis, parallel

WALLS_RULE = "water_closet_grab_bars"
HEIGHT_RULE = "grab_bar_height"
RULE_IDS = frozenset({WALLS_RULE, HEIGHT_RULE})

NEAR_TOILET_INCHES = 20.0
"""A side bar runs about 1 1/2 inches off a wall 16 to 18 inches from the centreline, so it lies well inside this."""


def length_inches(bar: SceneNode) -> float:
    return to_inches(max(bar.dimensions.x, bar.dimensions.y))


def bars_near(graph: SceneGraph, toilet: SceneNode) -> list[SceneNode]:
    reach = to_meters(NEAR_TOILET_INCHES)
    return [bar for bar in roles.grab_bars(graph) if gap_between_nodes(bar, toilet) <= reach]


def _longest_along(bars: list[SceneNode], direction) -> SceneNode | None:
    return max((bar for bar in bars if parallel(long_axis(bar), direction)), key=length_inches, default=None)


def side_and_rear(graph: SceneGraph, toilet: SceneNode) -> tuple[SceneNode, SceneNode] | None:
    """The side wall bar and the rear wall bar at this toilet, when the scan shows both."""
    depth = depth_axis(toilet)
    if depth is None:
        return None
    bars = bars_near(graph, toilet)
    side, rear = _longest_along(bars, depth), _longest_along(bars, across(depth))
    if side is None or rear is None:
        return None
    return side, rear


@traced("checks.grab_bars")
def grab_bars(ctx: CheckContext) -> list[Observation]:
    walls_rule, height_rule = ctx.rule(WALLS_RULE), ctx.rule(HEIGHT_RULE)
    return [
        *(_at_toilet(ctx, walls_rule, toilet) for toilet in toilets(ctx.graph)),
        *(_height(height_rule, bar) for bar in roles.grab_bars(ctx.graph)),
    ]


def _at_toilet(ctx: CheckContext, rule: RuleSpec, toilet: SceneNode) -> Observation:
    found = side_and_rear(ctx.graph, toilet)
    if found is None:
        return _asks_for_a_photo(toilet)
    side, rear = found
    side_length, rear_length = length_inches(side), length_inches(rear)
    rear_min = rule.parameter("rear_wall_min_inches")
    reason = _length_reason(side_length, rule.threshold, rear_length, rear_min)
    rear_decides = reason == "rear_too_short"
    return Observation(
        rule_id=WALLS_RULE,
        satisfied=reason == "measured",
        measured_inches=rear_length if rear_decides else side_length,
        required_inches=rear_min if rear_decides else rule.threshold,
        relied_on=(toilet.id, side.id, rear.id),
        locus=mounted_locus(toilet),
        facts={"side": side_length, "rear": rear_length, "side_min": rule.threshold, "rear_min": rear_min},
        dedupe_key=(WALLS_RULE, str(toilet.id)),
        reason=reason,
        seen_directly=True,
    )


def _length_reason(side: float, side_min: float, rear: float, rear_min: float) -> str:
    if not at_least(side, side_min):
        return "side_too_short"
    if not at_least(rear, rear_min):
        return "rear_too_short"
    return "measured"


def _asks_for_a_photo(toilet: SceneNode) -> Observation:
    return Observation(
        rule_id=WALLS_RULE,
        satisfied=False,
        relied_on=(toilet.id,),
        locus=mounted_locus(toilet),
        facts={"subject": toilet.label, "evidence": "photo"},
        dedupe_key=(WALLS_RULE, str(toilet.id)),
        reason="needs_photo",
    )


def _height(rule: RuleSpec, bar: SceneNode) -> Observation:
    top = top_inches(bar)
    low = rule.parameter("gripping_surface_min_inches")
    reason = band_reason(top, low, rule.threshold)
    return Observation(
        rule_id=HEIGHT_RULE,
        satisfied=reason == "measured",
        measured_inches=top,
        required_inches=band_limit(reason, low, rule.threshold),
        relied_on=(bar.id,),
        locus=mounted_locus(bar),
        facts={"low": low, "high": rule.threshold},
        dedupe_key=(HEIGHT_RULE, str(bar.id)),
        reason=reason,
        seen_directly=True,
    )
