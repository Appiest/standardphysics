"""What a label patch may change on a node, and the local rules that label a shop without a model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from standardphysics_contracts import (
    DisplayAppearance,
    DisplayReconstruction,
    SceneGraph,
    SceneNode,
    bounds_the_room,
)

from .footprints import covered_fraction, footprint, gap_between
from .ingest import FIXED_CATEGORIES
from .occupancy import reads_as_wall

QualityName = Literal["measured", "needs_another_look", "confirmed"]
ReconstructionSource = Literal["astra", "roomplan"]


COUNTER_LABELS = frozenset({"ordering counter", "service counter", "counter", "checkout counter", "cash wrap", "register counter", "sales counter"})
COUNTER_HEIGHT = (0.80, 1.40)
COUNTER_LENGTH = 2.2
WALL_GAP_METERS = 0.45
SHARED_FOOTPRINT = 0.5
"""A box with at least this much of its floor inside another box is likely one thing counted twice, or two things
RoomPlan merged. Touching or tucked-in neighbours, like chairs pulled up to a counter, are measured as they are."""
THIN_METERS = 0.04


@dataclass(frozen=True)
class LabelPatch:
    node_id: UUID
    label: str
    movable: bool
    quality: QualityName
    appearance: DisplayAppearance | None = None
    reconstruction: DisplayReconstruction | None = None


def apply_patches(
    graph: SceneGraph, patches: list[LabelPatch], *, source: ReconstructionSource = "roomplan"
) -> SceneGraph:
    """A patch cannot resize, remove, invent, or unlock a node."""
    by_id = {patch.node_id: patch for patch in patches}
    return graph.model_copy(update={"nodes": [_apply_one(node, by_id.get(node.id), source) for node in graph.nodes]})


def local_patches(graph: SceneGraph) -> list[LabelPatch]:
    # A's SHEET_THICKNESS calibration (0.05m) means a real scan's walls read as
    # sheets but a labelled wall measured 0.15m thick does not; the scanner's
    # kind label is the fallback, matching the pipeline's reads_as_wall test
    # and agents/checks/walls. upright_walls.
    walls = [node for node in graph.nodes if reads_as_wall(node)]
    objects = graph.contents()
    return [_local_patch(node, walls, objects, graph) for node in graph.nodes]


def _apply_one(node: SceneNode, patch: LabelPatch | None, source: ReconstructionSource) -> SceneNode:
    if patch is None:
        return node
    if node.labeled_by == "owner" or node.quality == "confirmed":
        if source == "astra" and patch.reconstruction is not None:
            return node.model_copy(update={"reconstruction": patch.reconstruction})
        return node
    if source == "roomplan" and node.labeled_by == "astra":
        return node
    update: dict[str, Any] = {
        "label": patch.label or node.label,
        "movable": _locked_movable(node, patch.movable),
        "quality": node.quality if source == "astra" or node.quality == "needs_another_look" else patch.quality,
    }
    if source == "astra":
        update["labeled_by"] = "astra"
        if patch.appearance is not None:
            update["appearance"] = patch.appearance
        if patch.reconstruction is not None:
            update["reconstruction"] = patch.reconstruction
    return node.model_copy(update=update)


def _locked_movable(node: SceneNode, proposed: bool) -> bool:
    if bounds_the_room(node) or node.raw_category in FIXED_CATEGORIES:
        return False
    if not node.movable:
        return False
    return proposed


def _local_patch(node: SceneNode, walls: list[SceneNode], objects: list[SceneNode], graph: SceneGraph) -> LabelPatch:
    label, movable = _local_identity(node, walls, graph)
    return LabelPatch(node.id, label, movable, _local_quality(node, objects))


def _local_identity(node: SceneNode, walls: list[SceneNode], graph: SceneGraph) -> tuple[str, bool]:
    if node.kind == "door":
        return _door_label(node, graph), False
    if bounds_the_room(node) or node.raw_category in FIXED_CATEGORIES:
        return node.label, False
    if node.label.strip().casefold() in COUNTER_LABELS:
        return node.label, False
    if _looks_like_counter(node, walls):
        return "Ordering counter", False
    return _named_furniture(node.raw_category) or (node.label, node.movable)


def _named_furniture(category: str) -> tuple[str, bool] | None:
    return {
        "storage": ("Display case", True), "chair": ("Chair", True), "table": ("Table", True),
        "sofa": ("Sofa", True), "stool": ("Stool", True), "bench": ("Bench", True),
    }.get(category)


def _door_label(node: SceneNode, graph: SceneGraph) -> str:
    doors = [item for item in graph.nodes if item.kind == "door"]
    return "Front door" if len(doors) == 1 and node.label.strip().casefold() == "door" else node.label


def _looks_like_counter(node: SceneNode, walls: list[SceneNode]) -> bool:
    return (
        node.raw_category in {"storage", "table", "counter"}
        and COUNTER_HEIGHT[0] <= node.dimensions.z <= COUNTER_HEIGHT[1]
        and max(node.dimensions.x, node.dimensions.y) >= COUNTER_LENGTH
        and any(gap_between(footprint(node), footprint(wall)) <= WALL_GAP_METERS for wall in walls)
    )


def _shares_footprint(own: list[tuple[float, float]], other: list[tuple[float, float]]) -> bool:
    """Whether most of this box's own floor lies inside the other's. A stool tucked under a counter shares its
    footprint; the counter, with most of its floor clear, does not."""
    return covered_fraction(own, other) >= SHARED_FOOTPRINT


def _local_quality(node: SceneNode, objects: list[SceneNode]) -> QualityName:
    if node.quality == "confirmed" or bounds_the_room(node):
        return node.quality
    if min(node.dimensions.x, node.dimensions.y, node.dimensions.z) < THIN_METERS:
        return "needs_another_look"
    own = footprint(node)
    if any(_shares_footprint(own, footprint(other)) for other in objects if other.id != node.id):
        return "needs_another_look"
    return node.quality
