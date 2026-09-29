"""Graph revisions, the scenario a scan is checked against, and the assessments made from them."""

from __future__ import annotations

import sqlite3
import uuid

from standardphysics_agents.checks_version import checks_version
from standardphysics_contracts import Assessment, Scenario, SceneGraph, graph_hash

from . import repository as repo

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
            repo.now(),
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
            repo.now(),
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
