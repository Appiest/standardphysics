"""Rearranging: check a layout while it is being dragged, and save one.

Moves go through Lane C's `apply_moves` and `violations`, the same hard
constraints the fix agent works under, so a drag can never save something the
agent would reject. The owner may also drag a built-in fixture such as a
counter. That is construction, not rearranging, so it is held to the rules for a
relocated fixture instead of being refused, and whatever sits on a moved piece
goes with it.
"""

from __future__ import annotations

import uuid

from standardphysics_agents.fix import apply_moves, carried_along, relocation_violations, violations
from standardphysics_contracts import (
    Blocked,
    LayoutCheckRequest,
    LayoutCheckResult,
    NodeMove,
    SaveLayoutRequest,
    SceneGraph,
    SceneNode,
    bounds_the_room,
    graph_hash,
)

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .stages import Stages
from .worker import ASSESS, Worker

STALE_LAYOUT = "a newer layout was saved since this one started"


def _base(database: Database, scan_id: uuid.UUID, base_revision: int):
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, base_revision)
        latest = repo.get_revision(connection, scan_id)
        scenario = repo.get_scenario(connection, scan_id)
    if row is None:
        raise ApiProblem(404, "no such revision")
    return repo.graph_of(row), latest["revision"], scenario


def _candidate(base: SceneGraph, moves: list[NodeMove]) -> tuple[SceneGraph, list[Blocked]]:
    known = {node.id for node in base.nodes}
    unknown = sorted(str(move.node_id) for move in moves if move.node_id not in known)
    if unknown:
        raise ApiProblem(400, "unknown node", need=unknown)
    moves = carried_along(base, moves)
    relocated = {move.node_id for move in moves if _is_fixture(base.by_id(move.node_id))}
    built = apply_moves(base, [move for move in moves if move.node_id in relocated])
    candidate = apply_moves(built, [move for move in moves if move.node_id not in relocated])
    broken = [*violations(built, candidate), *relocation_violations(base, candidate, relocated)]
    blocked = [Blocked(node_id=v.node_id, reason=v.kind, detail=v.detail) for v in broken]
    return candidate.model_copy(update={"revision": base.revision + 1}), blocked


def _is_fixture(node: SceneNode) -> bool:
    """Built in and standing in the room, like a counter. Walls and doors stay where they are."""
    return not node.movable and not bounds_the_room(node)


def check_layout(database: Database, stages: Stages, scan_id: uuid.UUID, body: LayoutCheckRequest) -> LayoutCheckResult:
    base, _, scenario = _base(database, scan_id, body.base_revision)
    candidate, blocked = _candidate(base, body.moves)
    findings = stages.assess(candidate, scenario, candidate.revision + 1).findings
    return LayoutCheckResult(
        sequence=body.sequence, graph_hash=graph_hash(candidate), findings=findings, blocked=blocked
    )


def save_layout(database: Database, worker: Worker, scan_id: uuid.UUID, body: SaveLayoutRequest) -> SceneGraph:
    base, _, _ = _base(database, scan_id, body.base_revision)
    candidate, blocked = _candidate(base, body.moves)
    if blocked:
        raise ApiProblem(409, "that layout breaks a hard constraint", need=[b.detail for b in blocked])
    saved = candidate.model_copy(update={"revision": body.base_revision + 1})
    with database.transaction() as connection:
        latest = repo.get_revision(connection, scan_id)["revision"]
        if latest != body.base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        repo.save_revision(connection, saved, source="owner", base_revision=body.base_revision)
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved
