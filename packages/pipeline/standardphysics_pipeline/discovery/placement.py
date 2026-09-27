"""Where a carved object really is: part of a scanned piece, on one, or on the floor.

Carving keeps what the camera saw, which for a counter is its lid and its
front edge: the scanned box below already owns the rest. Left alone, each of
those becomes its own "counter" floating at chest height, and a stool's
backrest becomes a second chair over the first.

**Part of a scanned piece.** A carved object mostly inside a scanned piece of
the same kind, give or take a hand's width round its footprint, is that piece.

**On a piece.** A small thing standing on a counter stands on the counter's
top, not a few inches into it where the carve reached down its front edge.

**On the floor.** A table or a counter stands on the floor. Carved from a view
that only saw its top, it is extended down when the mesh shows a solid body
below it, and dropped when nothing holds it up, since then it is a sign or a
shelf the detector called a counter.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from standardphysics_contracts import SceneGraph, bounds_the_room

from .boxes import RESTING_GAP, resting_parent, share_within, top_of
from .carve import MIN_EXTENT, CarvedBox
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
RING = 0.10
"""How far round a small object's footprint its supporting surface is read."""
SURFACE_POINTS = 10
SURFACE_SKIN = 0.015
SURFACE_SHARE = 0.3
"""Share of the points near the underside one height must hold to be a flat surface, not a wall or a leg."""
LEVEL_STEP = 0.01
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


def seated(box: CarvedBox, graph: SceneGraph, points: np.ndarray) -> CarvedBox:
    """The box set down on the surface it stands on, so its underside is that surface.

    The surface is measured round the object's footprint, where the counter
    top shows past it; the object itself hides what is directly under it.
    Where too little of it shows, the top of the scanned piece it rests on
    stands in. A carve reaches a few inches down a counter's front edge, and
    without this a card reader on a 34 inch counter would report the 31 inch
    underside of that edge.
    """
    if box.floor_clearance <= RESTING_GAP:
        return box
    surface = surface_around(box, points)
    if surface is None:
        parent = resting_parent(box, graph)
        surface = None if parent is None else top_of(graph.by_id(parent))
    top = box.centre[2] + box.dimensions[2] / 2
    return box.standing_on(surface) if surface is not None and top - surface >= MIN_EXTENT else box


def surface_around(box: CarvedBox, points: np.ndarray) -> float | None:
    """The height of the flat surface showing round the box's footprint near its underside, if there is one."""
    local = _in_box_frame(box, points)
    half = np.asarray(box.dimensions[:2]) / 2
    ring = np.all(np.abs(local) <= half + RING, axis=1) & ~np.all(np.abs(local) <= half, axis=1)
    heights = points[ring, 2]
    near = heights[np.abs(heights - box.floor_clearance) <= RESTING_GAP]
    if len(near) < SURFACE_POINTS:
        return None
    levels = np.arange(box.floor_clearance - RESTING_GAP, box.floor_clearance + RESTING_GAP, LEVEL_STEP)
    counts = np.asarray([np.count_nonzero(np.abs(near - level) <= SURFACE_SKIN) for level in levels])
    best = int(np.argmax(counts))
    return float(levels[best]) if counts[best] >= len(near) * SURFACE_SHARE else None


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
