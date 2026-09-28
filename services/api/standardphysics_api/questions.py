"""The ask box: a question about the shop, answered by Lane C against the measured model."""

from __future__ import annotations

import uuid

from standardphysics_agents.scenario_suggestion import suggest_scenario
from standardphysics_contracts import AskAnswer, AskRequest

from . import repository as repo
from .db import Database
from .errors import ApiProblem
from .rearrangement_base import rearrangement_base
from .stages import Stages


def answer_question(database: Database, stages: Stages, scan_id: uuid.UUID, body: AskRequest) -> AskAnswer:
    with database.connect() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        row = repo.get_revision(connection, scan_id, body.base_revision)
        scenario = repo.get_scenario(connection, scan_id)
        if row is None:
            raise ApiProblem(404, "not ready")
        graph = rearrangement_base(connection, row)
    answer = stages.ask(body.text, graph, scenario or suggest_scenario(graph))
    return AskAnswer(
        text=answer.text,
        understood=answer.understood,
        kind=answer.kind,
        subjects=list(answer.subjects),
        locus=answer.locus,
        data=answer.data,
        proposal=answer.proposal,
        findings=list(answer.findings),
    )
