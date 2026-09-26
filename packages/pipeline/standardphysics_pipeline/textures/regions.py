"""Finding the vertices near one object without testing every vertex of the scan.

A merged library floor has millions of vertices and hundreds of labelled
objects. Each object only ever cares about the vertices in and around its own
box, but testing the whole scan against every box made the per-object steps
cost objects times vertices: over ten minutes of a small server's time for the
mirrored completion alone. Vertices are grouped into metre cubes once, and an
object asks only for the cubes its box overlaps. Callers then run their own
exact test on those candidates, so the answer is the one the whole scan gave.
"""

from __future__ import annotations

import numpy as np
from standardphysics_contracts import SceneNode

CUBE_METRES = 1.0
BOUNDARY_SLACK = 1e-6
"""Padding on each box, so rounding in a caller's own test never leaves a vertex outside the candidates it was drawn from."""


class VertexIndex:
    """Vertices sorted into metre cubes, answering which could lie inside an oriented box."""

    def __init__(self, vertices: np.ndarray):
        self.vertices = vertices
        cubes = np.floor(vertices / CUBE_METRES).astype(np.int64)
        self.low = cubes.min(axis=0) if len(cubes) else np.zeros(3, dtype=np.int64)
        self.span = (cubes.max(axis=0) - self.low + 1) if len(cubes) else np.ones(3, dtype=np.int64)
        keys = self._key(cubes)
        self.order = np.argsort(keys, kind="stable")
        self.sorted_keys = keys[self.order]

    def _key(self, cubes: np.ndarray) -> np.ndarray:
        shifted = cubes - self.low
        return (shifted[:, 0] * self.span[1] + shifted[:, 1]) * self.span[2] + shifted[:, 2]

    def near_box(self, node: SceneNode, reach: float) -> np.ndarray:
        """Ascending indices of every vertex in the axis-aligned bounds of the node's box grown by `reach`."""
        low, high = _box_bounds(node, reach)
        return self.within(low, high)

    def within(self, low: np.ndarray, high: np.ndarray) -> np.ndarray:
        """Ascending indices of every vertex inside the axis-aligned bounds from `low` to `high`."""
        if not len(self.vertices):
            return np.empty(0, dtype=np.int64)
        first = np.maximum(np.floor(low / CUBE_METRES).astype(np.int64), self.low)
        last = np.minimum(np.floor(high / CUBE_METRES).astype(np.int64), self.low + self.span - 1)
        if np.any(last < first):
            return np.empty(0, dtype=np.int64)
        grid = np.stack(np.meshgrid(*[np.arange(a, b + 1) for a, b in zip(first, last)], indexing="ij"), axis=-1)
        keys = self._key(grid.reshape(-1, 3))
        starts = np.searchsorted(self.sorted_keys, keys, side="left")
        ends = np.searchsorted(self.sorted_keys, keys, side="right")
        counts = ends - starts
        offsets = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        candidates = self.order[np.repeat(starts, counts) + offsets]
        points = self.vertices[candidates]
        inside = np.all((points >= low) & (points <= high), axis=1)
        return np.sort(candidates[inside])


def _box_bounds(node: SceneNode, reach: float) -> tuple[np.ndarray, np.ndarray]:
    """The room-frame axis-aligned bounds of every point whose node-frame coordinates lie within the grown box.

    A caller measures a point against the box as `(point - centre) @ rotation`,
    so the box is mapped back through the inverse of that rotation, which also
    covers a transform that is not quite orthonormal.
    """
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    rotation, centre = matrix[:3, :3], matrix[:3, 3]
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z], dtype=np.float64) / 2 + reach
    try:
        back = np.linalg.inv(rotation)
    except np.linalg.LinAlgError:
        return np.full(3, -np.inf), np.full(3, np.inf)
    extent = np.abs(back).T @ half + BOUNDARY_SLACK
    return centre - extent, centre + extent


class TrianglesByCorner:
    """Triangles found from their first corner, so an object's triangles come from its own vertices."""

    def __init__(self, triangles: np.ndarray):
        self.triangles = triangles
        self.order = np.argsort(triangles[:, 0], kind="stable") if len(triangles) else np.empty(0, dtype=np.int64)
        self.first_corners = triangles[self.order, 0] if len(triangles) else np.empty(0, dtype=np.int64)

    def all_corners_in(self, members: np.ndarray, vertex_count: int) -> np.ndarray:
        """Ascending indices of the triangles whose every corner is one of the ascending `members`."""
        starts = np.searchsorted(self.first_corners, members, side="left")
        ends = np.searchsorted(self.first_corners, members, side="right")
        counts = ends - starts
        offsets = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        found = np.sort(self.order[np.repeat(starts, counts) + offsets])
        is_member = np.zeros(vertex_count, dtype=bool)
        is_member[members] = True
        return found[is_member[self.triangles[found]].all(axis=1)]
