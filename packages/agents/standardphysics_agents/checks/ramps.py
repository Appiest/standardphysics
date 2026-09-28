"""ADA 2010 405 and 505.4. A ramp's slope, rise, width, landings and handrails.

A ramp arrives as a box. Its vertical extent is the rise, its longest
horizontal extent the run, and the other horizontal extent the width. A box
cannot say which end is the top, so both landings are measured the same way,
and the slope is the run over the rise, which 405.2 wants to be at least 12.

Handrails are thin, and a detector that misses one says nothing about whether
it is there. A ramp that needs them passes when a railing stands along each
side and otherwise asks for a photo; it never fails for want of a detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from standardphysics_contracts import ClearFloorResult, SceneNode, Vec3, to_inches, to_meters
from standardphysics_pipeline import footprint, gap_between, region_locus
from standardphysics_pipeline.footprints import contains_point, floor_polygon, rotation_about_z

from ..tracing import traced
from . import roles
from .clear_floor import at_least
from .context import CheckContext
from .observation import Observation
from .rectangles import clear_floor, intruders, rectangle
from .vertical import mounted_locus

SLOPE_RULE = "ramp_running_slope"
RISE_RULE = "ramp_rise"
WIDTH_RULE = "ramp_clear_width"
LANDING_RULE = "ramp_landing_length"
HANDRAILS_RULE = "ramp_handrails"
HANDRAIL_HEIGHT_RULE = "handrail_height"

RULE_IDS = frozenset({SLOPE_RULE, RISE_RULE, WIDTH_RULE, LANDING_RULE, HANDRAILS_RULE, HANDRAIL_HEIGHT_RULE})

BESIDE_METERS = 0.3
"""A railing within a foot of the ramp's edge is that ramp's railing."""

MEETS_METERS = 0.05
"""A landing that comes within five centimetres of the ramp's end meets it."""

LandingState = Literal["long_enough", "too_short", "blocked", "unseen"]


@dataclass(frozen=True)
class RampRun:
    node: SceneNode
    rise: float
    run: float
    width: float
    axis: tuple[float, float]
    """A unit vector along the run, in the floor plane."""

    @property
    def centre(self) -> Vec3:
        return self.node.transform.position

    @property
    def across(self) -> tuple[float, float]:
        return (-self.axis[1], self.axis[0])

    @property
    def top_meters(self) -> float:
        return self.centre.z + self.node.dimensions.z / 2


def ramp_run(node: SceneNode) -> RampRun:
    cos_t, sin_t = rotation_about_z(node)
    dims = node.dimensions
    along_x = dims.x >= dims.y
    axis = (cos_t, sin_t) if along_x else (-sin_t, cos_t)
    run, width = (dims.x, dims.y) if along_x else (dims.y, dims.x)
    return RampRun(node=node, rise=to_inches(dims.z), run=to_inches(run), width=to_inches(width), axis=axis)


def run_per_rise(ramp: RampRun) -> float:
    return ramp.run / ramp.rise if ramp.rise > 0 else float("inf")


def _offset(ramp: RampRun, point: Vec3, direction: tuple[float, float]) -> float:
    return (point.x - ramp.centre.x) * direction[0] + (point.y - ramp.centre.y) * direction[1]


def railings_beside(ctx: CheckContext, ramp: RampRun) -> list[SceneNode]:
    ramp_foot = footprint(ramp.node)
    return [rail for rail in roles.handrails(ctx.graph) if gap_between(footprint(rail), ramp_foot) <= BESIDE_METERS]


def railed_sides(ramp: RampRun, railings: list[SceneNode]) -> int:
    sides = {_offset(ramp, rail.transform.position, ramp.across) > 0 for rail in railings}
    return len(sides)


def _slope(ctx: CheckContext, ramp: RampRun) -> Observation:
    rule = ctx.rule(SLOPE_RULE)
    ratio = run_per_rise(ramp)
    return Observation(
        rule_id=SLOPE_RULE,
        satisfied=rule.satisfied_by(ratio),
        measured_inches=ramp.run,
        required_inches=ramp.rise * rule.threshold,
        relied_on=(ramp.node.id,),
        locus=mounted_locus(ramp.node),
        facts={"ramp": ramp.node.label, "rise": ramp.rise, "run": ramp.run, "run_per_rise": ratio,
               "required_run_per_rise": rule.threshold},
        dedupe_key=(SLOPE_RULE, str(ramp.node.id)),
        reason="measured" if rule.satisfied_by(ratio) else "too_steep",
    )


def _dimension(ctx: CheckContext, ramp: RampRun, rule_id: str, value: float) -> Observation:
    rule = ctx.rule(rule_id)
    return Observation(
        rule_id=rule_id,
        satisfied=rule.satisfied_by(value),
        measured_inches=value,
        required_inches=rule.threshold,
        relied_on=(ramp.node.id,),
        locus=mounted_locus(ramp.node),
        facts={"ramp": ramp.node.label, "rise": ramp.rise, "run": ramp.run, "width": ramp.width},
        dedupe_key=(rule_id, str(ramp.node.id)),
        reason="measured",
    )


def _handrails(ctx: CheckContext, ramp: RampRun, railings: list[SceneNode]) -> Observation:
    rule = ctx.rule(HANDRAILS_RULE)
    needed = not rule.satisfied_by(ramp.rise)
    both_sides = railed_sides(ramp, railings) == 2
    return Observation(
        rule_id=HANDRAILS_RULE,
        satisfied=not needed or both_sides,
        measured_inches=ramp.rise,
        required_inches=rule.threshold,
        relied_on=(ramp.node.id, *(rail.id for rail in railings)),
        locus=mounted_locus(ramp.node),
        facts={"ramp": ramp.node.label, "rise": ramp.rise, "applies": needed,
               "railings": [rail.label for rail in railings]},
        dedupe_key=(HANDRAILS_RULE, str(ramp.node.id)),
        reason=_handrail_reason(needed, both_sides),
        asks_for="photo" if needed and not both_sides else None,
    )


def _handrail_reason(needed: bool, both_sides: bool) -> str:
    if not needed:
        return "low_rise"
    return "railed_both_sides" if both_sides else "railings_unseen"


def _handrail_height(ctx: CheckContext, ramp: RampRun, rail: SceneNode) -> Observation:
    rule = ctx.rule(HANDRAIL_HEIGHT_RULE)
    low = rule.parameter("gripping_surface_min_inches")
    height = to_inches(rail.transform.position.z + rail.dimensions.z / 2 - ramp.top_meters)
    too_low = not at_least(height, low)
    return Observation(
        rule_id=HANDRAIL_HEIGHT_RULE,
        satisfied=rule.satisfied_by(height) and not too_low,
        measured_inches=height,
        required_inches=low if too_low else rule.threshold,
        relied_on=(rail.id, ramp.node.id),
        locus=mounted_locus(rail),
        facts={"railing": rail.label, "ramp": ramp.node.label, "low": low, "high": rule.threshold},
        dedupe_key=(HANDRAIL_HEIGHT_RULE, str(ramp.node.id)),
        reason="too_low" if too_low else "measured",
    )


@dataclass(frozen=True)
class LandingEnd:
    state: LandingState
    length: float
    space: ClearFloorResult | None = None


def _landing_meeting(ctx: CheckContext, ramp: RampRun, sign: int) -> SceneNode | None:
    ramp_foot = footprint(ramp.node)
    half_run = to_meters(ramp.run) / 2
    for landing in roles.ramp_landings(ctx.graph):
        beyond = _offset(ramp, landing.transform.position, ramp.axis) * sign > half_run - MEETS_METERS
        if beyond and gap_between(footprint(landing), ramp_foot) <= MEETS_METERS:
            return landing
    return None


def _length_along(ramp: RampRun, node: SceneNode) -> float:
    reach = [x * ramp.axis[0] + y * ramp.axis[1] for x, y in footprint(node)]
    return to_inches(max(reach) - min(reach))


def _covered_by_the_scan(ctx: CheckContext, centre: Vec3, ramp: RampRun, length_inches: float) -> bool:
    floors = roles.floors(ctx.graph)
    if not floors:
        return True
    floor = floor_polygon(floors[0])
    patch = rectangle(centre, to_meters(ramp.width), to_meters(length_inches), ramp.across)
    return all(contains_point(floor, corner) for corner in patch)


def _landing_end(ctx: CheckContext, ramp: RampRun, sign: int, ignoring: frozenset[UUID]) -> LandingEnd:
    required = ctx.rule(LANDING_RULE).threshold
    named = _landing_meeting(ctx, ramp, sign)
    if named is not None:
        length = _length_along(ramp, named)
        return LandingEnd("long_enough" if at_least(length, required) else "too_short", length)
    reach = to_meters(ramp.run) / 2 + to_meters(required) / 2
    centre = Vec3(x=ramp.centre.x + ramp.axis[0] * reach * sign, y=ramp.centre.y + ramp.axis[1] * reach * sign, z=0.0)
    if not _covered_by_the_scan(ctx, centre, ramp, required):
        return LandingEnd("unseen", required)
    space = clear_floor(ctx.graph, centre, ramp.width, required, ramp.across, ignoring)
    return LandingEnd("long_enough" if space.fits else "blocked", required, space)


LANDING_ORDER: dict[LandingState, int] = {"too_short": 0, "blocked": 0, "unseen": 1, "long_enough": 2}


def _landings(ctx: CheckContext, ramp: RampRun, railings: list[SceneNode]) -> Observation:
    rule = ctx.rule(LANDING_RULE)
    ignoring = frozenset({ramp.node.id, *(rail.id for rail in railings),
                          *(node.id for node in roles.ramp_landings(ctx.graph))})
    ends = [_landing_end(ctx, ramp, sign, ignoring) for sign in (1, -1)]
    worst = min(ends, key=lambda end: LANDING_ORDER[end.state])
    blockers = _blockers(ctx, ramp, worst, ignoring)
    return Observation(
        rule_id=LANDING_RULE,
        satisfied=worst.state == "long_enough",
        measured_inches=worst.length,
        required_inches=rule.threshold,
        relied_on=(ramp.node.id,),
        locus=region_locus(worst.space, [ramp.node.id, *blockers], rotation=ramp.across)
        if worst.space is not None else mounted_locus(ramp.node),
        facts={"ramp": ramp.node.label, "blockers": [ctx.label(node_id) for node_id in blockers],
               "movable_blockers": ctx.labels_by_movability(blockers)[0]},
        dedupe_key=(LANDING_RULE, str(ramp.node.id)),
        reason=worst.state,
        asks_for="photo" if worst.state == "unseen" else None,
    )


def _blockers(ctx: CheckContext, ramp: RampRun, end: LandingEnd, ignoring: frozenset[UUID]) -> list[UUID]:
    if end.space is None or end.space.fits:
        return []
    patch = rectangle(end.space.center, to_meters(end.space.inches_wide), to_meters(end.space.inches_deep),
                      ramp.across)
    return intruders(ctx.graph, patch, ignoring)


def _ramp_observations(ctx: CheckContext, node: SceneNode) -> list[Observation]:
    ramp = ramp_run(node)
    railings = railings_beside(ctx, ramp)
    return [
        _slope(ctx, ramp),
        _dimension(ctx, ramp, RISE_RULE, ramp.rise),
        _dimension(ctx, ramp, WIDTH_RULE, ramp.width),
        _landings(ctx, ramp, railings),
        _handrails(ctx, ramp, railings),
        *_worst_railing(ctx, ramp, railings),
    ]


def _worst_railing(ctx: CheckContext, ramp: RampRun, railings: list[SceneNode]) -> list[Observation]:
    """One card per ramp: the railing furthest outside 34 to 38 inches, or any one when all are in range."""
    heights = [_handrail_height(ctx, ramp, rail) for rail in railings]
    return [min(heights, key=_railing_severity)] if heights else []


def _railing_severity(observation: Observation) -> tuple[bool, float]:
    return observation.satisfied, -abs((observation.measured_inches or 0.0) - (observation.required_inches or 0.0))


@traced("checks.ramps")
def ramps(ctx: CheckContext) -> list[Observation]:
    return [observation for node in roles.ramps(ctx.graph) for observation in _ramp_observations(ctx, node)]


