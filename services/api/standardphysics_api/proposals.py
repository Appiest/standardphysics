"""Asking Lane C's fix agent for a layout that clears a finding."""

from __future__ import annotations

import uuid

from standardphysics_contracts import ProposalRequest, ProposalResult

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .stages import Stages


def fix_inputs(database: Database, scan_id: uuid.UUID, revision: int):
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, revision)
        scenario = repo.get_scenario(connection, scan_id)
        assessment = repo.assessment_for_revision(connection, scan_id, revision)
    if row is None or scenario is None or assessment is None:
        raise ApiProblem(404, "not ready")
    return repo.graph_of(row), scenario, assessment


def propose(database: Database, stages: Stages, scan_id: uuid.UUID, body: ProposalRequest) -> ProposalResult:
    graph, scenario, assessment = fix_inputs(database, scan_id, body.base_revision)
    wanted = set(body.finding_ids)
    targets = [finding for finding in assessment.findings if finding.id in wanted]
    if len(targets) != len(wanted):
        raise ApiProblem(400, "unknown finding", need=sorted(str(i) for i in wanted - {f.id for f in targets}))
    outcome = stages.propose(graph, scenario, targets)
    return ProposalResult(
        base_revision=body.base_revision,
        proposal=outcome.proposal,
        message=outcome.message,
        question=outcome.relaxation.question if outcome.relaxation else None,
    )
