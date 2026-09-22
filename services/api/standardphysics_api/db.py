"""SQLite, one short-lived connection per unit of work.

Connections run in autocommit mode so `transaction` can issue BEGIN IMMEDIATE
itself. That takes the write lock up front, which is what makes finalize and
job claiming safe when two requests arrive together.
"""

from __future__ import annotations

import contextlib
import pathlib
import sqlite3
from collections.abc import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    device_model TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    state TEXT NOT NULL,
    content_hash TEXT,
    coverage_json TEXT NOT NULL DEFAULT '[]',
    owner_id TEXT REFERENCES owners(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    scan_id TEXT NOT NULL REFERENCES scans(id),
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL REFERENCES scans(id),
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    input_hash TEXT,
    note TEXT,
    UNIQUE (scan_id, kind, revision)
);
CREATE TABLE IF NOT EXISTS revisions (
    scan_id TEXT NOT NULL REFERENCES scans(id),
    revision INTEGER NOT NULL,
    graph_hash TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    source TEXT NOT NULL,
    base_revision INTEGER,
    glb_path TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, revision)
);
CREATE TABLE IF NOT EXISTS scenarios (
    scan_id TEXT PRIMARY KEY REFERENCES scans(id),
    scenario_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS simulations (
    scan_id TEXT NOT NULL REFERENCES scans(id),
    revision INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    scenario_json TEXT NOT NULL,
    mesh_artifact_id TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    cycle INTEGER NOT NULL DEFAULT 0,
    candidate_graph_json TEXT,
    result_json TEXT,
    PRIMARY KEY (scan_id, revision)
);
CREATE TABLE IF NOT EXISTS texture_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL REFERENCES scans(id),
    build_key TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(scan_id, build_key)
);
CREATE TABLE IF NOT EXISTS owners (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    shop_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_owner ON sessions(owner_id);
CREATE INDEX IF NOT EXISTS scans_by_owner ON scans(owner_id, created_at DESC);
CREATE TABLE IF NOT EXISTS assessments (
    id TEXT PRIMARY KEY,
    scan_id TEXT NOT NULL REFERENCES scans(id),
    graph_revision INTEGER NOT NULL,
    assessment_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    scenario_version INTEGER
);
CREATE TABLE IF NOT EXISTS evidence_bundles (
    scan_id TEXT NOT NULL REFERENCES scans(id),
    version INTEGER NOT NULL,
    manifest_hash TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
    complete INTEGER NOT NULL DEFAULT 0,
    missing_required_kinds_json TEXT NOT NULL DEFAULT '[]',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    semantic_processed_hash TEXT,
    PRIMARY KEY (scan_id, version)
);
"""


ADDED_COLUMNS = {
    "simulations": (
        ("cycle", "INTEGER NOT NULL DEFAULT 0"),
        ("candidate_graph_json", "TEXT"),
    ),
    "scans": (("owner_id", "TEXT REFERENCES owners(id)"),),
    "jobs": (
        ("input_hash", "TEXT"),
        ("note", "TEXT"),
    ),
    "scenarios": (("version", "INTEGER NOT NULL DEFAULT 0"),),
    "assessments": (("scenario_version", "INTEGER"),),
}
"""Columns that arrived after a table shipped, by the table they belong to.

`scans.owner_id` is nullable because a database written before owners existed
has rows that predate the column. `repository.list_scans` filters on it, so an
unclaimed scan is visible to nobody until someone adopts it.
"""


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema.

    Runs before `executescript` so that a CREATE INDEX over a new column finds
    the column already there.
    """
    for table, columns in ADDED_COLUMNS.items():
        present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not present:
            continue
        for name, definition in columns:
            if name not in present:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


class Database:
    def __init__(self, path: pathlib.Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            _add_missing_columns(connection)
            connection.executescript(SCHEMA)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
