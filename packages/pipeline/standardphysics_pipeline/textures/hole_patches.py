"""Patching the holes the LiDAR left in a room with the planes they lie on.

A phone's LiDAR misses dark, shiny and distant surfaces, so a scanned room is
full of holes: on one office the scan had no surface over a quarter of the
walls and most of the floor. The measured model knows where every wall and the
floor are, and a wall does not stop where the scanner stopped seeing it, so
each hole in a sheet is patched with small squares lying on that sheet's plane.
The patches are coloured in the same pass as the scan, which is what lets a
photo that saw the floor the LiDAR missed paint it.

Two more planes are patched the same way. The room graph carries no ceiling,
so a lid is laid over the room at the heights its walls reach up to, set where
the scan measured the ceiling beside them. The top of a table or a cabinet is
patched on the plane the scan measured it at, wherever the scan saw enough of
that top to know where it is.

Display only. Patches go into the picture of the room and nowhere else: every
check still measures the LiDAR mesh as captured.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.spatial import Delaunay, QhullError, cKDTree
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, measured_as, stands_upright

from .scan_colour import vertex_normals

CELL = 0.05
"""Patch size in metres: finer than a photo resolves from across a room."""
HOLE_DISTANCE = 0.06
"""A cell is a hole when no scanned vertex lies within this distance of its centre."""
OPENING_MARGIN = 0.02
WALL_TOLERANCE = 0.05
"""How far past the wall line, in metres, a floor patch may still sit."""
SEGMENT_EPSILON = 1e-9
FACING = 0.8
"""How closely, as the cosine to vertical, a scanned vertex must face up or down to count as lying flat."""
LEVEL_GAP = 0.4
"""Wall tops closer together than this, in metres, reach the same ceiling."""
CEILING_BAND = 0.5
"""How far above or below the wall tops that reach it a measured ceiling may sit."""
MINOR_LEVEL_SHARE = 0.1
"""The share of the room's wall run a ceiling height needs; a low partition is not the room's ceiling."""
MEASURED_CEILING_VERTICES = 200
LID_REACH = 1.5
"""How far, in plan, the lid reaches from the nearest column where the scan measured ceiling."""
RISES_ABOVE_LID = 0.1
TOP_REACH = 0.25
"""How far a measured top may sit from its box's top: generated box tops miss by up to about twenty centimetres."""
PLANE_THICKNESS = 0.02
TOP_SUPPORT = 0.3
RECESS_DEPTH = 1.0
"""How far behind a sheet's plane scanned surface still marks where the sheet is open rather than missing."""
"""The share of a solid's footprint its measured top must cover before the rest of that top is patched."""


@dataclass(frozen=True)
class PatchedGeometry:
    vertices: np.ndarray
    triangles: np.ndarray
    inferred: np.ndarray
    """Whether each vertex belongs to a patch rather than to the scan."""
    sheet_patches: np.ndarray
    """Whether each vertex patches a sheet of the room, the lid over it included, rather than the top of a solid."""


@dataclass(frozen=True)
class _Sheet:
    rotation: np.ndarray
    centre: np.ndarray
    half: np.ndarray
    across: int
    up: int
    """The two in-plane axes of the sheet's own frame."""
    normal: np.ndarray
    """The sheet's normal, turned to face into the room."""


def _sheet(node: SceneNode, room_centre: np.ndarray) -> _Sheet:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    rotation, centre = matrix[:3, :3], matrix[:3, 3]
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    thin = int(np.argmin(half))
    across, up = (axis for axis in range(3) if axis != thin)
    normal = rotation[:, thin]
    if not stands_upright(node):
        normal = normal if normal[2] >= 0 else -normal
    elif np.dot(room_centre - centre, normal) < 0:
        normal = -normal
    return _Sheet(rotation, centre, half, across, up, normal)


def _cell_centres(sheet: _Sheet) -> np.ndarray:
    """Cell centres covering the sheet, in its own frame."""
    along_across = np.arange(-sheet.half[sheet.across] + CELL / 2, sheet.half[sheet.across], CELL)
    along_up = np.arange(-sheet.half[sheet.up] + CELL / 2, sheet.half[sheet.up], CELL)
    grid_across, grid_up = np.meshgrid(along_across, along_up)
    local = np.zeros((grid_across.size, 3))
    local[:, sheet.across] = grid_across.ravel()
    local[:, sheet.up] = grid_up.ravel()
    return local


def _to_room(sheet: _Sheet, local: np.ndarray) -> np.ndarray:
    return local @ sheet.rotation.T + sheet.centre


def _inside_openings(points: np.ndarray, openings: list[SceneNode]) -> np.ndarray:
    """Points that fall in a door or window cut into the sheet, which must stay open."""
    blocked = np.zeros(len(points), dtype=bool)
    for opening in openings:
        matrix = np.asarray(opening.transform.m, dtype=np.float64).reshape(4, 4)
        half = np.array([opening.dimensions.x, opening.dimensions.y, opening.dimensions.z]) / 2
        local = (points - matrix[:3, 3]) @ matrix[:3, :3]
        blocked |= np.all(np.abs(local) <= half + OPENING_MARGIN, axis=1)
    return blocked


def _squares(sheet: _Sheet, centres_local: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two triangles per cell, wound so their normal faces into the room."""
    corners = []
    for step_across, step_up in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        offset = np.zeros(3)
        offset[sheet.across], offset[sheet.up] = step_across * CELL / 2, step_up * CELL / 2
        corners.append(_to_room(sheet, centres_local + offset))
    vertices = np.stack(corners, axis=1).reshape(-1, 3)
    first = np.arange(len(centres_local)) * 4
    triangles = np.concatenate([
        np.stack([first, first + 1, first + 2], axis=1),
        np.stack([first, first + 2, first + 3], axis=1),
    ])
    facing = np.cross(vertices[triangles[0, 1]] - vertices[triangles[0, 0]], vertices[triangles[0, 2]] - vertices[triangles[0, 0]])
    if len(triangles) and np.dot(facing, sheet.normal) < 0:
        triangles = triangles[:, ::-1]
    return vertices, triangles


def _openings_of(node: SceneNode, graph: SceneGraph) -> list[SceneNode]:
    """Doors and windows cut into the sheet. A whiteboard attached to it is not a hole."""
    return [other for other in graph.nodes if other.parent_id == node.id and other.relation == "cut_into"]


def _wall_runs(sheets: list[SceneNode]) -> np.ndarray:
    """Each upright sheet as the two ends of its run along the floor, in plan."""
    runs = []
    for node in sheets:
        if not stands_upright(node):
            continue
        matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
        half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
        run = int(np.argmax(half * (np.abs(matrix[2, :3]) < 0.5)))
        runs.append([(matrix[:3, :3][:, run] * half[run] * sign + matrix[:3, 3])[:2] for sign in (-1.0, 1.0)])
    return np.asarray(runs).reshape(-1, 2, 2)


def _walled_area(sheets: list[SceneNode]) -> Delaunay | None:
    """The floor plan the walls enclose, as the hull of their ends, or None without walls.

    A measured floor can run past the walls into the space next door, and a patch
    out there would be painted with whatever a photo saw through the doorway.
    """
    ends = _wall_runs(sheets).reshape(-1, 2)
    try:
        return Delaunay(ends) if len(ends) >= 3 else None
    except QhullError:
        return None


def _crosses_a_wall(starts: np.ndarray, ends: np.ndarray, walls: np.ndarray) -> np.ndarray:
    """Whether the straight line from each start to its end passes through one of the wall runs."""
    step, run = ends - starts, walls[:, 1] - walls[:, 0]
    to_wall = walls[None, :, 0] - starts[:, None]
    across = step[:, None, 0] * run[None, :, 1] - step[:, None, 1] * run[None, :, 0]
    safe = np.where(np.abs(across) < SEGMENT_EPSILON, np.inf, across)
    along_step = (to_wall[..., 0] * run[None, :, 1] - to_wall[..., 1] * run[None, :, 0]) / safe
    along_wall = (to_wall[..., 0] * step[:, None, 1] - to_wall[..., 1] * step[:, None, 0]) / safe
    return ((along_step > 0) & (along_step < 1) & (along_wall >= 0) & (along_wall <= 1)).any(axis=1)


def _outside_the_walls(centres: np.ndarray, node: SceneNode, walled: Delaunay | None) -> np.ndarray:
    if walled is None or stands_upright(node):
        return np.zeros(len(centres), dtype=bool)
    return walled.find_simplex(centres[:, :2], tol=WALL_TOLERANCE) < 0


def _no_patches() -> tuple[np.ndarray, np.ndarray]:
    return np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)


def _joined(pieces: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Several patch meshes as one, each piece's triangles shifted past the vertices before it."""
    if not pieces:
        return _no_patches()
    starts = np.cumsum([0] + [len(vertices) for vertices, _ in pieces[:-1]])
    return (
        np.concatenate([vertices for vertices, _ in pieces]),
        np.concatenate([triangles + start for (_, triangles), start in zip(pieces, starts)]),
    )


def _holes_at(scanned: cKDTree, centres: np.ndarray) -> np.ndarray:
    return np.isinf(scanned.query(centres, distance_upper_bound=HOLE_DISTANCE)[0])


def _room_sheets(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if bounds_the_room(node) and node.parent_id is None]


def patches_for(
    vertices: np.ndarray, graph: SceneGraph, scanned: cKDTree | None = None, normals: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Squares covering every stretch of wall and floor the scan has no surface on."""
    sheets = _room_sheets(graph)
    if not sheets or not len(vertices):
        return _no_patches()
    scanned = scanned if scanned is not None else cKDTree(vertices)
    room_centre = vertices.mean(axis=0)
    walled = _walled_area(sheets)
    pieces = []
    for node in sheets:
        sheet = _sheet(node, room_centre)
        local = _cell_centres(sheet)
        centres = _to_room(sheet, local)
        hole = _holes_at(scanned, centres)
        hole &= ~_inside_openings(centres, _openings_of(node, graph))
        hole &= ~_outside_the_walls(centres, node, walled)
        if normals is not None:
            hole &= ~_seen_through(sheet, local, vertices, normals, scanned)
        if hole.any():
            pieces.append(_squares(sheet, local[hole]))
    return _joined(pieces)


def _seen_through(
    sheet: _Sheet, local: np.ndarray, vertices: np.ndarray, normals: np.ndarray, scanned: cKDTree,
) -> np.ndarray:
    """Cells the scan saw past, into a recess behind the sheet's plane, which a patch must not close.

    A RoomPlan wall is a flat box, and a bench set into the wall is not in it.
    The LiDAR measures the recess, so the cells across its mouth have no scanned
    surface within HOLE_DISTANCE and read as holes; patching them drew a flat wall
    over the bench. Surface behind the plane that faces into the room was seen
    from the room, so the sheet is open there. The far face of a wall between
    two scanned rooms also lies behind the plane, but it faces away, so it does
    not count.
    """
    reach = float(np.linalg.norm(sheet.half)) + RECESS_DEPTH
    nearby = np.asarray(scanned.query_ball_point(sheet.centre, reach), dtype=np.int64)
    if not len(nearby):
        return np.zeros(len(local), dtype=bool)
    offset = vertices[nearby] - sheet.centre
    depth = offset @ sheet.normal
    behind = (depth < -HOLE_DISTANCE) & (depth > -RECESS_DEPTH) & (normals[nearby] @ sheet.normal > FACING)
    in_plane = offset[behind] @ sheet.rotation
    return _cells_hit(sheet, local, in_plane)


def _cells_hit(sheet: _Sheet, local: np.ndarray, in_plane: np.ndarray) -> np.ndarray:
    """Which of the sheet's cells have one of these sheet-frame points over them."""
    origin = -sheet.half[[sheet.across, sheet.up]]
    size = np.maximum(np.ceil(2 * sheet.half[[sheet.across, sheet.up]] / CELL).astype(np.int64), 1)
    def index(points: np.ndarray) -> np.ndarray:
        steps = np.floor((points[:, [sheet.across, sheet.up]] - origin) / CELL).astype(np.int64)
        inside = np.all((steps >= 0) & (steps < size), axis=1)
        return np.where(inside, steps[:, 0] * size[1] + steps[:, 1], -1)
    hit = np.zeros(int(size.prod()), dtype=bool)
    marks = index(in_plane)
    hit[marks[marks >= 0]] = True
    cells = index(local)
    return (cells >= 0) & hit[np.maximum(cells, 0)]


@dataclass(frozen=True)
class _CeilingLevel:
    lowest_top: float
    highest_top: float
    run: float
    height: float
    """Where the lid goes: the ceiling measured beside these walls, or their own tops when none was."""


@dataclass(frozen=True)
class _Plan:
    """A grid of columns over the walled floor plan, one per patch cell, rows running along y."""

    xy: np.ndarray
    shape: tuple[int, int]
    low: np.ndarray

    def columns_of(self, points: np.ndarray) -> np.ndarray:
        """The flat column index of each point, or -1 for a point outside the grid."""
        steps = np.floor((points[:, :2] - self.low) / CELL).astype(np.int64)
        rows, columns = self.shape
        inside = (steps[:, 0] >= 0) & (steps[:, 0] < columns) & (steps[:, 1] >= 0) & (steps[:, 1] < rows)
        return np.where(inside, steps[:, 1] * columns + steps[:, 0], -1)

    def highest(self, points: np.ndarray) -> np.ndarray:
        """The height of the highest point in each column, minus infinity where there is none."""
        tops = np.full(self.shape[0] * self.shape[1], -np.inf)
        columns = self.columns_of(points)
        inside = columns >= 0
        np.maximum.at(tops, columns[inside], points[inside, 2])
        return tops

    def reached_from(self, filled: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """For every column, how far it lies from the nearest filled one and which column that is."""
        distances, nearest = distance_transform_edt(~filled.reshape(self.shape), return_indices=True)
        return distances.ravel() * CELL, (nearest[0] * self.shape[1] + nearest[1]).ravel()


def _plan_over(walled: Delaunay) -> _Plan:
    low, high = walled.points.min(axis=0), walled.points.max(axis=0)
    along_x = np.arange(low[0] + CELL / 2, high[0], CELL)
    along_y = np.arange(low[1] + CELL / 2, high[1], CELL)
    grid_x, grid_y = np.meshgrid(along_x, along_y)
    return _Plan(np.stack([grid_x.ravel(), grid_y.ravel()], axis=1), grid_x.shape, low)


def _wall_tops(sheets: list[SceneNode]) -> list[tuple[float, float]]:
    """The height each upright sheet reaches, with how far it runs along the floor."""
    tops = []
    for node in sheets:
        if stands_upright(node):
            extents = measured_as(node)
            centre_height = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)[2, 3]
            tops.append((float(centre_height) + extents.z / 2, extents.x))
    return sorted(tops)


def _grouped_tops(tops: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    groups = [[tops[0]]]
    for top in tops[1:]:
        if top[0] - groups[-1][-1][0] > LEVEL_GAP:
            groups.append([])
        groups[-1].append(top)
    return groups


def _level_of(heights: np.ndarray, levels: list[_CeilingLevel]) -> np.ndarray:
    """The ceiling level each height belongs to, or -1 when it is near none of them."""
    lowest = np.array([level.lowest_top for level in levels])
    highest = np.array([level.highest_top for level in levels])
    apart = np.maximum(np.maximum(lowest - heights[:, None], heights[:, None] - highest), 0.0)
    return np.where(apart.min(axis=1) <= CEILING_BAND, apart.argmin(axis=1), -1)


def _level_height(ceiling: np.ndarray, group: list[tuple[float, float]]) -> float:
    """The median measured ceiling near these tops, or the run-weighted middle top without enough of one."""
    low, high = group[0][0] - CEILING_BAND, group[-1][0] + CEILING_BAND
    near = ceiling[(ceiling >= low) & (ceiling <= high)]
    if len(near) >= MEASURED_CEILING_VERTICES:
        return float(np.median(near))
    runs = np.cumsum([run for _, run in group])
    return group[int(np.searchsorted(runs, runs[-1] / 2))][0]


def _ceiling_levels(sheets: list[SceneNode], ceiling: np.ndarray) -> list[_CeilingLevel]:
    """The heights the room's ceiling sits at, one for each band of wall tops.

    A room is not always one height: a library floor ran at 2.7 metres with a
    4.1 metre void along its middle, and its walls stop at one height or the
    other. Wall tops that sit close together reach the same ceiling. RoomPlan
    stops a wall short of the ceiling, by up to forty centimetres in one
    classroom, so where the scan did measure a ceiling near those tops the lid
    goes at the measured height.
    """
    tops = _wall_tops(sheets)
    if not tops:
        return []
    total_run = sum(run for _, run in tops)
    groups = [group for group in _grouped_tops(tops) if sum(run for _, run in group) >= MINOR_LEVEL_SHARE * total_run]
    return [
        _CeilingLevel(group[0][0], group[-1][0], sum(run for _, run in group), _level_height(ceiling, group))
        for group in groups
    ]


def _ceiling_over(
    plan: _Plan, vertices: np.ndarray, facing_down: np.ndarray, levels: list[_CeilingLevel], walls: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """For each column, which level covers it and how far it lies from ceiling the scan measured.

    Columns under a stretch of ceiling the scan saw take its level, and a hole
    takes the level of the nearest measured ceiling, so a hole in the void stays
    at the void's height rather than the height of the wall beside it. Ceiling
    does not reach through a wall: a column whose nearest measured ceiling is
    on the far side of one is as far from ceiling as it can be. With no ceiling
    measured anywhere, the level the most wall reaches covers the room and the
    distance is to anything the scan saw at all.
    """
    highest = plan.highest(vertices[facing_down])
    measured = np.isfinite(highest)
    level = np.full(len(highest), -1)
    level[measured] = _level_of(highest[measured], levels)
    known = level >= 0
    if not known.any():
        distance, _ = plan.reached_from(np.isfinite(plan.highest(vertices)))
        return np.full(len(highest), int(np.argmax([level.run for level in levels]))), distance
    distance, nearest = plan.reached_from(known)
    spreading = np.flatnonzero((distance > 0) & (distance <= LID_REACH))
    distance[spreading[_crosses_a_wall(plan.xy[spreading], plan.xy[nearest[spreading]], walls)]] = np.inf
    return level[nearest], distance


def lid_patches(
    vertices: np.ndarray, normals: np.ndarray, graph: SceneGraph, scanned: cKDTree,
) -> tuple[np.ndarray, np.ndarray]:
    """Squares closing the ceiling the scan has no surface on, facing down into the room.

    The lid covers the floor plan the walls enclose, but only within a metre
    and a half of ceiling the scan measured. The walls' hull runs straight
    across the outside of a floor that is not a rectangle, and on a library
    floor a lid over the whole hull roofed wide stretches beyond the walls. The
    phone measures some of every ceiling it walks under, even in speckles, and
    measured none of the ceiling out there. A column where the scan rises
    above the lid is left open, since the room goes higher there than any lid
    at this height would admit.
    """
    sheets = _room_sheets(graph)
    walled = _walled_area(sheets)
    facing_down = normals[:, 2] < -FACING
    levels = _ceiling_levels(sheets, vertices[facing_down, 2])
    if walled is None or not levels:
        return _no_patches()
    plan = _plan_over(walled)
    level, from_ceiling = _ceiling_over(plan, vertices, facing_down, levels, _wall_runs(sheets))
    heights = np.array([one.height for one in levels])[level]
    open_column = (walled.find_simplex(plan.xy, tol=WALL_TOLERANCE) >= 0) & (from_ceiling <= LID_REACH)
    open_column &= plan.highest(vertices) <= heights + RISES_ABOVE_LID
    centres = np.column_stack([plan.xy, heights])
    open_column[open_column] = _holes_at(scanned, centres[open_column])
    return _joined([
        _squares(_ROOM_FRAME_FACING_DOWN, centres[at])
        for at in (open_column & (heights == height) for height in np.unique(heights)) if at.any()
    ])


_ROOM_FRAME_FACING_DOWN = _Sheet(np.eye(3), np.zeros(3), np.zeros(3), 0, 1, np.array([0.0, 0.0, -1.0]))
"""A sheet in the room's own frame, spanning x and y, whose squares face down."""


def _upright_axis(rotation: np.ndarray) -> int:
    return int(np.argmax(np.abs(rotation[2, :])))


def _footprint_area(node: SceneNode) -> float:
    extents = measured_as(node)
    return extents.x * extents.y


def _facing_up_near_top(node: SceneNode, vertices: np.ndarray, facing_up: np.ndarray, scanned: cKDTree) -> np.ndarray:
    """The upward-facing scanned points within the solid's footprint and near its top.

    They are looked for no lower than the middle of the box, so a box a few
    centimetres tall that stands for something flattened does not take the
    floor under it for its top.
    """
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    upright = _upright_axis(matrix[:3, :3])
    flat = [axis for axis in range(3) if axis != upright]
    top = matrix[2, 3] + half[upright]
    reach = float(np.linalg.norm(np.append(half[flat], TOP_REACH)))
    nearby = np.asarray(scanned.query_ball_point([matrix[0, 3], matrix[1, 3], top], reach), dtype=np.int64)
    points = vertices[nearby[facing_up[nearby]]]
    local = (points - matrix[:3, 3]) @ matrix[:3, :3]
    in_footprint = np.all(np.abs(local[:, flat]) <= half[flat], axis=1)
    near_top = (points[:, 2] >= max(top - TOP_REACH, matrix[2, 3])) & (points[:, 2] <= top + TOP_REACH)
    return points[in_footprint & near_top]


def _dominant_plane(points: np.ndarray) -> np.ndarray:
    """The points lying on the horizontal plane most of them share."""
    if not len(points):
        return points
    steps = np.floor((points[:, 2] - points[:, 2].min()) / PLANE_THICKNESS).astype(np.int64)
    height = float(np.median(points[steps == np.argmax(np.bincount(steps)), 2]))
    return points[np.abs(points[:, 2] - height) <= PLANE_THICKNESS]


def _top_sheet(node: SceneNode, height: float) -> _Sheet:
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    upright = _upright_axis(matrix[:3, :3])
    across, up = (axis for axis in range(3) if axis != upright)
    centre = np.array([matrix[0, 3], matrix[1, 3], height])
    return _Sheet(matrix[:3, :3], centre, half, across, up, np.array([0.0, 0.0, 1.0]))


def _within_measured_outline(plane: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """Cells inside the outline of the measured plane, so a round table is not patched out to its box's corners."""
    try:
        return Delaunay(plane[:, :2]).find_simplex(centres[:, :2]) >= 0
    except QhullError:
        return np.zeros(len(centres), dtype=bool)


def _top_holes(
    node: SceneNode, plane: np.ndarray, scanned: cKDTree, placed: list[np.ndarray],
) -> tuple[_Sheet, np.ndarray, np.ndarray] | None:
    """The solid's top sheet and the cells on it to patch, or None when too little of the top was measured."""
    sheet = _top_sheet(node, float(np.median(plane[:, 2])))
    local = _cell_centres(sheet)
    centres = _to_room(sheet, local)
    on_plane = np.isfinite(cKDTree(plane[:, :2]).query(centres[:, :2], distance_upper_bound=HOLE_DISTANCE)[0])
    if not len(centres) or on_plane.mean() < TOP_SUPPORT:
        return None
    hole = _holes_at(scanned, centres) & _within_measured_outline(plane, centres)
    if placed:
        hole &= _holes_at(cKDTree(np.concatenate(placed)), centres)
    return sheet, local[hole], centres[hole]


def top_patches(
    vertices: np.ndarray, normals: np.ndarray, graph: SceneGraph, scanned: cKDTree,
) -> tuple[np.ndarray, np.ndarray]:
    """Squares closing the holes in the measured tops of the solids standing in the room.

    Each solid's top is the plane most of the upward-facing scan within its
    footprint lies on, because a generated box top misses the real surface by
    inches. A top is patched only when the scan measured it over at least three
    tenths of the footprint: the tables in one classroom were measured over two
    fifths to three fifths of theirs, and a chair seen only from the side over
    a tenth, so nothing is given a top nobody saw. Broad solids go first, and a
    cell already patched on one solid is not patched again on another that
    overlaps it.
    """
    facing_up = normals[:, 2] > FACING
    solids = sorted((node for node in graph.nodes if not bounds_the_room(node)), key=_footprint_area, reverse=True)
    pieces, placed = [], []
    for node in solids:
        plane = _dominant_plane(_facing_up_near_top(node, vertices, facing_up, scanned))
        found = _top_holes(node, plane, scanned, placed) if len(plane) >= 3 else None
        if found is None or not len(found[1]):
            continue
        sheet, local, centres = found
        pieces.append(_squares(sheet, local))
        placed.append(centres)
    return _joined(pieces)


def with_holes_patched(vertices: np.ndarray, triangles: np.ndarray, graph: SceneGraph) -> PatchedGeometry:
    """The scan with its room's holes and its solids' top holes patched, and which vertices are patches."""
    sheet_pieces, top_pieces = [], []
    if len(vertices):
        scanned = cKDTree(vertices)
        normals = vertex_normals(vertices, triangles)
        sheet_pieces = [patches_for(vertices, graph, scanned, normals), lid_patches(vertices, normals, graph, scanned)]
        top_pieces = [top_patches(vertices, normals, graph, scanned)]
    sheet_vertices, sheet_triangles = _joined(sheet_pieces)
    top_vertices, top_triangles = _joined(top_pieces)
    patch_vertices, patch_triangles = _joined([(sheet_vertices, sheet_triangles), (top_vertices, top_triangles)])
    added = len(patch_vertices)
    return PatchedGeometry(
        vertices=np.concatenate([vertices, patch_vertices]),
        triangles=np.concatenate([triangles, patch_triangles + len(vertices)]).astype(np.int64),
        inferred=np.concatenate([np.zeros(len(vertices), bool), np.ones(added, bool)]),
        sheet_patches=np.concatenate([np.zeros(len(vertices), bool), np.arange(added) < len(sheet_vertices)]),
    )


def _segments_enter_box(origin: np.ndarray, points: np.ndarray, node: SceneNode) -> np.ndarray:
    """Whether the straight line from origin to each point passes through the node's box.

    The slab test in the box's own frame: the line is inside the box for the
    parameters where it is inside all three pairs of faces at once.
    """
    matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
    half = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z]) / 2
    start = (origin - matrix[:3, 3]) @ matrix[:3, :3]
    direction = (points - matrix[:3, 3]) @ matrix[:3, :3] - start
    parallel = np.abs(direction) < SEGMENT_EPSILON
    safe = np.where(parallel, SEGMENT_EPSILON, direction)
    first, second = (-half - start) / safe, (half - start) / safe
    enter = np.where(parallel, -np.inf, np.minimum(first, second)).max(axis=1)
    leave = np.where(parallel, np.inf, np.maximum(first, second)).min(axis=1)
    outside_a_slab = (parallel & (np.abs(start) > half)).any(axis=1)
    return ~outside_a_slab & (enter <= leave) & (leave > 0.0) & (enter < 1.0)


def hidden_behind_objects(graph: SceneGraph, vertices: np.ndarray, patches: np.ndarray):
    """For each camera, the patch vertices a measured object stands between it and.

    The LiDAR often misses the legs and underside of a chair as well as the floor
    beneath it, so no scanned surface blocks the view, and the floor patch there
    would take the chair's colour from every photo. The object's measured box is
    in the graph even where its surface is not in the scan, so it does the blocking.
    """
    boxes = ObjectBoxes(graph)
    patch_index = np.flatnonzero(patches)

    def hidden(camera) -> np.ndarray:
        blocked = np.zeros(len(vertices), dtype=bool)
        blocked[patch_index] = boxes.blocking(camera.position, vertices[patch_index])
        return blocked

    return hidden


class ObjectBoxes:
    """The measured object boxes that can stand between a camera and the floor, with their room-frame extents.

    A library floor has hundreds of objects, and testing every line of sight
    against every box was a third of a photo bake. A box can only cross a line
    from the camera to a point if it overlaps the box around the camera and all
    the points, so only those boxes are tested, with the same slab test.
    """

    def __init__(self, graph: SceneGraph):
        self.nodes = [
            node for node in graph.nodes
            if not (bounds_the_room(node) and node.parent_id is None) and node.relation != "cut_into"
        ]
        corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float64) / 2
        extents = []
        for node in self.nodes:
            matrix = np.asarray(node.transform.m, dtype=np.float64).reshape(4, 4)
            size = np.array([node.dimensions.x, node.dimensions.y, node.dimensions.z])
            world = (corners * size) @ matrix[:3, :3].T + matrix[:3, 3]
            extents.append((world.min(axis=0), world.max(axis=0)))
        self.low = np.array([low for low, _ in extents]).reshape(-1, 3)
        self.high = np.array([high for _, high in extents]).reshape(-1, 3)

    def blocking(self, origin: np.ndarray, points: np.ndarray) -> np.ndarray:
        """Whether some box stands on the straight line from origin to each point."""
        blocked = np.zeros(len(points), dtype=bool)
        if not len(points) or not self.nodes:
            return blocked
        low = np.minimum(points.min(axis=0), origin)
        high = np.maximum(points.max(axis=0), origin)
        near = np.flatnonzero(np.all(self.low <= high, axis=1) & np.all(self.high >= low, axis=1))
        for index in near:
            blocked |= _segments_enter_box(origin, points, self.nodes[index])
        return blocked
