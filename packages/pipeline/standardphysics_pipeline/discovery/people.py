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

**A person is also found in the room, not only in the photo.** The mesh is
fused once for the whole walk, so someone who stood still long enough to be
scanned leaves one body that every photo looks at, including the photos where
the detector did not name them: seen from behind, cut by the frame edge, or
from the far side of the room. Those photos carve the body into whatever their
rectangle was named. On Share-Tea a customer at the window counter was named
in 21 photos and still became a "poster" at head height, a chair and a bar
stool, carved from photos such as frame-0053 and frame-0127 that looked at
where she stood without naming her.

So each person rectangle is also carved into the mesh like any other object.
Where at least two photos carve a body standing on the floor in the same place,
an upright cylinder there is that person's volume: every loose point inside it
is removed, and anything carved inside it is not an object. The body test is
what makes this safe. A walking customer ARKit already filtered out leaves no
body, and their rectangle carves whatever stood behind them instead; on
Share-Tea that was the kiosk, the till and the payment terminal. No such carve
there was at once 1.2 m tall, standing on the floor and narrower than a metre.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneGraph

from ..lidar import DEFAULT_VOXEL, voxel_downsample
from ..textures.camera import PhotoCamera
from ..textures.project import in_parallel
from .boxes import claimed_by_any, structure_points
from .carve import NEAR_LIMIT, CarvedBox, FrameView, carve, nearest_band, unoccluded
from .detect import Detection

SURE = 0.6
"""The detector's own confidence below which a person rectangle deletes nothing from the mesh."""
MIN_BODY_HEIGHT = 1.2
MAX_BODY_HEIGHT = 2.2
MAX_BODY_UNDERSIDE = 0.25
"""A body stands on the floor; a person-sized box floating higher is something on a counter."""
MAX_BODY_FOOTPRINT = 1.0
MIN_BODY_SIGHTINGS = 2
"""Photos that must carve a body in the same place before it is a person's volume."""
SAME_PERSON = 0.4
"""How far apart two carved bodies may stand, in plan, and still be one person."""
PERSON_RADIUS = 0.35
"""Half a shoulder width plus arms and a backpack, measured from the body's centre."""
HEAD_ROOM = 0.15
HALF_BODY_DEPTH = 0.15
"""How far behind the surface a photo carved the middle of a body stands: about half a torso."""


@dataclass(frozen=True)
class PersonVolume:
    """Where one person stood long enough to be scanned: an upright cylinder from the floor."""

    centre: tuple[float, float]
    top: float
    frame_ids: tuple[str, ...]
    radius: float = PERSON_RADIUS

    def contains(self, points: np.ndarray) -> np.ndarray:
        across = np.hypot(points[:, 0] - self.centre[0], points[:, 1] - self.centre[1])
        return (across <= self.radius) & (points[:, 2] <= self.top)

    def holds(self, box: CarvedBox) -> bool:
        """Whether a carved box stands where this person stood."""
        across = float(np.hypot(box.centre[0] - self.centre[0], box.centre[1] - self.centre[1]))
        return across <= self.radius and box.floor_clearance < self.top


@dataclass(frozen=True)
class PeopleRemoval:
    points: np.ndarray
    """The mesh with every person's surface taken out."""
    removed: int
    frames_with_people: int
    volumes: tuple[PersonVolume, ...] = ()
    """Where people stood, for rejecting anything later carved there."""


def person_points(
    points: np.ndarray,
    graph: SceneGraph,
    views: Sequence[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> tuple[np.ndarray, int]:
    """Which points are a person's surface, and in how many frames a person appeared.

    Only a person the detector is reasonably sure of deletes anything. On
    Share-Tea one run drew a "person" at 0.5 round the whole of a frame with
    nobody in it; its nearest surface was a wall-mounted extinguisher, whose
    upper half went, and with it the carves of the two photos that boxed that
    half and the extinguisher itself. Below 0.6 a run holds a dozen or two
    such rectangles, a few thousand of its seventy thousand person points.
    """
    is_person = np.zeros(len(points), dtype=bool)
    frames = 0
    for camera, detections, depth_buffer in views:
        people = [detection for detection in detections if detection.is_person and detection.confidence >= SURE]
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
    views: Sequence[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> PeopleRemoval:
    """The mesh minus the surfaces people occupied and the volumes they stood in, with the room's structure kept."""
    is_person, frames = person_points(points, graph, views)
    volumes = tuple(person_volumes(points, graph, views))
    is_person |= _inside_any(points, volumes) & ~structure_points(points, graph)
    return PeopleRemoval(
        points=points[~is_person], removed=int(is_person.sum()), frames_with_people=frames, volumes=volumes,
    )


def in_person_volumes(
    points: np.ndarray,
    graph: SceneGraph,
    views: Sequence[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> np.ndarray:
    """Points of a full-resolution mesh standing where a person stood, the room's structure kept.

    The volumes are found on the same voxel-sampled cloud discovery carves, so a
    dense display mesh loses exactly the people discovery cleared.
    """
    volumes = person_volumes(voxel_downsample(points, DEFAULT_VOXEL), graph, views)
    return _inside_any(points, volumes) & ~structure_points(points, graph)


def _inside_any(points: np.ndarray, volumes: tuple[PersonVolume, ...] | list[PersonVolume]) -> np.ndarray:
    inside = np.zeros(len(points), dtype=bool)
    for volume in volumes:
        inside |= volume.contains(points)
    return inside


def person_volumes(
    points: np.ndarray,
    graph: SceneGraph,
    views: Sequence[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> list[PersonVolume]:
    """Every place at least two photos carve a standing body, as a volume to clear."""
    loose = points[~structure_points(points, graph) & ~claimed_by_any(points, graph)]
    bodies = [sighting for sighting in _person_carves(loose, views) if is_a_body(sighting.box)]
    return [_volume(group) for group in _grouped_by_place(bodies) if _seen_enough(group)]


def is_a_body(box: CarvedBox) -> bool:
    """Tall as a person, standing on the floor, and no wider than one."""
    height = box.dimensions[2]
    return (
        MIN_BODY_HEIGHT <= height <= MAX_BODY_HEIGHT
        and box.floor_clearance <= MAX_BODY_UNDERSIDE
        and max(box.dimensions[:2]) <= MAX_BODY_FOOTPRINT
    )


@dataclass(frozen=True)
class _BodySighting:
    frame_id: str
    box: CarvedBox
    centre: np.ndarray
    """Where the body's middle stands in plan: behind the surface the photo saw, away from the camera."""


def _person_carves(
    loose: np.ndarray,
    views: Sequence[tuple[PhotoCamera, list[Detection], np.ndarray | None]],
) -> list[_BodySighting]:
    carved = []
    for camera, detections, depth_buffer in views:
        people = [detection for detection in detections if detection.is_person]
        if not people:
            continue
        view = FrameView.of(loose, camera, depth_buffer)
        carved += [_sighting(camera, box) for one in people if (box := carve(view, one)) is not None]
    return carved


def _sighting(camera: PhotoCamera, box: CarvedBox) -> _BodySighting:
    """A photo sees the near half of a body, so its middle is further along the view than the carved surface."""
    seen = np.asarray(box.centre[:2], dtype=np.float64)
    away = seen - np.asarray(camera.position[:2], dtype=np.float64)
    length = float(np.linalg.norm(away))
    centre = seen if length == 0 else seen + away / length * HALF_BODY_DEPTH
    return _BodySighting(camera.frame_id, box, centre)


def _grouped_by_place(bodies: list[_BodySighting]) -> list[list[_BodySighting]]:
    """Bodies joined to the first group whose median centre stands within reach, best-evidenced first."""
    groups: list[list[_BodySighting]] = []
    for body in sorted(bodies, key=lambda one: -len(one.box.points)):
        home = next((group for group in groups if np.linalg.norm(_centre_of(group) - body.centre) <= SAME_PERSON), None)
        if home is None:
            groups.append([body])
        else:
            home.append(body)
    return groups


def _centre_of(group: list[_BodySighting]) -> np.ndarray:
    return np.median(np.asarray([body.centre for body in group]), axis=0)


def _seen_enough(group: list[_BodySighting]) -> bool:
    return len({body.frame_id for body in group}) >= MIN_BODY_SIGHTINGS


def _volume(group: list[_BodySighting]) -> PersonVolume:
    centre = _centre_of(group)
    tops = [body.box.centre[2] + body.box.dimensions[2] / 2 for body in group]
    return PersonVolume(
        centre=(float(centre[0]), float(centre[1])),
        top=float(np.median(tops)) + HEAD_ROOM,
        frame_ids=tuple(sorted({body.frame_id for body in group})),
    )


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
