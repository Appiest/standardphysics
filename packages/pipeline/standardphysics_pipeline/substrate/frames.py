"""Which photographs saw a region, and the pixels they saw it in.

A photo saw a region when the region's centre lands inside the picture in front
of the lens. Nothing checks whether something else stood between them, so a
frame listed here faced the region rather than proving it was unobstructed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..textures.camera import PhotoCamera
from .regions import Region, corners


@dataclass(frozen=True)
class View:
    """Where a region fell in one photograph."""

    frame_id: str
    box: tuple[float, float, float, float]
    """Left, top, right and bottom in pixels, clipped to the picture."""

    depth: float
    """Metres from the lens to the region's centre."""


def frames_seeing(region: Region, cameras: list[PhotoCamera]) -> list[str]:
    """The frames the region's centre was photographed in, in capture order."""
    return [view.frame_id for view in (image_of(region, camera) for camera in cameras) if view]


def image_of(region: Region, camera: PhotoCamera) -> View | None:
    """Where the region sits in one frame, or nothing if the frame did not face it."""
    columns, rows, depths = camera.project(region.centre[np.newaxis, :])
    if depths[0] <= 0 or not _inside(camera, columns[0], rows[0]):
        return None
    return View(frame_id=camera.frame_id, box=_box(region, camera), depth=float(depths[0]))


def _inside(camera: PhotoCamera, column: float, row: float) -> bool:
    return 0 <= column < camera.width and 0 <= row < camera.height


def _box(region: Region, camera: PhotoCamera) -> tuple[float, float, float, float]:
    columns, rows, depths = camera.project(corners(region))
    ahead = depths > 0
    columns, rows = np.clip(columns[ahead], 0, camera.width), np.clip(rows[ahead], 0, camera.height)
    return float(columns.min()), float(rows.min()), float(columns.max()), float(rows.max())
