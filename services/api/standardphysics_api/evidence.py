"""Evidence closure: derived readiness from stored bytes, bundles and jobs.

K froze this for the pilot contract (coordinator plan item 7a). B owns the
persisted lifecycle hardening: this module only closes bundles, tells callers
when exactly one semantic job is due, and assembles the status a client reads.
Nothing here claims a detection ran or an object exists.
"""

from __future__ import annotations

import sqlite3
import uuid

from standardphysics_contracts import (
    EvidenceBundle,
    EvidenceStatus,
    GEOMETRY_REQUIRED_ARTIFACT_KINDS,
    Scan,
    SEMANTIC_REQUIRED_ARTIFACT_KINDS,
)

from . import repository as repo
from .db import Database


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


def maybe_queue_semantic(connection: sqlite3.Connection, scan: Scan, kind: str) -> str | None:
    """Queue one semantic job for an unprocessed complete bundle.

    Returns "queued" when a job was queued, "pending" while one is already
    queued or running, and None when nothing is due. The caller wakes the
    worker exactly when this returns "queued".
    """
    bundle = repo.latest_bundle(connection, scan.id)
    if bundle is None or not bundle.complete:
        return None
    if bundle.semantic_processed_hash == bundle.manifest_hash:
        return None
    if repo.has_pending_process_job(connection, scan.id):
        return "pending"
    repo.queue_job_again(connection, scan.id, kind, 0)
    return "queued"


def mark_processed_if_complete(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    """Record that the worker's semantic pass consumed the current bundle."""
    bundle = repo.latest_bundle(connection, scan_id)
    if bundle is not None and bundle.complete:
        repo.bundle_processed(connection, scan_id, bundle.version, bundle.manifest_hash)


def evidence_status_for(database: Database, scan: Scan) -> EvidenceStatus:
    with database.connect() as connection:
        bundle = repo.latest_bundle(connection, scan.id)
        pending = repo.has_pending_process_job(connection, scan.id)
        states = repo.process_job_states(connection, scan.id)

    present = sorted({artifact.kind for artifact in scan.artifacts})
    missing_geometry = sorted(set(GEOMETRY_REQUIRED_ARTIFACT_KINDS) - set(present))
    missing_semantic = sorted(set(SEMANTIC_REQUIRED_ARTIFACT_KINDS) - set(present))

    geometry_state = "failed" if scan.state == "failed" else "ready" if not missing_geometry and bool(present) else "awaiting"
    semantic_present = [kind for kind in present if kind in repo.SEMANTIC_INPUT_KINDS]
    if not missing_semantic and semantic_present:
        evidence_state = "complete"
    elif semantic_present:
        evidence_state = "partial"
    else:
        evidence_state = "awaiting"

    reasons = list(bundle.reasons) if bundle is not None else []
    reasons.extend(_semantic_notes(scan, bundle, missing_semantic))

    semantic_state = _semantic_state(scan, bundle, pending, states)
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


def _semantic_state(scan: Scan, bundle: EvidenceBundle | None, pending: bool, states: tuple[str, ...]) -> str:
    if bundle is None:
        return "not_started"
    if "failed" in states and scan.state == "failed":
        return "failed"
    if bundle.complete and bundle.semantic_processed_hash == bundle.manifest_hash:
        return "complete"
    if pending:
        return "running" if "running" in states else "queued"
    if not bundle.complete:
        return "blocked_incomplete_evidence"
    return "not_started"


def _semantic_notes(scan: Scan, bundle: EvidenceBundle | None, missing_semantic: list[str]) -> list[str]:
    notes: list[str] = []
    if missing_semantic:
        notes.append(
            "photo recognition is blocked until these artifact kinds arrive: "
            + ", ".join(missing_semantic)
        )
    if scan.state == "ready" and bundle is not None and not bundle.complete:
        notes.append("geometry is usable but the scan is not evidence-complete for semantic detection")
    return notes
