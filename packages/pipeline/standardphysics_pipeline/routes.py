"""How wide is the tightest point on the way from here to there.

The widest path problem, not the shortest. A customer does not care how far
they travel, they care whether they fit, so we maximise the narrowest point
along the route rather than minimising its length.

`scipy.ndimage.distance_transform_edt` gives every free cell its distance to
the nearest obstacle. A bottleneck Dijkstra over that field finds the path
whose minimum clearance is as large as possible. Twice that minimum is the
corridor width, and the cell where it occurs is the pinch the camera flies to.
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections import deque
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from scipy import ndimage
from standardphysics_contracts import Vec3, to_inches, to_meters

from .occupancy import Grid, occupancy_excluding

NEIGHBOURS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
EIGHT_CONNECTED = np.ones((3, 3), dtype=bool)
"""The same neighbourhood as `NEIGHBOURS`, for `ndimage.label`."""

MAX_EXEMPT_SHARE = 0.35
"""Most of a leg has to remain measurable.

A fixed exemption swallows a short leg whole. Counter to Pickup is 1.6 m, and
two 0.75 m circles leave a 0.1 m sliver pressed against the counter, so the
reported width describes that sliver rather than the route. The exemption
shrinks on short legs so there is always something real left to measure."""

ENDPOINT_EXEMPTION = 0.75
"""Metres around each stop that do not count toward the route's bottleneck.

Standing at a counter puts you within arm's reach of it, so the tightest point
of a journey is always its destination. That is not what a route width check
asks about. Whether there is room to sit at the counter is a separate question
with its own rule, so the approach to each stop is exempt here and measured by
`counter_approach` instead.
"""


@dataclass
class PathResult:
    clearance_radius: float
    """Metres from the path's tightest point to the nearest obstacle."""

    pinch_cell: tuple[int, int] | None
    path: list[tuple[int, int]]
    reachable: bool
    exempt: np.ndarray | None = None
    """Which cells were left out of the measurement.

    Inside the exemption the search has no preference between neighbours, so
    the route wanders and its clearance there describes nothing. Anything
    reading per-point values needs to know which ones to ignore."""

    clearance: np.ndarray | None = None
    """Metres from each cell to the nearest cell this trip could not use.

    A trip held inside the room cannot use the ground beyond its floor, so that
    ground is no more room to pass than a wall is. Per-point widths along the
    route have to be read from this field rather than from `clearance_map`, or
    they describe space the search was never allowed into."""

    @property
    def width_meters(self) -> float:
        return self.clearance_radius * 2


def clearance_map(grid: Grid) -> np.ndarray:
    """Metres from each free cell to the nearest obstacle."""
    return ndimage.distance_transform_edt(~grid.occupied) * grid.cell_size


def _nearest(walkable: np.ndarray, cell: tuple[int, int]):
    """Snap a stop that landed inside furniture out to open floor."""
    rows, cols = walkable.shape
    row, col = cell
    if 0 <= row < rows and 0 <= col < cols and walkable[row, col]:
        return row, col
    free = np.argwhere(walkable)
    if free.size == 0:
        return None
    distances = np.abs(free[:, 0] - row) + np.abs(free[:, 1] - col)
    nearest = free[int(np.argmin(distances))]
    return int(nearest[0]), int(nearest[1])


def _stands_indoors(grid: Grid, cell: tuple[int, int]) -> bool:
    """Whether a stop is on the scanned floor rather than the ground outside.

    Asked of the whole floor, not only the part inside the walls: a stop left
    behind a wall is still a stop in the shop, and `_nearest` draws it in.
    """
    floor = grid.on_floor if grid.on_floor is not None else grid.indoors
    return floor is not None and grid.contains(*cell) and bool(floor[cell])


@dataclass(frozen=True)
class _RouteWorld:
    """The cells one trip may use, and its two stops snapped onto them."""

    walkable: np.ndarray
    in_the_room: bool
    start: tuple[int, int] | None
    goal: tuple[int, int] | None


def _walkable(grid: Grid, occupied: np.ndarray, in_the_room: bool) -> np.ndarray:
    """Free cells, held to the scanned floor when both stops stand on it.

    `occupancy.OUTSIDE_MARGIN` keeps open ground beyond the walls so a customer
    arriving from the street has somewhere to stand. Nothing is out there, which
    makes it the widest corridor in the capture, so a search allowed onto it
    leaves through a doorway or a gap in the walls and walks around the
    building: on the Apple living room sample 92 per cent of the route ran
    outdoors, and the width it reported was the width of the garden.

    A trip between two stops in the room is a trip through the room, so it
    never gets the outside, even when the floor cannot join its stops. A room
    that cannot answer from its own floor is blocked, and `what_sealed_the_route`
    names what blocks it. Only a stop standing outside opens the outside.

    `occupied` is an argument so a caller asking what one object's removal
    would open gets the same rule applied to the altered floor.
    """
    free = ~occupied
    return free & grid.indoors if in_the_room else free


def _route_world(grid: Grid, start, goal) -> _RouteWorld:
    in_the_room = _stands_indoors(grid, start) and _stands_indoors(grid, goal)
    walkable = _walkable(grid, grid.occupied, in_the_room)
    return _RouteWorld(
        walkable, in_the_room, _nearest(walkable, start), _nearest(walkable, goal)
    )


def _clearance_within(
    grid: Grid, clearance: np.ndarray, walkable: np.ndarray
) -> np.ndarray:
    """`clearance`, cut short wherever the trip's own world ends first."""
    if not (~grid.occupied & ~walkable).any():
        return clearance
    edge = ndimage.distance_transform_edt(walkable) * grid.cell_size
    return np.minimum(clearance, edge)


def _exempt_mask(
    grid: Grid, stops: list[tuple[int, int]], radius_m: float
) -> np.ndarray:
    rows, cols = grid.shape
    row_index, col_index = np.ogrid[:rows, :cols]
    mask = np.zeros((rows, cols), dtype=bool)
    radius_cells = radius_m / grid.cell_size
    for row, col in stops:
        mask |= ((row_index - row) ** 2 + (col_index - col) ** 2) <= radius_cells**2
    return mask


def _exemption_radius(
    grid: Grid, start: tuple[int, int], goal: tuple[int, int], requested: float
) -> float:
    separation = math.dist(start, goal) * grid.cell_size
    return min(requested, separation * MAX_EXEMPT_SHARE)


def widest_path(
    grid: Grid,
    clearance: np.ndarray,
    start: tuple[int, int],
    goal: tuple[int, int],
    endpoint_exemption: float = ENDPOINT_EXEMPTION,
    extra_exempt: np.ndarray | None = None,
    anchors: tuple[UUID | None, UUID | None] = (None, None),
) -> PathResult:
    """Bottleneck Dijkstra: maximise the smallest clearance along the route.

    `anchors` are the objects the two stops stand at, from `Stop.anchor_node_id`.
    """
    world = _route_world(grid, start, goal)
    if world.start is None or world.goal is None:
        return PathResult(0.0, None, [], reachable=False)

    clearance = _clearance_within(grid, clearance, world.walkable)
    radius = _exemption_radius(grid, world.start, world.goal, endpoint_exemption)
    leaving, arriving = _standing_rooms(grid, world, start, goal, radius)
    exempt = _stop_exemption(
        grid, world.walkable, [(world.start, anchors[0]), (world.goal, anchors[1])], radius
    ) | leaving | arriving
    if extra_exempt is not None:
        exempt |= extra_exempt
    search_field = np.where(exempt, np.inf, clearance)

    came_from, arrival = _bottleneck_search(
        grid, world.walkable, search_field, np.argwhere(leaving), arriving
    )
    if arrival is None:
        return PathResult(0.0, None, [], reachable=False)

    between = _retrace(came_from, arrival)
    path = (
        _walk_within(grid, leaving, between[0], start)[:0:-1]
        + between
        + _walk_within(grid, arriving, between[-1], goal)[1:]
    )
    measured = [cell for cell in path if not exempt[cell]]
    if not measured:
        measured = path
    pinch = min(measured, key=lambda cell: clearance[cell])
    return PathResult(
        float(clearance[pinch]), pinch, path, reachable=True, exempt=exempt,
        clearance=clearance,
    )


def _stop_exemption(
    grid: Grid,
    walkable: np.ndarray,
    stops: list[tuple[tuple[int, int], UUID | None]],
    radius: float,
) -> np.ndarray:
    """The cells near each stop that say nothing about the route.

    Within `radius` of a stop, two kinds of floor. One is floor whose nearest
    obstacle is the object the stop stands at, so a counter cannot set the
    bottleneck of the trip to it. The other is floor no closer to its nearest
    obstacle than the stop itself stands: a pickup point 2 inches from a
    display case starts every route out of it 2 inches from the case, and
    stepping away from something is not squeezing past it. What is left is
    floor where the route closes in on something the stop was clear of, which
    is a gap the route has to pass.

    Exempting the whole radius hid whatever else stood near a stop: two signs
    20 inches apart just inside the front door read as the 31 inch aisle beyond
    them, because the gap between them was exempt. A stop that names no anchor
    still gets the whole radius, since nothing then says which of the things
    around it is the destination and which is in the way.
    """
    exempt = np.zeros(grid.shape, dtype=bool)
    nearest = None
    for cell, anchor in stops:
        around = _exempt_mask(grid, [cell], radius)
        if anchor not in grid.node_ids:
            exempt |= around
            continue
        if nearest is None:
            nearest = _NearestObstacle.of(walkable)
        exempt |= around & (
            (grid.owner[nearest.rows, nearest.cols] == grid.node_ids.index(anchor))
            | nearest.no_closer_than(cell)
        )
    return exempt


@dataclass(frozen=True)
class _NearestObstacle:
    """For every cell, how far away the nearest cell a trip cannot use is, and
    which cell that is."""

    distance: np.ndarray
    rows: np.ndarray
    cols: np.ndarray

    @classmethod
    def of(cls, walkable: np.ndarray) -> _NearestObstacle:
        distance, (rows, cols) = ndimage.distance_transform_edt(
            walkable, return_indices=True
        )
        return cls(distance, rows, cols)

    def no_closer_than(self, stop: tuple[int, int]) -> np.ndarray:
        """Cells at least as far from their nearest obstacle as `stop` is from
        that same obstacle."""
        from_stop = np.hypot(self.rows - stop[0], self.cols - stop[1])
        return self.distance >= from_stop


def _standing_rooms(
    grid: Grid, world: _RouteWorld, start, goal, radius: float
) -> tuple[np.ndarray, np.ndarray]:
    """Both stops' standing room, with any floor they share split between them.

    Two large objects side by side, a bed and the chair beside it, reach far
    enough to share floor. A route whose first cell is already a goal has
    nowhere to go, so each shared cell goes to the stop it is nearer, and each
    stop keeps the cell it landed on.
    """
    assert world.start is not None and world.goal is not None
    leaving = _standing_room(grid, world.walkable, start, world.start, radius)
    arriving = _standing_room(grid, world.walkable, goal, world.goal, radius)
    shared = leaving & arriving
    if not shared.any():
        return leaving, arriving
    rows, cols = np.ogrid[: grid.shape[0], : grid.shape[1]]
    nearer_start = (rows - start[0]) ** 2 + (cols - start[1]) ** 2 <= (
        (rows - goal[0]) ** 2 + (cols - goal[1]) ** 2
    )
    leaving &= ~(shared & ~nearer_start)
    arriving &= ~(shared & nearer_start)
    leaving[world.goal], arriving[world.start] = False, False
    leaving[world.start], arriving[world.goal] = True, True
    return leaving, arriving


def _standing_room(
    grid: Grid,
    walkable: np.ndarray,
    stop: tuple[int, int],
    landed: tuple[int, int],
    radius: float,
) -> np.ndarray:
    """The cells a trip may begin or end on for one stop.

    A stop on open floor is exactly where it stands. A stop inside an object,
    which is where an anchor at a sofa's or a counter's centre lands, has to be
    reached from the floor beside the object, and the single nearest free cell
    is a poor choice of which floor: beside a chair pushed against the wall it
    is the sliver between the two, sealed off from the room, and beside a
    television on its stand it is the gap behind the stand.

    So the standing room is all the floor around the stop out to its nearest
    free cell plus the exemption radius, and the search arrives on whichever
    part of it the widest route reaches. The caller exempts it along with the
    exemption itself: standing at an object puts you beside it, and the
    tightest point of that is not a question about the route.
    """
    cells = np.zeros(grid.shape, dtype=bool)
    if landed == stop or radius <= 0:
        cells[landed] = True
        return cells
    reach = math.dist(stop, landed) * grid.cell_size + radius
    return _exempt_mask(grid, [stop], reach) & walkable


def _walk_within(
    grid: Grid, room: np.ndarray, entry: tuple[int, int], stop: tuple[int, int]
) -> list[tuple[int, int]]:
    """The shortest walk from where a route met a stop's standing room to the
    cell of it nearest the stop, starting at `entry`.

    The search ends as soon as it meets the standing room, which can be most
    of an exemption radius short of the object. Every cell of the room it met
    is reached at the same width, so the widest route has already decided
    which side of the object to arrive on, and this only finishes the trip.
    """
    labels, _ = ndimage.label(room, structure=EIGHT_CONNECTED)
    piece = labels == labels[entry]
    target = _nearest(piece, stop)
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {entry: None}
    frontier = deque([entry])
    while frontier and target not in came_from:
        cell = frontier.popleft()
        for neighbour in _walkable_neighbours(grid, piece, cell):
            if neighbour not in came_from:
                came_from[neighbour] = cell
                frontier.append(neighbour)
    walk = [target]
    while came_from[walk[-1]] is not None:
        walk.append(came_from[walk[-1]])
    return walk[::-1]


def _bottleneck_search(
    grid: Grid,
    walkable: np.ndarray,
    search_field: np.ndarray,
    sources: np.ndarray,
    goals: np.ndarray,
) -> tuple[dict[tuple[int, int], tuple[int, int]], tuple[int, int] | None]:
    """Dijkstra on the widest bottleneck rather than the shortest distance.

    `best[cell]` is the largest clearance a route from any source can guarantee
    all the way to that cell, so relaxing an edge takes the minimum of the
    width so far and the neighbour's own clearance. Cells leave the queue
    widest first, so the first goal cell out is the one the widest route
    reaches. Returns how each cell on the widest route was reached and that
    goal cell, or `None` when no goal can be reached.

    The loop runs over flat Python lists of a grid padded by one unwalkable
    cell on every side, so a neighbour needs no bounds check. A padded flat
    index orders exactly as its (row, col) does, so the queue breaks ties as
    the tuple form did, and the route found is the same cell for cell.
    """
    stride = grid.shape[1] + 2
    walk = _padded(walkable, False).tolist()
    field = _padded(search_field, -1.0).tolist()
    goal = _padded(goals, False).tolist()
    offsets = [d_row * stride + d_col for d_row, d_col in NEIGHBOURS]
    best = [-1.0] * len(walk)
    came_from: dict[int, int] = {}

    queue = []
    for row, col in sources:
        at = (int(row) + 1) * stride + int(col) + 1
        best[at] = field[at]
        queue.append((-field[at], at))
    heapq.heapify(queue)

    arrival = _widest_first(queue, walk, field, goal, offsets, best, came_from)
    return _unpadded(came_from, arrival, stride)


def _padded(values: np.ndarray, border) -> np.ndarray:
    rows, cols = values.shape
    padded = np.full((rows + 2, cols + 2), border, dtype=values.dtype)
    padded[1:-1, 1:-1] = values
    return padded.ravel()


def _widest_first(queue, walk, field, goal, offsets, best, came_from) -> int | None:
    """Pop cells widest first until a goal comes out; relax each neighbour on the way."""
    pop, push = heapq.heappop, heapq.heappush
    while queue:
        negative_width, at = pop(queue)
        width = -negative_width
        if width < best[at]:
            continue
        if goal[at]:
            return at
        for offset in offsets:
            neighbour = at + offset
            if walk[neighbour]:
                candidate = field[neighbour]
                if width < candidate:
                    candidate = width
                if candidate > best[neighbour]:
                    best[neighbour] = candidate
                    came_from[neighbour] = at
                    push(queue, (-candidate, neighbour))
    return None


def _unpadded(came_from: dict[int, int], arrival: int | None, stride: int):
    """The route back from the arrival in (row, col) cells; the rest of the search is never read."""
    def cell(at: int) -> tuple[int, int]:
        row, col = divmod(at, stride)
        return row - 1, col - 1

    if arrival is None:
        return {}, None
    reached, at = {}, arrival
    while at in came_from:
        reached[cell(at)] = cell(came_from[at])
        at = came_from[at]
    return reached, cell(arrival)


def _walkable_neighbours(grid: Grid, walkable: np.ndarray, cell: tuple[int, int]):
    row, col = cell
    for d_row, d_col in NEIGHBOURS:
        neighbour = (row + d_row, col + d_col)
        if grid.contains(*neighbour) and walkable[neighbour]:
            yield neighbour


def _retrace(came_from, arrival) -> list[tuple[int, int]]:
    """Walk back from the arrival to the source it was reached from.

    A source never gains a `came_from` entry: the search seeds it with its own
    clearance, and no route through it can do better than that."""
    path = [arrival]
    while path[-1] in came_from:
        path.append(came_from[path[-1]])
    path.reverse()
    return path


def blockers_at(grid: Grid, cell: tuple[int, int], radius_cells: int) -> list[UUID]:
    """The distinct objects forming the pinch, nearest first.

    A finding names the two things the customer has to squeeze between, so this
    looks outward from the pinch and reports who it runs into.
    """
    row, col = cell
    rows, cols = grid.shape
    reach = max(radius_cells + 2, 3)
    row0, row1 = max(row - reach, 0), min(row + reach + 1, rows)
    col0, col1 = max(col - reach, 0), min(col + reach + 1, cols)

    window_owner = grid.owner[row0:row1, col0:col1]
    hits = np.argwhere(window_owner >= 0)
    if hits.size == 0:
        return []

    distances = (hits[:, 0] + row0 - row) ** 2 + (hits[:, 1] + col0 - col) ** 2
    ordered = hits[np.argsort(distances)]

    found: list[UUID] = []
    for hit_row, hit_col in ordered:
        node_id = grid.node_ids[int(window_owner[hit_row, hit_col])]
        if node_id not in found:
            found.append(node_id)
        if len(found) == 2:
            break
    return found


def sample_cells(cells: list[tuple[int, int]], step: int = 3) -> list[tuple[int, int]]:
    """Which cells become drawable points. One definition, so a path and its
    per-point clearances cannot drift out of step with each other."""
    if not cells:
        return []
    sampled = cells[::step]
    if sampled[-1] != cells[-1]:
        sampled.append(cells[-1])
    return sampled


def path_clearances(
    grid: Grid,
    clearance: np.ndarray,
    cells: list[tuple[int, int]],
    step: int = 3,
    exempt: np.ndarray | None = None,
) -> list[float | None]:
    """Corridor width in inches at each drawn point, for colouring a route.

    `None` where the point falls inside the endpoint exemption. The route
    wanders there because every cell looks equally good to the search, so a
    number would be a measurement of nothing. A viewer should leave those
    stretches uncoloured rather than paint them as tight.
    """
    return [
        None
        if exempt is not None and exempt[cell]
        else to_inches(float(clearance[cell]) * 2)
        for cell in sample_cells(cells, step)
    ]


def world_path(grid: Grid, cells: list[tuple[int, int]], step: int = 3) -> list[Vec3]:
    """Thin the cell path down to something a viewer can draw.

    Kept dense enough that turn detection can still see a bend: at 25 mm cells
    every third sample is 75 mm, so a 180 around a narrow pivot survives.
    """
    return [grid.to_world(row, col) for row, col in sample_cells(cells, step)]


def longest_run_below(
    grid: Grid,
    clearance: np.ndarray,
    cells: list[tuple[int, int]],
    threshold_inches: float,
    exempt: np.ndarray | None = None,
) -> float:
    """The longest unbroken stretch of a route narrower than `threshold_inches`.

    ADA 2010 403.5.1 permits a route to narrow to 32 inches, but only for a run
    of 24 inches at most. A bottleneck says how tight the route gets and says
    nothing about how long it stays that way, so the exception cannot be
    settled without this.

    The threshold is an argument rather than a constant because it belongs to
    the rule pack, where a person has checked it against the source.

    Exempt cells are skipped and break the run. Inside the exemption the route
    wanders and brushes whatever is nearby, so counting those cells reports a
    long narrow stretch on a leg that was never narrow: on the fixture, 75 of
    the 124 sub-36 inch cells on leg 2 were exempt ones.
    """
    runs = runs_below(grid, clearance, cells, threshold_inches, exempt)
    return max((end - start for start, end in runs), default=0.0)


def runs_below(
    grid: Grid,
    clearance: np.ndarray,
    cells: list[tuple[int, int]],
    threshold_inches: float,
    exempt: np.ndarray | None = None,
) -> list[tuple[float, float]]:
    """Every unbroken stretch of a route narrower than `threshold_inches`, as
    inches along the route where each starts and ends.

    403.5.1's exception has two conditions. A narrow stretch may run 24 inches
    at most, and narrow stretches have to be separated by 48 inches of full
    width route, so two 24 inch pinches 10 inches apart do not qualify. The
    longest run alone answers the first condition and not the second.

    A stretch runs from its first narrow cell to its last, and exempt cells
    break it, as in `longest_run_below`.
    """
    limit = to_meters(threshold_inches) / 2
    along = _inches_along(grid, cells)
    runs: list[tuple[float, float]] = []
    start = None
    for index, cell in enumerate(cells):
        narrow = clearance[cell] < limit and not (exempt is not None and exempt[cell])
        if narrow and start is None:
            start = along[index]
        if not narrow and start is not None:
            runs.append((start, along[index - 1]))
            start = None
    if start is not None:
        runs.append((start, along[-1]))
    return runs


def _inches_along(grid: Grid, cells: list[tuple[int, int]]) -> list[float]:
    """How far along the route each cell sits, in inches."""
    steps = [0.0] + [math.dist(a, b) * grid.cell_size for a, b in itertools.pairwise(cells)]
    return [to_inches(metres) for metres in itertools.accumulate(steps)]


def what_sealed_the_route(
    grid: Grid,
    start: tuple[int, int],
    goal: tuple[int, int],
    graph=None,
) -> list[UUID]:
    """The objects standing between a start and an unreachable goal.

    A blocked route with no named obstacle is a dead end for everyone
    downstream: nothing to highlight, nothing to point a camera at, and nothing
    for the fix agent to try moving. If shelving can be pushed aside the honest
    answer is a rearrangement, not a question for the owner.

    The seal is what touches **both** sides. Nearest-to-the-goal is not enough:
    the walls beside a counter are closer to it than the shelving unit across
    the aisle, and naming those tells nobody anything they can act on.

    The two sides are the ones `widest_path` searched: a trip held inside the
    room is sealed when its floor is, whatever lies outside the walls.
    """
    if not (grid.contains(*start) and grid.contains(*goal)):
        return []
    if grid.occupied[start] or grid.occupied[goal]:
        return []
    world = _route_world(grid, start, goal)
    if world.start is None or world.goal is None:
        return []

    regions, _ = ndimage.label(world.walkable)
    here, there = regions[world.start], regions[world.goal]
    if here == there:
        return []

    candidates = _owners_touching(grid, regions == here) & _owners_touching(
        grid, regions == there
    )
    movable = {node.id for node in graph.movable()} if graph else set()
    opening = [
        owner
        for owner in candidates
        if _would_open(grid, owner, world, graph)
    ]

    # On a trip from outside, walls border both pockets and technically "open"
    # the route, because with one gone you can step outside and come back in
    # through the front door. That is true and useless. If anything movable
    # seals the route, name only those: they are what the owner can act on,
    # and a fix agent given a wall has nothing to try.
    actionable = [o for o in opening if grid.node_ids[o] in movable]
    return _nearest_owners(grid, actionable or opening or candidates, goal, movable)


def _would_open(grid: Grid, owner: int, world: _RouteWorld, graph=None) -> bool:
    """Whether taking this one object away reconnects the two sides.

    The exact question a shop owner is asking, and the only way to tell a
    shelving unit standing across the aisle from the walls that happen to
    border both halves of the room.

    Needs the graph to be honest. Freeing cells by `grid.owner` frees whatever
    that node claimed first, which where two objects overlap includes cells the
    other one still covers.
    """
    if graph is None:
        occupied = grid.occupied & (grid.owner != owner)
    else:
        occupied = occupancy_excluding(graph, grid, grid.node_ids[owner])
    regions, _ = ndimage.label(_walkable(grid, occupied, world.in_the_room))
    return regions[world.start] != 0 and regions[world.start] == regions[world.goal]


def _owners_touching(grid: Grid, region: np.ndarray) -> set[int]:
    """Which objects this pocket of free space runs into.

    Compared by object rather than by cell. A display case is two dozen cells
    thick, so the shell of occupied cells around one side of it never meets the
    shell around the other, and looking for a shared cell finds nothing.
    """
    shell = ndimage.binary_dilation(region) & grid.occupied
    return {owner for owner in grid.owner[shell].tolist() if owner >= 0}


def _nearest_owners(
    grid: Grid,
    owners,
    goal: tuple[int, int],
    movable: set[UUID],
    limit: int = 2,
) -> list[UUID]:
    """Furniture first, then whatever is closest to where they were heading."""
    ranked = []
    for owner in owners:
        cells = np.argwhere(grid.owner == owner)
        if cells.size == 0:
            continue
        distances = (cells[:, 0] - goal[0]) ** 2 + (cells[:, 1] - goal[1]) ** 2
        node_id = grid.node_ids[owner]
        ranked.append((node_id not in movable, int(distances.min()), owner))
    ranked.sort()
    return [grid.node_ids[owner] for _, _, owner in ranked[:limit]]


def _direction_at(path: list[tuple[int, int]], index: int) -> tuple[float, float]:
    before = path[max(index - 2, 0)]
    after = path[min(index + 2, len(path) - 1)]
    dy, dx = after[0] - before[0], after[1] - before[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _march(
    grid: Grid, origin: tuple[int, int], step: tuple[float, float], limit: int
):
    """Walk out from a cell until something solid stops you."""
    row, col = float(origin[0]), float(origin[1])
    for _ in range(limit):
        row += step[1]
        col += step[0]
        cell = (int(round(row)), int(round(col)))
        if not grid.contains(*cell):
            return None
        if grid.occupied[cell]:
            return cell
    return None


def straddling_blockers(
    grid: Grid, path: list[tuple[int, int]], pinch: tuple[int, int], reach: float = 4.0
):
    """The two things the route actually squeezes between at the pinch.

    Nearest-two-objects is not the same question. On the fixture's counter leg
    it names the counter and a table that do not overlap in x at all, so the
    "gap" between them is a diagonal across open floor rather than anything the
    route passes through.

    This measures across the corridor: step out from the pinch at right angles
    to the direction of travel, in both directions, and report what each side
    runs into.
    """
    if pinch not in path:
        return None
    index = path.index(pinch)
    dx, dy = _direction_at(path, index)
    across = (-dy, dx)
    limit = int(reach / grid.cell_size)

    left = _march(grid, pinch, across, limit)
    right = _march(grid, pinch, (-across[0], -across[1]), limit)
    if left is None or right is None:
        return None

    left_owner = grid.owner_at(*left)
    right_owner = grid.owner_at(*right)
    if left_owner is None or right_owner is None or left_owner == right_owner:
        return None
    return left_owner, right_owner
