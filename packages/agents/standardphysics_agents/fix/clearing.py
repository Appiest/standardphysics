"""Guesses for a turning circle that several pieces block at once: push every one of them out together.

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
"""

from __future__ import annotations

import math

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, lies_flat, to_meters
from standardphysics_pipeline import blocks_floor, contains_point, footprint
from standardphysics_pipeline.footprints import closest_point

from .strategies import Candidate

CIRCLE_CHECKS = frozenset({"turning_space"})
RING_OFFSETS_INCHES = (0.0, 6.0, 12.0, 18.0)
RING_DIRECTIONS = 8
PUSH_MARGINS_INCHES = (1.5, 6.0)
"""How far past the circle's edge a pushed piece lands: just clear, or with room for the grid's rounding."""


def _centres(finding: Finding) -> list[tuple[float, float]]:
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
    moves = [_push(node, centre, radius, margin) for node in blockers]
    if not moves or None in moves:
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
