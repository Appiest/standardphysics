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

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneGraph

from ..textures.camera import PhotoCamera
from ..textures.project import in_parallel
from .boxes import structure_points
from .carve import NEAR_LIMIT, nearest_band, unoccluded
from .detect import Detection


@dataclass(frozen=True)
class PeopleRemoval:
    points: np.ndarray
    """The mesh with every person's surface taken out."""
    removed: int
    frames_with_people: int


def person_points(
    points: np.ndarray,
    graph: SceneGraph,
    views: list[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> tuple[np.ndarray, int]:
    """Which points are a person's surface, and in how many frames a person appeared."""
    is_person = np.zeros(len(points), dtype=bool)
    frames = 0
    for camera, detections, depth_buffer in views:
        people = [detection for detection in detections if detection.is_person]
        if not people:
            continue
        frames += 1
        for detection in people:
            is_person |= _person_surface(points, camera, detection, depth_buffer)
    return is_person & ~structure_points(points, graph), frames


MIN_PERSON_SHARE = 0.5
"""The share of the photos that see a point which must see a person there before it is shown as one."""
MIN_PERSON_VIEWS = 2


def _visible(points: np.ndarray, camera: PhotoCamera, depth_buffer: np.ndarray | None) -> np.ndarray:
    columns, rows, depth = camera.project(points)
    inside = (
        (depth > NEAR_LIMIT) & (columns >= 0) & (columns <= camera.width - 1)
        & (rows >= 0) & (rows <= camera.height - 1)
    )
    if depth_buffer is not None:
        inside &= unoccluded(columns, rows, depth, camera, depth_buffer)
    return inside


def mostly_people(
    points: np.ndarray,
    graph: SceneGraph,
    views: Iterable[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
    visible_to: Callable[[PhotoCamera], np.ndarray] | None = None,
    depth_buffer_of: Callable[[PhotoCamera, np.ndarray], np.ndarray] | None = None,
) -> np.ndarray:
    """Points that were a person in most of the photos that saw them.

    `person_points` takes a point as a person if one photo put it in a person's
    outline, which suits discovery, where a leftover shell reads as an obstacle.
    For the picture of the room that rule deletes furniture: a table in front of
    a seated person and a television behind a passer-by are each the nearest
    surface in some person's outline once. A table is seen in plenty of photos
    with nobody over it, so a vote over every photo that saw the point keeps it,
    while someone who sat in one chair all session is still voted out.

    `visible_to` narrows each photo to the points it could frame, and
    `depth_buffer_of`, when given, builds the photo's depth buffer from those
    points in place of the one in its view. The photos vote side by side and
    their votes are added in photo order, so the result is the same as one
    photo at a time.
    """
    seen_count = np.zeros(len(points), dtype=np.int32)
    person_count = np.zeros(len(points), dtype=np.int32)

    def vote(view):
        camera, detections, depth_buffer = view
        indices = visible_to(camera) if visible_to is not None else slice(None)
        near = points[indices]
        if depth_buffer_of is not None:
            depth_buffer = depth_buffer_of(camera, near)
        return indices, _visible(near, camera, depth_buffer), _in_a_person(near, camera, detections, depth_buffer)

    for indices, seen, in_person in in_parallel(vote, list(views)):
        seen_count[indices] += seen
        person_count[indices] += in_person
    share = person_count / np.maximum(seen_count, 1)
    voted = (share >= MIN_PERSON_SHARE) & (person_count >= MIN_PERSON_VIEWS)
    return voted & ~structure_points(points, graph)


def _in_a_person(points: np.ndarray, camera: PhotoCamera, detections: list[Detection], depth_buffer) -> np.ndarray:
    inside = np.zeros(len(points), dtype=bool)
    for detection in detections:
        if detection.is_person:
            inside |= _person_surface(points, camera, detection, depth_buffer)
    return inside


def without_people(
    points: np.ndarray,
    graph: SceneGraph,
    views: list[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> PeopleRemoval:
    """The mesh minus the surfaces people occupied, with the room's structure kept."""
    is_person, frames = person_points(points, graph, views)
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
