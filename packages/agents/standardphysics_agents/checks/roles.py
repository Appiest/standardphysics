"""Which node is the counter, and which door is the front one.

RoomPlan gives a coarse category and Astra gives a label. A check needs to know
which box is the thing the rule is about, so the mapping from label to role
lives here once rather than as a string comparison inside each check.
"""

from __future__ import annotations

from typing import Literal

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, lies_flat
from standardphysics_pipeline import sleeping_places

RoomKind = Literal["service", "home", "general"]

SERVICE_COUNTER_LABELS = frozenset(
    {
        "ordering counter", "service counter", "counter", "checkout counter",
        "cash wrap", "register counter", "sales counter", "bar",
    }
)

ENTRANCE_LABELS = frozenset({"front door", "entrance", "entry door", "main door"})

DINING_SURFACE_LABELS = frozenset({"table", "dining table", "cafe table", "bar table"})

SEATING_LABELS = frozenset({"chair", "stool", "bar stool", "armchair", "seat"})

LOWERED_SECTION_LABELS = frozenset(
    {
        "lowered counter section",
        "lowered section",
        "accessible counter",
        "accessible section",
        "low counter",
    }
)

POINT_OF_SALE_LABELS = frozenset(
    {
        "card reader",
        "register",
        "cash register",
        "point of sale",
        "payment terminal",
        "card machine",
    }
)


def _normalized(label: str) -> str:
    return label.strip().casefold()


def _served_at(graph: SceneGraph, nodes: list[SceneNode]) -> list[SceneNode]:
    """In a home, only what the owner marked counts as a place people are served.

    A dorm's kitchen counter is labelled "Counter" and nobody orders from it,
    so a label alone does not make one there.
    """
    if not sleeping_places(graph):
        return nodes
    return [node for node in nodes if node.labeled_by == "owner"]


def service_counters(graph: SceneGraph) -> list[SceneNode]:
    return _served_at(
        graph,
        [
            node
            for node in graph.nodes
            if not bounds_the_room(node) and _normalized(node.label) in SERVICE_COUNTER_LABELS
        ],
    )


def room_kind(graph: SceneGraph) -> RoomKind:
    """What kind of room a scan is, from what is in it.

    A counter or a register people are served at makes it a service business.
    Otherwise a bed makes it a home. Anything else is a general room. Only a
    service business gets routes to a counter.
    """
    if service_counters(graph) or point_of_sale(graph):
        return "service"
    if sleeping_places(graph):
        return "home"
    return "general"


def doors(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if node.kind == "door"]


def entrance(graph: SceneGraph) -> SceneNode | None:
    """The door a customer comes in through.

    A labelled front door wins. Otherwise the only door is the front door, and
    with several unlabelled doors there is nothing to choose between them.
    """
    all_doors = doors(graph)
    for door in all_doors:
        if _normalized(door.label) in ENTRANCE_LABELS:
            return door
    return all_doors[0] if len(all_doors) == 1 else None


def dining_surfaces(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in DINING_SURFACE_LABELS
    ]


def is_seating(node: SceneNode) -> bool:
    return node.kind == "object" and _normalized(node.label) in SEATING_LABELS


def seating(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if is_seating(node)]


def lowered_sections(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in LOWERED_SECTION_LABELS
    ]


def point_of_sale(graph: SceneGraph) -> list[SceneNode]:
    return _served_at(
        graph,
        [
            node
            for node in graph.nodes
            if not bounds_the_room(node) and _normalized(node.label) in POINT_OF_SALE_LABELS
        ],
    )


def floors(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if lies_flat(node)]


def needs_another_look(graph: SceneGraph, node_ids) -> list[SceneNode]:
    """The nodes in this answer that we are not confident about."""
    found = []
    for node_id in node_ids:
        try:
            node = graph.by_id(node_id)
        except KeyError:
            continue
        if node.quality == "needs_another_look":
            found.append(node)
    return found
