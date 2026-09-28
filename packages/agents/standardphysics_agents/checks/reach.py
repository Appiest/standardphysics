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


@traced("checks.reach_range")
def reach_range(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    if not rule.measurable:
        return [ask(ctx, RULE_ID, no_nodes)]
    return [_reach(rule, node) for node in roles.operable_parts(ctx.graph)]
