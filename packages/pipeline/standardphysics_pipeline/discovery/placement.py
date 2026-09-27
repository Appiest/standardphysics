"""Where a carved object really is: part of a scanned piece, or a piece standing on the floor.

Carving keeps what the camera saw, which for a counter is its lid and its
front edge: the scanned box below already owns the rest. Left alone, each of
those becomes its own "counter" floating at chest height, and a stool's
backrest becomes a second chair over the first.

**Part of a scanned piece.** A carved object mostly inside a scanned piece of
the same kind, give or take a hand's width round its footprint, is that piece.

**On the floor.** A table or a counter stands on the floor. Carved from a view
that only saw its top, it is extended down when the mesh shows a solid body
below it, and dropped when nothing holds it up, since then it is a sign or a
shelf the detector called a counter.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from standardphysics_contracts import SceneGraph, bounds_the_room

from .boxes import RESTING_GAP, resting_parent, share_within
from .carve import CarvedBox
from .merge import DiscoveredObject
from .semantic_corrections import is_work_surface, same_furniture

REACH = 0.10
"""How far past a scanned piece's footprint a fragment of it may reach: a backrest's lean, a counter's lip."""
PART_OF = 0.5
"""Share of a carved object that must lie within a scanned piece of its own kind for it to be that piece."""
BODY_STEP = 0.05
BODY_POINTS = 3
SOLID_BENEATH = 0.6
"""Share of the height under a carved top in which the mesh shows the body continuing down."""
SPREAD = 0.5
"""How much of the box's width or depth the points at one height must span to be its body."""


def part_of_a_scanned_piece(object_: DiscoveredObject, graph: SceneGraph) -> bool:
    """Mostly within a scanned piece of the same kind, which already measures it."""
    return any(
        same_furniture(object_.name, node.label) and share_within(object_.box, node, REACH) >= PART_OF
        for node in graph.nodes
        if not bounds_the_room(node)
    )


def standing_on_the_floor(object_: DiscoveredObject, graph: SceneGraph, points: np.ndarray) -> DiscoveredObject | None:
    """A table or counter reaching the floor, or None when it is a fragment held up by nothing.

    Anything else is returned as it is.
    """
    box = object_.box
    if not is_work_surface(object_.name) or box.floor_clearance <= RESTING_GAP:
        return object_
    if resting_parent(box, graph) is not None or not solid_beneath(box, points):
        return None
    return replace(object_, box=box.standing_on(0.0))


def solid_beneath(box: CarvedBox, points: np.ndarray) -> bool:
    """Whether the mesh fills the column between the floor and the box's underside.

    Filled means that at most heights the points spread across at least half
    the box's width or depth, the way a counter's front or a cabinet's side
    does. Chair legs under the end of a table leave points at every height too,
    but only in a corner of it.
    """
    local = _in_box_frame(box, points)
    within = np.all(np.abs(local) <= np.asarray(box.dimensions[:2]) / 2, axis=1)
    local, heights = local[within], points[within, 2]
    levels = np.arange(BODY_STEP, box.floor_clearance - BODY_STEP / 2, BODY_STEP)
    if not len(levels):
        return True
    filled = [_spread_across(local[(heights >= level) & (heights < level + BODY_STEP)], box) for level in levels]
    return float(np.mean(filled)) >= SOLID_BENEATH


def _in_box_frame(box: CarvedBox, points: np.ndarray) -> np.ndarray:
    cos_t, sin_t = np.cos(box.yaw), np.sin(box.yaw)
    offset = points[:, :2] - np.asarray(box.centre[:2])
    return np.stack([offset[:, 0] * cos_t + offset[:, 1] * sin_t, -offset[:, 0] * sin_t + offset[:, 1] * cos_t], axis=1)


def _spread_across(local: np.ndarray, box: CarvedBox) -> bool:
    if len(local) < BODY_POINTS:
        return False
    spans = (local.max(axis=0) - local.min(axis=0)) / np.asarray(box.dimensions[:2])
    return bool(spans.max() >= SPREAD)
