"""The captured LiDAR mesh, in the room frame, as points and as faces.

The phone writes every mesh anchor in its own local frame with a transform
into ARKit world, and the uploaded artifact keeps ARKit's column-major layout.
Reading it as row-major stacks every anchor at the origin, so the reshape order
here is load-bearing.

Faces answer "what is in front of what" for a camera. Points answer "what is
where" for clustering and for carving an object out of a photo's rectangle.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import Mat4

DEFAULT_VOXEL = 0.02
"""Two centimetres: fine enough to separate a laptop from the desk under it."""


class LidarMeshError(ValueError):
    pass


@dataclass(frozen=True)
class MeshPart:
    """One anchor of the mesh: its column-major ARKit transform, its vertices and the triangles indexing them."""

    transform: np.ndarray
    vertices: np.ndarray
    triangles: np.ndarray


@dataclass(frozen=True)
class MeshArrays:
    parts: list[MeshPart]


def load_mesh(path: pathlib.Path) -> MeshArrays | None:
    """The uploaded mesh as arrays, recognized by content rather than by filename.

    Object storage drops extensions while local captures keep lidar-mesh.json.

    Read into arrays one anchor at a time rather than through the contract
    model, which keeps a Python object per number: a library walk's mesh came to
    1.7 GB that way, over a third of a small server's memory, before any work
    was done with it. The upload was already checked against the contract on
    arrival; this checks only what the arithmetic needs.
    """
    try:
        payload = json.loads(pathlib.Path(path).read_bytes())
    except (OSError, ValueError):
        return None
    raw = payload.get("parts") if isinstance(payload, dict) else None
    if not isinstance(raw, list) or not raw:
        return None
    parts = []
    for index in range(len(raw)):
        part, raw[index] = _mesh_part(raw[index]), None
        if part is None:
            return None
        parts.append(part)
    return MeshArrays(parts)


def _mesh_part(raw) -> MeshPart | None:
    try:
        transform = np.asarray(raw["transform"], dtype=np.float64)
        vertices = np.asarray(raw["vertices"], dtype=np.float32).reshape(-1, 3)
        triangles = np.asarray(raw["triangles"], dtype=np.int64).reshape(-1, 3)
    except (KeyError, TypeError, ValueError):
        return None
    if transform.shape != (16,) or not np.isfinite(transform).all() or not np.isfinite(vertices).all():
        return None
    if len(triangles) and (triangles.min() < 0 or triangles.max() >= len(vertices)):
        return None
    return MeshPart(transform, vertices, triangles)


def triangles_in_arkit_world(mesh: MeshArrays) -> np.ndarray:
    """Every face as three ARKit-world corners, anchor transforms already applied."""
    pieces = []
    for part in mesh.parts:
        matrix = np.asarray(part.transform, dtype=np.float32).reshape(4, 4, order="F")
        vertices = np.asarray(part.vertices, dtype=np.float32).reshape(-1, 3)
        vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
        pieces.append(vertices[np.asarray(part.triangles, dtype=np.int64).reshape(-1, 3)])
    return np.concatenate(pieces, axis=0) if pieces else np.empty((0, 3, 3), dtype=np.float32)


def vertices_in_arkit_world(mesh: MeshArrays) -> np.ndarray:
    pieces = []
    for part in mesh.parts:
        matrix = np.asarray(part.transform, dtype=np.float32).reshape(4, 4, order="F")
        vertices = np.asarray(part.vertices, dtype=np.float32).reshape(-1, 3)
        pieces.append(vertices @ matrix[:3, :3].T + matrix[:3, 3])
    return np.concatenate(pieces, axis=0) if pieces else np.empty((0, 3), dtype=np.float32)


def into_room(values: np.ndarray, capture_to_room: Mat4) -> np.ndarray:
    """Points or face corners moved from ARKit world into the room frame."""
    matrix = np.asarray(capture_to_room.m, dtype=np.float32).reshape(4, 4)
    return values @ matrix[:3, :3].T + matrix[:3, 3]


def voxel_downsample(points: np.ndarray, voxel: float = DEFAULT_VOXEL) -> np.ndarray:
    """One point per occupied voxel, so density reflects surface area rather than scan dwell time."""
    if not len(points):
        return points
    keys = np.floor(points / voxel).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    return points[np.sort(first)]


def room_cloud(path: pathlib.Path, capture_to_room: Mat4, voxel: float = DEFAULT_VOXEL) -> np.ndarray:
    """Downsampled room-frame points from an uploaded mesh artifact."""
    mesh = load_mesh(path)
    if mesh is None:
        raise LidarMeshError(f"not a LiDAR mesh: {path}")
    return voxel_downsample(into_room(vertices_in_arkit_world(mesh), capture_to_room), voxel)


def room_faces(path: pathlib.Path, capture_to_room: Mat4) -> np.ndarray:
    """Room-frame faces from an uploaded mesh artifact, for occlusion tests."""
    mesh = load_mesh(path)
    if mesh is None:
        raise LidarMeshError(f"not a LiDAR mesh: {path}")
    return into_room(triangles_in_arkit_world(mesh), capture_to_room)
