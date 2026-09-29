"""Grouping points and faces into cubes, so a photo only tests what could fall inside its frame."""

from __future__ import annotations

import numpy as np

from .camera import PhotoCamera

BLOCK_METRES = 1.0
"""The size of the cubes points are grouped into, so a photo only projects the points it could see."""


FRAME_MARGIN_PIXELS = 16.0
"""How far past the frame's edge a sphere still counts as in it, so rounding to a buffer's coarse pixels never drops a point."""


def spheres_in_frame(camera: PhotoCamera, centres: np.ndarray, radius: float | np.ndarray) -> np.ndarray:
    """Which spheres reach into the camera's frame, counting any the camera stands inside."""
    local = centres @ camera.room_to_camera[:3, :3].T + camera.room_to_camera[:3, 3]
    lens = np.asarray([camera.fx, camera.fy, camera.cx, camera.cy, camera.width, camera.height], dtype=np.float64)
    return _in_frustum(local, lens, radius)


def _in_frustum(local: np.ndarray, lens: np.ndarray, radius: float | np.ndarray) -> np.ndarray:
    """Whether spheres at camera-frame centres cross all four side planes of the view and are not wholly behind it.

    Each side of the frame is a plane through the camera; a sphere reaches the
    frame when its centre is no further than its radius outside every one of
    them. Comparing the projected centre with a projected radius instead
    misses nearby spheres toward the frame's corners, which project larger
    than their radius over their depth suggests.
    """
    fx, fy, cx, cy, width, height = np.moveaxis(np.atleast_2d(lens), -1, 0)
    x, y, z = local[:, 0], local[:, 1], local[:, 2]
    low_u, high_u = cx + FRAME_MARGIN_PIXELS, width - cx + FRAME_MARGIN_PIXELS
    low_v, high_v = cy + FRAME_MARGIN_PIXELS, height - cy + FRAME_MARGIN_PIXELS
    inside = z > -radius
    for along, focal, low, high in ((x, fx, low_u, high_u), (y, fy, low_v, high_v)):
        inside &= (focal * along + low * z) / np.hypot(focal, low) > -radius
        inside &= (-focal * along + high * z) / np.hypot(focal, high) > -radius
    return inside


class CameraArray:
    """Every camera's pose and lens as arrays, so all of them can be tested against one region at once."""

    def __init__(self, cameras: list[PhotoCamera]):
        matrices = np.asarray([camera.room_to_camera for camera in cameras], dtype=np.float64).reshape(-1, 4, 4)
        self.rotations, self.translations = matrices[:, :3, :3], matrices[:, :3, 3]
        self.lens = np.asarray([[c.fx, c.fy, c.cx, c.cy, c.width, c.height] for c in cameras], dtype=np.float64).reshape(-1, 6)

    def reaching(self, centre: np.ndarray, radius: float) -> np.ndarray:
        """Indices of the cameras whose frame a sphere reaches into, the same test `spheres_in_frame` makes."""
        local = self.rotations @ centre + self.translations
        return np.flatnonzero(_in_frustum(local, self.lens, radius))


class PointBlocks:
    """Points grouped into cubes, so each photo projects only the cubes inside its frame.

    A photo of one corner of a library floor sees a small share of it, so
    projecting every point through every photo spends nearly all its time on
    points that land outside the frame and are thrown away.
    """

    def __init__(self, points: np.ndarray):
        keys = np.floor(points / BLOCK_METRES).astype(np.int64).reshape(-1, 3)
        low = keys.min(axis=0) if len(keys) else np.zeros(3, dtype=np.int64)
        span = keys.max(axis=0) - low + 1 if len(keys) else np.ones(3, dtype=np.int64)
        shifted = keys - low
        flat = (shifted[:, 0] * span[1] + shifted[:, 1]) * span[2] + shifted[:, 2]
        self.order = np.argsort(flat, kind="stable").astype(np.int32 if len(flat) < 2**31 else np.int64)
        ordered = flat[self.order]
        edges = [np.flatnonzero(np.diff(ordered)) + 1, [len(flat)]] if len(flat) else []
        self.starts = np.concatenate([[0], *edges]).astype(np.int64)
        cubes = ordered[self.starts[:-1]]
        unique = np.stack([cubes // (span[1] * span[2]), cubes // span[2] % span[1], cubes % span[2]], axis=1) + low
        self.centres = (unique + 0.5) * BLOCK_METRES
        self.radius = BLOCK_METRES * np.sqrt(3) / 2

    def seen_by(self, camera: PhotoCamera) -> np.ndarray:
        """Indices of every point in a cube that reaches into the camera's frame."""
        return self.members(self.cubes_seen_by(camera))

    def cubes_seen_by(self, camera: PhotoCamera) -> np.ndarray:
        return np.flatnonzero(spheres_in_frame(camera, self.centres, self.radius))

    def members(self, cubes: np.ndarray) -> np.ndarray:
        """Indices of every point in these cubes."""
        first, count = self.starts[cubes], self.starts[cubes + 1] - self.starts[cubes]
        offsets = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
        return self.order[np.repeat(first, count) + offsets]

    def radii(self, cubes: np.ndarray) -> np.ndarray:
        return np.broadcast_to(self.radius, (len(self.centres),))[cubes]


class TriangleBlocks(PointBlocks):
    """Faces grouped by the cube their centre lies in, so each photo rasterizes only the cubes inside its frame.

    A face can reach outside the cube its centre falls in, so each cube's
    sphere is grown to hold every corner of every face assigned to it. A face
    outside every frame plane by more than the margin draws no pixel, so the
    buffer is the one the whole mesh would have drawn.
    """

    def __init__(self, triangles: np.ndarray):
        super().__init__(triangles.mean(axis=1))
        block_of = np.empty(len(triangles), dtype=np.int64)
        block_of[self.order] = np.repeat(np.arange(len(self.centres)), np.diff(self.starts))
        corners_reach = np.linalg.norm(triangles - self.centres[block_of][:, None, :], axis=2).max(axis=1)
        self.radius = np.zeros(len(self.centres))
        np.maximum.at(self.radius, block_of, corners_reach)
