"""Queued model work on a scan: rearrangement suggestions, simulations and a rebuild from the photos."""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from standardphysics_contracts import (
    RearrangementRequest,
    RearrangementStatus,
    RebuildRequest,
    SceneGraph,
    SimulationRequest,
    SimulationStatus,
    graph_hash,
)

from . import repository_jobs as jobs_repo
from . import repository_revisions as revisions_repo
from .budgets import admit_new_job
from .db import Database
from .errors import ApiProblem
from .rearrangement import queue_suggestion, suggestion_status
from .rearrangement_data import record_outcome
from .simulations import queue_simulation, simulation_status
from .stages import Stages
from .worker import Worker
from .worker_handlers import ASSESS


def install_rearrangement_routes(app: FastAPI, database: Database, worker: Worker) -> None:
    path = "/api/scans/{scan_id}/rearrangement-suggestion"

    @app.post(path, response_model=RearrangementStatus, status_code=202)
    def suggest_rearrangement(scan_id: uuid.UUID, body: RearrangementRequest) -> RearrangementStatus:
        """Queue a model suggestion. Never save a layout automatically."""
        return queue_suggestion(database, worker, worker.rearranger, scan_id, body)

    @app.get(path, response_model=RearrangementStatus)
    def rearrangement_suggestion(scan_id: uuid.UUID, revision: int) -> RearrangementStatus:
        """Whether suggestions are available, and the latest one for this revision."""
        return suggestion_status(database, worker.rearranger, scan_id, revision)

    @app.post(path + "/{suggestion_id}/put-back")
    def put_back_suggestion(scan_id: uuid.UUID, suggestion_id: str, revision: int) -> dict:
        with database.transaction() as connection:
            if not record_outcome(connection, scan_id, revision, suggestion_id, "put_back"):
                raise ApiProblem(404, "no such suggestion")
        return {}


def install_simulation_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.post("/api/scans/{scan_id}/simulations", response_model=SimulationStatus, status_code=202)
    def start_simulation(scan_id: uuid.UUID, body: SimulationRequest) -> SimulationStatus:
        limit = worker.settings.max_queued_jobs
        return queue_simulation(database, stages, worker, scan_id, body, max_queued_jobs=limit)

    @app.get("/api/scans/{scan_id}/simulations", response_model=SimulationStatus)
    def get_simulation(scan_id: uuid.UUID, revision: int) -> SimulationStatus:
        return simulation_status(database, scan_id, revision)

    @app.post("/api/scans/{scan_id}/rebuild", response_model=SceneGraph, status_code=201)
    def rebuild(scan_id: uuid.UUID, body: RebuildRequest) -> SceneGraph:
        from .layout import STALE_LAYOUT, _base
        base, latest, _ = _base(database, scan_id, body.base_revision)
        if latest != body.base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        frame_paths, poses_path, lidar_mesh_path = worker.label_inputs(scan_id)
        with database.connect() as connection:
            captured_row = revisions_repo.get_revision(connection, scan_id, 0)
        captured = revisions_repo.graph_of(captured_row) if captured_row else None
        rebuilt = stages.label_scan(
            base, frame_paths=frame_paths, poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path, capture_graph=captured,
        ).model_copy(update={"revision": base.revision + 1, "base_hash": graph_hash(base)})
        with database.transaction() as connection:
            if revisions_repo.latest_revision_number(connection, scan_id) != base.revision:
                raise ApiProblem(409, STALE_LAYOUT)
            admit_new_job(connection, worker.settings.max_queued_jobs)
            revisions_repo.save_revision(connection, rebuilt, source="rebuild", base_revision=base.revision)
            jobs_repo.enqueue_job(connection, scan_id, ASSESS, rebuilt.revision)
        worker.wake()
        return rebuilt
