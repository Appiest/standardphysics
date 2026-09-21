"""Which node is the counter, and which door is the front one.

RoomPlan gives a coarse category and Astra gives a label. A check needs to know
which box is the thing the rule is about, so the mapping from label to role
lives here once rather than as a string comparison inside each check.
"""

from __future__ import annotations

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, lies_flat

SERVICE_COUNTER_LABELS = frozenset(
    {
        "ordering counter", "service counter", "counter", "checkout counter",
        "cash wrap", "register counter", "sales counter", "bar",
    }
)

ENTRANCE_LABELS = frozenset({"front door", "entrance", "entry door", "main door"})

DINING_SURFACE_LABELS = frozenset({"table", "dining table", "cafe table", "bar table"})

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


def service_counters(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in SERVICE_COUNTER_LABELS
    ]


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


def lowered_sections(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in LOWERED_SECTION_LABELS
    ]


def point_of_sale(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in POINT_OF_SALE_LABELS
    ]


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
