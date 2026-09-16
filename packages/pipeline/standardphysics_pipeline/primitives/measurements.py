"""Primitives that take a measurement, in the units the standards are written in.

These are what a rule names instead of carrying its own check. `height_of` with
a threshold of 36 inches at most is the whole of ADA 904.4.1, and the next
provision about a height is the same primitive with a different number.

Nothing here reads a model, and nothing here decides whether a number is
acceptable. A primitive says how far apart two things are; a rule says whether
that is far enough.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from standardphysics_contracts import (
    Evidence,
    PrimitiveResult,
    Quantity,
    SceneGraph,
    SceneNode,
    Vec3,
    to_inches,
)

from .registry import Context, primitive

Axis = Literal["width", "depth", "height"]

AXIS_INDEX: dict[Axis, str] = {"width": "x", "depth": "y", "height": "z"}


class NodeArgument(BaseModel):
    node_id: UUID = Field(description="The node to measure, by its id in this room.")


class AxisArgument(BaseModel):
    node_id: UUID = Field(description="The node to measure.")
    axis: Axis = Field(description="Which way to measure it.")


class PairArgument(BaseModel):
    from_node_id: UUID = Field(description="The node to measure from.")
    to_node_id: UUID = Field(description="The node to measure to.")


def _node(graph: SceneGraph, node_id: UUID) -> SceneNode | None:
    try:
        return graph.by_id(node_id)
    except KeyError:
        return None


def _not_in_the_room(name: str, node_id: UUID) -> PrimitiveResult:
    return PrimitiveResult(
        primitive=name,
        quality="not_measurable",
        note="That is not something in this room.",
        evidence=Evidence(subjects=[node_id]),
    )


def _inches(name: str, value: float, subjects: list[UUID], at: Vec3, quality: str = "measured") -> PrimitiveResult:
    return PrimitiveResult(
        primitive=name,
        payload=Quantity(value=round(value, 2), unit="in"),
        evidence=Evidence(subjects=subjects, at=at),
        quality=quality,
    )


def _top_of(node: SceneNode) -> float:
    return node.transform.position.z + node.dimensions.z / 2


@primitive(
    "height_of",
    "How far the top surface of this node sits above the floor, in inches.",
    "quantity",
    NodeArgument,
)
def height_of(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    node = _node(context.graph, arguments.node_id)
    if node is None:
        return _not_in_the_room("height_of", arguments.node_id)
    position = node.transform.position
    top = _top_of(node)
    return _inches(
        "height_of",
        to_inches(top),
        [node.id],
        Vec3(x=position.x, y=position.y, z=top),
        "needs_another_look" if node.quality == "needs_another_look" else "measured",
    )


@primitive(
    "size_of",
    "How wide, deep or tall this node is, in inches.",
    "quantity",
    AxisArgument,
)
def size_of(arguments: AxisArgument, context: Context) -> PrimitiveResult:
    node = _node(context.graph, arguments.node_id)
    if node is None:
        return _not_in_the_room("size_of", arguments.node_id)
    extent = getattr(node.dimensions, AXIS_INDEX[arguments.axis])
    return _inches(
        "size_of",
        to_inches(extent),
        [node.id],
        node.transform.position,
        "needs_another_look" if node.quality == "needs_another_look" else "measured",
    )


@primitive(
    "distance_between",
    "How far apart two nodes are, centre to centre, in inches.",
    "quantity",
    PairArgument,
)
def distance_between(arguments: PairArgument, context: Context) -> PrimitiveResult:
    first = _node(context.graph, arguments.from_node_id)
    second = _node(context.graph, arguments.to_node_id)
    if first is None:
        return _not_in_the_room("distance_between", arguments.from_node_id)
    if second is None:
        return _not_in_the_room("distance_between", arguments.to_node_id)
    a, b = first.transform.position, second.transform.position
    span = ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5
    midpoint = Vec3(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2, z=(a.z + b.z) / 2)
    return _inches("distance_between", to_inches(span), [first.id, second.id], midpoint)


@primitive(
    "floor_area_of",
    "How much floor this node stands on, in square inches.",
    "quantity",
    NodeArgument,
)
def floor_area_of(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    node = _node(context.graph, arguments.node_id)
    if node is None:
        return _not_in_the_room("floor_area_of", arguments.node_id)
    if not context.graph.stands_on_floor(node):
        carried_by = context.graph.by_id(node.parent_id).label.lower() if node.parent_id else "something else"
        return PrimitiveResult(
            primitive="floor_area_of",
            quality="not_measurable",
            note=f"The {node.label.lower()} is on the {carried_by}, so it stands on no floor of its own.",
            evidence=Evidence(subjects=[node.id], at=node.transform.position),
        )
    area = to_inches(node.dimensions.x) * to_inches(node.dimensions.y)
    return PrimitiveResult(
        primitive="floor_area_of",
        payload=Quantity(value=round(area, 2), unit="sq in"),
        evidence=Evidence(subjects=[node.id], at=node.transform.position),
    )


@primitive(
    "clearance_above",
    "The empty height between this node's top and whatever is above it, in inches.",
    "quantity",
    NodeArgument,
)
def clearance_above(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    node = _node(context.graph, arguments.node_id)
    if node is None:
        return _not_in_the_room("clearance_above", arguments.node_id)
    ceiling = max((_top_of(other) for other in context.graph.nodes if other.kind == "wall"), default=None)
    if ceiling is None:
        return PrimitiveResult(
            primitive="clearance_above",
            quality="not_measurable",
            note="The scan found no walls, so there is no ceiling height to measure against.",
            evidence=Evidence(subjects=[node.id]),
        )
    position = node.transform.position
    return _inches(
        "clearance_above",
        to_inches(max(0.0, ceiling - _top_of(node))),
        [node.id],
        Vec3(x=position.x, y=position.y, z=_top_of(node)),
    )
