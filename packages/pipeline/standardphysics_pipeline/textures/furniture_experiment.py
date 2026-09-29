"""Isolated scan evidence and measured-box fitting for a display-only furniture trial.

The graph's boxes remain the measurement. These helpers transform a copy of the
scan into a node's frame and fit a generated mesh for display without changing
the graph, scan, or any assessment input.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np
from PIL import Image
from scipy.spatial import KDTree
from standardphysics_contracts import SceneGraph, SceneNode

from .atlas_texels import sample_surface
from .camera import PhotoCamera
from .material_references import ReferenceCrop, reference_crops
from .object_holes import people_masks
from .scan_colour import ColouredScan


@dataclass(frozen=True)
class MeasuredBox:
    rotation: np.ndarray
    centre: np.ndarray
    dimensions: np.ndarray

    @classmethod
    def from_node(cls, node: SceneNode) -> MeasuredBox:
        matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
        dimensions = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z], dtype=np.float64)
        if np.any(dimensions <= 0):
            raise ValueError("furniture box must have three positive dimensions")
        return cls(matrix[:3, :3], matrix[:3, 3], dimensions)

    def to_local(self, room_points: np.ndarray) -> np.ndarray:
        """Room-frame points in the node's measured box frame, Z up."""
        return (room_points - self.centre) @ self.rotation

    def to_room(self, local_points: np.ndarray) -> np.ndarray:
        return local_points @ self.rotation.T + self.centre


@dataclass(frozen=True)
class FurnitureEvidence:
    box: MeasuredBox
    points_local: np.ndarray
    colours: np.ndarray
    crop: Image.Image | None
    crop_frame_id: str | None
    photographed_points: int


def scanned_object_points(scan: ColouredScan, owners: np.ndarray, node_index: int, box: MeasuredBox) -> tuple[np.ndarray, np.ndarray]:
    """Only measured vertices owned by this node, never mirrored or hole-filled ones."""
    selected = (owners == node_index) & scan.scanned
    return box.to_local(scan.vertices[selected]), scan.colours[selected]


def _masked_crop(crop, cameras: list[PhotoCamera], people: dict | None) -> Image.Image:
    image = crop.image.convert("RGBA")
    if not people:
        return image
    camera = next(camera for camera in cameras if camera.frame_id == crop.frame_id)
    mask = people_masks(people, [camera], {camera.frame_id: (camera.height, camera.width)})[camera.frame_id]
    alpha = (mask[crop.box[1]:crop.box[3], crop.box[0]:crop.box[2]] * 255).astype(np.uint8)
    image.putalpha(Image.fromarray(alpha).resize(image.size))
    return image


def _reviewed_frame_crop(
    scan: ColouredScan, owners: np.ndarray, node_index: int,
    camera: PhotoCamera, frame_path: pathlib.Path,
) -> ReferenceCrop:
    """Crop a reviewed photo around all measured points when its best-source count is misleading."""
    points = scan.vertices[(owners == node_index) & scan.scanned]
    columns, rows, depth = camera.project(points)
    visible = (depth > 0) & (columns >= 0) & (columns < camera.width) & (rows >= 0) & (rows < camera.height)
    if visible.sum() < 20:
        raise ValueError(f"{camera.frame_id} has too few projected object points")
    left, right = np.percentile(columns[visible], [5, 95])
    top, bottom = np.percentile(rows[visible], [5, 95])
    padding = 0.35 * max(right - left, bottom - top)
    box = (
        max(0, int(left - padding)), max(0, int(top - padding)),
        min(camera.width, int(right + padding)), min(camera.height, int(bottom + padding)),
    )
    with Image.open(frame_path) as opened:
        image = opened.convert("RGB").crop(box)
    return ReferenceCrop(str(node_index), camera.frame_id, image, int(visible.sum()), box)


def furniture_evidence(
    scan: ColouredScan,
    owners: np.ndarray,
    graph: SceneGraph,
    node_index: int,
    cameras: list[PhotoCamera],
    frame_paths: dict[str, pathlib.Path],
    people: dict | None = None,
    reviewed_frame_id: str | None = None,
) -> FurnitureEvidence:
    """A node's measured points, best photographed crop, and immutable box."""
    box = MeasuredBox.from_node(graph.nodes[node_index])
    points, colours = scanned_object_points(scan, owners, node_index, box)
    unique_keys = [str(node.id) for node in graph.nodes]
    crop = next(
        (item for item in reference_crops(scan, owners, unique_keys, cameras, frame_paths)
         if item.key == unique_keys[node_index]),
        None,
    )
    if reviewed_frame_id is not None:
        camera = next(camera for camera in cameras if camera.frame_id == reviewed_frame_id)
        crop = _reviewed_frame_crop(scan, owners, node_index, camera, frame_paths[reviewed_frame_id])
    photographed = int(np.sum((owners == node_index) & scan.scanned & scan.seen))
    return FurnitureEvidence(
        box, points, colours,
        _masked_crop(crop, cameras, people) if crop is not None else None,
        crop.frame_id if crop is not None else None,
        photographed,
    )


def spar_point_cloud(
    points_local: np.ndarray, colours: np.ndarray, box: MeasuredBox, camera: PhotoCamera,
    count: int = 512,
) -> np.ndarray:
    """SPAR3D's N x 6 array: canonical XYZ in a unit box, then RGB in 0-1.

    Its fixed input camera sits on +X. Rotate only around Z so the real photo's
    horizontal viewing direction also faces +X, then use SPAR3D's box-centred
    unit-scale point convention.
    """
    if not len(points_local):
        raise ValueError("the object has no scanned points")
    camera_local = box.to_local(camera.position[None, :])[0]
    angle = -np.arctan2(camera_local[1], camera_local[0])
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, -sine, 0], [sine, cosine, 0], [0, 0, 1]])
    rotated = points_local @ rotation.T
    low, high = rotated.min(axis=0), rotated.max(axis=0)
    scale = float(np.max(high - low))
    if scale <= 1e-8:
        raise ValueError("the object's point cloud has no extent")
    normalized = (rotated - (low + high) / 2) / scale
    generator = np.random.default_rng(0)
    indices = generator.choice(len(points_local), count, replace=len(points_local) < count)
    return np.column_stack((normalized[indices], np.clip(colours[indices], 0, 1))).astype(np.float32)


def yawed(vertices: np.ndarray, quarter_turns: int) -> np.ndarray:
    """Rotate a Z-up mesh by a multiple of 90 degrees without tilting it."""
    quarter_turns %= 4
    x, y, z = vertices.T
    if quarter_turns == 0:
        return vertices.copy()
    if quarter_turns == 1:
        return np.column_stack((-y, x, z))
    if quarter_turns == 2:
        return np.column_stack((-x, -y, z))
    return np.column_stack((y, -x, z))


def fit_to_box(vertices: np.ndarray, dimensions: np.ndarray) -> np.ndarray:
    """Fill the measured extents, centred horizontally and resting on the bottom."""
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    extent = high - low
    if np.any(extent <= 1e-8) or np.any(dimensions <= 0):
        raise ValueError("mesh and measured box must have three positive extents")
    fitted = (vertices - low) * (dimensions / extent)
    fitted[:, :2] -= dimensions[:2] / 2
    fitted[:, 2] -= dimensions[2] / 2
    return fitted


def box_iou(vertices: np.ndarray, dimensions: np.ndarray) -> float:
    """3D overlap of a mesh's local AABB with the measured local box."""
    low, high = vertices.min(axis=0), vertices.max(axis=0)
    half = dimensions / 2
    overlap = np.maximum(0, np.minimum(high, half) - np.maximum(low, -half))
    intersection = float(np.prod(overlap))
    mesh_volume = float(np.prod(high - low))
    box_volume = float(np.prod(dimensions))
    union = mesh_volume + box_volume - intersection
    return intersection / union if union > 0 else 0.0


def mean_surface_distance(points: np.ndarray, vertices: np.ndarray, triangles: np.ndarray) -> float:
    """Mean nearest sampled-surface distance in metres, not vertex distance."""
    if not len(points) or not len(triangles):
        raise ValueError("points and triangles must be nonempty")
    surface, _ = sample_surface(vertices[triangles], spacing=0.01, seed=0)
    return float(KDTree(surface).query(points)[0].mean())


def choose_yaw(
    vertices: np.ndarray, triangles: np.ndarray, dimensions: np.ndarray, points_local: np.ndarray,
) -> tuple[np.ndarray, int, float]:
    """Fit four upright poses and keep the one closest to the scanned surface."""
    candidates = []
    for quarter_turns in range(4):
        fitted = fit_to_box(yawed(vertices, quarter_turns), dimensions)
        distance = mean_surface_distance(points_local, fitted, triangles)
        candidates.append((distance, quarter_turns, fitted))
    distance, quarter_turns, fitted = min(candidates, key=lambda item: item[0])
    return fitted, quarter_turns, distance
