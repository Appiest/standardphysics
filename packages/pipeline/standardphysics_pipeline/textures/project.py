"""Photo color for every texel of the room's atlases.

A texel is a point on a model surface. A photo may color it only when the point
faces the camera, sits inside the frame, and is the nearest thing along that ray
according to both the clean model and the captured LiDAR. When the two disagree
about what is there, the texel stays neutral rather than borrowing a nearby
object's color.

Depth comes from conservative triangle rasterization into small per-camera
buffers. Every buffer is eroded by one pixel, which makes occluders slightly
larger: a chair edge can then only fail to texture the wall behind it, never
paint onto it.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .camera import PhotoCamera
from .stages import advanced

NEAR_LIMIT = 0.15
OCCLUDER_TOLERANCE = 0.025
"""How far in front of a surface the scan must sit before something is standing on it.

Just above the LiDAR's own noise, and well under the thickness of the flattest
thing anyone leaves on a desk."""
MIN_FACING = 0.3
DEPTH_BUFFER_DIVISOR = 8
MAX_DEPTH_BUFFER_SIDE = 512
BORDER_FALLOFF_PIXELS = 48.0
TOP_VIEWS = 3
OUTLIER_DISTANCE = 0.12
KEPT_WEIGHT_SHARE = 0.5
EXPOSURE_ITERATIONS = 8
MAX_EXPOSURE_POINTS = 20_000
MAX_LOG_GAIN = 0.7
GUTTER_PASSES = 8
BLOCK_METRES = 1.0
"""The size of the cubes points are grouped into, so a photo only projects the points it could see."""


@dataclass(frozen=True)
class Texels:
    rows: np.ndarray
    columns: np.ndarray
    positions: np.ndarray
    normals: np.ndarray
    owners: np.ndarray
    base_colours: np.ndarray

    def __len__(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class DepthBuffers:
    camera: PhotoCamera
    clean: np.ndarray
    lidar: np.ndarray | None


@dataclass(frozen=True)
class ViewSamples:
    accepted: np.ndarray
    disagreed: np.ndarray
    u: np.ndarray
    v: np.ndarray
    weight: np.ndarray
    faced: np.ndarray
    """Turned toward this camera and inside its frame, before anything occludes it.

    A texel no camera ever faced could not have been photographed from where
    the owner walked, so it is not missing colour: it was never reachable.
    """


def face_normals(world: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit normals and areas for (T, 3, 3) triangles."""
    cross = np.cross(world[:, 1] - world[:, 0], world[:, 2] - world[:, 0])
    length = np.linalg.norm(cross, axis=1)
    return cross / np.maximum(length, 1e-12)[:, None], length / 2


ATLAS_BATCH_SIDES = (4, 8, 12, 24, 48)
"""The candidate squares faces are sorted into and rasterized together in; faces wider than the last are drawn one at a time."""
ATLAS_BATCH_CANDIDATES = 1_500_000
"""Candidate texels tested in one step, in doubles about a hundred megabytes."""


def rasterize_atlas(
    world: np.ndarray, uv: np.ndarray, owners: np.ndarray, size: int,
    base_colours: np.ndarray | None = None,
) -> Texels:
    """Every texel centre inside a triangle, with the surface point and owner it lands on.

    UV v runs up and image rows run down, so row = (1 - v) * size, with texel
    centres at integer coordinates. Where faces overlap, the later face owns the texel.

    Packed faces are a few texels across, and drawing them one by one was most
    of a floor's texel step: a Python call per face, a quarter of a million per
    atlas. Small faces are drawn together with the same arithmetic.
    """
    normals, areas = face_normals(world)
    if base_colours is None:
        base_colours = np.full((len(world), 3), 0.65, dtype=np.float32)
    x = uv[:, :, 0] * size - 0.5
    y = (1.0 - uv[:, :, 1]) * size - 0.5
    first_column, first_row = np.maximum(0, np.floor(x.min(axis=1))), np.maximum(0, np.floor(y.min(axis=1)))
    width = np.minimum(size - 1, np.ceil(x.max(axis=1))) - first_column + 1
    height = np.minimum(size - 1, np.ceil(y.max(axis=1))) - first_row + 1
    drawn = (areas > 1e-10) & (width > 0) & (height > 0)
    side = np.maximum(width, height)
    pieces = [_small_texels(x, y, first_column, first_row, width, height, faces, size, square)
              for faces, square in _batches(np.flatnonzero(drawn), side)]
    pieces += [_large_texels(world, uv, index, size) for index in np.flatnonzero(drawn & (side > ATLAS_BATCH_SIDES[-1]))]
    pieces = [_placed(world, *piece) for piece in pieces if piece is not None and len(piece[0])]
    if not pieces:
        empty = np.empty((0, 3), dtype=np.float32)
        return Texels(np.empty(0, np.int32), np.empty(0, np.int32), empty, empty, np.empty(0, np.int32), empty)
    faces, rows, columns, positions = (np.concatenate(parts) for parts in zip(*pieces))
    keep = _last_face_per_texel(faces, rows.astype(np.int64) * size + columns)
    faces = faces[keep]
    return Texels(
        rows[keep], columns[keep], positions[keep],
        normals[faces].astype(np.float32), owners[faces].astype(np.int32), np.asarray(base_colours, dtype=np.float32)[faces],
    )


def _placed(world, faces, rows, columns, weights):
    """A batch's texels with their surface points, kept compact: the weights are dropped once the points are made."""
    corners = world[faces]
    positions = (weights[:, 0, None] * corners[:, 0] + weights[:, 1, None] * corners[:, 1] + weights[:, 2, None] * corners[:, 2]).astype(np.float32)
    return faces.astype(np.int32), rows.astype(np.int32), columns.astype(np.int32), positions


def _last_face_per_texel(faces: np.ndarray, keys: np.ndarray) -> np.ndarray:
    """For each texel, sorted by texel, the entry of the latest face that covers it."""
    order = np.lexsort((faces, keys))
    last = np.append(keys[order][1:] != keys[order][:-1], True)
    return order[last]


def _batches(faces: np.ndarray, side: np.ndarray):
    """The faces in groups of one candidate square each, a square's worth of candidates at a time."""
    smaller = 0
    for square in ATLAS_BATCH_SIDES:
        chosen = faces[(side[faces] > smaller) & (side[faces] <= square)]
        smaller = square
        batch = max(1, ATLAS_BATCH_CANDIDATES // square ** 2)
        for start in range(0, len(chosen), batch):
            yield chosen[start:start + batch], square


def _small_texels(x, y, first_column, first_row, width, height, faces, size, square):
    """Texel centres inside many small faces at once, as (faces, rows, columns, barycentric weights)."""
    offsets = np.arange(square)
    down, across = np.meshgrid(offsets, offsets, indexing="ij")
    down, across = down.ravel(), across.ravel()
    x, y = x[faces], y[faces]
    within = (across[None] < width[faces, None]) & (down[None] < height[faces, None])
    determinant = (y[:, 1] - y[:, 2]) * (x[:, 0] - x[:, 2]) + (x[:, 2] - x[:, 1]) * (y[:, 0] - y[:, 2])
    within &= (np.abs(determinant) >= 1e-12)[:, None]
    face, slot = np.nonzero(within)
    px = first_column[faces][face] + across[slot]
    py = first_row[faces][face] + down[slot]
    x, y, determinant = x[face], y[face], determinant[face]
    first = ((y[:, 1] - y[:, 2]) * (px - x[:, 2]) + (x[:, 2] - x[:, 1]) * (py - y[:, 2])) / determinant
    second = ((y[:, 2] - y[:, 0]) * (px - x[:, 2]) + (x[:, 0] - x[:, 2]) * (py - y[:, 2])) / determinant
    weights = np.stack([first, second, 1.0 - first - second], axis=1)
    inside = np.all(weights >= -1e-9, axis=1)
    return faces[face[inside]], py[inside].astype(np.int64), px[inside].astype(np.int64), weights[inside]


def _large_texels(world, uv, index, size):
    """A face too large to batch, drawn on its own with the same arithmetic."""
    texels = _triangle_texels(world[index], uv[index], np.zeros(3), 0, np.zeros(3), size)
    if texels is None:
        return None
    rows, columns = texels[0].astype(np.int64), texels[1].astype(np.int64)
    x = uv[index, :, 0] * size - 0.5
    y = (1.0 - uv[index, :, 1]) * size - 0.5
    weights = _barycentric(x, y, columns.astype(np.float64), rows.astype(np.float64)).T
    return np.full(len(rows), index), rows, columns, weights


def _triangle_texels(world, uv, normal, owner, colour, size):
    x = uv[:, 0] * size - 0.5
    y = (1.0 - uv[:, 1]) * size - 0.5
    column_range = np.arange(max(0, int(np.floor(x.min()))), min(size - 1, int(np.ceil(x.max()))) + 1)
    row_range = np.arange(max(0, int(np.floor(y.min()))), min(size - 1, int(np.ceil(y.max()))) + 1)
    if not len(column_range) or not len(row_range):
        return None
    columns, rows = np.meshgrid(column_range, row_range)
    weights = _barycentric(x, y, columns.ravel().astype(np.float64), rows.ravel().astype(np.float64))
    if weights is None:
        return None
    inside = np.all(weights >= -1e-9, axis=0)
    if not inside.any():
        return None
    w = weights[:, inside]
    positions = (w[0, :, None] * world[0] + w[1, :, None] * world[1] + w[2, :, None] * world[2]).astype(np.float32)
    count = int(inside.sum())
    return (
        rows.ravel()[inside].astype(np.int32),
        columns.ravel()[inside].astype(np.int32),
        positions,
        np.repeat(normal[None].astype(np.float32), count, axis=0),
        np.full(count, owner, dtype=np.int32),
        np.repeat(np.asarray(colour, dtype=np.float32)[None], count, axis=0),
    )


def _barycentric(x, y, px, py):
    determinant = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
    if abs(determinant) < 1e-12:
        return None
    first = ((y[1] - y[2]) * (px - x[2]) + (x[2] - x[1]) * (py - y[2])) / determinant
    second = ((y[2] - y[0]) * (px - x[2]) + (x[0] - x[2]) * (py - y[2])) / determinant
    return np.stack([first, second, 1.0 - first - second])


def sample_surface(world: np.ndarray, spacing: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Points spread over triangles about `spacing` apart, with each point's triangle normal."""
    normals, areas = face_normals(world)
    counts = np.maximum(1, np.rint(areas / spacing ** 2)).astype(np.int64)
    counts[areas <= 1e-10] = 0
    owner = np.repeat(np.arange(len(world)), counts)
    generator = np.random.default_rng(seed)
    first, second = generator.random(len(owner)), generator.random(len(owner))
    root = np.sqrt(first)
    a, b, c = 1 - root, root * (1 - second), root * second
    corners = world[owner]
    points = a[:, None] * corners[:, 0] + b[:, None] * corners[:, 1] + c[:, None] * corners[:, 2]
    return points.astype(np.float32), normals[owner].astype(np.float32)


def evenly_spread(cameras: list[PhotoCamera], limit: int) -> list[PhotoCamera]:
    """At most `limit` cameras spread evenly through the walk, keeping the first and the last.

    Neighbouring video frames are nearly the same view, so thinning a long walk
    this way loses little while every per-photo step gets cheaper in proportion.
    """
    if len(cameras) <= limit:
        return cameras
    picks = np.linspace(0, len(cameras) - 1, limit).round().astype(int)
    return [cameras[index] for index in dict.fromkeys(picks.tolist())]


def in_parallel(work, items, counted: bool = True):
    """`work` over every item on all cores, in bounded batches, yielding results in item order.

    The per-photo work is array arithmetic that lets go of the interpreter lock,
    so threads run it side by side while sharing the points rather than copying
    them. Results come back in photo order, so the outcome is the same as one
    run photo by photo. Only one photo per core is in hand at a time, because
    each holds its share of the texels and a small server has little memory
    to spare.
    """
    workers = os.cpu_count() or 4
    finished = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(items), workers):
            for result in pool.map(work, items[start:start + workers]):
                finished += 1
                if counted:
                    advanced(finished, len(items))
                yield result


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
    usable = (depth > NEAR_LIMIT).all(axis=1)
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
    for corners_u, corners_v, corners_depth in zip(u[~few_pixels], v[~few_pixels], depth[~few_pixels]):
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


def view_samples(buffers: DepthBuffers, positions: np.ndarray, normals: np.ndarray, quality: float) -> ViewSamples:
    camera = buffers.camera
    u, v, depth = camera.project(positions)
    toward = camera.position[None, :].astype(np.float32) - positions
    distance = np.linalg.norm(toward, axis=1)
    facing = np.einsum("ij,ij->i", normals, toward) / np.maximum(distance, 1e-6)
    inside = (depth > NEAR_LIMIT) & (facing > MIN_FACING) & (u >= 0) & (u <= camera.width - 1) & (v >= 0) & (v <= camera.height - 1)
    accepted, disagreed = _depth_agreement(buffers, u, v, depth, facing, inside)
    border = np.clip(np.minimum.reduce([u, v, camera.width - 1 - u, camera.height - 1 - v]) / BORDER_FALLOFF_PIXELS, 0.0, 1.0)
    weight = np.where(accepted, facing ** 2 / np.maximum(distance, 0.5) * border * quality, 0.0)
    return ViewSamples(accepted & (weight > 0), disagreed, u, v, weight.astype(np.float32), inside)


def _depth_agreement(buffers, u, v, depth, facing, inside):
    camera, clean = buffers.camera, buffers.clean
    height, width = clean.shape
    columns = np.clip(np.rint((u + 0.5) / camera.width * width - 0.5).astype(np.int64), 0, width - 1)
    rows = np.clip(np.rint((v + 0.5) / camera.height * height - 0.5).astype(np.int64), 0, height - 1)
    # `footprint` is one *depth-buffer* pixel in world units. `camera.fx` and
    # `camera.width` are full-resolution values, while `width` is reduced, so
    # this is depth / reduced_fx. Do not multiply by DEPTH_BUFFER_DIVISOR a
    # second time: the buffer has already made that conversion.
    footprint = depth * camera.width / (camera.fx * width + 1e-9)
    slope = np.sqrt(np.clip(1 - facing ** 2, 0, 1)) / np.maximum(facing, MIN_FACING)
    # Erosion reaches one neighbouring pixel and nearest-pixel lookup adds at
    # most half a pixel. This deliberately favours rejecting a texel at a
    # sharp depth edge over importing foreground colour onto a farther face.
    spread = 1.5 * footprint * slope
    in_front_of_clean = depth <= clean[rows, columns] + 0.02 + 0.01 * depth + spread
    if buffers.lidar is None:
        return inside & in_front_of_clean, np.zeros_like(inside)
    scanned = buffers.lidar[rows, columns]
    # The two directions are not the same claim, so they do not share a number.
    #
    # A scan surface IN FRONT of the model surface is something really standing
    # there. A laptop lying on a desk puts its keyboard about two centimetres
    # above the top, and one loose tolerance covering both directions called
    # that agreement and painted the keyboard flat onto the desk. Judged
    # strictly, it is what it is: an object in the way.
    #
    # A scan surface BEHIND it is usually noise, a thin gap, or a wall the scan
    # caught once at a glancing angle, and rejecting those leaves holes in
    # surfaces that were photographed perfectly well.
    occluding = OCCLUDER_TOLERANCE + spread
    tolerance = 0.04 + 0.015 * depth + spread
    known = np.isfinite(scanned)
    behind_scan = known & (depth > scanned + occluding)
    short_of_scan = known & (depth < scanned - tolerance)
    visible = inside & in_front_of_clean
    # A supplied LiDAR mesh is an occlusion authority. Unknown depth must not
    # let a photo invent colour for a surface the scan did not verify.
    return visible & known & ~behind_scan & ~short_of_scan, visible & (short_of_scan | ~known)


def bilinear(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    x0 = np.clip(np.floor(u).astype(np.int64), 0, width - 2)
    y0 = np.clip(np.floor(v).astype(np.int64), 0, height - 2)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    top = image[y0, x0] * (1 - fx) + image[y0, x0 + 1] * fx
    bottom = image[y0 + 1, x0] * (1 - fx) + image[y0 + 1, x0 + 1] * fx
    return (top * (1 - fy) + bottom * fy).astype(np.float32)


def to_linear(srgb: np.ndarray) -> np.ndarray:
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)


def to_srgb(linear: np.ndarray) -> np.ndarray:
    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)


class TopViews:
    """The strongest few views per texel, with their colors, plus how often the scan contradicted the model."""

    def __init__(self, count: int, slots: int = TOP_VIEWS, color_type=np.float32):
        self.weights = np.zeros((count, slots), dtype=np.float32)
        self.colors = np.zeros((count, slots, 3), dtype=color_type)
        self.accepted = np.zeros(count, dtype=np.int16)
        self.disagreed = np.zeros(count, dtype=np.int16)

    def add(self, indices: np.ndarray, weights: np.ndarray, colors: np.ndarray) -> None:
        self.accepted[indices] += 1
        slots = np.argmin(self.weights[indices], axis=1)
        stronger = weights > self.weights[indices, slots]
        chosen, slot = indices[stronger], slots[stronger]
        self.weights[chosen, slot] = weights[stronger]
        self.colors[chosen, slot] = colors[stronger]

    def note_disagreement(self, indices: np.ndarray) -> None:
        self.disagreed[indices] += 1

    def resolve(self) -> tuple[np.ndarray, np.ndarray]:
        """Linear color per texel and whether it counts as covered."""
        total = self.weights.sum(axis=1)
        mean = _weighted_mean(self.weights, self.colors)
        distance = np.linalg.norm(self.colors - mean[:, None, :], axis=2)
        kept = np.where(distance <= OUTLIER_DISTANCE, self.weights, 0.0)
        enough = kept.sum(axis=1) >= KEPT_WEIGHT_SHARE * total
        # A disagreement between otherwise plausible photos is not a texture.
        # Keeping its average would turn a moving person or a bad pose into a
        # believable but incorrect wall colour. The caller leaves it neutral.
        final = np.where(enough[:, None], _weighted_mean(kept, self.colors), mean)
        covered = (total > 0) & enough & (self.disagreed <= self.accepted)
        return final, covered


def _weighted_mean(weights: np.ndarray, colors: np.ndarray) -> np.ndarray:
    total = weights.sum(axis=1, keepdims=True)
    return (weights[:, :, None] * colors).sum(axis=1) / np.maximum(total, 1e-9)


def exposure_gains(observations: list[tuple[np.ndarray, np.ndarray]], camera_count: int, point_count: int) -> np.ndarray:
    """Per-camera, per-channel linear gains that make shared points agree across photos.

    `observations[c]` holds the point indices camera c saw and their linear
    colors. Each point's log color is modelled as its true value minus its
    camera's gain, solved by alternating averages with the mean gain held at 0.
    """
    points = np.concatenate([indices for indices, _ in observations]) if observations else np.empty(0, np.int64)
    if not len(points):
        return np.ones((camera_count, 3), dtype=np.float32)
    cameras = np.concatenate([np.full(len(indices), camera) for camera, (indices, _) in enumerate(observations)])
    logs = np.log(np.maximum(np.concatenate([colors for _, colors in observations]), 1e-3))
    shared = np.bincount(points, minlength=point_count)[points] >= 2
    points, cameras, logs = points[shared], cameras[shared], logs[shared]
    gains = np.zeros((camera_count, 3))
    for _ in range(EXPOSURE_ITERATIONS):
        truth = _grouped_mean(points, logs + gains[cameras], point_count)
        gains = _grouped_mean(cameras, truth[points] - logs, camera_count)
        gains = np.clip(gains - gains.mean(axis=0), -MAX_LOG_GAIN, MAX_LOG_GAIN)
    return np.exp(gains).astype(np.float32)


def _grouped_mean(groups: np.ndarray, values: np.ndarray, count: int) -> np.ndarray:
    sizes = np.maximum(np.bincount(groups, minlength=count), 1)[:, None]
    return np.stack([np.bincount(groups, weights=values[:, channel], minlength=count) for channel in range(3)], axis=1) / sizes


def pad_gutters(image: np.ndarray, filled: np.ndarray, passes: int = GUTTER_PASSES) -> np.ndarray:
    """Spread island edge colors outward so filtering at chart borders never pulls in the background.

    Used a second way, over the texels photos actually reached, this closes the
    speckle a strict occlusion test leaves behind. A tabletop seen past the
    clutter standing on it keeps every texel the clutter hid, and those texels
    held base colour, so a photographed table came out flecked with brown. A
    few passes fill those specks from their neighbours. It changes only what is
    displayed: the coverage mask still records where photos genuinely landed.

    Each pass fills the unfilled pixels beside a filled one with the mean of
    their filled neighbours, wrapping at the image edge. Only that frontier is
    worked on: redoing the whole 4096-pixel image each pass was most of an
    atlas's image step on the droplet.
    """
    image, filled = image.copy(), filled.copy()
    height, width = filled.shape
    pixels = image.reshape(height * width, -1)
    for _ in range(passes):
        beside = np.roll(filled, 1, axis=0) | np.roll(filled, -1, axis=0) | np.roll(filled, 1, axis=1) | np.roll(filled, -1, axis=1)
        rows, columns = np.nonzero(beside & ~filled)
        if not len(rows):
            break
        total = np.zeros((len(rows), pixels.shape[1]), dtype=image.dtype)
        count = np.zeros(len(rows), dtype=np.float32)
        for neighbour_rows, neighbour_columns in (
            ((rows - 1) % height, columns), ((rows + 1) % height, columns),
            (rows, (columns - 1) % width), (rows, (columns + 1) % width),
        ):
            neighbour_filled = filled[neighbour_rows, neighbour_columns]
            total += pixels[neighbour_rows * width + neighbour_columns] * neighbour_filled[:, None]
            count += neighbour_filled
        pixels[rows * width + columns] = total / count[:, None]
        filled[rows, columns] = True
    return image
