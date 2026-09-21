"""Rows in, contract models out. Nothing here knows about HTTP."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime

from standardphysics_contracts import (
    Artifact,
    Assessment,
    CreateScanRequest,
    Scan,
    Scenario,
    SceneGraph,
    SurfaceCoverage,
    graph_hash,
)

REQUIRED_ARTIFACT_KINDS = ("room_json", "room_usdz")


def now() -> str:
    return datetime.now(UTC).isoformat()


def _artifacts(connection: sqlite3.Connection, scan_id: str) -> list[Artifact]:
    rows = connection.execute(
        "SELECT id, kind, sha256, bytes FROM artifacts WHERE scan_id = ? ORDER BY created_at, id",
        (scan_id,),
    )
    return [Artifact(id=r["id"], kind=r["kind"], sha256=r["sha256"], bytes=r["bytes"]) for r in rows]


def _scan(connection: sqlite3.Connection, row: sqlite3.Row) -> Scan:
    return Scan(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        device_model=row["device_model"],
        duration_seconds=row["duration_seconds"],
        state=row["state"],
        artifacts=_artifacts(connection, row["id"]),
        coverage=[SurfaceCoverage.model_validate(c) for c in json.loads(row["coverage_json"])],
        content_hash=row["content_hash"],
    )


def insert_scan(
    connection: sqlite3.Connection,
    request: CreateScanRequest,
    owner_id: uuid.UUID,
    scan_id: uuid.UUID | None = None,
    state: str = "uploading",
) -> uuid.UUID:
    scan_id = scan_id or uuid.uuid4()
    connection.execute(
        "INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state, owner_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(scan_id), request.name, now(), request.device_model, request.duration_seconds, state, str(owner_id)),
    )
    return scan_id


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


def scan_exists(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    return connection.execute("SELECT 1 FROM scans WHERE id = ?", (str(scan_id),)).fetchone() is not None


CHILD_TABLES = ("texture_builds", "simulations", "assessments", "scenarios", "revisions", "jobs", "artifacts")
"""Everything that references a scan, deepest first.

SQLite does not enforce the foreign keys by default, so leaving a child row
behind would not fail loudly. It would sit in the database pointing at a scan
that no longer exists until something joined on it.
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


def mark_finalized(
    connection: sqlite3.Connection, scan: Scan, coverage: list[SurfaceCoverage]
) -> None:
    connection.execute(
        "UPDATE scans SET state = 'measuring', content_hash = ?, coverage_json = ? WHERE id = ?",
        (content_hash(scan), json.dumps([c.model_dump(mode="json") for c in coverage]), str(scan.id)),
    )


def enqueue_job(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO jobs (scan_id, kind, revision, state, created_at) VALUES (?, ?, ?, 'queued', ?)",
        (str(scan_id), kind, revision, now()),
    )


def queue_job_again(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    """Queue a job whether or not it ran before, unless it is running now."""
    connection.execute(
        "INSERT INTO jobs (scan_id, kind, revision, state, created_at) VALUES (?, ?, ?, 'queued', ?)"
        " ON CONFLICT (scan_id, kind, revision) DO UPDATE SET state = 'queued', error = NULL"
        " WHERE jobs.state != 'running'",
        (str(scan_id), kind, revision, now()),
    )


def claim_job(connection: sqlite3.Connection, texture_only: bool | None = None) -> sqlite3.Row | None:
    return connection.execute(
        "UPDATE jobs SET state = 'running', attempts = attempts + 1"
        " WHERE id = (SELECT id FROM jobs WHERE state = 'queued'"
        " AND (? IS NULL OR (kind='texture')=?) ORDER BY id LIMIT 1)"
        " RETURNING id, scan_id, kind, revision",
        (texture_only, texture_only),
    ).fetchone()


def finish_job(connection: sqlite3.Connection, job_id: int, error: str | None = None) -> None:
    state = "failed" if error else "done"
    connection.execute("UPDATE jobs SET state = ?, error = ? WHERE id = ?", (state, error, job_id))


def retry_failed_jobs(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    """Queue again whichever stage failed, and show the state that stage runs in."""
    kinds = {
        row["kind"]
        for row in connection.execute(
            "SELECT kind FROM jobs WHERE scan_id = ? AND state = 'failed'"
            " AND kind NOT IN ('display', 'simulate', 'texture')",
            (str(scan_id),),
        )
    }
    if not kinds:
        return
    state = "measuring" if "process" in kinds else "checking"
    connection.execute("UPDATE scans SET state = ? WHERE id = ?", (state, str(scan_id)))
    connection.execute(
        "UPDATE jobs SET state = 'queued', error = NULL WHERE scan_id = ? AND state = 'failed'"
        " AND kind NOT IN ('display', 'simulate', 'texture')",
        (str(scan_id),),
    )


def requeue_interrupted_jobs(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE jobs SET state = 'queued' WHERE state = 'running'")


_REVISION_WRITE = {"owner": "INSERT INTO", "ingest": "INSERT INTO", "other": "INSERT OR IGNORE INTO"}
_REVISION_CONFLICT = {
    "ingest": " ON CONFLICT (scan_id, revision) DO UPDATE SET"
              " graph_hash = excluded.graph_hash, graph_json = excluded.graph_json,"
              " created_at = excluded.created_at",
}
"""An ingest revision is derived entirely from the uploaded artifacts, so running
ingest again replaces it. Everything an owner did stands on its own revision and
is never overwritten."""


def save_revision(
    connection: sqlite3.Connection,
    graph: SceneGraph,
    source: str,
    base_revision: int | None = None,
    glb_path: str | None = None,
) -> None:
    connection.execute(
        f"{_REVISION_WRITE[source if source in _REVISION_WRITE else 'other']} revisions"
        " (scan_id, revision, graph_hash, graph_json, source, base_revision, glb_path, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        f"{_REVISION_CONFLICT.get(source, '')}",
        (str(graph.scan_id), graph.revision, graph_hash(graph), graph.model_dump_json(),
         source, base_revision, glb_path, now()),
    )


def get_revision(
    connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int | None = None
) -> sqlite3.Row | None:
    if revision is None:
        return connection.execute(
            "SELECT * FROM revisions WHERE scan_id = ? ORDER BY revision DESC LIMIT 1", (str(scan_id),)
        ).fetchone()
    return connection.execute(
        "SELECT * FROM revisions WHERE scan_id = ? AND revision = ?", (str(scan_id), revision)
    ).fetchone()


def graph_of(row: sqlite3.Row) -> SceneGraph:
    return SceneGraph.model_validate_json(row["graph_json"])


def display_geometry(
    connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int | None = None
) -> tuple[str, int] | None:
    """The newest GLB, and the revision whose layout it was exported from."""
    row = connection.execute(
        "SELECT glb_path, revision FROM revisions WHERE scan_id = ? AND glb_path IS NOT NULL"
        " AND (? IS NULL OR revision = ?)"
        " ORDER BY revision DESC LIMIT 1",
        (str(scan_id), revision, revision),
    ).fetchone()
    return (row["glb_path"], row["revision"]) if row else None


def display_pending(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    return connection.execute(
        "SELECT 1 FROM jobs WHERE scan_id = ? AND kind IN ('process', 'assess', 'display')"
        " AND state IN ('queued', 'running') LIMIT 1", (str(scan_id),),
    ).fetchone() is not None


def base_glb_path(connection: sqlite3.Connection, scan_id: uuid.UUID) -> str | None:
    found = display_geometry(connection, scan_id)
    return found[0] if found else None


def set_glb_path(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int, path: str) -> None:
    connection.execute(
        "UPDATE revisions SET glb_path = ? WHERE scan_id = ? AND revision = ?", (path, str(scan_id), revision)
    )


def save_scenario(connection: sqlite3.Connection, scan_id: uuid.UUID, scenario: Scenario) -> None:
    connection.execute(
        "INSERT INTO scenarios (scan_id, scenario_json) VALUES (?, ?)"
        " ON CONFLICT (scan_id) DO UPDATE SET scenario_json = excluded.scenario_json",
        (str(scan_id), scenario.model_dump_json()),
    )


def get_scenario(connection: sqlite3.Connection, scan_id: uuid.UUID) -> Scenario | None:
    row = connection.execute("SELECT scenario_json FROM scenarios WHERE scan_id = ?", (str(scan_id),)).fetchone()
    return Scenario.model_validate_json(row["scenario_json"]) if row else None


def save_assessment(connection: sqlite3.Connection, assessment: Assessment) -> None:
    connection.execute(
        "INSERT INTO assessments (id, scan_id, graph_revision, assessment_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET assessment_json = excluded.assessment_json",
        (str(assessment.id), str(assessment.scan_id), assessment.graph_revision,
         assessment.model_dump_json(), now()),
    )


def latest_assessment(connection: sqlite3.Connection, scan_id: uuid.UUID) -> Assessment | None:
    row = connection.execute(
        "SELECT assessment_json FROM assessments WHERE scan_id = ?"
        " ORDER BY graph_revision DESC, created_at DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()
    return Assessment.model_validate_json(row["assessment_json"]) if row else None


def assessment_for_revision(
    connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int
) -> Assessment | None:
    row = connection.execute(
        "SELECT assessment_json FROM assessments WHERE scan_id = ? AND graph_revision = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (str(scan_id), revision),
    ).fetchone()
    return Assessment.model_validate_json(row["assessment_json"]) if row else None
