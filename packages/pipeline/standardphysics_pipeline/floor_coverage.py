"""Which floor the scan actually saw, as a grid a rearrangement can be held to.

RoomPlan reports a floor as one convex sheet, and the sheet covers corners the
phone never pointed at. The LiDAR mesh is the record of what it did point at.
A cell of the grid is observed when the mesh has a face lying down at floor
height over it, or when a piece the scan measured stood over it: floor hidden
under a display case was still seen, as floor with a display case on it.

The grid is measured once, at ingest, in the room frame of that moment, and
rides on the graph. Reading it back maps a point through the floor's own
movement since then, so placing a room elsewhere moves its grid with it.
"""

from __future__ import annotations

import base64
import functools
import math
import pathlib
import zlib
from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import FloorCoverage, LidarMesh, Mat4, SceneGraph, SceneNode, lies_flat

from .footprints import floor_polygon, footprint, polygon_bounds, rotation_about_z
from .lidar import into_room, load_mesh, triangles_in_arkit_world
from .occupancy import CELL_SIZE, blocks_floor

METHOD = 1
"""Bump when the rules below change, so a grid drawn by older rules is recognisable."""

FLOOR_BAND = 0.05
"""Metres above or below a floor sheet a mesh face may sit and still be that floor.

The LiDAR mesh is noisy by a centimetre or two and RoomPlan's floor plane is
fitted separately from it. A shelf's bottom board is well clear of 5 cm.
"""

LYING_DOWN = math.cos(math.radians(45.0))
"""How upright a face's normal must be for the face to count as floor.

The mesh rounds the join between floor and wall into a fillet a few
centimetres across. Faces tilted up to 45 degrees keep the floor running right
to the foot of the wall; a wall face is tilted 90 and never qualifies.
"""

UPPER_STOREY = 1.0
"""Metres above the lowest floor sheet beyond which a flat sheet is a ceiling."""

MOST_SUBDIVISIONS = 64
"""The most cells one face's edge may span before the face is split in four.

A face is sampled on a lattice a cell apart, which costs the square of its
length in cells. Splitting a large face first keeps each lattice small without
ever sampling it more coarsely than a cell.
"""


def with_floor_coverage(graph: SceneGraph, mesh_path: pathlib.Path, mesh_sha256: str) -> SceneGraph:
    """The graph carrying its floor coverage, or unchanged when the mesh cannot be read."""
    mesh = load_mesh(mesh_path)
    if mesh is None:
        return graph
    return graph.model_copy(update={"floor_coverage": measure_floor_coverage(graph, mesh, mesh_sha256)})


def measure_floor_coverage(graph: SceneGraph, mesh: LidarMesh, mesh_sha256: str) -> list[FloorCoverage]:
    """One grid per floor sheet, from a mesh already read, or none without a room frame."""
    if graph.capture_to_room is None:
        return []
    faces = into_room(triangles_in_arkit_world(mesh), graph.capture_to_room).astype(np.float64)
    standing = [node for node in graph.contents() if blocks_floor(node)]
    return [_measure_one(floor, faces, standing, mesh_sha256) for floor in floor_sheets(graph)]


def floor_sheets(graph: SceneGraph) -> list[SceneNode]:
    ground = graph.ground()
    if ground is None:
        return []
    lowest = ground.transform.position.z
    return [node for node in graph.nodes if lies_flat(node) and node.transform.position.z < lowest + UPPER_STOREY]


def _measure_one(floor: SceneNode, faces: np.ndarray, standing: list[SceneNode], mesh_sha256: str) -> FloorCoverage:
    min_x, min_y, max_x, max_y = polygon_bounds(floor_polygon(floor))
    columns = max(1, math.ceil((max_x - min_x) / CELL_SIZE))
    rows = max(1, math.ceil((max_y - min_y) / CELL_SIZE))
    layout = _Layout(min_x, min_y, rows, columns)
    observed = np.zeros((rows, columns), dtype=bool)
    _mark_points(observed, layout, _floor_samples(faces, floor.transform.position.z))
    for node in standing:
        _mark_under(observed, layout, node)
    return FloorCoverage(
        floor_id=floor.id,
        anchor=floor.transform,
        origin_x=min_x,
        origin_y=min_y,
        cell_size=CELL_SIZE,
        columns=columns,
        rows=rows,
        observed=encode_cells(observed),
        mesh_sha256=mesh_sha256,
        method=METHOD,
    )


@dataclass(frozen=True)
class _Layout:
    origin_x: float
    origin_y: float
    rows: int
    columns: int

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return math.floor((x - self.origin_x) / CELL_SIZE), math.floor((y - self.origin_y) / CELL_SIZE)


def _floor_samples(faces: np.ndarray, floor_z: float) -> np.ndarray:
    """Points spread over every face lying down at this floor's height, no further apart than a cell."""
    if not len(faces):
        return np.empty((0, 2))
    near = np.all(np.abs(faces[:, :, 2] - floor_z) <= FLOOR_BAND, axis=1)
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lying = np.abs(normals[:, 2]) >= LYING_DOWN * np.maximum(lengths, 1e-12)
    floor_faces = _split_large(faces[near & lying & (lengths > 0)][:, :, :2])
    if not len(floor_faces):
        return np.empty((0, 2))
    steps = np.ceil(_longest_edges(floor_faces) / CELL_SIZE).astype(int).clip(min=1)
    return np.concatenate([_lattice(floor_faces[steps == n], n) for n in np.unique(steps)])


def _longest_edges(faces: np.ndarray) -> np.ndarray:
    return np.max(np.linalg.norm(faces - np.roll(faces, 1, axis=1), axis=2), axis=1)


def _split_large(faces: np.ndarray) -> np.ndarray:
    """Faces longer than `MOST_SUBDIVISIONS` cells, quartered at their midpoints until none is."""
    large = _longest_edges(faces) > MOST_SUBDIVISIONS * CELL_SIZE if len(faces) else np.zeros(0, dtype=bool)
    if not large.any():
        return faces
    a, b, c = faces[large, 0], faces[large, 1], faces[large, 2]
    ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
    quarters = np.concatenate([np.stack(corners, axis=1) for corners in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    return np.concatenate([faces[~large], _split_large(quarters)])


def _lattice(faces: np.ndarray, steps: int) -> np.ndarray:
    """Barycentric points i/steps, j/steps over each face, corners included."""
    weights = np.asarray([(i / steps, j / steps) for i in range(steps + 1) for j in range(steps + 1 - i)])
    first, second = faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0]
    points = faces[:, None, 0] + weights[None, :, :1] * first[:, None] + weights[None, :, 1:] * second[:, None]
    return points.reshape(-1, 2)


def _mark_points(observed: np.ndarray, layout: _Layout, points: np.ndarray) -> None:
    if not len(points):
        return
    columns = np.floor((points[:, 0] - layout.origin_x) / CELL_SIZE).astype(int)
    rows = np.floor((points[:, 1] - layout.origin_y) / CELL_SIZE).astype(int)
    inside = (columns >= 0) & (columns < layout.columns) & (rows >= 0) & (rows < layout.rows)
    observed[rows[inside], columns[inside]] = True


def _mark_under(observed: np.ndarray, layout: _Layout, node: SceneNode) -> None:
    """Mark cells whose centre lies inside the node's footprint, tested in the node's own frame.

    Only the cells inside the footprint's bounding box are tested, so a room
    full of small pieces costs what their footprints cover rather than the
    whole floor once per piece.
    """
    low_x, low_y, high_x, high_y = polygon_bounds(footprint(node))
    first_column, first_row = layout.cell_of(low_x, low_y)
    last_column, last_row = layout.cell_of(high_x, high_y)
    columns = np.arange(max(first_column, 0), min(last_column + 1, layout.columns))
    rows = np.arange(max(first_row, 0), min(last_row + 1, layout.rows))
    if not len(columns) or not len(rows):
        return
    xs, ys = np.meshgrid(layout.origin_x + (columns + 0.5) * CELL_SIZE, layout.origin_y + (rows + 0.5) * CELL_SIZE)
    cos_t, sin_t = rotation_about_z(node)
    centre = node.transform.position
    dx, dy = xs - centre.x, ys - centre.y
    local_x, local_y = dx * cos_t + dy * sin_t, -dx * sin_t + dy * cos_t
    under = (np.abs(local_x) <= node.dimensions.x / 2) & (np.abs(local_y) <= node.dimensions.y / 2)
    observed[rows[0] : rows[-1] + 1, columns[0] : columns[-1] + 1] |= under


def encode_cells(observed: np.ndarray) -> str:
    return base64.b64encode(zlib.compress(np.packbits(observed.astype(bool), axis=None).tobytes(), 9)).decode("ascii")


@functools.lru_cache(maxsize=64)
def decode_cells(encoded: str, rows: int, columns: int) -> np.ndarray:
    """The observed grid, decoded once per grid however many candidates are checked against it."""
    packed = np.frombuffer(zlib.decompress(base64.b64decode(encoded)), dtype=np.uint8)
    cells = np.unpackbits(packed, count=rows * columns).astype(bool).reshape(rows, columns)
    cells.setflags(write=False)
    return cells


def _yaw(transform: Mat4) -> float:
    return math.atan2(transform.m[4], transform.m[0])


@dataclass(frozen=True)
class _Sheet:
    coverage: FloorCoverage
    observed: np.ndarray
    turn: float
    """How far the floor has turned since the grid was measured, in radians."""
    shift: tuple[float, float]
    """Where the measured frame's origin has moved to, after that turn."""

    @classmethod
    def of(cls, coverage: FloorCoverage, floor: SceneNode) -> _Sheet:
        anchor, now = coverage.anchor.m, floor.transform.m
        turn = _yaw(floor.transform) - _yaw(coverage.anchor)
        cos_t, sin_t = math.cos(turn), math.sin(turn)
        shift = (now[3] - (cos_t * anchor[3] - sin_t * anchor[7]), now[7] - (sin_t * anchor[3] + cos_t * anchor[7]))
        observed = decode_cells(coverage.observed, coverage.rows, coverage.columns)
        return cls(coverage=coverage, observed=observed, turn=turn, shift=shift)

    def seen(self, points: np.ndarray) -> np.ndarray:
        """Which points, in the room as it is now, land on a cell this grid saw."""
        cos_t, sin_t = math.cos(self.turn), math.sin(self.turn)
        dx, dy = points[:, 0] - self.shift[0], points[:, 1] - self.shift[1]
        measured_x, measured_y = dx * cos_t + dy * sin_t, -dx * sin_t + dy * cos_t
        grid = self.coverage
        columns = np.floor((measured_x - grid.origin_x) / grid.cell_size).astype(int)
        rows = np.floor((measured_y - grid.origin_y) / grid.cell_size).astype(int)
        inside = (columns >= 0) & (columns < grid.columns) & (rows >= 0) & (rows < grid.rows)
        seen = np.zeros(len(points), dtype=bool)
        seen[inside] = self.observed[rows[inside], columns[inside]]
        return seen


class ObservedFloor:
    """The floor a graph's scan saw, read back for asking about a footprint."""

    def __init__(self, sheets: list[_Sheet]):
        self._sheets = sheets

    @classmethod
    def of(cls, graph: SceneGraph) -> ObservedFloor | None:
        """None when the graph carries no grid for any floor it still has."""
        floors = {node.id: node for node in graph.nodes}
        sheets = [
            _Sheet.of(coverage, floors[coverage.floor_id])
            for coverage in graph.floor_coverage
            if coverage.floor_id in floors
        ]
        return cls(sheets) if sheets else None

    def unseen_share(self, node: SceneNode) -> float:
        """The fraction of the node's footprint standing on floor no grid saw.

        A point beyond every grid counts as unseen: no floor the scan measured
        is there at all.
        """
        points = footprint_samples(node, self._sheets[0].coverage.cell_size)
        seen = np.zeros(len(points), dtype=bool)
        for sheet in self._sheets:
            seen |= sheet.seen(points)
        return float(1.0 - seen.mean())


def footprint_samples(node: SceneNode, spacing: float) -> np.ndarray:
    """Points a cell apart across the node's footprint, at least one, in room coordinates."""
    counts = [max(1, math.ceil(extent / spacing)) for extent in (node.dimensions.x, node.dimensions.y)]
    local_x = (np.arange(counts[0]) + 0.5) / counts[0] * node.dimensions.x - node.dimensions.x / 2
    local_y = (np.arange(counts[1]) + 0.5) / counts[1] * node.dimensions.y - node.dimensions.y / 2
    grid_x, grid_y = (axis.ravel() for axis in np.meshgrid(local_x, local_y))
    cos_t, sin_t = rotation_about_z(node)
    centre = node.transform.position
    return np.column_stack((centre.x + grid_x * cos_t - grid_y * sin_t, centre.y + grid_x * sin_t + grid_y * cos_t))
