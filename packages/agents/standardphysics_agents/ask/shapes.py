"""The shape a set of furniture makes on the floor.

"How would you describe my table arrangement" is not a measurement question and
it is not a compliance question. It is somebody asking what their room looks
like from above, which is a thing the model of the room knows and they cannot
see while standing in it.

The classification is geometric and has no model in it. Centres are projected
onto the shop's own axes, grouped into rows and columns at a tolerance set by
how big the pieces are, and the pattern falls out of the counts. A grid of six
is three groups one way and two the other, and six is three times two.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

from standardphysics_contracts import SceneNode, bounds_the_room, stands_upright, to_inches

from ..numbers import COUNT_WORDS, plural, span
from ..tracing import traced
from . import subjects
from .answer import Answer, AskContext
from .directions import shop_axes
from .locus import subject_locus
from .query import Query

Shape = Literal["one", "pair", "row", "two_rows", "grid", "ring", "cluster", "spread"]

GROUP_TOLERANCE = 1.5
"""How many piece-widths apart two centres have to be to be different rows.

Set from the furniture rather than fixed, because two cafe tables 30 inches
apart are a row and two counters 30 inches apart are one block.
"""

AGAINST_A_WALL_INCHES = 24.0
"""Within two feet of a wall counts as around the edge of the room."""

CLUSTER_FRACTION = 0.35
"""Furniture inside this much of the room's span is grouped rather than spread."""


@dataclass(frozen=True)
class Arrangement:
    shape: Shape
    rows: int
    columns: int
    row_spacing_inches: float | None
    column_spacing_inches: float | None
    extent_inches: tuple[float, float]


def _project(nodes: list[SceneNode], axis: tuple[float, float]) -> list[float]:
    return [
        node.transform.position.x * axis[0] + node.transform.position.y * axis[1]
        for node in nodes
    ]


def _tolerance(nodes: list[SceneNode]) -> float:
    widest = max(max(node.dimensions.x, node.dimensions.y) for node in nodes)
    return widest * GROUP_TOLERANCE


def group(values: list[float], tolerance: float) -> list[list[float]]:
    """Values within `tolerance` of their neighbour belong together."""
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return groups


def _spacing(groups: list[list[float]]) -> float | None:
    """The typical gap between one row and the next."""
    if len(groups) < 2:
        return None
    centres = [statistics.fmean(items) for items in groups]
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    return to_inches(statistics.fmean(gaps))


def _near_a_wall(nodes: list[SceneNode], context: AskContext) -> int:
    from standardphysics_pipeline import gap_between_nodes

    walls = [node for node in context.graph.nodes if stands_upright(node)]
    if not walls:
        return 0
    return sum(
        1
        for node in nodes
        if to_inches(min(gap_between_nodes(node, wall) for wall in walls))
        <= AGAINST_A_WALL_INCHES
    )


def _extent(values: list[float]) -> float:
    return to_inches(max(values) - min(values)) if values else 0.0


def _room_span(context: AskContext, axis: tuple[float, float]) -> float:
    walls = [node for node in context.graph.nodes if stands_upright(node)]
    if not walls:
        return 0.0
    return _extent(_project(walls, axis))


def _shape_of(
    count: int, rows: int, columns: int, hugging: int, spread: tuple[float, float]
) -> Shape:
    if count == 1:
        return "one"
    if count == 2:
        return "pair"
    if rows == 1 or columns == 1:
        return "row"
    if rows * columns == count:
        return "two_rows" if min(rows, columns) == 2 and count == 4 else "grid"
    if hugging >= count - 1:
        return "ring"
    if max(spread) <= CLUSTER_FRACTION:
        return "cluster"
    return "spread"


@traced("ask.arrangement")
def arrangement(nodes: list[SceneNode], context: AskContext) -> Arrangement:
    back, right = shop_axes(context.scenario, context.graph)
    depths, sides = _project(nodes, back), _project(nodes, right)
    tolerance = _tolerance(nodes)

    rows = group(depths, tolerance)
    columns = group(sides, tolerance)
    room = (_room_span(context, back), _room_span(context, right))
    spread = (
        _extent(depths) / room[0] if room[0] else 0.0,
        _extent(sides) / room[1] if room[1] else 0.0,
    )

    return Arrangement(
        shape=_shape_of(
            len(nodes), len(rows), len(columns), _near_a_wall(nodes, context), spread
        ),
        rows=len(rows),
        columns=len(columns),
        row_spacing_inches=_spacing(rows),
        column_spacing_inches=_spacing(columns),
        extent_inches=(_extent(depths), _extent(sides)),
    )


def _number(count: int) -> str:
    return COUNT_WORDS.get(count, str(count))


def _row_sentence(what: str, found: Arrangement) -> str:
    axis = "the length of the room" if found.columns == 1 else "the width of the room"
    spacing = found.row_spacing_inches or found.column_spacing_inches
    gap = f", about {span(spacing)} apart" if spacing else ""
    return f"{what} stand in one row along {axis}{gap}."


def _grid_sentence(what: str, found: Arrangement) -> str:
    across, deep = _number(found.columns), _number(found.rows)
    sizes = []
    if found.column_spacing_inches:
        sizes.append(f"{span(found.column_spacing_inches)} apart across")
    if found.row_spacing_inches:
        sizes.append(f"{span(found.row_spacing_inches)} apart front to back")
    tail = f", {' and '.join(sizes)}" if sizes else ""
    return f"{what} sit in a {across} by {deep} grid{tail}."


SENTENCES = {
    "one": lambda what, found: f"{what} stands on its own.",
    "pair": lambda what, found: f"{what} sit as a pair, "
    f"{span(found.row_spacing_inches or found.column_spacing_inches or 0.0)} apart.",
    "row": _row_sentence,
    "two_rows": lambda what, found: f"{what} sit in two rows of two"
    + (
        f", {span(found.row_spacing_inches)} between the rows."
        if found.row_spacing_inches
        else "."
    ),
    "grid": _grid_sentence,
    "ring": lambda what, found: f"{what} run around the edges of the room.",
    "cluster": lambda what, found: f"{what} are grouped together in one part of "
    "the room.",
    "spread": lambda what, found: f"{what} are spread across the room, each one "
    "on its own.",
}


@traced("ask.describe")
def describe(query: Query, context: AskContext) -> Answer:
    found = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    placeable = [node for node in found if not bounds_the_room(node)]
    if not placeable:
        return _not_here(query)

    shape = arrangement(placeable, context)
    what = _what(placeable)
    text = SENTENCES[shape.shape](what, shape)
    return Answer(
        text=text,
        kind="DESCRIBE",
        query=query,
        subjects=tuple(node.id for node in placeable),
        locus=subject_locus(placeable, shape.shape.replace("_", " ")),
        data={
            "shape": shape.shape,
            "rows": shape.rows,
            "columns": shape.columns,
            "row_spacing_inches": shape.row_spacing_inches,
            "column_spacing_inches": shape.column_spacing_inches,
        },
    )


def _what(nodes: list[SceneNode]) -> str:
    label = nodes[0].label
    if len(nodes) == 1:
        return f"Your {label.casefold()}"
    return f"Your {_number(len(nodes))} {plural(label)}"


def _not_here(query: Query) -> Answer:
    asked = query.subject_labels[0].casefold() if query.subject_labels else "that"
    return Answer(
        text=f"The scan has no {plural(asked)} in it. Name a piece of furniture "
        "and we will tell you how it sits.",
        kind="DESCRIBE",
        query=query,
        data={"asked_about": asked},
    )
