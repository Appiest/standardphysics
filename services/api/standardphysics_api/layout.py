"""Rearranging: check a layout while it is being dragged, and save one.

Moves go through Lane C's `apply_moves` and `violations`, the same hard
constraints the fix agent works under, so a drag can never save something the
agent would reject. Whatever sits on a moved piece goes with it. While
planning, the owner may also drag a built-in fixture such as a counter: that is
construction, held to the rules for a relocated fixture instead of refused. A
saved layout says what the room is now, and construction has not happened yet,
so saving still refuses a moved fixture.
"""

from __future__ import annotations

import uuid

from standardphysics_agents.fix import apply_moves, carried_along, relocation_violations, violations
from standardphysics_agents.tracing import suspend_tracing
from standardphysics_agents.training.checker import trusted_where_moved
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
from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .budgets import admit_new_job
from .db import Database
from .errors import ApiProblem
from .rearrangement_base import rearrangement_base
from .rearrangement_data import record_outcome, suggested_hash
from .stages import Stages
from .worker import Worker
from .worker_handlers import ASSESS

STALE_LAYOUT = "a newer layout was saved since this one started"


def _base(database: Database, scan_id: uuid.UUID, base_revision: int):
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = revisions_repo.get_revision(connection, scan_id, base_revision)
        if row is None:
            raise ApiProblem(404, "no such revision")
        latest = revisions_repo.latest_revision_number(connection, scan_id)
        scenario = revisions_repo.get_scenario(connection, scan_id)
        base = rearrangement_base(connection, row)
    return base, latest, scenario


def plan_candidate(
    base: SceneGraph, moves: list[NodeMove], construction: bool = False
) -> tuple[SceneGraph, list[Blocked]]:
    known = {node.id for node in base.nodes}
    unknown = sorted(str(move.node_id) for move in moves if move.node_id not in known)
    if unknown:
        raise ApiProblem(400, "unknown node", need=unknown)
    moves = carried_along(base, moves)
    relocated = {move.node_id for move in moves if construction and _is_fixture(base.by_id(move.node_id))}
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
    return plan_check(stages, base, body.moves, scenario, body.sequence)


def plan_check(
    stages: Stages, base: SceneGraph, moves: list[NodeMove], scenario, sequence: int = 0
) -> LayoutCheckResult:
    """The plan's what-if. A piece the owner put somewhere the scan never saw it is taken as measured, so a pinch
    it causes is a problem to fix by moving things; pieces left where they were keep the report's second look.

    A drag asks for one of these every time a piece comes to rest, so its checks are not sent to Weave, as in the
    model loop: tracing them made one check of an 84-piece shop take seconds instead of 0.3 s."""
    candidate, blocked = plan_candidate(base, moves, construction=True)
    with suspend_tracing():
        findings = stages.assess(trusted_where_moved(base, candidate), scenario, candidate.revision + 1).findings
    return LayoutCheckResult(sequence=sequence, graph_hash=graph_hash(candidate), findings=findings, blocked=blocked)


def save_layout(database: Database, worker: Worker, scan_id: uuid.UUID, body: SaveLayoutRequest) -> SceneGraph:
    base, _, _ = _base(database, scan_id, body.base_revision)
    candidate, blocked = plan_candidate(base, body.moves)
    if blocked:
        raise ApiProblem(409, "that layout breaks a hard constraint", need=[b.detail for b in blocked])
    saved = candidate.model_copy(update={"revision": body.base_revision + 1})
    with database.transaction() as connection:
        latest = revisions_repo.latest_revision_number(connection, scan_id)
        if latest != body.base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        original_suggestion_hash = None
        if body.suggestion_id:
            original_suggestion_hash = suggested_hash(connection, scan_id, body.base_revision, body.suggestion_id)
            if original_suggestion_hash is None:
                raise ApiProblem(409, "that suggestion is no longer available")
        admit_new_job(connection, worker.settings.max_queued_jobs)
        revisions_repo.save_revision(connection, saved, source="owner", base_revision=body.base_revision)
        if body.suggestion_id:
            recorded_hash = graph_hash(saved)
            record_outcome(connection, scan_id, body.base_revision, body.suggestion_id, "saved",
                           {"saved_graph_hash": recorded_hash,
                            "modified": recorded_hash != original_suggestion_hash})
        jobs_repo.enqueue_job(connection, scan_id, ASSESS, saved.revision)
    worker.wake()
    return saved
