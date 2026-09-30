"""The room being built: its pieces, its shell, and the checked way to add things to it.

Every piece goes in through `commit` or `place_group`, both of which run the
hard constraints the fix loop uses, so a generated room never holds a layout
nobody could really have.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from shop_geometry import Box, Point, Rect, Wall, transform
from standardphysics_agents.checks import roles
from standardphysics_agents.fix.constraints import violations
from standardphysics_agents.fix.moves import RESTING_GAP
from standardphysics_contracts import SceneGraph, SceneNode, Stop, Vec3, to_meters
from standardphysics_pipeline import gap_between
from standardphysics_pipeline.footprints import Polygon
from standardphysics_pipeline.occupancy import blocks_floor

NAMESPACE = uuid.UUID("5b0f6a53-2f47-4c52-a7f3-6f1e9d2c4b10")
WALL_THICKNESS = 0.1
WALL_HEIGHT = 3.0
WALL_GAP = 0.03
SEAT_REACH = 1.0
FACING_TOLERANCE = 30.0
SURFACE_LABELS = frozenset({"Table", "Dining table", "Cafe table", "Bar table", "Accessible table", "Desk",
                            "Coffee table"})


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
    "booth_bench": Piece("Bench", "bench", (1.2, 0.5, 1.0), False),
    "waiting_bench": Piece("Bench", "bench", (1.5, 0.5, 0.45)),
    "salon_chair": Piece("Chair", "chair", (0.65, 0.65, 1.0)),
    "task_chair": Piece("Chair", "chair", (0.6, 0.6, 1.0)),
    "cafe_table": Piece("Cafe table", "table", (0.6, 0.6, 0.75)),
    "four_top": Piece("Table", "table", (0.8, 0.8, 0.75)),
    "booth_table": Piece("Table", "table", (0.75, 1.2, 0.75), False),
    "accessible_two": Piece("Accessible table", "table", (0.75, 0.75, 0.76)),
    "accessible_four": Piece("Accessible table", "table", (0.9, 0.9, 0.76)),
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
    "tray_return": Piece("Tray return", "storage", (1.0, 0.5, 1.1)),
    "drink_station": Piece("Drink station", "counter", (1.2, 0.6, 0.9), False),
    "condiments": Piece("Condiment station", "storage", (1.0, 0.5, 1.0)),
    "menu_stand": Piece("Menu stand", "storage", (0.5, 0.4, 1.4)),
    "luggage_cart": Piece("Luggage cart", "storage", (1.1, 0.6, 1.9)),
    "kiosk": Piece("Self-service kiosk", "storage", (0.55, 0.5, 1.6), False),
    "atm": Piece("ATM", "storage", (0.55, 0.6, 1.5), False),
    "host_stand": Piece("Host stand", "storage", (0.6, 0.5, 1.2)),
    "column": Piece("Column", "column", (0.4, 0.4, WALL_HEIGHT), False),
    "back_bar": Piece("Back bar", "storage", (1.0, 0.6, 0.9), False),
    "lowered": Piece("Lowered counter section", "counter", (to_meters(36.0), 0.7, to_meters(36.0)), False),
}


@dataclass(frozen=True)
class Placed:
    """A piece in its group's frame: x runs along the group, y runs out into the room (90 degrees)."""

    kind: str
    x: float
    y: float
    heading: float = 0.0
    """Turn for pieces with no front, such as tables and racks."""
    width: float | None = None
    unit: int = 0
    """Pieces sharing a unit stay or go together; unit 0 means the whole group is one."""
    faces: float | None = None
    """Where the piece's front points in the group frame, in degrees; 90 is out into the room."""
    height: float | None = None


def front_yaw(front_direction: float) -> float:
    """The yaw that points a piece's front (its local -Y side) along `front_direction`."""
    return front_direction + 90.0


class Shell(Protocol):
    """The empty room: where the floor is, which faces are walls, and where people come in."""

    walls: list[Wall]
    area: float
    synthetic: bool

    def inside(self, point: Point) -> bool: ...

    def inside_main(self, point: Point) -> bool: ...

    def sample_point(self, rng: random.Random) -> Point: ...

    def blocked(self, shape: Polygon) -> bool: ...

    def column_bay(self) -> Rect | None: ...

    def open_edges(self) -> list[Wall]:
        """Edges of the usable floor with no wall along them, room on the left; a last resort for fixtures."""
        ...

    def wing_centres(self) -> list[Point]: ...

    def build(self, room: Room) -> Entrance: ...


@dataclass(frozen=True)
class Entrance:
    door: SceneNode
    inside: Point
    wall: Wall | None
    t: float


@dataclass
class Room:
    name: str
    shop: object
    rng: random.Random
    shell: Shell
    nodes: list[SceneNode] = field(default_factory=list)
    boxes: list[tuple[Box, int]] = field(default_factory=list)
    """Floor footprints with the group each belongs to; -1 for fixed architecture."""
    reserved: list[Box] = field(default_factory=list)
    interiors: list[Box] = field(default_factory=list)
    """Enclosed rooms such as restrooms, which nothing of the shop's may enter."""
    stops: list[Stop] = field(default_factory=list)
    groups: int = 0

    def node(self, piece: Piece, box: Box, z: float | None = None, kind: str = "object",
             tall: float | None = None) -> SceneNode:
        tall = piece.size[2] if tall is None else tall
        made = SceneNode(
            id=uuid.uuid5(NAMESPACE, f"{self.name}:{len(self.nodes)}"), kind=kind, label=piece.label,
            raw_category=piece.category, dimensions=Vec3(x=box.w, y=box.d, z=tall),
            transform=transform(box.cx, box.cy, tall / 2 if z is None else z, box.heading),
            movable=piece.movable,
        )
        self.nodes.append(made)
        return made

    def graph(self, nodes: list[SceneNode] | None = None) -> SceneGraph:
        return SceneGraph(scan_id=uuid.uuid5(NAMESPACE, self.name), nodes=self.nodes if nodes is None else nodes)


@dataclass(frozen=True)
class Item:
    """One thing to add: its piece, where its footprint sits, and optionally its centre height and height."""

    piece: Piece
    box: Box
    z: float | None = None
    tall: float | None = None


def inside_floor(room: Room, point: Point) -> bool:
    return room.shell.inside(point)


def clear(room: Room, box: Box, aisle: float, group: int) -> bool:
    if not all(inside_floor(room, corner) for corner in box.corners(WALL_GAP)):
        return False
    if room.shell.blocked(box.corners()):
        return False
    if any(gap_between(box.corners(), zone.corners()) == 0.0 for zone in room.reserved):
        return False
    grown = box.corners(aisle)
    return not any(owner != group and gap_between(grown, other.corners()) == 0.0 for other, owner in room.boxes)


def commit(room: Room, items: list[Item], owner: int = -1) -> list[SceneNode] | None:
    """Add the items if together they break no hard constraint and no seat ends up facing away."""
    before = len(room.nodes)
    added = [room.node(item.piece, item.box, item.z, tall=item.tall) for item in items]
    base = room.graph(room.nodes[:before])
    ids = frozenset(node.id for node in added)
    if violations(base, room.graph(), added=ids) or misfacing_seats(room.nodes, added) or in_any_door_swing(
            room.nodes, added):
        del room.nodes[before:]
        return None
    room.boxes += [(item.box, owner) for item, node in zip(items, added, strict=True) if blocks_floor(node)]
    return added


# Door swings --------------------------------------------------------------


SWING_REACH = math.sqrt(1.25)
"""The checker keeps clear a square of half-sides w/2 and w around a door, square to the world axes.

The room is turned to a random heading after it is built, so the square lands
at an angle nobody knows yet. Every such square fits inside a disc of radius
w * sqrt(1.25) around the door, so keeping that disc clear keeps the square
clear whatever the turn.
"""


def in_any_door_swing(nodes: list[SceneNode], added: list[SceneNode]) -> bool:
    doors = [node for node in nodes if node.kind == "door"]
    standing = [node for node in added if node.transform.m[11] - node.dimensions.z / 2 <= RESTING_GAP]
    return any(node_box(piece).distance_to(node_box(door).centre) < SWING_REACH * max(door.dimensions.x,
                                                                                        door.dimensions.y)
               for piece in standing for door in doors)


# Seats face what they serve ------------------------------------------------


def _yaw(node: SceneNode) -> float:
    return math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))


def node_box(node: SceneNode) -> Box:
    return Box(node.transform.m[3], node.transform.m[7], node.dimensions.x, node.dimensions.y, _yaw(node))


def _angle_off(a: float, b: float) -> float:
    difference = abs(a - b) % 360.0
    return min(difference, 360.0 - difference)


def seat_point(seat: Box, toward: Point) -> Point:
    """The point on the seat's long midline nearest `toward`, so a long bench is judged where it meets a table."""
    lx, _ = seat.to_local(toward)
    along = max(-seat.w / 2, min(seat.w / 2, lx))
    c, s = math.cos(math.radians(seat.heading)), math.sin(math.radians(seat.heading))
    return seat.cx + along * c, seat.cy + along * s


def direction_into(surface: Box, point: Point) -> float:
    """The world direction from `point` into the surface's nearest side, judged in the surface's own frame."""
    lx, ly = surface.to_local(point)
    past_x, past_y = abs(lx) - surface.w / 2, abs(ly) - surface.d / 2
    local = (180.0 if lx > 0 else 0.0) if past_x > past_y else (-90.0 if ly > 0 else 90.0)
    return surface.heading + local


def _is_seat(node: SceneNode) -> bool:
    return node.kind == "object" and roles.is_seating(node)


def _is_surface(node: SceneNode) -> bool:
    return node.kind == "object" and node.label in SURFACE_LABELS


def seat_faces_its_surface(seat: SceneNode, surfaces: list[SceneNode]) -> bool:
    box = node_box(seat)
    reach = [(surface.distance_to(seat_point(box, surface.centre)), surface) for surface in map(node_box, surfaces)]
    near = [(distance, surface) for distance, surface in reach if distance <= SEAT_REACH]
    if not near:
        return True
    _, surface = min(near, key=lambda pair: pair[0])
    wanted = direction_into(surface, seat_point(box, surface.centre))
    return _angle_off(_yaw(seat) - 90.0, wanted) <= FACING_TOLERANCE


def misfacing_seats(nodes: list[SceneNode], added: list[SceneNode]) -> bool:
    surfaces = [node for node in nodes if _is_surface(node)]
    new_surfaces = [node for node in added if _is_surface(node)]
    seats = [node for node in nodes if _is_seat(node) and (node in added or _near_any(node, new_surfaces))]
    return any(not seat_faces_its_surface(seat, surfaces) for seat in seats)


def _near_any(seat: SceneNode, surfaces: list[SceneNode]) -> bool:
    centre = (seat.transform.m[3], seat.transform.m[7])
    return any(node_box(surface).distance_to(centre) < SEAT_REACH + 1.5 for surface in surfaces)


# Groups --------------------------------------------------------------------


def world_box(placed: Placed, origin: Point, heading: float) -> Box:
    piece = PIECES[placed.kind]
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    x = origin[0] + placed.x * c - placed.y * s
    y = origin[1] + placed.x * s + placed.y * c
    turn = placed.heading if placed.faces is None else front_yaw(placed.faces)
    return Box(x, y, placed.width or piece.size[0], piece.size[1], heading + turn)


def _surviving_units(room: Room, placed: list[Placed], boxes: list[Box], aisle: float) -> list[int]:
    """Indices of the pieces kept: whole units that fit, if at least half the units do."""
    fits = [clear(room, box, aisle, room.groups) for box in boxes]
    units = {item.unit for item in placed}
    kept = {u for u in units if all(ok for item, ok in zip(placed, fits, strict=True) if item.unit == u)}
    if 0 in units and 0 not in kept:
        return []
    if len(kept) * 2 < len(units):
        return []
    return [i for i, item in enumerate(placed) if item.unit in kept]


def _item(placed: Placed, box: Box) -> Item:
    piece = PIECES[placed.kind]
    tall = placed.height or piece.size[2]
    return Item(piece, box, tall / 2, tall)


def place_group(room: Room, placed: list[Placed], origin: Point, heading: float, aisle: float) -> list[SceneNode]:
    boxes = [world_box(item, origin, heading) for item in placed]
    keep = _surviving_units(room, placed, boxes, aisle)
    if not keep:
        return []
    added = commit(room, [_item(placed[i], boxes[i]) for i in keep], owner=room.groups)
    if added is None:
        return []
    room.groups += 1
    return added


# Doors ---------------------------------------------------------------------


def add_door(room: Room, wall: Wall, label: str, width: float | None = None) -> Entrance:
    width = width or room.rng.uniform(0.9, 1.07)
    t = room.rng.uniform(0.7 + width / 2, wall.length - 0.7 - width / 2)
    cx, cy = wall.point(t, -WALL_THICKNESS / 2)
    door = room.node(Piece(label, "door", (width, WALL_THICKNESS, 2.1), False),
                     Box(cx, cy, width, WALL_THICKNESS, wall.heading), kind="door")
    inside = wall.point(t, 0.9)
    room.reserved.append(Box(*inside, width + 0.8, 1.8, wall.heading))
    return Entrance(door, inside, wall, t)


def corridor(room: Room, start: Point, end: Point, width: float) -> None:
    length = math.dist(start, end)
    if length < 0.1:
        return
    heading = math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    room.reserved.append(Box((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, length + width, width, heading))


def long_walls(room: Room, least: float) -> list[Wall]:
    return [wall for wall in room.shell.walls if wall.length >= least]
