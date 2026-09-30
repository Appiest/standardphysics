"""Which way a piece should face once it stands somewhere.

A piece's visible front is its local -Y edge (`training.quality.front_heading_degrees`),
so a yaw of F + 90 degrees points the front along F. Three kinds of piece get a
settled heading instead of whatever the request asked for:

    seats        face the nearest surface they serve (table, desk, counter, ledge,
                 coffee table) within `SERVE_REACH_METERS`, squarely at its closest side
    wall pieces  shelving, fridges, stations and the like stand with their back to the
                 nearest wall within `WALL_REACH_METERS`, front out into the room
    seats with no surface but a wall behind them, such as a waiting row, do the same

Everything else keeps the requested heading.
"""

from __future__ import annotations

import math

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room
from standardphysics_pipeline import footprint

from ..checks import roles
from ..checks.walls import wall_faces

SERVE_REACH_METERS = 1.0
"""A seat this close to a surface's edge is taken to be sitting at it."""
WALL_REACH_METERS = 0.35
"""A piece whose back is this close to a wall is taken to stand against it."""

SEAT_WORDS = ("chair", "stool", "bench", "seat", "sofa", "armchair")
SURFACE_WORDS = ("table", "desk", "counter", "ledge", "bar")
WALL_PIECE_WORDS = (
    "shelf", "shelving", "bookcase", "fridge", "cooler", "rack", "station", "bins", "atm", "kiosk",
    "cabinet", "printer", "sink", "lavatory", "dispenser", "menu board", "changing table",
)


def _text(node: SceneNode) -> str:
    return f"{node.label} {node.raw_category}".casefold()


def is_seat(node: SceneNode) -> bool:
    return roles.is_seating(node) or any(word in _text(node) for word in SEAT_WORDS)


def is_surface(node: SceneNode) -> bool:
    return not bounds_the_room(node) and not is_seat(node) and any(word in _text(node) for word in SURFACE_WORDS)


def backs_onto_wall(node: SceneNode) -> bool:
    return not is_seat(node) and not is_surface(node) and any(word in _text(node) for word in WALL_PIECE_WORDS)


def yaw_facing(direction_degrees: float) -> float:
    """The yaw that points a piece's front along `direction_degrees`."""
    return (direction_degrees + 90.0 + 180.0) % 360.0 - 180.0


def _closest_on_segment(point, a, b) -> tuple[float, float]:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length))
    return ax + t * dx, ay + t * dy


def _closest_on_polygon(point, polygon) -> tuple[float, float]:
    edges = zip(polygon, polygon[1:] + polygon[:1], strict=True)
    return min((_closest_on_segment(point, a, b) for a, b in edges), key=lambda q: math.dist(point, q))


def _bearing(origin, target) -> float:
    return math.degrees(math.atan2(target[1] - origin[1], target[0] - origin[0]))


def _side_facing(seat_xy, surface: SceneNode) -> float:
    """The direction pointing from the side of `surface` the seat is at, straight in across it.

    Worked in the surface's own frame, so a chair tucked under a table (its centre
    inside the table's outline) still reads as sitting at the nearer side.
    """
    cx, cy = surface.transform.position.x, surface.transform.position.y
    cos_t, sin_t = surface.transform.m[0], surface.transform.m[4]
    scale = math.hypot(cos_t, sin_t) or 1.0
    cos_t, sin_t = cos_t / scale, sin_t / scale
    dx, dy = seat_xy[0] - cx, seat_xy[1] - cy
    along, across = dx * cos_t + dy * sin_t, -dx * sin_t + dy * cos_t
    half_x, half_y = max(surface.dimensions.x / 2, 1e-6), max(surface.dimensions.y / 2, 1e-6)
    yaw = math.degrees(math.atan2(sin_t, cos_t))
    if abs(along) / half_x >= abs(across) / half_y:
        return yaw + (180.0 if along > 0 else 0.0)
    return yaw + (-90.0 if across > 0 else 90.0)


def served_surface(seat_xy, graph: SceneGraph, ignoring) -> SceneNode | None:
    """The nearest surface whose edge is within reach of the seat, or None."""
    best, best_gap = None, SERVE_REACH_METERS
    for node in graph.nodes:
        if node.id == ignoring or not is_surface(node):
            continue
        gap = math.dist(seat_xy, _closest_on_polygon(seat_xy, footprint(node)))
        if gap <= best_gap:
            best, best_gap = node, gap
    return best


def wall_behind(xy, graph: SceneGraph, reach: float) -> tuple[float, float] | None:
    """The closest point of the nearest wall within `reach`, or None."""
    points = [_closest_on_segment(xy, a, b) for a, b in wall_faces(graph)]
    near = [point for point in points if math.dist(xy, point) <= reach]
    return min(near, key=lambda point: math.dist(xy, point), default=None)


def settled_yaw(node: SceneNode, xy: tuple[float, float], graph: SceneGraph) -> float | None:
    """The yaw this piece should have standing at `xy`, or None to keep the requested one."""
    if is_seat(node):
        surface = served_surface(xy, graph, node.id)
        if surface is not None:
            return yaw_facing(_side_facing(xy, surface))
    if is_seat(node) or backs_onto_wall(node):
        wall = wall_behind(xy, graph, node.dimensions.y / 2 + WALL_REACH_METERS)
        if wall is not None:
            return yaw_facing(_bearing(wall, xy))
    return None


def facing_error_degrees(node: SceneNode, graph: SceneGraph) -> float | None:
    """How far a piece's front turns from where it should face, or None when no rule applies."""
    centre = (node.transform.position.x, node.transform.position.y)
    wanted = settled_yaw(node, centre, graph)
    if wanted is None:
        return None
    yaw = math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))
    gap = abs(yaw - wanted) % 360.0
    return min(gap, 360.0 - gap)
