"""How a rearranged room looks, as a number in [0, 1] the reward can use.

Q = WALL_WEIGHT * W + PAIR_WEIGHT * P + SIGHT_WEIGHT * S.

W  wall relation. Each moved piece's angle to its nearest wall and distance
   from it, compared with the owner's layout (the room before any scramble).
P  chair and table pairs. Each seat and the table nearest it in the owner's
   layout keep their distance and the seat keeps facing the table.
S  sightlines. The open floor visible at eye level from the service counter's
   customer side, or from the entrance door, after the edits against before.

W and P score one relation with `relation_score`, which weights angle twice as
heavily as distance, following Yu et al. 2011 ("Make it Home"), whose layout
cost weights wall angle 10 against wall distance 1 to 5.

Every constant here is a starting value, to be tuned against a human rating
check (see `runs/finetune/multiroom/rating_pairs.jsonl`).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from standardphysics_contracts import MeasurementProvider, SceneGraph, SceneNode, bounds_the_room, lies_flat
from standardphysics_pipeline import contains_point, footprint
from standardphysics_pipeline.footprints import floor_polygon

from ..checks import roles
from .edits import yaw_degrees

WALL_WEIGHT = 0.4
"""Share of Q for wall relation. Starting value, to be tuned by a human rating check."""
PAIR_WEIGHT = 0.3
"""Share of Q for chair and table pairs. Starting value, to be tuned by a human rating check."""
SIGHT_WEIGHT = 0.3
"""Share of Q for sightlines. Starting value, to be tuned by a human rating check."""

DISTANCE_SCALE_METERS = 0.5
"""A relation's distance changing by this much keeps 1/e of its distance score.
Starting value, to be tuned by a human rating check."""

ANGLE_SHARE = 2 / 3
"""Angle counts twice as much as distance, after Yu et al. 2011."""

SIGHT_BLOCKING_HEIGHT_METERS = 1.2
"""A piece blocks sight when it spans this height: a standing adult's line of
sight clears a low table or a chair back and not a shelf or a partition.
Starting value, to be tuned by a human rating check."""

PAIR_REACH_METERS = 1.5
"""A seat further than this from every table belongs to no table."""

ISOVIST_RAYS = 180
ISOVIST_REACH_METERS = 20.0
ENTRANCE_STEP_METERS = 0.3
"""How far into the room from the door the entrance view is taken."""

TABLE_WORDS = ("table", "desk")
SEAT_WORDS = ("chair", "stool", "sofa", "bench", "seat")


@dataclass(frozen=True)
class Quality:
    q: float
    wall: float
    pairs: float
    sight: float

    def as_dict(self) -> dict:
        return asdict(self)


def relation_score(turned_degrees: float, moved_meters: float) -> float:
    """1 for an unchanged relation; the angle part halves at 45 degrees and is gone at 90."""
    angle = 1 - min(abs(turned_degrees), 90.0) / 90.0
    return ANGLE_SHARE * angle + (1 - ANGLE_SHARE) * math.exp(-abs(moved_meters) / DISTANCE_SCALE_METERS)


def _xy(node: SceneNode) -> tuple[float, float]:
    return node.transform.position.x, node.transform.position.y


def _angle_between(a: float, b: float, period: float) -> float:
    """Smallest difference between two angles that repeat every `period` degrees."""
    difference = abs(a - b) % period
    return min(difference, period - difference)


def wall_segments(graph: SceneGraph) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for node in graph.nodes:
        if node.kind != "wall" or lies_flat(node):
            continue
        hull = floor_polygon(node)
        ends = max(((a, b) for a in hull for b in hull), key=lambda pair: math.dist(*pair))
        segments.append(ends)
    return segments


def _to_segment(point, segment) -> float:
    (px, py), ((ax, ay), (bx, by)) = point, segment
    length = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length))
    return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))


def wall_relation(node: SceneNode, walls) -> tuple[float, float]:
    """The piece's heading against its nearest wall, in degrees modulo 180, and its distance from it."""
    nearest = min(walls, key=lambda wall: _to_segment(_xy(node), wall))
    (ax, ay), (bx, by) = nearest
    direction = math.degrees(math.atan2(by - ay, bx - ax))
    return (yaw_degrees(node) - direction) % 180.0, _to_segment(_xy(node), nearest)


def moved_ids(before: SceneGraph, after: SceneGraph) -> set:
    was = {node.id: node.transform.m for node in before.nodes}
    return {node.id for node in after.nodes if node.id in was and node.transform.m != was[node.id]}


def wall_term(owner: SceneGraph, after: SceneGraph, moved: set) -> float:
    walls = wall_segments(owner)
    owned = {node.id: node for node in owner.nodes}
    pieces = [node for node in after.nodes if node.id in moved and node.id in owned]
    if not walls or not pieces:
        return 1.0
    scores = []
    for piece in pieces:
        angle_before, distance_before = wall_relation(owned[piece.id], walls)
        angle_after, distance_after = wall_relation(piece, walls)
        scores.append(relation_score(_angle_between(angle_before, angle_after, 180.0),
                                     distance_after - distance_before))
    return sum(scores) / len(scores)


def _named(node: SceneNode, words) -> bool:
    text = f"{node.label} {node.raw_category}".casefold()
    return not bounds_the_room(node) and any(word in text for word in words)


def seat_table_pairs(owner: SceneGraph) -> list[tuple]:
    tables = [node for node in owner.nodes if _named(node, TABLE_WORDS)]
    pairs = []
    for seat in (node for node in owner.nodes if node.movable and _named(node, SEAT_WORDS)):
        nearest = min(tables, key=lambda table: math.dist(_xy(seat), _xy(table)), default=None)
        if nearest is not None and math.dist(_xy(seat), _xy(nearest)) <= PAIR_REACH_METERS:
            pairs.append((seat.id, nearest.id))
    return pairs


def front_heading_degrees(node: SceneNode) -> float:
    """The direction the object visibly faces, in degrees.

    `yaw_degrees` reads the node's local +X axis, but a chair's front is its
    local -Y edge: `measure.py`'s `_front_face_centre` and `locus.py`'s
    `_front_face` already read `dimensions.y` as the depth axis, never
    `dimensions.x`. Checking every scan in the local database confirms it
    geometrically too: for chairs within 1.2 m of their nearest table,
    `yaw_degrees(seat) - bearing(seat, table)` peaks hard at +90 degrees (100
    of about 250 chairs), not at 0, so local +X is a seat's side and local -Y
    is what actually points at the table.
    """
    return yaw_degrees(node) - 90.0


def _facing_off(seat: SceneNode, table: SceneNode) -> float:
    """How far the seat's heading turns from the table, in degrees."""
    (sx, sy), (tx, ty) = _xy(seat), _xy(table)
    bearing = math.degrees(math.atan2(ty - sy, tx - sx))
    return front_heading_degrees(seat) - bearing


def pair_term(owner: SceneGraph, after: SceneGraph, moved: set) -> float:
    owned, placed = {n.id: n for n in owner.nodes}, {n.id: n for n in after.nodes}
    affected = [(s, t) for s, t in seat_table_pairs(owner) if (s in moved or t in moved) and s in placed and t in placed]
    if not affected:
        return 1.0
    scores = []
    for seat, table in affected:
        turned = _angle_between(_facing_off(owned[seat], owned[table]), _facing_off(placed[seat], placed[table]), 360.0)
        stretched = math.dist(_xy(placed[seat]), _xy(placed[table])) - math.dist(_xy(owned[seat]), _xy(owned[table]))
        scores.append(relation_score(turned, stretched))
    return sum(scores) / len(scores)


def _blocks_sight(node: SceneNode) -> bool:
    if bounds_the_room(node):
        return False
    middle, half = node.transform.position.z, node.dimensions.z / 2
    return middle - half < SIGHT_BLOCKING_HEIGHT_METERS <= middle + half


def _edges(polygon) -> list:
    return list(zip(polygon, polygon[1:] + polygon[:1]))


def sight_edges(graph: SceneGraph, viewpoint) -> list:
    edges = list(wall_segments(graph))
    for node in graph.nodes:
        if _blocks_sight(node) and not contains_point(footprint(node), viewpoint):
            edges += _edges(footprint(node))
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    return edges + (_edges(floor_polygon(floor)) if floor is not None else [])


def _ray_hit(origin, direction, edge) -> float | None:
    (ox, oy), (dx, dy), ((ax, ay), (bx, by)) = origin, direction, edge
    ex, ey = bx - ax, by - ay
    denominator = dx * ey - dy * ex
    if abs(denominator) < 1e-12:
        return None
    t = ((ax - ox) * ey - (ay - oy) * ex) / denominator
    u = ((ax - ox) * dy - (ay - oy) * dx) / denominator
    return t if t > 1e-6 and 0.0 <= u <= 1.0 else None


def isovist_area(graph: SceneGraph, viewpoint) -> float:
    """Square metres of floor visible at eye level from `viewpoint`, by ray casting."""
    edges, step = sight_edges(graph, viewpoint), 2 * math.pi / ISOVIST_RAYS
    area = 0.0
    for index in range(ISOVIST_RAYS):
        direction = (math.cos(index * step), math.sin(index * step))
        hits = [hit for edge in edges if (hit := _ray_hit(viewpoint, direction, edge)) is not None]
        reach = min(hits, default=ISOVIST_REACH_METERS)
        area += 0.5 * min(reach, ISOVIST_REACH_METERS) ** 2 * step
    return area


def _entrance_view(graph: SceneGraph):
    door = roles.entrance(graph)
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    if door is None or floor is None:
        return None
    (dx, dy), (fx, fy) = _xy(door), _xy(floor)
    length = math.hypot(fx - dx, fy - dy) or 1.0
    return dx + (fx - dx) / length * ENTRANCE_STEP_METERS, dy + (fy - dy) / length * ENTRANCE_STEP_METERS


def viewpoint(graph: SceneGraph, measure: MeasurementProvider):
    """The customer side of the service counter, or just inside the entrance, or None."""
    counters = roles.service_counters(graph)
    if counters:
        centre = measure.counter_approach(graph, counters[0].id).center
        return centre.x, centre.y
    return _entrance_view(graph)


def sight_term(before: SceneGraph, after: SceneGraph, measure: MeasurementProvider) -> float:
    eye = viewpoint(before, measure)
    if eye is None:
        return 1.0
    was = isovist_area(before, eye)
    return 1.0 if was <= 0 else min(1.0, isovist_area(after, eye) / was)


def layout_quality(before: SceneGraph, after: SceneGraph, owner: SceneGraph, measure: MeasurementProvider) -> Quality:
    """Q for moving from `before` to `after`, judged against the owner's own layout."""
    moved = moved_ids(before, after)
    wall, pairs, sight = wall_term(owner, after, moved), pair_term(owner, after, moved), sight_term(before, after, measure)
    q = WALL_WEIGHT * wall + PAIR_WEIGHT * pairs + SIGHT_WEIGHT * sight
    return Quality(round(q, 6), round(wall, 6), round(pairs, 6), round(sight, 6))
