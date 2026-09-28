"""The service counter and what sits on or behind it: registers, the cash drawer, card readers, menu boards.

When a lowered counter section exists, ADA 2010 904.4 wants people to be able
to pay there. The generator sometimes leaves the movable card reader on the
high part of the counter, so `point_of_sale_height` fails and moving the reader
onto the lowered section fixes it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shop_geometry import Box, Point, Wall
from shop_room import (
    PIECES,
    WALL_GAP,
    Entrance,
    Item,
    Piece,
    Room,
    Unbuildable,
    clear,
    commit,
    long_walls,
    node_box,
)
from standardphysics_agents.checks.roles import SERVICE_COUNTER_LABELS
from standardphysics_contracts import SceneNode, Stop, Vec3, to_meters

COUNTER_GROUP = -3
"""The counter and its lowered section share a group so the section may touch the counter."""
REGISTER = Piece("Cash register", "electronics", (0.35, 0.28, 0.25), False)
CASH_DRAWER = Piece("Cash drawer", "storage", (0.4, 0.26, 0.1), False)
CARD_READER = Piece("Card reader", "electronics", (0.1, 0.16, 0.05))
MENU_BOARD = "Menu board"


@dataclass(frozen=True)
class CounterSpot:
    """A counter in the frame of the wall behind it: staff stand between the wall and `staff` metres out."""

    node: SceneNode
    wall: Wall
    t: float
    staff: float
    depth: float
    height: float
    on_a_real_wall: bool

    @property
    def length(self) -> float:
        return self.node.dimensions.x

    @property
    def front_edge(self) -> float:
        return self.staff + self.depth

    def at(self, along: float, out: float) -> Point:
        return self.wall.point(self.t + along, out)


def _counter_height(room: Room, lowered: bool) -> float:
    choices = (38.0, 42.0, 42.0, 47.0) if lowered else (34.0, 36.0, 36.0, 38.0, 42.0, 47.0)
    return to_meters(room.rng.choice(choices))


def _open_behind(room: Room, box: Box) -> bool:
    return clear(room, box, 0.0, COUNTER_GROUP)


def _counter_walls(room: Room, entrance: Entrance, length: float) -> list[Wall]:
    """Real walls first, in random order, then any open edge of a scan's floor."""
    walls = [w for w in long_walls(room, length + 1.6) if w != entrance.wall
             and room.shell.inside_main(w.point(w.length / 2, 0.5))]
    edges = [w for w in room.shell.open_edges() if w.length >= length + 1.6]
    room.rng.shuffle(walls)
    room.rng.shuffle(edges)
    return walls + edges


def _reserve_service_zones(room: Room, spot: CounterSpot) -> None:
    heading = spot.wall.heading
    if spot.staff > 0:
        room.reserved.append(Box(*spot.at(0.0, spot.staff / 2), spot.length + 0.6, spot.staff, heading))
    room.reserved.append(Box(*spot.at(0.0, spot.front_edge + 0.75), spot.length + 0.6, 1.5, heading))


def _try_counter_site(room: Room, wall: Wall, label: str, lowered: bool) -> CounterSpot | None:
    rng = room.rng
    length, depth = rng.uniform(1.8, min(4.5, wall.length - 1.6)), rng.uniform(0.6, 0.75)
    back_bar = rng.random() < 0.5
    staff = rng.uniform(1.0, 1.3) + (0.6 if back_bar else 0.0)
    t = rng.uniform(0.8 + length / 2, wall.length - 0.8 - length / 2)
    box = Box(*wall.point(t, staff + depth / 2), length, depth, wall.heading)
    staff_zone = Box(*wall.point(t, staff / 2 + 0.05), length, staff - 0.1, wall.heading)
    if not (_open_behind(room, box) and _open_behind(room, staff_zone)):
        return None
    height = _counter_height(room, lowered)
    node = room.node(Piece(label, "counter", (length, depth, height), False), box)
    room.boxes.append((box, COUNTER_GROUP))
    if back_bar:
        room.node(PIECES["back_bar"], Box(*wall.point(t, 0.3), length, 0.6, wall.heading))
    return CounterSpot(node, wall, t, staff, depth, height, wall in room.shell.walls)


def _scanned_counter(room: Room) -> CounterSpot | None:
    """A service counter the scan already holds, framed from its back edge toward the open floor."""
    for node in room.nodes:
        if node.kind != "object" or node.label.strip().casefold() not in SERVICE_COUNTER_LABELS:
            continue
        box = node_box(node)
        for turn in (0.0, 180.0):
            wall = _back_edge(box, turn)
            if room.shell.inside(wall.point(wall.length / 2, box.d + 0.8)):
                room.boxes.append((box, COUNTER_GROUP))
                top = node.transform.m[11] + node.dimensions.z / 2
                return CounterSpot(node, wall, box.w / 2, 0.0, box.d, top, False)
    return None


def _back_edge(box: Box, turn: float) -> Wall:
    heading = math.radians(box.heading + turn)
    ux, uy = math.cos(heading), math.sin(heading)
    nx, ny = -uy, ux
    a = (box.cx - ux * box.w / 2 - nx * box.d / 2, box.cy - uy * box.w / 2 - ny * box.d / 2)
    return Wall(a, (a[0] + ux * box.w, a[1] + uy * box.w))


def build_counter(room: Room, entrance: Entrance, label: str) -> tuple[CounterSpot, Box | None]:
    scanned = _scanned_counter(room)
    if scanned is not None:
        _reserve_service_zones(room, scanned)
        return scanned, None
    lowered = room.rng.random() < 0.4
    for wall in _counter_walls(room, entrance, 1.8):
        for _ in range(4):
            spot = _try_counter_site(room, wall, label, lowered)
            if spot is not None:
                section = _lowered_section(room, spot) if lowered else None
                _reserve_service_zones(room, spot)
                return spot, section
    raise Unbuildable("no wall has room for the counter")


def _lowered_section(room: Room, spot: CounterSpot) -> Box | None:
    lowered = PIECES["lowered"]
    for side in room.rng.sample((-1, 1), 2):
        along = side * (spot.length / 2 + lowered.size[0] / 2)
        box = Box(*spot.at(along, spot.staff + spot.depth / 2), lowered.size[0], spot.depth, spot.wall.heading)
        if _open_behind(room, box):
            room.node(lowered, box)
            room.boxes.append((box, COUNTER_GROUP))
            room.reserved.append(Box(*spot.at(along, spot.front_edge + 0.75), lowered.size[0] + 0.4, 1.5,
                                     spot.wall.heading))
            return box
    return None


def service_stops(spot: CounterSpot, counter_label: str, handoff_label: str | None) -> list[Stop]:
    """The stops a customer makes at this counter.

    Most businesses interact with the counter once: check in, pay, ask a
    question. Only a business that actually hands something over at a
    separate spot along the counter (a quick-service order-then-pickup
    layout) gets a second stop.
    """
    if handoff_label is None:
        at = spot.at(0.0, spot.front_edge + 0.6)
        return [Stop(name=counter_label, position=Vec3(x=at[0], y=at[1], z=0.0), anchor_node_id=spot.node.id)]
    order_at, handoff_at = spot.at(-spot.length * 0.25, spot.front_edge + 0.6), spot.at(spot.length * 0.25,
                                                                                        spot.front_edge + 0.6)
    return [Stop(name=counter_label, position=Vec3(x=order_at[0], y=order_at[1], z=0.0), anchor_node_id=spot.node.id),
            Stop(name=handoff_label, position=Vec3(x=handoff_at[0], y=handoff_at[1], z=0.0),
                 anchor_node_id=spot.node.id)]


# Paying --------------------------------------------------------------------


def _on_top(piece: Piece, spot: CounterSpot, along: float, out: float, top: float) -> Item:
    box = Box(*spot.at(along, out), piece.size[0], piece.size[1], spot.wall.heading)
    return Item(piece, box, top + piece.size[2] / 2)


def _till(spot: CounterSpot, along: float, top: float) -> list[Item]:
    """A register on the customer half of the top and its drawer on the staff half."""
    return [_on_top(REGISTER, spot, along, spot.front_edge - 0.17, top),
            _on_top(CASH_DRAWER, spot, along, spot.staff + 0.15, top)]


def _lowered_along(spot: CounterSpot, lowered: Box) -> float:
    ux, uy = math.cos(math.radians(spot.wall.heading)), math.sin(math.radians(spot.wall.heading))
    centre = spot.at(0.0, 0.0)
    return (lowered.cx - centre[0]) * ux + (lowered.cy - centre[1]) * uy


def cashier(room: Room, spot: CounterSpot, lowered: Box | None, registers: int) -> None:
    if lowered is not None:
        along = _lowered_along(spot, lowered)
        commit(room, _till(spot, along - 0.2, PIECES["lowered"].size[2]))
        _card_reader_by_the_lowered_section(room, spot, along)
        return
    for index in range(registers):
        commit(room, _till(spot, -spot.length / 2 + (index + 0.5) * spot.length / registers, spot.height))
    if room.rng.random() < 0.5:
        for _ in range(5):
            along = room.rng.uniform(-spot.length / 2 + 0.1, spot.length / 2 - 0.1)
            if commit(room, [_on_top(CARD_READER, spot, along, spot.front_edge - 0.1, spot.height)]):
                return


def _card_reader_by_the_lowered_section(room: Room, spot: CounterSpot, along: float) -> None:
    if room.rng.random() < 0.2:
        return
    if room.rng.random() < 0.5:
        side = 1.0 if along > 0 else -1.0
        commit(room, [_on_top(CARD_READER, spot, side * (spot.length / 2 - 0.2), spot.front_edge - 0.1,
                              spot.height)])
        return
    commit(room, [_on_top(CARD_READER, spot, along + 0.25, spot.front_edge - 0.1, PIECES["lowered"].size[2])])


# Menu boards ---------------------------------------------------------------


def _board(width: float, projection: float, bottom: float, tall: float, at: Point, heading: float) -> Item:
    piece = Piece(MENU_BOARD, "sign", (width, projection, tall), False)
    return Item(piece, Box(*at, width, projection, heading + 180.0), bottom + tall / 2, tall)


def menu_boards_behind(room: Room, spot: CounterSpot) -> None:
    """High, flush boards on the wall behind the staff, where menu boards usually hang."""
    if not spot.on_a_real_wall:
        return
    count = room.rng.randint(1, 3)
    width = min(room.rng.uniform(0.9, 1.2), (spot.length + 1.0) / count)
    bottom, tall = room.rng.uniform(2.05, 2.2), 0.6
    offset = room.rng.uniform(-0.4, 0.4)
    for index in range(count):
        along = offset + (index - (count - 1) / 2) * (width + 0.05)
        commit(room, [_board(width, 0.04, bottom, tall, spot.at(along, WALL_GAP + 0.02), spot.wall.heading)])


def menu_board_on_the_route(room: Room, hub: Point) -> None:
    """A board hung low and deep on a customer wall, which ADA 2010 307.2 counts as a protruding object."""
    walls = sorted(long_walls(room, 1.6), key=lambda w: w.distance_to(hub))[:4]
    if not walls:
        return
    wall = room.rng.choice(walls)
    width, projection = room.rng.uniform(0.7, 1.1), room.rng.uniform(0.12, 0.2)
    bottom = room.rng.uniform(1.2, 1.6)
    for _ in range(4):
        t = room.rng.uniform(width / 2 + 0.2, wall.length - width / 2 - 0.2)
        at = wall.point(t, WALL_GAP + projection / 2)
        if commit(room, [_board(width, projection, bottom, 0.6, at, wall.heading)]):
            return
