"""Lay the seating out again as a whole room, then let the gate measure it.

Moving the pieces in a finding's way one at a time keeps every mess the scan
started with and adds new ones: a table parked wherever it first stopped
blocking, and chairs left behind or dragged along still overlapping. This
plans the seating from scratch instead. Tables go square to the walls, as close
to a wall as they fit, with breathing room between them. Nothing ever covers a
stop or the square where a customer turns around. The aisles between the stops
are only a guess at where the route will run, so a piece that fits nowhere else
may cover one at a steep price, and the gate's measurement decides. Each
table offers seats along its long sides where there is room for them, the
chairs take the nearest seats, and any chair left over stands against a wall
facing into the room. Everything that is not a table or a chair stays where
the scan found it.

Planning happens in the room's own frame, where the walls run along the axes.
Every piece is then an upright rectangle, and finding where one fits is a
lookup in a summed-area table rather than polygon geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import product

import numpy as np

from standardphysics_contracts import NodeMove, Scenario, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline.footprints import polygon_bounds, rotation_about_z
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks import roles
from ..checks.route_geometry import reversal_stops, setback_point
from ..checks.turning_space import APPROACH_SETBACK_INCHES
from ..rules import AgentRulePack
from .composition import BREATHING_ROOM_METERS, Composition
from .constraints import SWING_KINDS, door_keep_clear, interior_bounds, violations
from .moves import apply_moves
from .strategies import Candidate

STRATEGY = "arrange_room"

CELL_METERS = 0.05
SAMPLE_METERS = 0.02
"""Spacing of the points a footprint is stamped onto the grid with; finer than a cell so nothing slips between."""

CHAIR_PITCH_METERS = 0.68
"""Table length each seat needs, so neighbouring chairs never touch."""

CHAIR_TUCK_METERS = 0.02
AISLE_MARGIN_METERS = 0.05
"""Kept clear on each side of an aisle, so a piece beside it leaves the full width."""

STOP_SQUARE_METERS = to_meters(36.0)

WALL_WEIGHT = 2.0
"""Score per metre a piece stands off the wall it should back onto."""

EFFORT_WEIGHT = 0.2
"""Score per metre a piece travels, per square metre of footprint."""

SEAT_VALUE = 3.0
"""Score for each chair a table can seat, so a table gives up a wall before it gives up its chairs."""

AISLE_WEIGHT = 40.0
"""Score per square metre of aisle a piece covers, far above anything a wall or a seat is worth."""

ROW_GAP_METERS = 0.05
"""Space between spare chairs standing side by side."""

ROW_WEIGHT = 2.0
"""Score per metre a spare chair stands from the one placed before it, so spares form one row instead of scattering."""

BREATHING_TIERS = (BREATHING_ROOM_METERS, 0.3, 0.2)
"""Breathing room to plan with, roomiest first.

A tighter plan is only ever another candidate. `Composition.cost` charges for
the lost breathing room, so it is measured after every roomier plan.
"""

MAX_PLANS = 16
NEGLIGIBLE_METERS = 0.005
NEGLIGIBLE_DEGREES = 0.5

FACINGS = ((0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0))
BACKS = ("bottom", "left", "top", "right")
"""The edge behind a chair facing each way in `FACINGS`."""

Bounds = tuple[float, float, float, float]


def _rotate(x, y, degrees: float):
    cos_d, sin_d = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return x * cos_d - y * sin_d, x * sin_d + y * cos_d


def _heading(node: SceneNode) -> float:
    cos_t, sin_t = rotation_about_z(node)
    return math.degrees(math.atan2(sin_t, cos_t))


def _wrapped(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def _facing_heading(facing: tuple[float, float]) -> float:
    """The heading that turns a RoomPlan chair, which faces along its local -y, toward `facing`."""
    return math.degrees(math.atan2(facing[0], -facing[1]))


def _move_to(node: SceneNode, x: float, y: float, heading: float) -> NodeMove:
    position = node.transform.position
    return NodeMove(
        node_id=node.id,
        delta_translation=Vec3(x=x - position.x, y=y - position.y, z=0.0),
        delta_rotation_z_degrees=_wrapped(heading - _heading(node)),
    )


def _window_sums(mask: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """How many marked cells each rows-by-cols window holds, indexed by its first cell."""
    table = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    table[1:, 1:] = mask.cumsum(axis=0).cumsum(axis=1)
    return table[rows:, cols:] - table[:-rows, cols:] - table[rows:, :-cols] + table[:-rows, :-cols]


def _grown(mask: np.ndarray, cells: int) -> np.ndarray:
    return _window_sums(np.pad(mask, cells), 2 * cells + 1, 2 * cells + 1) > 0


def _samples(half: float) -> np.ndarray:
    return np.linspace(-half, half, max(2, int(2 * half / SAMPLE_METERS) + 2))


def _inscribed(bounds: Bounds, degrees: float) -> Bounds:
    """The upright rectangle inside a world-aligned one, once it is turned into the room's frame.

    The frame cannot come from turning the graph itself: a scanned floor lies in
    its local x-z plane, and recomposing its transform as a turn about Z
    flattens it to nothing.
    """
    low_x, low_y, high_x, high_y = bounds
    xs, ys = zip(*(_rotate(x, y, degrees) for x in (low_x, high_x) for y in (low_y, high_y)))
    return sorted(xs)[1], sorted(ys)[1], sorted(xs)[2], sorted(ys)[2]


@dataclass
class _Floor:
    """The room's floor in its own frame, one cell per `CELL_METERS`.

    `blocked` is where nothing may go. `aisles` is where a piece should not go.
    """

    axis: float
    x0: float
    y0: float
    blocked: np.ndarray
    aisles: np.ndarray

    @classmethod
    def of(cls, graph: SceneGraph, axis: float, planned: set) -> _Floor | None:
        bounds = interior_bounds(graph)
        if bounds is None:
            return None
        low_x, low_y, high_x, high_y = _inscribed(bounds, -axis)
        shape = (math.ceil((high_y - low_y) / CELL_METERS), math.ceil((high_x - low_x) / CELL_METERS))
        floor = cls(axis, low_x, low_y, np.zeros(shape, dtype=bool), np.zeros(shape, dtype=bool))
        for node in graph.nodes:
            if node.id not in planned and (node.kind == "wall" or blocks_floor(node)):
                floor.mark_node(node)
        for door in (node for node in graph.nodes if node.kind in SWING_KINDS):
            floor.mark_world_box(polygon_bounds(door_keep_clear(door)))
        floor.blocked = _grown(floor.blocked, 1)
        return floor

    def to_frame(self, point) -> tuple[float, float]:
        return _rotate(point.x, point.y, -self.axis)

    def mark_points(self, x: np.ndarray, y: np.ndarray) -> None:
        rows = np.floor((y - self.y0) / CELL_METERS).astype(int)
        cols = np.floor((x - self.x0) / CELL_METERS).astype(int)
        inside = (rows >= 0) & (rows < self.blocked.shape[0]) & (cols >= 0) & (cols < self.blocked.shape[1])
        self.blocked[rows[inside], cols[inside]] = True

    def mark_node(self, node: SceneNode) -> None:
        cos_t, sin_t = rotation_about_z(node)
        centre = node.transform.position
        across, along = np.meshgrid(_samples(node.dimensions.x / 2), _samples(node.dimensions.y / 2))
        x, y = _rotate(centre.x + across * cos_t - along * sin_t,
                       centre.y + across * sin_t + along * cos_t, -self.axis)
        self.mark_points(x.ravel(), y.ravel())

    def mark_world_box(self, bounds: Bounds) -> None:
        low_x, low_y, high_x, high_y = bounds
        x, y = np.meshgrid(np.arange(low_x, high_x + SAMPLE_METERS, SAMPLE_METERS),
                           np.arange(low_y, high_y + SAMPLE_METERS, SAMPLE_METERS))
        frame_x, frame_y = _rotate(x, y, -self.axis)
        self.mark_points(frame_x.ravel(), frame_y.ravel())

    def mark_box(self, mask: np.ndarray, low_x: float, low_y: float, high_x: float, high_y: float) -> None:
        rows, cols = mask.shape
        bottom = min(max(math.floor((low_y - self.y0) / CELL_METERS), 0), rows)
        top = min(max(math.ceil((high_y - self.y0) / CELL_METERS), 0), rows)
        left = min(max(math.floor((low_x - self.x0) / CELL_METERS), 0), cols)
        right = min(max(math.ceil((high_x - self.x0) / CELL_METERS), 0), cols)
        mask[bottom:top, left:right] = True

    def mark_square(self, mask: np.ndarray, centre: tuple[float, float], half: float) -> None:
        self.mark_box(mask, centre[0] - half, centre[1] - half, centre[0] + half, centre[1] + half)

    def with_aisles(self, scenario: Scenario, rules: AgentRulePack, corners: tuple[int, ...]) -> _Floor:
        """A fresh copy of this floor to plan on, with one choice of aisles marked."""
        floor = replace(self, blocked=self.blocked.copy(), aisles=np.zeros_like(self.blocked))
        stops = [self.to_frame(stop.position) for stop in scenario.stops]
        half = to_meters(rules.by_id("route_clear_width").threshold) / 2 + AISLE_MARGIN_METERS
        for (start, end), corner in zip(zip(stops, stops[1:]), corners):
            for a, b in _legs(start, end, corner):
                floor.mark_box(floor.aisles, min(a[0], b[0]) - half, min(a[1], b[1]) - half,
                               max(a[0], b[0]) + half, max(a[1], b[1]) + half)
        turn = to_meters(rules.by_id("turning_space").parameter("circle_diameter_inches")) / 2 + AISLE_MARGIN_METERS
        setback = to_meters(APPROACH_SETBACK_INCHES)
        for index in reversal_stops(scenario.stops):
            floor.mark_square(floor.blocked, self.to_frame(setback_point(scenario.stops, index, setback)), turn)
        for stop in stops:
            floor.mark_square(floor.blocked, stop, STOP_SQUARE_METERS / 2)
        return floor


def _legs(start, end, corner: int):
    """An aisle between two stops as two straight runs, turning at one of the two corners."""
    via = (end[0], start[1]) if corner == 0 else (start[0], end[1])
    return [(start, via), (via, end)]


def _corner_choices(scenario: Scenario, floor: _Floor) -> list[tuple[int, ...]]:
    stops = [floor.to_frame(stop.position) for stop in scenario.stops]
    per_leg = [
        (0,) if min(abs(b[0] - a[0]), abs(b[1] - a[1])) < STOP_SQUARE_METERS else (0, 1)
        for a, b in zip(stops, stops[1:])
    ]
    return list(product(*per_leg))[:MAX_PLANS]


@dataclass(frozen=True)
class _Seating:
    """A table, which way its long side runs, and how many seats it offers on each side."""

    table: SceneNode
    orientation: float
    sides: tuple[int, int]
    chair_depth: float

    @property
    def node(self) -> SceneNode:
        return self.table

    @property
    def long(self) -> float:
        return max(self.table.dimensions.x, self.table.dimensions.y)

    @property
    def short(self) -> float:
        return min(self.table.dimensions.x, self.table.dimensions.y)

    @property
    def seats(self) -> int:
        return sum(self.sides)

    def extents(self) -> Bounds:
        """Frame offsets from the table's centre to the setting's edges, seats included."""
        along = self.long / 2
        reach = CHAIR_TUCK_METERS + self.chair_depth
        low = -self.short / 2 - (reach if self.sides[0] else 0.0)
        high = self.short / 2 + (reach if self.sides[1] else 0.0)
        if self.orientation == 0.0:
            return -along, low, along, high
        return -high, -along, -low, along

    def backs_onto(self) -> tuple[str, ...]:
        """The edges that should stand against a wall: the seatless side, or an end."""
        across = ("bottom", "top") if self.orientation == 0.0 else ("right", "left")
        ends = ("left", "right") if self.orientation == 0.0 else ("bottom", "top")
        if self.sides == (0, 0):
            return (*across, *ends)
        if self.sides[1] == 0:
            return (across[1],)
        if self.sides[0] == 0:
            return (across[0],)
        return ends

    def move(self, x: float, y: float, axis: float) -> NodeMove:
        long_along_x = self.table.dimensions.x >= self.table.dimensions.y
        base = self.orientation + axis - (0.0 if long_along_x else 90.0)
        heading = min((base, base + 180.0), key=lambda target: abs(_wrapped(target - _heading(self.table))))
        return _move_to(self.table, *_rotate(x, y, axis), heading)

    def slots(self, x: float, y: float, axis: float) -> list[tuple[tuple[float, float], float]]:
        """Where each seat is, in the world, and the heading that faces it toward the table."""
        radians = math.radians(self.orientation)
        along, across = (math.cos(radians), math.sin(radians)), (-math.sin(radians), math.cos(radians))
        found = []
        for side, count in ((-1, self.sides[0]), (1, self.sides[1])):
            offset = side * (self.short / 2 + CHAIR_TUCK_METERS + self.chair_depth / 2)
            for index in range(count):
                s = -self.long / 2 + self.long / count * (index + 0.5)
                seat = (x + along[0] * s + across[0] * offset, y + along[1] * s + across[1] * offset)
                facing = (-side * across[0], -side * across[1])
                found.append((_rotate(*seat, axis), _facing_heading(facing) + axis))
        return found


@dataclass(frozen=True)
class _SpareChair:
    """A chair no table has a seat for, standing with its back to a wall."""

    chair: SceneNode
    facing: int

    seats = 0

    @property
    def node(self) -> SceneNode:
        return self.chair

    def extents(self) -> Bounds:
        width, depth = self.chair.dimensions.x, self.chair.dimensions.y
        if self.facing % 2:
            width, depth = depth, width
        return -width / 2, -depth / 2, width / 2, depth / 2

    def backs_onto(self) -> tuple[str, ...]:
        return (BACKS[self.facing],)

    def move(self, x: float, y: float, axis: float) -> NodeMove:
        return _move_to(self.chair, *_rotate(x, y, axis), _facing_heading(FACINGS[self.facing]) + axis)

    def slots(self, x: float, y: float, axis: float) -> list:
        return []


Piece = _Seating | _SpareChair


def _per_side(table: SceneNode) -> int:
    return max(1, int(max(table.dimensions.x, table.dimensions.y) / CHAIR_PITCH_METERS))


def _seatings(table: SceneNode, need: int, chair_depth: float) -> list[_Seating]:
    per_side = _per_side(table)
    one, both = min(need, per_side), min(need, 2 * per_side)
    patterns = dict.fromkeys([(0, 0), (one, 0), (0, one), (math.ceil(both / 2), both // 2)])
    return [
        _Seating(table, orientation, sides, chair_depth)
        for orientation in (0.0, 90.0)
        for sides in patterns
    ]


@dataclass(frozen=True)
class _Spot:
    piece: Piece
    x: float
    y: float
    score: float

    def keep_out(self, floor: _Floor, breathing: float) -> None:
        low_x, low_y, high_x, high_y = self.piece.extents()
        floor.mark_box(floor.blocked, self.x + low_x - breathing, self.y + low_y - breathing,
                       self.x + high_x + breathing, self.y + high_y + breathing)


def _free_corners(occupied: np.ndarray, extents: Bounds) -> tuple[np.ndarray, np.ndarray, int, int] | None:
    low_x, low_y, high_x, high_y = extents
    height, width = math.ceil((high_y - low_y) / CELL_METERS), math.ceil((high_x - low_x) / CELL_METERS)
    if height > occupied.shape[0] or width > occupied.shape[1]:
        return None
    rows, cols = np.nonzero(_window_sums(occupied, height, width) == 0)
    return (rows, cols, height, width) if rows.size else None


def _best_for(floor: _Floor, piece: Piece, beside: tuple[float, float] | None = None) -> _Spot | None:
    """The lowest-scoring free place for one piece."""
    extents = piece.extents()
    free = _free_corners(floor.blocked, extents)
    if free is None:
        return None
    rows, cols, _, _ = free
    x = floor.x0 + cols * CELL_METERS - extents[0]
    y = floor.y0 + rows * CELL_METERS - extents[1]
    score = _scores(floor, piece, free, x, y, beside)
    best = int(np.argmin(score))
    return _Spot(piece, float(x[best]), float(y[best]), float(score[best]))


def _scores(floor: _Floor, piece: Piece, free, x: np.ndarray, y: np.ndarray,
            beside: tuple[float, float] | None) -> np.ndarray:
    """Off the aisles, against its wall, near where it was, seating the most, and beside `beside`."""
    rows, cols, height, width = free
    gaps = {
        "left": cols * CELL_METERS,
        "right": (floor.blocked.shape[1] - cols - width) * CELL_METERS,
        "bottom": rows * CELL_METERS,
        "top": (floor.blocked.shape[0] - rows - height) * CELL_METERS,
    }
    origin = floor.to_frame(piece.node.transform.position)
    footprint_area = piece.node.dimensions.x * piece.node.dimensions.y
    covered = _window_sums(floor.aisles, height, width)[rows, cols] * CELL_METERS**2
    score = (
        AISLE_WEIGHT * covered
        + WALL_WEIGHT * np.minimum.reduce([gaps[edge] for edge in piece.backs_onto()])
        + EFFORT_WEIGHT * footprint_area * np.hypot(x - origin[0], y - origin[1])
        - SEAT_VALUE * piece.seats
    )
    if beside is not None:
        score = score + ROW_WEIGHT * np.hypot(x - beside[0], y - beside[1])
    return score


def _place(floor: _Floor, options: list[Piece], breathing: float,
           beside: tuple[float, float] | None = None) -> _Spot | None:
    spots = [spot for piece in options if (spot := _best_for(floor, piece, beside))]
    best = min(spots, key=lambda spot: spot.score, default=None)
    if best is not None:
        best.keep_out(floor, breathing)
    return best


def _seat(chairs: list[SceneNode], slots) -> list[NodeMove]:
    """Each seat takes the nearest chair still standing, which keeps chairs from crossing the room."""
    moves = []
    for (x, y), heading in slots:
        chair = min(chairs, key=lambda node: math.dist((node.transform.position.x, node.transform.position.y), (x, y)))
        chairs.remove(chair)
        moves.append(_move_to(chair, x, y, heading))
    return moves


def _plan(floor: _Floor, tables: list[SceneNode], chairs: list[SceneNode], breathing: float) -> list[NodeMove] | None:
    chair_depth = max((chair.dimensions.y for chair in chairs), default=0.0)
    spots, need = [], len(chairs)
    for table in tables:
        spot = _place(floor, _seatings(table, need, chair_depth), breathing)
        if spot is None:
            return None
        spots.append(spot)
        need -= spot.piece.seats
    standing = list(chairs)
    moves = _seat(standing, [slot for spot in spots for slot in spot.piece.slots(spot.x, spot.y, floor.axis)])
    beside = None
    for chair in standing:
        spot = _place(floor, [_SpareChair(chair, facing) for facing in range(len(FACINGS))], ROW_GAP_METERS, beside)
        if spot is None:
            return None
        spots.append(spot)
        beside = (spot.x, spot.y)
    moves += [spot.piece.move(spot.x, spot.y, floor.axis) for spot in spots]
    return [move for move in moves if _moves_anything(move)]


def _moves_anything(move: NodeMove) -> bool:
    slide = math.hypot(move.delta_translation.x, move.delta_translation.y)
    return slide > NEGLIGIBLE_METERS or abs(move.delta_rotation_z_degrees) > NEGLIGIBLE_DEGREES


def arrangements(graph: SceneGraph, scenario: Scenario, rules: AgentRulePack) -> list[Candidate]:
    """Whole-room seating plans that break no hard constraint, cheapest `Composition.cost` first."""
    tables = sorted(
        (node for node in roles.dining_surfaces(graph) if node.movable),
        key=lambda node: -node.dimensions.x * node.dimensions.y,
    )
    chairs = [node for node in roles.seating(graph) if node.movable]
    if not tables:
        return []
    composition = Composition.of(graph)
    floor = _Floor.of(graph, composition.axis_degrees, {node.id for node in (*tables, *chairs)})
    if floor is None:
        return []
    found: dict[tuple, Candidate] = {}
    for breathing, corners in product(BREATHING_TIERS, _corner_choices(scenario, floor)):
        moves = _plan(floor.with_aisles(scenario, rules, corners), tables, chairs, breathing)
        if not moves or violations(graph, apply_moves(graph, moves)):
            continue
        key = tuple(sorted((str(m.node_id), round(m.delta_translation.x, 2), round(m.delta_translation.y, 2)) for m in moves))
        found.setdefault(key, Candidate(STRATEGY, moves, composition.cost(moves)))
    return sorted(found.values(), key=lambda candidate: candidate.disruption)[:MAX_PLANS]
