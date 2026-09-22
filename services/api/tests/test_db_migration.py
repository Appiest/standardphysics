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
    from conftest import no_blender_stages
    from standardphysics_pipeline.discovery import DiscoveryResult

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
            assert {"input_hash", "note"} <= columns, columns
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
