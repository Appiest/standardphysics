"""ADA 2010 308.2.1 and 305.3 at a self-order kiosk: reach its controls, and room to pull up.

309.3 puts a kiosk's screen, buttons and card slot inside the reach ranges of
308, and 305 wants a 30 by 48 inch patch of clear floor for a forward or a
parallel approach. A scan cannot say which face of a kiosk carries the screen,
so every face is tried and any clear face passes. The face against a wall
fails on its own, because the wall stands in its patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import ClearFloorResult, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import footprint, gap_between, region_locus
from standardphysics_pipeline.footprints import rotation_about_z

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .context import CheckContext
from .observation import Observation
from .reach import within_reach
from .rectangles import clear_floor, intruders, rectangle

REACH_RULE = "kiosk_reach"
FLOOR_RULE = "kiosk_clear_floor"
RULE_IDS = frozenset({REACH_RULE, FLOOR_RULE})

STANDS_ON_METERS = 0.05
"""A kiosk whose bottom is within five centimetres of another piece's top stands on it."""


@dataclass(frozen=True)
class Face:
    outward: tuple[float, float]
    centre: Vec3

    @property
    def along(self) -> tuple[float, float]:
        return (-self.outward[1], self.outward[0])


def faces(node: SceneNode) -> list[Face]:
    """The four sides of the kiosk's footprint, the local minus-Y front first."""
    cos_t, sin_t = rotation_about_z(node)
    centre = node.transform.position
    half_x, half_y = node.dimensions.x / 2, node.dimensions.y / 2
    directions = [((sin_t, -cos_t), half_y), ((-sin_t, cos_t), half_y), ((cos_t, sin_t), half_x),
                  ((-cos_t, -sin_t), half_x)]
    return [
        Face(outward, Vec3(x=centre.x + outward[0] * reach, y=centre.y + outward[1] * reach, z=0.0))
        for outward, reach in directions
    ]


def supports(graph: SceneGraph, node: SceneNode) -> frozenset[UUID]:
    """What the kiosk stands on: a counter under a tablet kiosk is not in its way."""
    bottom = node.transform.position.z - node.dimensions.z / 2
    foot = footprint(node)
    return frozenset(
        other.id for other in graph.nodes
        if other.id == node.parent_id or (
            other.id != node.id
            and abs(other.transform.position.z + other.dimensions.z / 2 - bottom) <= STANDS_ON_METERS
            and gap_between(footprint(other), foot) == 0.0
        )
    )


def _patch(ctx: CheckContext, face: Face, wide: float, deep: float, ignoring: frozenset[UUID]) -> ClearFloorResult:
    reach = to_meters(deep) / 2
    centre = Vec3(x=face.centre.x + face.outward[0] * reach, y=face.centre.y + face.outward[1] * reach, z=0.0)
    return clear_floor(ctx.graph, centre, wide, deep, face.along, ignoring)


def _approaches(rule: RuleSpec) -> tuple[tuple[float, float], tuple[float, float]]:
    """Forward, 30 along the face by 48 out, then parallel, 48 along by 30 out."""
    wide, deep = rule.parameter("clear_width_min_inches"), rule.parameter("clear_depth_min_inches")
    return (wide, deep), (deep, wide)


def clear_approach(
    ctx: CheckContext, rule: RuleSpec, kiosk: SceneNode, ignoring: frozenset[UUID]
) -> tuple[Face, ClearFloorResult]:
    """The first face with a clear approach, or the front face's forward patch when none has one."""
    front = faces(kiosk)[0]
    fallback = (front, _patch(ctx, front, *_approaches(rule)[0], ignoring))
    for face in faces(kiosk):
        for wide, deep in _approaches(rule):
            space = _patch(ctx, face, wide, deep, ignoring)
            if space.fits:
                return face, space
    return fallback


def _floor(ctx: CheckContext, rule: RuleSpec, kiosk: SceneNode) -> Observation:
    ignoring = frozenset({kiosk.id}) | supports(ctx.graph, kiosk)
    face, space = clear_approach(ctx, rule, kiosk, ignoring)
    blockers = [] if space.fits else intruders(ctx.graph, rectangle(
        space.center, to_meters(space.inches_wide), to_meters(space.inches_deep), face.along,
    ), ignoring)
    return Observation(
        rule_id=FLOOR_RULE,
        satisfied=space.fits,
        measured_inches=space.inches_wide,
        required_inches=rule.threshold,
        relied_on=(kiosk.id,),
        locus=region_locus(space, [kiosk.id, *blockers], rotation=face.along),
        facts={"kiosk": kiosk.label, "measured_wide": space.inches_wide, "measured_deep": space.inches_deep,
               "required_wide": rule.parameter("clear_width_min_inches"),
               "required_deep": rule.parameter("clear_depth_min_inches"),
               "blockers": [ctx.label(node_id) for node_id in blockers],
               "movable_blockers": ctx.labels_by_movability(blockers)[0]},
        dedupe_key=(FLOOR_RULE, str(kiosk.id)),
        reason="measured" if space.fits else "blocked",
    )


@traced("checks.kiosks")
def kiosks(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(FLOOR_RULE)
    found = roles.kiosks(ctx.graph)
    return [
        *(within_reach(ctx, REACH_RULE, kiosk) for kiosk in found),
        *(_floor(ctx, rule, kiosk) for kiosk in found),
    ]
