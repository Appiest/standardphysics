"""Guesses that slide a built-in together with the built-ins it touches.

A service counter's approach space is the patch of floor in front of it, so
sliding the counter alone carries that patch along, and a wall that was in the
patch is still in it. Sliding the lowered section alone pulls it off the
counter it belongs to, and the counter's height checks stop answering, which
the gate refuses. What a builder would do is move the whole run: the counter,
its lowered section and the register built into them, as one. The same holds
for a partition made of several wall panels that meet.

So this file takes each built-in a finding names, adds every built-in whose
outline touches it, and slides the set by the same amount along the named
piece's own sides and diagonals, never toward the problem's spot. Which pieces
count as built-ins is the caller's to say (`fixtures`). Nothing here is
measured; construction limits, the hard constraints and the checker decide.

A problem walled in by several built-ins, such as a restroom's partition
panels, gets one more guess: every built-in it names pushed out at once.
"""

from __future__ import annotations

import math
from uuid import UUID

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import closest_point

from .nudges import directions_of, not_toward
from .strategies import Candidate

TOUCHING_METERS = 0.05
"""Five centimetres. Two built-ins this close read as one run, as `checks/service_counter.py` reads them."""
SET_SLIDE_INCHES = (6.0, 12.0, 24.0)
APART_SHARES = (0.5, 1.0, 1.5)
"""How far each named built-in is pushed out, as shares of the problem's shortfall."""
APART_MARGIN_INCHES = 0.5


def _touching(graph: SceneGraph, node: SceneNode, fixtures: set[UUID]) -> list[SceneNode]:
    shape = footprint(node)
    return [other for other in graph.nodes
            if other.id in fixtures and other.id != node.id and gap_between(footprint(other), shape) <= TOUCHING_METERS]


def built_in_set_moves(graph: SceneGraph, finding: Finding, fixtures: set[UUID]) -> list[Candidate]:
    """Each named built-in slid with the built-ins touching it, shortest slide first.

    A named built-in already carried in an earlier one's run is not slid again as a run of its own.
    """
    named = [node for node in graph.nodes if node.id in fixtures and finding.locus and node.id in finding.locus.node_ids]
    found: list[Candidate] = []
    carried: set[UUID] = set()
    for node in named:
        if node.id in carried:
            continue
        members = [node, *_touching(graph, node, fixtures)]
        carried.update(member.id for member in members)
        for inches in SET_SLIDE_INCHES:
            meters = to_meters(inches)
            for x, y in not_toward(directions_of(node), node, finding):
                moves = [NodeMove(node_id=member.id, delta_translation=Vec3(x=x * meters, y=y * meters, z=0.0))
                         for member in members]
                found.append(Candidate("move_the_run", moves, meters))
    return found


def _away_from(node: SceneNode, spot: tuple[float, float]) -> tuple[float, float] | None:
    """The unit direction from the spot to the nearest edge of the piece, or None when the spot is on its edge."""
    edge = closest_point(footprint(node), spot)
    distance = math.dist(edge, spot)
    return None if distance < 1e-6 else ((edge[0] - spot[0]) / distance, (edge[1] - spot[1]) / distance)


def _shortfall_inches(finding: Finding) -> float:
    assert finding.required_inches is not None
    return finding.required_inches - (finding.measured_inches or 0.0)


def built_ins_apart_moves(graph: SceneGraph, finding: Finding, fixtures: set[UUID],
                          max_meters: float) -> list[Candidate]:
    """Every built-in the finding names pushed straight away from the problem's spot at once, least moved first.

    A restroom turning circle that falls short between two partition panels
    needs both of them moved: sliding either alone leaves the circle as short
    as it was, so the gate refuses each single slide. Each push is a share of
    the shortfall, capped at `max_meters`.
    """
    if finding.locus is None or finding.locus.point is None or finding.required_inches is None:
        return []
    named = [node for node in graph.nodes if node.id in fixtures and node.id in finding.locus.node_ids]
    spot = (finding.locus.point.x, finding.locus.point.y)
    headings = {node.id: heading for node in named if (heading := _away_from(node, spot)) is not None}
    if len(named) < 2 or len(headings) < len(named):
        return []
    pushes = sorted({min(max_meters, to_meters(_shortfall_inches(finding) * share + APART_MARGIN_INCHES))
                     for share in APART_SHARES})
    return [Candidate("push_built_ins_apart", [
        NodeMove(node_id=node_id, delta_translation=Vec3(x=x * meters, y=y * meters, z=0.0))
        for node_id, (x, y) in headings.items()], meters * len(headings)) for meters in pushes]
