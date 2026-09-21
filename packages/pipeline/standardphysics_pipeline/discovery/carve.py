"""Turning a rectangle drawn on a photo into a measured box in the room.

A detector says "payment terminal, these pixels". That names the thing but
gives it no size, so the LiDAR supplies every dimension and the photo only
supplies the identity. Three filters stand between the rectangle and the box.

**Behind the camera, or outside the frame.** Dropped by projection.

**Hidden.** A rectangle drawn around a terminal also covers the wall metres
behind it. A depth buffer built from the LiDAR faces keeps only the points the
camera could actually see through those pixels.

**Behind the thing named.** Even the visible points inside the rectangle
include whatever lies past the object's silhouette. The nearest populated
depth band drops the far half of the room, and clustering the rest by space
separates the object from what remains: of those pieces, the one whose
outline matches the rectangle is what the detector was looking at.

What survives is fitted as a box that may turn about Z but never tips, because
a terminal on a counter stands upright and a tilted box would misreport both
its footprint and its height.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import Mat4, Vec3

from ..textures.camera import PhotoCamera
from .clusters import dominant_cluster, without_the_surface_beneath
from .detect import Detection

NEAR_LIMIT = 0.05
DEPTH_TOLERANCE = 0.08
"""How far behind the nearest visible surface a point may sit and still count as seen."""
DEPTH_BIN = 0.05
BAND_SHARE = 0.12
"""A depth bin holding at least this share of the peak bin belongs to the same surface."""
MIN_POINTS = 25
MIN_EXTENT = 0.03
SLIVER_EXTENT = 0.035
"""A fitted box whose thinnest side lands this close to the floor value is a
smear across one surface, not an object seen from enough angles to have depth."""
MAX_EXTENT = 4.0
TRIM_PERCENTILE = 2.0
"""Extents are read between the 2nd and 98th percentile, so one stray vertex cannot inflate a box."""


@dataclass(frozen=True)
class CarvedBox:
    centre: tuple[float, float, float]
    dimensions: tuple[float, float, float]
    yaw: float
    points: np.ndarray
    """The room-frame points the box was fitted to, kept for merging across frames."""

    @property
    def volume(self) -> float:
        return self.dimensions[0] * self.dimensions[1] * self.dimensions[2]

    @property
    def floor_clearance(self) -> float:
        return self.centre[2] - self.dimensions[2] / 2

    def as_vec3(self) -> Vec3:
        return Vec3(x=self.dimensions[0], y=self.dimensions[1], z=self.dimensions[2])

    def as_transform(self) -> Mat4:
        cos_t, sin_t = math.cos(self.yaw), math.sin(self.yaw)
        x, y, z = self.centre
        return Mat4(m=[
            cos_t, -sin_t, 0.0, x,
            sin_t, cos_t, 0.0, y,
            0.0, 0.0, 1.0, z,
            0.0, 0.0, 0.0, 1.0,
        ])


@dataclass(frozen=True)
class FrameView:
    """One camera's view of a fixed set of points, projected once.

    A frame holds a dozen detections and every one of them would otherwise
    reproject the whole cloud. The projection depends only on the camera, so it
    is done once and every rectangle reads the same arrays.
    """

    camera: PhotoCamera
    points: np.ndarray
    columns: np.ndarray
    rows: np.ndarray
    depth: np.ndarray
    visible: np.ndarray
    """In front of the camera and not hidden behind a nearer surface."""

    @classmethod
    def of(
        cls,
        points: np.ndarray,
        camera: PhotoCamera,
        depth_buffer: np.ndarray | None = None,
    ) -> FrameView:
        columns, rows, depth = camera.project(points)
        visible = depth > NEAR_LIMIT
        if depth_buffer is not None:
            visible &= unoccluded(columns, rows, depth, camera, depth_buffer)
        return cls(camera, points, columns, rows, depth, visible)

    def through(self, detection: Detection) -> np.ndarray:
        return self.visible & detection.contains(self.columns, self.rows)


def carve(view: FrameView, detection: Detection) -> CarvedBox | None:
    """The measured box for one detection, or nothing when too few points survive."""
    chosen = np.flatnonzero(view.through(detection))
    if len(chosen) < MIN_POINTS:
        return None
    chosen = chosen[nearest_band(view.depth[chosen])]
    if len(chosen) < MIN_POINTS:
        return None
    chosen = chosen[without_the_surface_beneath(view.points[chosen])]
    if len(chosen) < MIN_POINTS:
        return None
    chosen = chosen[dominant_cluster(view.points[chosen], view.columns[chosen], view.rows[chosen], detection)]
    if len(chosen) < MIN_POINTS:
        return None
    return fit_box(view.points[chosen])


def unoccluded(
    columns: np.ndarray,
    rows: np.ndarray,
    depth: np.ndarray,
    camera: PhotoCamera,
    depth_buffer: np.ndarray,
) -> np.ndarray:
    height, width = depth_buffer.shape
    buffer_columns = np.clip(np.rint(columns * width / camera.width).astype(np.int64), 0, width - 1)
    buffer_rows = np.clip(np.rint(rows * height / camera.height).astype(np.int64), 0, height - 1)
    nearest = depth_buffer[buffer_rows, buffer_columns]
    return ~np.isfinite(nearest) | (depth <= nearest + DEPTH_TOLERANCE)


def nearest_band(depth: np.ndarray) -> np.ndarray:
    """The closest run of well-populated depth bins: the surface the detector named."""
    low, high = float(depth.min()), float(depth.max())
    if high - low < DEPTH_BIN:
        return np.ones(len(depth), dtype=bool)
    edges = np.arange(low, high + DEPTH_BIN, DEPTH_BIN)
    counts, edges = np.histogram(depth, bins=edges)
    populated = counts >= max(1.0, counts.max() * BAND_SHARE)
    start = int(np.argmax(populated))
    stop = start
    while stop + 1 < len(populated) and populated[stop + 1]:
        stop += 1
    return (depth >= edges[start]) & (depth <= edges[stop + 1])


def fit_box(points: np.ndarray) -> CarvedBox | None:
    """An upright box around the points, turned about Z to the footprint's long axis."""
    yaw = _footprint_yaw(points[:, :2])
    cos_t, sin_t = math.cos(yaw), math.sin(yaw)
    along = points[:, 0] * cos_t + points[:, 1] * sin_t
    across = -points[:, 0] * sin_t + points[:, 1] * cos_t
    spans = [_span(values) for values in (along, across, points[:, 2])]
    dimensions = tuple(max(MIN_EXTENT, high - low) for low, high in spans)
    if any(extent > MAX_EXTENT for extent in dimensions) or min(dimensions) < SLIVER_EXTENT:
        return None
    local_centre = [(low + high) / 2 for low, high in spans]
    centre = (
        local_centre[0] * cos_t - local_centre[1] * sin_t,
        local_centre[0] * sin_t + local_centre[1] * cos_t,
        local_centre[2],
    )
    return CarvedBox(centre=centre, dimensions=dimensions, yaw=yaw, points=points)


def _span(values: np.ndarray) -> tuple[float, float]:
    return (
        float(np.percentile(values, TRIM_PERCENTILE)),
        float(np.percentile(values, 100.0 - TRIM_PERCENTILE)),
    )


def _footprint_yaw(footprint: np.ndarray) -> float:
    """The long axis of the footprint, folded into a quarter turn.

    A box and the same box turned 90 degrees describe the same shape, so the
    yaw is reported in [0, pi/2) and the extents follow it.
    """
    centred = footprint - footprint.mean(axis=0)
    covariance = centred.T @ centred
    if not np.all(np.isfinite(covariance)) or np.allclose(covariance, 0.0):
        return 0.0
    _, vectors = np.linalg.eigh(covariance)
    principal = vectors[:, -1]
    return math.atan2(float(principal[1]), float(principal[0])) % (math.pi / 2)
