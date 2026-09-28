"""Reading a small object's height off the mesh instead of off the photos that named it.

A carve keeps the points a detector's rectangle covered, so it is only as tall
as the rectangles were. A fire extinguisher cut off by the edge of every photo
that boxed it carves as its lower half: on a real café capture one run put its
top at 44.4 inches and another at 50.3, while the mesh shows the body standing
off the wall up to about 53. Which photos the detector boxed changes from run
to run, and an inch either side of 48 is the whole of ADA 2010 308.2.1.

The mesh does not change. So each small object's column, its own footprint
widened by a few centimetres, is walked up from the carve's top and down from
its underside a couple of centimetres at a time, for as long as the mesh keeps
filling it:

    a gap          three empty steps in a row is air, and the object ended below it
    a broader body more points round the footprint than inside it is a counter
                   top, a shelf or a neighbour, not more of this object
    a cap          a body still going forty centimetres on is part of something
                   larger, a pole or a partition, and that end stays as carved

The wall behind a mounted object and the floor under one are the room's own,
and the lid of a scanned counter is its own, so none of them are walked into;
a sign flat on the wall above an extinguisher reads as wall. Another carved
object's points are left to that object. The object's height is then read
between the same trimmed percentiles a carve uses, over the whole column the
walk reached, so a stray vertex cannot stretch it and the rectangles that
happened to be drawn this time do not decide it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from standardphysics_contracts import SceneGraph

from ..textures.camera import PhotoCamera
from .boxes import claimed_by_any
from .carve import TRIM_PERCENTILE, CarvedBox
from .detect import Detection
from .merge import DiscoveredObject
from .people import PersonVolume, mostly_people

SMALL = 0.6
"""Metres. An object whose footprint is no longer than this on any side is small enough to walk."""
MARGIN = 0.05
"""How far past the carve's footprint the object's own column reaches: the part of it the carve missed."""
AROUND = 0.15
"""How far past the footprint the mesh is read for something broader than the object."""
STEP = 0.02
GAP = 0.06
"""Metres of empty column that end the object. The mesh leaves a step empty now and then, never three."""
GROWTH = 0.4
"""Metres the walk may go past the carve in either direction."""


@dataclass(frozen=True)
class _Column:
    """The loose mesh near one object: heights inside its widened footprint, and round it."""

    own: np.ndarray
    around: np.ndarray
    points: np.ndarray
    """The points of `own`, kept to become the object's."""

    def reaches(self, start: float, direction: int) -> float:
        """How far the object's body continues from `start`, walking up (+1) or down (-1).

        A body still going at the cap is part of something larger, a pole or a
        partition or a kiosk, and the carve's own end is kept.
        """
        reached, empty = start, 0.0
        for offset in np.arange(0.0, GROWTH, STEP):
            low = start + offset if direction > 0 else start - offset - STEP
            inside = _count(self.own, low)
            if inside == 0:
                empty += STEP
                if empty >= GAP - STEP / 2:
                    return reached
                continue
            if _count(self.around, low) > inside:
                return reached
            reached, empty = (low + STEP if direction > 0 else low), 0.0
        return start


@dataclass(frozen=True)
class MeshViews:
    """The mesh before any person was taken out of it, and every photo's view of it."""

    points: np.ndarray
    views: list[tuple[PhotoCamera, list[Detection], np.ndarray | None]]

    def loose_near(self, objects: list[DiscoveredObject], graph: SceneGraph, people: Sequence[PersonVolume]) -> np.ndarray:
        """The mesh no scanned piece claims round the small objects, less what most photos saw as a person.

        Carving takes out whatever any one photo's person rectangle reached
        first, which clears a customer's shell but also whatever stood behind a
        customer ARKit had already filtered out, or under a rectangle drawn
        round a hand. On Share-Tea one run's rectangles took 71 of the 146
        points standing off the wall at the extinguisher and another's took 13,
        so its top moved with them. Most photos that see the extinguisher see no
        one there, and the vote keeps it; someone who stood in one place all
        session is still voted out, and their volume with them.
        """
        loose = self.points[~claimed_by_any(self.points, graph)]
        loose = loose[_near_small(objects, loose)]
        person = mostly_people(loose, graph, self.views)
        for volume in people:
            person |= volume.contains(loose)
        return loose[~person]


def measured_on_the_mesh(objects: list[DiscoveredObject], loose: np.ndarray) -> list[DiscoveredObject]:
    """Every small object with its height read off the mesh; larger ones exactly as carved.

    Each object is walked against the others as they were carved, so the
    answer for one never depends on the order the rest were measured in.
    """
    return [
        replace(object_, box=grown_to_the_mesh(object_.box, loose, [other.box for other in objects if other is not object_]))
        for object_ in objects
    ]


def grown_to_the_mesh(box: CarvedBox, loose: np.ndarray, others: Sequence[CarvedBox] = ()) -> CarvedBox:
    """The box stretched up and down to where the mesh says the object ends, never shrunk and never widened."""
    if max(box.dimensions[:2]) > SMALL or not len(loose):
        return box
    column = _column_of(box, loose, others)
    bottom, top = _vertical_span(box)
    reached_low, reached_high = column.reaches(bottom, -1), column.reaches(top, +1)
    heights = column.points[:, 2]
    walked = column.points[(heights >= reached_low) & (heights <= reached_high)]
    if not len(walked):
        return box
    low, high = (float(np.percentile(walked[:, 2], share)) for share in (TRIM_PERCENTILE, 100.0 - TRIM_PERCENTILE))
    low, high = min(low, bottom), max(high, top)
    points = np.concatenate([box.points, walked], axis=0)
    return CarvedBox(
        centre=(box.centre[0], box.centre[1], (low + high) / 2),
        dimensions=(box.dimensions[0], box.dimensions[1], high - low),
        yaw=box.yaw,
        points=points,
    )


def _column_of(box: CarvedBox, loose: np.ndarray, others: Sequence[CarvedBox]) -> _Column:
    bottom, top = _vertical_span(box)
    nearby = loose[(loose[:, 2] >= bottom - GROWTH - STEP) & (loose[:, 2] <= top + GROWTH + STEP)]
    local = _footprint_offsets(box, nearby)
    half = np.asarray(box.dimensions[:2]) / 2
    within = np.all(np.abs(local) <= half + AROUND, axis=1)
    nearby, local = nearby[within], local[within]
    free = ~_held_by_others(nearby, others) | _inside(box, nearby)
    own = free & np.all(np.abs(local) <= half + MARGIN, axis=1)
    return _Column(own=nearby[own, 2], around=nearby[free & ~own, 2], points=nearby[own])


def _near_small(objects: list[DiscoveredObject], points: np.ndarray) -> np.ndarray:
    """Points any small object's walk could read."""
    near = np.zeros(len(points), dtype=bool)
    for object_ in objects:
        box = object_.box
        if max(box.dimensions[:2]) > SMALL:
            continue
        bottom, top = _vertical_span(box)
        flat = np.all(np.abs(_footprint_offsets(box, points)) <= np.asarray(box.dimensions[:2]) / 2 + AROUND, axis=1)
        near |= flat & (points[:, 2] >= bottom - GROWTH - STEP) & (points[:, 2] <= top + GROWTH + STEP)
    return near


def _held_by_others(points: np.ndarray, others: Sequence[CarvedBox]) -> np.ndarray:
    held = np.zeros(len(points), dtype=bool)
    for other in others:
        held |= _inside(other, points)
    return held


def _inside(box: CarvedBox, points: np.ndarray) -> np.ndarray:
    bottom, top = _vertical_span(box)
    flat = np.all(np.abs(_footprint_offsets(box, points)) <= np.asarray(box.dimensions[:2]) / 2, axis=1)
    return flat & (points[:, 2] >= bottom) & (points[:, 2] <= top)


def _footprint_offsets(box: CarvedBox, points: np.ndarray) -> np.ndarray:
    cos_t, sin_t = np.cos(box.yaw), np.sin(box.yaw)
    offset = points[:, :2] - np.asarray(box.centre[:2])
    return np.stack([offset[:, 0] * cos_t + offset[:, 1] * sin_t, -offset[:, 0] * sin_t + offset[:, 1] * cos_t], axis=1)


def _vertical_span(box: CarvedBox) -> tuple[float, float]:
    return box.centre[2] - box.dimensions[2] / 2, box.centre[2] + box.dimensions[2] / 2


def _count(heights: np.ndarray, low: float) -> int:
    return int(np.count_nonzero((heights >= low) & (heights < low + STEP)))
