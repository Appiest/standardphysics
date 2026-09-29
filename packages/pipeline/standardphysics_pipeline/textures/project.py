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
from .depth_buffers import NEAR_LIMIT
from .stages import advanced

OCCLUDER_TOLERANCE = 0.025
"""How far in front of a surface the scan must sit before something is standing on it.

Just above the LiDAR's own noise, and well under the thickness of the flattest
thing anyone leaves on a desk."""
MIN_FACING = 0.3


BORDER_FALLOFF_PIXELS = 48.0
TOP_VIEWS = 3
OUTLIER_DISTANCE = 0.12
KEPT_WEIGHT_SHARE = 0.5
EXPOSURE_ITERATIONS = 8
MAX_EXPOSURE_POINTS = 20_000
MAX_LOG_GAIN = 0.7


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
