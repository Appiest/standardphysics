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
from standardphysics_contracts import Scenario, SceneGraph, SceneNode, Stop, Vec3, stands_upright
from standardphysics_pipeline import build_grid, footprint, sleeping_places
from standardphysics_pipeline.footprints import rotation_about_z
from standardphysics_pipeline.occupancy import reads_as_wall

from standardphysics_agents.checks import roles
from standardphysics_agents.checks.roles import RoomKind, room_kind

STANDING_ROOM = 0.45
"""Metres of clear floor around a stop, about half a wheelchair's turning space."""

APART = 1.0
"""Metres between two different stops, so no marker hides another."""

INSET = 0.8
"""How far a stop sits inside the room from a wall or door."""

STAND_OFF = 0.5
"""Metres out from a counter's customer side where someone stands to order or collect."""

HANDOFF_WORDS = ("pickup", "pick-up", "handoff", "hand-off", "to-go")
"""Words that make a service counter the place orders are collected from rather than placed."""

ROUTE_NAMES: dict[RoomKind, str] = {
    "service": "Order a drink",
    "home": "Get around the room",
    "general": "Walk through the room",
}

Bounds = tuple[float, float, float, float]
Point = tuple[float, float]
Axis = tuple[float, float]
Side = tuple[Point, Axis]
"""A point and a direction: the half of the floor in front of a counter."""


def outline_points(graph: SceneGraph) -> list[tuple[float, float]]:
    """The room's walls on the floor, or everything when no wall is thin enough to read as one."""
    walls = [point for node in graph.nodes if stands_upright(node) for point in footprint(node)]
    return walls or [point for node in graph.nodes for point in footprint(node)]


def _room_bounds(graph: SceneGraph) -> Bounds:
    points = outline_points(graph)
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
    """Half a metre out from the face of the node that looks toward a point.

    A node exactly at the reference point has no direction toward it, and a
    min over the empty list is a crash pretending to be a suggestion. The
    forward face (the node's local minus-Y, the face the pipeline treats as
    the customer side) is the least-invented face to stand beside when nothing
    points at the node.
    """
    p = node.transform.position
    dx, dy = toward[0] - p.x, toward[1] - p.y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        cos_t, sin_t = rotation_about_z(node)
        return _toward(
            (p.x, p.y),
            (p.x + sin_t, p.y - cos_t),
            node.dimensions.y / 2 + 0.5,
        )
    angle = math.atan2(node.transform.m[4], node.transform.m[0])
    local_x = abs((dx * math.cos(angle) + dy * math.sin(angle)) / length)
    local_y = abs((-dx * math.sin(angle) + dy * math.cos(angle)) / length)
    halves = ((node.dimensions.x / 2, local_x), (node.dimensions.y / 2, local_y))
    exits = [half / part for half, part in halves if part > 1e-9]
    return _toward((p.x, p.y), toward, min(exits) + 0.5)


def counter_axes(counter: SceneNode) -> tuple[Axis, Axis, float, float]:
    """Along the counter, across it, its depth across, and half its length."""
    cos_t, sin_t = rotation_about_z(counter)
    along, across = (cos_t, sin_t), (-sin_t, cos_t)
    if counter.dimensions.x >= counter.dimensions.y:
        return along, across, counter.dimensions.y, counter.dimensions.x / 2
    return across, along, counter.dimensions.x, counter.dimensions.y / 2


def customer_side(counter: SceneNode, door: Point) -> Axis:
    """Which way a counter's customers stand from it: off its long side facing the front door.

    Staff work on the other side, which is where `staff_areas` puts the kitchen.
    """
    _, across, _, _ = counter_axes(counter)
    centre = counter.transform.position
    toward_door = across[0] * (door[0] - centre.x) + across[1] * (door[1] - centre.y)
    return across if toward_door >= 0 else (-across[0], -across[1])


def _stand_at(counter: SceneNode, front: Axis, along_by: float = 0.0) -> Point:
    """Where a customer stands at a counter, `along_by` metres along it from the middle."""
    along, _, depth, _ = counter_axes(counter)
    centre = counter.transform.position
    out = depth / 2 + STAND_OFF
    return (
        centre.x + front[0] * out + along[0] * along_by,
        centre.y + front[1] * out + along[1] * along_by,
    )


def _hands_off(node: SceneNode) -> bool:
    return any(word in node.label.casefold() for word in HANDOFF_WORDS)


def _service_counters(graph: SceneGraph) -> list[SceneNode]:
    """The counters the checks serve customers at, or anything labelled a counter when none is."""
    return roles.service_counters(graph) or [node for node in graph.nodes if "counter" in node.label.lower()]


def _ordering_counter(graph: SceneGraph) -> SceneNode | None:
    counters = _service_counters(graph)
    return _largest([node for node in counters if not _hands_off(node)] or counters)


def _pickup_counter(graph: SceneGraph) -> SceneNode | None:
    return _largest([node for node in _service_counters(graph) if _hands_off(node)])


def _toward_the_room(counter: SceneNode, centre: Point) -> float:
    """Which end of the counter faces the middle of the room, as +1 or -1 along it."""
    along, _, _, _ = counter_axes(counter)
    p = counter.transform.position
    return 1.0 if along[0] * (centre[0] - p.x) + along[1] * (centre[1] - p.y) >= 0 else -1.0


def _tables(graph: SceneGraph) -> list[SceneNode]:
    return [node for node in graph.nodes if node.raw_category == "table"]


class _OpenFloor:
    """Snaps a point to the closest cell inside the room with standing room."""

    def __init__(self, graph: SceneGraph):
        self.graph = graph
        self.grid = build_grid(graph)
        clearance = ndimage.distance_transform_edt(~self.grid.occupied) * self.grid.cell_size
        cell_indices = np.indices(self.grid.occupied.shape)
        rows, cols = cell_indices[0], cell_indices[1]
        xs = self.grid.origin_x + (cols + 0.5) * self.grid.cell_size
        ys = self.grid.origin_y + (rows + 0.5) * self.grid.cell_size
        inside = _inside_hull(_convex_hull(outline_points(graph)), xs, ys)
        if self.grid.indoors is not None:
            inside &= self.grid.indoors
        self.xs, self.ys = xs, ys
        self.roomy = inside & (clearance >= STANDING_ROOM)
        self.open = inside & (clearance > 0)

    def walled_off(self, point: Point) -> bool:
        """Off the floor, behind a wall or inside one: floor that moving furniture could never free."""
        row, col = self.grid.to_cell(*point)
        if not self.grid.contains(row, col) or (self.grid.indoors is not None and not self.grid.indoors[row, col]):
            return True
        owner = self.grid.owner_at(row, col)
        return owner is not None and reads_as_wall(self.graph.by_id(owner))

    def snap(self, point: Point, taken: list[Vec3], keep_to: Side | None = None) -> Vec3:
        """The nearest cell with standing room, clear of the stops already placed.

        `keep_to` holds the stop in front of a counter, so a crowded customer
        side does not send it round to the staff side, or across the shop.
        """
        distance = np.hypot(self.xs - point[0], self.ys - point[1])
        cost = np.where(self.roomy, distance, np.where(self.open, distance + 1.0, np.inf))
        if keep_to is not None:
            (origin_x, origin_y), (normal_x, normal_y) = keep_to
            in_front = (self.xs - origin_x) * normal_x + (self.ys - origin_y) * normal_y > 0
            cost = np.where(in_front, cost, np.inf)
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

    @property
    def door(self) -> Point:
        """Where customers come in: the entrance once it is placed, the middle of the room before."""
        return (self.placed[0].x, self.placed[0].y) if self.placed else self.centre

    def stop(self, name: str, at: Point, anchor: SceneNode | None, keep_to: Side | None = None) -> Stop:
        position = self.floor.snap(at, self.placed, keep_to)
        self.placed.append(position)
        return Stop(name=name, position=position, anchor_node_id=anchor.id if anchor else None)


def _entrance(room: _Room) -> Stop:
    door = _largest([node for node in room.graph.nodes if node.kind in ("door", "opening")])
    if door is None:
        return room.stop("Entrance", (room.centre[0], room.bounds[1] + INSET), None)
    p = door.transform.position
    return room.stop("Entrance", _toward((p.x, p.y), room.centre, INSET), door)


def _front(room: _Room, counter: SceneNode) -> Axis:
    """The counter's customer side, unless that side stands against a wall and the other does not.

    Furniture crowding the customer side does not flip it: that is a finding
    to report, and the staff side is still not where anyone orders.
    """
    front = customer_side(counter, room.door)
    back = (-front[0], -front[1])
    if room.floor.walled_off(_stand_at(counter, front)) and not room.floor.walled_off(_stand_at(counter, back)):
        return back
    return front


def _at_counter(room: _Room, name: str, counter: SceneNode, along_by: float = 0.0) -> Stop:
    front = _front(room, counter)
    centre = counter.transform.position
    return room.stop(name, _stand_at(counter, front, along_by), counter, ((centre.x, centre.y), front))


def _counter_stop(room: _Room) -> Stop:
    """In front of the counter people order at, on the side customers stand."""
    counter = _ordering_counter(room.graph)
    if counter is None:
        return room.stop("Counter", (room.centre[0], room.bounds[3] - INSET), None)
    return _at_counter(room, "Counter", counter)


def _pickup_stop(room: _Room) -> Stop:
    """At the pickup counter when the scan names one, otherwise a step along the ordering counter."""
    handoff = _pickup_counter(room.graph)
    if handoff is not None:
        return _at_counter(room, "Pickup", handoff)
    counter = _ordering_counter(room.graph)
    if counter is None:
        return room.stop("Pickup", (room.centre[0] + APART, room.bounds[3] - INSET), None)
    return _at_counter(room, "Pickup", counter, _toward_the_room(counter, room.centre) * APART)


def _service_stops(room: _Room) -> list[Stop]:
    table = _nearest(_tables(room.graph), room.centre)
    seat_at = _beside(table, room.centre) if table else (room.bounds[0] + INSET, room.centre[1])
    return [_counter_stop(room), _pickup_stop(room), room.stop("Seat", seat_at, table)]


def _home_stops(room: _Room) -> list[Stop]:
    bed = _largest(sleeping_places(room.graph))
    if bed is None:
        return _general_stops(room)
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


DESTINATIONS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "seating": ("Seats", ("seat", "chair", "bench"), ("table", "sofa", "chair")),
    "restroom": ("Restroom", ("restroom", "bathroom", "toilet", "washroom"), ("toilet",)),
    "fitting_room": ("Fitting room", ("fitting",), ()),
    "shelves": ("Shelves", ("shelf", "shelves", "rack"), ("storage",)),
}
"""Each place a customer might go: the stop's name, and the words or RoomPlan
categories that find the thing it stands beside. The name is what the copy
says, as "the restroom" or "the shelves"."""


def _far_corners(bounds, entrance: tuple[float, float]) -> list[tuple[float, float]]:
    """Room corners, furthest from the front door first, for places the scan didn't name."""
    corners = [
        (bounds[0] + INSET, bounds[1] + INSET), (bounds[2] - INSET, bounds[1] + INSET),
        (bounds[0] + INSET, bounds[3] - INSET), (bounds[2] - INSET, bounds[3] - INSET),
    ]
    return sorted(corners, key=lambda corner: -math.hypot(corner[0] - entrance[0], corner[1] - entrance[1]))


def _destination(
    graph: SceneGraph, destination: str, centre, fallback
) -> tuple[str, tuple[float, float], SceneNode | None]:
    name, words, categories = DESTINATIONS[destination]
    matches = [
        node for node in graph.nodes
        if node.raw_category in categories or any(word in node.label.lower() for word in words)
    ]
    node = _nearest(matches, centre)
    return name, (_beside(node, centre) if node else fallback), node


def suggest_path(graph: SceneGraph, destinations: list[str]) -> Scenario:
    """In the front door, to the counter, to each place the owner picked, and out again."""
    room = _Room(graph)
    entrance = _entrance(room)
    corners = _far_corners(room.bounds, (entrance.position.x, entrance.position.y))
    stops = [entrance, _counter_stop(room)]
    for index, destination in enumerate(destinations):
        if destination == "pickup":
            stops.append(_pickup_stop(room))
            continue
        name, at, anchor = _destination(graph, destination, room.centre, corners[index % len(corners)])
        stops.append(room.stop(name, at, anchor))
    exit_stop = entrance.model_copy(update={"name": "Exit"})
    return Scenario(name="Customer path", stops=[*stops, exit_stop])
