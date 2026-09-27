"""Taking people out of the displayed scan and closing the holes they leave in furniture.

People were in the room while it was scanned. The LiDAR recorded them as surface
and the photos coloured the furniture with their clothes: on one office a person
sat in a chair in most of the photos and was an eighth of the scan. Discovery
already finds the points a person occupied, so the picture of the room drops
them. The seat under a person was hidden from the scanner too, so what is left
is a hole in the chair; a hole small enough to be one gap in one object is
closed across its rim, the way a seat or a backrest runs on under a person.

Display only, like every patch: checks measure the LiDAR as captured, where
discovery removes people by the same rule before anything is measured.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room

from .stages import advanced

MAX_RIM_METRES = 2.5
"""The longest rim closed: a seat or a backrest, not the open side of a room."""
MAX_RIM_VERTICES = 600
OBJECT_REACH = 0.10
CAP_RING_SPACING = 0.03
"""How far apart, in metres, the rings closing a hole are: about one photo sample at room distance."""
"""How far outside an object's box a hole's centre may be and still be that object's."""


@dataclass(frozen=True)
class ClosedHoles:
    vertices: np.ndarray
    triangles: np.ndarray
    inferred: np.ndarray
    """Whether each vertex was added to close a hole."""
    closed: int


def without_vertices(vertices: np.ndarray, triangles: np.ndarray, drop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The mesh without the dropped vertices and every triangle that used one."""
    kept_triangles = triangles[~drop[triangles].any(axis=1)]
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[~drop] = np.arange(int((~drop).sum()))
    return vertices[~drop], remap[kept_triangles]


def _rims(triangles: np.ndarray) -> list[list[int]]:
    """Every closed loop of edges used by only one triangle, walked the way a filling triangle runs.

    A triangle a, b, c owns the directed edges a to b, b to c and c to a. An edge
    with no triangle running the other way is on a rim, and the triangle that
    closes the hole must run it backwards, so the walk follows reversed edges.
    """
    following = _rim_steps(triangles)
    rims, visited = [], set()
    for start in following:
        if start in visited:
            continue
        rim, current = [], start
        while current not in visited and current in following and len(rim) <= MAX_RIM_VERTICES:
            visited.add(current)
            rim.append(current)
            current = following[current]
        if current == start and len(rim) >= 3:
            rims.append(rim)
    return rims


def _rim_steps(triangles: np.ndarray) -> dict[int, int]:
    """For each vertex on a rim, the vertex the walk goes to next, in the order the rim edges first appear.

    A floor's mesh has millions of edges and only a few thousand on rims, so
    the edges are matched with their reverses as whole arrays: each directed
    edge becomes one integer, and an edge is on a rim when its reverse's
    integer is absent. Where a vertex starts more than one rim edge, the first
    in edge order is kept.
    """
    if not len(triangles):
        return {}
    directed = triangles[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2).astype(np.int64)
    count = int(directed.max()) + 1
    owned = np.unique(directed[:, 0] * count + directed[:, 1])
    reverse = directed[:, 1] * count + directed[:, 0]
    found = np.searchsorted(owned, reverse)
    unmatched = owned[np.minimum(found, len(owned) - 1)] != reverse
    rim_edges = directed[unmatched]
    _, first = np.unique(rim_edges[:, 1], return_index=True)
    kept = rim_edges[np.sort(first)]
    return dict(zip(kept[:, 1].tolist(), kept[:, 0].tolist()))


def _perimeter(points: np.ndarray) -> float:
    return float(np.linalg.norm(points - np.roll(points, -1, axis=0), axis=1).sum())


def _inside_objects(points: np.ndarray, objects: list[SceneNode]) -> np.ndarray:
    """Whether each point lies within reach of some object's box.

    A library floor has thousands of small rims and hundreds of objects, and
    asking object by object for each rim was two million little array calls.
    Each object is asked about every point at once instead.
    """
    inside = np.zeros(len(points), dtype=bool)
    for node in objects:
        matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
        half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
        inside |= np.all(np.abs((points - matrix[:3, 3]) @ matrix[:3, :3]) <= half + OBJECT_REACH, axis=1)
    return inside


def _cap(rim: list[int], points: np.ndarray, first_new: int) -> tuple[np.ndarray, list[list[int]]]:
    """Rings shrinking toward the centre every few centimetres, stitched from the rim inward.

    A photo colours each ring vertex where it lands, so the cap carries as much
    detail as the photos have. With one centre point instead, every colour
    stretched along a spoke and a screen came out as a starburst.
    """
    centre = points.mean(axis=0)
    radius = float(np.linalg.norm(points - centre, axis=1).max())
    ring_count = max(1, int(np.ceil(radius / CAP_RING_SPACING)))
    count = len(rim)
    rings = [list(rim)]
    new_points = []
    for step in range(1, ring_count):
        towards = step / ring_count
        new_points.append(points + (centre - points) * towards)
        rings.append([first_new + (step - 1) * count + i for i in range(count)])
    centre_index = first_new + (ring_count - 1) * count
    faces = []
    for outer, inner in zip(rings, rings[1:]):
        for i in range(count):
            j = (i + 1) % count
            faces.append([outer[i], outer[j], inner[j]])
            faces.append([outer[i], inner[j], inner[i]])
    innermost = rings[-1]
    faces.extend([innermost[i], innermost[(i + 1) % count], centre_index] for i in range(count))
    return np.vstack([*new_points, centre[None, :]]), faces


def closed_object_holes(vertices: np.ndarray, triangles: np.ndarray, graph: SceneGraph) -> ClosedHoles:
    """The mesh with every small hole inside a labelled object capped."""
    objects = [node for node in graph.nodes if not bounds_the_room(node)]
    added_vertices, added_faces, closed = [], [], 0
    next_index = len(vertices)
    rims = _rims(triangles) if objects else []
    small = [rim for rim in rims if _perimeter(vertices[rim]) <= MAX_RIM_METRES]
    inside = _inside_objects(np.array([vertices[rim].mean(axis=0) for rim in small]).reshape(-1, 3), objects)
    for number, (rim, capped) in enumerate(zip(small, inside), start=1):
        advanced(number, len(small))
        if not capped:
            continue
        points = vertices[rim]
        cap_vertices, cap_faces = _cap(rim, points, next_index)
        added_vertices.append(cap_vertices)
        added_faces.extend(cap_faces)
        next_index += len(cap_vertices)
        closed += 1
    if not closed:
        return ClosedHoles(vertices, triangles, np.zeros(len(vertices), dtype=bool), 0)
    new_vertices = np.concatenate(added_vertices)
    return ClosedHoles(
        vertices=np.concatenate([vertices, new_vertices]),
        triangles=np.concatenate([triangles, np.asarray(added_faces, dtype=np.int64)]),
        inferred=np.concatenate([np.zeros(len(vertices), dtype=bool), np.ones(len(new_vertices), dtype=bool)]),
        closed=closed,
    )


def people_masks(
    detections: dict[str, list], cameras: list, image_shapes: dict[str, tuple[int, int]],
) -> dict[str, np.ndarray]:
    """Per photo, a mask that is 1 everywhere except where the detector saw a person.

    Masks are at each loaded photo's own size; detection boxes are in the stored
    sensor pixels the camera was calibrated in, so they are scaled across.
    """
    masks: dict[str, np.ndarray] = {}
    for camera in cameras:
        height, width = image_shapes[camera.frame_id]
        mask = np.ones((height, width), dtype=np.float32)
        scale_x, scale_y = width / camera.width, height / camera.height
        for detection in detections.get(camera.frame_id, []):
            if not detection.is_person:
                continue
            left, top, right, bottom = detection.box
            mask[
                max(0, int(top * scale_y)):min(height, int(np.ceil(bottom * scale_y))),
                max(0, int(left * scale_x)):min(width, int(np.ceil(right * scale_x))),
            ] = 0.0
        masks[camera.frame_id] = mask
    return masks
