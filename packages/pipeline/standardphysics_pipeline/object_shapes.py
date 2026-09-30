"""Each object's scanned surface, cut out of the room's LiDAR mesh by its measured box.

A box says where a thing stands and how big it is, and nothing about its shape.
Drawing every box as the template for its category made every table in a
library the same four-legged slab and a wooden bench a sofa with arms. The mesh
the phone captured has the shape: the triangles inside an object's box are that
object as the scanner saw it.

Three rules decide which triangles are an object's.

**Inside its box.** A triangle belongs to a box when its centre does, and its
corners are pulled onto the box, so nothing is drawn past what was measured.

**The smallest box that holds it.** A laptop's box sits inside the desk's, so
the triangles inside the laptop's box are the laptop's and not the desk's.

**Not the room, and not what it stands on.** Triangles lying in the plane of a
room-bounding sheet belong to that sheet. Level triangles at the foot of a box
are the surface it stands on, and stay free for whatever that surface is.

The chosen surface is drawn as the 5 cm blocks it passes through, with
one-block gaps closed. A raw scanned surface is ragged at every edge and full
of holes where the phone never looked, and at the size a floor plan is viewed
the blocks show the same outline as a solid thing.

An object the scanner barely saw keeps its drawn stand-in, because a handful
of triangles is noise rather than a shape.
"""

from __future__ import annotations

import math
import pathlib
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

from .lidar import LidarMeshError, MeshPart, into_room, streamed_parts, triangles_by_part

INSIDE_MARGIN = 0.02
"""How far past its box a triangle's centre may sit and still be the object's: the box misses the surface by about this."""
SHEET_SKIN = 0.04
"""Triangles this close to a room-bounding sheet's plane are the sheet."""
SUPPORT_SKIN = 0.03
"""Level triangles this close to the foot of a box are what it stands on."""
LEVEL = 0.9
"""A triangle whose normal is at least this close to vertical is level."""
MIN_TRIANGLES = 40
MIN_HEIGHT_SHARE = 0.5
"""A shape must reach over this share of its box's height, or it is a sliver of the object rather than the object."""
BLOCK = 0.05
"""The side of the blocks an object is built from. Coarse enough for a phone to draw a whole floor, fine enough to keep a chair's back apart from its seat."""
CELL = 1.0
"""Metres per side of the grid that finds a box's candidate triangles without testing every triangle in the room."""


@dataclass(frozen=True)
class ObjectShape:
    vertices: np.ndarray
    """Corners in the node's own frame, unscaled, so the node's transform places them."""
    faces: np.ndarray
    """Rows of three indices into `vertices`."""


def scanned_shapes_from(graph: SceneGraph, lidar_mesh: pathlib.Path) -> dict[str, ObjectShape]:
    """Shapes for the graph's objects from an uploaded LiDAR mesh, or none when it cannot be read."""
    if graph.capture_to_room is None:
        return {}
    try:
        faces = _faces_under_objects(graph, streamed_parts(lidar_mesh))
    except (LidarMeshError, OSError, UnicodeDecodeError):
        return {}
    return scanned_shapes(graph, faces)


def _faces_under_objects(graph: SceneGraph, parts: Iterator[MeshPart]) -> np.ndarray:
    """The room-frame faces inside some object's box, read one anchor at a time.

    A floor joined from several walks carries millions of faces, and holding
    them all at once is more memory than a small server has. Most of them are
    floor, wall and ceiling, so only the faces inside an object's box are kept.
    """
    boxes = _BoxIndex(_standing_objects(graph))
    kept = []
    for corners in triangles_by_part(parts):
        corners = into_room(corners, graph.capture_to_room)
        kept.append(corners[boxes.holds(corners.mean(axis=1))])
    return np.concatenate(kept) if kept else np.empty((0, 3, 3), dtype=np.float32)


class _BoxIndex:
    """The objects bucketed by the floor-plan cells their boxes cover, to ask quickly whether a point is in any of them."""

    def __init__(self, nodes: list[SceneNode]):
        self._nodes = nodes
        self._buckets: dict[tuple[int, int], list[int]] = {}
        for index, node in enumerate(nodes):
            low, high = np.floor(np.asarray(_plan_bounds(node)) / CELL).astype(int)
            for x in range(low[0], high[0] + 1):
                for y in range(low[1], high[1] + 1):
                    self._buckets.setdefault((x, y), []).append(index)

    def holds(self, points: np.ndarray) -> np.ndarray:
        inside = np.zeros(len(points), dtype=bool)
        cells = np.floor(points[:, :2] / CELL).astype(np.int64)
        for cell in np.unique(cells, axis=0):
            members = self._buckets.get((int(cell[0]), int(cell[1])))
            if members:
                here = np.flatnonzero(np.all(cells == cell, axis=1))
                inside[here] = self._in_any(points[here], members)
        return inside

    def _in_any(self, points: np.ndarray, members: list[int]) -> np.ndarray:
        inside = np.zeros(len(points), dtype=bool)
        for index in members:
            node = self._nodes[index]
            _, _, half = _frame(node)
            inside |= np.all(np.abs(_local(node, points)) <= half + INSIDE_MARGIN, axis=1)
        return inside


def scanned_shapes(graph: SceneGraph, faces: np.ndarray) -> dict[str, ObjectShape]:
    """Every object the mesh shows well enough, by node id."""
    if not len(faces):
        return {}
    centres = faces.mean(axis=1)
    grid = _Grid(centres)
    free = ~_on_room_sheets(graph, centres, grid)
    shapes: dict[str, ObjectShape] = {}
    for node in sorted(_standing_objects(graph), key=_volume):
        chosen = _inside(node, centres, grid.near(node), free)
        chosen = chosen[~_standing_surface(node, faces[chosen].astype(np.float64))]
        shape = _shape(node, faces[chosen].astype(np.float64))
        if shape is None:
            continue
        free[chosen] = False
        shapes[str(node.id)] = shape
    return shapes


class _Grid:
    """Triangle centres bucketed by floor-plan cell, so a box only tests the triangles near it."""

    def __init__(self, centres: np.ndarray):
        cells = np.floor(centres[:, :2] / CELL).astype(np.int64)
        order = np.lexsort((cells[:, 1], cells[:, 0]))
        keys, starts = np.unique(cells[order], axis=0, return_index=True)
        ends = np.append(starts[1:], len(order))
        self._buckets = {
            (int(x), int(y)): order[start:end] for (x, y), start, end in zip(keys, starts, ends, strict=True)
        }

    def near(self, node: SceneNode, margin: float = INSIDE_MARGIN) -> np.ndarray:
        low, high = _plan_bounds(node, margin)
        lows, highs = np.floor(low / CELL).astype(int), np.floor(high / CELL).astype(int)
        found = [
            self._buckets[(x, y)]
            for x in range(lows[0], highs[0] + 1)
            for y in range(lows[1], highs[1] + 1)
            if (x, y) in self._buckets
        ]
        return np.concatenate(found) if found else np.empty(0, dtype=np.int64)


def _standing_objects(graph: SceneGraph) -> list[SceneNode]:
    """What stands in the room with some extent in every direction.

    A thing mounted on a surface is drawn from its own mounting instead.
    """
    return [node for node in graph.contents() if node.attachment is None and min(node.dimensions.as_tuple()) > 0]


def _frame(node: SceneNode) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.asarray(node.dimensions.as_tuple(), dtype=np.float64) / 2
    return matrix[:3, :3], matrix[:3, 3], half


def _local(node: SceneNode, points: np.ndarray) -> np.ndarray:
    rotation, origin, _ = _frame(node)
    return (points - origin) @ rotation


def _plan_bounds(node: SceneNode, margin: float = INSIDE_MARGIN) -> tuple[np.ndarray, np.ndarray]:
    rotation, origin, half = _frame(node)
    reach = np.abs(rotation[:2, :]) @ (half + margin)
    return origin[:2] - reach, origin[:2] + reach


def _volume(node: SceneNode) -> float:
    return math.prod(node.dimensions.as_tuple())


def _on_room_sheets(graph: SceneGraph, centres: np.ndarray, grid: _Grid) -> np.ndarray:
    """Which triangles lie in the plane of something that bounds the room."""
    on_sheet = np.zeros(len(centres), dtype=bool)
    for sheet in (node for node in graph.nodes if bounds_the_room(node)):
        near = grid.near(sheet, SHEET_SKIN)
        _, _, half = _frame(sheet)
        local = np.abs(_local(sheet, centres[near]))
        thin = int(np.argmin(half))
        on_sheet[near[np.all(local <= half + SHEET_SKIN, axis=1) & (local[:, thin] <= SHEET_SKIN)]] = True
    return on_sheet


def _inside(node: SceneNode, centres: np.ndarray, candidates: np.ndarray, free: np.ndarray) -> np.ndarray:
    candidates = candidates[free[candidates]]
    if not len(candidates):
        return candidates
    _, _, half = _frame(node)
    local = np.abs(_local(node, centres[candidates]))
    return candidates[np.all(local <= half + INSIDE_MARGIN, axis=1)]


def _standing_surface(node: SceneNode, faces: np.ndarray) -> np.ndarray:
    """Level triangles at the foot of the box: the floor or tabletop it stands on."""
    if not len(faces):
        return np.zeros(0, dtype=bool)
    rotation, origin, half = _frame(node)
    foot = origin[2] - float(np.abs(rotation[2, :]) @ half)
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    level = np.abs(normals[:, 2]) >= LEVEL * np.maximum(lengths, 1e-12)
    low = np.all(faces[:, :, 2] <= foot + SUPPORT_SKIN, axis=1)
    return level & low


def _shape(node: SceneNode, faces: np.ndarray) -> ObjectShape | None:
    if len(faces) < MIN_TRIANGLES:
        return None
    _, _, half = _frame(node)
    corners = np.clip(_local(node, faces.reshape(-1, 3)), -half, half)
    if not _reaches_up(node, corners, half):
        return None
    occupied = _occupancy(np.concatenate([corners, corners.reshape(-1, 3, 3).mean(axis=1)]), half)
    return _surface_of(occupied, 2 * half / np.asarray(occupied.shape), half)


def _reaches_up(node: SceneNode, corners: np.ndarray, half: np.ndarray) -> bool:
    rotation, _, _ = _frame(node)
    heights = corners @ rotation[2, :]
    extent = float(np.abs(rotation[2, :]) @ half) * 2
    return extent <= 0 or float(heights.max() - heights.min()) >= MIN_HEIGHT_SHARE * extent


def _occupancy(points: np.ndarray, half: np.ndarray) -> np.ndarray:
    """Which blocks of the box the scanned surface passes through, with one-block gaps closed."""
    counts = np.maximum(1, np.ceil(2 * half / BLOCK).astype(int))
    cells = np.clip(((points + half) / (2 * half) * counts).astype(int), 0, counts - 1)
    occupied = np.zeros(counts, dtype=bool)
    occupied[cells[:, 0], cells[:, 1], cells[:, 2]] = True
    closed = ndimage.binary_closing(np.pad(occupied, 1), structure=np.ones((3, 3, 3), dtype=bool))
    return occupied | closed[1:-1, 1:-1, 1:-1]


def _surface_of(occupied: np.ndarray, size: np.ndarray, half: np.ndarray) -> ObjectShape | None:
    """The outside faces of the occupied blocks, with each flat run of faces merged into one rectangle."""
    corners: list[np.ndarray] = []
    for axis in range(3):
        for sign in (1, -1):
            corners.extend(_faces_towards(occupied, axis, sign))
    if not corners:
        return None
    quads = np.asarray(corners, dtype=np.float64) * size - half
    vertices = quads.reshape(-1, 3)
    first = np.arange(0, len(vertices), 4)
    faces = np.concatenate([np.stack([first, first + 1, first + 2], axis=1), np.stack([first, first + 2, first + 3], axis=1)])
    return ObjectShape(vertices=vertices, faces=faces)


def _faces_towards(occupied: np.ndarray, axis: int, sign: int) -> list[np.ndarray]:
    """Rectangles of block faces looking along `axis` in direction `sign`, wound to face outward."""
    beyond = np.roll(np.pad(occupied, 1), -sign, axis=axis)[1:-1, 1:-1, 1:-1]
    exposed = occupied & ~beyond
    across, along = (index for index in range(3) if index != axis)
    reversed_winding = (sign < 0) != (axis == 1)
    quads = []
    for layer in range(occupied.shape[axis]):
        plane = layer + (1 if sign > 0 else 0)
        for row, column, rows, columns in _rectangles(np.take(exposed, layer, axis=axis)):
            quad = np.zeros((4, 3))
            quad[:, axis] = plane
            quad[:, across] = [row, row + rows, row + rows, row]
            quad[:, along] = [column, column, column + columns, column + columns]
            quads.append(quad[::-1] if reversed_winding else quad)
    return quads


def _rectangles(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Greedy rectangles covering a boolean grid, as (row, column, rows, columns)."""
    left = mask.copy()
    found = []
    for row, column in zip(*np.nonzero(mask), strict=True):
        if not left[row, column]:
            continue
        columns = _run_length(left[row, column:])
        rows = 1 + _run_length(left[row + 1:, column:column + columns].all(axis=1))
        left[row:row + rows, column:column + columns] = False
        found.append((int(row), int(column), rows, columns))
    return found


def _run_length(values: np.ndarray) -> int:
    """How many leading values are true."""
    stops = np.flatnonzero(~values)
    return int(stops[0]) if len(stops) else len(values)
