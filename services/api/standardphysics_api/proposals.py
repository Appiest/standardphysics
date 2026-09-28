"""Asking Lane C's fix agent for a layout that clears a finding."""

from __future__ import annotations

import logging
import uuid

from standardphysics_agents.fix import FixOutcome
from standardphysics_contracts import (
    Finding,
    OwnerWish,
    ProposalRequest,
    ProposalResult,
    Scenario,
    SceneGraph,
    SpaceTypology,
)

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .model_chooser import ModelChooser, ModelReplyError, ModelSlots
from .stages import Stages

log = logging.getLogger(__name__)


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


def space_typology_of(database: Database, scan_id: uuid.UUID) -> SpaceTypology | None:
    with database.connect() as connection:
        return repo.space_typology(connection, scan_id)


def owner_wishes_of(database: Database, scan_id: uuid.UUID) -> list[OwnerWish]:
    with database.connect() as connection:
        return repo.owner_wishes(connection, scan_id)


def _model_pick(
    stages: Stages, slots: ModelSlots, owner_id: uuid.UUID, graph: SceneGraph, scenario: Scenario,
    targets: list[Finding], typology: SpaceTypology | None, wishes: list[OwnerWish],
) -> FixOutcome | None:
    """The configured model's pick, or None when there is no model or it sent nothing usable, so the search runs."""
    chooser = ModelChooser.from_environment()
    if chooser is None:
        return None
    with slots.held(owner_id):
        try:
            return stages.model_proposal(graph, scenario, targets, chooser, typology, wishes)
        except (OSError, ModelReplyError) as error:
            log.warning("%s gave no usable pick, so the search proposes instead: %s", chooser.label, error)
            return None


def propose(
    database: Database, stages: Stages, slots: ModelSlots, owner_id: uuid.UUID, scan_id: uuid.UUID,
    body: ProposalRequest,
) -> ProposalResult:
    graph, scenario, assessment = fix_inputs(database, scan_id, body.base_revision)
    wanted = set(body.finding_ids)
    targets = [finding for finding in assessment.findings if finding.id in wanted]
    if len(targets) != len(wanted):
        raise ApiProblem(400, "unknown finding", need=sorted(str(i) for i in wanted - {f.id for f in targets}))
    wishes, typology = owner_wishes_of(database, scan_id), space_typology_of(database, scan_id)
    picked = _model_pick(stages, slots, owner_id, graph, scenario, targets, typology, wishes)
    outcome = picked or stages.propose(graph, scenario, targets, typology, wishes)
    explanation = stages.explain(graph, outcome.graph, scenario, wishes) if outcome.graph is not None else None
    return ProposalResult(
        base_revision=body.base_revision,
        proposal=outcome.proposal,
        message=outcome.message,
        question=outcome.relaxation.question if outcome.relaxation else None,
        explanation=explanation,
    )
