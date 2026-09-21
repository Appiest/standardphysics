"""The measured faces of a scanned region, cut into patches of a known size.

Counting what is on a surface only works if the surface has a size. A model can
say how many things it sees in a picture and cannot say how much of the room that
picture covers, so the picture has to be cut to a rectangle whose width and height
were measured rather than guessed. Everything here is that rectangle.

Nothing in this file knows what a shelf is. A region has faces because it has
extent, and a face carries patches because it has area.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneNode

PATCH_METRES = 0.6
"""How wide and tall a patch is.

Small enough that counting it is a bounded job, which is what
`scripts/book_count_spike` settled: a whole frame reads two to one across
repeats, and a tight crop reads within a few percent.
"""

MINIMUM_FACE_METRES = 0.3
"""A face smaller than this holds nothing worth crossing a room to photograph."""


@dataclass(frozen=True)
class Face:
    """One side of a region, and how big it actually is."""

    node_id: str
    label: str
    side: int
    """Which way it faces along the region's depth axis, +1 or -1."""

    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def name(self) -> str:
        """Which side, without pretending to know which one a person faces.

        The scanner picks a region's axes for its own reasons, so the side that
        happens to be +1 is not the front of anything.
        """
        return f"{self.label} side {1 if self.side > 0 else 2}"


@dataclass(frozen=True)
class Patch:
    """A rectangle on a face, with its corners in room coordinates."""

    face: Face
    corners: np.ndarray
    """Four room-frame points, wound so the normal points out of the region.

    The two sides of a region are the same rectangle at different depths, so only
    the winding tells them apart. Wind both the same way and a camera behind a
    unit looks like a camera in front of it.
    """

    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def normal(self) -> np.ndarray:
        """Which way the patch looks, so a camera behind it can be dropped."""
        first, second, last = self.corners[0], self.corners[1], self.corners[3]
        direction = np.cross(second - first, last - first)
        norm = float(np.linalg.norm(direction))
        return direction / norm if norm else direction


def faces_of(node: SceneNode) -> list[Face]:
    """Both broad sides of a region.

    A unit against a wall has one side anybody can see, and the other is reported
    as unseen rather than assumed empty. That is what the coverage figure is for.
    """
    width, _, height = node.dimensions.as_tuple()
    if width < MINIMUM_FACE_METRES or height < MINIMUM_FACE_METRES:
        return []
    return [
        Face(node_id=str(node.id), label=node.label, side=side, width=width, height=height)
        for side in (1, -1)
    ]


def patches_on(node: SceneNode, face: Face, size: float = PATCH_METRES) -> list[Patch]:
    """The face tiled into patches, dropping any remainder too small to read."""
    transform = np.array(node.transform.m, dtype=np.float64).reshape(4, 4)
    depth = node.dimensions.y
    return [
        _patch(face, transform, depth, across, up, size)
        for across in _steps(face.width, size)
        for up in _steps(face.height, size)
    ]


def _steps(span: float, size: float) -> list[float]:
    """Where each patch starts, measured from the middle of the face."""
    count = max(1, int(span // size))
    return [-(count * size) / 2 + index * size for index in range(count)]


def _patch(
    face: Face, transform: np.ndarray, depth: float, across: float, up: float, size: float
) -> Patch:
    offset = face.side * depth / 2
    left, right, bottom, top = across, across + size, up, up + size
    corners = [(left, bottom), (left, top), (right, top), (right, bottom)]
    if face.side < 0:
        corners.reverse()
    local = np.array([[x, offset, z] for x, z in corners])
    homogeneous = np.hstack([local, np.ones((4, 1))])
    return Patch(face=face, corners=(homogeneous @ transform.T)[:, :3], width=size, height=size)
