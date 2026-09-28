"""Rearranging: check a layout while it is being dragged, and save one.

Moves go through Lane C's `apply_moves` and `violations`, the same hard
constraints the fix agent works under, so a drag can never save something the
agent would reject.
"""

from __future__ import annotations

import uuid

from standardphysics_agents.fix import apply_moves, violations
from standardphysics_contracts import (
    Blocked,
    LayoutCheckRequest,
    LayoutCheckResult,
    NodeMove,
    SaveLayoutRequest,
    SceneGraph,
    graph_hash,
)

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .rearrangement_data import record_outcome, suggested_hash
from .stages import Stages
from .worker import ASSESS, Worker

STALE_LAYOUT = "a newer layout was saved since this one started"


def _base(database: Database, scan_id: uuid.UUID, base_revision: int):
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, base_revision)
        latest = repo.latest_revision_number(connection, scan_id)
        scenario = repo.get_scenario(connection, scan_id)
    if row is None:
        raise ApiProblem(404, "no such revision")
    return repo.graph_of(row), latest, scenario


def _candidate(base: SceneGraph, moves: list[NodeMove]) -> tuple[SceneGraph, list[Blocked]]:
    known = {node.id for node in base.nodes}
    unknown = sorted(str(move.node_id) for move in moves if move.node_id not in known)
    if unknown:
        raise ApiProblem(400, "unknown node", need=unknown)
    candidate = apply_moves(base, moves)
    blocked = [Blocked(node_id=v.node_id, reason=v.kind, detail=v.detail) for v in violations(base, candidate)]
    return candidate, blocked


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
        latest = repo.latest_revision_number(connection, scan_id)
        if latest != body.base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        original_suggestion_hash = None
        if body.suggestion_id:
            original_suggestion_hash = suggested_hash(connection, scan_id, body.base_revision, body.suggestion_id)
            if original_suggestion_hash is None:
                raise ApiProblem(409, "that suggestion is no longer available")
        repo.save_revision(connection, saved, source="owner", base_revision=body.base_revision)
        if body.suggestion_id:
            recorded_hash = graph_hash(saved)
            record_outcome(connection, scan_id, body.base_revision, body.suggestion_id, "saved",
                           {"saved_graph_hash": recorded_hash,
                            "modified": recorded_hash != original_suggestion_hash})
        repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved
