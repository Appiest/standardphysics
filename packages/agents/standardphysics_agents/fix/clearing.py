"""Guesses for a space that several pieces block at once: push every one of them out together.

The slide ladder moves the pieces a finding names along one measurement line,
and a turning circle has no such line: a chair on its left and a bench on its
right both have to leave, each in its own direction, before the circle fits.
Moving one of them alone changes nothing the checker can measure, so the gate
refuses it. This file pushes every piece reaching inside the required circle
straight out from the centre, all in one guess, the way `room_solver.py` does
for its construction search, but only ever with furniture.

The check accepts a circle anywhere near the stop, not only at the spot the
finding reports, so the push is also tried around a ring of nearby centres. A
centre where a wall or a built-in reaches inside is skipped, because no amount
of furniture moving would clear it. Nothing here is measured; the hard
constraints and the checker decide which pushes are real.

The clear floor in front of a counter is the same problem in a rectangle. On
Share Tea two chairs, a stool and a backpack stood in the 48 by 30 inches in
front of the ordering counter, and no slide of one of them measured as any
better. Every piece in the rectangle is pushed out of it at once: straight away
from the counter, off either end, or each by its own nearest way out. A push
that lands a piece on another is nudged to the nearest legal floor, the way a
model's free-form move is.
"""

from __future__ import annotations

import math

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, lies_flat, to_meters
from standardphysics_pipeline import blocks_floor, contains_point, footprint
from standardphysics_pipeline.footprints import Polygon, closest_point, rotation_about_z, touching
from standardphysics_pipeline.measure import COUNTER_CLEAR_DEPTH, COUNTER_CLEAR_WIDTH

from ..checks.rectangles import EDGE_TOLERANCE, rectangle
from .constraints import violations
from .moves import apply_moves
from .snap import snap_moves
from .strategies import Candidate

CIRCLE_CHECKS = frozenset({"turning_space"})
RECTANGLE_CHECKS = frozenset({"service_counter_approach"})
"""Checks whose locus names the piece the space stands against first and sits at the space's centre."""
RING_OFFSETS_INCHES = (0.0, 6.0, 12.0, 18.0)
RING_DIRECTIONS = 8
PUSH_MARGINS_INCHES = (1.5, 6.0)
"""How far past the circle's edge a pushed piece lands: just clear, or with room for the grid's rounding."""


def _centres(finding: Finding) -> list[tuple[float, float]]:
    assert finding.locus is not None
    point = finding.locus.point
    centres = []
    for inches in RING_OFFSETS_INCHES:
        steps = 1 if inches == 0 else RING_DIRECTIONS
        for step in range(steps):
            angle = 2 * math.pi * step / steps
            centres.append((point.x + to_meters(inches) * math.cos(angle), point.y + to_meters(inches) * math.sin(angle)))
    return centres


def _reaches_inside(node: SceneNode, centre: tuple[float, float], radius: float) -> bool:
    shape = footprint(node)
    return contains_point(shape, centre) or math.dist(closest_point(shape, centre), centre) < radius


def _push(node: SceneNode, centre: tuple[float, float], radius: float, margin: float) -> NodeMove | None:
    """The slide that puts the piece's nearest edge just outside the circle, straight away from the centre."""
    shape = footprint(node)
    edge = closest_point(shape, centre)
    distance = math.dist(edge, centre)
    if distance < 1e-6:
        return None
    toward_edge = ((edge[0] - centre[0]) / distance, (edge[1] - centre[1]) / distance)
    if contains_point(shape, centre):
        away, meters = (-toward_edge[0], -toward_edge[1]), distance + radius + margin
    else:
        away, meters = toward_edge, radius - distance + margin
    return NodeMove(node_id=node.id, delta_translation=Vec3(x=away[0] * meters, y=away[1] * meters, z=0.0))


def _blockers(graph: SceneGraph, centre: tuple[float, float], radius: float) -> list[SceneNode] | None:
    """The movable pieces reaching inside the circle, or None when something that cannot move does."""
    inside = [node for node in graph.nodes
              if not lies_flat(node) and blocks_floor(node) and _reaches_inside(node, centre, radius)]
    return None if any(not node.movable for node in inside) else inside


def _clearing(blockers: list[SceneNode], centre, radius: float, margin: float) -> Candidate | None:
    pushes = [_push(node, centre, radius, margin) for node in blockers]
    moves = [move for move in pushes if move is not None]
    if not pushes or len(moves) < len(pushes):
        return None
    return Candidate("clear_the_circle", moves,
                     sum(math.hypot(move.delta_translation.x, move.delta_translation.y) for move in moves))


def circle_clearing_moves(graph: SceneGraph, finding: Finding, pinned=frozenset()) -> list[Candidate]:
    """Every piece inside a too-tight turning circle pushed out of it at once, for each nearby centre, least moved first.

    A centre whose circle a pinned piece reaches into is skipped too, since that piece may not move.
    """
    if finding.check_id not in CIRCLE_CHECKS or finding.locus is None or finding.locus.point is None:
        return []
    if finding.required_inches is None:
        return []
    radius = to_meters(finding.required_inches) / 2
    found: dict[tuple, Candidate] = {}
    for centre in _centres(finding):
        blockers = _blockers(graph, centre, radius)
        if not blockers or any(node.id in pinned for node in blockers):
            continue
        for margin in PUSH_MARGINS_INCHES:
            candidate = _clearing(blockers, centre, radius, to_meters(margin))
            if candidate is not None:
                found.setdefault(_rounded(candidate), candidate)
    return sorted(found.values(), key=lambda candidate: candidate.disruption)


def _rounded(candidate: Candidate) -> tuple:
    """Two pushes that land every piece within an inch of the same spot count as one guess."""
    inch = to_meters(1.0)
    return tuple(sorted((str(move.node_id), round(move.delta_translation.x / inch), round(move.delta_translation.y / inch))
                        for move in candidate.moves))


Axes = tuple[tuple[float, float], tuple[float, float]]


def _space_axes(against: SceneNode, centre: tuple[float, float]) -> Axes:
    """Along the piece's face, and straight out from it toward the space."""
    cos_t, sin_t = rotation_about_z(against)
    across = (-sin_t, cos_t)
    toward = (centre[0] - against.transform.position.x) * across[0] + (centre[1] - against.transform.position.y) * across[1]
    sign = 1.0 if toward >= 0 else -1.0
    return (cos_t, sin_t), (across[0] * sign, across[1] * sign)


def _span(node: SceneNode, centre: tuple[float, float], axis: tuple[float, float]) -> tuple[float, float]:
    """How far the piece reaches, least and most, along `axis` from the centre."""
    reach = [(x - centre[0]) * axis[0] + (y - centre[1]) * axis[1] for x, y in footprint(node)]
    return min(reach), max(reach)


def _exits(node: SceneNode, centre: tuple[float, float], axes: Axes, margin: float) -> dict[str, NodeMove]:
    """The slide that takes the piece just past each side of the space it can leave by."""
    along, out = axes
    low, high = _span(node, centre, along)
    near, _ = _span(node, centre, out)
    half_width, depth = COUNTER_CLEAR_WIDTH / 2 + margin, COUNTER_CLEAR_DEPTH / 2 + margin
    slides = {"away": (out, depth - near), "left": (along, -half_width - high), "right": (along, half_width - low)}
    return {side: NodeMove(node_id=node.id, delta_translation=Vec3(x=axis[0] * meters, y=axis[1] * meters, z=0.0))
            for side, (axis, meters) in slides.items()}


def _length(move: NodeMove) -> float:
    return math.hypot(move.delta_translation.x, move.delta_translation.y)


def _in_formation(moves: list[NodeMove]) -> list[NodeMove]:
    """Every piece slid as far as the one with furthest to go, so they leave keeping their spacing.

    Slid each just past the edge, a stool behind another lands on it; on Share Tea every such push collided.
    """
    furthest = max(moves, key=_length).delta_translation
    return [NodeMove(node_id=move.node_id, delta_translation=furthest) for move in moves]


def _pushes(blockers: list[SceneNode], centre: tuple[float, float], axes: Axes, margin: float) -> list[Candidate]:
    """For each side, everything out that side, each just past the edge or all in formation, and everything out its
    own nearest side."""
    exits = [_exits(node, centre, axes, margin) for node in blockers]
    each = [[ways[side] for ways in exits] for side in ("away", "left", "right")]
    nearest = [min(ways.values(), key=_length) for ways in exits]
    return [Candidate("clear_the_space", moves, sum(_length(move) for move in moves))
            for moves in [*each, *map(_in_formation, each), nearest]]


def _landed(graph: SceneGraph, candidate: Candidate, space: Polygon) -> Candidate | None:
    """The push as asked when it is legal, else with each piece nudged to the nearest legal floor outside the space,
    else None."""
    if not violations(graph, apply_moves(graph, candidate.moves)):
        return candidate
    snapped = snap_moves(graph, candidate.moves, avoid=space)
    if snapped.dropped:
        return None
    return Candidate(candidate.strategy, snapped.kept, sum(_length(move) for move in snapped.kept))


def _space_blockers(graph: SceneGraph, space: Polygon, against: SceneNode) -> list[SceneNode] | None:
    """The movable pieces standing in the space, or None when something that cannot move does."""
    inside = [node for node in graph.nodes if node.id != against.id and not lies_flat(node)
              and blocks_floor(node) and touching(footprint(node), space)]
    return None if any(not node.movable for node in inside) else inside


def space_clearing_moves(graph: SceneGraph, finding: Finding, pinned=frozenset()) -> list[Candidate]:
    """Every piece standing in a too-small clear floor space pushed out of it at once, least moved first."""
    if finding.check_id not in RECTANGLE_CHECKS or finding.locus is None or not finding.locus.node_ids:
        return []
    against = graph.by_id(finding.locus.node_ids[0])
    centre = (finding.locus.point.x, finding.locus.point.y)
    space = rectangle(finding.locus.point, COUNTER_CLEAR_WIDTH - 2 * EDGE_TOLERANCE,
                      COUNTER_CLEAR_DEPTH - 2 * EDGE_TOLERANCE, rotation_about_z(against))
    blockers = _space_blockers(graph, space, against)
    if not blockers or any(node.id in pinned for node in blockers):
        return []
    axes = _space_axes(against, centre)
    found: dict[tuple, Candidate] = {}
    for margin in PUSH_MARGINS_INCHES:
        for push in _pushes(blockers, centre, axes, to_meters(margin)):
            candidate = _landed(graph, push, space)
            if candidate is not None:
                found.setdefault(_rounded(candidate), candidate)
    return sorted(found.values(), key=lambda candidate: candidate.disruption)
