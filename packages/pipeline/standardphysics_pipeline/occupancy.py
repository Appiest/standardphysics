"""The floor, rasterized, so clearance becomes a distance problem.

Every obstacle is an oriented box. A cell is occupied when its centre falls
inside one, tested in that box's own frame so rotation is handled exactly
rather than by an axis-aligned bounding box that would eat the corners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, Vec3, lies_flat, stands_upright, to_meters

from .footprints import floor_polygon, polygon_bounds

CELL_SIZE = 0.025
"""25 mm. Fine enough that quantisation stays near half an inch."""

BLOCKING_HEIGHT = to_meters(0.25)
"""A node blocks the floor when its top rises above 1/4 inch.

ADA 2010 303 allows a vertical change in level of at most 1/4 in without
treatment and requires a ramp above 1/2 in, so a customer cannot roll over
anything taller. Toe clearance describes space beneath an element; it does not
make a solid object on the floor passable.
"""

PASSABLE_KINDS = {"floor", "window", "opening", "door"}
"""Doors and openings are how you get through a wall, not obstacles."""

CUTS_THROUGH_WALLS = {"door", "opening"}
"""Leaving a door out of the grid is not enough.

RoomPlan reports a wall at its full length and puts the door inside it as a
separate surface, so the wall stays solid right across the opening. The door
has to be subtracted from the wall, not merely skipped, or no route can ever
leave the room."""

OUTSIDE_MARGIN = 1.5
"""Metres of ground kept walkable beyond the floor's edge.

Once a doorway is open the search can leave the building, and beyond the floor
there is nothing to stop it: the padded grid is empty space with excellent
clearance, so a bottleneck search explores every cell of it before it ever
reaches the goal. A route from the street needs a little ground outside; it
does not need a field."""

INDOOR_MARGIN = 0.05
"""Metres of slack when asking whether a cell is on the scanned floor.

The grid quantises at 25 mm and a doorway is cut right at the wall line, so a
cell straddling the floor's edge is still somewhere a customer stands."""

CANE_DETECTABLE = to_meters(27.0)
"""Height below which an object counts as being in the way.

ADA 2010 307.2 treats leading edges above 27 inches as protruding objects with
their own rule, and anything below that as detectable at floor level. So an
object whose underside clears 27 inches is not a floor obstruction, and one
whose underside is below it is, however high its top reaches.

Person C should confirm this against the source with the other thresholds.
"""

THINNEST_BARRIER_CELLS = 2
"""The fewest cells an obstacle is drawn across, on either axis.

RoomPlan reports a wall as a surface with no thickness. A box with no
thickness contains no cell centre, so every wall of a capture was missing from
the grid: the room had no edge, the ground beyond it counted as clearance, and
the widest route ran around the room's perimeter and out through its doorways.

A band two cells wide is 4-connected at any angle, which is what stops a route
stepping diagonally between two of its cells. Only the grid is thickened. The
reported width comes from `footprints`, which measures to the surface itself.
"""

THINNEST_WALL = 2 * INDOOR_MARGIN
"""The fewest metres a wall is drawn across.

`INDOOR_MARGIN` counts cells just past the floor's edge as indoors, and a wall
drawn on that edge has to cover the slack on both sides of its line. A thinner
one leaves a sliver of indoor floor running round the outside of the room, and
the sliver joins every doorway to every other, so a trip held indoors could
still leave through one door and come back in through another.
"""

DOORWAY_BITE = 0.12
"""Metres the cleared opening extends past the door on its thin axis.

A door panel is often slightly thinner than the wall holding it. Clearing
exactly the panel's footprint can leave a sliver of wall sealing the gap, and a
one cell sliver blocks a route as completely as a brick wall."""


@dataclass(frozen=True)
class Grid:
    origin_x: float
    origin_y: float
    cell_size: float
    occupied: np.ndarray
    owner: np.ndarray
    """Index into `node_ids` for each occupied cell, -1 where free.

    A finding has to name the two objects forming a pinch, so the grid keeps
    track of who put each cell there.
    """

    node_ids: list[UUID] = field(default_factory=list)

    indoors: np.ndarray | None = None
    """Which free cells are the scanned floor, as against the ground outside it.

    `OUTSIDE_MARGIN` leaves open ground beyond the walls so a route can start
    on the pavement, and `routes.widest_path` has to tell that ground from the
    room to keep a trip between two stops inside from using it. `None` where
    the capture returned no floor, which leaves the two indistinguishable.
    """

    def owner_at(self, row: int, col: int) -> UUID | None:
        index = int(self.owner[row, col])
        return self.node_ids[index] if index >= 0 else None

    @property
    def shape(self) -> tuple[int, int]:
        return self.occupied.shape

    def to_cell(self, x: float, y: float) -> tuple[int, int]:
        col = int((x - self.origin_x) / self.cell_size)
        row = int((y - self.origin_y) / self.cell_size)
        return row, col

    def to_world(self, row: int, col: int) -> Vec3:
        return Vec3(
            x=self.origin_x + (col + 0.5) * self.cell_size,
            y=self.origin_y + (row + 0.5) * self.cell_size,
            z=0.0,
        )

    def contains(self, row: int, col: int) -> bool:
        rows, cols = self.occupied.shape
        return 0 <= row < rows and 0 <= col < cols


def reads_as_wall(node: SceneNode) -> bool:
    """A wall as the capture knows it: an upright room sheet, or the wall
    class an ingest step or reviewer assigned.

    A wall measured through a door jamb can be too thick for the sheet
    heuristic to accept while its assigned class still names it a wall, so
    the class label is the fallback on top of the sheet geometry; it never
    substitutes for it.
    """
    return stands_upright(node) or node.kind == "wall"


def blocks_floor(node: SceneNode) -> bool:
    """Whether this node takes up floor space a customer has to get around.

    Needs both ends of the object, not just the top. A wall-mounted cabinet
    whose underside is a metre up has a high top and blocks nothing on the
    floor; ADA 2010 307 handles it as a protruding object instead. A floor mat
    has a low top and blocks nothing either.
    """
    if node.kind in PASSABLE_KINDS:
        return False
    centre = node.transform.position.z
    top = centre + node.dimensions.z / 2
    bottom = centre - node.dimensions.z / 2
    return top > BLOCKING_HEIGHT and bottom < CANE_DETECTABLE


def _rotation_2d(node: SceneNode) -> tuple[float, float]:
    """Cosine and sine of the node's rotation about Z, from its transform."""
    m = node.transform.m
    cos_t, sin_t = m[0], m[4]
    scale = (cos_t * cos_t + sin_t * sin_t) ** 0.5
    if scale == 0:
        return 1.0, 0.0
    return cos_t / scale, sin_t / scale


def _bounds(graph: SceneGraph) -> tuple[float, float, float, float]:
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    if floor is not None:
        min_x, min_y, max_x, max_y = polygon_bounds(floor_polygon(floor))
        return (
            min_x - OUTSIDE_MARGIN, min_y - OUTSIDE_MARGIN,
            max_x + OUTSIDE_MARGIN, max_y + OUTSIDE_MARGIN,
        )
    xs, ys = [], []
    for node in graph.nodes:
        p = node.transform.position
        reach = max(node.dimensions.x, node.dimensions.y)
        xs += [p.x - reach, p.x + reach]
        ys += [p.y - reach, p.y + reach]
    return min(xs), min(ys), max(xs), max(ys)


def build_grid(graph: SceneGraph, cell_size: float = CELL_SIZE) -> Grid:
    min_x, min_y, max_x, max_y = _bounds(graph)
    if _measures_nothing(graph):
        return _all_blocked(min_x, min_y, max_x, max_y, cell_size)
    cols = max(int(np.ceil((max_x - min_x) / cell_size)), 1)
    rows = max(int(np.ceil((max_y - min_y) / cell_size)), 1)
    occupied = np.zeros((rows, cols), dtype=bool)
    owner = np.full((rows, cols), -1, dtype=np.int32)
    node_ids: list[UUID] = []

    centres_x = min_x + (np.arange(cols) + 0.5) * cell_size
    centres_y = min_y + (np.arange(rows) + 0.5) * cell_size
    world_x, world_y = np.meshgrid(centres_x, centres_y)

    for node in graph.nodes:
        if not blocks_floor(node):
            continue
        node_ids.append(node.id)
        _mark(occupied, owner, len(node_ids) - 1, node, world_x, world_y, cell_size)

    for node in graph.nodes:
        if node.kind in CUTS_THROUGH_WALLS:
            _punch(occupied, owner, node, world_x, world_y)

    _bound_the_world(occupied, graph, world_x, world_y)
    return Grid(
        min_x, min_y, cell_size, occupied, owner, node_ids,
        _floor_mask(graph, world_x, world_y, INDOOR_MARGIN),
    )


def _measures_nothing(graph: SceneGraph) -> bool:
    """Whether the capture handed back a region with no size in any direction.

    A wall is allowed to have no thickness and a floor no height, but nothing
    real has no extent at all. One of those in the graph means the capture did
    not measure what it claims to describe, and a room whose shape is unknown
    has no walkable ground in it until somebody scans it again.
    """
    return any(
        max(node.dimensions.as_tuple()) <= 0 for node in graph.nodes
    )


def _all_blocked(
    min_x: float, min_y: float, max_x: float, max_y: float, cell_size: float
) -> Grid:
    """Nothing walkable, which is the safe way to be wrong about a room."""
    cols = max(int(np.ceil((max_x - min_x) / cell_size)), 1)
    rows = max(int(np.ceil((max_y - min_y) / cell_size)), 1)
    return Grid(
        min_x, min_y, cell_size,
        np.ones((rows, cols), dtype=bool),
        np.full((rows, cols), -1, dtype=np.int32),
        [],
        None,
    )


def _mark(
    occupied: np.ndarray,
    owner: np.ndarray,
    index: int,
    node: SceneNode,
    world_x: np.ndarray,
    world_y: np.ndarray,
    cell_size: float,
) -> None:
    """Occupy every cell whose centre lies inside this node's oriented box."""
    inside = _solid_cells(node, world_x, world_y, cell_size)
    owner[inside & ~occupied] = index
    occupied |= inside


def _solid_cells(
    node: SceneNode, world_x: np.ndarray, world_y: np.ndarray, cell_size: float
) -> np.ndarray:
    """The cells an obstacle fills, never thinner than `THINNEST_BARRIER_CELLS`
    and, for a wall, never thinner than `THINNEST_WALL`."""
    thinnest = THINNEST_BARRIER_CELLS * cell_size
    if reads_as_wall(node):
        thinnest = max(thinnest, THINNEST_WALL)
    grow_x = max(thinnest - node.dimensions.x, 0.0) / 2
    grow_y = max(thinnest - node.dimensions.y, 0.0) / 2
    return _inside_box(node, world_x, world_y, grow_x, grow_y)


def _inside_box(
    node: SceneNode,
    world_x: np.ndarray,
    world_y: np.ndarray,
    grow_x: float = 0.0,
    grow_y: float = 0.0,
) -> np.ndarray:
    """Cells whose centre lies in the node's oriented box, grown by (grow_x, grow_y).

    Only the cells under the box's axis-aligned extent are tested. A floor the
    size of a library is over a million cells, and testing each of a hundred and
    seventy boxes against all of them took seconds on every page that asked.
    """
    p = node.transform.position
    cos_t, sin_t = _rotation_2d(node)
    half_x, half_y = node.dimensions.x / 2 + grow_x, node.dimensions.y / 2 + grow_y
    rows, cols = _window(
        world_x, world_y, p.x, p.y,
        abs(cos_t) * half_x + abs(sin_t) * half_y, abs(sin_t) * half_x + abs(cos_t) * half_y,
    )
    dx, dy = world_x[rows, cols] - p.x, world_y[rows, cols] - p.y
    inside = np.zeros(world_x.shape, dtype=bool)
    inside[rows, cols] = (np.abs(dx * cos_t + dy * sin_t) <= half_x) & (np.abs(-dx * sin_t + dy * cos_t) <= half_y)
    return inside


def _window(
    world_x: np.ndarray, world_y: np.ndarray, x: float, y: float, reach_x: float, reach_y: float
) -> tuple[slice, slice]:
    """The rows and columns of a regular grid of cell centres within reach of (x, y)."""
    columns, rows = world_x[0], world_y[:, 0]
    return (
        slice(np.searchsorted(rows, y - reach_y, "left"), np.searchsorted(rows, y + reach_y, "right")),
        slice(np.searchsorted(columns, x - reach_x, "left"), np.searchsorted(columns, x + reach_x, "right")),
    )


def _punch(
    occupied: np.ndarray,
    owner: np.ndarray,
    node: SceneNode,
    world_x: np.ndarray,
    world_y: np.ndarray,
) -> None:
    """Open the doorway back up through whatever wall was drawn across it."""
    grow_x, grow_y = _bite(node)
    opening = _inside_box(node, world_x, world_y, grow_x, grow_y)
    occupied[opening] = False
    owner[opening] = -1


def _bite(node: SceneNode) -> tuple[float, float]:
    """Grow the cut along the door's thin axis only, never along its width."""
    if node.dimensions.y <= node.dimensions.x:
        return 0.0, DOORWAY_BITE
    return DOORWAY_BITE, 0.0


def _bound_the_world(
    occupied: np.ndarray, graph: SceneGraph, world_x: np.ndarray, world_y: np.ndarray
) -> None:
    """Close off everything well outside the building.

    Without this the search wanders across the empty padding beyond the walls,
    which is both slow and meaningless. Keeps a margin so a route can still
    start on the pavement outside the front door.
    """
    walkable = _floor_mask(graph, world_x, world_y, OUTSIDE_MARGIN)
    if walkable is None:
        return
    occupied[~walkable] = True


def _floor_mask(
    graph: SceneGraph, world_x: np.ndarray, world_y: np.ndarray, margin: float
) -> np.ndarray | None:
    """Which cells lie on the scanned floor, give or take `margin` metres."""
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    if floor is None:
        return None
    return _inside_convex_polygon(floor_polygon(floor), world_x, world_y, margin)


def _inside_convex_polygon(polygon, world_x: np.ndarray, world_y: np.ndarray, margin: float) -> np.ndarray:
    """Vectorized convex containment for the transformed floor boundary."""
    if len(polygon) < 3:
        return np.zeros(world_x.shape, dtype=bool)
    area = sum(start[0] * end[1] - end[0] * start[1] for start, end in zip(polygon, polygon[1:] + polygon[:1]))
    direction = 1 if area >= 0 else -1
    inside = np.ones(world_x.shape, dtype=bool)
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        edge_x, edge_y = end[0] - start[0], end[1] - start[1]
        cross = edge_x * (world_y - start[1]) - edge_y * (world_x - start[0])
        inside &= direction * cross >= -margin * (edge_x * edge_x + edge_y * edge_y) ** 0.5
    return inside


def _cell_centres(grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = grid.shape
    xs = grid.origin_x + (np.arange(cols) + 0.5) * grid.cell_size
    ys = grid.origin_y + (np.arange(rows) + 0.5) * grid.cell_size
    return np.meshgrid(xs, ys)


def occupancy_excluding(graph: SceneGraph, grid: Grid, node_id) -> np.ndarray:
    """The same floor with one object taken away.

    `grid.owner` records a single owner per cell and the first node to claim a
    cell keeps it, so it cannot answer this. Where two objects overlap, freeing
    one node's cells by owner also frees cells the other still covers, and a
    route reads as open when it is still sealed.

    Rasterising the remaining nodes answers the question honestly. The grid's
    origin and cell size are reused so the result lines up with the original
    cell for cell.
    """
    world_x, world_y = _cell_centres(grid)
    occupied = np.zeros(grid.shape, dtype=bool)

    for node in graph.nodes:
        if node.id == node_id or not blocks_floor(node):
            continue
        occupied |= _solid_cells(node, world_x, world_y, grid.cell_size)

    for node in graph.nodes:
        if node.id != node_id and node.kind in CUTS_THROUGH_WALLS:
            grow_x, grow_y = _bite(node)
            occupied[_inside_box(node, world_x, world_y, grow_x, grow_y)] = False

    _bound_the_world(occupied, graph, world_x, world_y)
    return occupied
