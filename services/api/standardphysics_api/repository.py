"""Rows in, contract models out. Nothing here knows about HTTP."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime

from standardphysics_agents.checks_version import checks_version

from standardphysics_contracts import (
    GEOMETRY_REQUIRED_ARTIFACT_KINDS,
    SEMANTIC_REQUIRED_ARTIFACT_KINDS,
    Artifact,
    Assessment,
    CreateScanRequest,
    EvidenceBundle,
    OwnerWish,
    Scan,
    Scenario,
    SceneGraph,
    SpaceTypology,
    SurfaceCoverage,
    graph_hash,
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


def other_running_job(connection: sqlite3.Connection, scan_id: uuid.UUID, job_id: int | None = None) -> bool:
    """True when a job for this scan is running, not counting the one given."""
    return connection.execute(
        "SELECT 1 FROM jobs WHERE scan_id = ? AND state = 'running' AND id IS NOT ?", (str(scan_id), job_id)
    ).fetchone() is not None


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
    "texture_builds", "simulations", "rearrangements", "assessments", "evidence_bundles", "scenarios",
    "revisions", "job_attempts", "jobs", "artifacts",
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


def queued_job_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM jobs WHERE state = 'queued'").fetchone()[0]


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


def has_pending_process_job(connection: sqlite3.Connection, scan_id: uuid.UUID) -> bool:
    """True while a semantic job is queued or running. Kind string matches worker.PROCESS."""
    return (
        connection.execute(
            "SELECT 1 FROM jobs WHERE scan_id = ? AND kind = 'process' AND state IN ('queued', 'running') LIMIT 1",
            (str(scan_id),),
        ).fetchone()
        is not None
    )


def latest_semantic_arrival(connection: sqlite3.Connection, scan_id: uuid.UUID) -> str | None:
    """The newest created_at among semantic-input artifacts, for the settle gate."""
    placeholders = ", ".join("?" for _ in SEMANTIC_INPUT_KINDS)
    row = connection.execute(
        f"SELECT MAX(created_at) AS latest FROM artifacts WHERE scan_id = ? AND kind IN ({placeholders})",
        (str(scan_id), *SEMANTIC_INPUT_KINDS),
    ).fetchone()
    return row["latest"] if row else None


def latest_process_job(connection: sqlite3.Connection, scan_id: uuid.UUID) -> sqlite3.Row | None:
    """The newest process job row, or None when the scan has never queued one."""
    return connection.execute(
        "SELECT * FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()


def set_job_binding(
    connection: sqlite3.Connection,
    job_id: int,
    input_hash: str | None,
    note: str | None,
) -> None:
    """Record which input manifest this run consumed, and its visible outcome.

    Runs at claim time and again at publish, so a fresh attempt starts from a
    clean latest view: the previous attempt's note and request receipts are
    cleared here, and only this attempt's own writes land afterwards.
    """
    connection.execute(
        "UPDATE jobs SET input_hash = ?, note = ?, model_requests_json = NULL WHERE id = ?",
        (input_hash, note, job_id),
    )


def set_job_requests(connection: sqlite3.Connection, job_id: int, requests_json: str | None) -> None:
    """Persist what every real detector request was: provider, model, provider
    request id and usage. Categories and counts only; never a secret or a pixel."""
    connection.execute(
        "UPDATE jobs SET model_requests_json = ? WHERE id = ?",
        (requests_json, job_id),
    )


def record_job_attempt(
    connection: sqlite3.Connection,
    job_id: int,
    attempt: int,
    scan_id: uuid.UUID,
) -> None:
    """Snap the finished job row into immutable per-attempt history.

    Each attempt gets its own (job, attempt) row that is never updated: what
    the attempt consumed, how it ended and which provider requests it made.
    The mutable jobs row stays the latest active view and is cleared at each
    claim, so an empty or failed attempt can never inherit the previous
    attempt's receipts.
    """
    row = connection.execute(
        "SELECT state, error, input_hash, note, model_requests_json FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return
    connection.execute(
        "INSERT INTO job_attempts (job_id, attempt, scan_id, input_hash, state, error, note,"
        " model_requests_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (job_id, attempt) DO NOTHING",
        (
            job_id,
            attempt,
            str(scan_id),
            row["input_hash"],
            row["state"],
            row["error"],
            row["note"],
            row["model_requests_json"],
            now(),
        ),
    )


def process_job_states(connection: sqlite3.Connection, scan_id: uuid.UUID) -> tuple[str, ...]:
    """Every state a process job has been in for this scan, newest first."""
    rows = connection.execute(
        "SELECT state FROM jobs WHERE scan_id = ? AND kind = 'process' ORDER BY id DESC",
        (str(scan_id),),
    ).fetchall()
    return tuple(row["state"] for row in rows)


def mark_finalized(connection: sqlite3.Connection, scan: Scan, coverage: list[SurfaceCoverage]) -> None:
    connection.execute(
        "UPDATE scans SET state = 'measuring', content_hash = ?, coverage_json = ? WHERE id = ?",
        (content_hash(scan), json.dumps([c.model_dump(mode="json") for c in coverage]), str(scan.id)),
    )


def enqueue_job(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    queued_at = now()
    connection.execute(
        "INSERT OR IGNORE INTO jobs (scan_id, kind, revision, state, created_at, queued_at)"
        " VALUES (?, ?, ?, 'queued', ?, ?)",
        (str(scan_id), kind, revision, queued_at, queued_at),
    )


def queue_job_again(connection: sqlite3.Connection, scan_id: uuid.UUID, kind: str, revision: int) -> None:
    """Queue a job whether or not it ran before, unless it is running now."""
    queued_at = now()
    connection.execute(
        "INSERT INTO jobs (scan_id, kind, revision, state, created_at, queued_at) VALUES (?, ?, ?, 'queued', ?, ?)"
        " ON CONFLICT (scan_id, kind, revision) DO UPDATE SET state = 'queued', error = NULL,"
        " queued_at = excluded.queued_at WHERE jobs.state != 'running'",
        (str(scan_id), kind, revision, queued_at, queued_at),
    )


def oldest_queued_job_seconds(connection: sqlite3.Connection) -> float | None:
    """How long the job at the front of the queue has waited, or None when nothing is queued.

    A number that keeps growing while the worker reports itself idle means jobs
    are arriving and nothing is taking them.
    """
    row = connection.execute(
        "SELECT MIN(COALESCE(queued_at, created_at)) AS since FROM jobs WHERE state = 'queued'"
    ).fetchone()
    if row["since"] is None:
        return None
    return round((datetime.now(UTC) - datetime.fromisoformat(row["since"])).total_seconds(), 1)


LANED_KINDS = ("texture", "rearrange", "furniture")
"""Job kinds that run on a worker thread of their own, never the main one."""


def _lane_filter(texture_only: bool | None, kind: str | None) -> tuple[str, tuple]:
    if kind is not None:
        return "kind = ?", (kind,)
    if texture_only:
        return "kind = ?", ("texture",)
    if texture_only is False:
        return "kind NOT IN (" + ", ".join("?" for _ in LANED_KINDS) + ")", LANED_KINDS
    return "1=1", ()


def claim_job(
    connection: sqlite3.Connection, texture_only: bool | None = None, *, kind: str | None = None
) -> sqlite3.Row | None:
    """The oldest queued job on a lane: textures, one kind, everything but the laned kinds, or anything."""
    lane, parameters = _lane_filter(texture_only, kind)
    return connection.execute(
        "UPDATE jobs SET state = 'running', attempts = attempts + 1"
        " WHERE id = (SELECT id FROM jobs WHERE state = 'queued'"
        f" AND {lane} ORDER BY id LIMIT 1)"
        " RETURNING id, scan_id, kind, revision, attempts",
        parameters,
    ).fetchone()


def finish_job(connection: sqlite3.Connection, job_id: int, error: str | None = None) -> None:
    state = "failed" if error else "done"
    connection.execute("UPDATE jobs SET state = ?, error = ? WHERE id = ?", (state, error, job_id))


def fail_running_job(connection: sqlite3.Connection, job_id: int, error: str) -> bool:
    """Fail a job only if it is still running, so an outcome already written is never overwritten."""
    cursor = connection.execute(
        "UPDATE jobs SET state = 'failed', error = ? WHERE id = ? AND state = 'running'", (error, job_id)
    )
    return cursor.rowcount > 0


def requeue_running_job(connection: sqlite3.Connection, job_id: int) -> bool:
    """Put a claimed job back at its place in the queue, if nothing has settled it since."""
    cursor = connection.execute(
        "UPDATE jobs SET state = 'queued', queued_at = ? WHERE id = ? AND state = 'running'", (now(), job_id)
    )
    return cursor.rowcount > 0


def retry_failed_jobs(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    """Queue again whichever stage failed, and show the state that stage runs in."""
    kinds = {
        row["kind"]
        for row in connection.execute(
            "SELECT kind FROM jobs WHERE scan_id = ? AND state = 'failed'"
            " AND kind NOT IN ('display', 'simulate', 'texture', 'rearrange')",
            (str(scan_id),),
        )
    }
    if not kinds:
        return
    state = "measuring" if "process" in kinds else "checking"
    connection.execute("UPDATE scans SET state = ? WHERE id = ?", (state, str(scan_id)))
    connection.execute(
        "UPDATE jobs SET state = 'queued', error = NULL, queued_at = ? WHERE scan_id = ? AND state = 'failed'"
        " AND kind NOT IN ('display', 'simulate', 'texture', 'rearrange')",
        (now(), str(scan_id)),
    )


INTERRUPTED_SIMULATION = "Simulation interrupted; start a new run to continue"


def fail_interrupted_simulations(connection: sqlite3.Connection) -> None:
    """Fail every simulation a stopped process left running, rather than queueing it again.

    A simulation spends paid TypeSafe and Astra calls against a per-run budget
    that lives only in the process running it. Queued again, it would start
    from nothing with a fresh budget and could spend the owner's limit a second
    time without anyone asking, so the owner starts the new run.
    """
    connection.execute(
        "UPDATE jobs SET state = 'failed', error = ? WHERE kind = 'simulate' AND state = 'running'",
        (INTERRUPTED_SIMULATION,),
    )


def requeue_interrupted_jobs(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE jobs SET state = 'queued', queued_at = ? WHERE state = 'running'", (now(),))


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
        (
            str(graph.scan_id),
            graph.revision,
            graph_hash(graph),
            graph.model_dump_json(),
            source,
            base_revision,
            glb_path,
            now(),
        ),
    )


def get_revision(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int | None = None) -> sqlite3.Row | None:
    if revision is None:
        return connection.execute(
            "SELECT * FROM revisions WHERE scan_id = ? ORDER BY revision DESC LIMIT 1", (str(scan_id),)
        ).fetchone()
    return connection.execute(
        "SELECT * FROM revisions WHERE scan_id = ? AND revision = ?", (str(scan_id), revision)
    ).fetchone()


def require_revision(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int) -> sqlite3.Row:
    """A revision a queued job names. The job was queued after it was saved, so its absence is a fault."""
    row = get_revision(connection, scan_id, revision)
    if row is None:
        raise LookupError(f"scan {scan_id} has no revision {revision}")
    return row


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
    return (
        connection.execute(
            "SELECT 1 FROM jobs WHERE scan_id = ? AND kind IN ('process', 'assess', 'display')"
            " AND state IN ('queued', 'running') LIMIT 1",
            (str(scan_id),),
        ).fetchone()
        is not None
    )


def base_glb_path(connection: sqlite3.Connection, scan_id: uuid.UUID) -> str | None:
    found = display_geometry(connection, scan_id)
    return found[0] if found else None


def set_glb_path(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int, path: str) -> None:
    connection.execute(
        "UPDATE revisions SET glb_path = ? WHERE scan_id = ? AND revision = ?", (path, str(scan_id), revision)
    )


def save_scenario(connection: sqlite3.Connection, scan_id: uuid.UUID, scenario: Scenario) -> None:
    """Replace the scenario and advance its version, so derived results saved
    against the old route are visibly stale until they are recomputed."""
    connection.execute(
        "INSERT INTO scenarios (scan_id, scenario_json, version) VALUES (?, ?, 1)"
        " ON CONFLICT (scan_id) DO UPDATE SET"
        " scenario_json = excluded.scenario_json, version = scenarios.version + 1",
        (str(scan_id), scenario.model_dump_json()),
    )


def get_scenario(connection: sqlite3.Connection, scan_id: uuid.UUID) -> Scenario | None:
    row = connection.execute("SELECT scenario_json FROM scenarios WHERE scan_id = ?", (str(scan_id),)).fetchone()
    return Scenario.model_validate_json(row["scenario_json"]) if row else None


def scenario_version(connection: sqlite3.Connection, scan_id: uuid.UUID) -> int | None:
    row = connection.execute("SELECT version FROM scenarios WHERE scan_id = ?", (str(scan_id),)).fetchone()
    return row["version"] if row else None


def save_assessment(connection: sqlite3.Connection, assessment: Assessment) -> None:
    """Stored with the fingerprint of the checks that made it, so a deploy that changes them can check again."""
    connection.execute(
        "INSERT INTO assessments (id, scan_id, graph_revision, assessment_json,"
        " created_at, scenario_version, checks_version)"
        " VALUES (?, ?, ?, ?, ?, (SELECT version FROM scenarios WHERE scan_id = ?), ?)"
        " ON CONFLICT (id) DO UPDATE SET assessment_json = excluded.assessment_json,"
        " scenario_version = excluded.scenario_version, checks_version = excluded.checks_version",
        (
            str(assessment.id),
            str(assessment.scan_id),
            assessment.graph_revision,
            assessment.model_dump_json(),
            now(),
            str(assessment.scan_id),
            checks_version(),
        ),
    )


def results_made_under_other_checks(connection: sqlite3.Connection) -> list[tuple[uuid.UUID, int]]:
    """Each ready shop whose current revision was last checked under different checks, with that revision."""
    rows = connection.execute(
        "SELECT scans.id AS scan_id, latest.revision AS revision FROM scans"
        " JOIN (SELECT scan_id, MAX(revision) AS revision FROM revisions GROUP BY scan_id) AS latest"
        "   ON latest.scan_id = scans.id"
        " JOIN assessments ON assessments.scan_id = scans.id AND assessments.graph_revision = latest.revision"
        " WHERE scans.state = 'ready' AND assessments.created_at = ("
        "   SELECT MAX(created_at) FROM assessments AS newer"
        "   WHERE newer.scan_id = scans.id AND newer.graph_revision = latest.revision)"
        " AND (assessments.checks_version IS NULL OR assessments.checks_version != ?)",
        (checks_version(),),
    ).fetchall()
    return [(uuid.UUID(row["scan_id"]), row["revision"]) for row in rows]


def latest_revision_number(connection: sqlite3.Connection, scan_id: uuid.UUID) -> int | None:
    row = connection.execute(
        "SELECT revision FROM revisions WHERE scan_id = ? ORDER BY revision DESC LIMIT 1",
        (str(scan_id),),
    ).fetchone()
    return row["revision"] if row else None


def latest_assessment(connection: sqlite3.Connection, scan_id: uuid.UUID) -> Assessment | None:
    """The assessment of the scan's current graph revision, or nothing.

    An assessment computed from an older graph must never be read as the
    current one: a role change or re-measure leaves it behind and the next
    assessment job supersedes it. Callers that want a historic snapshot pin the
    revision with `assessment_for_revision`.
    """
    revision = latest_revision_number(connection, scan_id)
    if revision is None:
        return None
    return assessment_for_revision(connection, scan_id, revision)


def assessment_for_revision(connection: sqlite3.Connection, scan_id: uuid.UUID, revision: int) -> Assessment | None:
    """The newest assessment for this graph revision, if it still matches the
    current scenario. A route confirm replaces the scenario, so every result
    measured against the old one is history until the next assess job runs."""
    row = connection.execute(
        "SELECT assessment_json, scenario_version FROM assessments WHERE scan_id = ?"
        " AND graph_revision = ? ORDER BY created_at DESC LIMIT 1",
        (str(scan_id), revision),
    ).fetchone()
    if row is None:
        return None
    current = scenario_version(connection, scan_id)
    stored = row["scenario_version"]
    if stored is not None and current is not None and stored != current:
        return None
    # Assessments saved before any route existed predate the run they should
    # re-measure once a route is confirmed. Databases migrated with a version-0
    # scenario row keep the older tolerance so untouched scans stay readable.
    if stored is None and current is not None and current > 0:
        return None
    return Assessment.model_validate_json(row["assessment_json"])
