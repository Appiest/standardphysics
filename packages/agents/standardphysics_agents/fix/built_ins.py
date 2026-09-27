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
"""

from __future__ import annotations

from uuid import UUID

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters
from standardphysics_pipeline import footprint, gap_between

from .nudges import directions_of, not_toward
from .strategies import Candidate

TOUCHING_METERS = 0.05
"""Five centimetres. Two built-ins this close read as one run, as `checks/service_counter.py` reads them."""
SET_SLIDE_INCHES = (6.0, 12.0, 24.0)


def _touching(graph: SceneGraph, node: SceneNode, fixtures: set[UUID]) -> list[SceneNode]:
    shape = footprint(node)
    return [other for other in graph.nodes
            if other.id in fixtures and other.id != node.id and gap_between(footprint(other), shape) <= TOUCHING_METERS]


def built_in_set_moves(graph: SceneGraph, finding: Finding, fixtures: set[UUID]) -> list[Candidate]:
    """Each named built-in slid with the built-ins touching it, shortest slide first.

    A named built-in already carried in an earlier one's run is not slid again as a run of its own.
    """
    named = [node for node in graph.nodes if node.id in fixtures and finding.locus and node.id in finding.locus.node_ids]
    found, carried = [], set()
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
