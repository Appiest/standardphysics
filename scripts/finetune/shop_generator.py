"""Procedural shop rooms for training.

A room starts from a shell: either generated rectangles (a plain, L, T or long
narrow plan) or a real scan emptied of its furniture. Then come the fixed
things a shop is built around: a service counter with its registers, cash
drawer, card reader, menu boards and sometimes a lowered section; a
single-user restroom; kiosks and a drink station in fast-food rooms; and
reserved walking corridors between the doors, the counter and the restroom.
Last, furniture groups are drawn from the shop type's recipe until the room
reaches its target density. Every piece is checked against the hard
constraints the fix loop uses, so a generated room is one somebody could
really have.

A piece's front is its local -Y side (`training.quality.front_heading_degrees`
is yaw - 90), so a piece meant to face direction F gets yaw F + 90. Seats face
the surface they serve and wall-backed pieces face into the room.

Labels are taken from the exact names in `standardphysics_agents.checks.roles`,
because the checker recognises counters, tables and seats by name and nothing
else. Sizes are ordinary trade sizes for each piece.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from shop_geometry import Box, Point, turned
from shop_restroom import add_restroom
from shop_room import (
    PIECES,
    WALL_GAP,
    Entrance,
    Item,
    Placed,
    Room,
    Unbuildable,
    add_door,
    clear,
    commit,
    corridor,
    inside_floor,
    long_walls,
    place_group,
)
from shop_service import build_counter, cashier, menu_board_on_the_route, menu_boards_behind, service_stops
from shop_shells import RectShell, ScanShell
from standardphysics_agents.fix.moves import RESTING_GAP
from standardphysics_agents.training.checker import TrainingChecker
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_pipeline import gap_between

SCAN_SHARE = 0.4
"""The share of rooms built inside a real scan rather than generated rectangles."""
LEAST_MOVABLE_PIECES = 3
"""A room with fewer movable pieces on the floor gives a rearrangement nothing to do."""
DINING_TABLES = frozenset({"Cafe table", "Table", "Bar table", "Accessible table"})
SEATED_TABLE_TOP = 0.765
"""The highest top, in metres, that still counts as a seated-height table (the 0.74 to 0.76 m tables)."""
HIGH_TABLE_TOP = 1.0
"""The lowest top, in metres, that counts as a high table (the 1.05 m bar tables and ledges)."""
LEAST_TABLES_FOR_A_HIGH_ONE = 3
"""A dining room with this many tables or more has room for a high one as well as seated ones."""
ACCESSIBLE_TABLE_TRY = 0.75
"""How often a dining room tries for an accessible table; about one try in five finds no site, leaving ~60%."""

# Furniture groups ----------------------------------------------------------


def _row(kind: str, low: int, high: int, gap: float = 0.02) -> Callable[[random.Random], list[Placed]]:
    """Wall-backed pieces side by side, each facing into the room."""
    def build(rng: random.Random) -> list[Placed]:
        count, width, depth = rng.randint(low, high), PIECES[kind].size[0], PIECES[kind].size[1]
        pitch = width + gap
        return [Placed(kind, (i - (count - 1) / 2) * pitch, depth / 2, unit=i + 1, faces=90.0) for i in range(count)]
    return build


def _low_height(rng: random.Random) -> float:
    return rng.uniform(0.74, 0.76)


def banquette(rng: random.Random) -> list[Placed]:
    count, pitch = rng.randint(2, 5), rng.choice((1.1, 1.2, 1.3))
    group = [Placed("banquette", 0.0, 0.3, width=count * pitch, faces=90.0)]
    for i in range(count):
        x = (i - (count - 1) / 2) * pitch
        group += [Placed("cafe_table", x, 0.95, height=_low_height(rng)), Placed("chair", x, 1.55, faces=-90.0)]
    return group


def booths(rng: random.Random) -> list[Placed]:
    """Fixed booths running out from the wall: a bench, a table, a bench, repeated."""
    count, pitch, group = rng.randint(1, 4), 1.97, []
    for i in range(count):
        x = (i - (count - 1) / 2) * pitch
        group += [Placed("booth_bench", x - 0.725, 0.62, unit=i + 1, faces=0.0),
                  Placed("booth_table", x, 0.62, unit=i + 1, height=_low_height(rng)),
                  Placed("booth_bench", x + 0.725, 0.62, unit=i + 1, faces=180.0)]
    return group


def bar_rail(rng: random.Random) -> list[Placed]:
    count = rng.randint(3, 8)
    group = [Placed("window_ledge", 0.0, 0.2, width=count * 0.6)]
    return group + [Placed("stool", (i - (count - 1) / 2) * 0.6, 0.65, faces=-90.0) for i in range(count)]


def styling_stations(rng: random.Random) -> list[Placed]:
    count = rng.randint(2, 4)
    group = []
    for i in range(count):
        x = (i - (count - 1) / 2) * 1.5
        group += [Placed("styling_station", x, 0.225, unit=i + 1, faces=90.0),
                  Placed("salon_chair", x, 1.1, unit=i + 1, faces=-90.0)]
    return group


def waiting_row(rng: random.Random) -> list[Placed]:
    count = rng.randint(3, 6)
    group = [Placed("chair", (i - (count - 1) / 2) * 0.55, 0.3, faces=90.0) for i in range(count)]
    return group + [Placed("planter", (count + 1) / 2 * 0.55 + 0.1, 0.3)]


def two_top(rng: random.Random) -> list[Placed]:
    return [Placed("cafe_table", 0, 0, height=_low_height(rng)), Placed("chair", 0, -0.6, faces=90.0),
            Placed("chair", 0, 0.6, faces=-90.0)]


FOUR_SEATS = ((0.0, -0.7, 90.0), (0.0, 0.7, -90.0), (-0.7, 0.0, 0.0), (0.7, 0.0, 180.0))


def four_top(rng: random.Random) -> list[Placed]:
    return [Placed("four_top", 0, 0, height=_low_height(rng))] + [Placed("chair", x, y, faces=f)
                                                                 for x, y, f in FOUR_SEATS]


def communal(rng: random.Random) -> list[Placed]:
    per_side, kind = rng.randint(3, 6), rng.choice(("chair", "stool"))
    group = [Placed("communal_table", 0, 0, width=per_side * 0.6 + 0.3, height=_low_height(rng))]
    for i in range(per_side):
        x = (i - (per_side - 1) / 2) * 0.6
        group += [Placed(kind, x, -0.75, faces=90.0), Placed(kind, x, 0.75, faces=-90.0)]
    return group


def high_top(rng: random.Random) -> list[Placed]:
    return [Placed("bar_table", 0, 0), Placed("stool", 0, -0.55, faces=90.0), Placed("stool", 0, 0.55, faces=-90.0)]


def lounge(rng: random.Random) -> list[Placed]:
    if rng.random() < 0.5:
        return [Placed("coffee_table", 0, 0), Placed("armchair", -1.05, 0, faces=0.0),
                Placed("armchair", 1.05, 0, faces=180.0)]
    return [Placed("coffee_table", 0, 0), Placed("sofa", 0, -0.85, faces=90.0),
            Placed("armchair", 0, 0.95, faces=-90.0)]


def merch_island(rng: random.Random) -> list[Placed]:
    return [Placed("display_table", 0, 0), Placed("mannequin", 1.0, 0, rng.choice((0.0, 90.0)))]


def desk_pod(rng: random.Random) -> list[Placed]:
    pairs = rng.randint(1, 2)
    group = []
    for i in range(pairs):
        x = (i - (pairs - 1) / 2) * 1.45
        group += [Placed("desk", x, -0.35), Placed("desk", x, 0.35),
                  Placed("task_chair", x, -1.05, faces=90.0), Placed("task_chair", x, 1.05, faces=-90.0)]
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
    return lambda rng: [Placed(kind, 0, PIECES[kind].size[1] / 2, faces=90.0)]


def accessible_table(rng: random.Random) -> list[Placed]:
    """A 30 inch high table with its -Y side left open for a wheelchair's 30 by 48 inch approach."""
    if rng.random() < 0.5:
        return [Placed("accessible_two", 0, 0), Placed("chair", 0, 0.66, faces=-90.0)]
    return [Placed("accessible_four", 0, 0)] + [Placed("chair", x * 8 / 7, y * 8 / 7, faces=f)
                                               for x, y, f in FOUR_SEATS[1:]]


@dataclass(frozen=True)
class Pattern:
    build: Callable[[random.Random], list[Placed]]
    on_wall: bool
    weight: int = 1
    most: int = 99
    squared: bool = False
    """Kept square to the building, as shelving aisles are, whatever the room's style."""
    early: str | None = None
    """Tried before the weighted draw, so a room of this type nearly always has one.

    Patterns sharing a key are alternatives: the first that fits settles the key.
    """


WALL, FLOOR = True, False


@dataclass(frozen=True)
class ShopType:
    name: str
    errand: str
    counters: tuple[str, ...]
    size: tuple[float, float, float, float]
    """Width range then depth range, in metres, for the main rectangle."""
    patterns: tuple[Pattern, ...]
    visit: tuple[frozenset[str], str]
    """The labels a customer's destination can carry, and what to call that stop."""
    density: tuple[float, float] = (0.16, 0.3)
    restroom: float = 0.7
    """The chance the room has a single-user restroom."""
    dining: bool = False
    kiosks: float = 0.0
    registers: tuple[int, int] = (1, 1)
    menus: bool = False
    counter_label: str = "Counter"
    """What a customer calls their one stop at the counter."""
    handoff_label: str | None = None
    """A second stop where the counter hands something over at a separate spot, for
    quick-service businesses with an order end and a pickup end. None everywhere else,
    because most counters (check-in desks, checkout counters, sales counters) are a
    single stop."""

    @property
    def browse_first(self) -> bool:
        """Retail: the customer browses the floor before paying, rather than the
        counter being the first thing they do."""
        return self.visit[1] == "Browse"


SEAT_VISIT = (DINING_TABLES, "Seat")
WAIT_VISIT = (frozenset({"Chair", "Bench"}), "Wait")
LOUNGE_VISIT = (frozenset({"Sofa", "Armchair", "Bench"}), "Wait")
SHOP_TYPES = (
    ShopType("cafe", "Order a coffee", ("Ordering counter",), (5, 11, 6, 13), (
        Pattern(two_top, FLOOR, 4, early="low"), Pattern(banquette, WALL, 3, 2),
        Pattern(bar_rail, WALL, 2, 1, early="high"), Pattern(four_top, FLOOR, 2), Pattern(communal, FLOOR, 1, 1),
        Pattern(high_top, FLOOR, 1, early="high"),
        Pattern(lounge, FLOOR, 1, 1), Pattern(against_wall("condiments"), WALL, 1, 1),
        Pattern(against_wall("drink_station"), WALL, 1, 1), Pattern(against_wall("trash"), WALL, 1, 1),
        Pattern(single("planter"), FLOOR, 1, 3)), SEAT_VISIT, restroom=1.0, dining=True, kiosks=0.2, menus=True,
        counter_label="Order", handoff_label="Pickup"),
    ShopType("boba tea shop", "Order a drink", ("Ordering counter",), (4, 8, 5, 10), (
        Pattern(two_top, FLOOR, 3, early="low"), Pattern(bar_rail, WALL, 3, 2, early="high"),
        Pattern(high_top, FLOOR, 2, early="high"),
        Pattern(banquette, WALL, 1, 1), Pattern(against_wall("trash"), WALL, 1, 1),
        Pattern(_row("cooler", 1, 2), WALL, 1, 1)), SEAT_VISIT, dining=True, kiosks=0.3, menus=True,
        counter_label="Order", handoff_label="Pickup"),
    ShopType("bakery", "Buy a loaf", ("Sales counter",), (5, 10, 5, 11), (
        Pattern(_row("bread_rack", 1, 3), WALL, 3, 2), Pattern(single("pastry_case"), FLOOR, 2, 2),
        Pattern(two_top, FLOOR, 3, early="low"), Pattern(bar_rail, WALL, 1, 1, early="high"),
        Pattern(high_top, FLOOR, 1, 1, early="high"),
        Pattern(_row("cooler", 1, 3), WALL, 1, 1), Pattern(single("display_table"), FLOOR, 1, 2)),
        SEAT_VISIT, dining=True, menus=True, counter_label="Counter"),
    ShopType("restaurant", "Eat dinner", ("Service counter", "Bar"), (7, 13, 8, 15), (
        Pattern(four_top, FLOOR, 4), Pattern(two_top, FLOOR, 3, early="low"), Pattern(banquette, WALL, 3, 3),
        Pattern(booths, WALL, 2, 2), Pattern(high_top, FLOOR, 1, 2, early="high"), Pattern(communal, FLOOR, 1, 1),
        Pattern(single("host_stand"), FLOOR, 1, 1), Pattern(single("planter"), FLOOR, 1, 4)),
        SEAT_VISIT, (0.2, 0.34), restroom=1.0, dining=True, kiosks=0.15, menus=True, counter_label="Check in"),
    ShopType("fast food restaurant", "Order a burger", ("Ordering counter",), (8, 15, 8, 15), (
        Pattern(booths, WALL, 4, 3), Pattern(banquette, WALL, 2, 2), Pattern(two_top, FLOOR, 4, early="low"),
        Pattern(high_top, FLOOR, 2, 3, early="high"), Pattern(four_top, FLOOR, 2),
        Pattern(against_wall("trash"), WALL, 2, 2, early="trash"), Pattern(against_wall("tray_return"), WALL, 1, 1),
        Pattern(against_wall("drink_station"), WALL, 2, 1, early="drinks"),
        Pattern(against_wall("condiments"), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 2)),
        SEAT_VISIT, (0.2, 0.34), restroom=1.0, dining=True, kiosks=0.75, registers=(2, 4), menus=True,
        counter_label="Order", handoff_label="Pickup"),
    ShopType("ice cream parlor", "Get a scoop", ("Ordering counter",), (4, 9, 5, 10), (
        Pattern(single("freezer_case"), FLOOR, 2, 1), Pattern(two_top, FLOOR, 3, early="low"),
        Pattern(high_top, FLOOR, 2, early="high"), Pattern(_row("waiting_bench", 1, 2), WALL, 1, 1),
        Pattern(bar_rail, WALL, 1, 1, early="high")),
        SEAT_VISIT, dining=True, menus=True, counter_label="Order"),
    ShopType("boutique", "Try on a jacket", ("Cash wrap",), (5, 11, 6, 13), (
        Pattern(single("clothing_rack"), FLOOR, 4, 8), Pattern(merch_island, FLOOR, 2, 3),
        Pattern(_row("shelf", 2, 5), WALL, 2, 3), Pattern(_row("fitting_room", 1, 3, 0.0), WALL, 1, 1),
        Pattern(_row("waiting_bench", 1, 1), WALL, 1, 1), Pattern(single("mannequin"), FLOOR, 1, 3)),
        (frozenset({"Clothing rack"}), "Browse"), counter_label="Checkout"),
    ShopType("bookstore", "Find a book", ("Checkout counter",), (6, 12, 7, 14), (
        Pattern(_row("bookcase", 2, 6), WALL, 4, 4), Pattern(aisle_block("book_shelf", 3), FLOOR, 3, 2, True),
        Pattern(single("display_table"), FLOOR, 2, 3), Pattern(lounge, FLOOR, 1, 1),
        Pattern(_row("waiting_bench", 1, 1), WALL, 1, 1)),
        (frozenset({"Double-sided bookshelf", "Bookcase"}), "Browse"), counter_label="Checkout"),
    ShopType("convenience store", "Buy snacks", ("Checkout counter", "Register counter"), (6, 12, 7, 13), (
        Pattern(aisle_block("gondola", 4), FLOOR, 5, 2, True),
        Pattern(aisle("gondola", 2, 4), FLOOR, 2, 3, True), Pattern(_row("cooler", 2, 6), WALL, 3, 2),
        Pattern(_row("shelf", 2, 5), WALL, 2, 3), Pattern(against_wall("atm"), WALL, 1, 1),
        Pattern(single("display_table"), FLOOR, 1, 1)), (frozenset({"Gondola shelf", "Shelving unit"}), "Browse"),
        (0.22, 0.36), counter_label="Checkout"),
    ShopType("pharmacy", "Pick up a prescription", ("Service counter",), (6, 12, 7, 13), (
        Pattern(aisle_block("gondola", 3), FLOOR, 4, 2, True), Pattern(_row("shelf", 2, 5), WALL, 3, 3),
        Pattern(waiting_row, WALL, 2, 1), Pattern(_row("cooler", 1, 3), WALL, 1, 1)), WAIT_VISIT,
        counter_label="Check in"),
    ShopType("salon", "Get a haircut", ("Service counter",), (5, 10, 6, 12), (
        Pattern(styling_stations, WALL, 4, 3), Pattern(_row("shampoo_sink", 2, 3, 0.1), WALL, 2, 1),
        Pattern(waiting_row, WALL, 2, 1), Pattern(_row("shelf", 1, 3), WALL, 1, 1),
        Pattern(single("planter"), FLOOR, 1, 2)), (frozenset({"Chair"}), "Seat"), counter_label="Check in"),
    ShopType("small office", "Visit the front desk", ("Front desk", "Reception desk"), (5, 11, 6, 12), (
        Pattern(desk_pod, FLOOR, 3, 3), Pattern(waiting_row, WALL, 2, 1), Pattern(lounge, FLOOR, 1, 1),
        Pattern(_row("file_cabinet", 2, 4), WALL, 1, 2), Pattern(against_wall("printer"), WALL, 1, 1),
        Pattern(_row("bookcase", 1, 3), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 2)),
        WAIT_VISIT, restroom=1.0, counter_label="Check in"),
    ShopType("clinic waiting room", "Check in for an appointment", ("Reception desk", "Front desk"),
             (5, 10, 5, 10), (
        Pattern(waiting_row, WALL, 4, 4), Pattern(lounge, FLOOR, 1, 1), Pattern(four_top, FLOOR, 1, 1),
        Pattern(_row("bookcase", 1, 2), WALL, 1, 1), Pattern(single("planter"), FLOOR, 1, 3)),
        WAIT_VISIT, restroom=1.0, counter_label="Check in"),
    ShopType("hotel lobby", "Check in at the front desk", ("Front desk",), (7, 14, 7, 14), (
        Pattern(lounge, FLOOR, 3, 3, early="lounge"), Pattern(_row("waiting_bench", 1, 2), WALL, 2, 2),
        Pattern(single("luggage_cart"), FLOOR, 1, 2), Pattern(single("planter"), FLOOR, 2, 4),
        Pattern(against_wall("trash"), WALL, 1, 1), Pattern(single("display_table"), FLOOR, 1, 1)),
        LOUNGE_VISIT, (0.12, 0.24), restroom=1.0, counter_label="Check in"),
)
FOOD_KINDS = frozenset(shop.name for shop in SHOP_TYPES if shop.menus)


# Room assembly -------------------------------------------------------------


def _columns(room: Room) -> None:
    bay = room.shell.column_bay()
    if bay is None or room.rng.random() < 0.5:
        return
    spacing = room.rng.uniform(3.2, 4.5)
    x = bay.x0 + spacing
    while x < bay.x1 - 1.5:
        y = bay.y0 + spacing
        while y < bay.y1 - 1.5:
            box = Box(x, y, 0.4, 0.4)
            if clear(room, box, 0.05, -2):
                room.node(PIECES["column"], box)
                room.boxes.append((box, -1))
            y += spacing
        x += spacing


def _half_span(placed: list[Placed]) -> float:
    return max(abs(item.x) + (item.width or PIECES[item.kind].size[0]) / 2 for item in placed)


def _wall_anchor(room: Room, placed: list[Placed]) -> tuple[Point, float] | None:
    walls = long_walls(room, 1.4)
    if not walls:
        return None
    wall = room.rng.choice(walls)
    half = min(_half_span(placed), wall.length / 2 - 0.05)
    return wall.point(room.rng.uniform(half + 0.05, wall.length - half - 0.05), WALL_GAP), wall.heading


def _floor_anchor(room: Room, style: float) -> tuple[Point, float]:
    point = room.shell.sample_point(room.rng)
    if style < 0:
        return point, room.rng.uniform(0, 180)
    return point, style + room.rng.choice((0.0, 90.0))


def _try_group(room: Room, pattern: Pattern, style: float, aisle_width: float) -> bool:
    placed = pattern.build(room.rng)
    anchor = _wall_anchor(room, placed) if pattern.on_wall else _floor_anchor(room, 0.0 if pattern.squared else style)
    return anchor is not None and bool(place_group(room, placed, *anchor, aisle_width))


def _pick(room: Room, counts: dict[int, int]) -> int | None:
    patterns = room.shop.patterns
    open_ = [i for i, p in enumerate(patterns) if counts.get(i, 0) < p.most]
    if not open_:
        return None
    return room.rng.choices(open_, weights=[patterns[i].weight for i in open_])[0]


def _furnished_area(room: Room) -> float:
    return sum(b.w * b.d for b, owner in room.boxes if owner >= 0)


def _early_groups(room: Room, style: float, aisle_width: float) -> dict[int, int]:
    counts: dict[int, int] = {}
    settled: set[str] = set()
    for index, pattern in enumerate(room.shop.patterns):
        if pattern.early is None or pattern.early in settled:
            continue
        if any(_try_group(room, pattern, style, aisle_width) for _ in range(25)):
            counts[index] = 1
            settled.add(pattern.early)
    return counts


def _furnish(room: Room) -> None:
    target = room.shell.area * room.rng.uniform(*room.shop.density)
    style = room.rng.choice((0.0, 0.0, 45.0, -1.0))
    aisle_width = room.rng.uniform(0.42, 0.62)
    counts = _early_groups(room, style, aisle_width)
    for _ in range(1500):
        index = _pick(room, counts)
        if _furnished_area(room) >= target or index is None:
            return
        if _try_group(room, room.shop.patterns[index], style, aisle_width):
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


def _open_spot(room: Room, node: SceneNode) -> Point | None:
    centre, heading = node.transform.position, math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))
    for side in (90.0, -90.0, 0.0, 180.0):
        reach = (node.dimensions.y if side in (90.0, -90.0) else node.dimensions.x) / 2 + 0.55
        angle = math.radians(heading + side)
        spot = (centre.x + reach * math.cos(angle), centre.y + reach * math.sin(angle))
        probe = Box(*spot, 0.5, 0.5)
        if inside_floor(room, spot) and not any(gap_between(probe.corners(), b.corners()) == 0.0
                                                for b, _ in room.boxes):
            return spot
    return None


def _rotate_everything(room: Room) -> None:
    turn = room.rng.choice((0.0, 90.0, 180.0, 270.0)) if room.rng.random() < 0.4 else room.rng.uniform(0, 360)
    shift = (room.rng.uniform(-4, 4), room.rng.uniform(-4, 4), 0.0)
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))

    def moved(stop: Stop) -> Stop:
        x, y = stop.position.x, stop.position.y
        return stop.model_copy(update={"position": Vec3(x=x * c - y * s + shift[0], y=x * s + y * c + shift[1],
                                                        z=0.0)})

    room.nodes = [node.model_copy(update={"transform": Mat4(m=turned(node.transform.m, turn, shift))})
                  for node in room.nodes]
    room.stops = [moved(stop) for stop in room.stops]


# Amenities near the door ---------------------------------------------------


def _kiosks(room: Room, entrance: Entrance) -> None:
    wall = entrance.wall
    if wall is None or room.rng.random() >= room.shop.kiosks:
        return
    kiosk, side = PIECES["kiosk"], room.rng.choice((-1.0, 1.0))
    start = entrance.door.dimensions.x / 2 + 0.75
    for index in range(room.rng.randint(1, 3)):
        t = entrance.t + side * (start + index * (kiosk.size[0] + 0.1))
        box = Box(*wall.point(t, WALL_GAP + kiosk.size[1] / 2), kiosk.size[0], kiosk.size[1], wall.heading + 180.0)
        if not (0.3 < t < wall.length - 0.3 and clear(room, box, 0.0, -2) and commit(room, [Item(kiosk, box)])):
            return
        room.reserved.append(Box(*wall.point(t, WALL_GAP + kiosk.size[1] + 0.4), kiosk.size[0], 0.8, wall.heading))


def _menu_stand(room: Room, entrance: Entrance, hub: Point) -> None:
    """A movable stand just inside the door, which sometimes ends up in the walking route."""
    if room.shop.name not in FOOD_KINDS or room.rng.random() > 0.6:
        return
    ex, ey = entrance.inside
    heading = math.atan2(hub[1] - ey, hub[0] - ex)
    for _ in range(8):
        reach, sideways = room.rng.uniform(0.9, 2.2), room.rng.uniform(-0.9, 0.9)
        x = ex + reach * math.cos(heading) - sideways * math.sin(heading)
        y = ey + reach * math.sin(heading) + sideways * math.cos(heading)
        box = Box(x, y, *PIECES["menu_stand"].size[:2], math.degrees(heading) + 90.0)
        private = any(gap_between(box.corners(), inner.corners()) == 0.0 for inner in room.interiors)
        if _open_floor(room, box) and not private and commit(room, [Item(PIECES["menu_stand"], box)]):
            return


def _accessible_table(room: Room, entrance: Entrance, hub: Point) -> None:
    """In about 60% of dining rooms, an accessible table with its open side toward open floor.

    The open side keeps the 30 by 48 inch forward approach of ADA 2010 902.2 and 305
    clear. Sites beside the entrance-to-counter route are tried first.
    """
    if not room.shop.dining or room.rng.random() > ACCESSIBLE_TABLE_TRY:
        return
    sites = [*(_beside_the_route(room, entrance, hub) for _ in range(40)),
             *((room.shell.sample_point(room.rng), room.rng.choice((0.0, 90.0, 180.0, 270.0))) for _ in range(60))]
    for origin, toward_open in sites:
        approach = Box(*_ahead(origin, toward_open, 0.47 + 0.61), 0.76, 1.22, toward_open + 90.0)
        if _open_floor(room, approach) and place_group(room, accessible_table(room.rng), origin, toward_open + 90.0,
                                                       0.5):
            room.reserved.append(approach)
            return


def _beside_the_route(room: Room, entrance: Entrance, hub: Point) -> tuple[Point, float]:
    """A spot 1.3 to 2.6 m to one side of the entrance-to-counter line, and the direction back to the line."""
    ex, ey = entrance.inside
    length, heading = math.dist(entrance.inside, hub), math.degrees(math.atan2(hub[1] - ey, hub[0] - ex))
    along, side = room.rng.uniform(0.15, 0.85) * length, room.rng.choice((-1.0, 1.0))
    origin = _ahead(_ahead(entrance.inside, heading, along), heading + 90.0 * side, room.rng.uniform(1.3, 2.6))
    return origin, heading - 90.0 * side


def _open_floor(room: Room, box: Box) -> bool:
    shape = box.corners()
    return (all(inside_floor(room, corner) for corner in shape) and not room.shell.blocked(shape)
            and not any(gap_between(shape, other.corners()) == 0.0 for other, _ in room.boxes))


def _ahead(point: Point, direction: float, distance: float) -> Point:
    return (point[0] + distance * math.cos(math.radians(direction)),
            point[1] + distance * math.sin(math.radians(direction)))


# Building ------------------------------------------------------------------


def _shell(rng: random.Random, shop: ShopType, from_scan: bool) -> RectShell | ScanShell:
    return ScanShell.draw(rng) if from_scan else RectShell.draw(rng, shop.size)


def _entry_stops(door: SceneNode, inside: Point) -> tuple[Stop, Stop]:
    at = Vec3(x=inside[0], y=inside[1], z=0.0)
    return (Stop(name="Entrance", position=at, anchor_node_id=door.id),
            Stop(name="Exit", position=at, anchor_node_id=door.id))


def _second_door(room: Room, entrance: Entrance, hub: Point, width: float) -> None:
    others = [w for w in long_walls(room, 2.6) if w != entrance.wall]
    if not room.shell.synthetic or not others or room.rng.random() > 0.35:
        return
    door = add_door(room, room.rng.choice(others), "Back door")
    corridor(room, hub, door.inside, width)


def _restrooms(room: Room, hub: Point) -> list[Stop]:
    if room.rng.random() >= room.shop.restroom:
        return []
    first = add_restroom(room, hub)
    if first is None:
        if room.shop.restroom >= 1.0:
            raise Unbuildable("this room has no corner for its restroom")
        return []
    second = add_restroom(room, hub) if room.shop.registers[1] > 1 and room.rng.random() < 0.5 else None
    return [first] if second is None else [first, second]


def _service(room: Room, entrance: Entrance) -> tuple[list[Stop], Point]:
    spot, lowered = build_counter(room, entrance, room.rng.choice(room.shop.counters))
    service = service_stops(spot, room.shop.counter_label, room.shop.handoff_label)
    hub = (service[0].position.x, service[0].position.y)
    cashier(room, spot, lowered, room.rng.randint(*room.shop.registers))
    if room.shop.menus:
        menu_boards_behind(room, spot)
    return service, hub


def _routes(room: Room, entrance: Entrance, hub: Point) -> list[Stop]:
    route_width = room.rng.uniform(1.0, 1.5)
    corridor(room, entrance.inside, hub, route_width)
    for centre in room.shell.wing_centres():
        corridor(room, hub, centre, route_width)
    _second_door(room, entrance, hub, route_width)
    return _restrooms(room, hub)


def _amenities(room: Room, entrance: Entrance, hub: Point) -> None:
    _columns(room)
    _kiosks(room, entrance)
    if room.shop.menus and room.rng.random() < 0.35:
        menu_board_on_the_route(room, hub)
    _accessible_table(room, entrance, hub)
    _menu_stand(room, entrance, hub)


def _movable_floor_pieces(room: Room) -> int:
    return sum(1 for node in room.nodes if node.kind == "object" and node.movable
               and node.transform.m[11] - node.dimensions.z / 2 <= RESTING_GAP)


def _lacks_table_heights(room: Room) -> bool:
    """A dining room without a seated-height table, or with three or more tables and no high one.

    The early "low" and "high" groups make this rare but do not rule it out: both can fail to find a
    site in 25 tries and the weighted draw may never pick them afterwards."""
    if not room.shop.dining:
        return False
    tops = [node.transform.m[11] + node.dimensions.z / 2 for node in room.nodes if node.label in DINING_TABLES]
    has_seated = any(top <= SEATED_TABLE_TOP for top in tops)
    has_high = len(tops) < LEAST_TABLES_FOR_A_HIGH_ONE or any(top >= HIGH_TABLE_TOP for top in tops)
    return not (has_seated and has_high)


def build_room(name: str, shop: ShopType, rng: random.Random,
               from_scan: bool = False) -> tuple[SceneGraph, Scenario] | None:
    room = Room(name, shop, rng, _shell(rng, shop, from_scan))
    entrance = room.shell.build(room)
    service, hub = _service(room, entrance)
    restrooms = _routes(room, entrance, hub)
    _amenities(room, entrance, hub)
    _furnish(room)
    visit = _destination(room)
    if visit is None or _movable_floor_pieces(room) < LEAST_MOVABLE_PIECES or _lacks_table_heights(room):
        return None
    arrive, leave = _entry_stops(entrance.door, entrance.inside)
    journey = [visit, *service] if room.shop.browse_first else [*service, visit]
    room.stops = [arrive, *journey, *restrooms, leave]
    _rotate_everything(room)
    return room.graph(), Scenario(name=shop.errand, stops=room.stops)


SPACE_TYPOLOGIES = {
    "cafe": SpaceTypology.QSR_BEVERAGE,
    "boba tea shop": SpaceTypology.QSR_BEVERAGE,
    "ice cream parlor": SpaceTypology.QSR_BEVERAGE,
    "restaurant": SpaceTypology.RESTAURANT_DINING,
    "fast food restaurant": SpaceTypology.RESTAURANT_DINING,
    "bakery": SpaceTypology.COMMERCIAL_RETAIL,
    "boutique": SpaceTypology.COMMERCIAL_RETAIL,
    "bookstore": SpaceTypology.COMMERCIAL_RETAIL,
    "convenience store": SpaceTypology.COMMERCIAL_RETAIL,
    "pharmacy": SpaceTypology.COMMERCIAL_RETAIL,
    "small office": SpaceTypology.BUSINESS_OFFICE,
    "hotel lobby": SpaceTypology.HOSPITALITY_LOUNGE,
}
"""Which ADA directives govern each kind of shop. A salon and a clinic waiting room match no space type
the directive corpus defines, so they get none rather than a guess."""


def space_typology_for(errand: str) -> SpaceTypology | None:
    """The space type of a generated room, read from its errand (the scenario's name)."""
    shop = next((shop for shop in SHOP_TYPES if shop.errand == errand), None)
    return None if shop is None else SPACE_TYPOLOGIES.get(shop.name)


CIRCULATION_RULES = frozenset({
    "route_clear_width", "turn_clear_width", "passing_space", "turning_space",
    "service_counter_approach", "exit_path", "restroom_turning_space",
})
"""Rules about getting around the room. A generated room is the owner's working layout, so it must pass
these before any scramble; a counter or dispenser at the wrong height is left in on purpose."""


def circulation_problems(graph: SceneGraph, scenario: Scenario) -> list[str]:
    checker = TrainingChecker(scenario)
    return sorted({finding.check_id for finding in checker.assess(graph).problems
                   if finding.check_id in CIRCULATION_RULES})

def generate(index: int, attempts: int = 30) -> tuple[SceneGraph, Scenario, ShopType]:
    """The room for this index. Whether it starts from a scan is settled once per index, so the scan
    share holds even though scan shells fail more draws; after half the attempts it falls back to rectangles.
    A draw whose own layout already blocks a route or a turn is discarded, so every circulation problem a
    scramble produces is one the scramble caused."""
    shop = SHOP_TYPES[index % len(SHOP_TYPES)]
    wants_scan = random.Random(index).random() < SCAN_SHARE
    for attempt in range(attempts):
        from_scan = wants_scan and attempt < attempts // 2
        try:
            made = build_room(f"generated-{index:05d}", shop, random.Random(index * 7919 + attempt), from_scan)
        except Unbuildable:
            continue
        if made is not None and not circulation_problems(*made):
            return (*made, shop)
    raise RuntimeError(f"could not generate a room for index {index}")
