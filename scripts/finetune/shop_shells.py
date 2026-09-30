"""Empty rooms to furnish: generated rectangles, or real scans stripped of their furniture.

A rect shell is one to three rectangles joined into a plain, L, T or long narrow
plan. A scan shell is a real RoomPlan export turned so its floor is square to
the axes, cut to the part its walls enclose (or to a 10 to 14 metre window of a
large one), and emptied of everything movable, so the generator refurnishes a
room somebody really measured. Both emit exactly one convex floor node first,
because the hard-constraint checker reads only the first floor and treats it
as convex.
"""

from __future__ import annotations

import functools
import json
import math
import os
import pathlib
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from shop_geometry import Box, Point, Rect, Wall, clip_segment, outline, turned
from shop_room import (
    NAMESPACE,
    WALL_HEIGHT,
    WALL_THICKNESS,
    Entrance,
    Piece,
    Room,
    Unbuildable,
    add_door,
    node_box,
)
from standardphysics_agents.checks.roles import ENTRANCE_LABELS
from standardphysics_agents.training.quality import wall_segments
from standardphysics_contracts import Mat4, SceneGraph, SceneNode
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import Polygon, contains_point, floor_polygon
from standardphysics_pipeline.occupancy import blocks_floor

SCAN_EXPORTS = pathlib.Path(os.environ.get(
    "SP_SCAN_EXPORTS", pathlib.Path(__file__).resolve().parents[2].parent / "sp-data/real/exports"))
SHELL_KINDS = frozenset({"wall", "door", "window", "opening"})
LARGEST_WHOLE_SCAN = 160.0
"""Square metres. A scan bigger than this is furnished one 10 to 14 metre window at a time."""
SMALLEST_SCAN_ROOM = 14.0


def _floor_node(room: Room, region: Rect) -> None:
    box = Box((region.x0 + region.x1) / 2, (region.y0 + region.y1) / 2, region.x1 - region.x0, region.y1 - region.y0)
    room.node(Piece("Floor", "floor", (box.w, box.d, 0.01), False), box, z=0.0, kind="floor")


# Rectangles ----------------------------------------------------------------


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


@dataclass
class RectShell:
    rects: list[Rect]
    walls: list[Wall] = field(init=False)
    synthetic: bool = True

    def __post_init__(self) -> None:
        self.walls = outline(self.rects)

    @classmethod
    def draw(cls, rng: random.Random, size: tuple[float, float, float, float]) -> RectShell:
        plan = rng.choices([p for p, _ in PLANS], weights=[w for _, w in PLANS])[0]
        return cls(plan(rng, rng.uniform(*size[:2]), rng.uniform(*size[2:])))

    @property
    def area(self) -> float:
        return sum(rect.area for rect in self.rects)

    def inside(self, point: Point) -> bool:
        return any(contains_point(rect.polygon(), point, 0.005) for rect in self.rects)

    def inside_main(self, point: Point) -> bool:
        return self.rects[0].inside(*point)

    def sample_point(self, rng: random.Random) -> Point:
        rect = rng.choices(self.rects, weights=[r.area for r in self.rects])[0]
        return rng.uniform(rect.x0 + 0.6, rect.x1 - 0.6), rng.uniform(rect.y0 + 0.6, rect.y1 - 0.6)

    def blocked(self, shape: Polygon) -> bool:
        return False

    def column_bay(self) -> Rect | None:
        return self.rects[0] if self.rects[0].area >= 55 else None

    def open_edges(self) -> list[Wall]:
        return []

    def wing_centres(self) -> list[Point]:
        return [((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) for r in self.rects[1:]]

    def _front_wall(self) -> Wall:
        main = self.rects[0]
        fronts = [w for w in self.walls if w.a[1] == main.y0 and w.b[1] == main.y0 and w.length > 2.2]
        if not fronts:
            raise Unbuildable("the front wall is too short for a door")
        return fronts[0]

    def build(self, room: Room) -> Entrance:
        x0, y0 = min(r.x0 for r in self.rects), min(r.y0 for r in self.rects)
        x1, y1 = max(r.x1 for r in self.rects), max(r.y1 for r in self.rects)
        _floor_node(room, Rect(x0, y0, x1, y1))
        for wall in self.walls:
            cx, cy = wall.point(wall.length / 2, -WALL_THICKNESS / 2)
            box = Box(cx, cy, wall.length + WALL_THICKNESS, WALL_THICKNESS, wall.heading)
            room.node(Piece("Wall", "wall", (0, 0, WALL_HEIGHT), False), box, kind="wall")
        return add_door(room, self._front_wall(), "Front door")


# Real scans ----------------------------------------------------------------


@dataclass(frozen=True)
class Scan:
    name: str
    graph: SceneGraph


@functools.lru_cache(maxsize=1)
def available_scans() -> tuple[Scan, ...]:
    """Every real export with a scene graph, in file-name order so draws stay reproducible."""
    if not SCAN_EXPORTS.is_dir():
        return ()
    scans = []
    for path in sorted(SCAN_EXPORTS.glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("latest"):
            scans.append(Scan(data.get("name", path.stem), SceneGraph.model_validate(data["latest"])))
    return tuple(scans)


def _floor_heading(floor: SceneNode) -> float:
    hull = floor_polygon(floor)
    a, b = max(zip(hull, [*hull[1:], hull[0]], strict=True), key=lambda pair: math.dist(*pair))
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _moved(node: SceneNode, turn: float, shift: tuple[float, float, float]) -> SceneNode:
    return node.model_copy(update={"transform": Mat4(m=turned(node.transform.m, turn, shift))})


def _bounds(points: list[Point]) -> Rect:
    return Rect(min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points),
                max(p[1] for p in points))


def _enclosed_region(floor: SceneNode, walls: list[SceneNode]) -> Rect | None:
    """The floor cut back to the box its nearby walls span; a scan's floor often overhangs its walls."""
    floor_rect = _bounds(floor_polygon(floor))
    grown = Rect(floor_rect.x0 - 0.3, floor_rect.y0 - 0.3, floor_rect.x1 + 0.3, floor_rect.y1 + 0.3)
    near = [w for w in walls if grown.inside(w.transform.m[3], w.transform.m[7])]
    if len(near) < 3:
        return None
    return floor_rect.intersect(_bounds([p for w in near for p in footprint(w)]))


@functools.lru_cache(maxsize=1)
def usable_floors() -> tuple[tuple[str, tuple[SceneNode, ...], Rect], ...]:
    """Every scan floor with enough walled area, with the scan turned square to that floor and levelled on it."""
    found = []
    for scan in available_scans():
        for floor in (n for n in scan.graph.nodes if n.kind == "floor"):
            turn, level = -_floor_heading(floor), (0.0, 0.0, -floor.transform.m[11])
            nodes = tuple(_moved(n, turn, level) for n in scan.graph.nodes)
            region = _enclosed_region(_moved(floor, turn, level), [n for n in nodes if n.kind == "wall"])
            if region is not None and region.area >= SMALLEST_SCAN_ROOM:
                found.append((scan.name, nodes, region))
    return tuple(found)


def _window(rng: random.Random, region: Rect) -> Rect:
    if region.area <= LARGEST_WHOLE_SCAN:
        return region
    side = rng.uniform(10.0, 14.0)
    w, d = min(side, region.x1 - region.x0), min(side, region.y1 - region.y0)
    x0, y0 = rng.uniform(region.x0, region.x1 - w), rng.uniform(region.y0, region.y1 - d)
    return Rect(x0, y0, x0 + w, y0 + d)


@dataclass
class ScanShell:
    scan_name: str
    region: Rect
    kept: list[SceneNode]
    """The scan's walls, doors, windows, openings and fixed objects, already in the room frame."""
    walls: list[Wall] = field(init=False)
    obstacles: list[Polygon] = field(init=False)
    synthetic: bool = False

    def __post_init__(self) -> None:
        wall_nodes = [n for n in self.kept if n.kind == "wall"]
        segments = wall_segments(SceneGraph(scan_id=uuid.uuid4(), nodes=wall_nodes)) if wall_nodes else []
        self.walls = [face for a, b in segments for face in self._faces(a, b)]
        self.obstacles = [_thin(a, b) for a, b in segments]

    @classmethod
    def draw(cls, rng: random.Random) -> ScanShell:
        floors = usable_floors()
        if not floors:
            raise Unbuildable("no usable scans on this machine")
        scan, nodes, region = rng.choice(floors)
        region = _window(rng, region)
        shift = (-region.x0, -region.y0, 0.0)
        kept = [_moved(n, 0.0, shift) for n in nodes if _belongs(n, region)]
        return cls(scan, Rect(0.0, 0.0, region.x1 - region.x0, region.y1 - region.y0), kept)

    def _faces(self, a: Point, b: Point) -> list[Wall]:
        clipped = clip_segment(a, b, self.region)
        if clipped is None or math.dist(*clipped) < 0.5:
            return []
        face = Wall(*clipped)
        left, right = face.point(face.length / 2, 0.2), face.point(face.length / 2, -0.2)
        sides = [(self.inside(left), face), (self.inside(right), face.reversed())]
        return [wall for open_side, wall in sides if open_side]

    @property
    def area(self) -> float:
        return self.region.area

    def inside(self, point: Point) -> bool:
        return contains_point(self.region.polygon(), point, 0.005)

    def inside_main(self, point: Point) -> bool:
        return self.region.inside(*point)

    def sample_point(self, rng: random.Random) -> Point:
        return rng.uniform(self.region.x0 + 0.6, self.region.x1 - 0.6), rng.uniform(self.region.y0 + 0.6,
                                                                                     self.region.y1 - 0.6)

    def blocked(self, shape: Polygon) -> bool:
        return any(gap_between(shape, wall) == 0.0 for wall in self.obstacles)

    def column_bay(self) -> Rect | None:
        return None

    def open_edges(self) -> list[Wall]:
        corners = self.region.polygon()
        edges = [Wall(corners[i], corners[(i + 1) % 4]) for i in range(4)]
        return [edge for edge in edges if not any(_along(face, edge) for face in self.walls)]

    def wing_centres(self) -> list[Point]:
        return []

    def build(self, room: Room) -> Entrance:
        _floor_node(room, self.region)
        adopted = _adopt(room, self.kept)
        for node in adopted:
            if node.kind not in SHELL_KINDS and blocks_floor(node):
                room.boxes.append((node_box(node), -1))
        doors = [node for node in adopted if node.kind == "door" and self.inside(node_box(node).centre)]
        for door in doors:
            self._keep_door_clear(room, door)
        return self._entrance(room, doors)

    def _door_sides(self, door: SceneNode) -> list[tuple[Point, float]]:
        box = node_box(door)
        normals = (box.heading + 90.0, box.heading - 90.0)
        points = [((box.cx + 0.9 * math.cos(math.radians(n)), box.cy + 0.9 * math.sin(math.radians(n))), n)
                  for n in normals]
        return [(point, normal) for point, normal in points if self.inside(point)]

    def _keep_door_clear(self, room: Room, door: SceneNode) -> None:
        for point, normal in self._door_sides(door):
            room.reserved.append(Box(*point, door.dimensions.x + 0.8, 1.8, normal - 90.0))

    def _entrance(self, room: Room, doors: list[SceneNode]) -> Entrance:
        usable = [door for door in doors if self._door_sides(door)]
        labelled = [door for door in usable if door.label.strip().casefold() in ENTRANCE_LABELS]
        chosen = (labelled or usable or [None])[0]
        if chosen is None:
            return self._new_entrance(room)
        index = room.nodes.index(chosen)
        room.nodes[index] = chosen.model_copy(update={"label": "Front door"})
        inside, _ = self._door_sides(chosen)[0]
        wall = min(self.walls, key=lambda face: face.distance_to(node_box(chosen).centre), default=None)
        if wall is None or wall.distance_to(node_box(chosen).centre) > 0.3:
            return Entrance(room.nodes[index], inside, None, 0.0)
        return Entrance(room.nodes[index], inside, wall, math.dist(wall.a, _foot(wall, node_box(chosen).centre)))

    def _new_entrance(self, room: Room) -> Entrance:
        long_enough = [w for w in [*self.walls, *self.open_edges()] if w.length > 2.4]
        outer = [w for w in long_enough if not self.inside(w.point(w.length / 2, -0.2))] or long_enough
        if not outer:
            raise Unbuildable(f"{self.scan_name} has no door and no outside wall to put one in")
        return add_door(room, room.rng.choice(outer), "Front door")


def _along(face: Wall, edge: Wall) -> bool:
    """Whether a wall face runs along most of a region edge."""
    same_way = abs((face.heading - edge.heading + 180.0) % 360.0 - 180.0) < 10.0
    close = edge.distance_to(face.point(face.length / 2)) < 0.3
    return same_way and close and face.length > edge.length / 2


def _foot(wall: Wall, point: Point) -> Point:
    ux, uy = (wall.b[0] - wall.a[0]) / wall.length, (wall.b[1] - wall.a[1]) / wall.length
    t = (point[0] - wall.a[0]) * ux + (point[1] - wall.a[1]) * uy
    return wall.a[0] + ux * t, wall.a[1] + uy * t


def _thin(a: Point, b: Point) -> Polygon:
    length = math.dist(a, b)
    heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    return Box((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, length, 0.02, heading).corners()


def _belongs(node: SceneNode, region: Rect) -> bool:
    """Walls and openings that reach into the region, and fixed objects standing in it."""
    if node.kind == "floor" or node.movable:
        return False
    if node.kind in SHELL_KINDS:
        return gap_between(footprint(node), region.polygon()) == 0.0
    return region.inside(node.transform.m[3], node.transform.m[7])


def _adopt(room: Room, kept: list[SceneNode]) -> list[SceneNode]:
    """Copy the scan's nodes into the room under ids of this room's own, keeping parent links that survive."""
    ids = {node.id: uuid.uuid5(NAMESPACE, f"{room.name}:scan:{node.id}") for node in kept}
    adopted = [node.model_copy(update={
        "id": ids[node.id], "parent_id": ids.get(node.parent_id), "attachment": None, "appearance": None,
        "reconstruction": None, "measured_position": None,
        "relation": node.relation if node.parent_id in ids else None,
    }) for node in kept]
    room.nodes.extend(adopted)
    return adopted
