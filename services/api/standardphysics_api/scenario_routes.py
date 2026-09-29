"""The route through a shop an owner confirms, the wishes and space type it is checked under, and approaches."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from standardphysics_agents.scenario_suggestion import DESTINATIONS
from standardphysics_contracts import (
    ApproachReport,
    ApproachRequest,
    OwnerWishesRequest,
    RouteLegs,
    Scan,
    Scenario,
    SpaceTypologyRequest,
)

from . import repository as repo
from .approach import evaluate as evaluate_approach
from .db import Database
from .errors import ApiProblem
from .route import confirm, legs, suggestion
from .scan_routes import scan_or_404
from .stages import Stages
from .worker import Worker

PLACES = {*DESTINATIONS, "pickup"}


def _destinations(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    named = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [part for part in named if part not in PLACES]
    if unknown:
        raise ApiProblem(400, "unknown destination", need=unknown)
    return named


def install_scenario_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/scenario/suggestion", response_model=Scenario)
    def scenario_suggestion(scan_id: uuid.UUID, destinations: str | None = None) -> Scenario:
        """The café template with no `destinations`, or a path through the places named, comma separated."""
        return suggestion(database, scan_id, _destinations(destinations))

    @app.post("/api/scans/{scan_id}/scenario/legs", response_model=RouteLegs)
    def scenario_legs(scan_id: uuid.UUID, body: Scenario) -> RouteLegs:
        return legs(database, stages, scan_id, body)

    @app.put("/api/scans/{scan_id}/owner-wishes", response_model=Scan)
    def set_owner_wishes(scan_id: uuid.UUID, body: OwnerWishesRequest) -> Scan:
        """Everything the owner wants kept in this shop; every later proposal and loop is held to it."""
        with database.transaction() as connection:
            scan_or_404(connection, scan_id)
            repo.set_owner_wishes(connection, scan_id, body.wishes)
            return scan_or_404(connection, scan_id)

    @app.put("/api/scans/{scan_id}/space-type", response_model=Scan)
    def set_space_type(scan_id: uuid.UUID, body: SpaceTypologyRequest) -> Scan:
        with database.transaction() as connection:
            scan_or_404(connection, scan_id)
            repo.set_space_typology(connection, scan_id, body.space_typology)
            return scan_or_404(connection, scan_id)

    @app.put("/api/scans/{scan_id}/scenario", response_model=Scenario)
    def confirm_scenario(scan_id: uuid.UUID, body: Scenario) -> Scenario:
        return confirm(database, worker, scan_id, body)

    @app.post("/api/scans/{scan_id}/revisions/{base_revision}/approach",
              response_model=ApproachReport)
    def approach(scan_id: uuid.UUID, base_revision: int, body: ApproachRequest) -> ApproachReport:
        """One measured journey to one target for the signed-in owner.

        Wrap R's conservative evaluator: an unmeasured horizontal reach stays
        needs_verification, never clear; a reach needs a named provenance.
        """
        return evaluate_approach(database, stages, scan_id, base_revision, body)
