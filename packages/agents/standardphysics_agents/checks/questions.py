"""The things LiDAR cannot see, turned into one specific ask each.

A scan measures shapes. It does not measure how hard a door is to push, what a
handle feels like in a closed fist, whether the mat by the door is stuck down,
or which of the boxes on a wall is a light switch. Each of those becomes a
single request for one piece of evidence, written as the thing to do rather
than as the thing we are missing.
"""

from __future__ import annotations

from typing import Callable

from standardphysics_contracts import SceneGraph, SceneNode

from ..tracing import traced
from . import roles
from .context import CheckContext
from .lavatory import mirrors_over_lavatories
from .observation import Observation
from .restroom import toilets

NodeFinder = Callable[[SceneGraph], list[SceneNode]]


def _entrance_nodes(graph: SceneGraph) -> list[SceneNode]:
    door = roles.entrance(graph)
    return [door] if door else []


def _inside_door_nodes(graph: SceneGraph) -> list[SceneNode]:
    """Doors other than the one customers come in through.

    404.2.9 limits the push needed to open interior hinged doors and sliding
    or folding doors. It sets no limit for exterior hinged doors, because the
    force that keeps one shut against wind usually exceeds 5 pounds, so the
    front door is the one door this question must not name. When the scan
    cannot say which door is the front one, it names none of them.
    """
    entrance = roles.entrance(graph)
    if entrance is None:
        return []
    return [door for door in roles.doors(graph) if door.id != entrance.id]


def _floor_nodes(graph: SceneGraph) -> list[SceneNode]:
    return roles.floors(graph)[:1]


def no_nodes(graph: SceneGraph) -> list[SceneNode]:
    return []


ASK_ABOUT: tuple[tuple[str, NodeFinder], ...] = (
    ("entrance_threshold", _entrance_nodes),
    ("door_hardware", _entrance_nodes),
    ("door_opening_force", _inside_door_nodes),
    ("floor_surface", _floor_nodes),
    ("restroom_turning_space", no_nodes),
    ("water_closet_location", no_nodes),
    ("water_closet_seat_height", toilets),
    ("water_closet_grab_bars", no_nodes),
    ("grab_bar_height", no_nodes),
    ("lavatory_height", no_nodes),
    ("lavatory_knee_clearance", roles.lavatories),
    ("mirror_height", no_nodes),
    ("sign_tactile_height", _inside_door_nodes),
    ("sign_location", _inside_door_nodes),
)

RULE_IDS = frozenset(rule_id for rule_id, _ in ASK_ABOUT)

MEASURED_WHEN_SEEN: dict[str, NodeFinder] = {
    "restroom_turning_space": toilets,
    "water_closet_location": toilets,
    "water_closet_grab_bars": toilets,
    "grab_bar_height": roles.grab_bars,
    "lavatory_height": roles.lavatories,
    "mirror_height": mirrors_over_lavatories,
}
"""Asked about only while the scan does not show the thing; its own check measures it once the scan does.

`restroom`, `water_closet`, `grab_bars` and `lavatory` each take over as soon
as the graph holds what they measure, so an owner is never asked for a photo of
something the scan already answered.
"""


def _seen(ctx: CheckContext, rule_id: str) -> bool:
    finder = MEASURED_WHEN_SEEN.get(rule_id)
    return finder is not None and bool(finder(ctx.graph))


@traced("checks.scan_cannot_see")
def scan_cannot_see(ctx: CheckContext) -> list[Observation]:
    return [ask(ctx, rule_id, finder) for rule_id, finder in ASK_ABOUT if not _seen(ctx, rule_id)]


def ask(ctx: CheckContext, rule_id: str, finder: NodeFinder) -> Observation:
    rule = ctx.rule(rule_id)
    nodes = finder(ctx.graph)
    return Observation(
        rule_id=rule_id,
        satisfied=False,
        measured_inches=None,
        required_inches=rule.threshold if rule.unit == "in" else None,
        relied_on=tuple(node.id for node in nodes),
        locus=None,
        facts={
            "subject": nodes[0].label if nodes else None,
            "evidence": rule.evidence,
        },
        dedupe_key=(rule_id,),
        reason=f"needs_{rule.evidence}",
    )
