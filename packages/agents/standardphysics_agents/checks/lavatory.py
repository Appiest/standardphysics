"""ADA 2010 606.3 and 603.3. How high a restroom sink sits, and the mirror over it.

606.3 caps the front of the higher of the rim or the counter at 34 inches. A
measured top surface is that height when the scan has one; otherwise the top of
the sink's box is, since the box holds both the bowl and the counter around it.

603.3 caps the bottom of a mirror's reflecting surface at 40 inches when it
hangs over a sink. Only a mirror whose outline comes within
`OVER_LAVATORY_INCHES` of a sink's, with its bottom at or above the rim, counts
as hanging over it. A mirror anywhere else is left alone, because a scan cannot
say whether a mirror on a shop wall is inside a restroom.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, SceneNode, to_inches, to_meters
from standardphysics_pipeline.footprints import gap_between_nodes

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .context import CheckContext
from .heights import band_reason, bottom_inches, top_inches
from .observation import Observation
from .vertical import mounted_locus

HEIGHT_RULE = "lavatory_height"
MIRROR_RULE = "mirror_height"
RULE_IDS = frozenset({HEIGHT_RULE, MIRROR_RULE})

OVER_LAVATORY_INCHES = 12.0
RIM_SLACK_INCHES = 1.0
"""A mirror whose bottom sits more than an inch below the rim runs down past the sink rather than hanging over it."""


def rim_inches(lavatory: SceneNode) -> float:
    surface = lavatory.top_surface
    if surface is not None and surface.height_m is not None:
        return to_inches(surface.height_m)
    return top_inches(lavatory)


def _hangs_over(mirror: SceneNode, lavatory: SceneNode) -> bool:
    return (gap_between_nodes(mirror, lavatory) <= to_meters(OVER_LAVATORY_INCHES)
            and bottom_inches(mirror) >= rim_inches(lavatory) - RIM_SLACK_INCHES)


def mirrors_over_lavatories(graph: SceneGraph) -> list[SceneNode]:
    sinks = roles.lavatories(graph)
    return [mirror for mirror in roles.mirrors(graph) if any(_hangs_over(mirror, sink) for sink in sinks)]


@traced("checks.lavatory")
def lavatory(ctx: CheckContext) -> list[Observation]:
    height_rule, mirror_rule = ctx.rule(HEIGHT_RULE), ctx.rule(MIRROR_RULE)
    return [
        *(_rim(height_rule, sink) for sink in roles.lavatories(ctx.graph)),
        *(_mirror(mirror_rule, mirror) for mirror in mirrors_over_lavatories(ctx.graph)),
    ]


def _highest_allowed(rule: RuleSpec, rule_id: str, node: SceneNode, measured: float) -> Observation:
    reason = band_reason(measured, None, rule.threshold)
    return Observation(
        rule_id=rule_id,
        satisfied=reason == "measured",
        measured_inches=measured,
        required_inches=rule.threshold,
        relied_on=(node.id,),
        locus=mounted_locus(node),
        facts={"subject": node.label, "high": rule.threshold},
        dedupe_key=(rule_id, str(node.id)),
        reason=reason,
        seen_directly=True,
    )


def _rim(rule: RuleSpec, sink: SceneNode) -> Observation:
    return _highest_allowed(rule, HEIGHT_RULE, sink, rim_inches(sink))


def _mirror(rule: RuleSpec, mirror: SceneNode) -> Observation:
    return _highest_allowed(rule, MIRROR_RULE, mirror, bottom_inches(mirror))
