"""ADA 2010 404.2.4. Room to work the door once you get to it.

Table 404.2.4.1 has twelve rows, and which one applies depends on the approach
direction and on which way the door opens. A scan gives the doorway and the
route gives the approach. Neither gives the swing: nothing in a measured room
says whether a door is pulled or pushed from inside.

So this measures the patch of floor on the approach side once and reads the
table from the stricter end. Clear to the pull depth and it complies whichever
way the door opens. Short of the push depth and it fails either way. In between
it depends on the swing, which makes it a question for the owner rather than a
finding, because a check that guessed would be citing a row of the table
nobody established.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from standardphysics_contracts import (
    ClearFloorResult,
    SceneGraph,
    SceneNode,
    Vec3,
    lies_flat,
    to_inches,
    to_meters,
)

from ..rules import RuleSpec
from ..tracing import traced
from . import roles
from .context import CheckContext
from .observation import Observation
from .rectangles import clear_floor
from .route_geometry import travel_heading

RULE_ID = "door_maneuvering_clearance"

Verdict = Literal["complies_either_way", "depends_on_the_swing", "too_tight"]


@dataclass(frozen=True)
class DoorClearance:
    verdict: Verdict
    pull: ClearFloorResult
    push: ClearFloorResult


def door_verdict(
    pull_fits: bool, push_fits: bool
) -> Verdict:
    """Table 404.2.4.1, read from the stricter end.

    The pull side of a front approach wants 60 inches and the push side 48, so
    a patch deep enough to pull is deep enough either way.
    """
    if pull_fits:
        return "complies_either_way"
    if push_fits:
        return "depends_on_the_swing"
    return "too_tight"


def _interior_side(graph: SceneGraph, door: SceneNode) -> tuple[float, float]:
    """Which way the room is, from the doorway.

    A door sits in a wall at the edge of the floor, so the room is whichever
    way the middle of the floor lies.
    """
    floors = [node for node in graph.nodes if lies_flat(node)]
    middle = floors[0].transform.position if floors else Vec3(x=0.0, y=0.0, z=0.0)
    position = door.transform.position
    dx, dy = middle.x - position.x, middle.y - position.y
    return (0.0, 1.0) if abs(dy) >= abs(dx) else (1.0 if dx > 0 else -1.0, 0.0)


def _patch_centre(
    door: SceneNode, inward: tuple[float, float], depth_inches: float
) -> Vec3:
    """The patch starts at the inside face of the doorway, not at its middle.

    A door node spans the thickness of the wall it sits in, so measuring from
    its centre puts half a wall inside the clearance being measured.
    """
    position = door.transform.position
    thickness = min(door.dimensions.x, door.dimensions.y)
    reach = thickness / 2 + to_meters(depth_inches) / 2
    return Vec3(
        x=position.x + inward[0] * reach, y=position.y + inward[1] * reach, z=0.0
    )


def _across(inward: tuple[float, float]) -> tuple[float, float]:
    """The rotation that lays a patch's width along the wall."""
    return (-inward[1], inward[0])


def measure_clearance(
    ctx: CheckContext, rule: RuleSpec, door: SceneNode
) -> DoorClearance:
    opening = to_inches(max(door.dimensions.x, door.dimensions.y))
    width_inches = opening + rule.parameter(
        "front_approach_pull_latch_side_inches"
    )
    inward = _interior_side(ctx.graph, door)
    rotation = _across(inward)
    ignoring = frozenset({door.id})

    depths = (
        rule.parameter("front_approach_pull_depth_inches"),
        rule.parameter("front_approach_push_depth_inches"),
    )
    pull, push = (
        clear_floor(
            ctx.graph,
            _patch_centre(door, inward, depth),
            width_inches,
            depth,
            rotation,
            ignoring,
        )
        for depth in depths
    )
    return DoorClearance(door_verdict(pull.fits, push.fits), pull, push)


def approaches_head_on(ctx: CheckContext, door: SceneNode) -> bool:
    """Whether the route comes at this door from the front.

    Table 404.2.4.1's other rows are for a hinge side or latch side approach,
    and they want numbers this check does not carry yet.
    """
    heading = travel_heading(ctx.scenario.stops, 0)
    if heading is None:
        return True
    inward = _interior_side(ctx.graph, door)
    alignment = abs(heading[0] * inward[0] + heading[1] * inward[1])
    return alignment >= 0.7


@traced("checks.door_maneuvering_clearance")
def door_maneuvering_clearance(ctx: CheckContext) -> list[Observation]:
    rule = ctx.rule(RULE_ID)
    return [
        _observation(ctx, rule, door)
        for door in roles.doors(ctx.graph)
        if approaches_head_on(ctx, door)
    ]


def _observation(ctx: CheckContext, rule: RuleSpec, door: SceneNode) -> Observation:
    found = measure_clearance(ctx, rule, door)
    from standardphysics_pipeline import region_locus

    return Observation(
        rule_id=RULE_ID,
        satisfied=found.verdict == "complies_either_way",
        measured_inches=(
            found.pull.inches_deep
            if found.verdict == "complies_either_way"
            else found.push.inches_deep
        ),
        required_inches=rule.threshold,
        relied_on=(door.id,),
        locus=region_locus(found.pull, [door.id]),
        facts={
            "door": door.label,
            "pull_depth": found.pull.inches_deep,
            "push_depth": found.push.inches_deep,
            "latch_side": rule.parameter("front_approach_pull_latch_side_inches"),
        },
        dedupe_key=(RULE_ID, str(door.id)),
        reason=found.verdict,
        asks_for="swing" if found.verdict == "depends_on_the_swing" else None,
    )
