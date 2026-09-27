"""Guesses that nudge one piece a short way in each of its own eight directions.

The slide ladder only moves a piece along the finding's measurement line or
across it, by multiples of the shortfall. A bench blocking a turning circle
often has a wall behind it on that line and a free side the ladder never
tries, so every guess the ladder makes collides. This file tries each piece the
finding names a few inches at a time along its own sides and corners: forward,
back, left, right and the four diagonals, in the piece's frame rather than the
room's, so a shelf standing square to a wall slides along that wall and stays
against it. A direction that heads toward the problem's spot is left out:
pushing a piece further into the space it blocks cannot open it.
"""

from __future__ import annotations

import math

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline.footprints import rotation_about_z

from .strategies import Candidate

NUDGE_INCHES = (3.0, 6.0, 10.0, 15.0, 24.0)
DIRECTIONS = 8
TOWARD_COSINE = -0.2
"""A direction this far or further round toward the problem's spot counts as heading into it."""


def directions_of(node: SceneNode) -> list[tuple[float, float]]:
    """The piece's own axes and diagonals, as unit vectors on the floor."""
    cos_t, sin_t = rotation_about_z(node)
    heading = math.atan2(sin_t, cos_t)
    return [(math.cos(heading + 2 * math.pi * step / DIRECTIONS), math.sin(heading + 2 * math.pi * step / DIRECTIONS))
            for step in range(DIRECTIONS)]


def not_toward(directions: list[tuple[float, float]], node: SceneNode,
               finding: Finding) -> list[tuple[float, float]]:
    """The directions that do not carry the piece toward the problem's spot; all of them when it sits on the spot."""
    spot = finding.locus.point
    away = (node.transform.position.x - spot.x, node.transform.position.y - spot.y)
    length = math.hypot(*away)
    if length < 1e-6:
        return directions
    return [(x, y) for x, y in directions if (x * away[0] + y * away[1]) / length > TOWARD_COSINE]


def _named_movable(graph: SceneGraph, finding: Finding, pinned) -> list[SceneNode]:
    named = set(finding.locus.node_ids) if finding.locus else set()
    return [node for node in graph.nodes if node.id in named and node.movable and node.id not in pinned]


def nudge_moves(graph: SceneGraph, finding: Finding, pinned=frozenset()) -> list[Candidate]:
    """Short slides of each movable piece the finding names, in its own eight directions, shortest first."""
    found = []
    for node in _named_movable(graph, finding, pinned):
        for x, y in not_toward(directions_of(node), node, finding):
            for inches in NUDGE_INCHES:
                meters = to_meters(inches)
                move = NodeMove(node_id=node.id, delta_translation=Vec3(x=x * meters, y=y * meters, z=0.0))
                found.append(Candidate("nudge", [move], meters))
    return sorted(found, key=lambda candidate: candidate.disruption)
