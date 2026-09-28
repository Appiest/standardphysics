"""Asking Lane C's fix agent for a layout that clears a finding."""

from __future__ import annotations

import uuid

from standardphysics_agents.fix.budget import deadline_in
from standardphysics_contracts import OwnerWish, ProposalRequest, ProposalResult, SpaceTypology

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .model_chooser import MENU_SECONDS, SEARCH_AFTER_MENU_SECONDS, ModelChooser
from .rearrangement_base import rearrangement_base
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
        graph = rearrangement_base(connection, row)
    return graph, scenario, assessment


def space_typology_of(database: Database, scan_id: uuid.UUID) -> SpaceTypology | None:
    with database.connect() as connection:
        return repo.space_typology(connection, scan_id)


def owner_wishes_of(database: Database, scan_id: uuid.UUID) -> list[OwnerWish]:
    with database.connect() as connection:
        return repo.owner_wishes(connection, scan_id)


def _model_outcome(stages: Stages, graph, scenario, targets, chooser: ModelChooser, typology, wishes):
    """The model's pick from a menu built within `MENU_SECONDS`, else the search within `SEARCH_AFTER_MENU_SECONDS`.

    The menu has already measured the search's own slides and placements for
    these findings, so the search after it only gets a short budget of its own.
    """
    picked = stages.model_proposal(graph, scenario, targets, chooser, typology, wishes,
                                   deadline=deadline_in(MENU_SECONDS))
    return picked or stages.propose(graph, scenario, targets, typology, wishes,
                                    deadline=deadline_in(SEARCH_AFTER_MENU_SECONDS))


def propose(database: Database, stages: Stages, scan_id: uuid.UUID, body: ProposalRequest) -> ProposalResult:
    graph, scenario, assessment = fix_inputs(database, scan_id, body.base_revision)
    wanted = set(body.finding_ids)
    targets = [finding for finding in assessment.findings if finding.id in wanted]
    if len(targets) != len(wanted):
        raise ApiProblem(400, "unknown finding", need=sorted(str(i) for i in wanted - {f.id for f in targets}))
    wishes, typology = owner_wishes_of(database, scan_id), space_typology_of(database, scan_id)
    chooser = ModelChooser.from_environment()
    outcome = _model_outcome(stages, graph, scenario, targets, chooser, typology, wishes) if chooser else None
    outcome = outcome or stages.propose(graph, scenario, targets, typology, wishes)
    explanation = stages.explain(graph, outcome.graph, scenario, wishes) if outcome.graph is not None else None
    return ProposalResult(
        base_revision=body.base_revision,
        proposal=outcome.proposal,
        message=outcome.message,
        question=outcome.relaxation.question if outcome.relaxation else None,
        explanation=explanation,
    )
