"""Photo bakes run in a process of their own, so their arithmetic never holds up a request."""

from __future__ import annotations

import os
import uuid

import hanging_child
import pytest

from conftest import create_scan
from standardphysics_api import repository as repo
from standardphysics_api import worker as worker_module
from standardphysics_api.settings import Settings
from standardphysics_api.textures import TEXTURE
from standardphysics_api.worker import in_own_process


def record_process(path: str) -> None:
    with open(path, "w") as handle:
        handle.write(str(os.getpid()))


def fail() -> None:
    raise SystemExit(3)


def test_the_function_runs_in_another_process(tmp_path):
    marker = tmp_path / "pid"
    in_own_process(record_process, str(marker))
    assert int(marker.read_text()) != os.getpid()


def test_a_bake_that_dies_is_reported_as_a_failure():
    with pytest.raises(RuntimeError, match="exited with code 3"):
        in_own_process(fail)


def test_the_server_bakes_out_of_process_unless_told_not_to(monkeypatch):
    monkeypatch.delenv("SP_BAKE_IN_PROCESS", raising=False)
    assert Settings.from_environment().bake_in_own_process
    monkeypatch.setenv("SP_BAKE_IN_PROCESS", "1")
    assert not Settings.from_environment().bake_in_own_process


def test_a_hung_child_is_killed_after_its_timeout(tmp_path):
    pid_file = tmp_path / "pid"
    with pytest.raises(RuntimeError, match="did not finish within 3 seconds"):
        in_own_process(hanging_child.hang_after_writing_pid, str(pid_file), timeout_seconds=3)
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_a_hung_bake_fails_its_job_with_a_clear_error(make_client, monkeypatch):
    monkeypatch.setattr(worker_module, "bake_photos", hanging_child.hang_like_a_bake)
    with make_client(bake_in_own_process=True, bake_timeout_seconds=1.0) as client:
        scan_id = create_scan(client)
        with client.app.state.database.transaction() as connection:
            repo.enqueue_job(connection, uuid.UUID(scan_id), TEXTURE, 1)
        client.app.state.worker.run_once(True)
        with client.app.state.database.connect() as connection:
            job = connection.execute("SELECT state, error FROM jobs WHERE scan_id = ?", (scan_id,)).fetchone()
    assert job["state"] == "failed"
    assert "did not finish within 1 second" in job["error"]


def test_bakes_get_forty_five_minutes_unless_told_otherwise(monkeypatch):
    monkeypatch.delenv("SP_BAKE_TIMEOUT_SECONDS", raising=False)
    assert Settings.from_environment().bake_timeout_seconds == 45 * 60
    monkeypatch.setenv("SP_BAKE_TIMEOUT_SECONDS", "600")
    assert Settings.from_environment().bake_timeout_seconds == 600
