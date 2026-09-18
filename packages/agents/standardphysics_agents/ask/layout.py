"""Asking for the furniture to be moved.

"Move the seating to the back" goes through the same hard constraints and the
same measurement as a rearrangement the loop proposed on its own. There is one
checker for dragging, for words and for automatic fixes, which is the only way
the three can agree about what is allowed.

A request the owner made does not have to improve anything measurable. They
want the seating at the back. The gate's job here is to stop that breaking
something, not to argue about it.
"""

from __future__ import annotations

from standardphysics_contracts import (
    NodeMove,
    Proposal,
    Scenario,
    SceneGraph,
    SceneNode,
    Vec3,
    graph_hash,
    to_meters,
)

from ..copy import no_room_for_request, request_rationale
from ..evaluation.gate import accepts
from ..fix.constraints import violations
from ..fix.moves import apply_moves
from ..fix.search import proposal_id
from ..hashing import inventory
from ..tracing import traced
from . import subjects
from .answer import Answer, AskContext
from .directions import RELATIVE, Direction, axis_for, centroid, outward
from .locus import subject_locus
from .query import Query

DISTANCE_LADDER_INCHES = (12.0, 24.0, 36.0, 48.0, 60.0)
"""How far to try, when the owner did not say. The furthest that works wins."""


def _distances(query: Query) -> tuple[float, ...]:
    if query.distance_inches is not None:
        return (query.distance_inches,)
    return DISTANCE_LADDER_INCHES


def _slide(node: SceneNode, axis: tuple[float, float], distance: float) -> NodeMove:
    return NodeMove(
        node_id=node.id,
        delta_translation=Vec3(x=axis[0] * distance, y=axis[1] * distance, z=0.0),
    )


def _spread(
    nodes: list[SceneNode], distance: float, direction: Direction
) -> list[NodeMove]:
    middle = centroid(nodes)
    sign = 1.0 if direction == "apart" else -1.0
    return [_slide(node, outward(node, middle), distance * sign / 2) for node in nodes]


def moves_for(
    nodes: list[SceneNode], direction: Direction, scenario: Scenario, inches: float
) -> list[NodeMove]:
    distance = to_meters(inches)
    if direction in RELATIVE:
        return _spread(nodes, distance, direction)
    return [_slide(node, axis_for(direction, scenario), distance) for node in nodes]


def _proposal(
    graph: SceneGraph,
    candidate: SceneGraph,
    moves: list[NodeMove],
    direction: Direction,
    inches: float,
) -> Proposal:
    base = graph_hash(graph)
    labels = [graph.by_id(move.node_id).label for move in moves]
    return Proposal(
        id=proposal_id(base, moves),
        base_graph_hash=base,
        moves=moves,
        targets=[],
        rationale=request_rationale(direction, labels, inches),
        inventory_before=inventory(graph),
        inventory_after=inventory(candidate),
    )


@traced("ask.rearrange")
def rearrange(query: Query, context: AskContext) -> Answer:
    movable = [
        node
        for node in subjects.resolve(
            context.graph, query.subject_node_ids, query.subject_labels
        )
        if node.movable
    ]
    if not movable:
        return _nothing_to_move(query, context)

    before = context.baseline()
    best: tuple[Proposal, SceneGraph, float] | None = None
    blocked_by: str | None = None

    for inches in _distances(query):
        moves = moves_for(movable, query.direction, context.scenario, inches)
        candidate = apply_moves(context.graph, moves)
        broken = violations(context.graph, candidate)
        if broken:
            blocked_by = broken[0].blocker
            break
        after = context and _look(context, candidate)
        if not accepts(before, after, require_improvement=False).accepted:
            blocked_by = blocked_by or "walkway"
            break
        best = (
            _proposal(context.graph, candidate, moves, query.direction, inches),
            candidate,
            inches,
        )

    if best is None:
        return _no_room(query, movable, blocked_by)
    proposal, candidate, moved_inches = best
    return Answer(
        text=proposal.rationale,
        kind="REARRANGE",
        query=query,
        subjects=tuple(node.id for node in movable),
        locus=subject_locus(movable, proposal.rationale),
        data={
            "inventory_before": proposal.inventory_before,
            "inventory_after": proposal.inventory_after,
            "moved_inches": moved_inches,
            "moved_count": len(movable),
        },
        proposal=proposal,
        graph=candidate,
    )


def _look(context: AskContext, candidate: SceneGraph):
    from ..assess import assess

    return assess(
        candidate,
        context.scenario,
        context.measure,
        rules=context.rules,
        ledger=context.ledger,
        max_tier=context.max_tier,
    )


def _no_room(query: Query, movable: list[SceneNode], blocked_by: str | None) -> Answer:
    return Answer(
        text=no_room_for_request([node.label for node in movable], blocked_by),
        kind="REARRANGE",
        query=query,
        subjects=tuple(node.id for node in movable),
        locus=subject_locus(movable, "no room"),
        data={"blocked_by": blocked_by},
    )


def _nothing_to_move(query: Query, context: AskContext) -> Answer:
    named = subjects.resolve(
        context.graph, query.subject_node_ids, query.subject_labels
    )
    if named:
        return Answer(
            text="That one is built in. Tell us which of the loose pieces to move.",
            kind="REARRANGE",
            query=query,
            subjects=tuple(node.id for node in named),
            locus=subject_locus(named, named[0].label),
        )
    asked = query.subject_labels[0].casefold() if query.subject_labels else "that"
    return Answer(
        text=f"The scan has no {asked} in it. Point at the piece you mean in "
        "the plan and we will move it.",
        kind="REARRANGE",
        query=query,
    )
