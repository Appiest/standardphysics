"""Uploading a scan's artifacts, checking them before they are stored, and completing the scan."""

from __future__ import annotations

import contextlib
import pathlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import anyio
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from standardphysics_contracts import Artifact, ArtifactKind, CompleteRequest, EvidenceStatus, Scan, SurfaceCoverage

from . import repository as repo
from . import repository_jobs as jobs_repo
from .auth import owner_of
from .budgets import Budgets, InFlight, Reservation, UploadAdmission, UploadReservations
from .coverage import parse_coverage
from .db import Database
from .errors import ApiProblem
from .evidence import evidence_status_for, maybe_queue_semantic, record_closure
from .lidar_mesh import MAX_LIDAR_MESH_BYTES, InvalidLidarMesh, validate_lidar_mesh_file
from .live_walk import frame_pose, read_during_walk
from .receive_deadlines import BodyTooSlow
from .scan_routes import scan_or_404
from .settings import Settings
from .store import ArtifactStore, ArtifactTooLarge, InvalidArtifactId, StagedUpload
from .textures import MAX_METADATA_BYTES, maybe_queue_texture, validate_manifest
from .usdz_validation import MAX_ARCHIVE_BYTES, InvalidUsdz, validate_room_usdz
from .worker import Worker
from .worker_handlers import PROCESS


def _accept_staged(
    database: Database, admission: UploadAdmission, artifact_id: str, kind: str, claimed: str, staged: StagedUpload
) -> tuple[int, Artifact]:
    """Store a staged upload, or refuse it. The caller discards whatever is still staged afterwards."""
    store, scan_id = admission.store, admission.scan_id
    if staged.sha256 != claimed.lower():
        raise ApiProblem(400, "checksum mismatch")
    with database.transaction() as connection:
        scan_or_404(connection, scan_id)
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
            scan_or_404(connection, admission.scan_id)
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
        scan = scan_or_404(connection, scan_id)
        if scan.state in ("failed", "uploading"):
            budgets.admit_queued_work(connection)
        if scan.state == "failed":
            jobs_repo.retry_failed_jobs(connection, scan_id)
            return scan_or_404(connection, scan_id), True
        if scan.state != "uploading":
            return scan, False
        missing = repo.missing_required(scan)
        if missing:
            raise ApiProblem(409, "missing artifacts", need=missing)
        repo.mark_finalized(connection, scan, _coverage_of(store, scan))
        record_closure(connection, scan)
        jobs_repo.enqueue_job(connection, scan_id, PROCESS, 0)
        return scan_or_404(connection, scan_id), True


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
    except BodyTooSlow as slow:
        raise ApiProblem(408, f"The upload stopped arriving: {slow}. Send it again.") from None


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
        scan = scan_or_404(connection, scan_id)
        if scan.state == "uploading":
            return False
        record_closure(connection, scan)
        queued = maybe_queue_semantic(connection, scan, PROCESS, settle_seconds=settle_seconds)
    return queued == "queued"


def install_upload_routes(
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
        x_frame_pose: Annotated[str | None, Header()] = None,
    ):
        admission = UploadAdmission(budgets, store, owner_of(request), scan_id, reservations)
        pose = frame_pose(x_frame_pose, artifact_id, x_artifact_kind)
        with _reserve_or_refuse_early(database, admission, artifact_id, request) as reservation:
            staged = await _stage_upload(store, scan_id, artifact_id, request, reservation)
            try:
                await _validate_staged(staged, x_artifact_kind, validations)
                status, artifact = _accept_staged(
                    database, admission, artifact_id, x_artifact_kind, x_checksum_sha256, staged
                )
            finally:
                store.discard(staged)
        if status == 201:
            read_during_walk(worker.live_reader, store, scan_id, artifact_id, pose)
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
                current = scan_or_404(connection, scan_id)
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
            scan = scan_or_404(connection, scan_id)
        return evidence_status_for(database, scan)
