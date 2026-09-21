"""Which points a node already owns, and which node a new one is standing on.

Everything RoomPlan boxed is already accounted for, so discovery only looks at
what is left over. Testing that membership in the node's own turned frame
matters: a counter at forty degrees covers a much larger axis-aligned range
than it actually occupies, and testing against that range would hide a
terminal sitting beside it.
"""

from __future__ import annotations

from uuid import UUID

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

from .carve import CarvedBox

CLAIMED_MARGIN = 0.03
"""Points within three centimetres of a known node's shell belong to that node."""
UNCLAIMED_LID = 0.10
"""How much of a piece of furniture's own height it does not get to claim.

Everything anybody leaves on a table lives in the last few centimetres of that
table's box, and a generated box is least accurate exactly there: on a real
capture RoomPlan's tops miss the scanned surface by up to twenty-one inches. A
table that claims all the way to its own ceiling therefore swallows the laptop
standing on it, and the laptop is deleted before anything can look at it. So a
piece of furniture claims its body and leaves its lid alone."""
RESTING_GAP = 0.12
"""A new object counts as sitting on a node whose top is within this of its underside."""
STRUCTURE_KINDS = frozenset({"wall", "floor", "door", "window", "opening"})


def _frame(node: SceneNode) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    rotation = m[:3, :3]
    scale = np.linalg.norm(rotation, axis=0)
    scale[scale == 0] = 1.0
    half = np.asarray(node.dimensions.as_tuple(), dtype=np.float64) / 2
    return rotation / scale, m[:3, 3], half


def to_local(points: np.ndarray, node: SceneNode) -> np.ndarray:
    rotation, origin, _ = _frame(node)
    return (points - origin) @ rotation


def inside(points: np.ndarray, node: SceneNode, margin: float = CLAIMED_MARGIN) -> np.ndarray:
    """Whether each point lies within the node's own box, give or take a margin."""
    _, _, half = _frame(node)
    local = np.abs(to_local(points, node))
    return np.all(local <= half + margin, axis=1)


def claimed_by(points: np.ndarray, node: SceneNode, margin: float = CLAIMED_MARGIN) -> np.ndarray:
    """What this node accounts for: its whole box, except the lid of furniture."""
    _, _, half = _frame(node)
    local = to_local(points, node)
    within = np.all(np.abs(local) <= half + margin, axis=1)
    if bounds_the_room(node) or half[2] * 2 <= UNCLAIMED_LID:
        return within
    return within & (local[:, 2] <= half[2] - UNCLAIMED_LID)


def claimed_by_any(points: np.ndarray, graph: SceneGraph, margin: float = CLAIMED_MARGIN) -> np.ndarray:
    """Points already inside something the scan measured."""
    owned = np.zeros(len(points), dtype=bool)
    for node in graph.nodes:
        owned |= claimed_by(points, node, margin)
    return owned


def structure_points(points: np.ndarray, graph: SceneGraph, margin: float = CLAIMED_MARGIN) -> np.ndarray:
    """Points belonging to the walls, floor and openings, which nothing may delete."""
    owned = np.zeros(len(points), dtype=bool)
    for node in graph.nodes:
        if node.kind in STRUCTURE_KINDS:
            owned |= inside(points, node, margin)
    return owned


def contained_fraction(carved: CarvedBox, node: SceneNode) -> float:
    """The share of a carved object's own points that fall inside an existing node."""
    if not len(carved.points):
        return 0.0
    return float(inside(carved.points, node, 0.0).mean())


def resting_parent(carved: CarvedBox, graph: SceneGraph) -> UUID | None:
    """The node whose top surface this object stands on, when it is not on the floor."""
    underside = carved.floor_clearance
    if underside <= RESTING_GAP:
        return None
    footprint = np.asarray([[carved.centre[0], carved.centre[1], 0.0]], dtype=np.float64)
    best: tuple[float, UUID] | None = None
    for node in graph.nodes:
        if bounds_the_room(node):
            continue
        top = node.transform.position.z + node.dimensions.z / 2
        gap = underside - top
        if -RESTING_GAP <= gap <= RESTING_GAP and _over(footprint, node) and (best is None or gap < best[0]):
            best = (gap, node.id)
    return best[1] if best is not None else None


def _over(footprint: np.ndarray, node: SceneNode) -> bool:
    _, _, half = _frame(node)
    local = np.abs(to_local(footprint, node))[0]
    return bool(local[0] <= half[0] and local[1] <= half[1])
