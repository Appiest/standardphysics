"""Reading a work surface's height off the mesh instead of off RoomPlan's box.

RoomPlan boxes a counter or a table and puts its lid wherever the fitted box
ends. On a real café capture the tops the mesh saw well missed it by about an
inch: a bar RoomPlan put at 44.2 inches is 43.1 on the mesh, a service
counter it put at 35.7 is 34.3. An inch is the whole margin of ADA 2010
904.4.1, so a counter height read off the box is a guess.

The surface gives itself away by covering the footprint. A top is the highest
height at which the mesh reaches across most of the piece's outline; the
register on a counter or the backrest of a stool covers a corner of it, not the
whole. Only broad pieces low enough to be looked down on are read this way: a
seat is too small to tell its seat from its back, and a bookcase's top is above
the phone and never seen.
"""

from __future__ import annotations

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, Vec3, bounds_the_room

from .boxes import to_local
from .carve import CarvedBox

BROAD_TOP = 0.3
"""Square metres of footprint below which a piece is a seat or a stool, not a work surface."""
TALLEST_WORKTOP = 1.3
"""Metres. Above this the top is over the phone's head, so the mesh never saw it from above."""
SEARCH = 0.3
"""Metres either side of RoomPlan's top that the scanned surface may lie."""
LEVEL_STEP = 0.01
SKIN = 0.015
"""Points within this of a height are that height's surface."""
CELL = 0.10
"""The footprint is read in ten centimetre cells, so a dense corner counts once."""
WELL_SEEN = 0.4
"""Share of the footprint the best-covered height must reach before the mesh overrules the box."""
NEAR_PEAK = 0.8
"""A height covering this share of the best one's cover is still the same surface; the highest wins."""
UPRIGHT = 0.99
ABOVE_THE_FLOOR = 0.15
"""Metres. Lower than this, what spreads across a carve standing on the floor is the floor itself."""


def carries_a_worktop(node: SceneNode) -> bool:
    """Broad, low enough to be seen from above, and standing square rather than tipped."""
    if bounds_the_room(node) or node.transform.m[10] < UPRIGHT:
        return False
    size = node.dimensions
    return size.x * size.y >= BROAD_TOP and size.z <= TALLEST_WORKTOP


def measured_top(node: SceneNode, points: np.ndarray) -> float | None:
    """The height of the scanned surface across the node's footprint, or None when too little of it was seen."""
    local = to_local(points, node)
    half = np.asarray(node.dimensions.as_tuple(), dtype=np.float64) / 2
    top = node.transform.position.z + half[2]
    heights = points[:, 2]
    near = np.all(np.abs(local[:, :2]) <= half[:2], axis=1) & (np.abs(heights - top) <= SEARCH)
    cells = _cell_ids(local[near, :2], half)
    return _highest_broad_level(heights[near], cells, _cell_count(half), (top - SEARCH, top + SEARCH))


def carved_top(box: CarvedBox, points: np.ndarray) -> float | None:
    """The highest surface the mesh spreads across a carved box's footprint, anywhere in its height.

    A carve of a counter reaches as high as whatever stands on it, so its own
    top says nothing about the counter's; the surface does.
    """
    cos_t, sin_t = np.cos(box.yaw), np.sin(box.yaw)
    offset = points[:, :2] - np.asarray(box.centre[:2])
    local = np.stack([offset[:, 0] * cos_t + offset[:, 1] * sin_t, -offset[:, 0] * sin_t + offset[:, 1] * cos_t], axis=1)
    half = np.asarray(box.dimensions[:2], dtype=np.float64) / 2
    bottom, top = max(box.floor_clearance, ABOVE_THE_FLOOR), box.centre[2] + box.dimensions[2] / 2
    heights = points[:, 2]
    near = np.all(np.abs(local) <= half, axis=1) & (heights >= bottom) & (heights <= top)
    return _highest_broad_level(heights[near], _cell_ids(local[near], half), _cell_count(half), (bottom, top))


def with_measured_top(node: SceneNode, surface: float) -> SceneNode:
    """The same piece standing where it stood, its lid moved to the scanned surface."""
    bottom = node.transform.position.z - node.dimensions.z / 2
    height = surface - bottom
    matrix = list(node.transform.m)
    matrix[11] = bottom + height / 2
    return node.model_copy(update={
        "dimensions": Vec3(x=node.dimensions.x, y=node.dimensions.y, z=height),
        "transform": node.transform.model_copy(update={"m": matrix}),
    })


def measure_worktops(graph: SceneGraph, points: np.ndarray) -> list[SceneNode]:
    """Every work surface whose scanned top differs from its box, refitted; untouched pieces are left out."""
    refitted = []
    for node in graph.nodes:
        if not carries_a_worktop(node):
            continue
        surface = measured_top(node, points)
        bottom = node.transform.position.z - node.dimensions.z / 2
        if surface is not None and surface > bottom and not np.isclose(surface, bottom + node.dimensions.z):
            refitted.append(with_measured_top(node, surface))
    return refitted


def _cell_ids(footprint: np.ndarray, half: np.ndarray) -> np.ndarray:
    columns = np.floor((footprint[:, 0] + half[0]) / CELL).astype(np.int64)
    rows = np.floor((footprint[:, 1] + half[1]) / CELL).astype(np.int64)
    return columns * (int(np.ceil(2 * half[1] / CELL)) + 1) + rows


def _cell_count(half: np.ndarray) -> int:
    return max(1, int(np.ceil(2 * half[0] / CELL)) * int(np.ceil(2 * half[1] / CELL)))


def _highest_broad_level(
    heights: np.ndarray, cells: np.ndarray, total: int, span: tuple[float, float],
) -> float | None:
    levels = np.arange(span[0], span[1] + LEVEL_STEP / 2, LEVEL_STEP)
    cover = np.asarray([
        len(np.unique(cells[np.abs(heights - level) <= SKIN])) / total for level in levels
    ])
    if not len(cover) or cover.max() < WELL_SEEN:
        return None
    highest = float(levels[cover >= cover.max() * NEAR_PEAK].max())
    return float(np.median(heights[np.abs(heights - highest) <= SKIN]))
