"""Place furniture around a required clear space, then measure the whole room.

The small slide ladder cannot escape a corner or move several chairs out of
one turning space. This bounded beam tries positions and angles together.
Geometry only generates candidates; the search's assessment gate accepts them.
"""

from __future__ import annotations

import math
from itertools import combinations

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import Polygon, polygon_bounds, rotation_about_z

from ..checks.rectangles import rectangle
from ..rules import AgentRulePack
from .constraints import violations
from .moves import apply_moves, move_node
from .pinch import Pinch
from .strategies import Candidate

ANGLES = (0.0, -30.0, 30.0, -45.0, 45.0, -60.0, 60.0, -90.0, 90.0, 180.0)
OPTIONS_PER_PIECE = 8
BEAM_WIDTH = 16
MAX_PIECES = 8


def _space(finding: Finding, graph: SceneGraph, rules: AgentRulePack) -> Polygon:
    """The required region, rather than the undersized region that was measured."""
    rule = rules.by_id(finding.check_id)
    width = depth = to_meters(finding.required_inches)
    rotation = (1.0, 0.0)
    if finding.check_id == "service_counter_approach":
        width = to_meters(rule.parameter("clear_width_min_inches"))
        depth = to_meters(rule.parameter("clear_depth_min_inches"))
        rotation = rotation_about_z(graph.by_id(finding.locus.node_ids[0]))
    return rectangle(finding.locus.point, width, depth, rotation)


def _candidate(moves: list[NodeMove]) -> Candidate:
    return Candidate("place_furniture", moves, sum(
        math.hypot(m.delta_translation.x, m.delta_translation.y)
        + abs(m.delta_rotation_z_degrees) / 180.0
        for m in moves
    ))


def _options(node: SceneNode, space: Polygon) -> list[NodeMove]:
    low_x, low_y, high_x, high_y = polygon_bounds(space)
    origin = node.transform.position
    options = []
    # Step beyond the clearance edge, with room for occupancy-grid rounding.
    margin = 0.06
    for angle in ANGLES:
        turn = NodeMove(node_id=node.id, delta_translation=Vec3(x=0, y=0, z=0),
                        delta_rotation_z_degrees=angle)
        shape = footprint(move_node(node, turn))
        half_x = max(abs(x - origin.x) for x, _ in shape)
        half_y = max(abs(y - origin.y) for _, y in shape)
        xs = (low_x - half_x - margin, high_x + half_x + margin)
        ys = (low_y - half_y - margin, high_y + half_y + margin)
        points = [(origin.x, origin.y)]
        # Try each side and slide along it to get past neighboring furniture.
        for offset in (0, -0.3, 0.3, -0.75, 0.75, -1.5, 1.5):
            points.extend((x, origin.y + offset) for x in xs)
            points.extend((origin.x + offset, y) for y in ys)
        points.extend((x, y) for x in xs for y in ys)
        for x, y in points:
            move = turn.model_copy(update={"delta_translation": Vec3(
                x=x - origin.x, y=y - origin.y, z=0,
            )})
            if gap_between(footprint(move_node(node, move)), space) <= 0:
                continue
            options.append(move)
    options.sort(key=lambda move: _candidate([move]).disruption)
    return options


def _groups(pieces: list) -> list[tuple]:
    """Which pieces to try together.

    Single moves first, then every pair, and the whole set when there are more
    than two, because a space of zero width can need two things moved before any
    single move shows an improvement.
    """
    groups: list[tuple] = [(node,) for node in pieces]
    if len(pieces) > 2:
        groups.append(tuple(pieces))
    groups.extend(combinations(pieces, 2))
    return groups


def _expand(graph: SceneGraph, node, moves_for_node, beam: list[list[NodeMove]]) -> list[list[NodeMove]]:
    """Every legal way to add one more move for this node to each route in the beam."""
    expanded = []
    for moves in beam:
        accepted = 0
        occupied = set()
        for move in moves_for_node:
            position = move_node(node, move).transform.position
            # Keep different destinations in the beam, not eight near-identical
            # rotations of one parking spot.
            key = (round(position.x / 0.2), round(position.y / 0.2))
            if key in occupied:
                continue
            trial = [*moves, move]
            if violations(graph, apply_moves(graph, trial)):
                continue
            expanded.append(trial)
            occupied.add(key)
            accepted += 1
            if accepted == OPTIONS_PER_PIECE:
                break
    return expanded


def _beam_for(graph: SceneGraph, group: tuple, options: dict) -> list[list[NodeMove]]:
    beam: list[list[NodeMove]] = [[]]
    for node in group:
        expanded = _expand(graph, node, options[node.id], beam)
        beam = sorted(expanded, key=lambda moves: _candidate(moves).disruption)[:BEAM_WIDTH]
        if not beam:
            break
    return beam


def placements(graph: SceneGraph, pinch: Pinch, finding: Finding,
               rules: AgentRulePack, limit: int) -> list[Candidate]:
    if limit <= 0 or not pinch.fixable:
        return []
    space = _space(finding, graph, rules)
    pieces = sorted(pinch.movable, key=lambda n: str(n.id))[:MAX_PIECES]
    options = {node.id: _options(node, space) for node in pieces}
    found = []
    for group in _groups(pieces):
        found.extend(_candidate(moves) for moves in _beam_for(graph, group, options))
        if len(found) >= limit:
            break
    return found[:limit]
