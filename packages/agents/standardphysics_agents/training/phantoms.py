"""Pieces a training room must not let a model move.

Photo discovery names things RoomPlan has no box for, and some of what it
finds is not furniture at all: room 6's "Sofa" is 18 by 47 by 38 cm and floats
27 cm above the floor. Trained on rooms where that box was movable, models
learned to shove it around to widen routes. A training room pins three kinds of
piece so no target and no reward ever depends on moving one:

    unconfirmed_discovery  named from the photos and never confirmed by a person
    implausible_size       measures outside what its label allows (`PLAUSIBLE_SIZES`)
    floating               its underside is more than `RESTING_GAP` above the floor
                           and nothing is under it to rest on

A person confirming a node is recorded on the node itself: the owner's review
and relabel endpoints set `labeled_by` to "owner", a hand-entered number sets
`quality` to "confirmed", and a reviewed fitting carries an attachment whose
`review_status` is "confirmed_by_user". A node still labelled by discovery has
had none of these.

This is a filter over training copies. The production pipeline never calls it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, lies_flat, measured_as
from standardphysics_pipeline import contains_point, footprint
from standardphysics_pipeline.footprints import floor_polygon

from ..fix.moves import RESTING_GAP, top_of, underside

DISCOVERY_SOURCE = "discovery"
PERSON_SOURCES = frozenset({"owner"})


@dataclass(frozen=True)
class SizeRange:
    """Plausible metres for one kind of furniture.

    `shortest_side` and `longest_side` bound the footprint's two sides, and
    `height` bounds how tall it stands, all as `measured_as` reads a node.
    """

    shortest_side: tuple[float, float]
    longest_side: tuple[float, float]
    height: tuple[float, float]

    def admits(self, width: float, depth: float, height: float) -> bool:
        short, long = sorted((width, depth))
        return (
            self.shortest_side[0] <= short <= self.shortest_side[1]
            and self.longest_side[0] <= long <= self.longest_side[1]
            and self.height[0] <= height <= self.height[1]
        )


PLAUSIBLE_SIZES: dict[str, SizeRange] = {
    "chair": SizeRange((0.30, 1.00), (0.35, 1.20), (0.40, 1.40)),
    "stool": SizeRange((0.25, 0.80), (0.25, 0.90), (0.35, 1.10)),
    "sofa": SizeRange((0.50, 1.40), (0.70, 3.60), (0.50, 1.30)),
    "bench": SizeRange((0.25, 1.20), (0.60, 3.60), (0.30, 1.20)),
    "table": SizeRange((0.35, 2.00), (0.40, 4.80), (0.35, 1.20)),
    "desk": SizeRange((0.40, 1.20), (0.60, 3.20), (0.60, 1.20)),
    "storage": SizeRange((0.20, 1.20), (0.30, 3.20), (0.40, 2.60)),
    "cabinet": SizeRange((0.20, 1.20), (0.30, 3.20), (0.40, 2.60)),
    "shelf": SizeRange((0.15, 1.00), (0.30, 3.20), (0.40, 2.60)),
    "bookcase": SizeRange((0.15, 1.00), (0.30, 3.20), (0.60, 2.60)),
    "bed": SizeRange((0.70, 2.30), (1.60, 2.50), (0.25, 1.40)),
    "display case": SizeRange((0.30, 1.50), (0.50, 4.50), (0.60, 2.50)),
    "bin": SizeRange((0.15, 1.00), (0.20, 1.40), (0.25, 1.30)),
}
"""What each furniture label may measure, in metres, for training rooms.

Ranges are deliberately wide: they exist to catch a box that cannot be the
thing it is called (a 38 cm tall "sofa" 18 cm deep, a 4 cm tall "chair"), not
to judge style. A label matches the first entry whose name appears in it as
whole words, so "orange chair" and "chair_right" are chairs and "display case"
is not a case of anything else. A label no entry matches is not judged on size.
"""


@dataclass(frozen=True)
class Pinned:
    node_id: UUID
    label: str
    reasons: tuple[str, ...]
    details: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"node_id": str(self.node_id), "label": self.label, "reasons": list(self.reasons),
                "details": list(self.details)}


def _words(text: str) -> str:
    return " " + " ".join(re.split(r"[^a-z]+", text.casefold())).strip() + " "


def size_category(node: SceneNode) -> str | None:
    for text in (node.label, node.raw_category):
        words = _words(text)
        for category in PLAUSIBLE_SIZES:
            if f" {category} " in words:
                return category
    return None


def confirmed_by_person(node: SceneNode) -> bool:
    if node.labeled_by in PERSON_SOURCES or node.quality == "confirmed":
        return True
    return node.attachment is not None and node.attachment.review_status == "confirmed_by_user"


def _unconfirmed_discovery(graph: SceneGraph, node: SceneNode) -> str | None:
    if node.labeled_by == DISCOVERY_SOURCE and not confirmed_by_person(node):
        return "labeled_by discovery, no confirmation recorded"
    return None


def _implausible_size(graph: SceneGraph, node: SceneNode) -> str | None:
    category = size_category(node)
    if category is None:
        return None
    size = measured_as(node)
    if PLAUSIBLE_SIZES[category].admits(size.x, size.y, size.z):
        return None
    return f"{category} measuring {size.x:.2f} x {size.y:.2f} x {size.z:.2f} m"


def _floor_under(graph: SceneGraph, node: SceneNode) -> float:
    centre = (node.transform.position.x, node.transform.position.y)
    floors = [other for other in graph.nodes if lies_flat(other)]
    under = [floor for floor in floors if contains_point(floor_polygon(floor), centre)]
    chosen = under or floors
    return min(floor.transform.position.z for floor in chosen) if chosen else 0.0


def _has_support(graph: SceneGraph, node: SceneNode) -> bool:
    centre = (node.transform.position.x, node.transform.position.y)
    bottom = underside(node)
    return any(
        other.id != node.id
        and not bounds_the_room(other)
        and abs(bottom - top_of(other)) <= RESTING_GAP
        and contains_point(footprint(other), centre)
        for other in graph.nodes
    )


def _floating(graph: SceneGraph, node: SceneNode) -> str | None:
    lift = underside(node) - _floor_under(graph, node)
    if lift <= RESTING_GAP or _has_support(graph, node):
        return None
    return f"underside {lift:.2f} m above the floor with nothing under it"


REASONS = (
    ("unconfirmed_discovery", _unconfirmed_discovery),
    ("implausible_size", _implausible_size),
    ("floating", _floating),
)


def _why_pinned(graph: SceneGraph, node: SceneNode) -> Pinned | None:
    found = [(reason, detail) for reason, test in REASONS if (detail := test(graph, node))]
    if not found:
        return None
    return Pinned(node.id, node.label, tuple(r for r, _ in found), tuple(d for _, d in found))


def phantoms(graph: SceneGraph) -> list[Pinned]:
    """Every movable piece a training room must hold still, and why."""
    found = (_why_pinned(graph, node) for node in graph.nodes if node.movable and not bounds_the_room(node))
    return [pinned for pinned in found if pinned is not None]


def unmeasured(graph: SceneGraph) -> list[SceneNode]:
    """Nodes with no extent in any direction.

    The occupancy grid reads one of these as a capture that measured nothing
    and blocks every cell, so a single zero-size candidate fitting leaves a
    whole library floor with no walkable ground. Training copies drop them.
    """
    return [node for node in graph.nodes if max(node.dimensions.as_tuple()) <= 0]


def without_unmeasured(graph: SceneGraph) -> SceneGraph:
    return without_nodes(graph, {node.id for node in unmeasured(graph)})


def scan_errors(graph: SceneGraph, pinned: list[Pinned]) -> list[SceneNode]:
    """Pinned furniture that floats with nothing under it, which is a scan error rather than a piece.

    A chair or table hanging half a metre in the air is not somewhere a person
    could put it back to, and leaving it in the room teaches a model to stack
    furniture on it. Only pieces with a furniture label (`size_category`) count;
    a floating television or artwork is plausibly mounted and stays.
    """
    floating = {item.node_id for item in pinned if "floating" in item.reasons}
    return [node for node in graph.nodes if node.id in floating and size_category(node) is not None]


def without_nodes(graph: SceneGraph, dropped: set) -> SceneGraph:
    kept = [node for node in graph.nodes if node.id not in dropped]
    return graph.model_copy(update={"nodes": [
        node.model_copy(update={"parent_id": None, "relation": None}) if node.parent_id in dropped else node
        for node in kept
    ]})


def pin(graph: SceneGraph, pinned: list[Pinned]) -> SceneGraph:
    """The same room with every pinned piece made immovable."""
    ids = {item.node_id for item in pinned}
    return graph.model_copy(
        update={"nodes": [node.model_copy(update={"movable": False}) if node.id in ids else node for node in graph.nodes]}
    )
