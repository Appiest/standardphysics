"""Measured regions and the operators over them.

A region is a posed box the scan measured: an id, where it sits, and how far it
extends along its own three axes. Nothing else is asserted about it. Whatever a
scanner or a model called it lives on the node it came from and never reaches
this module, so every operator here returns the same value on a planet where
none of those words mean anything.

Directions are always passed in. `gap(a, b, along=up)` is how far `a` sits above
`b`, but only because the caller chose `up`; the module has no idea which way
that is except through `gravity`, which is read off the phone's own alignment
rather than assumed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product
from uuid import UUID

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, SurfaceText

from ..coords import capture_to_room
from ..footprints import Polygon, convex_hull, convex_intersection, gap_between, signed_area

SAMPLES_PER_AXIS = 8
"""Points per axis when measuring how much of one region lies in another.

A 512-point grid puts a fraction within about one part in eight along each axis,
finer than the scan's own boxes are drawn.
"""

TOUCHING = 0.02
"""Metres of separation below which two surfaces are said to meet: the width of
the error in a phone's box around a piece of furniture."""

ARKIT_DOWN = np.array([0.0, -1.0, 0.0])
"""Gravity in ARKit's world frame. ARKit aligns that frame to the accelerometer,
so this is the measured direction the phone fell, not a convention."""


@dataclass(frozen=True)
class Region:
    """A measured box with nothing said about what it is."""

    id: UUID
    pose: np.ndarray
    """Room-from-region transform, 4 by 4."""

    extents: np.ndarray
    """Full length along each of the region's own axes, in metres."""

    markings: tuple[SurfaceText, ...] = ()
    """Glyphs read off its surfaces, each with the frames it was read from."""

    @property
    def rotation(self) -> np.ndarray:
        return self.pose[:3, :3]

    @property
    def centre(self) -> np.ndarray:
        return self.pose[:3, 3]


@dataclass(frozen=True)
class Contact:
    """Whether two surfaces meet, how far apart they are, and over what area."""

    meets: bool
    separation: float
    """Metres between the surfaces, or minus how deep they interpenetrate."""

    area: float
    """Square metres of the two faces that lie against each other."""


def region_of(node: SceneNode) -> Region:
    pose = np.array(node.transform.m, dtype=np.float64).reshape(4, 4)
    extents = np.array(node.dimensions.as_tuple(), dtype=np.float64)
    return Region(id=node.id, pose=pose, extents=extents, markings=tuple(node.texts))


def regions(graph: SceneGraph) -> list[Region]:
    """Every measured region in the scan."""
    return [region_of(node) for node in graph.nodes]


def gravity(graph: SceneGraph) -> np.ndarray:
    """The measured direction of fall in the room frame, as a unit vector."""
    frame = graph.capture_to_room or capture_to_room(0.0)
    rotation = np.array(frame.m, dtype=np.float64).reshape(4, 4)[:3, :3]
    down = rotation @ ARKIT_DOWN
    return down / np.linalg.norm(down)


def corners(region: Region) -> np.ndarray:
    """The eight corners in the room frame."""
    half = region.extents / 2
    signs = np.array(list(product((-1.0, 1.0), repeat=3)))
    return (signs * half) @ region.rotation.T + region.centre


def hull(region: Region) -> np.ndarray:
    """The region's convex hull, which for a measured box is its corners."""
    return corners(region)


def bounds(region: Region) -> tuple[np.ndarray, np.ndarray]:
    """The lowest and highest room-frame coordinate on each axis."""
    points = corners(region)
    return points.min(axis=0), points.max(axis=0)


def centroid(region: Region) -> np.ndarray:
    return region.centre.copy()


def volume(region: Region) -> float:
    return float(np.prod(region.extents))


def area(region: Region) -> float:
    """Surface area, so a sheet with no thickness still has one."""
    x, y, z = region.extents
    return float(2 * (x * y + y * z + z * x))


def principal_axes(region: Region) -> list[tuple[np.ndarray, float]]:
    """Each direction the region extends along, with its length, longest first."""
    axes = [(region.rotation[:, index].copy(), float(region.extents[index])) for index in range(3)]
    return sorted(axes, key=lambda axis: axis[1], reverse=True)


def markings_on(region: Region) -> list[SurfaceText]:
    """What is written on the region: the glyphs as read, and where they were read."""
    return list(region.markings)


def extent_along(region: Region, direction: np.ndarray) -> tuple[float, float]:
    """The lowest and highest the region reaches along a direction."""
    projected = corners(region) @ _unit(direction)
    return float(projected.min()), float(projected.max())


def contains(region: Region, points: np.ndarray, margin: float = 1e-6) -> np.ndarray:
    """Which room-frame points fall inside the region, as a boolean per point."""
    local = (points - region.centre) @ region.rotation
    return np.all(np.abs(local) <= region.extents / 2 + margin, axis=1)


def overlap_fraction(a: Region, b: Region) -> float:
    """How much of `a` lies within `b`, from 0 to 1.

    A sheet with no thickness is measured over its area rather than its volume,
    so the fraction of a wall lying inside a box still means something.
    """
    return float(contains(b, _sample(a)).mean())


def gap(a: Region, b: Region, along: np.ndarray) -> float:
    """How far `a` begins beyond where `b` ends along a direction.

    Positive is clear air between them, zero is touching, negative is how far
    they overlap in that direction.
    """
    a_low, _ = extent_along(a, along)
    _, b_high = extent_along(b, along)
    return a_low - b_high


def footprint(region: Region, along: np.ndarray) -> Polygon:
    """The region's outline seen looking along a direction, anticlockwise."""
    u, v = _plane_basis(along)
    points = corners(region)
    return convex_hull([(float(p @ u), float(p @ v)) for p in points])


def footprint_overlap(a: Region, b: Region, along: np.ndarray) -> float:
    """How much of `a`'s outline along a direction falls inside `b`'s, from 0 to 1."""
    outline = footprint(a, along)
    own = abs(signed_area(outline)) if len(outline) >= 3 else 0.0
    if own == 0.0:
        return 0.0
    shared = convex_intersection(outline, footprint(b, along))
    return abs(signed_area(shared)) / own if len(shared) >= 3 else 0.0


def relative_offset(a: Region, b: Region) -> np.ndarray:
    """`a`'s pose in `b`'s frame: where it sits and how it is turned, seen from `b`."""
    return np.linalg.inv(b.pose) @ a.pose


def separation(a: Region, b: Region) -> tuple[float, np.ndarray]:
    """The widest gap between two regions over the axes that can separate boxes.

    For two boxes those are their six face normals and the nine crossings of
    their edges. The value is the true distance when the nearest features are
    faces, and a lower bound when they are an edge and a corner.
    """
    best, best_axis = -math.inf, np.zeros(3)
    for axis in _separating_axes(a, b):
        a_low, a_high = extent_along(a, axis)
        b_low, b_high = extent_along(b, axis)
        width = max(b_low - a_high, a_low - b_high)
        if width > best:
            best, best_axis = width, axis
    return float(best), best_axis


def adjacency(a: Region, b: Region, within: float = TOUCHING) -> Contact:
    """Whether the surfaces of two regions meet, and over how much area."""
    distance, axis = separation(a, b)
    if distance > within:
        return Contact(meets=False, separation=distance, area=0.0)
    shared = convex_intersection(footprint(a, axis), footprint(b, axis))
    touching = abs(signed_area(shared)) if len(shared) >= 3 else 0.0
    return Contact(meets=True, separation=distance, area=touching)


def free_space(
    start: Region,
    end: Region,
    among: list[Region],
    along: np.ndarray,
    height: float,
) -> float:
    """The widest body that can travel straight from one region to another.

    The body stands on whichever of the two reaches lower along `along`, is
    `height` tall, and moves in the plane across that direction. Anything in
    `among` that reaches into that band is in its way; the width is twice the
    nearest such thing's distance from the line walked. Nothing in the way is
    an unbounded width.
    """
    base = min(extent_along(start, along)[0], extent_along(end, along)[0])
    band = (base + TOUCHING, base + height)
    walked = _line_between(start, end, along)
    widths = [
        2 * gap_between(walked, footprint(region, along))
        for region in among
        if region.id not in (start.id, end.id) and _reaches_into(region, along, band)
    ]
    return min(widths, default=math.inf)


def _reaches_into(region: Region, along: np.ndarray, band: tuple[float, float]) -> bool:
    low, high = extent_along(region, along)
    return high > band[0] and low < band[1]


def _line_between(start: Region, end: Region, along: np.ndarray) -> Polygon:
    u, v = _plane_basis(along)
    return [(float(r.centre @ u), float(r.centre @ v)) for r in (start, end)]


def _sample(region: Region) -> np.ndarray:
    steps = (np.arange(SAMPLES_PER_AXIS) + 0.5) / SAMPLES_PER_AXIS - 0.5
    grid = np.array(list(product(steps, repeat=3)))
    return (grid * region.extents) @ region.rotation.T + region.centre


def _separating_axes(a: Region, b: Region) -> list[np.ndarray]:
    faces = [a.rotation[:, i] for i in range(3)] + [b.rotation[:, i] for i in range(3)]
    crossings = [np.cross(a.rotation[:, i], b.rotation[:, j]) for i in range(3) for j in range(3)]
    return [_unit(axis) for axis in faces + crossings if np.linalg.norm(axis) > 1e-9]


def _plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = _unit(normal)
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = _unit(np.cross(n, helper))
    return u, np.cross(n, u)


def _unit(vector: np.ndarray) -> np.ndarray:
    return np.asarray(vector, dtype=np.float64) / np.linalg.norm(vector)
