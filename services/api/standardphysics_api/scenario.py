"""A suggested route for a scan that has none.

RoomPlan names walls, doors, tables and storage, but nothing tells us where a
person orders, sleeps or sits. This proposes stops that fit the kind of room
the scan is, on open floor with room to stand, and the owner moves the markers
before any path is checked. A suggestion is never assessed on its own.

A shop gets the ordering route. A home or dorm room gets the bed and the desk,
and a general room gets a seat or the middle of the floor. Nothing gets a
counter the scan does not contain.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np
from scipy import ndimage
from standardphysics_agents.checks.roles import RoomKind, room_kind
from standardphysics_contracts import Scenario, SceneGraph, SceneNode, Stop, Vec3
from standardphysics_pipeline import build_grid, footprint, sleeping_places

STANDING_ROOM = 0.45
"""Metres of clear floor around a stop, about half a wheelchair's turning space."""

APART = 1.0
"""Metres between two different stops, so no marker hides another."""

INSET = 0.8
"""How far a stop sits inside the room from a wall or door."""

ROUTE_NAMES: dict[RoomKind, str] = {
    "service": "Order a drink",
    "home": "Get around the room",
    "general": "Walk through the room",
}

Bounds = tuple[float, float, float, float]
Point = tuple[float, float]


def _outline_points(graph: SceneGraph) -> list[tuple[float, float]]:
    walls = [point for node in graph.nodes if node.kind == "wall" for point in footprint(node)]
    return walls or [point for node in graph.nodes for point in footprint(node)]


def _room_bounds(graph: SceneGraph) -> Bounds:
    points = _outline_points(graph)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Counter-clockwise hull, by the monotone chain method."""
    ordered = sorted(set(points))

    def turn(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def chain(sequence):
        hull: list[tuple[float, float]] = []
        for point in sequence:
            while len(hull) >= 2 and turn(hull[-2], hull[-1], point) <= 0:
                hull.pop()
            hull.append(point)
        return hull[:-1]

    return chain(ordered) + chain(reversed(ordered))


def _inside_hull(hull: list[tuple[float, float]], xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    inside = np.ones(xs.shape, dtype=bool)
    for (ax, ay), (bx, by) in zip(hull, hull[1:] + hull[:1]):
        inside &= (bx - ax) * (ys - ay) - (by - ay) * (xs - ax) > 0
    return inside


def _centre(bounds: Bounds) -> Point:
    return (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2


def _toward(origin: Point, target: Point, distance: float) -> Point:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length = math.hypot(dx, dy) or 1.0
    return origin[0] + dx / length * distance, origin[1] + dy / length * distance


def _largest(nodes: list[SceneNode]) -> SceneNode | None:
    return max(nodes, key=lambda node: node.dimensions.x * node.dimensions.y, default=None)


def _nearest(nodes: list[SceneNode], point: Point) -> SceneNode | None:
    def distance(node: SceneNode) -> float:
        p = node.transform.position
        return math.hypot(p.x - point[0], p.y - point[1])

    return min(nodes, key=distance, default=None)


def _beside(node: SceneNode, toward: Point) -> Point:
    """Half a metre out from the face of the node that looks toward a point."""
    p = node.transform.position
    dx, dy = toward[0] - p.x, toward[1] - p.y
    length = math.hypot(dx, dy) or 1.0
    angle = math.atan2(node.transform.m[4], node.transform.m[0])
    local_x = abs((dx * math.cos(angle) + dy * math.sin(angle)) / length)
    local_y = abs((-dx * math.sin(angle) + dy * math.cos(angle)) / length)
    halves = ((node.dimensions.x / 2, local_x), (node.dimensions.y / 2, local_y))
    exits = [half / part for half, part in halves if part > 1e-9]
    return _toward((p.x, p.y), toward, min(exits) + 0.5)


def _tables(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if node.raw_category == "table"]


class _OpenFloor:
    """Snaps a point to the closest cell inside the room with standing room."""

    def __init__(self, graph: SceneGraph):
        self.grid = build_grid(graph)
        clearance = ndimage.distance_transform_edt(~self.grid.occupied) * self.grid.cell_size
        rows, cols = np.indices(self.grid.occupied.shape)
        xs = self.grid.origin_x + (cols + 0.5) * self.grid.cell_size
        ys = self.grid.origin_y + (rows + 0.5) * self.grid.cell_size
        inside = _inside_hull(_convex_hull(_outline_points(graph)), xs, ys)
        self.xs, self.ys = xs, ys
        self.roomy = inside & (clearance >= STANDING_ROOM)
        self.open = inside & (clearance > 0)

    def snap(self, point: Point, taken: list[Vec3]) -> Vec3:
        """The nearest cell with standing room, clear of the stops already placed."""
        distance = np.hypot(self.xs - point[0], self.ys - point[1])
        cost = np.where(self.roomy, distance, np.where(self.open, distance + 1.0, np.inf))
        for other in taken:
            cost = np.where(np.hypot(self.xs - other.x, self.ys - other.y) < APART, np.inf, cost)
        if np.isinf(cost).all():
            cost = np.where(self.open, distance, np.inf)
        distance = cost
        row, col = np.unravel_index(int(np.argmin(distance)), distance.shape)
        return Vec3(x=float(self.xs[row, col]), y=float(self.ys[row, col]), z=0.0)


class _Room:
    """One room being given stops, each placed clear of the ones before it."""

    def __init__(self, graph: SceneGraph):
        self.graph = graph
        self.bounds = _room_bounds(graph)
        self.centre = _centre(self.bounds)
        self.floor = _OpenFloor(graph)
        self.placed: list[Vec3] = []

    def stop(self, name: str, at: Point, anchor: SceneNode | None) -> Stop:
        position = self.floor.snap(at, self.placed)
        self.placed.append(position)
        return Stop(name=name, position=position, anchor_node_id=anchor.id if anchor else None)


def _entrance(room: _Room) -> Stop:
    door = _largest([node for node in room.graph.nodes if node.kind in ("door", "opening")])
    if door is None:
        return room.stop("Entrance", (room.centre[0], room.bounds[1] + INSET), None)
    p = door.transform.position
    return room.stop("Entrance", _toward((p.x, p.y), room.centre, INSET), door)


def _counter(room: _Room) -> tuple[Point, SceneNode | None]:
    named = [node for node in room.graph.nodes if "counter" in node.label.lower()]
    counter = _largest(named)
    if counter is not None:
        return _beside(counter, room.centre), counter
    return (room.centre[0], room.bounds[3] - INSET), None


def _service_stops(room: _Room) -> list[Stop]:
    counter_at, counter = _counter(room)
    table = _nearest(_tables(room.graph), room.centre)
    seat_at = _beside(table, room.centre) if table else (room.bounds[0] + INSET, room.centre[1])
    return [
        room.stop("Counter", counter_at, counter),
        room.stop("Pickup", (counter_at[0] + 1.0, counter_at[1]), counter),
        room.stop("Seat", seat_at, table),
    ]


def _home_stops(room: _Room) -> list[Stop]:
    bed = _largest(sleeping_places(room.graph))
    stops = [room.stop("Bedside", _beside(bed, room.centre), bed)]
    desk = _nearest(_tables(room.graph), room.centre)
    if desk is not None:
        stops.append(room.stop("Desk", _beside(desk, room.centre), desk))
    return stops


def _general_stops(room: _Room) -> list[Stop]:
    table = _nearest(_tables(room.graph), room.centre)
    if table is None:
        return [room.stop("Middle of the room", room.centre, None)]
    return [room.stop("Seat", _beside(table, room.centre), table)]


STOPS: dict[RoomKind, Callable[[_Room], list[Stop]]] = {
    "service": _service_stops,
    "home": _home_stops,
    "general": _general_stops,
}


def suggest_scenario(graph: SceneGraph) -> Scenario:
    kind = room_kind(graph)
    room = _Room(graph)
    entrance = _entrance(room)
    stops = [entrance, *STOPS[kind](room)]
    exit_stop = entrance.model_copy(update={"name": "Exit"})
    return Scenario(name=ROUTE_NAMES[kind], stops=[*stops, exit_stop])
