"""A suggested customer route for a scan that has none.

RoomPlan names walls, doors, tables and storage, but nothing tells us where a
customer orders or sits. This proposes one stop of each kind from what the scan
does contain, on open floor with room to stand, and the owner moves the markers
before any path is checked. A suggestion is never assessed on its own.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from standardphysics_contracts import Scenario, SceneGraph, SceneNode, Stop, Vec3, stands_upright
from standardphysics_pipeline import build_grid, footprint
from standardphysics_pipeline.footprints import rotation_about_z

STANDING_ROOM = 0.45
"""Metres of clear floor around a stop, about half a wheelchair's turning space."""

APART = 1.0
"""Metres between two different stops, so no marker hides another."""

INSET = 0.8
"""How far a stop sits inside the room from a wall or door."""


def _outline_points(graph: SceneGraph) -> list[tuple[float, float]]:
    walls = [point for node in graph.nodes if stands_upright(node) for point in footprint(node)]
    return walls or [point for node in graph.nodes for point in footprint(node)]


def _room_bounds(graph: SceneGraph) -> tuple[float, float, float, float]:
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


def _centre(bounds: tuple[float, float, float, float]) -> tuple[float, float]:
    return (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2


def _toward(origin: tuple[float, float], target: tuple[float, float], distance: float) -> tuple[float, float]:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length = math.hypot(dx, dy) or 1.0
    return origin[0] + dx / length * distance, origin[1] + dy / length * distance


def _largest(nodes: list[SceneNode]) -> SceneNode | None:
    return max(nodes, key=lambda node: node.dimensions.x * node.dimensions.y, default=None)


def _nearest(nodes: list[SceneNode], point: tuple[float, float]) -> SceneNode | None:
    def distance(node: SceneNode) -> float:
        p = node.transform.position
        return math.hypot(p.x - point[0], p.y - point[1])

    return min(nodes, key=distance, default=None)


def _beside(node: SceneNode, toward: tuple[float, float]) -> tuple[float, float]:
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

    def snap(self, point: tuple[float, float], taken: list[Vec3]) -> Vec3:
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


def _entrance(graph: SceneGraph, bounds, centre) -> tuple[tuple[float, float], SceneNode | None]:
    door = _largest([node for node in graph.nodes if node.kind in ("door", "opening")])
    if door is not None:
        p = door.transform.position
        return _toward((p.x, p.y), centre, INSET), door
    return (centre[0], bounds[1] + INSET), None


def _counter(graph: SceneGraph, bounds, centre) -> tuple[tuple[float, float], SceneNode | None]:
    named = [node for node in graph.nodes if "counter" in node.label.lower()]
    counter = _largest(named)
    if counter is not None:
        return _beside(counter, centre), counter
    return (centre[0], bounds[3] - INSET), None


def suggest_scenario(graph: SceneGraph) -> Scenario:
    bounds = _room_bounds(graph)
    centre = _centre(bounds)
    floor = _OpenFloor(graph)
    entrance, door = _entrance(graph, bounds, centre)
    counter_at, counter = _counter(graph, bounds, centre)
    table = _nearest([node for node in graph.nodes if node.raw_category == "table"], centre)
    seat_at = _beside(table, centre) if table else (bounds[0] + INSET, centre[1])
    pickup_at = (counter_at[0] + 1.0, counter_at[1])

    placed: list[Vec3] = []

    def stop(name: str, at: tuple[float, float], anchor: SceneNode | None) -> Stop:
        position = floor.snap(at, placed)
        placed.append(position)
        return Stop(name=name, position=position, anchor_node_id=anchor.id if anchor else None)

    stops = [
        stop("Entrance", entrance, door),
        stop("Counter", counter_at, counter),
        stop("Pickup", pickup_at, counter),
        stop("Seat", seat_at, table),
    ]
    exit_stop = stops[0].model_copy(update={"name": "Exit"})
    return Scenario(name="Order a drink", stops=[*stops, exit_stop])
