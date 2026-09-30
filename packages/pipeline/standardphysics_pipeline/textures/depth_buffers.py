"""Per-camera depth buffers of the LiDAR points and faces, eroded a pixel so occluders only ever grow."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .camera import PhotoCamera
from .visibility_blocks import TriangleBlocks

NEAR_LIMIT = 0.15


DEPTH_BUFFER_DIVISOR = 8
MAX_DEPTH_BUFFER_SIDE = 512


class DepthPyramid:
    """A depth buffer's farthest recorded depth over aligned squares of 1, 2, 4 and more pixels.

    A cube whose nearest point lies beyond the farthest depth over every pixel
    it covers is hidden entirely. The pyramid answers that for a whole cube
    with four lookups, at the level where the cube's rectangle spans at most
    two squares each way.
    """

    def __init__(self, buffer: np.ndarray):
        self.levels = [buffer]
        while max(self.levels[-1].shape) > 1:
            level = self.levels[-1]
            padded = np.pad(level, ((0, level.shape[0] % 2), (0, level.shape[1] % 2)), constant_values=np.inf)
            self.levels.append(padded.reshape(padded.shape[0] // 2, 2, padded.shape[1] // 2, 2).max(axis=(1, 3)))

    def farthest(self, left: np.ndarray, right: np.ndarray, top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
        """The farthest depth over each inclusive pixel rectangle, clipped to the buffer beforehand."""
        span = np.maximum(right - left, bottom - top) + 1
        level = np.clip(np.ceil(np.log2(np.maximum(span, 1))).astype(np.int64), 0, len(self.levels) - 1)
        result = np.full(len(left), np.inf, dtype=np.float64)
        for k in np.unique(level):
            chosen = np.flatnonzero(level == k)
            grid = self.levels[k]
            first_row, last_row = np.minimum(top[chosen] >> k, grid.shape[0] - 1), np.minimum(bottom[chosen] >> k, grid.shape[0] - 1)
            first_column, last_column = np.minimum(left[chosen] >> k, grid.shape[1] - 1), np.minimum(right[chosen] >> k, grid.shape[1] - 1)
            result[chosen] = np.maximum.reduce([
                grid[first_row, first_column], grid[first_row, last_column], grid[last_row, first_column], grid[last_row, last_column],
            ])
        return result


@dataclass(frozen=True)
class SphereFootprints:
    """Where spheres land in a low-resolution camera: nearest depth and the pixel rectangle each covers."""

    usable: np.ndarray
    """Whether the sphere stays in front of the near plane, so its footprint can be trusted."""
    nearest: np.ndarray
    left: np.ndarray
    right: np.ndarray
    top: np.ndarray
    bottom: np.ndarray


def sphere_footprints(small: PhotoCamera, centres: np.ndarray, radii: np.ndarray, margin: int) -> SphereFootprints:
    """Each sphere's nearest depth, and the rectangle its bounding box projects to, grown by `margin` pixels and clipped.

    The box's eight corners bound the sphere's projection whenever all of them
    are in front of the camera; a sphere reaching the near plane is marked unusable.
    """
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float64)
    corners = centres[:, None, :] + signs[None] * radii[:, None, None]
    local = small.to_camera(corners.reshape(-1, 3).astype(np.float64)).reshape(-1, 8, 3)
    depth = local[..., 2]
    usable = np.asarray((depth > NEAR_LIMIT).all(axis=1))
    safe = np.where(depth > NEAR_LIMIT, depth, NEAR_LIMIT)
    u, v = small.fx * local[..., 0] / safe + small.cx, small.fy * local[..., 1] / safe + small.cy
    nearest = small.to_camera(centres.astype(np.float64))[:, 2] - radii
    return SphereFootprints(
        usable, nearest,
        np.clip(np.floor(u.min(axis=1)) - margin, 0, small.width - 1).astype(np.int64),
        np.clip(np.ceil(u.max(axis=1)) + margin, 0, small.width - 1).astype(np.int64),
        np.clip(np.floor(v.min(axis=1)) - margin, 0, small.height - 1).astype(np.int64),
        np.clip(np.ceil(v.max(axis=1)) + margin, 0, small.height - 1).astype(np.int64),
    )


def depth_buffer(camera: PhotoCamera, points: np.ndarray) -> np.ndarray:
    """Nearest depth per low-resolution pixel, eroded one pixel so occluders grow and holes close."""
    small = camera.resized(max(1, camera.width // DEPTH_BUFFER_DIVISOR), max(1, camera.height // DEPTH_BUFFER_DIVISOR))
    u, v, depth = small.project(points)
    columns, rows = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
    valid = (depth > NEAR_LIMIT) & (columns >= 0) & (columns < small.width) & (rows >= 0) & (rows < small.height)
    buffer = np.full(small.width * small.height, np.inf, dtype=np.float32)
    np.minimum.at(buffer, rows[valid] * small.width + columns[valid], depth[valid].astype(np.float32))
    return _erode(buffer.reshape(small.height, small.width))


DEPTH_TRIANGLE_CHUNK = 250_000
"""How many faces are transformed into a camera's frame at once.

The whole mesh at once is what set a ceiling on how long a walk could be: a
four million face capture needs about half a gigabyte of temporaries per
camera, and the bake refused rather than allocate it. The buffer keeps the
nearest of whatever it is shown, and taking a minimum does not care what order
it sees things in, so a chunked pass writes the same buffer the whole mesh
would have, face for face, while holding memory flat.
"""


DEPTH_EXTRAPOLATION = 1.07
"""How much nearer than its nearest corner a face may draw: the rasterizer lets barycentrics reach -0.03, which extrapolates inverse depth by up to six per cent."""
FIRST_ROUND_CUBES = 32


def occluder_depth_buffer(camera: PhotoCamera, triangles: np.ndarray, blocks: TriangleBlocks) -> np.ndarray:
    """`triangle_depth_buffer` of the cubes in frame, drawn nearest first, skipping cubes wholly behind what is already drawn.

    On a library floor a photo's frame holds dozens of layers of shelving and
    floor, and drawing every layer was the costliest step left in a build. A
    skipped cube could only have drawn depths beyond what the buffer already
    holds over its footprint, and the buffer keeps the nearest, so the result
    is the buffer drawing everything would have made.
    """
    scale = min(1.0 / DEPTH_BUFFER_DIVISOR, MAX_DEPTH_BUFFER_SIDE / max(camera.width, camera.height))
    small = camera.resized(max(1, round(camera.width * scale)), max(1, round(camera.height * scale)))
    buffer = np.full((small.height, small.width), np.inf, dtype=np.float32)
    cubes = blocks.cubes_seen_by(camera)
    footprints = sphere_footprints(small, blocks.centres[cubes], blocks.radii(cubes), margin=1)
    order = np.argsort(footprints.nearest, kind="stable")
    start, size = 0, FIRST_ROUND_CUBES
    while start < len(order):
        batch = order[start:start + size]
        if start:
            farthest = DepthPyramid(buffer).farthest(footprints.left[batch], footprints.right[batch], footprints.top[batch], footprints.bottom[batch])
            batch = batch[~(footprints.usable[batch] & (footprints.nearest[batch] / DEPTH_EXTRAPOLATION > farthest))]
        chosen = blocks.members(cubes[batch])
        for first in range(0, len(chosen), DEPTH_TRIANGLE_CHUNK):
            _draw_depth(buffer, small, triangles[chosen[first:first + DEPTH_TRIANGLE_CHUNK]])
        start, size = start + size, size * 2
    return _erode(buffer)


def triangle_depth_buffer(camera: PhotoCamera, triangles: np.ndarray) -> np.ndarray:
    """Conservative, perspective-correct nearest-face depth at bounded resolution.

    Point splats leave holes between LiDAR vertices, which lets a chair's
    photo leak onto the floor behind it. Faces cover those gaps. The buffer is
    deliberately low-resolution and eroded afterwards, favouring an unpainted
    texel over borrowed foreground colour.
    """
    scale = min(1.0 / DEPTH_BUFFER_DIVISOR, MAX_DEPTH_BUFFER_SIDE / max(camera.width, camera.height))
    small = camera.resized(max(1, round(camera.width * scale)), max(1, round(camera.height * scale)))
    buffer = np.full((small.height, small.width), np.inf, dtype=np.float32)
    for start in range(0, len(triangles), DEPTH_TRIANGLE_CHUNK):
        _draw_depth(buffer, small, triangles[start:start + DEPTH_TRIANGLE_CHUNK])
    return _erode(buffer)


def _draw_depth(buffer: np.ndarray, small: PhotoCamera, triangles: np.ndarray) -> None:
    """Rasterize these faces into the buffer, keeping whichever is nearest."""
    if not len(triangles):
        return
    local = triangles @ small.room_to_camera[:3, :3].T + small.room_to_camera[:3, 3]
    depth = local[..., 2]
    in_front = np.all(depth > NEAR_LIMIT, axis=1)
    local, depth = local[in_front], depth[in_front]
    if not len(local):
        return
    u = small.fx * local[..., 0] / depth + small.cx
    v = small.fy * local[..., 1] / depth + small.cy
    visible = (
        (u.max(axis=1) >= -1) & (u.min(axis=1) <= small.width)
        & (v.max(axis=1) >= -1) & (v.min(axis=1) <= small.height)
    )
    u, v, depth = u[visible], v[visible], depth[visible]
    few_pixels = _pixel_spans(buffer, u, v).max(axis=0) <= SMALL_TRIANGLE_SIDES[-1]
    _rasterize_small_triangles(buffer, u[few_pixels], v[few_pixels], depth[few_pixels])
    for corners_u, corners_v, corners_depth in zip(u[~few_pixels], v[~few_pixels], depth[~few_pixels], strict=True):
        _rasterize_depth_triangle(buffer, corners_u, corners_v, corners_depth)


SMALL_TRIANGLE_PIXELS = 8
"""Faces whose pixel box, margin included, is at most this wide and tall are drawn all at once.

A LiDAR face is a centimetre or two, and the buffer is a few hundred pixels
across, so nearly every face covers a pixel or two, which the one-pixel margin
drawn around every face makes a box of four or five. Drawing them one at a time
in Python took most of a photo bake: millions of faces for each of up to 192
photos. Drawn together they give the same buffer, pixel for pixel, because each
pixel is tested and filled exactly as the loop tests and fills it.
"""


def _pixel_box(buffer: np.ndarray, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, ...]:
    """Each face's pixel box, grown one pixel all round and clipped to the buffer, as the loop draws it."""
    left = np.maximum(0, np.floor(u.min(axis=1)).astype(np.int64) - 1)
    right = np.minimum(buffer.shape[1] - 1, np.ceil(u.max(axis=1)).astype(np.int64) + 1)
    top = np.maximum(0, np.floor(v.min(axis=1)).astype(np.int64) - 1)
    bottom = np.minimum(buffer.shape[0] - 1, np.ceil(v.max(axis=1)).astype(np.int64) + 1)
    return left, right, top, bottom


def _pixel_spans(buffer: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    left, right, top, bottom = _pixel_box(buffer, u, v)
    return np.stack([right - left + 1, bottom - top + 1])


SMALL_TRIANGLE_BATCH = 40_000
"""Faces drawn together per step, so the per-pixel arrays stay near a hundred megabytes."""


SMALL_TRIANGLE_SIDES = (3, 5, SMALL_TRIANGLE_PIXELS, 16, 32, 64)
"""The candidate squares faces are sorted into, so a face two pixels wide is not tested against sixty-four.

A thinned floor's faces are ten or twenty centimetres across and cover dozens
of pixels near the camera; drawn one by one in Python they were two thirds of
a depth buffer. Only faces wider than the largest square are still drawn alone.
"""
BATCH_CANDIDATES = 2_500_000
"""Candidate pixels tested in one step, so a batch of large faces holds as much as one of small faces."""


def _rasterize_small_triangles(buffer: np.ndarray, u: np.ndarray, v: np.ndarray, depth: np.ndarray) -> None:
    """`_rasterize_depth_triangle` for many small faces at once, a batch at a time, each size with its own square."""
    spans = _pixel_spans(buffer, u, v).max(axis=0)
    smaller = 0
    for side in SMALL_TRIANGLE_SIDES:
        faces = np.flatnonzero((spans > smaller) & (spans <= side))
        smaller = side
        batch = max(1, min(SMALL_TRIANGLE_BATCH, BATCH_CANDIDATES // max(side, 1) ** 2))
        for start in range(0, len(faces), batch):
            chosen = faces[start:start + batch]
            _rasterize_small_batch(buffer, u[chosen], v[chosen], depth[chosen], side)


def _rasterize_small_batch(buffer: np.ndarray, u: np.ndarray, v: np.ndarray, depth: np.ndarray, side: int = SMALL_TRIANGLE_PIXELS) -> None:
    """Every candidate pixel of every face tested and filled as the per-face loop would."""
    if not len(u):
        return
    left, right, top, bottom = _pixel_box(buffer, u, v)
    determinant = (v[:, 1] - v[:, 2]) * (u[:, 0] - u[:, 2]) + (u[:, 2] - u[:, 1]) * (v[:, 0] - v[:, 2])
    offsets = np.arange(side)
    columns = (left[:, None] + offsets[None, :])[:, None, :]
    rows = (top[:, None] + offsets[None, :])[:, :, None]
    wanted = (columns <= right[:, None, None]) & (rows <= bottom[:, None, None]) & (np.abs(determinant) >= 1e-9)[:, None, None]
    safe = np.where(np.abs(determinant) < 1e-9, 1.0, determinant)[:, None, None]
    du, dv = columns - u[:, 2, None, None], rows - v[:, 2, None, None]
    first = ((v[:, 1] - v[:, 2])[:, None, None] * du + (u[:, 2] - u[:, 1])[:, None, None] * dv) / safe
    second = ((v[:, 2] - v[:, 0])[:, None, None] * du + (u[:, 0] - u[:, 2])[:, None, None] * dv) / safe
    third = 1.0 - first - second
    inverse_depth = first / depth[:, 0, None, None] + second / depth[:, 1, None, None] + third / depth[:, 2, None, None]
    inside = wanted & (first >= -0.03) & (second >= -0.03) & (third >= -0.03) & (inverse_depth > 0)
    pixels = np.broadcast_to(rows * buffer.shape[1] + columns, inside.shape)[inside]
    np.minimum.at(buffer.reshape(-1), pixels, (1.0 / inverse_depth[inside]).astype(buffer.dtype))


def _rasterize_depth_triangle(buffer: np.ndarray, u: np.ndarray, v: np.ndarray, depth: np.ndarray) -> None:
    # Expand one pixel around the chart so tiny cracks and edge rounding cannot
    # expose the farther surface. Barycentric interpolation is performed over
    # inverse depth, which is perspective-correct for a projected triangle.
    left, right = max(0, int(np.floor(u.min())) - 1), min(buffer.shape[1] - 1, int(np.ceil(u.max())) + 1)
    top, bottom = max(0, int(np.floor(v.min())) - 1), min(buffer.shape[0] - 1, int(np.ceil(v.max())) + 1)
    if left > right or top > bottom:
        return
    determinant = (v[1] - v[2]) * (u[0] - u[2]) + (u[2] - u[1]) * (v[0] - v[2])
    if abs(determinant) < 1e-9:
        return
    columns, rows = np.meshgrid(np.arange(left, right + 1), np.arange(top, bottom + 1))
    first = ((v[1] - v[2]) * (columns - u[2]) + (u[2] - u[1]) * (rows - v[2])) / determinant
    second = ((v[2] - v[0]) * (columns - u[2]) + (u[0] - u[2]) * (rows - v[2])) / determinant
    third = 1.0 - first - second
    inside = (first >= -0.03) & (second >= -0.03) & (third >= -0.03)
    inverse_depth = first / depth[0] + second / depth[1] + third / depth[2]
    values = np.where(inside & (inverse_depth > 0), 1.0 / inverse_depth, np.inf)
    target = buffer[top:bottom + 1, left:right + 1]
    np.minimum(target, values, out=target)


def _erode(buffer: np.ndarray) -> np.ndarray:
    padded = np.pad(buffer, 1, constant_values=np.inf)
    height, width = buffer.shape
    neighbours = [padded[dy:dy + height, dx:dx + width] for dy in range(3) for dx in range(3)]
    return np.minimum.reduce(neighbours)
