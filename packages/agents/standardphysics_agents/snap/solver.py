"""Snap a requested rearrangement to the nearest layout the room allows.

The model says where it wants each piece; this module decides where each piece
can actually stand. For every requested move, in the order tables, other
pieces, then seats:

1. The requested spot is the piece's position plus the model's slide.
2. Candidate spots ring outward from it, nearest first, up to `SEARCH_RADIUS_METERS`,
   then run back along the straight line to where the piece started, so a piece
   pushed into a wall ends up against it rather than staying put.
3. At each spot the piece takes its settled heading (`facing.settled_yaw`): a seat
   turns to its table, a shelf turns its back to the wall. Other pieces keep the
   requested turn.
4. The first spot where `fix.constraints.violations` finds nothing wins: no
   overlap, still on the floor, within the travel limit, no table left without
   room to use it. A cheap footprint test rejects most spots before that call.
5. A piece with no legal spot stays where it was, and the result says so.

Seats paired with a table the model moved travel with it unless the model moved
them itself. A layout this returns never breaks a hard constraint.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from standardphysics_contracts import NodeMove, SceneGraph, SceneNode, Vec3, bounds_the_room
from standardphysics_pipeline import footprint, gap_between

from ..fix import apply_moves, violations
from ..fix.constraints import OVERLAP_TOLERANCE
from .facing import is_seat, is_surface, served_surface, settled_yaw

SEARCH_RADIUS_METERS = 0.9
RING_STEP_METERS = 0.05
DIRECTIONS = 16

Rejection = Callable[[SceneGraph, SceneGraph], "str | None"]


@dataclass(frozen=True)
class Placement:
    node_id: UUID
    label: str
    snapped_meters: float | None
    """How far the legal spot is from the requested one; None when no legal spot was found."""
    turned_to_settle: bool


@dataclass
class Snapped:
    graph: SceneGraph
    placements: list[Placement] = field(default_factory=list)
    refused: str | None = None
    """Why a directive refused the finished layout, if one did."""

    @property
    def unplaced(self) -> list[Placement]:
        return [placement for placement in self.placements if placement.snapped_meters is None]

    @property
    def mean_snap_meters(self) -> float:
        placed = [p.snapped_meters for p in self.placements if p.snapped_meters is not None]
        return sum(placed) / len(placed) if placed else 0.0


def _yaw(node: SceneNode) -> float:
    return math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))


def _xy(node: SceneNode) -> tuple[float, float]:
    return node.transform.position.x, node.transform.position.y


def _rings(target: tuple[float, float]):
    yield target
    radius = RING_STEP_METERS
    while radius <= SEARCH_RADIUS_METERS + 1e-9:
        count = max(DIRECTIONS, int(2 * math.pi * radius / RING_STEP_METERS))
        for index in range(count):
            angle = 2 * math.pi * index / count
            yield target[0] + radius * math.cos(angle), target[1] + radius * math.sin(angle)
        radius += RING_STEP_METERS


def _back_toward(target: tuple[float, float], origin: tuple[float, float]):
    """Points on the way back from an unreachable request to where the piece started."""
    length = math.dist(target, origin)
    steps = int(length / RING_STEP_METERS)
    for step in range(1, steps + 1):
        share = step * RING_STEP_METERS / length
        yield target[0] + (origin[0] - target[0]) * share, target[1] + (origin[1] - target[1]) * share


def spots_near(target: tuple[float, float], origin: tuple[float, float]):
    """Nearest-first spots around the request, then back along the way the piece came."""
    yield from _rings(target)
    yield from _back_toward(target, origin)


def _order(graph: SceneGraph, moves: list[NodeMove]) -> list[NodeMove]:
    nodes = {node.id: node for node in graph.nodes}

    def rank(move: NodeMove) -> int:
        node = nodes.get(move.node_id)
        if node is None:
            return 3
        return 0 if is_surface(node) else 2 if is_seat(node) else 1

    return sorted(moves, key=rank)


def seat_pairs(graph: SceneGraph) -> list[tuple[UUID, UUID]]:
    """(seat, surface) for every movable seat sitting at a surface."""
    pairs = []
    for node in graph.nodes:
        if node.movable and node.kind == "object" and is_seat(node):
            surface = served_surface(_xy(node), graph, node.id)
            if surface is not None:
                pairs.append((node.id, surface.id))
    return pairs


def with_carried_seats(graph: SceneGraph, moves: list[NodeMove]) -> list[NodeMove]:
    """The requested moves plus a matching slide for each seat of a moved table the model left alone."""
    asked = {move.node_id: move for move in moves}
    carried = [
        NodeMove(node_id=seat, delta_translation=asked[table].delta_translation)
        for seat, table in seat_pairs(graph)
        if table in asked and seat not in asked
    ]
    return _order(graph, [*moves, *carried])


def _pose_move(node: SceneNode, xy: tuple[float, float], yaw: float) -> NodeMove:
    turn = (yaw - _yaw(node) + 180.0) % 360.0 - 180.0
    return NodeMove(node_id=node.id, delta_translation=Vec3(x=xy[0] - _xy(node)[0], y=xy[1] - _xy(node)[1], z=0.0),
                    delta_rotation_z_degrees=turn)


def _obstacles(graph: SceneGraph, moving: UUID) -> list:
    return [footprint(node) for node in graph.nodes
            if node.id != moving and node.kind in ("wall", "object") and not _is_thin_floor(node)]


def _is_thin_floor(node: SceneNode) -> bool:
    return node.kind != "wall" and bounds_the_room(node)


def _shrunk(polygon: list, by: float) -> list:
    cx, cy = sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon)
    out = []
    for x, y in polygon:
        reach = math.hypot(x - cx, y - cy)
        keep = max(0.0, reach - by) / reach if reach else 0.0
        out.append((cx + (x - cx) * keep, cy + (y - cy) * keep))
    return out


def _looks_clear(posed: SceneNode, obstacles: list) -> bool:
    """A quick footprint test that only ever rejects spots `violations` would reject too."""
    shape = _shrunk(footprint(posed), 2 * OVERLAP_TOLERANCE)
    return all(gap_between(shape, other) > 0.0 for other in obstacles)


def _place(base: SceneGraph, current: SceneGraph, move: NodeMove) -> tuple[SceneGraph, Placement]:
    node = next(n for n in current.nodes if n.id == move.node_id)
    target = (_xy(node)[0] + move.delta_translation.x, _xy(node)[1] + move.delta_translation.y)
    asked_yaw = _yaw(node) + move.delta_rotation_z_degrees
    obstacles = _obstacles(current, node.id)
    for spot in spots_near(target, _xy(node)):
        yaw = settled_yaw(node, spot, current)
        pose = _pose_move(node, spot, asked_yaw if yaw is None else yaw)
        candidate = apply_moves(current, [pose])
        posed = next(n for n in candidate.nodes if n.id == node.id)
        if not _looks_clear(posed, obstacles) or violations(base, candidate):
            continue
        return candidate, Placement(node.id, node.label, math.dist(spot, target), yaw is not None)
    return current, Placement(node.id, node.label, None, False)


def snap(base: SceneGraph, moves: list[NodeMove], rejection: Rejection | None = None) -> Snapped:
    """The nearest legal layout to the requested one, never breaking a hard constraint."""
    current, placements = base, []
    movable = {node.id for node in base.nodes if node.movable}
    for move in with_carried_seats(base, [m for m in moves if m.node_id in movable]):
        current, placement = _place(base, current, move)
        placements.append(placement)
    refused = rejection(base, current) if rejection else None
    return Snapped(current, placements, refused)
