"""Where something is, in the shop's own terms.

Coordinates are no use to anybody standing in the room. What helps is which
wall it is against, whether it is left or right as you come in, and what is
next to it.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode, Vec3, bounds_the_room, stands_upright, to_inches
from standardphysics_pipeline import gap_between_nodes

from ..numbers import span, things
from ..tracing import traced
from . import subjects
from .answer import Answer, AskContext
from .directions import shop_axes
from .locus import subject_locus
from .query import Query

AGAINST_A_WALL_INCHES = 12.0
"""Within a foot of a wall is against it, as far as a description goes."""

MIDDLE_BAND = 0.25
"""How much of the room counts as the middle, either side of centre."""


def _along(position: Vec3, axis: tuple[float, float]) -> float:
    return position.x * axis[0] + position.y * axis[1]


def _fraction(node: SceneNode, axis: tuple[float, float], nodes: list[SceneNode]) -> float:
    """Where this sits between the two extremes of the room, 0 to 1."""
    values = [_along(other.transform.position, axis) for other in nodes]
    low, high = min(values), max(values)
    if high - low < 1e-6:
        return 0.5
    return (_along(node.transform.position, axis) - low) / (high - low)


def _depth_word(fraction: float) -> str:
    if fraction > 0.5 + MIDDLE_BAND:
        return "at the back"
    if fraction < 0.5 - MIDDLE_BAND:
        return "near the door"
    return "half way in"


def _side_word(fraction: float) -> str:
    if fraction > 0.5 + MIDDLE_BAND:
        return "on the right"
    if fraction < 0.5 - MIDDLE_BAND:
        return "on the left"
    return "in the middle"


def _nearest_wall(node: SceneNode, walls: list[SceneNode]) -> tuple[SceneNode, float] | None:
    if not walls:
        return None
    nearest = min(walls, key=lambda wall: gap_between_nodes(node, wall))
    return nearest, to_inches(gap_between_nodes(node, nearest))


def _neighbour(node: SceneNode, others: list[SceneNode]) -> tuple[SceneNode, float] | None:
    candidates = [
        other for other in others if other.id != node.id and not bounds_the_room(other)
    ]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda other: gap_between_nodes(node, other))
    return nearest, to_inches(gap_between_nodes(node, nearest))


WALL_NAMES = {
    "at the back": "the back wall",
    "near the door": "the front wall",
    "on the left": "the left wall",
    "on the right": "the right wall",
}


def _wall_name(depth: str, side: str, depth_fraction: float, side_fraction: float) -> str:
    """Which wall it is against: whichever axis it sits furthest along."""
    from_middle = (abs(depth_fraction - 0.5), abs(side_fraction - 0.5))
    word = depth if from_middle[0] >= from_middle[1] else side
    return WALL_NAMES.get(word, "a wall")


def describe_position(node: SceneNode, context: AskContext) -> str:
    back, right = shop_axes(context.scenario, context.graph)
    walls = [n for n in context.graph.nodes if stands_upright(n)]
    reference = walls or context.graph.nodes
    depth_fraction = _fraction(node, back, reference)
    side_fraction = _fraction(node, right, reference)
    depth = _depth_word(depth_fraction)
    side = _side_word(side_fraction)

    against = _nearest_wall(node, walls)
    if against and against[1] <= AGAINST_A_WALL_INCHES:
        wall = _wall_name(depth, side, depth_fraction, side_fraction)
        along = side if wall in ("the back wall", "the front wall") else depth
        return f"against {wall}, {along}"
    return f"out on the floor, {depth} and {side}"


@traced("ask.where")
def where(query: Query, context: AskContext) -> Answer:
    found = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    if not found:
        return _not_here(query)

    node = found[0]
    named = things([node.label]) or "It"
    subject = named[:1].upper() + named[1:]
    sentences = [f"{subject} is {describe_position(node, context)}."]

    neighbour = _neighbour(node, context.graph.nodes)
    if neighbour:
        other, gap = neighbour
        sentences.append(
            f"The nearest thing to it is the {other.label.casefold()}, "
            f"{span(gap)} away."
        )
    text = " ".join(sentences)
    return Answer(
        text=text,
        kind="WHERE",
        query=query,
        subjects=tuple(n.id for n in found),
        locus=subject_locus(found, node.label),
        data={
            "position": describe_position(node, context),
            "nearest": str(neighbour[0].id) if neighbour else None,
            "nearest_inches": round(neighbour[1], 2) if neighbour else None,
        },
    )


def _not_here(query: Query) -> Answer:
    asked = query.subject_labels[0].casefold() if query.subject_labels else "that"
    return Answer(
        text=f"The scan has no {asked} in it. Tell us what it looks like and "
        "we will find it.",
        kind="WHERE",
        query=query,
        data={"asked_about": asked},
    )
