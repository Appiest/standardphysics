"""A single-user restroom carved out of a corner or along a wall.

The fixtures follow ADA 2010 604 and 609 closely enough to be worth checking:
the toilet's centreline sits 16 to 18 inches (0.41 to 0.46 m) from the side
wall, a 42 inch side grab bar and a 36 inch rear bar sit 33 to 36 inches up.
About one in ten toilets is set off that spec, as real ones are.

Movable clutter (a trash can, sometimes a supply cart or step stool) is placed
so that about half the restrooms lose their 60 inch turning circle (ADA 2010
603.2.1) while the fixtures alone would leave one, which makes the loss
something a rearrangement can fix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shop_geometry import Box, Point, Wall
from shop_room import (
    Item,
    Piece,
    Room,
    clear,
    commit,
    corridor,
)
from standardphysics_contracts import SceneNode, Stop, Vec3, to_meters
from standardphysics_pipeline import gap_between

PARTITION = Piece("Partition wall", "wall", (0.0, 0.0, 2.4), False)
PARTITION_THICKNESS = 0.1
TOILET = Piece("Toilet", "toilet", (0.40, 0.70, 0.80), False)
LAVATORY = Piece("Lavatory", "sink", (0.50, 0.45, 0.17), False)
SIDE_GRAB_BAR = Piece("Grab bar", "grab bar", (1.07, 0.05, 0.05), False)
REAR_GRAB_BAR = Piece("Grab bar", "grab bar", (0.91, 0.05, 0.05), False)
CHANGING_TABLE = Piece("Baby changing table", "changing table", (0.9, 0.1, 0.55), False)
CLUTTER = (Piece("Trash can", "storage", (0.35, 0.35, 0.6)),
           Piece("Supply cart", "storage", (0.6, 0.45, 0.9)),
           Piece("Step stool", "storage", (0.45, 0.35, 0.25)))
GRAB_BAR_CENTRE_HEIGHT = 0.86
LAVATORY_TOP = 0.85
TURNING_RADIUS = to_meters(60.0) / 2
SNUG = 0.01
TIGHT_SHARE = 0.2
"""How often a restroom is drawn from the full size range, where the fixtures alone can use up the turning space."""


@dataclass(frozen=True)
class Frame:
    """Restroom coordinates: x runs from the toilet's side wall, y from the rear wall out to the door."""

    wall: Wall
    t0: float
    width: float
    depth: float
    toilet_left: bool
    rear_open: bool = False
    """The rear is an open edge of a scan's floor rather than a wall, so it needs a partition too."""

    def at(self, x: float, y: float) -> Point:
        return self.wall.point(self.t0 + x if self.toilet_left else self.t0 + self.width - x, y)

    def direction(self, local: float) -> float:
        heading = self.wall.heading
        return heading + local if self.toilet_left else heading + 180.0 - local

    def box(self, x: float, y: float, w: float, d: float, axis: float = 0.0) -> Box:
        """A box whose width runs along local direction `axis`."""
        return Box(*self.at(x, y), w, d, self.direction(axis))

    def facing(self, piece: Piece, x: float, y: float, front: float, z: float | None = None) -> Item:
        """A piece whose front points along local direction `front`, its width across that direction."""
        box = Box(*self.at(x, y), piece.size[0], piece.size[1], self.direction(front) + 90.0)
        return Item(piece, box, z)


@dataclass(frozen=True)
class Layout:
    frame: Frame
    door_x: float
    door_width: float
    toilet_x: float
    lavatory_on_rear: bool

    @property
    def door_swing(self) -> tuple[float, float, float, float]:
        f = self.frame
        return (self.door_x - self.door_width / 2, f.depth + PARTITION_THICKNESS / 2 - self.door_width,
                self.door_x + self.door_width / 2, f.depth)

    def outside(self, out: float) -> Point:
        return self.frame.at(self.door_x, self.frame.depth + PARTITION_THICKNESS + out)


# Siting --------------------------------------------------------------------


def _needs_side(room: Room, frame: Frame, x: float) -> bool:
    return room.shell.inside(frame.at(x, frame.depth / 2))


def _sides(room: Room, frame: Frame) -> tuple[bool, bool]:
    return _needs_side(room, frame, -0.15), _needs_side(room, frame, frame.width + 0.15)


def _footprint(room: Room, frame: Frame) -> Box:
    """The restroom with its partitions, pulled in 4 cm where it meets the room's own walls."""
    t, near_side, far_side = PARTITION_THICKNESS, *_sides(room, frame)
    low, high = (-t if near_side else 0.04), (frame.width + t if far_side else frame.width - 0.04)
    return frame.box((low + high) / 2, (frame.depth + t + 0.04) / 2, high - low, frame.depth + t - 0.04)


def _frames_along(room: Room, walls: list[Wall], width: float, depth: float, rear_open: bool) -> list[Frame]:
    frames = []
    for wall in (w for w in walls if w.length >= width + 0.2):
        middle = room.rng.uniform(0.6, wall.length - width - 0.6) if wall.length > width + 1.2 else 0.0
        frames += [Frame(wall, 0.0, width, depth, True, rear_open),
                   Frame(wall, wall.length - width, width, depth, False, rear_open),
                   Frame(wall, middle, width, depth, room.rng.random() < 0.5, rear_open)]
    room.rng.shuffle(frames)
    return frames


def _candidate_frames(room: Room, width: float, depth: float) -> list[Frame]:
    """Corners and stretches of real wall first, then open edges of a scan's floor."""
    return (_frames_along(room, room.shell.walls, width, depth, False)
            + _frames_along(room, room.shell.open_edges(), width, depth, True))


def _layout(room: Room, frame: Frame) -> Layout:
    rng = room.rng
    door_width = rng.uniform(0.81, 0.97)
    door_x = frame.width - door_width / 2 - rng.uniform(0.05, 0.25)
    toilet_x = rng.uniform(0.41, 0.46) if rng.random() < 0.9 else rng.uniform(0.32, 0.6)
    return Layout(frame, door_x, door_width, toilet_x, frame.width >= 2.0)


def _open_floor(room: Room, box: Box) -> bool:
    shape = box.corners()
    return (all(room.shell.inside(corner) for corner in shape) and not room.shell.blocked(shape)
            and not any(gap_between(shape, other.corners()) == 0.0 for other, _ in room.boxes))


def _site_is_open(room: Room, layout: Layout) -> bool:
    outside = Box(*layout.outside(0.75), 1.5, 1.5, layout.frame.direction(0.0))
    return clear(room, _footprint(room, layout.frame), 0.0, -2) and _open_floor(room, outside)


# Building ------------------------------------------------------------------


def _partitions(room: Room, layout: Layout) -> SceneNode:
    f, t = layout.frame, PARTITION_THICKNESS
    near_side, far_side = _sides(room, f)
    low, high = (-t if near_side else 0.0), (f.width + t if far_side else f.width)
    door_low, door_high = layout.door_x - layout.door_width / 2, layout.door_x + layout.door_width / 2
    pieces = [((low + door_low) / 2, f.depth + t / 2, door_low - low, t),
              ((door_high + high) / 2, f.depth + t / 2, high - door_high, t)]
    if near_side:
        pieces.append((-t / 2, (f.depth + t) / 2, t, f.depth + t))
    if far_side:
        pieces.append((f.width + t / 2, (f.depth + t) / 2, t, f.depth + t))
    if f.rear_open:
        pieces.append(((low + high) / 2, -t / 2, high - low, t))
    for x, y, w, d in pieces:
        box = f.box(x, y, w, d)
        room.node(PARTITION, box, kind="wall")
        room.boxes.append((box, -1))
    door = Piece("Restroom door", "door", (layout.door_width, t, 2.1), False)
    return room.node(door, f.box(layout.door_x, f.depth + t / 2, layout.door_width, t), kind="door")


def _mounted(piece: Piece, box: Box, bottom: float) -> Item:
    return Item(piece, box, bottom + piece.size[2] / 2)


def _grab_bars(layout: Layout) -> list[Item]:
    f, g = layout.frame, SNUG
    side = f.box(0.025 + g, 0.30 + SIDE_GRAB_BAR.size[0] / 2, SIDE_GRAB_BAR.size[0], 0.05, 90.0)
    rear = f.box(layout.toilet_x + 0.15, 0.025 + g, REAR_GRAB_BAR.size[0], 0.05, 0.0)
    return [Item(SIDE_GRAB_BAR, side, GRAB_BAR_CENTRE_HEIGHT), Item(REAR_GRAB_BAR, rear, GRAB_BAR_CENTRE_HEIGHT)]


def _basin_spot(layout: Layout) -> tuple[float, float, float]:
    """Where the lavatory's centre sits and which way it faces, in restroom coordinates."""
    f = layout.frame
    if layout.lavatory_on_rear:
        return f.width - 0.35, LAVATORY.size[1] / 2 + SNUG, 90.0
    return f.width - LAVATORY.size[1] / 2 - SNUG, 0.45, 180.0


def _washing(room: Room, layout: Layout) -> list[Item]:
    f, rng = layout.frame, room.rng
    x, y, front = _basin_spot(layout)
    basin = f.facing(LAVATORY, x, y, front, LAVATORY_TOP - LAVATORY.size[2] / 2)
    soap_reach = rng.uniform(0.08, 0.15)
    soap = Piece("Soap dispenser", "dispenser", (0.12, soap_reach, 0.2), False)
    towel_reach = rng.uniform(0.08, 0.25)
    towels = Piece("Paper towel dispenser", "dispenser", (0.3, towel_reach, 0.4), False)
    if layout.lavatory_on_rear:
        soap_at, towel_y = (x, soap_reach / 2 + SNUG), 0.6
    else:
        soap_at, towel_y = (f.width - soap_reach / 2 - SNUG, y), min(1.05, f.depth - 0.3)
    return [basin,
            _lifted(f.facing(soap, *soap_at, front), rng.uniform(1.0, 1.1)),
            _lifted(f.facing(towels, f.width - towel_reach / 2 - SNUG, towel_y, 180.0), rng.uniform(1.0, 1.3))]


def _lifted(item: Item, bottom: float) -> Item:
    return _mounted(item.piece, item.box, bottom)


def _changing_table(room: Room, layout: Layout) -> None:
    f = layout.frame
    right_end = 0.1 + CHANGING_TABLE.size[0]
    if room.rng.random() > 0.3 or right_end > layout.door_x - layout.door_width / 2 - 0.05:
        return
    item = f.facing(CHANGING_TABLE, 0.1 + CHANGING_TABLE.size[0] / 2, f.depth - CHANGING_TABLE.size[1] / 2 - SNUG,
                    -90.0)
    commit(room, [_lifted(item, 0.85)])


# Turning space -------------------------------------------------------------


LocalRect = tuple[float, float, float, float]


def _rect_distance(rect: LocalRect, x: float, y: float) -> float:
    return math.hypot(max(rect[0] - x, 0.0, x - rect[2]), max(rect[1] - y, 0.0, y - rect[3]))


def turning_circle_fits(width: float, depth: float, obstacles: list[LocalRect]) -> bool:
    """Whether a 60 inch circle fits on the floor between the walls and the obstacles."""
    if min(width, depth) < 2 * TURNING_RADIUS:
        return False
    steps_x, steps_y = int((width - 2 * TURNING_RADIUS) / 0.05), int((depth - 2 * TURNING_RADIUS) / 0.05)
    for i in range(steps_x + 1):
        for j in range(steps_y + 1):
            x, y = TURNING_RADIUS + i * 0.05, TURNING_RADIUS + j * 0.05
            if all(_rect_distance(rect, x, y) >= TURNING_RADIUS for rect in obstacles):
                return True
    return False


def _fixture_rects(layout: Layout) -> list[LocalRect]:
    toilet = (layout.toilet_x - 0.2, SNUG, layout.toilet_x + 0.2, SNUG + TOILET.size[1])
    x, y, front = _basin_spot(layout)
    half_w, half_d = (0.25, 0.225) if front == 90.0 else (0.225, 0.25)
    return [toilet, (x - half_w, y - half_d, x + half_w, y + half_d)]


def _overlaps(a: LocalRect, b: LocalRect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clutter_spots(room: Room, layout: Layout, piece: Piece, taken: list[LocalRect]) -> list[LocalRect]:
    f, (w, d) = layout.frame, piece.size[:2]
    spots = []
    for _ in range(30):
        x0 = room.rng.uniform(SNUG, f.width - w - SNUG)
        y0 = room.rng.uniform(SNUG, f.depth - d - SNUG)
        spot = (x0, y0, x0 + w, y0 + d)
        if not any(_overlaps(spot, other) for other in [*taken, layout.door_swing]):
            spots.append(spot)
    return spots


def _wanted(spot: LocalRect, layout: Layout, taken: list[LocalRect], blocking: bool) -> bool:
    f = layout.frame
    return turning_circle_fits(f.width, f.depth, [*taken, spot]) != blocking


def _clutter(room: Room, layout: Layout) -> None:
    f = layout.frame
    taken = _fixture_rects(layout)
    blocking = room.rng.random() < 0.55
    pieces = [CLUTTER[0], *[piece for piece in CLUTTER[1:] if room.rng.random() < 0.3]]
    for piece in pieces:
        spots = _clutter_spots(room, layout, piece, taken)
        spots.sort(key=lambda spot: not _wanted(spot, layout, taken, blocking))
        for spot in spots[:6]:
            box = f.box((spot[0] + spot[2]) / 2, (spot[1] + spot[3]) / 2, piece.size[0], piece.size[1])
            if commit(room, [Item(piece, box)]):
                taken.append(spot)
                break


# Putting it together -------------------------------------------------------


def _restore(room: Room, sizes: tuple[int, int, int]) -> None:
    del room.nodes[sizes[0]:]
    del room.boxes[sizes[1]:]
    del room.reserved[sizes[2]:]


def _furnish(room: Room, layout: Layout, door: SceneNode) -> bool:
    f = layout.frame
    toilet = f.facing(TOILET, layout.toilet_x, TOILET.size[1] / 2 + SNUG, 90.0)
    if not commit(room, [toilet, *_grab_bars(layout), *_washing(room, layout)]):
        return False
    _changing_table(room, layout)
    _clutter(room, layout)
    return True


def _reserve(room: Room, layout: Layout, hub: Point) -> None:
    room.reserved.append(_footprint(room, layout.frame))
    room.interiors.append(_footprint(room, layout.frame))
    room.reserved.append(Box(*layout.outside(0.75), 1.5, 1.5, layout.frame.direction(0.0)))
    corridor(room, hub, layout.outside(0.9), room.rng.uniform(1.0, 1.3))


def add_restroom(room: Room, hub: Point) -> Stop | None:
    """Try a drawn size, then a compact one, at every corner and along every wall long enough."""
    sizes = [_drawn_size(room), (room.rng.uniform(1.5, 1.8), 1.75)]
    for frame in (f for width, depth in sizes for f in _candidate_frames(room, width, depth)):
        stop = _build_at(room, _layout(room, frame), hub)
        if stop is not None:
            return stop
    return None


def _drawn_size(room: Room) -> tuple[float, float]:
    """Mostly a room the fixtures leave a turning circle in, sometimes one too tight for any."""
    if room.rng.random() < TIGHT_SHARE:
        return room.rng.uniform(1.5, 2.6), room.rng.uniform(1.7, 2.8)
    return room.rng.uniform(1.9, 2.6), room.rng.uniform(2.2, 2.8)


def _build_at(room: Room, layout: Layout, hub: Point) -> Stop | None:
    if not _site_is_open(room, layout):
        return None
    sizes = (len(room.nodes), len(room.boxes), len(room.reserved))
    door = _partitions(room, layout)
    if not _furnish(room, layout, door):
        _restore(room, sizes)
        return None
    _reserve(room, layout, hub)
    spot = layout.outside(0.6)
    return Stop(name="Restroom", position=Vec3(x=spot[0], y=spot[1], z=0.0), anchor_node_id=door.id)

