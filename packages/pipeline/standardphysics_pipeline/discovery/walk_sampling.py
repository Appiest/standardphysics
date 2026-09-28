"""Which photos of a walk are worth asking the detector about, decided as they arrive.

The phone keeps a photo every half second whether or not it moved, and a person
standing still for four seconds hands over eight copies of one view. Reading
all eight spends the account's shared token budget for one answer.

A photo is kept when the phone has moved or turned enough since the last kept
one, or when it has held still long enough that the view is worth a fresh look.
The rule only ever looks back, so it gives the same answer whether it sees the
walk one photo at a time while the person is still walking or all at once
afterwards. That is what lets photos read during the walk be the same photos
discovery wants at the end.

On the Share-Tea walk (439 photos over 238 s) this keeps 355.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import PoseRecord

from ..textures.camera import PhotoCamera

MIN_MOVE_METRES = 0.10
MIN_TURN_DEGREES = 8.0
MIN_INTERVAL_SECONDS = 0.45
"""About two photos a second at most, however fast the phone swings. The phone
keeps one every 0.5 s; the slack keeps a timestamp a hair early from dropping one."""
MAX_STILL_SECONDS = 3.0
"""A view held this long is read again: people and things in it may have moved."""


@dataclass(frozen=True)
class Viewpoint:
    timestamp: float
    position: np.ndarray
    forward: np.ndarray
    """Unit viewing direction. Any rigid frame works; only distances and angles are compared."""


def viewpoint_of_pose(pose: PoseRecord) -> Viewpoint:
    """The phone's place and heading as it recorded them, in its own capture frame."""
    transform = pose.transform
    return Viewpoint(
        timestamp=pose.timestamp,
        position=np.array(transform[12:15], dtype=np.float64),
        forward=-np.array(transform[8:11], dtype=np.float64),
    )


def viewpoint_of_camera(camera: PhotoCamera) -> Viewpoint:
    return Viewpoint(timestamp=camera.timestamp, position=camera.position, forward=camera.forward)


def _turn_degrees(first: np.ndarray, second: np.ndarray) -> float:
    norms = float(np.linalg.norm(first) * np.linalg.norm(second))
    if norms == 0.0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(first @ second) / norms))))


class WalkSampler:
    """Decides photo by photo, in capture order, which ones to read."""

    def __init__(self) -> None:
        self._last_kept: Viewpoint | None = None

    def keep(self, view: Viewpoint) -> bool:
        if self._worth_reading(view):
            self._last_kept = view
            return True
        return False

    def _worth_reading(self, view: Viewpoint) -> bool:
        last = self._last_kept
        if last is None:
            return True
        waited = view.timestamp - last.timestamp
        if waited < MIN_INTERVAL_SECONDS:
            return False
        if waited >= MAX_STILL_SECONDS:
            return True
        moved = float(np.linalg.norm(view.position - last.position))
        return moved >= MIN_MOVE_METRES or _turn_degrees(view.forward, last.forward) >= MIN_TURN_DEGREES


def worth_reading(cameras: Iterable[PhotoCamera]) -> list[PhotoCamera]:
    """The cameras a sampler keeps when shown the whole walk in capture order."""
    sampler = WalkSampler()
    ordered = sorted(cameras, key=lambda camera: camera.timestamp)
    return [camera for camera in ordered if sampler.keep(viewpoint_of_camera(camera))]
