"""The HTTP surface. Upload paths and status codes follow services/api/openapi.json."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import pathlib
import re
import uuid
from typing import Annotated

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image as PILImage
from pydantic import BaseModel
from standardphysics_agents import init_tracing, project_url, shutdown_tracing
from standardphysics_contracts import (
    ApproachReport,
    ApproachRequest,
    Artifact,
    ArtifactKind,
    AskAnswer,
    AskRequest,
    Assessment,
    CompleteRequest,
    CreateScanRequest,
    EvidenceStatus,
    FrameEntry,
    FrameListing,
    LayoutCheckRequest,
    LayoutCheckResult,
    LoopRequest,
    LoopResult,
    ManualMarkRequest,
    ProposalRequest,
    ProposalResult,
    RebuildRequest,
    Report,
    SaveLayoutRequest,
    Scan,
    ScanList,
    Scenario,
    SceneGraph,
    SimulationRequest,
    SimulationStatus,
    graph_hash,
)
from standardphysics_contracts.textures import FRAME_ID_PATTERN
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import accounts
from . import repository as repo
from .approach import evaluate as evaluate_approach
from .architecture_export import install_architecture_export_routes
from .auth import install_auth, owner_of
from .combine import SaveCombineRequest, save_combine
from .coverage import parse_coverage
from .db import Database
from .errors import ApiProblem
from .evidence import evidence_status_for, maybe_queue_semantic, record_closure
from .labels import mark_counter, mark_observation, review_outlet, unmark_counter
from .layout import check_layout, save_layout
from .lidar_mesh import InvalidLidarMesh, validate_lidar_mesh
from .loop_run import run as run_loop_on
from .loop_run import stream as stream_loop_on
from .proposals import propose
from .questions import answer_question
from .replays import install_replay_routes
from .report import build_report
from .route import confirm, suggestion
from .seed import seed_sample_shop
from .settings import Settings
from .simulations import queue_simulation, simulation_status
from .splats import install_splat_routes
from .stages import Stages, preview_ledger
from .store import ArtifactStore, ArtifactTooLarge, InvalidArtifactId
from .textures import install_texture_routes, maybe_queue_texture, validate_manifest
from .usdz_validation import InvalidUsdz, validate_room_usdz
from .worker import ASSESS, PROCESS, Worker

log = logging.getLogger(__name__)


def _start_tracing(settings: Settings) -> None:
    """Weave sees the whole run: seeding, uploads, checks and every model call."""
    if init_tracing(settings.weave_project, settings.weave_entity):
        log.info("this run is traced to %s", project_url())


def _seed_demo_account(database: Database, store: ArtifactStore, settings: Settings) -> None:
    """Put the sample shop behind a real account, and say how to sign in as it."""
    seed_sample_shop(database, store, settings.seed_owner_email, settings.seed_owner_password)
    log.warning(
        "sample shop seeded. Sign in as %s with password %s",
        settings.seed_owner_email,
        settings.seed_owner_password,
    )


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
    worker = Worker(database, store, stages, settings)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.preview_unverified_rules:
            log.warning("SP_PREVIEW_UNVERIFIED_RULES is on: findings come from rules no person has verified")
        _start_tracing(settings)
        with database.transaction() as connection:
            expired = accounts.drop_expired_sessions(connection)
        if expired:
            log.info("cleared %d expired session(s)", expired)
        if settings.seed_sample_shop:
            _seed_demo_account(database, store, settings)
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()
        shutdown_tracing()

    app = FastAPI(title="Standard Physics API", version="0.1.0", lifespan=lifespan)
    app.state.database, app.state.store, app.state.worker = database, store, worker
    _install_error_handlers(app)
    install_auth(app, database)
    install_architecture_export_routes(app, database)
    _install_scan_routes(app, database, store)
    _install_upload_routes(app, database, store, worker, settings)
    _install_workspace_routes(app, database, store)
    _install_combine_routes(app, database, store, worker)
    _install_file_routes(app, database, store)
    _install_layout_routes(app, database, stages, worker)
    _install_route_routes(app, database, stages, worker)
    _install_simulation_routes(app, database, stages, worker)
    install_replay_routes(app, database, store)
    install_texture_routes(app, database, store, worker)
    install_splat_routes(app, database, store)
    _install_label_routes(app, database, store, worker)

    @app.get("/api/scans/{scan_id}/report", response_model=Report)
    def report(scan_id: uuid.UUID) -> Report:
        return build_report(database, stages.ledger_factory(), scan_id)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Reachable without a session, so a load balancer can ask.

        It touches the database, because a process that is listening but cannot
        read its own scans is not healthy in any way that matters.
        """
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone()
        return {"status": "ok"}

    return app


def _scan_or_404(connection, scan_id: uuid.UUID) -> Scan:
    scan = repo.get_scan(connection, scan_id)
    if scan is None:
        raise ApiProblem(404, "no scan")
    return scan


def _install_scan_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.post("/api/scans", status_code=201, response_model=Scan)
    def create_scan(body: CreateScanRequest, request: Request) -> Scan:
        with database.transaction() as connection:
            scan_id = repo.insert_scan(connection, body, owner_of(request).id)
            return repo.get_scan(connection, scan_id)

    @app.get("/api/scans", response_model=ScanList)
    def list_scans(request: Request) -> ScanList:
        with database.connect() as connection:
            return ScanList(scans=repo.list_scans(connection, owner_of(request).id))

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
            running = connection.execute(
                "SELECT 1 FROM jobs WHERE scan_id=? AND state='running'", (str(scan_id),)
            ).fetchone()
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
        record_closure(connection, scan)
        repo.enqueue_job(connection, scan_id, PROCESS, 0)
        return repo.get_scan(connection, scan_id), True


STAGED_VALIDATORS = {
    "lidar_mesh": (validate_lidar_mesh, InvalidLidarMesh, "invalid lidar mesh"),
    "photo_manifest": (validate_manifest, ValueError, "invalid photo manifest"),
    "room_usdz": (validate_room_usdz, InvalidUsdz, "invalid usdz archive"),
}
"""Artifact kinds whose bytes are checked before they are stored: the check, what it raises, and the 400 to send."""


def _validate_staged(store: ArtifactStore, staged, kind: str) -> None:
    if kind not in STAGED_VALIDATORS:
        return
    validate, invalid, message = STAGED_VALIDATORS[kind]
    try:
        validate(staged.temp_path.read_bytes())
    except invalid:
        store.discard(staged)
        raise ApiProblem(400, message) from None


def _install_upload_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker, settings: Settings) -> None:
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
        _validate_staged(store, staged, x_artifact_kind)
        status, artifact = _accept_staged(
            database, store, scan_id, artifact_id, x_artifact_kind, x_checksum_sha256, staged
        )
        queued_semantic = False
        if x_artifact_kind in ("photo_manifest", "frames", "poses", "lidar_mesh"):
            maybe_queue_texture(database, store, worker, scan_id)
        if x_artifact_kind in repo.SEMANTIC_INPUT_KINDS:
            with database.transaction() as connection:
                scan = _scan_or_404(connection, scan_id)
                if scan.state != "uploading":
                    record_closure(connection, scan)
                    queued_semantic = maybe_queue_semantic(
                        connection, scan, PROCESS,
                        settle_seconds=settings.evidence_settle_seconds,
                    ) == "queued"
        if queued_semantic:
            worker.wake()
        return JSONResponse(artifact.model_dump(mode="json"), status_code=status)

    @app.post("/api/scans/{scan_id}/complete", response_model=Scan)
    def complete(scan_id: uuid.UUID, body: CompleteRequest | None = None) -> Scan:
        scan, queued = _finalize(database, store, scan_id)
        if not queued and scan.state != "uploading":
            with database.transaction() as connection:
                current = _scan_or_404(connection, scan_id)
                explicit = maybe_queue_semantic(
                    connection, current, PROCESS,
                    settle_seconds=settings.evidence_settle_seconds, explicit=True,
                ) == "queued"
            if explicit:
                worker.wake()
        elif queued:
            worker.wake()
        return scan

    @app.get("/api/scans/{scan_id}/evidence", response_model=EvidenceStatus)
    def evidence(scan_id: uuid.UUID) -> EvidenceStatus:
        with database.connect() as connection:
            scan = _scan_or_404(connection, scan_id)
        return evidence_status_for(database, scan)


def _file_or_404(path: str | pathlib.Path | None, media_type: str) -> FileResponse:
    if path is None or not pathlib.Path(path).is_file():
        raise ApiProblem(404, "not ready")
    return FileResponse(path, media_type=media_type)


def _jpeg_sensor_size(data: bytes) -> tuple[int, int]:
    """The stored sensor pixel dimensions declared by the frame's own header."""
    with PILImage.open(io.BytesIO(data)) as opened:
        width, height = opened.size
    return width, height


def _frame_entry(store: ArtifactStore, scan_id: uuid.UUID, artifact: Artifact) -> FrameEntry | None:
    """One listing entry, or None when the stored bytes are not a readable image."""
    try:
        width, height = _jpeg_sensor_size(store.artifact_path(scan_id, artifact.id).read_bytes())
    except (OSError, ValueError):
        return None
    return FrameEntry(
        frame_id=artifact.id,
        width=width,
        height=height,
        image_url=f"/api/scans/{scan_id}/frames/{artifact.id}",
    )


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


def _install_combine_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/rooms")
    def rooms(scan_id: uuid.UUID) -> dict:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
        manifest = store.scan_dir(scan_id) / "rooms.json"
        if not manifest.exists():
            return {"rooms": []}
        return json.loads(manifest.read_text())

    @app.post("/api/scans/{scan_id}/combine", response_model=SceneGraph, status_code=201)
    def combine(scan_id: uuid.UUID, body: SaveCombineRequest) -> SceneGraph:
        return save_combine(database, worker, scan_id, body)



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

    @app.post("/api/scans/{scan_id}/loop/stream")
    def fix_what_it_can_as_it_goes(scan_id: uuid.UUID, body: LoopRequest) -> StreamingResponse:
        lines = stream_loop_on(database, stages, scan_id, body)
        # no-transform stops a compressing proxy from holding lines back until the loop ends.
        headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
        return StreamingResponse(lines, media_type="application/x-ndjson", headers=headers)

    @app.post("/api/scans/{scan_id}/proposals", response_model=ProposalResult)
    def proposal(scan_id: uuid.UUID, body: ProposalRequest) -> ProposalResult:
        return propose(database, stages, scan_id, body)

    @app.post("/api/scans/{scan_id}/revisions", response_model=SceneGraph, status_code=201)
    def save_revision(scan_id: uuid.UUID, body: SaveLayoutRequest) -> SceneGraph:
        return save_layout(database, worker, scan_id, body)


class ReviewOutletRequest(BaseModel):
    status: str


def _install_label_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    counter_path = "/api/scans/{scan_id}/revisions/{base_revision}/counters/{node_id}"

    @app.put(counter_path, response_model=SceneGraph, status_code=201)
    def mark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return mark_counter(database, worker, scan_id, base_revision, node_id)

    @app.delete(counter_path, response_model=SceneGraph, status_code=201)
    def unmark_as_counter(scan_id: uuid.UUID, base_revision: int, node_id: uuid.UUID) -> SceneGraph:
        return unmark_counter(database, worker, scan_id, base_revision, node_id)

    outlet_review_path = "/api/scans/{scan_id}/revisions/{base_revision}/outlets/{node_id}/review"

    @app.put(outlet_review_path, response_model=SceneGraph, status_code=201)
    def update_outlet_review(
        scan_id: uuid.UUID,
        base_revision: int,
        node_id: uuid.UUID,
        body: ReviewOutletRequest,
    ) -> SceneGraph:
        return review_outlet(database, worker, scan_id, base_revision, node_id, body.status)

    @app.put("/api/scans/{scan_id}/revisions/{base_revision}/observations",
             response_model=SceneGraph, status_code=201)
    def add_observation(
        scan_id: uuid.UUID,
        base_revision: int,
        body: ManualMarkRequest,
        request: Request,
    ) -> SceneGraph:
        """A person marks photo evidence for a target the pipeline did not find.

        The middleware settled ownership; the actor is the signed-in owner.
        A mark with a node attaches to that node's evidence; without one it is
        stored unlocalized on the graph, never given an invented position.
        """
        return mark_observation(
            database, store, worker, scan_id, base_revision, body, owner_of(request).email
        )


def _install_route_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/scenario/suggestion", response_model=Scenario)
    def scenario_suggestion(scan_id: uuid.UUID) -> Scenario:
        return suggestion(database, scan_id)

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

    @app.get("/api/scans/{scan_id}/crops/{crop_id}")
    def get_crop(scan_id: uuid.UUID, crop_id: str) -> FileResponse:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
        if "/" in crop_id or "\\" in crop_id or ".." in crop_id:
            raise ApiProblem(400, "invalid crop id")
        filename = crop_id if (crop_id.endswith(".jpg") or crop_id.endswith(".png")) else f"{crop_id}.jpg"
        crops_dir = (store.scan_dir(scan_id) / "crops").resolve()
        crop_path = (crops_dir / filename).resolve()
        if not str(crop_path).startswith(str(crops_dir)):
            raise ApiProblem(400, "invalid crop path")
        if not crop_path.is_file():
            raise ApiProblem(404, "crop not found")
        media_type = "image/png" if filename.endswith(".png") else "image/jpeg"
        return FileResponse(crop_path, media_type=media_type)

    @app.get("/api/scans/{scan_id}/frames", response_model=FrameListing)
    def frames(scan_id: uuid.UUID) -> FrameListing:
        """The stored source-resolution frames a photo review can open.

        Authenticated by the same ownership middleware as every other scan
        route. Each entry names one ACTUAL stored frame artifact; a scan with
        no frames returns an empty list, never invented identities. A stored
        artifact whose bytes are not a readable image is reported by id under
        `unreadable` instead of failing the whole listing.
        """
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            stored = repo.artifacts_of_kind(connection, scan_id, "frames")
        entries: list[FrameEntry] = []
        unreadable: list[str] = []
        for artifact in stored:
            entry = _frame_entry(store, scan_id, artifact)
            if entry is None:
                unreadable.append(artifact.id)
            else:
                entries.append(entry)
        return FrameListing(frames=entries, unreadable=unreadable)

    @app.get("/api/scans/{scan_id}/frames/{frame_id}")
    def frame_bytes(scan_id: uuid.UUID, frame_id: str) -> Response:
        """The original bytes of one stored frame, never a downscaled copy."""
        if not re.fullmatch(FRAME_ID_PATTERN, frame_id):
            raise ApiProblem(400, "invalid frame id")
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            artifact = repo.find_artifact(connection, scan_id, frame_id)
        if artifact is None or artifact.kind != "frames":
            raise ApiProblem(404, "frame not found")
        return Response(
            content=store.artifact_path(scan_id, artifact.id).read_bytes(),
            media_type="image/jpeg",
        )


def _install_simulation_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
    @app.post("/api/scans/{scan_id}/simulations", response_model=SimulationStatus, status_code=202)
    def start_simulation(scan_id: uuid.UUID, body: SimulationRequest) -> SimulationStatus:
        return queue_simulation(database, stages, worker, scan_id, body)

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
            captured_row = repo.get_revision(connection, scan_id, 0)
        captured = repo.graph_of(captured_row) if captured_row else None
        rebuilt = stages.label_scan(
            base, frame_paths=frame_paths, poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path, capture_graph=captured,
        ).model_copy(update={"revision": base.revision + 1, "base_hash": graph_hash(base)})
        with database.transaction() as connection:
            if repo.get_revision(connection, scan_id)["revision"] != base.revision:
                raise ApiProblem(409, STALE_LAYOUT)
            repo.save_revision(connection, rebuilt, source="rebuild", base_revision=base.revision)
            repo.enqueue_job(connection, scan_id, ASSESS, rebuilt.revision)
        worker.wake()
        return rebuilt
