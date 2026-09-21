"""Height-aware body clearance from the saved scene and captured LiDAR mesh.

Each profile is a vertical cylinder. The mesh is conservatively projected only
where triangles intersect its height band. Four-connected paths cannot cut
diagonal corners. This is a navigation/reach approximation, not biomechanics.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.spatial import ConvexHull
from standardphysics_contracts import LidarMesh, SceneGraph, lies_flat
from standardphysics_pipeline import Grid
from standardphysics_pipeline.footprints import footprint


@dataclass(frozen=True)
class BodyProfile:
    id: str
    width: float
    height: float
    eye_height: float
    shoulder_height: float
    arm_length: float


PROFILES = (
    BodyProfile("wheelchair", 0.762, 1.30, 1.15, 0.98, 0.60),
    BodyProfile("wide-wheelchair", 0.9144, 1.35, 1.20, 1.03, 0.60),
    BodyProfile("walker", 0.6604, 1.75, 1.62, 1.40, 0.62),
    BodyProfile("short-reach", 0.6096, 1.45, 1.32, 1.10, 0.42),
)
CELL_SIZE = 0.025
FLOOR_NOISE_BAND = 0.08


@dataclass
class ScanSpace:
    grid: Grid
    clearance: np.ndarray
    traversable: np.ndarray
    components: np.ndarray
    starts: np.ndarray
    profile: BodyProfile
    triangles_considered: int


def floor_polygon(graph: SceneGraph) -> np.ndarray:
    points: list[np.ndarray] = []
    for node in graph.nodes:
        if not lies_flat(node):
            continue
        dims = node.dimensions
        matrix = np.asarray(node.transform.m).reshape(4, 4)
        corners = np.asarray([(x*dims.x/2, y*dims.y/2, z*dims.z/2, 1)
                              for x, y, z in product((-1, 1), repeat=3)])
        points.extend((corners @ matrix.T)[:, :2])
    if len(points) < 3:
        raise ValueError("scan has no measured floor surface")
    unique = np.unique(np.asarray(points), axis=0)
    return unique[ConvexHull(unique).vertices]


def world_triangles(mesh: LidarMesh) -> np.ndarray:
    parts = []
    if mesh.floorY is None:
        raise ValueError("LiDAR floorY is required for height-aware collision checks")
    for part in mesh.parts:
        matrix = np.asarray(part.transform).reshape(4, 4, order="F")
        vertices = np.asarray(part.vertices).reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        # ARKit x/y/z to application x/-z/y, with floor at zero.
        vertices = np.column_stack((vertices[:, 0], -vertices[:, 2], vertices[:, 1]-mesh.floorY))
        parts.append(vertices[np.asarray(part.triangles).reshape(-1, 3)])
    return np.concatenate(parts)


def _pixels(polygon: np.ndarray, origin: np.ndarray, cell_size: float) -> list[tuple[float, float]]:
    return [tuple(point) for point in ((polygon-origin)/cell_size).tolist()]


def build_spaces(graph: SceneGraph, mesh: LidarMesh,
                 profiles: tuple[BodyProfile, ...] = PROFILES,
                 cell_size: float = CELL_SIZE) -> list[ScanSpace]:
    polygon = floor_polygon(graph)
    origin = polygon.min(axis=0)-cell_size*2
    size = np.ceil((polygon.max(axis=0)-origin)/cell_size).astype(int)+2
    triangles = world_triangles(mesh)
    bottom, top = triangles[:, :, 2].min(axis=1), triangles[:, :, 2].max(axis=1)
    spaces = []
    for profile in profiles:
        image = Image.new("1", (int(size[0]), int(size[1])), 1)
        draw = ImageDraw.Draw(image)
        draw.polygon(_pixels(polygon, origin, cell_size), fill=0)
        for node in graph.nodes:
            center = node.transform.position.z
            if node.kind in {"floor", "door", "opening", "window"}:
                continue
            if center-node.dimensions.z/2 >= profile.height or center+node.dimensions.z/2 <= 0:
                continue
            draw.polygon(_pixels(np.asarray(footprint(node)), origin, cell_size), fill=1)
        relevant = triangles[(top >= FLOOR_NOISE_BAND) & (bottom <= profile.height)]
        for face in relevant:
            draw.polygon(_pixels(face[:, :2], origin, cell_size), fill=1)
        occupied = np.asarray(image, dtype=bool)
        # Account for rasterized cell edges rather than obstacle-cell centers.
        clearance = np.maximum(0, ndimage.distance_transform_edt(~occupied)*cell_size-cell_size*np.sqrt(2))
        traversable = (~occupied) & (clearance >= profile.width/2)
        components, _ = ndimage.label(traversable)
        grid = Grid(float(origin[0]), float(origin[1]), cell_size, occupied,
                    np.full(occupied.shape, -1, dtype=np.int32), [])
        starts = np.argwhere(traversable)
        if not len(starts):
            raise ValueError(f"no supported starting cells for {profile.id}; inspect scan coverage")
        spaces.append(ScanSpace(grid, clearance, traversable, components, starts,
                                profile, len(relevant)))
    return spaces
