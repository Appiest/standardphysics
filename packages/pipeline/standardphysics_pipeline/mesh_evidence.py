"""Small shape hints from the partial scan, for visual object completion.

An empty cell means unobserved, not empty space. These hints never enter the
measurement or occlusion paths, which keep the original indexed mesh.
"""

from __future__ import annotations

import pathlib

import numpy as np
from standardphysics_contracts import LidarMesh, SceneGraph

MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SAMPLED_VERTICES = 60_000


def object_mesh_profiles(graph: SceneGraph, path: pathlib.Path | None) -> dict[str, dict]:
    if path is None or graph.capture_to_room is None:
        return {}
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return {}
        mesh = LidarMesh.model_validate_json(path.read_bytes())
    except (OSError, ValueError):
        return {}
    total = sum(len(part.vertices) // 3 for part in mesh.parts)
    if not total:
        return {}
    stride = max(1, int(np.ceil(total / MAX_SAMPLED_VERTICES)))
    pieces = []
    alignment = np.asarray(graph.capture_to_room.m).reshape(4, 4)
    for part in mesh.parts:
        vertices = np.asarray(part.vertices, dtype=np.float32).reshape(-1, 3)[::stride]
        transform = alignment @ np.asarray(part.transform).reshape(4, 4, order="F")
        pieces.append(vertices @ transform[:3, :3].T + transform[:3, 3])
    points = np.concatenate(pieces)
    return {str(node.id): _profile(points, node) for node in graph.contents()}


def _profile(points, node) -> dict:
    dimensions = np.asarray(node.dimensions.as_tuple())
    if not np.isfinite(dimensions).all() or np.any(dimensions <= 0):
        return {"sample_count": 0, "status": "unusable bounds"}
    try:
        inverse = np.linalg.inv(np.asarray(node.transform.m).reshape(4, 4))
    except np.linalg.LinAlgError:
        return {"sample_count": 0, "status": "unusable placement"}
    normalized = (points @ inverse[:3, :3].T + inverse[:3, 3]) / dimensions
    inside = normalized[np.all((normalized >= -0.5) & (normalized <= 0.5), axis=1)]
    grid = np.zeros((6, 4, 4), dtype=bool)
    if len(inside):
        cells = np.minimum(((inside + 0.5) * [4, 4, 6]).astype(int), [3, 3, 5])
        grid[cells[:, 2], cells[:, 1], cells[:, 0]] = True
    return {
        "sample_count": len(inside),
        "slices_bottom_to_top": [["".join("#" if cell else "." for cell in row) for row in level] for level in grid],
        "legend": "6 height slices, each 4x4 XY cells in object-local coordinates. # is observed surface; . is unobserved, not proof of free space. Bounds may include nearby objects.",
    }
