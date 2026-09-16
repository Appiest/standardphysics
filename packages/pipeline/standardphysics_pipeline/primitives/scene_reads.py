"""Primitives that read the measured scene without taking a new measurement.

What is on the desk, what does the whiteboard say, where in the room is this.
Every one of them answers from the graph, so the numbers and the names come
from the scan rather than from whoever asked.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from standardphysics_contracts import (
    Evidence,
    NodeSet,
    PrimitiveResult,
    SceneGraph,
    SceneNode,
    TextSet,
    Vec3,
    to_inches,
)

from .registry import Context, primitive


class NoArguments(BaseModel):
    """A primitive that reads the whole room and takes nothing."""


class NodeArgument(BaseModel):
    node_id: UUID = Field(description="The node to read, by its id in this room.")


class SearchArgument(BaseModel):
    words: str = Field(
        min_length=1,
        max_length=80,
        description="Words to match against object names, such as 'pen' or 'desk chair'.",
    )


def _missing(name: str, node_id: UUID) -> PrimitiveResult:
    return PrimitiveResult(
        primitive=name,
        quality="not_measurable",
        note="That is not something in this room.",
        evidence=Evidence(subjects=[node_id]),
    )


def _at(node: SceneNode) -> Vec3:
    return node.transform.position


def _node(graph: SceneGraph, node_id: UUID) -> SceneNode | None:
    try:
        return graph.by_id(node_id)
    except KeyError:
        return None


def _nodes_result(name: str, found: list[SceneNode], anchor: SceneNode | None) -> PrimitiveResult:
    return PrimitiveResult(
        primitive=name,
        payload=NodeSet(node_ids=[node.id for node in found]),
        evidence=Evidence(
            subjects=[node.id for node in found],
            at=_at(found[0]) if found else (_at(anchor) if anchor else None),
        ),
    )


@primitive(
    "objects_on",
    "Everything resting directly on this node's top surface, such as what is on a desk.",
    "nodes",
    NodeArgument,
)
def objects_on(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    anchor = _node(context.graph, arguments.node_id)
    if anchor is None:
        return _missing("objects_on", arguments.node_id)
    return _nodes_result("objects_on", context.graph.children_of(anchor.id, "rests_on"), anchor)


@primitive(
    "objects_inside",
    "Everything contained within this node, such as the pens in a cup.",
    "nodes",
    NodeArgument,
)
def objects_inside(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    anchor = _node(context.graph, arguments.node_id)
    if anchor is None:
        return _missing("objects_inside", arguments.node_id)
    return _nodes_result("objects_inside", context.graph.children_of(anchor.id, "inside"), anchor)


@primitive(
    "everything_carried_by",
    "Everything this node holds at any depth, so a desk reaches the pens in the cup on it.",
    "nodes",
    NodeArgument,
)
def everything_carried_by(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    anchor = _node(context.graph, arguments.node_id)
    if anchor is None:
        return _missing("everything_carried_by", arguments.node_id)
    return _nodes_result("everything_carried_by", context.graph.descendants_of(anchor.id), anchor)


@primitive(
    "what_carries",
    "The chain holding this node up, from what it sits on out to the floor.",
    "nodes",
    NodeArgument,
)
def what_carries(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    anchor = _node(context.graph, arguments.node_id)
    if anchor is None:
        return _missing("what_carries", arguments.node_id)
    return _nodes_result("what_carries", context.graph.ancestors_of(anchor.id), anchor)


@primitive(
    "find_objects",
    "Objects in the room whose name matches these words.",
    "nodes",
    SearchArgument,
)
def find_objects(arguments: SearchArgument, context: Context) -> PrimitiveResult:
    """Matched on the names the scan gave things.

    This is a plain word match, not a judgment about meaning. A caller that
    needs "somewhere to sit" rather than "chair" has to widen the words itself,
    because deciding that a stool counts is exactly the judgment a primitive is
    not allowed to make.
    """
    wanted = [word for word in arguments.words.lower().split() if word]
    found = [
        node
        for node in context.graph.nodes
        if node.kind == "object" and _named_by(node, wanted)
    ]
    return _nodes_result("find_objects", found, None)


@primitive(
    "text_on",
    "The words read off this node's surfaces, such as what a whiteboard says.",
    "texts",
    NodeArgument,
)
def text_on(arguments: NodeArgument, context: Context) -> PrimitiveResult:
    anchor = _node(context.graph, arguments.node_id)
    if anchor is None:
        return _missing("text_on", arguments.node_id)
    if not anchor.texts:
        return PrimitiveResult(
            primitive="text_on",
            payload=TextSet(texts=[]),
            quality="needs_another_look",
            note=(
                f"Nothing was read off the {anchor.label.lower()}."
                " It may have no writing, or the scan never saw it closely."
            ),
            evidence=Evidence(subjects=[anchor.id], at=_at(anchor)),
        )
    return PrimitiveResult(
        primitive="text_on",
        payload=TextSet(texts=[item.text for item in anchor.texts]),
        evidence=Evidence(
            subjects=[anchor.id],
            at=_at(anchor),
            frames=[frame for item in anchor.texts for frame in item.evidence_frame_ids],
        ),
        quality="measured" if all(item.confidence >= 0.6 for item in anchor.texts) else "needs_another_look",
    )


@primitive(
    "everything_in_the_room",
    "Every object the scan found, however it is stacked.",
    "nodes",
    NoArguments,
)
def everything_in_the_room(_: NoArguments, context: Context) -> PrimitiveResult:
    found = [node for node in context.graph.nodes if node.kind == "object"]
    return _nodes_result("everything_in_the_room", found, None)


def _named_by(node: SceneNode, wanted: list[str]) -> bool:
    name = f"{node.label} {node.raw_category}".lower()
    return all(word in name for word in wanted)


def inches_between(first: SceneNode, second: SceneNode) -> float:
    a, b = _at(first), _at(second)
    return to_inches(((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5)
