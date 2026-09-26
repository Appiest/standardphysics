"""Assign measured LiDAR faces to photographed or RoomPlan objects.

Inferred wall and top patches are for display, never for a measured height or
for the denominator of scan coverage. Unidentified faces remain unlabelled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from uuid import UUID

import numpy as np
from scipy import ndimage
from standardphysics_contracts import (
    Mat4,
    SceneGraph,
    SceneNode,
    SurfaceHeight,
    Vec3,
    bounds_the_room,
    lies_flat,
    stands_upright,
)

from .boxes import claimed_by_any, inside, to_local

HEIGHT_BAND_M = 0.025
TOP_REACH_M = 0.25
MIN_TOP_AREA_M2 = 0.02
MIN_TOP_SPAN_M = 0.08
MIN_FLOOR_AREA_M2 = 0.05
MIN_TOP_COVERAGE = 0.15
CEILING_NAMESPACE = uuid.UUID("87b88e42-11dc-53a4-aa79-c5e7e8161c71")
CANDIDATE_NAMESPACE = uuid.UUID("d6a47fd4-7e1f-50bf-9245-559a01d1d062")
MAX_COMPONENT_CELLS = 2_000_000
MAX_CANDIDATES = 80
MIN_CANDIDATE_AREA_M2 = 0.1


@dataclass(frozen=True)
class SurfacePiece:
    owner_id: UUID | None
    label: str | None
    orientation: str
    area_m2: float
    height_m: float
    centre: Vec3
    dimensions: Vec3
    component: int


@dataclass(frozen=True)
class SegmentedSurfaces:
    nodes: list[SceneNode]
    pieces: list[SurfacePiece]
    total_area_m2: float
    before_area_m2: float
    labelled_area_m2: float


def _face_geometry(faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cross = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    length = np.linalg.norm(cross, axis=1)
    area = length / 2
    centres = faces.mean(axis=1)
    vertical = np.divide(np.abs(cross[:, 2]), length, out=np.zeros(len(length)), where=length > 0)
    return centres, area, vertical


def _owners(centres: np.ndarray, graph: SceneGraph) -> np.ndarray:
    assigned = np.full(len(centres), -1, dtype=np.int32)
    nodes = graph.nodes
    order = sorted(range(len(nodes)), key=lambda index: (
        bounds_the_room(nodes[index]),
        nodes[index].dimensions.x * nodes[index].dimensions.y * nodes[index].dimensions.z,
    ))
    for index in order:
        free = np.flatnonzero(assigned < 0)
        if not len(free):
            break
        assigned[free[inside(centres[free], nodes[index], margin=0.03)]] = index
    return assigned


def _floor_level(centres: np.ndarray, area: np.ndarray, vertical: np.ndarray, graph: SceneGraph) -> tuple[float, float] | None:
    floors = [node for node in graph.nodes if lies_flat(node)]
    for floor in floors:
        mask = ((vertical >= 0.8)
                & (np.abs(centres[:, 2] - floor.transform.position.z) <= 0.12))
        top = _dominant_top(centres, area, mask)
        if top is None or top[2] < MIN_FLOOR_AREA_M2:
            continue
        return top[0], top[1]
    return None


def _has_ceiling(graph: SceneGraph, walls: list[SceneNode]) -> bool:
    """A flat sheet above the middle of the walls is a ceiling already in the scan."""
    middle = max(node.transform.position.z for node in walls)
    return any(lies_flat(node) and node.transform.position.z > middle for node in graph.nodes)


def _ceiling(centres: np.ndarray, area: np.ndarray, vertical: np.ndarray, graph: SceneGraph) -> SceneNode | None:
    walls = [node for node in graph.nodes if stands_upright(node)]
    if not walls or _has_ceiling(graph, walls):
        return None
    tops = [node.transform.position.z + node.dimensions.z / 2 for node in walls]
    mask = (vertical >= 0.8) & (centres[:, 2] >= max(tops) - 0.5) & (centres[:, 2] <= max(tops) + 0.5)
    height = _dominant_top(centres, area, mask)
    if height is None or height[2] < 1.0:
        return None
    near = mask & (np.abs(centres[:, 2] - height[0]) <= HEIGHT_BAND_M * 2)
    low, high = np.quantile(centres[near, :2], [0.02, 0.98], axis=0)
    if np.any(high - low < 1.0):
        return None
    centre = (low + high) / 2
    return SceneNode(
        id=uuid.uuid5(CEILING_NAMESPACE, str(graph.scan_id)), kind="ceiling", label="Ceiling",
        raw_category="lidar_ceiling", dimensions=Vec3(x=max(0.05, high[0] - low[0]),
                                                 y=max(0.05, high[1] - low[1]), z=0.03),
        transform=Mat4.translation(float(centre[0]), float(centre[1]), height[0]),
        movable=False, labeled_by="lidar",
    )


def _top_candidates(node: SceneNode, centres: np.ndarray, vertical: np.ndarray) -> np.ndarray:
    local = to_local(centres, node)
    half = np.asarray(node.dimensions.as_tuple()) / 2
    target = node.transform.position.z + half[2]
    return (np.all(np.abs(local[:, :2]) <= half[:2] + 0.03, axis=1)
            & (np.abs(centres[:, 2] - target) <= TOP_REACH_M)
            & (vertical >= 0.8))


def _dominant_top(centres: np.ndarray, area: np.ndarray, mask: np.ndarray) -> tuple[float, float, float] | None:
    heights, weights = centres[mask, 2], area[mask]
    if not len(heights):
        return None
    bands = np.floor(heights / HEIGHT_BAND_M).astype(np.int64)
    unique, inverse = np.unique(bands, return_inverse=True)
    weight_by_band = np.bincount(inverse, weights=weights)
    selected = unique[int(np.argmax(weight_by_band))]
    on_top = mask & (np.abs(centres[:, 2] - (selected + 0.5) * HEIGHT_BAND_M) <= HEIGHT_BAND_M)
    points = centres[on_top]
    support = float(area[on_top].sum())
    if (support < MIN_TOP_AREA_M2 or len(points) < 3
            or np.ptp(points[:, 0]) < MIN_TOP_SPAN_M
            or np.ptp(points[:, 1]) < MIN_TOP_SPAN_M):
        return None
    low, high = np.quantile(points[:, 2], [0.1, 0.9])
    return float(np.median(points[:, 2])), float(max(0.02, (high - low) / 2)), support


def _height(node: SceneNode, centres: np.ndarray, area: np.ndarray, vertical: np.ndarray,
            floor: tuple[float, float] | None) -> SurfaceHeight:
    top = _dominant_top(centres, area, _top_candidates(node, centres, vertical))
    if floor is None or top is None:
        return SurfaceHeight()
    top_z, top_uncertainty, support = top
    if support < MIN_TOP_COVERAGE * node.dimensions.x * node.dimensions.y:
        return SurfaceHeight()
    height = top_z - floor[0]
    if height < 0:
        return SurfaceHeight()
    return SurfaceHeight(height_m=height, uncertainty_m=top_uncertainty + floor[1], support_area_m2=support)


def _pieces(centres: np.ndarray, area: np.ndarray, vertical: np.ndarray,
            owners: np.ndarray, nodes: list[SceneNode], components: np.ndarray) -> list[SurfacePiece]:
    orientation = np.where(vertical >= 0.8, 0, np.where(vertical <= 0.2, 2, 1))
    bands = np.floor(centres[:, 2] / HEIGHT_BAND_M).astype(np.int64)
    keys = np.stack([owners, orientation, np.where(orientation == 0, bands, 0), components], axis=1)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    descriptions = ("horizontal", "sloped", "vertical")
    areas = np.bincount(inverse, weights=area)
    pieces = []
    for index, key in enumerate(unique):
        if areas[index] < MIN_TOP_AREA_M2:
            continue
        points = centres[inverse == index]
        low, high = points.min(axis=0), points.max(axis=0)
        centre = (low + high) / 2
        size = np.maximum(high - low, 0.05)
        pieces.append(SurfacePiece(
            owner_id=nodes[int(key[0])].id if key[0] >= 0 else None,
            label=nodes[int(key[0])].label if key[0] >= 0 else None,
            orientation=descriptions[int(key[1])], area_m2=float(areas[index]),
            height_m=float(np.median(points[:, 2])),
            centre=Vec3(x=float(centre[0]), y=float(centre[1]), z=float(centre[2])),
            dimensions=Vec3(x=float(size[0]), y=float(size[1]), z=float(size[2])),
            component=int(key[3]),
        ))
    return pieces


def _components(centres: np.ndarray, owners: np.ndarray, vertical: np.ndarray) -> np.ndarray:
    groups = np.full(len(centres), -1, dtype=np.int32)
    orientation = np.where(vertical >= 0.8, 0, np.where(vertical <= 0.2, 2, 1))
    next_group = 0
    for direction in range(3):
        selected = np.flatnonzero((owners < 0) & (orientation == direction))
        if not len(selected):
            continue
        cell_size = np.array([0.1, 0.1, 0.05 if direction == 0 else 0.1])
        cells = np.floor(centres[selected] / cell_size).astype(np.int32)
        cells -= cells.min(axis=0)
        shape = tuple((cells.max(axis=0) + 1).tolist())
        if np.prod(shape) > MAX_COMPONENT_CELLS:
            continue
        occupied = np.zeros(shape, dtype=bool)
        occupied[tuple(cells.T)] = True
        labels, count = ndimage.label(occupied, structure=ndimage.generate_binary_structure(3, 3))
        groups[selected] = labels[tuple(cells.T)] + next_group
        next_group += count
    return groups


def _candidate_nodes(pieces: list[SurfacePiece], graph: SceneGraph,
                     floor_z: float | None) -> list[SceneNode]:
    candidates = []
    for index in sorted(range(len(pieces)), key=lambda item: -pieces[item].area_m2):
        piece = pieces[index]
        if len(candidates) >= MAX_CANDIDATES:
            break
        if piece.owner_id is not None or piece.component < 0 or piece.area_m2 < MIN_CANDIDATE_AREA_M2:
            continue
        if floor_z is not None and piece.orientation == "horizontal" and abs(piece.height_m - floor_z) < 0.12:
            continue
        candidates.append(SceneNode(
            id=uuid.uuid5(CANDIDATE_NAMESPACE, f"{graph.scan_id}:{piece.orientation}:{piece.centre.as_tuple()}"),
            kind="surface_candidate", label=f"Unidentified {piece.orientation} surface",
            raw_category="lidar_candidate", dimensions=piece.dimensions,
            transform=Mat4.translation(*piece.centre.as_tuple()), quality="needs_another_look",
            movable=False, labeled_by="lidar",
        ))
        pieces[index] = replace(piece, owner_id=candidates[-1].id)
    return candidates


def segment_surfaces(faces: np.ndarray, graph: SceneGraph, before: SceneGraph | None = None) -> SegmentedSurfaces:
    """Segment original scanned faces; photo-discovered nodes can supply their names."""
    graph = graph.model_copy(update={"nodes": [node for node in graph.nodes if node.raw_category != "lidar_candidate"]})
    if not len(faces):
        return SegmentedSurfaces(graph.nodes, [], 0.0, 0.0, 0.0)
    centres, area, vertical = _face_geometry(faces)
    original = before or graph
    original = original.model_copy(update={
        "nodes": [node for node in original.nodes if node.raw_category != "lidar_candidate"]
    })
    before_area = float(area[claimed_by_any(centres, original)].sum())
    floor = _floor_level(centres, area, vertical, graph)
    ceiling = _ceiling(centres, area, vertical, graph)
    graph = graph.model_copy(update={"nodes": [*graph.nodes, ceiling]}) if ceiling is not None else graph
    owners = _owners(centres, graph)
    if floor is not None:
        floor_index = next((index for index, node in enumerate(graph.nodes) if lies_flat(node)), None)
        if floor_index is not None:
            floor_faces = (owners < 0) & (vertical >= 0.8) & (np.abs(centres[:, 2] - floor[0]) <= 0.06)
            owners[floor_faces] = floor_index
    nodes = [
        node if bounds_the_room(node) else node.model_copy(update={"top_surface": _height(node, centres, area, vertical, floor)})
        for node in graph.nodes
    ]
    pieces = _pieces(centres, area, vertical, owners, nodes, _components(centres, owners, vertical))
    candidates = _candidate_nodes(pieces, graph, floor[0] if floor else None)
    return SegmentedSurfaces([*nodes, *candidates], pieces,
                             float(area.sum()), before_area, float(area[owners >= 0].sum()))
