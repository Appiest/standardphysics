"""Which node is the counter, and which door is the front one.

RoomPlan gives a coarse category and Astra gives a label. A check needs to know
which box is the thing the rule is about, so the mapping from label to role
lives here once rather than as a string comparison inside each check.
"""

from __future__ import annotations

from typing import Literal

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, lies_flat

SERVICE_COUNTER_LABELS = frozenset(
    {
        "ordering counter", "service counter", "counter", "checkout counter",
        "cash wrap", "register counter", "sales counter", "bar",
    }
)

ENTRANCE_LABELS = frozenset({"front door", "entrance", "entry door", "main door"})

DINING_SURFACE_LABELS = frozenset({"table", "dining table", "cafe table", "bar table"})

WORK_SURFACE_LABELS = frozenset({"desk", "work table", "work surface", "workbench"})

SEATING_LABELS = frozenset({"chair", "stool", "bar stool", "bench", "seat"})
"""Seats get pulled out to use a table, so they are never what stops someone using it."""

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

OPERABLE_PART_LABELS = frozenset(
    {
        "light switch", "switch", "thermostat", "soap dispenser", "paper towel dispenser",
        "towel dispenser", "hand dryer", "hand sanitizer", "sanitizer", "coat hook",
        "call button", "intercom", "door bell", "bell", "fire alarm", "fire extinguisher",
    }
)
"""Things a customer works with a hand while standing or sitting where they are, ADA 2010 309 and 308."""
HOUSINGS = frozenset({"dispenser", "station", "stand", "unit", "pump", "cabinet", "panel", "box"})
"""Words a detector adds after an operable thing's name for what holds it: a sanitizer dispenser is a sanitizer."""


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


MAX_OPERABLE_EXTENT_METERS = 1.0
"""A switch, dispenser or extinguisher fits in a metre; a 'dispenser' 1.2 m tall is a bad carve, not a control."""


def operable_parts(graph: SceneGraph) -> list[SceneNode]:
    return [
        node for node in graph.nodes
        if not bounds_the_room(node) and is_operable_part(node.label)
        and max(node.dimensions.as_tuple()) <= MAX_OPERABLE_EXTENT_METERS
    ]


def is_operable_part(label: str) -> bool:
    """Whether a free-text name ends in an operable thing's name, before at most one word for its housing.

    A detector writes "wall mounted hand sanitizer", "hand sanitizer dispenser"
    or "fire extinguisher cabinet" for the same few things. The name must come
    last, so "fire extinguisher sign" is a sign and "bell pepper" is not a bell.
    """
    words = _normalized(label).replace("-", " ").split()
    readings = [words, words[:-1]] if len(words) > 1 and words[-1] in HOUSINGS else [words]
    return any(reading[-len(name.split()):] == name.split() for reading in readings for name in OPERABLE_PART_LABELS)


def point_of_sale(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in POINT_OF_SALE_LABELS
    ]


UsedFromTheFloor = Literal["surface", "counter"]
"""surface: a dining or work surface, which 902.2 has a person pull up to face-on.
counter: a service counter, which 904.4 lets a person pull up to side-on or face-on.
"""


def used_from_the_floor(node: SceneNode) -> UsedFromTheFloor | None:
    """How a person uses this piece from where they sit or stand, if they do.

    None for everything else: a display case is looked into, a shelf reached
    past, and neither needs a patch of floor kept clear in front of it.
    """
    if bounds_the_room(node):
        return None
    label = _normalized(node.label)
    if label in DINING_SURFACE_LABELS or label in WORK_SURFACE_LABELS:
        return "surface"
    if label in SERVICE_COUNTER_LABELS:
        return "counter"
    return None


def is_seating(node: SceneNode) -> bool:
    return _normalized(node.label) in SEATING_LABELS or _normalized(node.raw_category) in SEATING_LABELS


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
