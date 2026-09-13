"""The HTTP surface. Upload paths and status codes follow services/api/openapi.json."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import uuid
from typing import Annotated

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from standardphysics_agents import init_tracing, project_url, shutdown_tracing
from standardphysics_contracts import (
    Artifact,
    ArtifactKind,
    AskAnswer,
    AskRequest,
    Assessment,
    CreateScanRequest,
    LayoutCheckRequest,
    LayoutCheckResult,
    LoopRequest,
    LoopResult,
    ProposalRequest,
    ProposalResult,
    Report,
    SaveLayoutRequest,
    Scan,
    ScanList,
    Scenario,
    SceneGraph,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import repository as repo
from .coverage import parse_coverage
from .db import Database
from .errors import ApiProblem
from .labels import mark_counter, unmark_counter
from .layout import check_layout, save_layout
from .simulations import queue_simulation, simulation_status
from .replays import install_replay_routes
from standardphysics_contracts import RebuildRequest, SimulationRequest, SimulationStatus, graph_hash
from .lidar_mesh import InvalidLidarMesh, validate_lidar_mesh
from .loop_run import run as run_loop_on
from .proposals import propose
from .questions import answer_question
from .report import build_report
from .route import confirm, suggestion
from .seed import seed_sample_shop
from .settings import Settings
from .stages import Stages, preview_ledger
from .store import ArtifactStore, ArtifactTooLarge, InvalidArtifactId
from .worker import ASSESS, PROCESS, Worker

log = logging.getLogger(__name__)


def _start_tracing(settings: Settings) -> None:
    """Weave sees the whole run: seeding, uploads, checks and every model call."""
    if init_tracing(settings.weave_project, settings.weave_entity):
        log.info("this run is traced to %s", project_url())


def _problem_response(exc: ApiProblem) -> JSONResponse:
    return JSONResponse(exc.body.model_dump(exclude_none=True), status_code=exc.status)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def api_problem(_: Request, exc: ApiProblem):
        return _problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        return _problem_response(ApiProblem(400, "invalid request", need=fields))

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(_: Request, exc: StarletteHTTPException):
        return _problem_response(ApiProblem(exc.status_code, str(exc.detail).lower()))


def create_app(settings: Settings | None = None, stages: Stages | None = None, run_worker: bool = True) -> FastAPI:
    settings = settings or Settings.from_environment()
    if stages is None:
        stages = Stages(ledger_factory=preview_ledger) if settings.preview_unverified_rules else Stages()
    database = Database(settings.database_path)
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes)
    worker = Worker(database, store, stages)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.preview_unverified_rules:
            log.warning("SP_PREVIEW_UNVERIFIED_RULES is on: findings come from rules no person has verified")
        _start_tracing(settings)
        if settings.seed_sample_shop:
            seed_sample_shop(database, store)
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()
        shutdown_tracing()

    app = FastAPI(title="Standard Physics API", version="0.1.0", lifespan=lifespan)
    app.state.database, app.state.store, app.state.worker = database, store, worker
    _install_error_handlers(app)
    _install_scan_routes(app, database, store)
    _install_upload_routes(app, database, store, worker)
    _install_workspace_routes(app, database, store)
    _install_file_routes(app, database, store)
    _install_layout_routes(app, database, stages, worker)
    _install_route_routes(app, database, worker)
    _install_simulation_routes(app, database, stages, worker)
    install_replay_routes(app, database, store)
    _install_label_routes(app, database, worker)

    @app.get("/api/scans/{scan_id}/report", response_model=Report)
    def report(scan_id: uuid.UUID) -> Report:
        return build_report(database, stages.ledger_factory(), scan_id)

    return app


def _scan_or_404(connection, scan_id: uuid.UUID) -> Scan:
    scan = repo.get_scan(connection, scan_id)
    if scan is None:
        raise ApiProblem(404, "no scan")
    return scan


def _install_scan_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.post("/api/scans", status_code=201, response_model=Scan)
    def create_scan(body: CreateScanRequest) -> Scan:
        with database.transaction() as connection:
            scan_id = repo.insert_scan(connection, body)
            return repo.get_scan(connection, scan_id)

    @app.get("/api/scans", response_model=ScanList)
    def list_scans() -> ScanList:
        with database.connect() as connection:
            return ScanList(scans=repo.list_scans(connection))

    @app.get("/api/scans/{scan_id}", response_model=Scan)
    def get_scan(scan_id: uuid.UUID) -> Scan:
        with database.connect() as connection:
            return _scan_or_404(connection, scan_id)

    @app.delete("/api/scans/{scan_id}", status_code=204)
    def delete_scan(scan_id: uuid.UUID) -> Response:
        """Remove a scan and everything stored for it.

        The rows go first and the files second. A stored file with no scan row
        is invisible and reclaimable; a scan row whose files have gone is a
        listing that breaks the moment anyone opens it.
        """
        with database.transaction() as connection:
            _scan_or_404(connection, scan_id)
            running = connection.execute("SELECT 1 FROM jobs WHERE scan_id=? AND state='running'", (str(scan_id),)).fetchone()
            if running:
                raise ApiProblem(409, "Wait for this room's running job to finish before deleting it")
            repo.delete_scan(connection, scan_id)
        store.remove_scan(scan_id)
        return Response(status_code=204)


def _accept_staged(database, store, scan_id, artifact_id, kind, claimed, staged) -> tuple[int, Artifact]:
    if staged.sha256 != claimed.lower():
        store.discard(staged)
        raise ApiProblem(400, "checksum mismatch")
    with database.transaction() as connection:
        _scan_or_404(connection, scan_id)
        existing = repo.find_artifact(connection, scan_id, artifact_id)
        if existing is not None:
            store.discard(staged)
            if existing.kind != kind:
                raise ApiProblem(409, "artifact already stored with different kind")
            if existing.sha256 != staged.sha256:
                raise ApiProblem(409, "artifact already stored with different content")
            return 200, existing
        artifact = Artifact(id=artifact_id, kind=kind, sha256=staged.sha256, bytes=staged.bytes)
        repo.insert_artifact(connection, scan_id, artifact)
        store.commit(staged, store.artifact_path(scan_id, artifact_id))
        return 201, artifact


def _finalize(database: Database, store: ArtifactStore, scan_id: uuid.UUID) -> tuple[Scan, bool]:
    with database.transaction() as connection:
        scan = _scan_or_404(connection, scan_id)
        if scan.state == "failed":
            repo.retry_failed_jobs(connection, scan_id)
            return repo.get_scan(connection, scan_id), True
        if scan.state != "uploading":
            return scan, False
        missing = repo.missing_required(scan)
        if missing:
            raise ApiProblem(409, "missing artifacts", need=missing)
        coverage_artifact = next((a for a in scan.artifacts if a.kind == "coverage"), None)
        coverage = (
            parse_coverage(store.artifact_path(scan_id, coverage_artifact.id).read_bytes())
            if coverage_artifact else []
        )
        repo.mark_finalized(connection, scan, coverage)
        repo.enqueue_job(connection, scan_id, PROCESS, 0)
        return repo.get_scan(connection, scan_id), True


def _install_upload_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    @app.put("/api/scans/{scan_id}/artifacts/{artifact_id}", response_model=Artifact, status_code=201)
    async def upload_artifact(
        scan_id: uuid.UUID,
        artifact_id: str,
        request: Request,
        x_checksum_sha256: Annotated[str, Header()],
        x_artifact_kind: Annotated[ArtifactKind, Header()],
    ):
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
        try:
            store.artifact_path(scan_id, artifact_id)
            staged = await store.stage(scan_id, request.stream())
        except InvalidArtifactId:
            raise ApiProblem(400, "invalid artifact id") from None
        except ArtifactTooLarge:
            raise ApiProblem(413, "artifact too large") from None
        if x_artifact_kind == "lidar_mesh":
            try:
                validate_lidar_mesh(staged.temp_path.read_bytes())
            except InvalidLidarMesh:
                store.discard(staged)
                raise ApiProblem(400, "invalid lidar mesh") from None
        status, artifact = _accept_staged(
            database, store, scan_id, artifact_id, x_artifact_kind, x_checksum_sha256, staged
        )
        return JSONResponse(artifact.model_dump(mode="json"), status_code=status)

    @app.post("/api/scans/{scan_id}/complete", response_model=Scan)
    def complete(scan_id: uuid.UUID) -> Scan:
        scan, queued = _finalize(database, store, scan_id)
        if queued:
            worker.wake()
        return scan


def _file_or_404(path: str | pathlib.Path | None, media_type: str) -> FileResponse:
    if path is None or not pathlib.Path(path).is_file():
        raise ApiProblem(404, "not ready")
    return FileResponse(path, media_type=media_type)


def _install_workspace_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.get("/api/scans/{scan_id}/scene", response_model=SceneGraph)
    def scene(scan_id: uuid.UUID, revision: int | None = None) -> SceneGraph:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            row = repo.get_revision(connection, scan_id, revision)
        if row is None:
            raise ApiProblem(404, "not ready")
        return repo.graph_of(row)

    @app.get("/api/scans/{scan_id}/scenario", response_model=Scenario)
    def scenario(scan_id: uuid.UUID) -> Scenario:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            found = repo.get_scenario(connection, scan_id)
        if found is None:
            raise ApiProblem(404, "not ready")
        return found

    @app.get("/api/scans/{scan_id}/assessment", response_model=Assessment)
    def assessment(scan_id: uuid.UUID, revision: int | None = None) -> Assessment:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            found = (
                repo.latest_assessment(connection, scan_id)
                if revision is None
                else repo.assessment_for_revision(connection, scan_id, revision)
            )
        if found is None:
            raise ApiProblem(404, "not ready")
        return found



def _install_layout_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.post("/api/scans/{scan_id}/layout-checks", response_model=LayoutCheckResult)
    def layout_check(scan_id: uuid.UUID, body: LayoutCheckRequest) -> LayoutCheckResult:
        return check_layout(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/ask", response_model=AskAnswer)
    def ask_about_the_shop(scan_id: uuid.UUID, body: AskRequest) -> AskAnswer:
        return answer_question(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/loop", response_model=LoopResult)
    def fix_what_it_can(scan_id: uuid.UUID, body: LoopRequest) -> LoopResult:
        return run_loop_on(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/proposals", response_model=ProposalResult)
    def proposal(scan_id: uuid.UUID, body: ProposalRequest) -> ProposalResult:
        return propose(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/revisions", response_model=SceneGraph, status_code=201)
    def save_revision(scan_id: uuid.UUID, body: SaveLayoutRequest) -> SceneGraph:
        return save_layout(database, worker, scan_id, body)


def _install_label_routes(app: FastAPI, database: Database, worker: Worker) -> None:
    counter_path = "/api/scans/{scan_id}/revisions/{base_revision}/counters/{node_id}"

    @app.put(counter_path, response_model=SceneGraph, status_code=201)
    def mark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return mark_counter(database, worker, scan_id, base_revision, node_id)

    @app.delete(counter_path, response_model=SceneGraph, status_code=201)
    def unmark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return unmark_counter(database, worker, scan_id, base_revision, node_id)


def _install_route_routes(app: FastAPI, database: Database, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/scenario/suggestion", response_model=Scenario)
    def scenario_suggestion(scan_id: uuid.UUID) -> Scenario:
        return suggestion(database, scan_id)

    @app.put("/api/scans/{scan_id}/scenario", response_model=Scenario)
    def confirm_scenario(scan_id: uuid.UUID, body: Scenario) -> Scenario:
        return confirm(database, worker, scan_id, body)


def _install_file_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.head("/api/scans/{scan_id}/scene.glb")
    @app.get("/api/scans/{scan_id}/scene.glb")
    def scene_glb(scan_id: uuid.UUID, revision: int | None = None) -> Response:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            found = repo.display_geometry(connection, scan_id, revision)
            pending = repo.display_pending(connection, scan_id)
        response = _file_or_404(found[0], "model/gltf-binary") if found else Response(status_code=404)
        if found:
            response.headers["X-Exported-Revision"] = str(found[1])
        response.headers["X-Display-Pending"] = str(pending).lower()
        return response

    @app.get("/api/scans/{scan_id}/lidar-mesh")
    def lidar_mesh(scan_id: uuid.UUID) -> FileResponse:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            artifact = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        path = store.artifact_path(scan_id, artifact.id) if artifact else None
        return _file_or_404(path, "application/json")

    @app.get("/api/scans/{scan_id}/renders/{finding_id}.png")
    def render(scan_id: uuid.UUID, finding_id: uuid.UUID) -> FileResponse:
        with database.connect() as connection:
            revision = repo.get_revision(connection, scan_id)
        if revision is None:
            raise ApiProblem(404, "not ready")
        directory = store.scan_dir(scan_id) / "revisions"
        matches = sorted(directory.glob(f"*/renders/{finding_id}.png"), key=lambda path: int(path.parent.parent.name))
        return _file_or_404(matches[-1] if matches else None, "image/png")


def _install_simulation_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.post("/api/scans/{scan_id}/simulations", response_model=SimulationStatus, status_code=202)
    def start_simulation(scan_id: uuid.UUID, body: SimulationRequest) -> SimulationStatus:
        return queue_simulation(database, stages, worker, scan_id, body)

    @app.get("/api/scans/{scan_id}/simulations", response_model=SimulationStatus)
    def get_simulation(scan_id: uuid.UUID, revision: int) -> SimulationStatus:
        return simulation_status(database, scan_id, revision)

    @app.post("/api/scans/{scan_id}/rebuild", response_model=SceneGraph, status_code=201)
    def rebuild(scan_id: uuid.UUID, body: RebuildRequest) -> SceneGraph:
        from .layout import _base, STALE_LAYOUT
        base, latest, _ = _base(database, scan_id, body.base_revision)
        if latest != body.base_revision:
            raise ApiProblem(409, STALE_LAYOUT)
        frame_paths, poses_path = worker.label_inputs(scan_id)
        rebuilt = stages.label_scan(
            base, frame_paths=frame_paths, poses_path=poses_path
        ).model_copy(update={"revision": base.revision + 1, "base_hash": graph_hash(base)})
        with database.transaction() as connection:
            if repo.get_revision(connection, scan_id)["revision"] != base.revision:
                raise ApiProblem(409, STALE_LAYOUT)
            repo.save_revision(connection, rebuilt, source="rebuild", base_revision=base.revision)
            repo.enqueue_job(connection, scan_id, ASSESS, rebuilt.revision)
        worker.wake()
        return rebuilt
