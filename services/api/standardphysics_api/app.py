"""The HTTP surface. Upload paths and status codes follow services/api/openapi.json.

`create_app` builds the app and installs each group of routes from the module
that holds it, in a fixed order.
"""

from __future__ import annotations

import contextlib
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from standardphysics_agents import init_tracing, project_url, shutdown_tracing
from standardphysics_contracts import Report
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import accounts
from .architecture_export import install_architecture_export_routes
from .auth import install_auth
from .budgets import Budgets, UploadReservations
from .db import Database
from .errors import ApiProblem
from .file_routes import install_file_routes, render_response, scene_glb_response
from .health_routes import install_health_routes
from .layout_routes import install_combine_routes, install_label_routes, install_layout_routes
from .model_chooser import ModelSlots
from .notifications import notifier_from
from .owner_accounts import install_account_routes, revoke_passwords_left_on_apple_accounts
from .owner_routes import PhotoLimits, answered, install_owner_routes
from .plans import install_plan_routes
from .rearrangement import Rearranger
from .replays import install_replay_routes
from .report import build_report
from .request_size import BoundedRequestBodies
from .scan_routes import install_scan_routes, install_workspace_routes
from .scenario_routes import install_scenario_routes
from .seed import seed_sample_shop
from .settings import Settings
from .sharing import install_share_routes
from .simulation_routes import install_rearrangement_routes, install_simulation_routes
from .splats import install_splat_routes
from .stages import Stages, configured_stages
from .store import ArtifactStore, ScanQuota
from .team import adopt_allowlist
from .textures import install_texture_routes
from .upload_routes import install_upload_routes
from .waitlist import install_waitlist_routes
from .worker import Worker

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


def create_app(
    settings: Settings | None = None,
    stages: Stages | None = None,
    run_worker: bool = True,
    rearranger: Rearranger | None = None,
) -> FastAPI:
    settings = settings or Settings.from_environment()
    if stages is None:
        stages = configured_stages(settings)
    database = Database(settings.database_path)
    adopt_allowlist(database, settings.team_emails)
    revoke_passwords_left_on_apple_accounts(database)
    quota = ScanQuota(settings.max_scan_artifacts, settings.max_scan_bytes)
    store = ArtifactStore(settings.data_dir, settings.max_artifact_bytes, quota, settings.receive_deadlines())
    worker = Worker(database, store, stages, settings, rearranger)
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
    install_waitlist_routes(app, database, settings)
    install_architecture_export_routes(app, database)
    budgets = Budgets(
        settings.max_owner_scans, settings.max_owner_bytes, settings.max_queued_jobs, settings.min_free_disk_bytes
    )
    install_scan_routes(app, database, store, budgets)
    reservations = UploadReservations(settings.max_owner_uploads, settings.max_concurrent_uploads)
    install_upload_routes(app, database, store, worker, settings, budgets, reservations)
    install_workspace_routes(app, database, store, stages)
    install_owner_routes(app, database, store, stages, PhotoLimits(budgets, reservations))
    install_combine_routes(app, database, store, worker)
    install_file_routes(app, database, store)
    model_slots = ModelSlots(settings.max_owner_model_runs, settings.max_concurrent_model_runs)
    install_layout_routes(app, database, stages, worker, model_slots)
    install_scenario_routes(app, database, stages, worker)
    install_simulation_routes(app, database, stages, worker)
    install_rearrangement_routes(app, database, worker)
    install_replay_routes(app, database, store)
    install_texture_routes(app, database, store, worker)
    install_splat_routes(app, database, store)
    install_label_routes(app, database, store, worker)

    _install_report_route(app, database, stages)
    install_plan_routes(app, database, stages)
    install_share_routes(
        app, database, lambda scan_id: answered_report(database, stages, scan_id),
        lambda scan_id: scene_glb_response(database, scan_id, None),
        lambda scan_id, finding_id: render_response(store, database, scan_id, finding_id),
    )

    install_health_routes(app, database, worker, settings.git_sha)
    app.add_middleware(
        BoundedRequestBodies, max_bytes=settings.max_request_body_bytes, deadlines=settings.receive_deadlines()
    )
    return app


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
