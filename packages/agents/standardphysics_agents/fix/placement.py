"""Place furniture around a required clear space, then measure the whole room.

The small slide ladder cannot escape a corner or move several chairs out of
one turning space. This bounded beam tries positions and angles together.
Geometry only generates candidates; the search's assessment gate accepts them.

A piece staff carry by hand, such as a sign stand, is also offered free floor
anywhere in the room, after the single-piece moves beside the space and before
moving pieces in pairs.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import build_grid, clearance_map, footprint, gap_between
from standardphysics_pipeline.footprints import Polygon, polygon_bounds, rotation_about_z
from standardphysics_pipeline.occupancy import CELL_SIZE

from ..checks.rectangles import intruders, rectangle
from ..rules import AgentRulePack
from .budget import out_of_time
from .constraints import violations
from .moves import apply_moves, carried_by_hand, move_node, without
from .pinch import Pinch
from .strategies import Candidate

ANGLES = (0.0, -30.0, 30.0, -45.0, 45.0, -60.0, 60.0, -90.0, 90.0, 180.0)
OPTIONS_PER_PIECE = 8
BEAM_WIDTH = 16
MAX_PIECES = 8
RECTANGLE_RULE = "service_counter_approach"
"""The one rule whose required space is a rectangle placed exactly, rather than a square around a circle."""
FREE_SPOTS = 6
"""Places anywhere in the room tried for each hand-carried piece."""
FREE_SPOT_MARGIN = 0.05
"""Metres kept between a set-down piece and anything else, and the space it was cleared from."""
SNUG_STEP = 0.1
"""Free spots are ranked by how snugly they hold the piece, in steps of this
many metres of spare room, then by distance. A snug spot is tucked against a
wall or a fixture, which is where staff put a sign they have moved."""
FREE_SPOT_SPACING = 0.5


def _space(finding: Finding, graph: SceneGraph, rules: AgentRulePack, inset: float = 0.0) -> Polygon:
    """The required region, rather than the undersized region that was measured, pulled in by `inset`."""
    rule = rules.by_id(finding.check_id)
    assert finding.locus is not None and finding.required_inches is not None
    width = depth = to_meters(finding.required_inches)
    rotation = (1.0, 0.0)
    if finding.check_id == RECTANGLE_RULE:
        width = to_meters(rule.parameter("clear_width_min_inches"))
        depth = to_meters(rule.parameter("clear_depth_min_inches"))
        rotation = rotation_about_z(graph.by_id(finding.locus.node_ids[0]))
    return rectangle(finding.locus.point, width - 2 * inset, depth - 2 * inset, rotation)


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


def _singles(pieces: list) -> list[tuple]:
    return [(node,) for node in pieces]


def _together(pieces: list) -> list[tuple]:
    """The whole set when there are more than two, then every pair.

    A space of zero width can need two things moved before any single move
    shows an improvement.
    """
    groups: list[tuple] = [tuple(pieces)] if len(pieces) > 2 else []
    groups.extend(combinations(pieces, 2))
    return groups


def _one_or_all(carried: list) -> list[tuple]:
    return _singles(carried) + ([tuple(carried)] if len(carried) > 1 else [])


def _free_spots(graph: SceneGraph, node: SceneNode, space: Polygon) -> list[NodeMove]:
    """Moves setting a hand-carried piece down on free floor anywhere in the room, snug spots nearest first."""
    grid = build_grid(without(graph, [node.id]))
    spare = clearance_map(grid) - (math.hypot(node.dimensions.x, node.dimensions.y) / 2 + FREE_SPOT_MARGIN)
    usable = spare >= 0 if grid.indoors is None else (spare >= 0) & grid.indoors
    rows, cols = np.nonzero(usable)
    xs = grid.origin_x + (cols + 0.5) * grid.cell_size
    ys = grid.origin_y + (rows + 0.5) * grid.cell_size
    origin = node.transform.position
    order = np.lexsort((np.hypot(xs - origin.x, ys - origin.y), np.floor(spare[rows, cols] / SNUG_STEP)))
    moves, taken = [], set()
    for index in order:
        area = (round(xs[index] / FREE_SPOT_SPACING), round(ys[index] / FREE_SPOT_SPACING))
        move = NodeMove(node_id=node.id, delta_translation=Vec3(x=float(xs[index] - origin.x),
                                                                  y=float(ys[index] - origin.y), z=0.0))
        if area in taken or gap_between(footprint(move_node(node, move)), space) <= FREE_SPOT_MARGIN:
            continue
        taken.add(area)
        moves.append(move)
        if len(moves) == FREE_SPOTS:
            break
    return moves


def _expand(graph: SceneGraph, node, moves_for_node, beam: list[list[NodeMove]],
            deadline: float | None = None) -> list[list[NodeMove]]:
    """Every legal way to add one more move for this node to each route in the beam, until the deadline."""
    expanded = []
    for moves in beam:
        if out_of_time(deadline):
            return []
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


def _beam_for(graph: SceneGraph, group: tuple, options: dict,
              deadline: float | None = None) -> list[list[NodeMove]]:
    beam: list[list[NodeMove]] = [[]]
    for node in group:
        expanded = _expand(graph, node, options[node.id], beam, deadline)
        beam = sorted(expanded, key=lambda moves: _candidate(moves).disruption)[:BEAM_WIDTH]
        if not beam:
            break
    return beam


def _beams(graph: SceneGraph, groups: list[tuple], options: dict, limit: int,
           deadline: float | None = None) -> list[Candidate]:
    found: list[Candidate] = []
    for group in groups:
        if len(found) >= limit or out_of_time(deadline):
            break
        found.extend(_candidate(moves) for moves in _beam_for(graph, group, options, deadline))
    return found


def placements(graph: SceneGraph, pinch: Pinch, finding: Finding,
               rules: AgentRulePack, limit: int, deadline: float | None = None) -> list[Candidate]:
    """Up to `limit` legal placements, or fewer when a `deadline` (see `fix/budget.py`) passes first."""
    if limit <= 0 or not pinch.fixable:
        return []
    space = _space(finding, graph, rules)
    pieces = sorted(pinch.movable, key=lambda n: str(n.id))[:MAX_PIECES]
    options = {node.id: _options(node, space) for node in pieces}
    found = _beams(graph, _singles(pieces), options, limit, deadline)
    carried = [node for node in pieces if carried_by_hand(node)]
    spots = {node.id: _free_spots(graph, node, space) for node in carried}
    found += _beams(graph, _one_or_all(carried), spots, limit - len(found), deadline)
    found += _beams(graph, _together(pieces), options, limit - len(found), deadline)
    found = found[:limit]
    if finding.check_id != RECTANGLE_RULE:
        return found
    return _emptiest_first(graph, found, _space(finding, graph, rules, inset=CELL_SIZE))


def _emptiest_first(graph: SceneGraph, found: list[Candidate], space: Polygon) -> list[Candidate]:
    """Candidates that leave fewer pieces standing in the space are measured first.

    A candidate that leaves a piece in the space cannot clear it, and measuring
    one costs a whole assessment. The order within each count stays least
    disruptive first. Only the counter's 30 by 48 inch space is drawn exactly;
    the square standing in for a turning circle counts pieces in its corners
    that the circle does not reach. The space is pulled in by a grid cell, the
    resolution the measurement itself works at.
    """
    return sorted(found, key=lambda candidate: len(intruders(apply_moves(graph, candidate.moves), space)))
