"""Completing half-seen furniture from its own other half.

A chair against a wall shows the scanner one side, and a sofa seen from the room
shows its front and one arm. Most furniture is the same on the left as on the
right, so the side the scanner saw says what the other looks like. Whether an
object is symmetric is not read off its name: its scanned points are reflected
across each upright plane through its measured box and scored two ways. Agreement
is how much of the reflection lands on scanned surface. Contradiction is how much
lands in space a camera saw straight through to something behind, which is known
to be empty. A chair reflected front to back puts its backrest where every photo
saw the seat and the floor, so that plane fails, while left to right lands on the
other armrest or behind the chair where no camera looked. Reflected surface is
added only where the scan has none and no camera saw through, and later takes
the colour of the point it was reflected from.

Display only, and marked as inferred like every other added surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

from .project import CameraArray
from .regions import TrianglesByCorner, VertexIndex
from .stages import advanced

OBJECT_REACH = 0.03
"""How far outside its box a scanned vertex may sit and still belong to the object."""
AGREEMENT_DISTANCE = 0.03
"""How close a reflected point must land to scanned surface to agree with it."""
MIN_AGREEMENT = 0.4
"""The share of reflected points that must land on scanned surface for a plane to count."""
MIN_MARGIN = 0.15
"""How far agreement must exceed contradiction: an object that is only as similar as it is contradicted is not symmetric."""
MIN_POINTS = 150
"""Scanned points an object needs, above the floor, before its symmetry is judged."""
ABOVE_FLOOR = 0.05
"""Points this close to the floor are left out of the verdict: floor is symmetric in every direction."""
SEEN_THROUGH_MARGIN = 0.08
"""How much farther than a point a camera must have seen for that point to count as empty space."""
MIN_SEEN_THROUGH_VIEWS = 2
"""Cameras that must each have seen past a point before it counts as empty.

One camera is not enough: its depth reading slips past a thin chair leg or
through a gap in the scan often enough that a single view blocked most of a
chair's hidden side. Two independent views agreeing is far harder to fake.
"""
OFFSETS = np.linspace(-0.08, 0.08, 9)
"""Where the plane may sit relative to the box centre, since a measured box is not always centred."""
MISSING_DISTANCE = 0.05
"""A reflected triangle is added only where no scanned vertex lies this close to any of its corners."""

SeenThrough = Callable[[np.ndarray], np.ndarray]
"""Which of the given room-frame points some camera saw past, so they are known to be empty."""


def seen_through_by(
    cameras, depth_buffers: list[np.ndarray], min_views: int = MIN_SEEN_THROUGH_VIEWS,
) -> SeenThrough:
    """Points enough cameras saw beyond: the surface each recorded along that ray lies well behind them.

    Only the cameras whose frame reaches the points are asked. Each question is
    about one piece of furniture, and a walk of a library floor has thousands of
    photos, nearly all of them pointed somewhere else.
    """
    rig = CameraArray(cameras)
    stacked = _stacked(depth_buffers)

    def seen_through(points: np.ndarray) -> np.ndarray:
        views = np.zeros(len(points), dtype=np.int32)
        if not len(points):
            return views >= min_views
        centre = (points.min(axis=0) + points.max(axis=0)) / 2
        radius = float(np.linalg.norm(points - centre, axis=1).max())
        reaching = rig.reaching(centre, radius)
        if stacked is not None and len(reaching):
            step = max(1, CAMERA_POINT_PAIRS // len(points))
            for start in range(0, len(reaching), step):
                views += _views_beyond(points, rig, stacked, reaching[start:start + step])
            return views >= min_views
        for index in reaching:
            views += _seen_beyond(points, cameras[index], depth_buffers[index])
        return views >= min_views

    return seen_through


CAMERA_POINT_PAIRS = 2_000_000
"""Camera and point pairs worked on at once, so a large object seen by hundreds of photos stays near a hundred megabytes."""


def _stacked(depth_buffers: list[np.ndarray]) -> np.ndarray | None:
    """The buffers as one array when they share a size, as every photo of one phone's walk does."""
    shapes = {buffer.shape for buffer in depth_buffers}
    return np.stack(depth_buffers) if len(shapes) == 1 else None


def _views_beyond(points: np.ndarray, rig: CameraArray, stacked: np.ndarray, reaching: np.ndarray) -> np.ndarray:
    """How many of the reaching cameras saw beyond each point, all cameras at once.

    Mirroring asks this for every candidate plane of every object, a few
    hundred points at a time; one camera at a time it was two hundred thousand
    small calls on a library floor.
    """
    rotation, translation = rig.rotations[reaching], rig.translations[reaching]

    def along(axis: int) -> np.ndarray:
        return (rotation[:, axis, 0, None] * points[:, 0] + rotation[:, axis, 1, None] * points[:, 1]
                + rotation[:, axis, 2, None] * points[:, 2] + translation[:, axis, None])

    across, down, depth = along(0), along(1), along(2)
    safe = np.where(np.abs(depth) < 1e-9, 1e-9, depth)
    fx, fy, cx, cy, width, height = (column[:, None] for column in rig.lens[reaching].T)
    columns, rows = fx * across / safe + cx, fy * down / safe + cy
    in_view = (depth > 0.2) & (columns >= 0) & (columns < width) & (rows >= 0) & (rows < height)
    buffer_height, buffer_width = stacked.shape[1:]
    recorded = stacked[
        reaching[:, None],
        np.clip((rows * buffer_height / height).astype(np.int64), 0, buffer_height - 1),
        np.clip((columns * buffer_width / width).astype(np.int64), 0, buffer_width - 1),
    ]
    return (in_view & np.isfinite(recorded) & (recorded > depth + SEEN_THROUGH_MARGIN)).sum(axis=0)


def _seen_beyond(points: np.ndarray, camera, buffer: np.ndarray) -> np.ndarray:
    columns, rows, depth = camera.project(points)
    in_view = (depth > 0.2) & (columns >= 0) & (columns < camera.width) & (rows >= 0) & (rows < camera.height)
    height, width = buffer.shape
    recorded = buffer[
        np.clip((rows * height / camera.height).astype(np.int64), 0, height - 1),
        np.clip((columns * width / camera.width).astype(np.int64), 0, width - 1),
    ]
    return in_view & np.isfinite(recorded) & (recorded > depth + SEEN_THROUGH_MARGIN)


@dataclass(frozen=True)
class MirrorPlane:
    node_index: int
    axis: int
    """Which of the node's own axes the reflection flips."""
    offset: float
    margin: float
    """Agreement minus contradiction for the chosen plane."""


@dataclass(frozen=True)
class Completed:
    vertices: np.ndarray
    triangles: np.ndarray
    added: np.ndarray
    """Whether each vertex was reflected in to complete an object."""
    source: np.ndarray
    """For each reflected vertex, the vertex it was reflected from; -1 for everything else."""
    planes: list[MirrorPlane]


def _frame(node: SceneNode) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    return matrix[:3, :3], matrix[:3, 3], half


def _upright_planes(rotation: np.ndarray) -> list[int]:
    """The node's own axes that run horizontally, so reflecting across them keeps the floor below."""
    return [axis for axis in range(3) if abs(rotation[2, axis]) < 0.5]


def _reflected(local: np.ndarray, axis: int, offset: float) -> np.ndarray:
    mirrored = local.copy()
    mirrored[:, axis] = 2 * offset - mirrored[:, axis]
    return mirrored


def _score(own: np.ndarray, tree: cKDTree, to_room, seen_through: SeenThrough, axis: int, offset: float) -> tuple[float, float]:
    """Agreement and contradiction of one reflection, each as a share of the reflected points."""
    mirrored = _reflected(own, axis, offset)
    agreement = float((tree.query(mirrored)[0] <= AGREEMENT_DISTANCE).mean())
    contradiction = float(seen_through(to_room(mirrored)).mean())
    return agreement, contradiction


@dataclass(frozen=True)
class _IndexedScan:
    """The scan indexed once, so each object reads only the vertices and triangles around its own box.

    Testing every vertex of a merged floor against every object, and building a
    fresh search tree over the whole floor for every plane, was most of ten
    minutes on a small server. The candidates are a superset of what each exact
    test keeps and come back in ascending order, so every result is unchanged.
    """

    vertices: np.ndarray
    triangles: np.ndarray
    regions: VertexIndex
    by_corner: TrianglesByCorner
    tree: cKDTree

    @classmethod
    def of(cls, vertices: np.ndarray, triangles: np.ndarray) -> _IndexedScan:
        return cls(vertices, triangles, VertexIndex(vertices), TrianglesByCorner(triangles), cKDTree(vertices))

    def near(self, node: SceneNode) -> np.ndarray:
        return self.regions.near_box(node, OBJECT_REACH)

    def distance_to_surface(self, added: list[np.ndarray]) -> Callable[[np.ndarray], np.ndarray]:
        """Distance from each point to the nearest scanned or already reflected vertex.

        The nearest point of a union is the nearer of the nearest in each part,
        so the floor's tree is built once and only the reflected vertices, which
        are few, are searched afresh.
        """
        added_tree = cKDTree(np.concatenate(added)) if added else None

        def distance(points: np.ndarray) -> np.ndarray:
            nearest = self.tree.query(points)[0]
            return nearest if added_tree is None else np.minimum(nearest, added_tree.query(points)[0])

        return distance


def symmetry_planes(index: int, node: SceneNode, scan: _IndexedScan, seen_through: SeenThrough) -> list[MirrorPlane]:
    """Every upright plane the object is symmetric across, best first, with its best offset.

    More than one can hold: a table is the same side to side and front to back.
    Only some fill anything, since a plane that maps the scanned half onto
    itself adds nothing, so all of them are returned rather than the best alone.
    """
    rotation, centre, half = _frame(node)
    candidates = scan.vertices[scan.near(node)]
    local = (candidates - centre) @ rotation
    own = local[np.all(np.abs(local) <= half + OBJECT_REACH, axis=1) & (candidates[:, 2] > ABOVE_FLOOR)]
    if len(own) < MIN_POINTS:
        return []
    tree = cKDTree(own)

    def to_room(points):
        return points @ rotation.T + centre

    planes = []
    for axis in _upright_planes(rotation):
        best = None
        for offset in OFFSETS * min(1.0, half[axis] / 0.3):
            agreement, contradiction = _score(own, tree, to_room, seen_through, axis, float(offset))
            margin = agreement - contradiction
            if agreement >= MIN_AGREEMENT and margin >= MIN_MARGIN and (best is None or margin > best.margin):
                best = MirrorPlane(index, axis, float(offset), margin)
        if best is not None:
            planes.append(best)
    return sorted(planes, key=lambda plane: -plane.margin)


def _reflected_triangles(
    plane: MirrorPlane, node: SceneNode, scan: _IndexedScan,
    distance_to_surface: Callable[[np.ndarray], np.ndarray], seen_through: SeenThrough,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reflected copies of the object's triangles that land where nothing was scanned or seen through.

    Reflection turns a triangle inside out, so each copy's winding is reversed
    to keep it facing outward.
    """
    rotation, centre, half = _frame(node)
    near = scan.near(node)
    local = (scan.vertices[near] - centre) @ rotation
    members = near[np.all(np.abs(local) <= half + OBJECT_REACH, axis=1)]
    own = scan.triangles[scan.by_corner.all_corners_in(members, len(scan.vertices))]
    if not len(own):
        return np.empty((0, 3)), np.empty((0, 3), dtype=np.int64), np.empty(0, dtype=np.int64)
    used = np.unique(own)
    reflected = _reflected(local[np.searchsorted(near, used)], plane.axis, plane.offset)
    mirrored = reflected @ rotation.T + centre
    missing = (distance_to_surface(mirrored) > MISSING_DISTANCE) & ~seen_through(mirrored)
    within = np.all(np.abs(reflected) <= half + OBJECT_REACH, axis=1)
    corners = np.searchsorted(used, own)
    keep = (missing & within)[corners].all(axis=1)
    return mirrored, corners[keep][:, ::-1], used


def mirrored_completion(
    vertices: np.ndarray, triangles: np.ndarray, graph: SceneGraph, seen_through: SeenThrough,
) -> Completed:
    """The mesh with every symmetric object's unscanned parts reflected in from its scanned parts.

    Each plane fills only what the scan and the planes before it left empty, so
    two planes never lay the same missing corner twice.
    """
    new_vertices, new_triangles, sources, planes = [], [], [], []
    next_index = len(vertices)
    scan = _IndexedScan.of(vertices, triangles)
    for index, node in enumerate(graph.nodes):
        advanced(index + 1, len(graph.nodes))
        if bounds_the_room(node):
            continue
        for plane in symmetry_planes(index, node, scan, seen_through):
            distance = scan.distance_to_surface(new_vertices)
            mirrored, faces, used = _reflected_triangles(plane, node, scan, distance, seen_through)
            if not len(faces):
                continue
            kept = np.unique(faces)
            remap = np.full(len(mirrored), -1, dtype=np.int64)
            remap[kept] = next_index + np.arange(len(kept))
            new_vertices.append(mirrored[kept])
            new_triangles.append(remap[faces])
            sources.append(used[kept])
            next_index += len(kept)
            planes.append(plane)
    unchanged = np.full(len(vertices), -1, dtype=np.int64)
    return Completed(
        vertices=np.concatenate([vertices, *new_vertices]),
        triangles=np.concatenate([triangles, *new_triangles]).astype(np.int64),
        added=np.arange(next_index) >= len(vertices),
        source=np.concatenate([unchanged, *sources]),
        planes=planes,
    )
