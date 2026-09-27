"""deploy/digitalocean/backup.sh and restore.sh, end to end against a volume in a temporary directory.

The volume holds a database made with the API's own schema and a few artifact
files laid out the way `ArtifactStore` lays them out. Each test backs it up,
restores a snapshot into a fresh directory, and compares the copy with the
original.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest

from standardphysics_api.db import Database

REPO = pathlib.Path(__file__).resolve().parents[2]
BACKUP = REPO / "deploy/digitalocean/backup.sh"
RESTORE = REPO / "deploy/digitalocean/restore.sh"
DATABASE_NAME = "standardphysics.sqlite3"

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="the backup scripts need rsync")


class Volume:
    """A scan volume: the database at its root and each scan's artifacts under scans/."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.database = Database(root / DATABASE_NAME)

    def add_scan(self, artifacts: dict[str, bytes]) -> str:
        scan_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO scans (id, name, created_at, device_model, duration_seconds, state)"
                " VALUES (?, 'Corner cafe', '2026-09-27T10:00:00Z', 'iPhone17,1', 142.5, 'uploaded')",
                (scan_id,),
            )
            for artifact_id, body in artifacts.items():
                self.write_artifact(connection, scan_id, artifact_id, body)
        return scan_id

    def write_artifact(self, connection: sqlite3.Connection, scan_id: str, artifact_id: str, body: bytes) -> None:
        path = self.root / "scans" / scan_id / "artifacts" / artifact_id
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        connection.execute(
            "INSERT INTO artifacts (scan_id, id, kind, sha256, bytes, created_at)"
            " VALUES (?, ?, 'photo', ?, ?, '2026-09-27T10:00:00Z')",
            (scan_id, artifact_id, hashlib.sha256(body).hexdigest(), len(body)),
        )


class Backups:
    def __init__(self, tmp_path: pathlib.Path, volume: Volume, keep: int = 14):
        self.tmp_path = tmp_path
        self.destination = tmp_path / "backups"
        self.environment = {
            **os.environ,
            "SCANS_PATH": str(volume.root),
            "SP_BACKUP_DEST": str(self.destination),
            "SP_BACKUP_KEEP": str(keep),
            "SP_BACKUP_SNAPSHOT_WITH": "host",
            "SP_BACKUP_PYTHON": sys.executable,
        }

    def run(self, script: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(script), *arguments], env=self.environment, capture_output=True, text=True, timeout=120
        )

    def back_up(self) -> str:
        """One snapshot, named after the second it began, so two in one second would collide."""
        before = set(self.snapshots())
        result = self.run(BACKUP)
        assert result.returncode == 0, result.stderr
        (made,) = set(self.snapshots()) - before
        time.sleep(1.1)
        return made

    def restore(self, snapshot: str, into: str = "restored") -> tuple[subprocess.CompletedProcess, pathlib.Path]:
        target = self.tmp_path / into
        return self.run(RESTORE, snapshot, str(target)), target

    def snapshots(self) -> list[str]:
        if not self.destination.exists():
            return []
        return sorted(path.name for path in self.destination.iterdir() if not path.name.endswith(".partial"))


def rows(database: pathlib.Path, table: str) -> list[tuple]:
    with sqlite3.connect(database) as connection:
        return sorted(connection.execute(f"SELECT * FROM {table}").fetchall())


def files_under(root: pathlib.Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture
def volume(tmp_path: pathlib.Path) -> Volume:
    shop = Volume(tmp_path / "volume")
    shop.add_scan({"photo-0001.jpg": b"\xff\xd8 first photo", "room.usdz": b"PK usdz bytes"})
    shop.add_scan({"photo-0001.jpg": b"\xff\xd8 another shop"})
    return shop


def test_a_restored_snapshot_matches_the_volume_it_was_taken_from(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    snapshot = backups.back_up()
    result, restored = backups.restore("latest")

    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot in result.stdout
    assert "integrity check: ok" in result.stdout
    assert "scans:           2" in result.stdout
    assert "artifacts:       3 listed, 3 files" in result.stdout
    for table in ("scans", "artifacts"):
        assert rows(restored / DATABASE_NAME, table) == rows(volume.root / DATABASE_NAME, table)
    assert files_under(restored / "scans") == files_under(volume.root / "scans")


def test_the_snapshot_holds_writes_still_in_the_wal_and_leaves_out_uncommitted_ones(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    with volume.database.connect() as committed, volume.database.connect() as uncommitted:
        committed.execute("PRAGMA wal_autocheckpoint=0")
        committed.execute("UPDATE scans SET name = 'Renamed while the WAL held it'")
        uncommitted.execute("BEGIN IMMEDIATE")
        uncommitted.execute("DELETE FROM artifacts")
        backups.back_up()
        uncommitted.execute("ROLLBACK")

    result, restored = backups.restore("latest")
    assert result.returncode == 0, result.stdout + result.stderr
    names = {row[1] for row in rows(restored / DATABASE_NAME, "scans")}
    assert names == {"Renamed while the WAL held it"}
    assert len(rows(restored / DATABASE_NAME, "artifacts")) == 3
    assert not list(restored.glob(f"{DATABASE_NAME}-*"))


def test_an_unchanged_artifact_is_shared_with_the_snapshot_before_it(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    first = backups.back_up()
    new_scan = volume.add_scan({"photo-0002.jpg": b"\xff\xd8 a later photo"})
    second = backups.back_up()

    unchanged = next((volume.root / "scans").rglob("room.usdz")).relative_to(volume.root)
    assert (backups.destination / first / unchanged).stat().st_ino == (backups.destination / second / unchanged).stat().st_ino

    result, restored = backups.restore(second)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (restored / "scans" / new_scan / "artifacts" / "photo-0002.jpg").read_bytes() == b"\xff\xd8 a later photo"
    result, older = backups.restore(first, into="older")
    assert not (older / "scans" / new_scan).exists()


def test_only_the_newest_snapshots_are_kept(tmp_path, volume):
    backups = Backups(tmp_path, volume, keep=2)
    made = [backups.back_up() for _ in range(3)]
    assert backups.snapshots() == made[1:]


def test_a_restore_reports_an_artifact_the_database_lists_without_its_file(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    snapshot = backups.back_up()
    lost = next((backups.destination / snapshot / "scans").rglob("room.usdz"))
    lost.unlink()

    result, _ = backups.restore(snapshot)
    assert result.returncode == 2
    assert "artifacts:       3 listed, 2 files" in result.stdout
    assert "missing file:" in result.stdout and "room.usdz" in result.stdout


def test_a_restore_never_writes_over_a_directory_with_files_in_it(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    backups.back_up()
    occupied = tmp_path / "restored"
    occupied.mkdir()
    (occupied / "keep-me").write_text("someone's work")

    result, _ = backups.restore("latest")
    assert result.returncode == 1
    assert "already holds files" in result.stderr
    assert sorted(path.name for path in occupied.iterdir()) == ["keep-me"]


def test_a_backup_with_nowhere_to_go_fails_loudly(tmp_path, volume):
    backups = Backups(tmp_path, volume)
    backups.environment["SP_BACKUP_DEST"] = ""
    result = backups.run(BACKUP)
    assert result.returncode == 1
    assert "SP_BACKUP_DEST is not set" in result.stderr
