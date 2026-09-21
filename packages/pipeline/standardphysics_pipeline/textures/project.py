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

from dataclasses import dataclass

import numpy as np

from .camera import PhotoCamera

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
MAX_LOG_GAIN = 0.7
GUTTER_PASSES = 8


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


def rasterize_atlas(
    world: np.ndarray, uv: np.ndarray, owners: np.ndarray, size: int,
    base_colours: np.ndarray | None = None,
) -> Texels:
    """Every texel centre inside a triangle, with the surface point and owner it lands on.

    UV v runs up and image rows run down, so row = (1 - v) * size, with texel
    centres at integer coordinates.
    """
    normals, areas = face_normals(world)
    if base_colours is None:
        base_colours = np.full((len(world), 3), 0.65, dtype=np.float32)
    pieces = [
        _triangle_texels(world[index], uv[index], normals[index], owners[index], base_colours[index], size)
        for index in np.flatnonzero(areas > 1e-10)
    ]
    pieces = [piece for piece in pieces if piece is not None]
    if not pieces:
        empty = np.empty((0, 3), dtype=np.float32)
        return Texels(np.empty(0, np.int32), np.empty(0, np.int32), empty, empty, np.empty(0, np.int32), empty)
    rows, columns, positions, normals_out, owners_out, colours_out = (np.concatenate(parts) for parts in zip(*pieces))
    _, last = np.unique((rows.astype(np.int64) * size + columns)[::-1], return_index=True)
    keep = len(rows) - 1 - last
    return Texels(rows[keep], columns[keep], positions[keep], normals_out[keep], owners_out[keep], colours_out[keep])


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


def depth_buffer(camera: PhotoCamera, points: np.ndarray) -> np.ndarray:
    """Nearest depth per low-resolution pixel, eroded one pixel so occluders grow and holes close."""
    small = camera.resized(max(1, camera.width // DEPTH_BUFFER_DIVISOR), max(1, camera.height // DEPTH_BUFFER_DIVISOR))
    u, v, depth = small.project(points)
    columns, rows = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
    valid = (depth > NEAR_LIMIT) & (columns >= 0) & (columns < small.width) & (rows >= 0) & (rows < small.height)
    buffer = np.full(small.width * small.height, np.inf, dtype=np.float32)
    order = np.argsort(-depth[valid])
    buffer[(rows[valid] * small.width + columns[valid])[order]] = depth[valid][order]
    return _erode(buffer.reshape(small.height, small.width))


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
    if not len(triangles):
        return buffer
    local = triangles @ small.room_to_camera[:3, :3].T + small.room_to_camera[:3, 3]
    depth = local[..., 2]
    valid = np.all(depth > NEAR_LIMIT, axis=1)
    local, depth = local[valid], depth[valid]
    if not len(local):
        return buffer
    u = small.fx * local[..., 0] / depth + small.cx
    v = small.fy * local[..., 1] / depth + small.cy
    visible = (
        (u.max(axis=1) >= -1) & (u.min(axis=1) <= small.width)
        & (v.max(axis=1) >= -1) & (v.min(axis=1) <= small.height)
    )
    for corners_u, corners_v, corners_depth in zip(u[visible], v[visible], depth[visible]):
        _rasterize_depth_triangle(buffer, corners_u, corners_v, corners_depth)
    return _erode(buffer)


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

    def __init__(self, count: int):
        self.weights = np.zeros((count, TOP_VIEWS), dtype=np.float32)
        self.colors = np.zeros((count, TOP_VIEWS, 3), dtype=np.float32)
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
    """
    image, filled = image.copy(), filled.copy()
    for _ in range(passes):
        total = np.zeros_like(image)
        count = np.zeros(filled.shape, dtype=np.float32)
        for axis, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
            shifted_filled = np.roll(filled, step, axis=axis)
            total += np.roll(image, step, axis=axis) * shifted_filled[..., None]
            count += shifted_filled
        grow = ~filled & (count > 0)
        image[grow] = total[grow] / count[grow][:, None]
        filled |= grow
    return image
