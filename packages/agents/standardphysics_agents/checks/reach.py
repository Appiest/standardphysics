"""ADA 2010 308. Whether a customer can reach what they have to work by hand.

A scan sees a thermostat or a soap dispenser as a box on a wall, with a top
and a bottom but no idea where on it the button is. Production therefore asks
for a photo. Training, which takes scanned geometry as measured, reads the
top of each operable part against the 48 inch high forward reach and its
bottom against the 15 inch low reach: the top is the highest a hand might
have to go, so a part whose top is in range is in range.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode, to_inches

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .clear_floor import at_least, at_most
from .context import CheckContext
from .observation import Observation
from .questions import ask, no_nodes
from .vertical import mounted_locus

RULE_ID = "reach_range"


def _top_inches(node: SceneNode) -> float:
    return to_inches(node.transform.position.z + node.dimensions.z / 2)


def _bottom_inches(node: SceneNode) -> float:
    return to_inches(node.transform.position.z - node.dimensions.z / 2)


def _reach(rule: RuleSpec, node: SceneNode) -> Observation:
    top, bottom = _top_inches(node), _bottom_inches(node)
    low = rule.parameter("unobstructed_low_inches")
    too_low = bottom < low
    return Observation(
        rule_id=RULE_ID,
        satisfied=rule.satisfied_by(top) and not too_low,
        measured_inches=bottom if too_low else top,
        required_inches=low if too_low else rule.threshold,
        relied_on=(node.id,),
        locus=mounted_locus(node),
        facts={"subject": node.label, "top": top, "bottom": bottom, "high": rule.threshold, "low": low},
        dedupe_key=(RULE_ID, str(node.id)),
        reason="too_low" if too_low else "measured",
    )


def within_reach(ctx: CheckContext, rule_id: str, node: SceneNode) -> Observation:
    """A box's top and bottom against the 308 ranges, failing only what the box itself settles.

    Everything on a box whose top is between the low and high reach is in
    range. A box wholly above the high reach or below the low one is out of
    it. A box that runs past the high reach may still carry its controls lower,
    as a floor-standing kiosk carries its screen, so that one asks for the
    height of the highest control rather than guessing.
    """
    rule = ctx.rule(rule_id)
    top, bottom = _top_inches(node), _bottom_inches(node)
    high, low = rule.threshold, rule.parameter("unobstructed_low_inches")
    reason = _reach_reason(top, bottom, high, low)
    return Observation(
        rule_id=rule_id,
        satisfied=reason == "measured",
        measured_inches=_reach_measurement(reason, top, bottom),
        required_inches=low if reason == "too_low" else high,
        relied_on=(node.id,),
        locus=mounted_locus(node),
        facts={"subject": node.label, "top": top, "bottom": bottom, "high": high, "low": low},
        dedupe_key=(rule_id, str(node.id)),
        reason=reason,
        asks_for="measurement" if reason == "controls_unplaced" else None,
    )


def _reach_reason(top: float, bottom: float, high: float, low: float) -> str:
    if not at_most(bottom, high):
        return "too_high"
    if not at_least(top, low):
        return "too_low"
    if at_most(top, high):
        return "measured"
    return "controls_unplaced"


def _reach_measurement(reason: str, top: float, bottom: float) -> float:
    return {"too_high": bottom, "too_low": top}.get(reason, top)


@traced("checks.reach_range")
def reach_range(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    if not rule.measurable:
        return [ask(ctx, RULE_ID, no_nodes)]
    return [_reach(rule, node) for node in roles.operable_parts(ctx.graph)]
