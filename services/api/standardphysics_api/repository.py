"""Rows in, contract models out. Nothing here knows about HTTP.

This module holds scans, their artifacts and their evidence bundles. The job
queue is in `repository_jobs`, and graph revisions, scenarios and assessments
are in `repository_revisions`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime

from standardphysics_contracts import (
    GEOMETRY_REQUIRED_ARTIFACT_KINDS,
    SEMANTIC_REQUIRED_ARTIFACT_KINDS,
    Artifact,
    CreateScanRequest,
    EvidenceBundle,
    OwnerWish,
    Scan,
    SpaceTypology,
    SurfaceCoverage,
)

REQUIRED_ARTIFACT_KINDS = ("room_json", "room_usdz")

SEMANTIC_INPUT_KINDS = frozenset((*SEMANTIC_REQUIRED_ARTIFACT_KINDS, "photo_manifest", "coverage"))
"""Artifact kinds whose arrival changes the evidence manifest semantic jobs run on.

Frozen by K (contract 2): a bundle's manifest hashes only these kinds, so a
walkthrough video cannot trigger a recognition rerun while a new frame set can.
B owns the persisted lifecycle built on top.
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def _artifacts(connection: sqlite3.Connection, scan_id: str, with_photos: bool = True) -> list[Artifact]:
    rows = connection.execute(
        "SELECT id, kind, sha256, bytes FROM artifacts WHERE scan_id = ? AND (? OR kind != 'frames')"
        " ORDER BY created_at, id",
        (scan_id, with_photos),
    )
    return [Artifact(id=r["id"], kind=r["kind"], sha256=r["sha256"], bytes=r["bytes"]) for r in rows]


def _scan(connection: sqlite3.Connection, row: sqlite3.Row, with_photos: bool = True) -> Scan:
    return Scan(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        device_model=row["device_model"],
        duration_seconds=row["duration_seconds"],
        state=row["state"],
        artifacts=_artifacts(connection, row["id"], with_photos),
        coverage=[SurfaceCoverage.model_validate(c) for c in json.loads(row["coverage_json"])],
        content_hash=row["content_hash"],
        space_typology=row["space_typology"],
        owner_wishes=_wishes_from(row["owner_wishes_json"]),
    )


def insert_scan(
    connection: sqlite3.Connection,
    request: CreateScanRequest,
    owner_id: uuid.UUID,
    scan_id: uuid.UUID | None = None,
    state: str = "uploading",
) -> uuid.UUID:
    scan_id = scan_id or uuid.uuid4()
    replaces = str(request.replaces) if request.replaces else None
    connection.execute(
        "INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state, owner_id, space_typology,"
        " replaces_scan_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(scan_id), request.name, now(), request.device_model, request.duration_seconds, state,
            str(owner_id), _typology_value(request.space_typology), replaces,
        ),
    )
    return scan_id


def _typology_value(typology: SpaceTypology | None) -> str | None:
    return None if typology is None else typology.value


def set_space_typology(connection: sqlite3.Connection, scan_id: uuid.UUID, typology: SpaceTypology | None) -> None:
    connection.execute(
        "UPDATE scans SET space_typology = ? WHERE id = ?", (_typology_value(typology), str(scan_id))
    )


def space_typology(connection: sqlite3.Connection, scan_id: uuid.UUID) -> SpaceTypology | None:
    row = connection.execute("SELECT space_typology FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
    return SpaceTypology(row["space_typology"]) if row and row["space_typology"] else None


def _wishes_from(stored: str) -> list[OwnerWish]:
    return [OwnerWish.model_validate(wish) for wish in json.loads(stored)]


def set_owner_wishes(connection: sqlite3.Connection, scan_id: uuid.UUID, wishes: list[OwnerWish]) -> None:
    stored = json.dumps([wish.model_dump(mode="json") for wish in wishes])
    connection.execute("UPDATE scans SET owner_wishes_json = ? WHERE id = ?", (stored, str(scan_id)))


def owner_wishes(connection: sqlite3.Connection, scan_id: uuid.UUID) -> list[OwnerWish]:
    row = connection.execute("SELECT owner_wishes_json FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
    return _wishes_from(row["owner_wishes_json"]) if row else []


def get_scan(connection: sqlite3.Connection, scan_id: uuid.UUID) -> Scan | None:
    row = connection.execute("SELECT * FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
    return _scan(connection, row) if row else None


def scan_owner(connection: sqlite3.Connection, scan_id: uuid.UUID) -> uuid.UUID | None:
    """Who owns this scan, or None when the scan is missing or unclaimed."""
    row = connection.execute("SELECT owner_id FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
    if row is None or row["owner_id"] is None:
        return None
    return uuid.UUID(row["owner_id"])


def list_scans(connection: sqlite3.Connection, owner_id: uuid.UUID) -> list[Scan]:
    rows = connection.execute(
        "SELECT * FROM scans WHERE owner_id = ? ORDER BY created_at DESC", (str(owner_id),)
    ).fetchall()
    return [_scan(connection, row) for row in rows]


def list_shops(connection: sqlite3.Connection, owner_id: uuid.UUID) -> list[Scan]:
    """The owner's shops: every scan except one a later walk of the same shop has replaced.

    A replaced scan stays listed while its replacement is still coming in, and
    again if the replacement fails, so the owner always has results to open.
    Photos are left out, since a floor keeps thousands; the scan itself lists them.
    """
    rows = connection.execute(
        "SELECT * FROM scans WHERE owner_id = ? AND id NOT IN ("
        " SELECT replaces_scan_id FROM scans WHERE replaces_scan_id IS NOT NULL AND state = 'ready'"
        ") ORDER BY created_at DESC",
        (str(owner_id),),
    ).fetchall()
    return [_scan(connection, row, with_photos=False) for row in rows]


def scan_exists(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    return connection.execute("SELECT 1 FROM scans WHERE id = ?", (str(scan_id),)).fetchone() is not None


def mark_for_deletion(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    """Hide a scan whose job is still running, for the worker to delete when the job ends.

    With no owner the scan answers 404 to everyone and leaves every list, so to
    the owner it is gone the moment they ask.
    """
    connection.execute("UPDATE scans SET owner_id = NULL, deleting_at = ? WHERE id = ?", (now(), str(scan_id)))


def marked_for_deletion(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    row = connection.execute("SELECT deleting_at FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
    return row is not None and row["deleting_at"] is not None


CHILD_TABLES = (
    "owner_requests", "checklist_items", "share_links", "layout_plans",
    "texture_builds", "simulations", "rearrangement_teacher_events", "rearrangements", "label_corrections",
    "assessments", "evidence_bundles", "scenarios", "revisions", "job_attempts", "jobs", "artifacts",
)
"""Everything that references a scan, deepest first.

Connections turn foreign keys on, so a table missing from this list makes
deleting any scan that has rows in it fail. Job attempts and evidence bundles
were missing, which meant no scan the worker had processed could be deleted.
"""


def delete_scan(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    for table in CHILD_TABLES:
        connection.execute(f"DELETE FROM {table} WHERE scan_id = ?", (str(scan_id),))
    connection.execute("DELETE FROM scans WHERE id = ?", (str(scan_id),))


def set_state(connection: sqlite3.Connection, scan_id: uuid.UUID, state: str) -> None:
    connection.execute("UPDATE scans SET state = ? WHERE id = ?", (state, str(scan_id)))


def find_artifact(connection: sqlite3.Connection, scan_id: uuid.UUID, artifact_id: str) -> Artifact | None:
    row = connection.execute(
        "SELECT id, kind, sha256, bytes FROM artifacts WHERE scan_id = ? AND id = ?",
        (str(scan_id), artifact_id),
    ).fetchone()
    return Artifact(id=row["id"], kind=row["kind"], sha256=row["sha256"], bytes=row["bytes"]) if row else None


def artifact_usage(connection: sqlite3.Connection, scan_id: uuid.UUID) -> tuple[int, int]:
    """How many artifacts a scan holds, and their bytes together."""
    row = connection.execute(
        "SELECT COUNT(*) AS held, COALESCE(SUM(bytes), 0) AS held_bytes FROM artifacts WHERE scan_id = ?",
        (str(scan_id),),
    ).fetchone()
    return row["held"], row["held_bytes"]


def owner_scan_count(connection: sqlite3.Connection, owner_id: uuid.UUID) -> int:
    return connection.execute("SELECT COUNT(*) FROM scans WHERE owner_id = ?", (str(owner_id),)).fetchone()[0]


def owner_artifact_bytes(connection: sqlite3.Connection, owner_id: uuid.UUID) -> int:
    """Every uploaded byte across the owner's scans."""
    return connection.execute(
        "SELECT COALESCE(SUM(artifacts.bytes), 0) FROM artifacts"
        " JOIN scans ON scans.id = artifacts.scan_id WHERE scans.owner_id = ?",
        (str(owner_id),),
    ).fetchone()[0]


def artifact_of_kind(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str) -> Artifact | None:
    row = connection.execute(
        "SELECT id, kind, sha256, bytes FROM artifacts WHERE scan_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
        (str(scan_id), kind),
    ).fetchone()
    return Artifact(id=row["id"], kind=row["kind"], sha256=row["sha256"], bytes=row["bytes"]) if row else None


def artifacts_of_kind(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str) -> list[Artifact]:
    rows = connection.execute(
        "SELECT id, kind, sha256, bytes FROM artifacts WHERE scan_id = ? AND kind = ? ORDER BY created_at, id",
        (str(scan_id), kind),
    ).fetchall()
    return [Artifact(id=row["id"], kind=row["kind"], sha256=row["sha256"], bytes=row["bytes"]) for row in rows]


def insert_artifact(connection: sqlite3.Connection, scan_id: uuid.UUID, artifact: Artifact) -> None:
    connection.execute(
        "INSERT INTO artifacts (scan_id, id, kind, sha256, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(scan_id), artifact.id, artifact.kind, artifact.sha256, artifact.bytes, now()),
    )


def missing_required(scan: Scan) -> list[str]:
    present = {artifact.kind for artifact in scan.artifacts}
    return sorted(kind for kind in REQUIRED_ARTIFACT_KINDS if kind not in present)


def content_hash(scan: Scan) -> str:
    lines = sorted(f"{artifact.id}:{artifact.sha256}" for artifact in scan.artifacts)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def build_evidence_bundle(scan: Scan, version: int, created_at: str | None = None) -> EvidenceBundle:
    """Close the scan's current artifacts into one versioned bundle.

    The manifest hashes only the semantic-input kinds (frozen set above), so a
    bundle changes exactly when recognition inputs change. `complete` means both
    the geometry and the semantics required kinds are present.
    """
    by_kind: dict[str, list[str]] = {}
    for artifact in scan.artifacts:
        by_kind.setdefault(artifact.kind, []).append(artifact.sha256)
    present = set(by_kind)
    required = set(GEOMETRY_REQUIRED_ARTIFACT_KINDS) | set(SEMANTIC_REQUIRED_ARTIFACT_KINDS)
    missing = sorted(required - present)
    reasons: list[str] = []
    if missing:
        reasons.append(f"missing artifacts: {', '.join(missing)}")
    manifest_lines = sorted(
        f"{kind}:{','.join(sorted(shas))}" for kind, shas in by_kind.items() if kind in SEMANTIC_INPUT_KINDS
    )
    manifest_hash = hashlib.sha256("\n".join(manifest_lines).encode("utf-8")).hexdigest()
    latest_shas = {kind: shas[-1] for kind, shas in by_kind.items()}
    return EvidenceBundle(
        version=version,
        manifest_hash=manifest_hash,
        artifact_ids=[artifact.id for artifact in scan.artifacts],
        artifact_hashes=latest_shas,
        complete=not missing,
        missing_required_kinds=missing,
        reasons=reasons,
        created_at=datetime.fromisoformat(created_at) if created_at else None,
    )


def insert_bundle(connection: sqlite3.Connection, scan_id: uuid.UUID, bundle: EvidenceBundle) -> None:
    connection.execute(
        "INSERT INTO evidence_bundles (scan_id, version, manifest_hash, artifact_ids_json,"
        " artifact_hashes_json, complete, missing_required_kinds_json, reasons_json,"
        " created_at, semantic_processed_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(scan_id),
            bundle.version,
            bundle.manifest_hash,
            json.dumps(bundle.artifact_ids),
            json.dumps(bundle.artifact_hashes),
            int(bundle.complete),
            json.dumps(bundle.missing_required_kinds),
            json.dumps(bundle.reasons),
            bundle.created_at.isoformat() if bundle.created_at else now(),
            bundle.semantic_processed_hash,
        ),
    )


def latest_bundle(connection: sqlite3.Connection, scan_id: uuid.UUID) -> EvidenceBundle | None:
    row = connection.execute(
        "SELECT * FROM evidence_bundles WHERE scan_id = ? ORDER BY version DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()
    if row is None:
        return None
    return EvidenceBundle(
        version=row["version"],
        manifest_hash=row["manifest_hash"],
        artifact_ids=json.loads(row["artifact_ids_json"]),
        artifact_hashes=json.loads(row["artifact_hashes_json"]),
        complete=bool(row["complete"]),
        missing_required_kinds=json.loads(row["missing_required_kinds_json"]),
        reasons=json.loads(row["reasons_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        semantic_processed_hash=row["semantic_processed_hash"],
    )


def bundle_processed(connection: sqlite3.Connection, scan_id: uuid.UUID, version: int, manifest_hash: str) -> None:
    """Record that a semantic job consumed this exact bundle's manifest."""
    connection.execute(
        "UPDATE evidence_bundles SET semantic_processed_hash = ? WHERE scan_id = ? AND version = ?",
        (manifest_hash, str(scan_id), version),
    )


def latest_semantic_arrival(connection: sqlite3.Connection, scan_id: uuid.UUID) -> str | None:
    """The newest created_at among semantic-input artifacts, for the settle gate."""
    placeholders = ", ".join("?" for _ in SEMANTIC_INPUT_KINDS)
    row = connection.execute(
        f"SELECT MAX(created_at) AS latest FROM artifacts WHERE scan_id = ? AND kind IN ({placeholders})",
        (str(scan_id), *SEMANTIC_INPUT_KINDS),
    ).fetchone()
    return row["latest"] if row else None


def mark_finalized(connection: sqlite3.Connection, scan: Scan, coverage: list[SurfaceCoverage]) -> None:
    connection.execute(
        "UPDATE scans SET state = 'measuring', content_hash = ?, coverage_json = ? WHERE id = ?",
        (content_hash(scan), json.dumps([c.model_dump(mode="json") for c in coverage]), str(scan.id)),
    )
