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
import math
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from scipy import ndimage
from standardphysics_contracts import Vec3, to_inches, to_meters

from .occupancy import Grid, occupancy_excluding

NEIGHBOURS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]

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
    """Whether a stop is on the scanned floor rather than the ground outside."""
    return (
        grid.indoors is not None
        and grid.contains(*cell)
        and bool(grid.indoors[cell])
    )


def _connected(walkable: np.ndarray, start, goal) -> bool:
    regions, _ = ndimage.label(walkable)
    return regions[start] != 0 and regions[start] == regions[goal]


def _route_world(grid: Grid, start, goal):
    """The cells a route may use, with its two stops snapped into them.

    `occupancy.OUTSIDE_MARGIN` keeps open ground beyond the walls so a customer
    arriving from the street has somewhere to stand. Nothing is out there, which
    makes it the widest corridor in the capture, so a scan whose walls do not
    close sends the search out through the gap and around the building: on the
    Apple living room sample 92 per cent of the route ran outdoors, and the
    width it reported was the width of the garden.

    A trip between two stops in the room is a trip through the room, so the
    outside is held back for it. It opens again when the room cannot answer on
    its own: a stop standing outside, or a goal the floor genuinely cannot
    reach without leaving.
    """
    free = ~grid.occupied
    if _stands_indoors(grid, start) and _stands_indoors(grid, goal):
        inside = free & grid.indoors
        here, there = _nearest(inside, start), _nearest(inside, goal)
        if here is not None and there is not None and _connected(inside, here, there):
            return inside, here, there
    return free, _nearest(free, start), _nearest(free, goal)


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
) -> PathResult:
    """Bottleneck Dijkstra: maximise the smallest clearance along the route."""
    walkable, start, goal = _route_world(grid, start, goal)
    if start is None or goal is None:
        return PathResult(0.0, None, [], reachable=False)

    radius = _exemption_radius(grid, start, goal, endpoint_exemption)
    exempt = _exempt_mask(grid, [start, goal], radius)
    if extra_exempt is not None:
        exempt |= extra_exempt
    search_field = np.where(exempt, np.inf, clearance)

    best, came_from = _bottleneck_search(grid, walkable, search_field, start, goal)

    if best[goal] < 0:
        return PathResult(0.0, None, [], reachable=False)

    path = _retrace(came_from, start, goal)
    measured = [cell for cell in path if not exempt[cell]]
    if not measured:
        measured = path
    pinch = min(measured, key=lambda cell: clearance[cell])
    return PathResult(
        float(clearance[pinch]), pinch, path, reachable=True, exempt=exempt
    )


def _bottleneck_search(
    grid: Grid,
    walkable: np.ndarray,
    search_field: np.ndarray,
    start: tuple[int, int],
    goal: tuple[int, int],
) -> tuple[np.ndarray, dict[tuple[int, int], tuple[int, int]]]:
    """Dijkstra on the widest bottleneck rather than the shortest distance.

    `best[cell]` is the largest clearance a route from start can guarantee all
    the way to that cell, so relaxing an edge takes the minimum of the width so
    far and the neighbour's own clearance.
    """
    rows, cols = grid.shape
    best = np.full((rows, cols), -1.0)
    came_from: dict[tuple[int, int], tuple[int, int]] = {}

    best[start] = search_field[start]
    queue = [(-best[start], start)]

    while queue:
        negative_width, cell = heapq.heappop(queue)
        width = -negative_width
        if width < best[cell]:
            continue
        if cell == goal:
            break
        for neighbour in _walkable_neighbours(grid, walkable, cell):
            candidate = min(width, search_field[neighbour])
            if candidate > best[neighbour]:
                best[neighbour] = candidate
                came_from[neighbour] = cell
                heapq.heappush(queue, (-candidate, neighbour))

    return best, came_from


def _walkable_neighbours(grid: Grid, walkable: np.ndarray, cell: tuple[int, int]):
    row, col = cell
    for d_row, d_col in NEIGHBOURS:
        neighbour = (row + d_row, col + d_col)
        if grid.contains(*neighbour) and walkable[neighbour]:
            yield neighbour


def _retrace(came_from, start, goal) -> list[tuple[int, int]]:
    path = [goal]
    while path[-1] != start:
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
    longest = 0.0
    current = 0.0
    previous = None
    limit = to_meters(threshold_inches) / 2

    for cell in cells:
        if exempt is not None and exempt[cell]:
            longest = max(longest, current)
            current = 0.0
            previous = None
            continue
        below = clearance[cell] < limit
        if below and previous is not None:
            current += math.dist(previous, cell) * grid.cell_size
        elif below:
            current = 0.0
        else:
            longest = max(longest, current)
            current = 0.0
        previous = cell if below else None

    return to_inches(max(longest, current))


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
    """
    free = ~grid.occupied
    if not (grid.contains(*start) and grid.contains(*goal)):
        return []
    if not (free[start] and free[goal]):
        return []

    regions, _ = ndimage.label(free)
    here, there = regions[start], regions[goal]
    if here == there or here == 0 or there == 0:
        return []

    candidates = _owners_touching(grid, regions == here) & _owners_touching(
        grid, regions == there
    )
    movable = {node.id for node in graph.movable()} if graph else set()
    opening = [
        owner
        for owner in candidates
        if _would_open(grid, owner, start, goal, graph)
    ]

    # Walls border both pockets and technically "open" the route, because with
    # one gone you can step outside and come back in through the front door.
    # That is true and useless. If anything movable seals the route, name only
    # those: they are what the owner can act on, and a fix agent given a wall
    # has nothing to try.
    actionable = [o for o in opening if grid.node_ids[o] in movable]
    return _nearest_owners(grid, actionable or opening or candidates, goal, movable)


def _would_open(
    grid: Grid,
    owner: int,
    start: tuple[int, int],
    goal: tuple[int, int],
    graph=None,
) -> bool:
    """Whether taking this one object away reconnects the two sides.

    The exact question a shop owner is asking, and the only way to tell a
    shelving unit standing across the aisle from the walls that happen to
    border both halves of the room.

    Needs the graph to be honest. Freeing cells by `grid.owner` frees whatever
    that node claimed first, which where two objects overlap includes cells the
    other one still covers.
    """
    if graph is None:
        free = ~grid.occupied | (grid.owner == owner)
    else:
        free = ~occupancy_excluding(graph, grid, grid.node_ids[owner])
    regions, _ = ndimage.label(free)
    return regions[start] != 0 and regions[start] == regions[goal]


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
