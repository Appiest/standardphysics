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
        "cash wrap", "register counter", "sales counter", "bar", "front desk", "reception desk",
        "pos counter", "payment counter", "transaction counter", "cashier counter",
        "pickup counter", "pick-up counter", "pickup shelf", "handoff counter", "hand-off counter",
        "hand-off shelf", "handoff shelf", "order pickup", "drink pickup", "beverage pickup", "to-go shelf",
    }
)
"""904.4 covers every counter a customer is served at, including the one they collect an order from."""

ENTRANCE_LABELS = frozenset({"front door", "entrance", "entry door", "main door"})

DINING_SURFACE_LABELS = frozenset({"table", "dining table", "cafe table", "bar table", "accessible table"})

WORK_SURFACE_LABELS = frozenset({"desk", "work table", "work surface", "workbench"})

SEATING_LABELS = frozenset({"chair", "stool", "bar stool", "bench", "seat"})
"""Seats get pulled out to use a table, so they are never what stops someone using it."""

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
        "card machine", "cash drawer", "pos terminal", "cashier drawer", "cash box", "tablet pos",
        "square reader", "tip screen",
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
KIOSK_LABELS = frozenset(
    {
        "kiosk", "self-order kiosk", "self order kiosk", "ordering kiosk", "ordering machine",
        "self-service kiosk", "self service kiosk", "touchscreen", "touch screen", "order screen",
        "self checkout", "self-checkout", "card kiosk",
    }
)
"""Machines a customer orders or pays at on their own, which 308 and 305 treat like any operable part."""

SELF_SERVICE_LABELS = frozenset(
    {
        "condiments", "condiment station", "condiment bar", "napkins", "napkin dispenser", "lids",
        "lid dispenser", "straws", "straw dispenser", "utensils", "utensil dispenser", "cutlery",
        "drink dispenser", "beverage dispenser", "soda fountain", "beverage station", "drink station",
        "self-serve station", "self serve station", "self-service station", "water dispenser",
    }
)
"""Things a customer takes for themselves, which 904.5.1 puts within the reach ranges of 308."""

RAMP_LABELS = frozenset({"ramp", "accessible ramp", "wheelchair ramp", "incline", "slope"})

RAMP_LANDING_LABELS = frozenset({"ramp landing", "landing"})

HANDRAIL_LABELS = frozenset({"handrail", "hand rail", "railing", "rail", "guardrail", "guard rail", "banister"})

LAVATORY_LABELS = frozenset(
    {"sink", "lavatory", "bathroom sink", "restroom sink", "hand sink", "wash basin", "washbasin", "basin", "vanity"}
)
"""606 covers lavatories and sinks alike; RoomPlan boxes both as a sink."""

MIRROR_LABELS = frozenset({"mirror", "bathroom mirror", "restroom mirror", "vanity mirror", "wall mirror"})

GRAB_BAR_LABELS = frozenset({"grab bar", "grab bars", "grab rail", "safety bar", "toilet grab bar"})

HOUSINGS = frozenset({"dispenser", "station", "stand", "unit", "pump", "cabinet", "panel", "box"})
"""Words a detector adds after an operable thing's name for what holds it: a sanitizer dispenser is a sanitizer."""


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


GENERIC_DOOR_LABELS = frozenset({"door", "doorway", "opening"})


def door_name(graph: SceneGraph, door: SceneNode) -> str:
    """How a request names this door: the front door, its own label, or its place among unnamed doors."""
    front = entrance(graph)
    if front is not None and front.id == door.id:
        return "the front door"
    label = _normalized(door.label)
    if label and label not in GENERIC_DOOR_LABELS:
        return f"the {label}"
    ids = [each.id for each in doors(graph)]
    return f"door {ids.index(door.id) + 1}" if door.id in ids else "the door"


def dining_surfaces(graph: SceneGraph) -> list[SceneNode]:
    return [
        node
        for node in graph.nodes
        if not bounds_the_room(node) and _normalized(node.label) in DINING_SURFACE_LABELS
    ]


def is_seating(node: SceneNode) -> bool:
    """A chair, stool or bench, by its label or the category the scan gave it."""
    return _normalized(node.label) in SEATING_LABELS or _normalized(node.raw_category) in SEATING_LABELS


def seating(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if is_seating(node)]


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


def _labelled(graph: SceneGraph, labels: frozenset[str]) -> list[SceneNode]:
    return [node for node in graph.nodes if not bounds_the_room(node) and _normalized(node.label) in labels]


def kiosks(graph: SceneGraph) -> list[SceneNode]:
    return _labelled(graph, KIOSK_LABELS)


def self_service(graph: SceneGraph) -> list[SceneNode]:
    return _labelled(graph, SELF_SERVICE_LABELS)


def ramps(graph: SceneGraph) -> list[SceneNode]:
    return _labelled(graph, RAMP_LABELS)


def ramp_landings(graph: SceneGraph) -> list[SceneNode]:
    return _labelled(graph, RAMP_LANDING_LABELS)


def handrails(graph: SceneGraph) -> list[SceneNode]:
    """Railings are thin and long, so a railing's box can read as a sheet of the room; the name decides here."""
    return [node for node in graph.nodes if _normalized(node.label) in HANDRAIL_LABELS]


def _named(graph: SceneGraph, labels: frozenset[str]) -> list[SceneNode]:
    """By the label a person or a model gave it, or by the category the scanner did.

    A mirror is a thin sheet and a grab bar a thin rod, so either can read as
    part of the room's shell; the name decides for these, as it does for a railing.
    """
    return [node for node in graph.nodes
            if _normalized(node.label) in labels or _normalized(node.raw_category) in labels]


def lavatories(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in _named(graph, LAVATORY_LABELS) if not bounds_the_room(node)]


def mirrors(graph: SceneGraph) -> list[SceneNode]:
    return _named(graph, MIRROR_LABELS)


def grab_bars(graph: SceneGraph) -> list[SceneNode]:
    return _named(graph, GRAB_BAR_LABELS)


def point_of_sale(graph: SceneGraph) -> list[SceneNode]:
    return _served_at(
        graph,
        [
            node
            for node in graph.nodes
            if not bounds_the_room(node) and _normalized(node.label) in POINT_OF_SALE_LABELS
        ],
    )


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
