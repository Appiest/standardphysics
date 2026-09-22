"""Evidence closure: derived readiness from stored bytes, bundles and jobs.

K froze this for the pilot contract (coordinator plan item 7a). B owns the
persisted lifecycle hardening: this module only closes bundles, tells callers
when exactly one semantic job is due, and assembles the status a client reads.
Nothing here claims a detection ran or an object exists.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from standardphysics_contracts import (
    GEOMETRY_REQUIRED_ARTIFACT_KINDS,
    SEMANTIC_REQUIRED_ARTIFACT_KINDS,
    EvidenceBundle,
    EvidenceStatus,
    Scan,
)

from . import repository as repo
from .db import Database
from .store import ArtifactStore


def association_state(database: Database, store: ArtifactStore, scan_id: uuid.UUID) -> tuple[str, str | None, bool]:
    """Whether the uploaded frames, poses, manifest and mesh agree with each other.

    Reuses the exact frame/pose/manifest/mesh pairing checks the texture build
    depends on, so one receipt rule covers every consumer. Returns the state,
    the mismatch and whether the phone declared pairings with a photo manifest:

        not_started        every named pair is stored and consistent
        failed             a schema or association check did not hold
        waiting_for_photos the manifest names photos that are still uploading
        needs_photos       no projectable poses exist, so nothing more to pair

    Only a declared manifest makes `failed` or `waiting_for_photos` binding:
    a legacy capture cannot be cross-checked, so its discovery pass reports
    its own failures instead of the receipt refusing it.
    """
    from .textures import _inputs

    with database.connect() as connection:
        manifest = repo.artifact_of_kind(connection, scan_id, "photo_manifest")
        state, _, failure = _inputs(connection, store, scan_id)
    return state, failure, manifest is not None


def record_closure(connection: sqlite3.Connection, scan: Scan) -> EvidenceBundle:
    """Close the scan's current artifacts into a bundle when they changed.

    Identical content does not create a new version, so a duplicate upload or
    a repeated /complete can never schedule another derived job.
    """
    latest = repo.latest_bundle(connection, scan.id)
    version = 1 if latest is None else latest.version + 1
    candidate = repo.build_evidence_bundle(scan, version, repo.now())
    if latest is not None and latest.manifest_hash == candidate.manifest_hash:
        return latest
    repo.insert_bundle(connection, scan.id, candidate)
    return candidate


def maybe_queue_semantic(
    connection: sqlite3.Connection,
    scan: Scan,
    kind: str,
    *,
    settle_seconds: float = 30.0,
    explicit: bool = False,
) -> str | None:
    """Queue one semantic job for an unprocessed complete bundle.

    A phone delivers evidence over minutes (frames one by one, manifest last):
    queueing per arrival would run hundreds of provider jobs on partial
    evidence. A job is only due when the bundle is complete, no result has been
    published for it, nothing is already queued, and either the caller asks
    explicitly (a re-posted /complete) or the evidence has been quiet for
    `settle_seconds`. Returns "queued", "pending", or None.
    """
    bundle = repo.latest_bundle(connection, scan.id)
    if bundle is None or not bundle.complete:
        return None
    if bundle.semantic_processed_hash == bundle.manifest_hash:
        return None
    if repo.has_pending_process_job(connection, scan.id):
        return "pending"
    if not explicit and not _settled(connection, scan.id, bundle, settle_seconds):
        return None
    repo.queue_job_again(connection, scan.id, kind, 0)
    return "queued"


def _settled(
    connection: sqlite3.Connection,
    scan_id: uuid.UUID,
    bundle: EvidenceBundle,
    settle_seconds: float,
) -> bool:
    """The evidence has stopped moving: the closing manifest is in, or it is quiet."""
    if "photo_manifest" in bundle.artifact_hashes:
        return True
    latest = repo.latest_semantic_arrival(connection, scan_id)
    if latest is None:
        return True
    arrival = datetime.fromisoformat(latest)
    return (datetime.now(UTC) - arrival).total_seconds() >= settle_seconds


def evidence_status_for(database: Database, scan: Scan) -> EvidenceStatus:
    with database.connect() as connection:
        bundle = repo.latest_bundle(connection, scan.id)
        pending = repo.has_pending_process_job(connection, scan.id)
        states = repo.process_job_states(connection, scan.id)
        job = repo.latest_process_job(connection, scan.id)

    present = sorted({artifact.kind for artifact in scan.artifacts})
    missing_geometry = sorted(set(GEOMETRY_REQUIRED_ARTIFACT_KINDS) - set(present))
    missing_semantic = sorted(set(SEMANTIC_REQUIRED_ARTIFACT_KINDS) - set(present))

    geometry_state = (
        "failed" if scan.state == "failed" else "ready" if not missing_geometry and bool(present) else "awaiting"
    )
    semantic_present = [kind for kind in present if kind in repo.SEMANTIC_INPUT_KINDS]
    if not missing_semantic and semantic_present:
        evidence_state = "complete"
    elif semantic_present:
        evidence_state = "partial"
    else:
        evidence_state = "awaiting"

    reasons = list(bundle.reasons) if bundle is not None else []
    reasons.extend(_semantic_notes(scan, bundle, missing_semantic))
    reasons.extend(_job_notes(job))

    semantic_state = _semantic_state(scan, bundle, pending, states, job)
    return EvidenceStatus(
        scan_id=scan.id,
        geometry_state=geometry_state,
        evidence_state=evidence_state,
        semantic_state=semantic_state,
        present_kinds=present,
        missing_geometry_kinds=missing_geometry,
        missing_semantic_kinds=missing_semantic,
        reasons=reasons,
        bundle_version=bundle.version if bundle is not None else 0,
        manifest_hash=bundle.manifest_hash if bundle is not None else None,
        complete_evidence=bool(bundle and not missing_semantic and not missing_geometry),
        semantic_job_pending=pending,
        latest_bundle=bundle,
    )


def _job_notes(job: sqlite3.Row | None) -> list[str]:
    if job is None:
        return []
    notes: list[str] = []
    if job["state"] == "failed" and job["error"]:
        notes.append(f"last processing job failed: {job['error']}")
    if job["note"]:
        notes.append(job["note"])
    return notes


def _semantic_state(
    scan: Scan,
    bundle: EvidenceBundle | None,
    pending: bool,
    states: tuple[str, ...],
    job: sqlite3.Row | None,
) -> str:
    if bundle is None:
        return "not_started"
    if "failed" in states and scan.state == "failed":
        return "failed"
    if bundle.complete and bundle.semantic_processed_hash == bundle.manifest_hash:
        return "complete"
    if pending:
        return "running" if "running" in states else "queued"
    if job is not None and job["state"] == "failed":
        return "failed"
    if bundle.complete:
        return "settling"
    return "blocked_incomplete_evidence"


def _semantic_notes(scan: Scan, bundle: EvidenceBundle | None, missing_semantic: list[str]) -> list[str]:
    notes: list[str] = []
    if missing_semantic:
        notes.append("photo recognition is blocked until these artifact kinds arrive: " + ", ".join(missing_semantic))
    if scan.state == "ready" and bundle is not None and not bundle.complete:
        notes.append("geometry is usable but the scan is not evidence-complete for semantic detection")
    if bundle is not None and bundle.complete and bundle.semantic_processed_hash != bundle.manifest_hash:
        notes.append("complete evidence has not settled yet; recognition waits for it to stop changing")
    return notes
