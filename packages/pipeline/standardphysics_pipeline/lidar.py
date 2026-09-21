"""The captured LiDAR mesh, in the room frame, as points and as faces.

The phone writes every mesh anchor in its own local frame with a transform
into ARKit world, and the uploaded artifact keeps ARKit's column-major layout.
Reading it as row-major stacks every anchor at the origin, so the reshape order
here is load-bearing.

Faces answer "what is in front of what" for a camera. Points answer "what is
where" for clustering and for carving an object out of a photo's rectangle.
"""

from __future__ import annotations

import pathlib

import numpy as np
from pydantic import ValidationError
from standardphysics_contracts import LidarMesh, Mat4

DEFAULT_VOXEL = 0.02
"""Two centimetres: fine enough to separate a laptop from the desk under it."""


class LidarMeshError(ValueError):
    pass


def load_mesh(path: pathlib.Path) -> LidarMesh | None:
    """The uploaded mesh, recognized by content rather than by filename.

    Object storage drops extensions while local captures keep lidar-mesh.json.
    """
    try:
        return LidarMesh.model_validate_json(pathlib.Path(path).read_bytes())
    except (OSError, ValidationError, ValueError):
        return None


def triangles_in_arkit_world(mesh: LidarMesh) -> np.ndarray:
    """Every face as three ARKit-world corners, anchor transforms already applied."""
    pieces = []
    for part in mesh.parts:
        matrix = np.asarray(part.transform, dtype=np.float32).reshape(4, 4, order="F")
        vertices = np.asarray(part.vertices, dtype=np.float32).reshape(-1, 3)
        vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
        pieces.append(vertices[np.asarray(part.triangles, dtype=np.int64).reshape(-1, 3)])
    return np.concatenate(pieces, axis=0) if pieces else np.empty((0, 3, 3), dtype=np.float32)


def vertices_in_arkit_world(mesh: LidarMesh) -> np.ndarray:
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
