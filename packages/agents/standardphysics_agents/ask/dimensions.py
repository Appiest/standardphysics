"""How tall, how wide, how deep.

Height is measured from the floor to the top, because that is what somebody
means by how tall their counter is. Length is the long side of the footprint
and depth is the short one, because that is how furniture is sold.
"""

from __future__ import annotations

from standardphysics_contracts import SceneNode, lies_flat, to_inches

from ..numbers import by, inches, plural, span, things
from ..tracing import traced
from . import subjects
from .answer import Answer, AskContext
from .locus import subject_locus
from .query import Dimension, Query, register_dimensions

SAME_WITHIN_INCHES = 0.5
"""Two pieces this close in size are the same size, as far as anybody cares."""


def height_inches(node: SceneNode) -> float:
    """Floor to top. A raised shelf is as tall as its top edge."""
    return to_inches(node.transform.position.z + node.dimensions.z / 2)


def length_inches(node: SceneNode) -> float:
    return to_inches(max(node.dimensions.x, node.dimensions.y))


def depth_inches(node: SceneNode) -> float:
    return to_inches(min(node.dimensions.x, node.dimensions.y))


READERS = {
    "height": height_inches,
    "length": length_inches,
    "width": length_inches,
    "depth": depth_inches,
}

AREAS = frozenset({"footprint"})
"""Measurements answered as a width by a depth rather than as one figure."""

register_dimensions({*READERS, *AREAS})

WORDS: dict[str, str] = {
    "height": "tall",
    "length": "long",
    "width": "wide",
    "depth": "deep",
}


def _same(values: list[float]) -> bool:
    return max(values) - min(values) <= SAME_WITHIN_INCHES


def _footprint_text(nodes: list[SceneNode], subject: str) -> str:
    sizes = {
        by(length_inches(node), depth_inches(node)) for node in nodes
    }
    if len(sizes) == 1:
        return f"{subject} {_is_are(nodes)} {sizes.pop()}."
    listed = ", ".join(sorted(sizes))
    return f"{subject} come in {len(sizes)} sizes: {listed}."


def _is_are(nodes: list[SceneNode]) -> str:
    return "is" if len(nodes) == 1 else "are"


def _measure_text(
    nodes: list[SceneNode], dimension: Dimension, subject: str
) -> str:
    values = [READERS[dimension](node) for node in nodes]
    word = WORDS[dimension]
    if _same(values):
        return f"{subject} {_is_are(nodes)} {inches(values[0])} {word}."
    return (
        f"{subject} run from {inches(min(values))} to {inches(max(values))} {word}."
    )


@traced("ask.measure")
def measure(query: Query, context: AskContext) -> Answer:
    if subjects.is_about_the_room(query.subject_labels):
        return _room(query, context)

    found = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    if not found:
        return _not_here(query)

    dimension = query.dimension or "height"
    subject = _subject_phrase(found)
    text = (
        _footprint_text(found, subject)
        if dimension in AREAS
        else _measure_text(found, dimension, subject)
    )
    return Answer(
        text=text,
        kind="MEASURE",
        query=query,
        subjects=tuple(node.id for node in found),
        locus=subject_locus(found, text.rstrip(".")),
        data={
            "dimension": dimension,
            "inches": {
                str(node.id): {
                    "height": round(height_inches(node), 2),
                    "length": round(length_inches(node), 2),
                    "depth": round(depth_inches(node), 2),
                }
                for node in found
            },
        },
    )


def _subject_phrase(nodes: list[SceneNode]) -> str:
    named = things([node.label for node in nodes])
    if named is None:
        return "It"
    return named[:1].upper() + named[1:]


def _room(query: Query, context: AskContext) -> Answer:
    floors = [node for node in context.graph.nodes if lies_flat(node)]
    if not floors:
        return _not_here(query)
    floor = floors[0]
    long_side, short_side = length_inches(floor), depth_inches(floor)
    size = f"{span(long_side)} by {span(short_side)}"
    return Answer(
        text=f"The room is {size} of floor.",
        kind="MEASURE",
        query=query,
        subjects=(floor.id,),
        locus=subject_locus([floor], by(long_side, short_side)),
        data={
            "length_inches": round(long_side, 2),
            "depth_inches": round(short_side, 2),
        },
    )


def _not_here(query: Query) -> Answer:
    asked = query.subject_labels[0].casefold() if query.subject_labels else "that"
    return Answer(
        text=f"The scan has no {plural(asked)} in it. Point at the piece you "
        "mean in the plan and we will measure it.",
        kind="MEASURE",
        query=query,
        data={"asked_about": asked},
    )
