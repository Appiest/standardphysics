"""Additive schema migration proofs (contract 2).

A database written by an older server opens unchanged: the new columns and the
evidence_bundles table are added, the existing rows stay readable through the
same repository functions, and a brand-new scan completes its evidence flow on
top of the migrated rows.
"""

import json
import pathlib
import sqlite3

from conftest import create_scan, drain, put_artifact, usdz_fixture


def _legacy_database(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE scans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            device_model TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            state TEXT NOT NULL,
            content_hash TEXT,
            coverage_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE artifacts (
            scan_id TEXT NOT NULL REFERENCES scans(id),
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scan_id, id)
        );
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            kind TEXT NOT NULL,
            revision INTEGER NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (scan_id, kind, revision)
        );
        CREATE TABLE revisions (
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
        CREATE TABLE scenarios (
            scan_id TEXT PRIMARY KEY REFERENCES scans(id),
            scenario_json TEXT NOT NULL
        );
        CREATE TABLE assessments (
            id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scans(id),
            graph_revision INTEGER NOT NULL,
            assessment_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE owners (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            shop_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES owners(id),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state)
        VALUES ('aaaaaaaa-1111-2222-3333-444444444444', 'legacy scan', '2026-09-01T00:00:00+00:00',
                'iPhone12,3', 90.5, 'ready');
        INSERT INTO artifacts (scan_id, id, kind, sha256, bytes, created_at)
        VALUES ('aaaaaaaa-1111-2222-3333-444444444444', 'room-json', 'room_json',
                'f' * 64, 2, '2026-09-01T00:00:01+00:00');
        """
    )
    connection.commit()
    connection.close()


def test_legacy_database_opens_additively_and_keeps_old_rows(make_client, tmp_path):
    from standardphysics_pipeline.discovery import DiscoveryResult

    from conftest import no_blender_stages

    closing = no_blender_stages(
        label=lambda graph, **kwargs: graph,
        discover=lambda inputs: DiscoveryResult(),
    )
    database_path = tmp_path / "var" / "standardphysics.sqlite3"
    _legacy_database(database_path)
    with make_client(seed=False, stages=closing, evidence_settle_seconds=0.0) as client:
        from standardphysics_api import repository as repo

        with client.app.state.database.connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            assert {"input_hash", "note", "interruptions"} <= columns, columns
            scenario_columns = {row[1] for row in connection.execute("PRAGMA table_info(scenarios)")}
            assert "version" in scenario_columns
            assessment_columns = {row[1] for row in connection.execute("PRAGMA table_info(assessments)")}
            assert "scenario_version" in assessment_columns
            bundles = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_bundles'"
            ).fetchone()
            assert bundles is not None
            legacy = repo.get_scan(connection, __import__("uuid").UUID("aaaaaaaa-1111-2222-3333-444444444444"))
            assert legacy is not None
            assert legacy.name == "legacy scan"

        scan_id = create_scan(client)
        room = json.dumps(
            {
                "version": 1,
                "story": "ground",
                "captureMetadata": {"source": "pilot"},
                "walls": [
                    {
                        "identifier": "11111111-1111-1111-1111-111111111111",
                        "dimensions": [4.0, 2.4, 0.2],
                        "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -0.5, 0, 1],
                        "confidence": "high",
                    }
                ],
            }
        ).encode()
        put_artifact(client, scan_id, "room-json", room, "room_json")
        put_artifact(client, scan_id, "room-usdz", usdz_fixture(), "room_usdz")
        put_artifact(client, scan_id, "frames", b"frame-bytes", "frames")
        put_artifact(client, scan_id, "poses", b"{}", "poses")
        put_artifact(
            client,
            scan_id,
            "lidar-mesh",
            json.dumps(
                {
                    "parts": [
                        {
                            "id": "00000000-0000-0000-0000-000000000001",
                            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                            "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0],
                            "triangles": [0, 1, 2],
                        }
                    ]
                }
            ).encode(),
            "lidar_mesh",
        )
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        drain(client)
        status = client.get(f"/api/scans/{scan_id}/evidence").json()
        assert status["complete_evidence"] is True
        assert status["semantic_state"] == "complete"
        assert client.get(f"/api/scans/{scan_id}/scene").status_code == 200


SCAN_ID = "bbbbbbbb-1111-2222-3333-444444444444"
OWNER_ID = "cccccccc-1111-2222-3333-444444444444"

PRODUCTION_ROWS = {
    "owners": "SELECT id, email, guest, apple_sub, team FROM owners",
    "scans": "SELECT id, name, owner_id, space_typology, owner_wishes_json FROM scans",
    "jobs": "SELECT scan_id, kind, revision, state, input_hash, queued_at FROM jobs",
    "scenarios": "SELECT scan_id, scenario_json, version FROM scenarios",
    "applied_steps": "SELECT name, applied_at FROM applied_steps",
}


def _read(path: pathlib.Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _column_names(path: pathlib.Path, table: str) -> set[str]:
    return {row[1] for row in _read(path, f"PRAGMA table_info({table})")}


def _shape(path: pathlib.Path) -> dict[str, object]:
    """Every table's columns and every index, ignoring the order columns were added in."""
    tables = [
        name
        for (name,) in _read(path, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        if name != "schema_migrations"
    ]
    columns = {table: sorted(row[1:] for row in _read(path, f"PRAGMA table_info({table})")) for table in tables}
    indexes = _read(
        path,
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type = 'index' AND tbl_name != 'schema_migrations' ORDER BY name",
    )
    return {"columns": columns, "indexes": indexes}


def _recorded(path: pathlib.Path) -> list[tuple]:
    return _read(path, "SELECT version, name, detected FROM schema_migrations ORDER BY version")


def _every_version() -> list[int]:
    from standardphysics_api.db import MIGRATIONS

    return list(range(1, MIGRATIONS[-1].version + 1))


PREVIOUS_RELEASE_VERSION = 34
"""The newest migration previous_release_db.py already builds. Later ones run on its databases."""


def _seed_production_rows(path: pathlib.Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        f"""
        INSERT INTO owners (id, email, shop_name, password_hash, created_at, guest, apple_sub, team)
        VALUES ('{OWNER_ID}', 'owner@example.com', 'Corner Books', 'hash', '2026-09-01T00:00:00+00:00',
                0, 'apple-sub', 1);
        INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state, owner_id,
                           space_typology, owner_wishes_json)
        VALUES ('{SCAN_ID}', 'production scan', '2026-09-02T00:00:00+00:00', 'iPhone15,2', 120.0, 'ready',
                '{OWNER_ID}', 'bookstore', '["wider aisles"]');
        INSERT INTO jobs (scan_id, kind, revision, state, created_at, input_hash, queued_at)
        VALUES ('{SCAN_ID}', 'process', 1, 'done', '2026-09-02T00:01:00+00:00', 'abc',
                '2026-09-02T00:01:00+00:00');
        INSERT INTO scenarios (scan_id, scenario_json, version) VALUES ('{SCAN_ID}', '{{}}', 3);
        INSERT INTO applied_steps (name, applied_at) VALUES ('team_role_from_allowlist', '2026-09-03 00:00:00');
        """
    )
    connection.commit()
    connection.close()


def _production_rows(path: pathlib.Path) -> dict[str, list[tuple]]:
    return {table: _read(path, sql) for table, sql in PRODUCTION_ROWS.items()}


def test_a_fresh_database_runs_every_migration_to_the_shape_an_upgraded_one_reaches(tmp_path):
    from previous_release_db import open_as_previous_release

    from standardphysics_api.db import Database

    fresh = tmp_path / "fresh.sqlite3"
    previous = tmp_path / "previous.sqlite3"
    Database(fresh)
    open_as_previous_release(previous)
    Database(previous)

    recorded = _recorded(fresh)
    assert [version for version, _, _ in recorded] == _every_version()
    assert all(detected == 0 for _, _, detected in recorded)
    assert _shape(fresh) == _shape(previous)


def test_the_previous_release_database_detects_what_it_has_and_runs_only_what_came_after(tmp_path):
    from previous_release_db import open_as_previous_release

    from standardphysics_api.db import Database

    path = tmp_path / "standardphysics.sqlite3"
    fresh = tmp_path / "fresh.sqlite3"
    open_as_previous_release(path)

    Database(path)
    Database(fresh)

    recorded = _recorded(path)
    assert [version for version, _, _ in recorded] == _every_version()
    assert all(detected == (version <= PREVIOUS_RELEASE_VERSION) for version, _, detected in recorded)
    assert _shape(path) == _shape(fresh)


def test_the_production_shape_keeps_its_rows_through_the_upgrade(tmp_path):
    from previous_release_db import open_as_previous_release

    from standardphysics_api.db import Database

    path = tmp_path / "standardphysics.sqlite3"
    open_as_previous_release(path)
    _seed_production_rows(path)
    rows_before = _production_rows(path)

    Database(path)

    assert all(rows_before.values())
    assert _production_rows(path) == rows_before


def test_an_older_database_runs_only_the_migrations_it_is_missing(tmp_path):
    from standardphysics_api.db import Database

    path = tmp_path / "standardphysics.sqlite3"
    _legacy_database(path)

    Database(path)

    detected = {name for _, name, was_detected in _recorded(path) if was_detected}
    ran = {name for _, name, was_detected in _recorded(path) if not was_detected}
    assert "create_core_tables" in detected
    assert {"add_scans_owner_id", "add_jobs_input_hash", "create_evidence_bundles", "add_owners_team"} <= ran
    assert [version for version, _, _ in _recorded(path)] == _every_version()
    assert _read(path, "SELECT name FROM scans") == [("legacy scan",)]


def test_opening_twice_changes_nothing_the_second_time(tmp_path):
    from standardphysics_api.db import Database

    path = tmp_path / "standardphysics.sqlite3"
    Database(path)
    recorded = _read(path, "SELECT version, name, applied_at, detected FROM schema_migrations")
    schema_version = _read(path, "PRAGMA schema_version")

    Database(path)

    assert _read(path, "SELECT version, name, applied_at, detected FROM schema_migrations") == recorded
    assert _read(path, "PRAGMA schema_version") == schema_version


def test_a_migration_that_fails_halfway_stays_at_the_last_completed_version_until_rerun(tmp_path):
    import pytest

    from standardphysics_api import db

    path = tmp_path / "standardphysics.sqlite3"
    database = db.Database(path)
    next_version = _every_version()[-1] + 1
    half_done = db.Migration(
        next_version,
        "add_scan_colour",
        ("ALTER TABLE scans ADD COLUMN colour TEXT", "UPDATE no_such_table SET colour = 'red'"),
        db.columns_exist("scans", "colour"),
    )

    with pytest.raises(db.MigrationError, match=rf"{next_version} \(add_scan_colour\)"):
        db.migrate(database, (*db.MIGRATIONS, half_done))

    assert _recorded(path)[-1][0] == next_version - 1
    assert "colour" not in _column_names(path, "scans")

    repaired = db.Migration(
        next_version, "add_scan_colour", ("ALTER TABLE scans ADD COLUMN colour TEXT",), half_done.already_present
    )
    db.migrate(database, (*db.MIGRATIONS, repaired))

    assert _recorded(path)[-1] == (next_version, "add_scan_colour", 0)
    assert "colour" in _column_names(path, "scans")


def test_the_previous_release_still_opens_and_writes_a_database_the_migrations_upgraded(tmp_path):
    from previous_release_db import open_as_previous_release

    from standardphysics_api.db import Database

    path = tmp_path / "standardphysics.sqlite3"
    _legacy_database(path)
    Database(path)
    upgraded_shape = _shape(path)

    open_as_previous_release(path)

    assert _shape(path) == upgraded_shape
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state) "
        "VALUES (?, 'after rollback', '2026-09-04T00:00:00+00:00', 'iPhone15,2', 30.0, 'created')",
        (SCAN_ID,),
    )
    connection.commit()
    connection.close()
    assert ("after rollback",) in _read(path, "SELECT name FROM scans")


def test_every_migration_only_adds_and_the_versions_run_in_order():
    from standardphysics_api.db import MIGRATIONS

    forbidden = ("DROP ", "RENAME ", "DELETE ", "UPDATE ")
    for migration in MIGRATIONS:
        for statement in migration.statements:
            assert not any(word in statement.upper() for word in forbidden), (migration.name, statement)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))
