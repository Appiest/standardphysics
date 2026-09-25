"""Procedural shop rooms for training.

A room is built in four passes: a shell (one to three rectangles joined into a
plain, L, T or long narrow plan, with a front door and sometimes a back door), a
fixed service counter with its staff zone, reserved walking corridors between the
doors and the counter, and then furniture groups drawn from the shop type's
recipe until the room reaches its target density. Every group is checked against
the hard constraints the fix loop uses, so a generated room is one somebody could
really have.

Labels are taken from the exact names in `standardphysics_agents.checks.roles`,
because the checker recognises counters, tables and seats by name and nothing
else. Sizes are ordinary trade sizes for each piece.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from standardphysics_agents.fix.constraints import violations
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, to_meters
from standardphysics_pipeline import gap_between
from standardphysics_pipeline.footprints import Polygon, contains_point

NAMESPACE = uuid.UUID("5b0f6a53-2f47-4c52-a7f3-6f1e9d2c4b10")
WALL_THICKNESS = 0.1
WALL_HEIGHT = 3.0
WALL_GAP = 0.03
EDGE = 1e-3


class Unbuildable(ValueError):
    """This draw of the plan cannot hold the shop, so the caller draws again."""


@dataclass(frozen=True)
class Piece:
    label: str
    category: str
    size: tuple[float, float, float]
    movable: bool = True


PIECES = {
    "chair": Piece("Chair", "chair", (0.45, 0.5, 0.85)),
    "stool": Piece("Bar stool", "stool", (0.4, 0.4, 0.75)),
    "armchair": Piece("Armchair", "chair", (0.8, 0.8, 0.8)),
    "sofa": Piece("Sofa", "sofa", (1.9, 0.85, 0.8)),
    "banquette": Piece("Bench", "bench", (1.0, 0.6, 0.9)),
    "waiting_bench": Piece("Bench", "bench", (1.5, 0.5, 0.45)),
    "salon_chair": Piece("Chair", "chair", (0.65, 0.65, 1.0)),
    "task_chair": Piece("Chair", "chair", (0.6, 0.6, 1.0)),
    "cafe_table": Piece("Cafe table", "table", (0.6, 0.6, 0.75)),
    "four_top": Piece("Table", "table", (0.8, 0.8, 0.75)),
    "communal_table": Piece("Table", "table", (2.4, 0.9, 0.75)),
    "bar_table": Piece("Bar table", "table", (0.6, 0.6, 1.05)),
    "window_ledge": Piece("Bar table", "table", (1.8, 0.4, 1.05)),
    "coffee_table": Piece("Coffee table", "table", (1.0, 0.55, 0.45)),
    "desk": Piece("Desk", "table", (1.4, 0.7, 0.75)),
    "display_table": Piece("Display table", "storage", (1.2, 0.8, 0.8)),
    "shelf": Piece("Shelving unit", "storage", (0.9, 0.4, 1.8)),
    "bookcase": Piece("Bookcase", "storage", (0.8, 0.3, 2.0)),
    "gondola": Piece("Gondola shelf", "storage", (1.2, 0.9, 1.5)),
    "book_shelf": Piece("Double-sided bookshelf", "storage", (1.2, 0.6, 1.5)),
    "cooler": Piece("Drinks fridge", "refrigerator", (0.75, 0.8, 2.0), False),
    "bread_rack": Piece("Bread rack", "storage", (1.2, 0.5, 1.8)),
    "pastry_case": Piece("Pastry display case", "storage", (1.2, 0.7, 1.2)),
    "freezer_case": Piece("Ice cream freezer", "refrigerator", (1.5, 0.9, 1.2), False),
    "clothing_rack": Piece("Clothing rack", "storage", (1.2, 0.5, 1.5)),
    "mannequin": Piece("Mannequin", "storage", (0.5, 0.4, 1.8)),
    "fitting_room": Piece("Fitting room", "storage", (1.0, 1.0, 2.1), False),
    "styling_station": Piece("Styling station", "storage", (1.0, 0.45, 1.8)),
    "shampoo_sink": Piece("Shampoo sink", "sink", (0.7, 1.2, 1.0), False),
    "file_cabinet": Piece("File cabinet", "storage", (0.4, 0.6, 1.3)),
    "printer": Piece("Printer", "storage", (0.6, 0.6, 1.0)),
    "planter": Piece("Planter", "storage", (0.5, 0.5, 1.1)),
    "trash": Piece("Trash and recycling bins", "storage", (0.8, 0.45, 1.0)),
    "condiments": Piece("Condiment station", "storage", (1.0, 0.5, 1.0)),
    "menu_stand": Piece("Menu stand", "storage", (0.5, 0.4, 1.4)),
    "atm": Piece("ATM", "storage", (0.55, 0.6, 1.5), False),
    "host_stand": Piece("Host stand", "storage", (0.6, 0.5, 1.2)),
    "column": Piece("Column", "column", (0.4, 0.4, WALL_HEIGHT), False),
    "back_bar": Piece("Back bar", "storage", (1.0, 0.6, 0.9), False),
    "lowered": Piece("Lowered counter section", "counter", (to_meters(36.0), 0.7, to_meters(36.0)), False),
    "point_of_sale": Piece("Point of sale", "storage", (0.2, 0.16, 0.08)),
}


@dataclass(frozen=True)
class Placed:
    """A piece in its group's frame: x runs along the group, y runs out into the room."""

    kind: str
    x: float
    y: float
    heading: float = 0.0
    width: float | None = None
    unit: int = 0
    """Pieces sharing a unit stay or go together; unit 0 means the whole group is one."""


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    d: float
    heading: float = 0.0

    def corners(self, grow: float = 0.0) -> Polygon:
        c, s = math.cos(math.radians(self.heading)), math.sin(math.radians(self.heading))
        hw, hd = self.w / 2 + grow, self.d / 2 + grow
        return [(self.cx + x * c - y * s, self.cy + x * s + y * c)
                for x, y in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def inside(self, x: float, y: float) -> bool:
        return self.x0 + EDGE < x < self.x1 - EDGE and self.y0 + EDGE < y < self.y1 - EDGE

    def polygon(self) -> Polygon:
        return [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]


@dataclass(frozen=True)
class Wall:
    """An inner wall face from a to b, with the room on its left."""

    a: tuple[float, float]
    b: tuple[float, float]

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def heading(self) -> float:
        return math.degrees(math.atan2(self.b[1] - self.a[1], self.b[0] - self.a[0]))

    def point(self, t: float, out: float = 0.0) -> tuple[float, float]:
        ux, uy = (self.b[0] - self.a[0]) / self.length, (self.b[1] - self.a[1]) / self.length
        return (self.a[0] + ux * t - uy * out, self.a[1] + uy * t + ux * out)


# Shells --------------------------------------------------------------------


def _plain(rng: random.Random, width: float, depth: float) -> list[Rect]:
    return [Rect(0, 0, width, depth)]


def _l_plan(rng: random.Random, width: float, depth: float) -> list[Rect]:
    wing_w, wing_d = rng.uniform(2.2, 4.0), rng.uniform(2.5, min(5.0, depth))
    if rng.random() < 0.5:
        return [Rect(0, 0, width, depth), Rect(width, depth - wing_d, width + wing_w, depth)]
    return [Rect(0, 0, width, depth), Rect(-wing_w, depth - wing_d, 0, depth)]


def _t_plan(rng: random.Random, width: float, depth: float) -> list[Rect]:
    back_w = rng.uniform(2.4, max(2.5, width * 0.6))
    left = rng.uniform(0.3, width - back_w - 0.3) if width - back_w > 0.6 else (width - back_w) / 2
    return [Rect(0, 0, width, depth), Rect(left, depth, left + back_w, depth + rng.uniform(2.5, 4.5))]


def _long_plan(rng: random.Random, width: float, depth: float) -> list[Rect]:
    return [Rect(0, 0, rng.uniform(3.6, 5.0), rng.uniform(9.0, 15.0))]


PLANS: tuple[tuple[Callable[[random.Random, float, float], list[Rect]], int], ...] = (
    (_plain, 4), (_l_plan, 3), (_t_plan, 2), (_long_plan, 1),
)


def _inside_any(rects: list[Rect], x: float, y: float) -> bool:
    return any(rect.inside(x, y) for rect in rects)


def _edges(rect: Rect) -> list[Wall]:
    """Counter-clockwise, so the room is on each edge's left."""
    corners = rect.polygon()
    return [Wall(corners[i], corners[(i + 1) % 4]) for i in range(4)]


def _split(edge: Wall, cuts: list[float]) -> list[Wall]:
    horizontal = edge.a[1] == edge.b[1]
    lo, hi = sorted((edge.a[0], edge.b[0]) if horizontal else (edge.a[1], edge.b[1]))
    points = sorted({lo, hi, *[c for c in cuts if lo < c < hi]})
    if (edge.b[0] if horizontal else edge.b[1]) < (edge.a[0] if horizontal else edge.a[1]):
        points.reverse()
    fixed = edge.a[1] if horizontal else edge.a[0]
    as_point = (lambda v: (v, fixed)) if horizontal else (lambda v: (fixed, v))
    return [Wall(as_point(p), as_point(q)) for p, q in zip(points, points[1:])]


def _merge(walls: list[Wall]) -> list[Wall]:
    merged: list[Wall] = []
    for wall in walls:
        last = merged[-1] if merged else None
        if last and last.b == wall.a and abs(last.heading - wall.heading) < 1e-6:
            merged[-1] = Wall(last.a, wall.b)
        else:
            merged.append(wall)
    return merged


def outline(rects: list[Rect]) -> list[Wall]:
    """The wall faces around a union of rectangles, dropping the seams between them."""
    xs = [v for r in rects for v in (r.x0, r.x1)]
    ys = [v for r in rects for v in (r.y0, r.y1)]
    walls = []
    for rect in rects:
        for edge in _edges(rect):
            for piece in _split(edge, xs if edge.a[1] == edge.b[1] else ys):
                mx, my = piece.point(piece.length / 2, -0.01)
                if not _inside_any(rects, mx, my):
                    walls.append(piece)
    return _merge(walls)


# Furniture groups ----------------------------------------------------------


def _row(kind: str, low: int, high: int, gap: float = 0.02) -> Callable[[random.Random], list[Placed]]:
    def build(rng: random.Random) -> list[Placed]:
        count, width, depth = rng.randint(low, high), PIECES[kind].size[0], PIECES[kind].size[1]
        pitch = width + gap
        return [Placed(kind, (i - (count - 1) / 2) * pitch, depth / 2, unit=i + 1) for i in range(count)]
    return build


def banquette(rng: random.Random) -> list[Placed]:
    count, pitch = rng.randint(2, 5), rng.choice((1.1, 1.2, 1.3))
    group = [Placed("banquette", 0.0, 0.3, width=count * pitch)]
    for i in range(count):
        x = (i - (count - 1) / 2) * pitch
        group += [Placed("cafe_table", x, 0.95), Placed("chair", x, 1.55, 180.0)]
    return group


def bar_rail(rng: random.Random) -> list[Placed]:
    count = rng.randint(3, 8)
    group = [Placed("window_ledge", 0.0, 0.2, width=count * 0.6)]
    return group + [Placed("stool", (i - (count - 1) / 2) * 0.6, 0.65) for i in range(count)]


def styling_stations(rng: random.Random) -> list[Placed]:
    count = rng.randint(2, 4)
    group = []
    for i in range(count):
        x = (i - (count - 1) / 2) * 1.5
        group += [Placed("styling_station", x, 0.225, unit=i + 1), Placed("salon_chair", x, 1.1, 180.0, unit=i + 1)]
    return group


def waiting_row(rng: random.Random) -> list[Placed]:
    count = rng.randint(3, 6)
    group = [Placed("chair", (i - (count - 1) / 2) * 0.55, 0.3) for i in range(count)]
    return group + [Placed("planter", (count + 1) / 2 * 0.55 + 0.1, 0.3)]


def two_top(rng: random.Random) -> list[Placed]:
    return [Placed("cafe_table", 0, 0), Placed("chair", 0, -0.6), Placed("chair", 0, 0.6, 180.0)]


def four_top(rng: random.Random) -> list[Placed]:
    seats = [(0, -0.7, 0.0), (0, 0.7, 180.0), (-0.7, 0, 90.0), (0.7, 0, -90.0)]
    return [Placed("four_top", 0, 0)] + [Placed("chair", x, y, h) for x, y, h in seats]


def communal(rng: random.Random) -> list[Placed]:
    per_side, kind = rng.randint(3, 6), rng.choice(("chair", "stool"))
    group = [Placed("communal_table", 0, 0, width=per_side * 0.6 + 0.3)]
    for i in range(per_side):
        x = (i - (per_side - 1) / 2) * 0.6
        group += [Placed(kind, x, -0.75), Placed(kind, x, 0.75, 180.0)]
    return group


def high_top(rng: random.Random) -> list[Placed]:
    return [Placed("bar_table", 0, 0), Placed("stool", 0, -0.55), Placed("stool", 0, 0.55)]


def lounge(rng: random.Random) -> list[Placed]:
    if rng.random() < 0.5:
        return [Placed("coffee_table", 0, 0), Placed("armchair", -1.05, 0, 90.0), Placed("armchair", 1.05, 0, -90.0)]
    return [Placed("coffee_table", 0, 0), Placed("sofa", 0, -0.85), Placed("armchair", 0, 0.95, 180.0)]


def merch_island(rng: random.Random) -> list[Placed]:
    return [Placed("display_table", 0, 0), Placed("mannequin", 1.0, 0, rng.choice((0.0, 90.0)))]


def desk_pod(rng: random.Random) -> list[Placed]:
    pairs = rng.randint(1, 2)
    group = []
    for i in range(pairs):
        x = (i - (pairs - 1) / 2) * 1.45
        group += [Placed("desk", x, -0.35), Placed("desk", x, 0.35),
                  Placed("task_chair", x, -1.05), Placed("task_chair", x, 1.05, 180.0)]
    return group


def aisle(kind: str, low: int, high: int) -> Callable[[random.Random], list[Placed]]:
    def build(rng: random.Random) -> list[Placed]:
        count = rng.randint(low, high)
        return [Placed(kind, (i - (count - 1) / 2) * PIECES[kind].size[0], 0, unit=i + 1) for i in range(count)]
    return build


def aisle_block(kind: str, most_runs: int) -> Callable[[random.Random], list[Placed]]:
    """Parallel double-sided runs with a walking aisle between each pair."""
    def build(rng: random.Random) -> list[Placed]:
        runs, units = rng.randint(2, most_runs), rng.randint(2, 5)
        width, depth = PIECES[kind].size[:2]
        pitch = depth + rng.uniform(1.0, 1.35)
        return [Placed(kind, (i - (units - 1) / 2) * width, (r - (runs - 1) / 2) * pitch, unit=r * units + i + 1)
                for r in range(runs) for i in range(units)]
    return build


def single(kind: str) -> Callable[[random.Random], list[Placed]]:
    return lambda rng: [Placed(kind, 0, 0)]


def against_wall(kind: str) -> Callable[[random.Random], list[Placed]]:
    return lambda rng: [Placed(kind, 0, PIECES[kind].size[1] / 2)]


@dataclass(frozen=True)
class Pattern:
    build: Callable[[random.Random], list[Placed]]
    on_wall: bool
    weight: int = 1
    most: int = 99
    squared: bool = False
    """Kept square to the building, as shelving aisles are, whatever the room's style."""


WALL, FLOOR = True, False


@dataclass(frozen=True)
class ShopType:
    name: str
    errand: str
    counter: str
    size: tuple[float, float, float, float]
    """Width range then depth range, in metres, for the main rectangle."""
    patterns: tuple[Pattern, ...]
    visit: tuple[frozenset[str], str]
    """The labels a customer's destination can carry, and what to call that stop."""
    density: tuple[float, float] = (0.16, 0.3)


SEAT_VISIT = (frozenset({"Cafe table", "Table", "Bar table"}), "Seat")
WAIT_VISIT = (frozenset({"Chair", "Bench"}), "Wait")
SHOP_TYPES = (
    ShopType("cafe", "Order a coffee", "Ordering counter", (5, 11, 6, 13), (
        Pattern(banquette, WALL, 3, 2), Pattern(bar_rail, WALL, 2, 1), Pattern(two_top, FLOOR, 4),
        Pattern(four_top, FLOOR, 2), Pattern(communal, FLOOR, 1, 1), Pattern(high_top, FLOOR, 1),
        Pattern(lounge, FLOOR, 1, 1), Pattern(against_wall("condiments"), WALL, 1, 1),
        Pattern(against_wall("trash"), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 3),
        Pattern(single("menu_stand"), FLOOR, 1, 1)), SEAT_VISIT),
    ShopType("boba tea shop", "Order a drink", "Ordering counter", (4, 8, 5, 10), (
        Pattern(bar_rail, WALL, 3, 2), Pattern(two_top, FLOOR, 3), Pattern(high_top, FLOOR, 2),
        Pattern(banquette, WALL, 1, 1), Pattern(against_wall("trash"), WALL, 1, 1),
        Pattern(_row("cooler", 1, 2), WALL, 1, 1), Pattern(single("menu_stand"), FLOOR, 1, 1)), SEAT_VISIT),
    ShopType("bakery", "Buy a loaf", "Sales counter", (5, 10, 5, 11), (
        Pattern(_row("bread_rack", 1, 3), WALL, 3, 2), Pattern(single("pastry_case"), FLOOR, 2, 2),
        Pattern(two_top, FLOOR, 3), Pattern(bar_rail, WALL, 1, 1), Pattern(_row("cooler", 1, 3), WALL, 1, 1),
        Pattern(single("display_table"), FLOOR, 1, 2)), SEAT_VISIT),
    ShopType("restaurant", "Eat dinner", "Service counter", (7, 13, 8, 15), (
        Pattern(four_top, FLOOR, 4), Pattern(two_top, FLOOR, 3), Pattern(banquette, WALL, 3, 3),
        Pattern(communal, FLOOR, 1, 1), Pattern(single("host_stand"), FLOOR, 1, 1),
        Pattern(single("planter"), FLOOR, 1, 4)), SEAT_VISIT, (0.2, 0.34)),
    ShopType("ice cream parlor", "Get a scoop", "Ordering counter", (4, 9, 5, 10), (
        Pattern(single("freezer_case"), FLOOR, 2, 1), Pattern(two_top, FLOOR, 3), Pattern(high_top, FLOOR, 2),
        Pattern(_row("waiting_bench", 1, 2), WALL, 1, 1), Pattern(bar_rail, WALL, 1, 1)), SEAT_VISIT),
    ShopType("boutique", "Try on a jacket", "Cash wrap", (5, 11, 6, 13), (
        Pattern(single("clothing_rack"), FLOOR, 4, 8), Pattern(merch_island, FLOOR, 2, 3),
        Pattern(_row("shelf", 2, 5), WALL, 2, 3), Pattern(_row("fitting_room", 1, 3, 0.0), WALL, 1, 1),
        Pattern(_row("waiting_bench", 1, 1), WALL, 1, 1), Pattern(single("mannequin"), FLOOR, 1, 3)),
        (frozenset({"Clothing rack"}), "Browse")),
    ShopType("bookstore", "Find a book", "Checkout counter", (6, 12, 7, 14), (
        Pattern(_row("bookcase", 2, 6), WALL, 4, 4), Pattern(aisle_block("book_shelf", 3), FLOOR, 3, 2, True),
        Pattern(single("display_table"), FLOOR, 2, 3), Pattern(lounge, FLOOR, 1, 1),
        Pattern(_row("waiting_bench", 1, 1), WALL, 1, 1)), (frozenset({"Double-sided bookshelf", "Bookcase"}), "Browse")),
    ShopType("convenience store", "Buy snacks", "Checkout counter", (6, 12, 7, 13), (
        Pattern(aisle_block("gondola", 4), FLOOR, 5, 2, True),
        Pattern(aisle("gondola", 2, 4), FLOOR, 2, 3, True), Pattern(_row("cooler", 2, 6), WALL, 3, 2),
        Pattern(_row("shelf", 2, 5), WALL, 2, 3), Pattern(against_wall("atm"), WALL, 1, 1),
        Pattern(single("display_table"), FLOOR, 1, 1)), (frozenset({"Gondola shelf", "Shelving unit"}), "Browse"), (0.22, 0.36)),
    ShopType("pharmacy", "Pick up a prescription", "Service counter", (6, 12, 7, 13), (
        Pattern(aisle_block("gondola", 3), FLOOR, 4, 2, True), Pattern(_row("shelf", 2, 5), WALL, 3, 3),
        Pattern(waiting_row, WALL, 2, 1), Pattern(_row("cooler", 1, 3), WALL, 1, 1)), WAIT_VISIT),
    ShopType("salon", "Get a haircut", "Service counter", (5, 10, 6, 12), (
        Pattern(styling_stations, WALL, 4, 3), Pattern(_row("shampoo_sink", 2, 3, 0.1), WALL, 2, 1),
        Pattern(waiting_row, WALL, 2, 1), Pattern(_row("shelf", 1, 3), WALL, 1, 1),
        Pattern(single("planter"), FLOOR, 1, 2)), (frozenset({"Chair"}), "Seat")),
    ShopType("small office", "Visit the front desk", "Service counter", (5, 11, 6, 12), (
        Pattern(desk_pod, FLOOR, 3, 3), Pattern(waiting_row, WALL, 2, 1), Pattern(lounge, FLOOR, 1, 1),
        Pattern(_row("file_cabinet", 2, 4), WALL, 1, 2), Pattern(against_wall("printer"), WALL, 1, 1),
        Pattern(_row("bookcase", 1, 3), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 2)), WAIT_VISIT),
    ShopType("clinic waiting room", "Check in for an appointment", "Service counter", (5, 10, 5, 10), (
        Pattern(waiting_row, WALL, 4, 4), Pattern(lounge, FLOOR, 1, 1), Pattern(four_top, FLOOR, 1, 1),
        Pattern(_row("bookcase", 1, 2), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 3)), WAIT_VISIT),
)


# Room assembly -------------------------------------------------------------


@dataclass
class Room:
    name: str
    shop: ShopType
    rng: random.Random
    rects: list[Rect]
    walls: list[Wall]
    nodes: list[SceneNode] = field(default_factory=list)
    boxes: list[tuple[Box, int]] = field(default_factory=list)
    """Placed footprints with the group each belongs to; -1 for fixed architecture."""
    reserved: list[Box] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)
    groups: int = 0

    def node(self, piece: Piece, box: Box, z: float | None = None, kind: str = "object") -> SceneNode:
        tall = piece.size[2]
        made = SceneNode(
            id=uuid.uuid5(NAMESPACE, f"{self.name}:{len(self.nodes)}"), kind=kind, label=piece.label,
            raw_category=piece.category, dimensions=Vec3(x=box.w, y=box.d, z=tall),
            transform=_transform(box.cx, box.cy, tall / 2 if z is None else z, box.heading),
            movable=piece.movable,
        )
        self.nodes.append(made)
        return made

    def graph(self, nodes: list[SceneNode] | None = None) -> SceneGraph:
        return SceneGraph(scan_id=uuid.uuid5(NAMESPACE, self.name), nodes=nodes or self.nodes)


def _transform(x: float, y: float, z: float, heading: float) -> Mat4:
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    return Mat4(m=[c, -s, 0, x, s, c, 0, y, 0, 0, 1, z, 0, 0, 0, 1])


def _shell(room: Room) -> None:
    x0, y0 = min(r.x0 for r in room.rects), min(r.y0 for r in room.rects)
    x1, y1 = max(r.x1 for r in room.rects), max(r.y1 for r in room.rects)
    box = Box((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)
    room.node(Piece("Floor", "floor", (box.w, box.d, 0.01), False), box, z=0.0, kind="floor")
    for wall in room.walls:
        cx, cy = wall.point(wall.length / 2, -WALL_THICKNESS / 2)
        box = Box(cx, cy, wall.length + WALL_THICKNESS, WALL_THICKNESS, wall.heading)
        room.node(Piece("Wall", "wall", (0, 0, WALL_HEIGHT), False), box, kind="wall")


def _door(room: Room, wall: Wall, label: str) -> tuple[SceneNode, tuple[float, float]]:
    width = room.rng.uniform(0.9, 1.07)
    t = room.rng.uniform(0.7 + width / 2, wall.length - 0.7 - width / 2)
    cx, cy = wall.point(t, -WALL_THICKNESS / 2)
    door = room.node(Piece(label, "door", (width, WALL_THICKNESS, 2.1), False),
                     Box(cx, cy, width, WALL_THICKNESS, wall.heading), kind="door")
    ex, ey = wall.point(t, 0.9)
    room.reserved.append(Box(*wall.point(t, 0.9), width + 0.8, 1.8, wall.heading))
    return door, (ex, ey)


def _long_walls(room: Room, least: float) -> list[Wall]:
    return [wall for wall in room.walls if wall.length >= least]


def _front_wall(room: Room) -> Wall:
    main = room.rects[0]
    fronts = [w for w in room.walls if w.a[1] == main.y0 and w.b[1] == main.y0 and w.length > 2.2]
    if not fronts:
        raise Unbuildable("the front wall is too short for a door")
    return fronts[0]


def _counter_wall(room: Room, front: Wall, length: float) -> Wall:
    choices = [w for w in _long_walls(room, length + 1.6) if w != front and _faces_main(room, w)]
    if not choices:
        raise Unbuildable("no wall is long enough for the counter")
    return room.rng.choice(choices)


def _faces_main(room: Room, wall: Wall) -> bool:
    main = room.rects[0]
    return main.inside(*wall.point(wall.length / 2, 0.5))


def _counter_height(room: Room) -> float:
    return to_meters(room.rng.choice((34.0, 36.0, 36.0, 38.0, 42.0, 47.0)))


def _counter(room: Room, front: Wall) -> tuple[SceneNode, Wall, float, float]:
    length, depth = room.rng.uniform(2.0, 4.5), room.rng.uniform(0.6, 0.75)
    wall = _counter_wall(room, front, length)
    staff = room.rng.uniform(1.0, 1.3)
    back_bar = room.rng.random() < 0.5
    staff += 0.6 if back_bar else 0.0
    t = room.rng.uniform(0.8 + length / 2, wall.length - 0.8 - length / 2)
    height = _counter_height(room)
    counter_box = Box(*wall.point(t, staff + depth / 2), length, depth, wall.heading)
    counter = room.node(Piece(room.shop.counter, "counter", (length, depth, height), False), counter_box)
    room.boxes.append((counter_box, -1))
    room.reserved.append(Box(*wall.point(t, staff / 2), length + 0.6, staff, wall.heading))
    room.reserved.append(Box(*wall.point(t, staff + depth + 0.75), length + 0.6, 1.5, wall.heading))
    if back_bar:
        room.node(PIECES["back_bar"], Box(*wall.point(t, 0.3), length, 0.6, wall.heading))
    _counter_extras(room, wall, t, staff, depth, length, height)
    return counter, wall, t, staff + depth


def _counter_extras(room: Room, wall: Wall, t: float, staff: float, depth: float, length: float,
                    height: float) -> None:
    pos = PIECES["point_of_sale"]
    along = t + room.rng.uniform(-length / 2 + 0.2, length / 2 - 0.2)
    reader = Box(*wall.point(along, staff + depth - 0.15), pos.size[0], pos.size[1], wall.heading)
    room.node(pos, reader, z=height + pos.size[2] / 2)
    if room.rng.random() < 0.4:
        lowered = PIECES["lowered"]
        side = room.rng.choice((-1, 1))
        box = Box(*wall.point(t + side * (length / 2 + lowered.size[0] / 2), staff + depth / 2),
                  lowered.size[0], depth, wall.heading)
        if all(_inside_floor(room, point) for point in box.corners()):
            room.node(lowered, box)
            room.boxes.append((box, -1))


def _service_stops(room: Room, counter: SceneNode, wall: Wall, t: float, front_edge: float) -> list[Stop]:
    length = counter.dimensions.x
    order_at = wall.point(t - length * 0.25, front_edge + 0.6)
    pickup_at = wall.point(t + length * 0.25, front_edge + 0.6)
    return [Stop(name="Counter", position=Vec3(x=order_at[0], y=order_at[1], z=0.0), anchor_node_id=counter.id),
            Stop(name="Pickup", position=Vec3(x=pickup_at[0], y=pickup_at[1], z=0.0), anchor_node_id=counter.id)]


def _corridor(room: Room, start: tuple[float, float], end: tuple[float, float], width: float) -> None:
    length = math.dist(start, end)
    if length < 0.1:
        return
    heading = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    room.reserved.append(Box((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, length + width, width, heading))


def _columns(room: Room) -> None:
    main = room.rects[0]
    if (main.x1 - main.x0) * (main.y1 - main.y0) < 55 or room.rng.random() < 0.5:
        return
    spacing = room.rng.uniform(3.2, 4.5)
    x = main.x0 + spacing
    while x < main.x1 - 1.5:
        y = main.y0 + spacing
        while y < main.y1 - 1.5:
            box = Box(x, y, 0.4, 0.4)
            if _clear(room, box, 0.05, -2):
                room.node(PIECES["column"], box)
                room.boxes.append((box, -1))
            y += spacing
        x += spacing


def _inside_floor(room: Room, point: tuple[float, float]) -> bool:
    return any(contains_point(rect.polygon(), point, 0.005) for rect in room.rects)


def _clear(room: Room, box: Box, aisle: float, group: int) -> bool:
    if not all(_inside_floor(room, corner) for corner in box.corners(WALL_GAP)):
        return False
    if any(gap_between(box.corners(), zone.corners()) == 0.0 for zone in room.reserved):
        return False
    grown = box.corners(aisle)
    return not any(owner != group and gap_between(grown, other.corners()) == 0.0 for other, owner in room.boxes)


def _world(placed: Placed, origin: tuple[float, float], heading: float) -> Box:
    piece = PIECES[placed.kind]
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    x = origin[0] + placed.x * c - placed.y * s
    y = origin[1] + placed.x * s + placed.y * c
    return Box(x, y, placed.width or piece.size[0], piece.size[1], heading + placed.heading)


def _half_span(placed: list[Placed]) -> float:
    return max(abs(item.x) + (item.width or PIECES[item.kind].size[0]) / 2 for item in placed)


def _wall_anchor(room: Room, placed: list[Placed]) -> tuple[tuple[float, float], float] | None:
    walls = _long_walls(room, 1.4)
    if not walls:
        return None
    wall = room.rng.choice(walls)
    half = min(_half_span(placed), wall.length / 2 - 0.05)
    return wall.point(room.rng.uniform(half + 0.05, wall.length - half - 0.05), WALL_GAP), wall.heading


def _floor_anchor(room: Room, style: float) -> tuple[tuple[float, float], float]:
    rect = room.rng.choices(room.rects, weights=[(r.x1 - r.x0) * (r.y1 - r.y0) for r in room.rects])[0]
    point = (room.rng.uniform(rect.x0 + 0.6, rect.x1 - 0.6), room.rng.uniform(rect.y0 + 0.6, rect.y1 - 0.6))
    if style < 0:
        return point, room.rng.uniform(0, 180)
    return point, style + room.rng.choice((0.0, 90.0))


def _surviving_units(room: Room, placed: list[Placed], boxes: list[Box], aisle: float) -> list[int]:
    """Indices of the pieces kept: whole units that fit, if at least half the units do."""
    fits = [_clear(room, box, aisle, room.groups) for box in boxes]
    units = {item.unit for item in placed}
    kept = {u for u in units if all(ok for item, ok in zip(placed, fits) if item.unit == u)}
    if 0 in units and 0 not in kept:
        return []
    if len(kept) * 2 < len(units):
        return []
    return [i for i, item in enumerate(placed) if item.unit in kept]


def _try_group(room: Room, pattern: Pattern, style: float, aisle: float) -> bool:
    placed = pattern.build(room.rng)
    anchor = _wall_anchor(room, placed) if pattern.on_wall else _floor_anchor(room, 0.0 if pattern.squared else style)
    if anchor is None:
        return False
    boxes = [_world(item, *anchor) for item in placed]
    keep = _surviving_units(room, placed, boxes, aisle)
    if not keep:
        return False
    placed, boxes = [placed[i] for i in keep], [boxes[i] for i in keep]
    group = room.groups
    before = len(room.nodes)
    added = [room.node(PIECES[item.kind], box) for item, box in zip(placed, boxes)]
    base = room.graph(room.nodes[:before])
    if violations(base, room.graph(), added=frozenset(node.id for node in added)):
        del room.nodes[before:]
        return False
    room.boxes += [(box, group) for box in boxes]
    room.groups += 1
    return True


def _pick(room: Room, counts: dict[int, int]) -> int | None:
    open_ = [i for i, p in enumerate(room.shop.patterns) if counts.get(i, 0) < p.most]
    if not open_:
        return None
    return room.rng.choices(open_, weights=[room.shop.patterns[i].weight for i in open_])[0]


def _furnish(room: Room) -> None:
    area = sum((r.x1 - r.x0) * (r.y1 - r.y0) for r in room.rects)
    target = area * room.rng.uniform(*room.shop.density)
    style = room.rng.choice((0.0, 0.0, 45.0, -1.0))
    aisle = room.rng.uniform(0.42, 0.62)
    counts: dict[int, int] = {}
    for _ in range(1500):
        if sum(b.w * b.d for b, owner in room.boxes if owner >= 0) >= target:
            return
        index = _pick(room, counts)
        if index is None:
            return
        if _try_group(room, room.shop.patterns[index], style, aisle):
            counts[index] = counts.get(index, 0) + 1


def _destination(room: Room) -> Stop | None:
    labels, name = room.shop.visit
    targets = [n for n in room.nodes if n.label in labels]
    room.rng.shuffle(targets)
    for node in targets:
        spot = _open_spot(room, node)
        if spot:
            return Stop(name=name, position=Vec3(x=spot[0], y=spot[1], z=0.0), anchor_node_id=node.id)
    return None


def _open_spot(room: Room, node: SceneNode) -> tuple[float, float] | None:
    centre, heading = node.transform.position, math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))
    for side in (90.0, -90.0, 0.0, 180.0):
        reach = (node.dimensions.y if side in (90.0, -90.0) else node.dimensions.x) / 2 + 0.55
        angle = math.radians(heading + side)
        spot = (centre.x + reach * math.cos(angle), centre.y + reach * math.sin(angle))
        probe = Box(*spot, 0.5, 0.5)
        if _inside_floor(room, spot) and not any(gap_between(probe.corners(), b.corners()) == 0.0
                                                 for b, _ in room.boxes):
            return spot
    return None


def _rotate_everything(room: Room) -> None:
    turn = room.rng.choice((0.0, 90.0, 180.0, 270.0)) if room.rng.random() < 0.4 else room.rng.uniform(0, 360)
    shift = (room.rng.uniform(-4, 4), room.rng.uniform(-4, 4))
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))

    def move(x: float, y: float) -> tuple[float, float]:
        return x * c - y * s + shift[0], x * s + y * c + shift[1]

    def moved(stop: Stop) -> Stop:
        x, y = move(stop.position.x, stop.position.y)
        return stop.model_copy(update={"position": Vec3(x=x, y=y, z=0.0)})

    turned = _transform(0, 0, 0, turn).m
    for index, node in enumerate(room.nodes):
        m = node.transform.m
        x, y = move(m[3], m[7])
        rot = [turned[0] * m[0] + turned[1] * m[4], turned[0] * m[1] + turned[1] * m[5],
               turned[4] * m[0] + turned[5] * m[4], turned[4] * m[1] + turned[5] * m[5]]
        room.nodes[index] = node.model_copy(update={"transform": Mat4(
            m=[rot[0], rot[1], 0, x, rot[2], rot[3], 0, y, 0, 0, 1, m[11], 0, 0, 0, 1])})
    room.stops = [moved(stop) for stop in room.stops]


def _base_room(name: str, shop: ShopType, rng: random.Random) -> Room:
    plan = rng.choices([p for p, _ in PLANS], weights=[w for _, w in PLANS])[0]
    width, depth = rng.uniform(*shop.size[:2]), rng.uniform(*shop.size[2:])
    rects = plan(rng, width, depth)
    return Room(name, shop, rng, rects, outline(rects))


def _entry_stops(door: SceneNode, inside: tuple[float, float]) -> tuple[Stop, Stop]:
    at = Vec3(x=inside[0], y=inside[1], z=0.0)
    return Stop(name="Entrance", position=at, anchor_node_id=door.id), Stop(name="Exit", position=at,
                                                                            anchor_node_id=door.id)


def _connect_wings(room: Room, hub: tuple[float, float], width: float) -> None:
    for rect in room.rects[1:]:
        _corridor(room, hub, ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2), width)


def _second_door(room: Room, front: Wall, hub: tuple[float, float], width: float) -> None:
    others = [w for w in _long_walls(room, 2.6) if w != front]
    if not others or room.rng.random() > 0.35:
        return
    _, inside = _door(room, room.rng.choice(others), "Back door")
    _corridor(room, hub, inside, width)


def build_room(name: str, shop: ShopType, rng: random.Random) -> tuple[SceneGraph, Scenario] | None:
    room = _base_room(name, shop, rng)
    _shell(room)
    front = _front_wall(room)
    door, inside = _door(room, front, "Front door")
    counter, wall, t, front_edge = _counter(room, front)
    service = _service_stops(room, counter, wall, t, front_edge)
    route_width = rng.uniform(1.0, 1.5)
    hub = (service[0].position.x, service[0].position.y)
    _corridor(room, inside, hub, route_width)
    _connect_wings(room, hub, route_width)
    _second_door(room, front, hub, route_width)
    _columns(room)
    _furnish(room)
    visit = _destination(room)
    if visit is None:
        return None
    entrance, leave = _entry_stops(door, inside)
    room.stops = [entrance, *service, visit, leave]
    _rotate_everything(room)
    return room.graph(), Scenario(name=shop.errand, stops=room.stops)


def generate(index: int, attempts: int = 30) -> tuple[SceneGraph, Scenario, ShopType]:
    shop = SHOP_TYPES[index % len(SHOP_TYPES)]
    for attempt in range(attempts):
        try:
            made = build_room(f"generated-{index:05d}", shop, random.Random(index * 7919 + attempt))
        except Unbuildable:
            continue
        if made is not None:
            return (*made, shop)
    raise RuntimeError(f"could not generate a room for index {index}")
