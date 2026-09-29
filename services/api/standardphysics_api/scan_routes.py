"""Creating, listing, reading and deleting scans, and the graph and findings saved for one."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request, Response
from standardphysics_contracts import Assessment, CreateScanRequest, Scan, ScanList, Scenario, SceneGraph

from . import repository as repo
from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .auth import owner_of
from .budgets import Budgets
from .db import Database
from .errors import ApiProblem
from .owner_requests import carry_answers
from .owner_routes import answered
from .route import saved as saved_scenario
from .stages import Stages
from .store import ArtifactStore


def scan_or_404(connection, scan_id: uuid.UUID) -> Scan:
    scan = repo.get_scan(connection, scan_id)
    if scan is None:
        raise ApiProblem(404, "no scan")
    return scan


def install_scan_routes(app: FastAPI, database: Database, store: ArtifactStore, budgets: Budgets) -> None:
    @app.post("/api/scans", status_code=201, response_model=Scan)
    def create_scan(body: CreateScanRequest, request: Request) -> Scan:
        owner = owner_of(request)
        with database.transaction() as connection:
            budgets.admit_scan(connection, store, owner)
            if body.replaces is not None and repo.scan_owner(connection, body.replaces) != owner.id:
                raise ApiProblem(404, "no scan")
            scan_id = repo.insert_scan(connection, body, owner.id)
            if body.replaces is not None:
                carry_answers(connection, store, body.replaces, scan_id)
            return scan_or_404(connection, scan_id)

    @app.get("/api/scans", response_model=ScanList)
    def list_scans(request: Request) -> ScanList:
        with database.connect() as connection:
            return ScanList(scans=repo.list_shops(connection, owner_of(request).id))

    @app.get("/api/scans/{scan_id}", response_model=Scan)
    def get_scan(scan_id: uuid.UUID) -> Scan:
        with database.connect() as connection:
            return scan_or_404(connection, scan_id)

    @app.delete("/api/scans/{scan_id}", status_code=204)
    def delete_scan(scan_id: uuid.UUID) -> Response:
        """Remove a scan and everything stored for it.

        The rows go first and the files second. A stored file with no scan row
        is invisible and reclaimable; a scan row whose files have gone is a
        listing that breaks the moment anyone opens it. A scan still being
        measured is hidden now and deleted by the worker when its job ends, so
        a job that never ends can't keep a shop on anyone's list.
        """
        with database.transaction() as connection:
            scan_or_404(connection, scan_id)
            if jobs_repo.other_running_job(connection, scan_id):
                repo.mark_for_deletion(connection, scan_id)
                return Response(status_code=204)
            repo.delete_scan(connection, scan_id)
        store.remove_scan(scan_id)
        return Response(status_code=204)


def install_workspace_routes(app: FastAPI, database: Database, store: ArtifactStore, stages: Stages) -> None:
    @app.get("/api/scans/{scan_id}/scene", response_model=SceneGraph)
    def scene(scan_id: uuid.UUID, revision: int | None = None) -> SceneGraph:
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
            row = revisions_repo.get_revision(connection, scan_id, revision)
        if row is None:
            raise ApiProblem(404, "not ready")
        return revisions_repo.graph_of(row)

    @app.get("/api/scans/{scan_id}/scenario", response_model=Scenario)
    def scenario(scan_id: uuid.UUID) -> Scenario:
        return saved_scenario(database, scan_id)

    @app.get("/api/scans/{scan_id}/assessment", response_model=Assessment)
    def assessment(scan_id: uuid.UUID, revision: int | None = None) -> Assessment:
        with database.connect() as connection:
            scan_or_404(connection, scan_id)
            found = (
                revisions_repo.latest_assessment(connection, scan_id)
                if revision is None
                else revisions_repo.assessment_for_revision(connection, scan_id, revision)
            )
            if found is None:
                raise ApiProblem(404, "not ready")
            return answered(connection, stages, scan_id, found)
