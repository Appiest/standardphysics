"""Guesses that carry a table and its seats together, so the seats stay at their table.

An owner who put four stools round a bar table wants them there. Every other
guess moves pieces one by one, so clearing a turning circle beside that table
pulls a stool away from it, and the owner turns the change down. Moving the
whole set by the same slide keeps each seat exactly where it was relative to
its table, and a set can often move where no single piece of it could. Which
pieces belong together is the caller's to say (`groups`), since it depends on
reading the room's layout rather than on geometry. A set is only slid away
from the problem's spot or across it, never toward it, since carrying a table
into the space it already crowds cannot open that space.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from standardphysics_contracts import Finding, NodeMove, SceneGraph, SceneNode, Vec3, to_meters

from .nudges import directions_of, not_toward
from .strategies import Candidate

GROUP_SLIDE_INCHES = (6.0, 12.0, 18.0, 24.0, 36.0)


def _groups_named(finding: Finding, groups: Iterable[frozenset[UUID]], movable: set[UUID]) -> list[frozenset[UUID]]:
    """The sets with a member the finding names, each only when every member may move."""
    named = set(finding.locus.node_ids) if finding.locus else set()
    return [group for group in dict.fromkeys(groups) if len(group) > 1 and group & named and group <= movable]


def _largest(graph: SceneGraph, group: frozenset[UUID]) -> SceneNode:
    """The set's biggest piece, normally the table, whose sides the slides follow."""
    members = [node for node in graph.nodes if node.id in group]
    return max(members, key=lambda node: node.dimensions.x * node.dimensions.y)


def group_moves(graph: SceneGraph, finding: Finding, groups: Iterable[frozenset[UUID]],
                pinned=frozenset()) -> list[Candidate]:
    """Every named set slid as one along its table's sides and diagonals, least moved first."""
    movable = {node.id for node in graph.nodes if node.movable and node.id not in pinned}
    found = []
    for group in _groups_named(finding, groups, movable):
        members = sorted(group, key=str)
        table = _largest(graph, group)
        for x, y in not_toward(directions_of(table), table, finding):
            for inches in GROUP_SLIDE_INCHES:
                meters = to_meters(inches)
                moves = [NodeMove(node_id=node_id, delta_translation=Vec3(x=x * meters, y=y * meters, z=0.0))
                         for node_id in members]
                found.append(Candidate("move_the_set", moves, meters * len(moves)))
    return sorted(found, key=lambda candidate: candidate.disruption)
