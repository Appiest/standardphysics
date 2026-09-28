"""The HTTP surface. Upload paths and status codes follow services/api/openapi.json."""

from __future__ import annotations

import contextlib
import io
import logging
import pathlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import anyio
from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from PIL import Image as PILImage
from pydantic import BaseModel
from standardphysics_agents import init_tracing, project_url, shutdown_tracing, tracing_status
from standardphysics_agents.scenario_suggestion import DESTINATIONS
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
    ModelLoopInfo,
    ModelLoopRequest,
    OwnerWishesRequest,
    ProposalRequest,
    ProposalResult,
    RebuildRequest,
    Report,
    RouteLegs,
    SaveLayoutRequest,
    Scan,
    ScanList,
    Scenario,
    SceneGraph,
    SimulationRequest,
    SimulationStatus,
    SpaceTypologyRequest,
    SurfaceCoverage,
    graph_hash,
)
from standardphysics_contracts.textures import FRAME_ID_PATTERN
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import accounts
from . import repository as repo
from .approach import evaluate as evaluate_approach
from .architecture_export import install_architecture_export_routes
from .auth import install_auth, owner_of
from .budgets import Budgets, InFlight, Reservation, UploadAdmission, UploadReservations, admit_new_job
from .combine import SaveCombineRequest, rooms_of, save_combine
from .coverage import parse_coverage
from .db import Database
from .errors import ApiProblem
from .evidence import evidence_status_for, maybe_queue_semantic, record_closure
from .labels import mark_counter, mark_observation, review_outlet, unmark_counter
from .layout import check_layout, save_layout
from .lidar_mesh import MAX_LIDAR_MESH_BYTES, InvalidLidarMesh, validate_lidar_mesh_file
from .loop_run import run as run_loop_on
from .loop_run import stream as stream_loop_on
from .model_loop import loop_info, stream_model_loop
from .notifications import notifier_from
from .owner_accounts import install_account_routes, revoke_passwords_left_on_apple_accounts
from .owner_requests import carry_answers
from .owner_routes import answered, install_owner_routes
from .plans import install_plan_routes
from .proposals import propose
from .questions import answer_question
from .replays import install_replay_routes
from .report import build_report
from .request_size import BoundedRequestBodies
from .route import confirm, legs, suggestion
from .seed import seed_sample_shop
from .settings import Settings
from .sharing import install_share_routes
from .simulations import queue_simulation, simulation_status
from .splats import install_splat_routes
from .stages import Stages, configured_stages
from .store import ArtifactStore, ArtifactTooLarge, InvalidArtifactId, ScanQuota, StagedUpload
from .team import adopt_allowlist
from .textures import MAX_METADATA_BYTES, install_texture_routes, maybe_queue_texture, validate_manifest
from .usdz_validation import MAX_ARCHIVE_BYTES, InvalidUsdz, validate_room_usdz
from .worker import ASSESS, PROCESS, Worker

PLACES = {*DESTINATIONS, "pickup"}
DEMO_PASSWORD_FILE = "demo-password"

log = logging.getLogger(__name__)


def _start_tracing(settings: Settings) -> None:
    """Weave sees the whole run: seeding, uploads, checks and every model call."""
    if init_tracing(settings.weave_project, settings.weave_entity):
        log.info("this run is traced to %s", project_url())


def _seed_demo_account(database: Database, store: ArtifactStore, settings: Settings) -> None:
    """Put the sample shop behind a real account, and say where its password is.

    Only the run that creates the account knows its password. A later run's
    `seed_owner_password` may be freshly generated and was never stored, so it
    is neither named nor written over the file the first run left."""
    created_owner = seed_sample_shop(database, store, settings.seed_owner_email, settings.seed_owner_password)
    whereabouts = _demo_password_whereabouts(settings) if created_owner else _existing_password_whereabouts(settings)
    log.warning(
        "sample shop seeded. Sign in as %s with the password %s",
        settings.seed_owner_email,
        whereabouts,
    )


def _demo_password_whereabouts(settings: Settings) -> str:
    if not settings.seed_owner_password_generated:
        return "in SP_SEED_OWNER_PASSWORD"
    path = settings.data_dir / DEMO_PASSWORD_FILE
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(settings.seed_owner_password + "\n")
    return f"written to {path}"


def _existing_password_whereabouts(settings: Settings) -> str:
    path = settings.data_dir / DEMO_PASSWORD_FILE
    return f"it was created with: SP_SEED_OWNER_PASSWORD as it was then, or the one in {path}"


def _sweep_abandoned_staging(store: ArtifactStore, settings: Settings) -> None:
    removed = store.remove_abandoned_staging(settings.staging_max_age_seconds)
    if removed:
        log.info("deleted %d staged upload(s) abandoned before this start", removed)


def _problem_response(exc: ApiProblem) -> JSONResponse:
    return JSONResponse(exc.body.model_dump(exclude_none=True), status_code=exc.status, headers=exc.headers)


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
        stages = configured_stages(settings)
    database = Database(settings.database_path)
    adopt_allowlist(database, settings.team_emails)
    revoke_passwords_left_on_apple_accounts(database)
    quota = ScanQuota(settings.max_scan_artifacts, settings.max_scan_bytes)
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes, quota)
    worker = Worker(database, store, stages, settings)
    worker.notifier = notifier_from(settings)

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
        _sweep_abandoned_staging(store, settings)
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()
        shutdown_tracing()

    app = FastAPI(title="Standard Physics API", version="0.1.0", lifespan=lifespan)
    app.state.database, app.state.store, app.state.worker = database, store, worker
    app.state.notifier = worker.notifier
    _install_error_handlers(app)
    install_auth(app, database, store)
    install_account_routes(app, database, settings.apple_audiences)
    install_architecture_export_routes(app, database)
    budgets = Budgets(
        settings.max_owner_scans, settings.max_owner_bytes, settings.max_queued_jobs, settings.min_free_disk_bytes
    )
    _install_scan_routes(app, database, store, budgets)
    reservations = UploadReservations(settings.max_owner_uploads, settings.max_concurrent_uploads)
    _install_upload_routes(app, database, store, worker, settings, budgets, reservations)
    _install_workspace_routes(app, database, store, stages)
    install_owner_routes(app, database, store, stages)
    _install_combine_routes(app, database, store, worker)
    _install_file_routes(app, database, store)
    _install_layout_routes(app, database, stages, worker)
    _install_route_routes(app, database, stages, worker)
    _install_simulation_routes(app, database, stages, worker)
    install_replay_routes(app, database, store)
    install_texture_routes(app, database, store, worker)
    install_splat_routes(app, database, store)
    _install_label_routes(app, database, store, worker)

    _install_report_route(app, database, stages)
    install_plan_routes(app, database, stages)
    install_share_routes(
        app, database, lambda scan_id: answered_report(database, stages, scan_id),
        lambda scan_id: _scene_glb_response(database, scan_id, None),
        lambda scan_id, finding_id: _render_response(store, database, scan_id, finding_id),
    )

    _install_health_routes(app, database, worker, settings.git_sha)
    app.add_middleware(BoundedRequestBodies, max_bytes=settings.max_request_body_bytes)
    return app


def _install_health_routes(app: FastAPI, database: Database, worker: Worker, commit: str) -> None:
    @app.get("/health")
    def health():
        """Liveness, reachable without a session, so a load balancer and the container healthcheck can ask.

        It touches the database, because a process that is listening but cannot
        read its own scans is not healthy in any way that matters. It fails when
        a worker loop has died, since then uploads are accepted and never
        measured. It stays healthy while a loop is busy with a fifteen-minute
        bake, and in a second process that found the worker lock taken, because
        restarting either one would fix nothing. A loop that is stalled or
        running a job past its deadline is named in the body but not failed
        here. Both are judged from timing alone, and a restart in the middle of
        a slow job throws its work away. /health/ready fails on them.
        """
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone()
        state = worker.summary()
        body = {"status": "ok" if state != "stopped" else "worker stopped", "worker": state}
        return JSONResponse(body, status_code=503 if state == "stopped" else 200)

    @app.get("/health/ready")
    def health_ready():
        """Whether queued work is getting done: 503 while any worker loop has died, is stuck
        outside a job, or is running a job past its kind's deadline."""
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM scans LIMIT 1").fetchone()
        problems = worker.problems()
        body = {"status": "degraded" if problems else "ready", "problems": problems}
        return JSONResponse(body, status_code=503 if problems else 200)

    @app.get("/health/details")
    def health_details() -> dict:
        """What each worker loop is doing, how long since it last beat, how long the queue has waited,
        which commit this server was built from, and whether its traces are reaching Weave."""
        with database.connect() as connection:
            oldest = repo.oldest_queued_job_seconds(connection)
        problems = worker.problems()
        return {
            "status": "degraded" if problems else "ok",
            "problems": problems,
            "worker": worker.status(),
            "oldest_queued_job_seconds": oldest,
            "commit": commit,
            "tracing": tracing_status(),
        }


def answered_report(database: Database, stages: Stages, scan_id: uuid.UUID) -> Report:
    """The report with the owner's answers laid over its findings."""
    built = build_report(database, stages.ledger_factory(), scan_id)
    if built.assessment is None:
        return built
    with database.connect() as connection:
        return built.model_copy(update={"assessment": answered(connection, stages, scan_id, built.assessment)})


def _install_report_route(app: FastAPI, database: Database, stages: Stages) -> None:
    @app.get("/api/scans/{scan_id}/report", response_model=Report)
    def report(scan_id: uuid.UUID) -> Report:
        return answered_report(database, stages, scan_id)


def _scan_or_404(connection, scan_id: uuid.UUID) -> Scan:
    scan = repo.get_scan(connection, scan_id)
    if scan is None:
        raise ApiProblem(404, "no scan")
    return scan


def _install_scan_routes(app: FastAPI, database: Database, store: ArtifactStore, budgets: Budgets) -> None:
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
            return _scan_or_404(connection, scan_id)

    @app.get("/api/scans", response_model=ScanList)
    def list_scans(request: Request) -> ScanList:
        with database.connect() as connection:
            return ScanList(scans=repo.list_shops(connection, owner_of(request).id))

    @app.get("/api/scans/{scan_id}", response_model=Scan)
    def get_scan(scan_id: uuid.UUID) -> Scan:
        with database.connect() as connection:
            return _scan_or_404(connection, scan_id)

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
            _scan_or_404(connection, scan_id)
            if repo.other_running_job(connection, scan_id):
                repo.mark_for_deletion(connection, scan_id)
                return Response(status_code=204)
            repo.delete_scan(connection, scan_id)
        store.remove_scan(scan_id)
        return Response(status_code=204)


def _accept_staged(
    database: Database, admission: UploadAdmission, artifact_id: str, kind: str, claimed: str, staged: StagedUpload
) -> tuple[int, Artifact]:
    """Store a staged upload, or refuse it. The caller discards whatever is still staged afterwards."""
    store, scan_id = admission.store, admission.scan_id
    if staged.sha256 != claimed.lower():
        raise ApiProblem(400, "checksum mismatch")
    with database.transaction() as connection:
        _scan_or_404(connection, scan_id)
        existing = repo.find_artifact(connection, scan_id, artifact_id)
        if existing is not None:
            if existing.kind != kind:
                raise ApiProblem(409, "artifact already stored with different kind")
            if existing.sha256 != staged.sha256:
                raise ApiProblem(409, "artifact already stored with different content")
            return 200, existing
        admission.before_storing(connection, staged.bytes)
        artifact = Artifact(id=artifact_id, kind=kind, sha256=staged.sha256, bytes=staged.bytes)
        repo.insert_artifact(connection, scan_id, artifact)
        store.commit(staged, store.artifact_path(scan_id, artifact_id))
        return 201, artifact


def _reserve_or_refuse_early(
    database: Database, admission: UploadAdmission, artifact_id: str, request: Request
) -> contextlib.AbstractContextManager[Reservation]:
    """Say no before reading the body when a new artifact could not fit anyway, and otherwise hold
    room for it while it streams. A repeat upload still gets its 200.

    A body sent without a length is reserved at the largest size an artifact may have.
    """
    declared = request.headers.get("content-length", "")
    declared_bytes = int(declared) if declared.isdigit() else admission.store.max_bytes

    def refuse_a_doomed_upload(in_flight: InFlight) -> None:
        with database.connect() as connection:
            _scan_or_404(connection, admission.scan_id)
            if repo.find_artifact(connection, admission.scan_id, artifact_id) is None:
                admission.before_reading(connection, declared_bytes, in_flight)

    return admission.reservations.hold(admission.owner.id, declared_bytes, refuse_a_doomed_upload)


def _coverage_of(store: ArtifactStore, scan: Scan) -> list[SurfaceCoverage]:
    """The phone's coverage.json, or none. Coverage never blocks a scan, so an oversized file is skipped unread."""
    artifact = next((a for a in scan.artifacts if a.kind == "coverage"), None)
    if artifact is None or artifact.bytes > MAX_METADATA_BYTES:
        return []
    return parse_coverage(store.artifact_path(scan.id, artifact.id).read_bytes())


def _finalize(database: Database, store: ArtifactStore, budgets: Budgets, scan_id: uuid.UUID) -> tuple[Scan, bool]:
    with database.transaction() as connection:
        scan = _scan_or_404(connection, scan_id)
        if scan.state in ("failed", "uploading"):
            budgets.admit_queued_work(connection)
        if scan.state == "failed":
            repo.retry_failed_jobs(connection, scan_id)
            return _scan_or_404(connection, scan_id), True
        if scan.state != "uploading":
            return scan, False
        missing = repo.missing_required(scan)
        if missing:
            raise ApiProblem(409, "missing artifacts", need=missing)
        repo.mark_finalized(connection, scan, _coverage_of(store, scan))
        record_closure(connection, scan)
        repo.enqueue_job(connection, scan_id, PROCESS, 0)
        return _scan_or_404(connection, scan_id), True


@dataclass(frozen=True)
class StagedCheck:
    """How an artifact kind is checked before it is stored: its size cap, the check, what it raises, the 400."""

    max_bytes: int
    validate: Callable[[pathlib.Path], object]
    invalid: type[Exception]
    message: str


def _photo_manifest_file(path: pathlib.Path) -> object:
    return validate_manifest(path.read_bytes())


STAGED_CHECKS = {
    "lidar_mesh": StagedCheck(MAX_LIDAR_MESH_BYTES, validate_lidar_mesh_file, InvalidLidarMesh, "invalid lidar mesh"),
    "photo_manifest": StagedCheck(MAX_METADATA_BYTES, _photo_manifest_file, ValueError, "invalid photo manifest"),
    "room_usdz": StagedCheck(MAX_ARCHIVE_BYTES, validate_room_usdz, InvalidUsdz, "invalid usdz archive"),
}
"""Artifact kinds whose bytes are checked before they are stored. The size is
compared from the staged file's length, so an oversized one is never read."""


async def _validate_staged(staged: StagedUpload, kind: str, validations: anyio.CapacityLimiter) -> None:
    """Check the staged file on a worker thread, no more at once than `validations` allows.

    A mesh check reads hundreds of megabytes. On the event loop it would stop
    every other request, /health included, until it finished.
    """
    check = STAGED_CHECKS.get(kind)
    if check is None:
        return
    if staged.bytes > check.max_bytes:
        raise ApiProblem(413, f"{kind} is larger than {check.max_bytes} bytes")
    try:
        await anyio.to_thread.run_sync(check.validate, staged.temp_path, limiter=validations)
    except check.invalid:
        raise ApiProblem(400, check.message) from None


async def _stage_upload(
    store: ArtifactStore, scan_id: uuid.UUID, artifact_id: str, request: Request, reservation: Reservation
):
    """Stage the uploaded bytes, no more than were reserved, turning the store's refusals into problems."""
    try:
        store.artifact_path(scan_id, artifact_id)
        return await store.stage(scan_id, reservation.counted(request.stream()), reservation.declared_bytes)
    except InvalidArtifactId:
        raise ApiProblem(400, "invalid artifact id") from None
    except ArtifactTooLarge:
        raise ApiProblem(413, "artifact too large") from None


def _queue_for_arrival(
    database: Database,
    store: ArtifactStore,
    worker: Worker,
    scan_id: uuid.UUID,
    kind: str,
    settle_seconds: float,
) -> bool:
    """Queue whatever this artifact's arrival has made ready.

    True when a semantic job was queued, which is the caller's cue to wake the
    worker. A scan still uploading is left alone: the bundle is not whole yet,
    and queueing per arriving frame would run a provider job on a partial one.
    """
    if kind in ("photo_manifest", "frames", "poses", "lidar_mesh"):
        maybe_queue_texture(database, store, worker, scan_id)
    if kind not in repo.SEMANTIC_INPUT_KINDS:
        return False
    with database.transaction() as connection:
        scan = _scan_or_404(connection, scan_id)
        if scan.state == "uploading":
            return False
        record_closure(connection, scan)
        queued = maybe_queue_semantic(connection, scan, PROCESS, settle_seconds=settle_seconds)
    return queued == "queued"


def _install_upload_routes(
    app: FastAPI,
    database: Database,
    store: ArtifactStore,
    worker: Worker,
    settings: Settings,
    budgets: Budgets,
    reservations: UploadReservations,
) -> None:
    validations = anyio.CapacityLimiter(settings.max_concurrent_validations)

    @app.put("/api/scans/{scan_id}/artifacts/{artifact_id}", response_model=Artifact, status_code=201)
    async def upload_artifact(
        scan_id: uuid.UUID,
        artifact_id: str,
        request: Request,
        x_checksum_sha256: Annotated[str, Header()],
        x_artifact_kind: Annotated[ArtifactKind, Header()],
    ):
        admission = UploadAdmission(budgets, store, owner_of(request), scan_id, reservations)
        with _reserve_or_refuse_early(database, admission, artifact_id, request) as reservation:
            staged = await _stage_upload(store, scan_id, artifact_id, request, reservation)
            try:
                await _validate_staged(staged, x_artifact_kind, validations)
                status, artifact = _accept_staged(
                    database, admission, artifact_id, x_artifact_kind, x_checksum_sha256, staged
                )
            finally:
                store.discard(staged)
        if _queue_for_arrival(
            database, store, worker, scan_id, x_artifact_kind, settings.evidence_settle_seconds
        ):
            worker.wake()
        return JSONResponse(artifact.model_dump(mode="json"), status_code=status)

    @app.post("/api/scans/{scan_id}/complete", response_model=Scan)
    def complete(scan_id: uuid.UUID, body: CompleteRequest | None = None) -> Scan:
        scan, queued = _finalize(database, store, budgets, scan_id)
        if not queued and scan.state != "uploading":
            with database.transaction() as connection:
                current = _scan_or_404(connection, scan_id)
                explicit = maybe_queue_semantic(
                    connection, current, PROCESS,
                    settle_seconds=settings.evidence_settle_seconds, explicit=True,
                    max_queued_jobs=budgets.queued_jobs,
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


def _install_workspace_routes(app: FastAPI, database: Database, store: ArtifactStore, stages: Stages) -> None:
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
            return answered(connection, stages, scan_id, found)


def _install_combine_routes(app: FastAPI, database: Database, store: ArtifactStore, worker: Worker) -> None:
    @app.get("/api/scans/{scan_id}/rooms")
    def rooms(scan_id: uuid.UUID, revision: int | None = None) -> dict:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
        return rooms_of(database, store, scan_id, revision)

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

    @app.get("/api/model-loop", response_model=ModelLoopInfo)
    def model_loop_info() -> ModelLoopInfo:
        return loop_info()

    @app.post("/api/scans/{scan_id}/model-loop/stream")
    def model_loop_stream(scan_id: uuid.UUID, body: ModelLoopRequest) -> StreamingResponse:
        lines = stream_model_loop(database, stages, scan_id, body)
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


def _destinations(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    named = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [part for part in named if part not in PLACES]
    if unknown:
        raise ApiProblem(400, "unknown destination", need=unknown)
    return named


def _install_route_routes(app: FastAPI, database: Database, stages: Stages, worker: Worker) -> None:
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
            _scan_or_404(connection, scan_id)
            repo.set_owner_wishes(connection, scan_id, body.wishes)
            return _scan_or_404(connection, scan_id)

    @app.put("/api/scans/{scan_id}/space-type", response_model=Scan)
    def set_space_type(scan_id: uuid.UUID, body: SpaceTypologyRequest) -> Scan:
        with database.transaction() as connection:
            _scan_or_404(connection, scan_id)
            repo.set_space_typology(connection, scan_id, body.space_typology)
            return _scan_or_404(connection, scan_id)

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


def _scene_glb_response(database: Database, scan_id: uuid.UUID, revision: int | None) -> Response:
    """The exported geometry, and whether a newer export is still being made.

    The pending header goes out either way: a viewer that gets a 404 still
    needs to know the difference between nothing to show and not yet.
    """
    with database.connect() as connection:
        _scan_or_404(connection, scan_id)
        found = repo.display_geometry(connection, scan_id, revision)
        pending = repo.display_pending(connection, scan_id)
    response = _file_or_404(found[0], "model/gltf-binary") if found else Response(status_code=404)
    if found:
        response.headers["X-Exported-Revision"] = str(found[1])
    response.headers["X-Display-Pending"] = str(pending).lower()
    return response


def _crop_file(store: ArtifactStore, scan_id: uuid.UUID, crop_id: str) -> tuple[pathlib.Path, str]:
    """The stored crop named by this id, and what it is.

    The id is a caller's string, so it is checked twice: once for the obvious
    traversal spellings, and again by resolving the result and requiring it to
    still sit under the scan's own crops directory. A symlink cannot carry it
    out of there either, because the comparison happens after resolution.
    """
    if "/" in crop_id or "\\" in crop_id or ".." in crop_id:
        raise ApiProblem(400, "invalid crop id")
    filename = crop_id if crop_id.endswith((".jpg", ".png")) else f"{crop_id}.jpg"
    crops_dir = (store.scan_dir(scan_id) / "crops").resolve()
    crop_path = (crops_dir / filename).resolve()
    if not str(crop_path).startswith(str(crops_dir)):
        raise ApiProblem(400, "invalid crop path")
    if not crop_path.is_file():
        raise ApiProblem(404, "crop not found")
    return crop_path, "image/png" if filename.endswith(".png") else "image/jpeg"


def _frame_listing(store: ArtifactStore, scan_id: uuid.UUID, stored: list[Artifact]) -> FrameListing:
    """Split the stored frames into the ones that open and the ones that do not.

    An artifact whose bytes will not read as an image is reported by id rather
    than failing the listing, so one bad frame does not hide the rest.
    """
    entries: list[FrameEntry] = []
    unreadable: list[str] = []
    for artifact in stored:
        entry = _frame_entry(store, scan_id, artifact)
        if entry is None:
            unreadable.append(artifact.id)
        else:
            entries.append(entry)
    return FrameListing(frames=entries, unreadable=unreadable)


def _render_response(
    store: ArtifactStore, database: Database, scan_id: uuid.UUID, finding_id: uuid.UUID
) -> FileResponse:
    with database.connect() as connection:
        revision = repo.get_revision(connection, scan_id)
    if revision is None:
        raise ApiProblem(404, "not ready")
    directory = store.scan_dir(scan_id) / "revisions"
    matches = sorted(directory.glob(f"*/renders/{finding_id}.png"), key=lambda path: int(path.parent.parent.name))
    return _file_or_404(matches[-1] if matches else None, "image/png")


def _install_file_routes(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    @app.head("/api/scans/{scan_id}/scene.glb")
    @app.get("/api/scans/{scan_id}/scene.glb")
    def scene_glb(scan_id: uuid.UUID, revision: int | None = None) -> Response:
        return _scene_glb_response(database, scan_id, revision)

    @app.get("/api/scans/{scan_id}/lidar-mesh")
    def lidar_mesh(scan_id: uuid.UUID) -> FileResponse:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
            artifact = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        path = store.artifact_path(scan_id, artifact.id) if artifact else None
        return _file_or_404(path, "application/json")

    @app.get("/api/scans/{scan_id}/renders/{finding_id}.png")
    def render(scan_id: uuid.UUID, finding_id: uuid.UUID) -> FileResponse:
        return _render_response(store, database, scan_id, finding_id)

    @app.get("/api/scans/{scan_id}/crops/{crop_id}")
    def get_crop(scan_id: uuid.UUID, crop_id: str) -> FileResponse:
        with database.connect() as connection:
            _scan_or_404(connection, scan_id)
        crop_path, media_type = _crop_file(store, scan_id, crop_id)
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
        return _frame_listing(store, scan_id, stored)

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
            captured_row = repo.get_revision(connection, scan_id, 0)
        captured = repo.graph_of(captured_row) if captured_row else None
        rebuilt = stages.label_scan(
            base, frame_paths=frame_paths, poses_path=poses_path,
            lidar_mesh_path=lidar_mesh_path, capture_graph=captured,
        ).model_copy(update={"revision": base.revision + 1, "base_hash": graph_hash(base)})
        with database.transaction() as connection:
            if repo.latest_revision_number(connection, scan_id) != base.revision:
                raise ApiProblem(409, STALE_LAYOUT)
            admit_new_job(connection, worker.settings.max_queued_jobs)
            repo.save_revision(connection, rebuilt, source="rebuild", base_revision=base.revision)
            repo.enqueue_job(connection, scan_id, ASSESS, rebuilt.revision)
        worker.wake()
        return rebuilt
