"""SQLite, one short-lived connection per unit of work.

Connections run in autocommit mode so `transaction` can issue BEGIN IMMEDIATE
itself. That takes the write lock up front, which is what makes finalize and
job claiming safe when two requests arrive together.

The schema is built by `MIGRATIONS`, a numbered list that only ever grows at
the end. `schema_migrations` records each version a database has run and when,
so a start runs only the versions it has not recorded, in order, each in its
own transaction.
"""

from __future__ import annotations

import contextlib
import dataclasses
import pathlib
import sqlite3
from collections.abc import Callable, Iterator, Sequence

Presence = Callable[[sqlite3.Connection], bool]


@dataclasses.dataclass(frozen=True)
class Migration:
    """One additive step of the schema: new tables, columns or indexes, never a drop or rename.

    `already_present` is asked once per database, on the first start after
    numbered migrations shipped, to recognise what the earlier
    add-missing-columns code had already built there.
    """

    version: int
    name: str
    statements: tuple[str, ...]
    already_present: Presence


class MigrationError(RuntimeError):
    pass


def objects_exist(*names: str) -> Presence:
    """True when every named table or index is in the database."""

    def present(connection: sqlite3.Connection) -> bool:
        placeholders = ", ".join("?" * len(names))
        found = connection.execute(
            f"SELECT count(*) FROM sqlite_master WHERE name IN ({placeholders})", names
        ).fetchone()[0]
        return found == len(names)

    return present


def columns_exist(table: str, *columns: str) -> Presence:
    def present(connection: sqlite3.Connection) -> bool:
        found = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        return set(columns) <= found

    return present


def _creates(version: int, name: str, objects: tuple[str, ...], *statements: str) -> Migration:
    """Tables and indexes, each written IF NOT EXISTS so a database holding some of them still runs it."""
    return Migration(version, name, statements, objects_exist(*objects))


def _adds_column(version: int, table: str, column: str, definition: str) -> Migration:
    """One column per migration, because SQLite has no ADD COLUMN IF NOT EXISTS.

    A migration adding two columns could meet a database holding only one of
    them and fail on the duplicate; a migration adding one is either wholly
    present or wholly missing.
    """
    statement = f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
    return Migration(version, f"add_{table}_{column}", (statement,), columns_exist(table, column))


MIGRATIONS: tuple[Migration, ...] = (
    _creates(
        1,
        "create_core_tables",
        ("scans", "artifacts", "jobs", "revisions", "scenarios"),
        """CREATE TABLE IF NOT EXISTS scans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            device_model TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            state TEXT NOT NULL,
            content_hash TEXT,
            coverage_json TEXT NOT NULL DEFAULT '[]'
        )""",
        """CREATE TABLE IF NOT EXISTS artifacts (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scan_id, id)
        )""",
        """CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            kind TEXT NOT NULL,
            revision INTEGER NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (scan_id, kind, revision)
        )""",
        """CREATE TABLE IF NOT EXISTS revisions (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            revision INTEGER NOT NULL,
            graph_hash TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            source TEXT NOT NULL,
            base_revision INTEGER,
            glb_path TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scan_id, revision)
        )""",
        """CREATE TABLE IF NOT EXISTS scenarios (
            scan_id TEXT PRIMARY KEY REFERENCES scans(id),
            scenario_json TEXT NOT NULL
        )""",
    ),
    _creates(
        2,
        "create_simulations",
        ("simulations",),
        """CREATE TABLE IF NOT EXISTS simulations (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            revision INTEGER NOT NULL,
            request_json TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            scenario_json TEXT NOT NULL,
            mesh_artifact_id TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            PRIMARY KEY (scan_id, revision)
        )""",
    ),
    _adds_column(3, "simulations", "cycle", "INTEGER NOT NULL DEFAULT 0"),
    _adds_column(4, "simulations", "candidate_graph_json", "TEXT"),
    _creates(
        5,
        "create_texture_builds",
        ("texture_builds",),
        """CREATE TABLE IF NOT EXISTS texture_builds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            build_key TEXT NOT NULL,
            graph_json TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(scan_id, build_key)
        )""",
    ),
    _creates(
        6,
        "create_owners_and_sessions",
        ("owners", "sessions", "sessions_by_owner"),
        """CREATE TABLE IF NOT EXISTS owners (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            shop_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES owners(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS sessions_by_owner ON sessions(owner_id)",
    ),
    _adds_column(7, "scans", "owner_id", "TEXT REFERENCES owners(id)"),
    _creates(
        8,
        "create_scans_by_owner",
        ("scans_by_owner",),
        "CREATE INDEX IF NOT EXISTS scans_by_owner ON scans(owner_id, created_at DESC)",
    ),
    _creates(
        9,
        "create_assessments",
        ("assessments",),
        """CREATE TABLE IF NOT EXISTS assessments (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            graph_revision INTEGER NOT NULL,
            assessment_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ),
    _adds_column(10, "jobs", "input_hash", "TEXT"),
    _adds_column(11, "jobs", "note", "TEXT"),
    _adds_column(12, "scenarios", "version", "INTEGER NOT NULL DEFAULT 0"),
    _adds_column(13, "assessments", "scenario_version", "INTEGER"),
    _creates(
        14,
        "create_evidence_bundles",
        ("evidence_bundles",),
        """CREATE TABLE IF NOT EXISTS evidence_bundles (
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
        )""",
    ),
    _adds_column(15, "jobs", "model_requests_json", "TEXT"),
    _creates(
        16,
        "create_job_attempts",
        ("job_attempts",),
        """CREATE TABLE IF NOT EXISTS job_attempts (
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            attempt INTEGER NOT NULL,
            scan_id TEXT NOT NULL,
            input_hash TEXT,
            state TEXT NOT NULL,
            error TEXT,
            note TEXT,
            model_requests_json TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (job_id, attempt)
        )""",
    ),
    _creates(
        17,
        "create_owner_requests",
        ("owner_requests", "owner_requests_waiting"),
        """CREATE TABLE IF NOT EXISTS owner_requests (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            request_id TEXT NOT NULL,
            status TEXT NOT NULL,
            answer_yes INTEGER,
            answer_number REAL,
            photo_name TEXT,
            answered_at TEXT,
            review TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            PRIMARY KEY (scan_id, request_id)
        )""",
        "CREATE INDEX IF NOT EXISTS owner_requests_waiting ON owner_requests(status, answered_at)",
    ),
    _adds_column(18, "owners", "guest", "INTEGER NOT NULL DEFAULT 0"),
    _adds_column(19, "owners", "apple_sub", "TEXT"),
    _creates(
        20,
        "create_owners_by_apple",
        ("owners_by_apple",),
        "CREATE UNIQUE INDEX IF NOT EXISTS owners_by_apple ON owners(apple_sub) WHERE apple_sub IS NOT NULL",
    ),
    _creates(
        21,
        "create_devices",
        ("devices", "devices_by_owner"),
        """CREATE TABLE IF NOT EXISTS devices (
            token TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES owners(id),
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS devices_by_owner ON devices(owner_id)",
    ),
    _adds_column(22, "owners", "reminded_at", "TEXT"),
    _adds_column(23, "scans", "last_opened_at", "TEXT"),
    _adds_column(24, "scans", "results_told_at", "TEXT"),
    _creates(
        25,
        "create_share_links",
        ("share_links",),
        """CREATE TABLE IF NOT EXISTS share_links (
            token_hash TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )""",
    ),
    _creates(
        26,
        "create_layout_plans",
        ("layout_plans",),
        """CREATE TABLE IF NOT EXISTS layout_plans (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            base_revision INTEGER NOT NULL,
            name TEXT NOT NULL,
            moves_json TEXT NOT NULL,
            findings_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ),
    _adds_column(27, "scans", "replaces_scan_id", "TEXT"),
    _adds_column(28, "scans", "deleting_at", "TEXT"),
    _adds_column(29, "jobs", "queued_at", "TEXT"),
    _adds_column(30, "scans", "space_typology", "TEXT"),
    _adds_column(31, "scans", "owner_wishes_json", "TEXT NOT NULL DEFAULT '[]'"),
    _creates(
        32,
        "create_applied_steps",
        ("applied_steps",),
        """CREATE TABLE IF NOT EXISTS applied_steps (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )""",
    ),
    _adds_column(33, "owners", "team", "INTEGER NOT NULL DEFAULT 0"),
    _creates(
        34,
        "create_checklist_items",
        ("checklist_items",),
        """CREATE TABLE IF NOT EXISTS checklist_items (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            finding_id TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scan_id, finding_id)
        )""",
    ),
    _adds_column(35, "jobs", "interruptions", "INTEGER NOT NULL DEFAULT 0"),
)
"""Every schema change, oldest first. Add a change as the next version at the end.

`scans.owner_id` is nullable because a database written before owners existed
has rows that predate the column. `repository.list_scans` filters on it, so an
unclaimed scan is visible to nobody until someone adopts it.
"""

VERSION_TABLE = """CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    detected INTEGER NOT NULL DEFAULT 0
)"""
"""`detected` is 1 for a version found already built on the first versioned start, not run."""


@contextlib.contextmanager
def _immediate(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _has_any_table(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _record(connection: sqlite3.Connection, migration: Migration, *, detected: bool) -> None:
    connection.execute(
        "INSERT INTO schema_migrations (version, name, applied_at, detected) VALUES (?, ?, datetime('now'), ?)",
        (migration.version, migration.name, int(detected)),
    )


def _start_recording_versions(connection: sqlite3.Connection, migrations: Sequence[Migration]) -> None:
    """Create the version table, crediting what a database from before it already has.

    A database with tables but no version table was built by the
    add-missing-columns code, which brought every table and column up to date
    on each start. The versions whose tables, columns and indexes are all
    there are recorded as detected here, once, and never run; the rest run
    next like on any other database.
    """
    with _immediate(connection):
        if objects_exist("schema_migrations")(connection):
            return
        built_before_versioning = _has_any_table(connection)
        connection.execute(VERSION_TABLE)
        if not built_before_versioning:
            return
        for migration in migrations:
            if migration.already_present(connection):
                _record(connection, migration, detected=True)


def _is_recorded(connection: sqlite3.Connection, migration: Migration) -> bool:
    row = connection.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (migration.version,)).fetchone()
    return row is not None


def _run(connection: sqlite3.Connection, migration: Migration) -> None:
    with _immediate(connection):
        if _is_recorded(connection, migration):
            return
        try:
            for statement in migration.statements:
                connection.execute(statement)
        except sqlite3.Error as error:
            raise MigrationError(f"Migration {migration.version} ({migration.name}) failed: {error}") from error
        _record(connection, migration, detected=False)


def migrate(database: Database, migrations: Sequence[Migration]) -> None:
    """Run every migration this database has not recorded, oldest first.

    Each runs in its own BEGIN IMMEDIATE transaction and is recorded inside it,
    so a failure rolls that one back, leaves the database at the version before
    it, and stops the start with an error naming it. The lock also means a
    second process starting at the same moment waits and then skips what the
    first one ran.
    """
    with database.connect() as connection:
        _start_recording_versions(connection, migrations)
        for migration in migrations:
            _run(connection, migration)


def first_time(connection: sqlite3.Connection, step: str) -> bool:
    """True the first time a one-off data step asks, and False every time after.

    Some changes can't be expressed as a migration: they read the rows, or the
    server's settings, as they stand on the day the step runs. Recording the
    step's name inside the same transaction means it runs once per database,
    however many times the server restarts.
    """
    cursor = connection.execute(
        "INSERT OR IGNORE INTO applied_steps (name, applied_at) VALUES (?, datetime('now'))", (step,)
    )
    return cursor.rowcount == 1


class Database:
    def __init__(self, path: pathlib.Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        migrate(self, MIGRATIONS)

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
        with self.connect() as connection, _immediate(connection):
            yield connection
