"""Flat pieces of the measured world, found by their shape and nothing else.

A scanner returns boxes for the furniture categories it ships with, so a room is
only ever as rich as somebody else's list. The mesh has no such list in it: it is
a few hundred thousand triangles, and a surface is a run of them facing the same
way, at the same depth, that you can walk across without stepping off.

That last part is what makes this work. Sorting triangles into bins by facing and
depth fails whichever way the bins are drawn: round the numbers and a floor that
sags two centimetres splits into sheets, cluster them by gaps instead and every
surface in the room chains into one blob through the triangles between them. A
surface is only a surface if its pieces touch, so growing one outward from a seed
is the thing that has to happen, and nothing cheaper stands in for it.

Nothing here knows what a wall, a desk or a partition is. A plane comes out
because triangles agreed, and what it turns out to be is a later question.
"""

from __future__ import annotations

import pathlib
from collections import deque
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import Mat4

from ..lidar import room_faces

CELL_METRES = 0.08
"""The grain everything is worked at.

Triangles are pooled into cells first, so a scan of three hundred thousand faces
becomes a few tens of thousands of places and the growing is quick. Eight
centimetres keeps a desktop separate from the shelf above it.
"""

SAME_FACING = 0.94
"""How nearly two cells must face the same way to belong to one surface, about
twenty degrees."""

FLATNESS_METRES = 0.06
"""How far a cell may sit off the surface it is joining. Thinner than a desktop,
thicker than the noise on a phone mesh."""

SMALLEST_AREA = 0.15
"""Square metres below which a run of triangles is mesh noise rather than a thing."""


@dataclass(frozen=True)
class Plane:
    """One flat run of measured surface."""

    normal: np.ndarray
    centre: np.ndarray
    length: float
    """The longer of its two in-plane extents."""

    breadth: float
    """The shorter one."""

    area: float
    """How much measured surface it carries, not length times breadth."""

    lowest: float
    highest: float

    @property
    def upright(self) -> float:
        """How vertical the surface is, from 0 lying flat to 1 standing on edge."""
        return float(1.0 - abs(self.normal[2]))

    @property
    def height(self) -> float:
        return self.highest - self.lowest


@dataclass(frozen=True)
class _Cells:
    """The scan pooled into places, each with a facing and a weight."""

    at: np.ndarray
    middle: np.ndarray
    facing: np.ndarray
    area: np.ndarray

    def __len__(self) -> int:
        return len(self.area)


def planes_of(mesh: pathlib.Path, capture_to_room: Mat4) -> list[Plane]:
    """Every flat run in the scan, largest first."""
    faces = room_faces(mesh, capture_to_room)
    if not len(faces):
        return []
    cells = _pool(*_faces(faces))
    return sorted(_regions(cells), key=lambda plane: -plane.area)


def _faces(faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A normal, a middle and an area for every triangle."""
    a, b, c = faces[:, 0], faces[:, 1], faces[:, 2]
    cross = np.cross(b - a, c - a)
    lengths = np.linalg.norm(cross, axis=1)
    keep = lengths > 1e-9
    normals = cross[keep] / lengths[keep][:, None]
    return _one_way(normals), ((a + b + c) / 3.0)[keep], lengths[keep] / 2.0


def _one_way(normals: np.ndarray) -> np.ndarray:
    """A plane faces both ways, so pick one and let both sides agree."""
    flip = (normals[:, 2] < 0) | ((normals[:, 2] == 0) & (normals[:, 0] < 0))
    return np.where(flip[:, None], -normals, normals)


def _pool(normals: np.ndarray, centres: np.ndarray, areas: np.ndarray) -> _Cells:
    """Every triangle dropped into a cell, and each cell given one facing."""
    at = np.floor(centres / CELL_METRES).astype(np.int64)
    places, index = np.unique(at, axis=0, return_inverse=True)
    weight = np.zeros(len(places))
    np.add.at(weight, index, areas)
    facing = np.zeros((len(places), 3))
    np.add.at(facing, index, normals * areas[:, None])
    middle = np.zeros((len(places), 3))
    np.add.at(middle, index, centres * areas[:, None])
    lengths = np.linalg.norm(facing, axis=1)
    safe = np.where(lengths < 1e-12, 1.0, lengths)
    return _Cells(
        at=places,
        middle=middle / np.where(weight[:, None] == 0, 1.0, weight[:, None]),
        facing=facing / safe[:, None],
        area=weight,
    )


def _regions(cells: _Cells) -> list[Plane]:
    """Grow a surface out of every cell that has not joined one yet."""
    home = {tuple(place): index for index, place in enumerate(cells.at)}
    taken = np.zeros(len(cells), dtype=bool)
    found: list[Plane] = []
    for seed in np.argsort(-cells.area):
        if taken[seed]:
            continue
        member = _reach(seed, cells, home, taken)
        plane = _measure(cells, member)
        if plane is not None:
            found.append(plane)
    return found


def _reach(seed: int, cells: _Cells, home: dict, taken: np.ndarray) -> list[int]:
    """Everything reachable from the seed without stepping off the surface."""
    normal = cells.facing[seed]
    anchor = cells.middle[seed]
    member = [seed]
    taken[seed] = True
    queue = deque([seed])
    while queue:
        for near in _neighbours(cells.at[queue.popleft()], home):
            if taken[near] or not _joins(cells, near, normal, anchor):
                continue
            taken[near] = True
            member.append(near)
            queue.append(near)
    return member


def _joins(cells: _Cells, index: int, normal: np.ndarray, anchor: np.ndarray) -> bool:
    facing = abs(float(np.dot(cells.facing[index], normal)))
    off = abs(float(np.dot(cells.middle[index] - anchor, normal)))
    return facing >= SAME_FACING and off <= FLATNESS_METRES


def _neighbours(place: np.ndarray, home: dict):
    x, y, z = int(place[0]), int(place[1]), int(place[2])
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                found = home.get((x + dx, y + dy, z + dz))
                if found is not None:
                    yield found


def _measure(cells: _Cells, member: list[int]) -> Plane | None:
    areas = cells.area[member]
    total = float(areas.sum())
    if total < SMALLEST_AREA:
        return None
    middles = cells.middle[member]
    normal = _average(cells.facing[member], areas)
    across, up = _in_plane_axes(normal)
    spans = sorted((float(np.ptp(middles @ across)), float(np.ptp(middles @ up))))
    return Plane(
        normal=normal,
        centre=middles.mean(axis=0),
        length=spans[1],
        breadth=spans[0],
        area=total,
        lowest=float(middles[:, 2].min()),
        highest=float(middles[:, 2].max()),
    )


def _average(normals: np.ndarray, areas: np.ndarray) -> np.ndarray:
    weighted = (normals * areas[:, None]).sum(axis=0)
    length = float(np.linalg.norm(weighted))
    return weighted / length if length else np.array([0.0, 0.0, 1.0])


def _in_plane_axes(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two directions lying in the plane, so its extent can be measured."""
    seed = np.array([0.0, 0.0, 1.0]) if abs(normal[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    across = np.cross(normal, seed)
    across = across / np.linalg.norm(across)
    return across, np.cross(normal, across)
