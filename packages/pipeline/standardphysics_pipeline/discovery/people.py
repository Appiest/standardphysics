"""Taking people out of the LiDAR before anything measures it.

A customer standing in an aisle while the owner scans becomes part of the
mesh, and from then on the aisle reads as blocked. The phone already asks
ARKit to keep people out of the reconstruction, which handles most of it, but
a person at the edge of the segmentation still leaves a shell behind.

So every frame is read for people, and the surface a person occupies is
removed. The points behind them stay: the detector's rectangle also covers the
wall metres back, and only the nearest depth band is the person.

Two things are never removed. Walls and floors keep their points even when
someone stood against them, because a person cannot delete a wall. And a point
no camera saw as a person is untouched.

Removing a person leaves a hole in the floor where they stood. That is the
right trade: a hole reads as unscanned, while a shell reads as an obstacle
that is not there.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneGraph

from ..textures.camera import PhotoCamera
from .boxes import structure_points
from .carve import NEAR_LIMIT, nearest_band, unoccluded
from .detect import Detection


@dataclass(frozen=True)
class PeopleRemoval:
    points: np.ndarray
    """The mesh with every person's surface taken out."""
    removed: int
    frames_with_people: int


def without_people(
    points: np.ndarray,
    graph: SceneGraph,
    views: list[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> PeopleRemoval:
    """The mesh minus the surfaces people occupied, with the room's structure kept."""
    is_person = np.zeros(len(points), dtype=bool)
    frames = 0
    for camera, detections, depth_buffer in views:
        people = [detection for detection in detections if detection.is_person]
        if not people:
            continue
        frames += 1
        for detection in people:
            is_person |= _person_surface(points, camera, detection, depth_buffer)
    is_person &= ~structure_points(points, graph)
    return PeopleRemoval(points=points[~is_person], removed=int(is_person.sum()), frames_with_people=frames)


def _person_surface(
    points: np.ndarray,
    camera: PhotoCamera,
    detection: Detection,
    depth_buffer: np.ndarray | None,
) -> np.ndarray:
    columns, rows, depth = camera.project(points)
    inside = (depth > NEAR_LIMIT) & detection.contains(columns, rows)
    if depth_buffer is not None:
        inside &= unoccluded(columns, rows, depth, camera, depth_buffer)
    hits = np.flatnonzero(inside)
    if not len(hits):
        return inside
    surface = np.zeros(len(points), dtype=bool)
    surface[hits[nearest_band(depth[hits])]] = True
    return surface
