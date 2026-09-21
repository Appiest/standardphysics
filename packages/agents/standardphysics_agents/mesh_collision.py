"""Continuous route capsules against measured triangles clipped to the mobility band."""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree
from standardphysics_contracts import LidarMesh, Vec3, bounds_the_room, to_meters
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import contains_point


def _clip_height(polygon: list[np.ndarray], bound: float, above: bool) -> list[np.ndarray]:
    clipped = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        start_inside = start[1] >= bound if above else start[1] <= bound
        end_inside = end[1] >= bound if above else end[1] <= bound
        if start_inside:
            clipped.append(start)
        if start_inside != end_inside:
            clipped.append(start + (end - start) * ((bound - start[1]) / (end[1] - start[1])))
    return clipped


def _point_segment_distance(point, start, end) -> float:
    delta = end - start
    length = float(delta @ delta)
    fraction = float(np.clip((point - start) @ delta / length, 0, 1)) if length else 0.0
    return float(np.linalg.norm(point - start - fraction * delta))


def _cross(left, right) -> float:
    return float(left[0] * right[1] - left[1] * right[0])


def _segments_distance(a, b, c, d) -> float:
    ab, cd = b - a, d - c
    denominator = _cross(ab, cd)
    if abs(denominator) > 1e-12:
        t, u = _cross(c - a, cd) / denominator, _cross(c - a, ab) / denominator
        if 0 <= t <= 1 and 0 <= u <= 1:
            return 0.0
    return min(_point_segment_distance(a, c, d), _point_segment_distance(b, c, d),
               _point_segment_distance(c, a, b), _point_segment_distance(d, a, b))


def _inside(point, polygon) -> bool:
    crosses = [_cross(end - start, point - start) for start, end in zip(polygon, np.roll(polygon, -1, axis=0))]
    area = abs(sum(_cross(start, end) for start, end in zip(polygon, np.roll(polygon, -1, axis=0))))
    return area > 1e-12 and (min(crosses) >= -1e-12 or max(crosses) <= 1e-12)


class MeshCollisionIndex:
    """Raw LiDAR is supplemental evidence, never a substitute for measured dimensions.

    A spatial index prunes candidate faces; exact segment-to-polygon distances
    catch collisions between route samples and far from triangle vertices.
    """
    MIN_HEIGHT_METERS = to_meters(0.25)
    MAX_HEIGHT_METERS = to_meters(27.0)

    def __init__(self, mesh: LidarMesh) -> None:
        self._polygons = []
        for part in mesh.parts:
            local = np.asarray(part.vertices, dtype=float).reshape((-1, 3))
            matrix = np.asarray(part.transform, dtype=float).reshape((4, 4), order="F")
            world = local @ matrix[:3, :3].T + matrix[:3, 3]
            world[:, 1] -= mesh.floorY or 0.0
            faces = world[np.asarray(part.triangles, dtype=np.int64).reshape((-1, 3))]
            heights = faces[:, :, 1]
            relevant = faces[(heights.max(axis=1) >= self.MIN_HEIGHT_METERS) & (heights.min(axis=1) <= self.MAX_HEIGHT_METERS)]
            for face in relevant:
                polygon = _clip_height(list(face), self.MIN_HEIGHT_METERS, True)
                polygon = _clip_height(polygon, self.MAX_HEIGHT_METERS, False) if polygon else []
                if polygon:
                    self._polygons.append(np.asarray([(point[0], -point[2]) for point in polygon]))
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        if not self._polygons:
            self._tree = None
            self._maximum_radius = 0.0
            return
        centers = np.asarray([polygon.mean(axis=0) for polygon in self._polygons])
        self._maximum_radius = max(
            float(np.linalg.norm(polygon - center, axis=1).max())
            for polygon, center in zip(self._polygons, centers)
        )
        self._tree = cKDTree(centers)

    def excluding_nodes(self, nodes, margin_meters: float = 0.03) -> MeshCollisionIndex:
        """Return an index without raw faces wholly inside movable footprints.

        A captured mesh keeps furniture at its original location after a graph
        rearrangement. The graph remains authoritative for those objects. Faces
        extending outside a footprint stay in the index so nearby walls and
        unknown obstacles are not erased with the furniture.
        """
        footprints = [
            footprint(node)
            for node in nodes
            if not bounds_the_room(node) and node.movable
        ]
        if not footprints:
            return self
        filtered = [
            polygon
            for polygon in self._polygons
            if not any(
                all(
                    contains_point(node_footprint, tuple(point), margin_meters)
                    for point in polygon
                )
                for node_footprint in footprints
            )
        ]
        clone = object.__new__(MeshCollisionIndex)
        clone._polygons = filtered
        clone._rebuild_tree()
        return clone

    def collides(self, path: list[Vec3], radius_inches: float) -> bool:
        if self._tree is None or not path:
            return False
        points = np.asarray([(point.x, point.y) for point in path], dtype=float)
        radius = to_meters(radius_inches)
        segments = zip(points[:-1], points[1:]) if len(points) > 1 else [(points[0], points[0])]
        for start, end in segments:
            search_radius = radius + self._maximum_radius + float(np.linalg.norm(end - start)) / 2
            for index in self._tree.query_ball_point((start + end) / 2, search_radius):
                polygon = self._polygons[index]
                if _inside(start, polygon) or _inside(end, polygon):
                    return True
                if any(_segments_distance(start, end, a, b) <= radius + 1e-9
                       for a, b in zip(polygon, np.roll(polygon, -1, axis=0))):
                    return True
        return False
