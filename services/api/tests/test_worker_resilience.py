"""The worker keeps running jobs through a locked database, reports its pulse, and refuses to share the queue."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from conftest import create_scan
from standardphysics_api import repository as repo
from standardphysics_api import worker as worker_module
from standardphysics_api.textures import TEXTURE
from standardphysics_api.worker import PROCESS, Worker


def _wait_for(condition, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _queue(client, scan_id: str, kind: str, revision: int = 1) -> None:
    with client.app.state.database.transaction() as connection:
        repo.enqueue_job(connection, uuid.UUID(scan_id), kind, revision)


def _job(client, scan_id: str, kind: str):
    with client.app.state.database.connect() as connection:
        return connection.execute(
            "SELECT state, error FROM jobs WHERE scan_id = ? AND kind = ?", (scan_id, kind)
        ).fetchone()


def _fast_retries(monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "FIRST_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "LONGEST_RETRY_SECONDS", 0.05)


def test_the_loop_survives_a_locked_database_and_keeps_claiming(make_client, monkeypatch, caplog):
    _fast_retries(monkeypatch)
    real_claim = repo.claim_job
    calls = {"claim": 0}

    def locked_twice(connection, texture_only=None):
        calls["claim"] += 1
        if calls["claim"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real_claim(connection, texture_only)

    monkeypatch.setattr(worker_module.repo, "claim_job", locked_twice)
    with make_client() as client:
        worker = client.app.state.worker
        with caplog.at_level(logging.ERROR, logger=worker_module.__name__):
            worker.start()
            try:
                assert _wait_for(lambda: calls["claim"] >= 4)
                assert all(pulse.thread.is_alive() for pulse in worker.pulses.values())
            finally:
                worker.stop()
    assert "database is locked" in caplog.text


def test_a_finished_job_is_recorded_even_when_the_first_write_is_locked(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    real_finish = repo.finish_job
    calls = {"finish": 0}

    def locked_once(connection, job_id, error=None):
        calls["finish"] += 1
        if calls["finish"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_finish(connection, job_id, error)

    monkeypatch.setattr(worker_module.repo, "finish_job", locked_once)
    monkeypatch.setattr(Worker, "_simulate", lambda self, scan_id, revision, job=None: False)
    with make_client() as client:
        scan_id = create_scan(client)
        _queue(client, scan_id, "simulate")
        client.app.state.worker.run_once()
        assert _job(client, scan_id, "simulate")["state"] == "done"


def test_details_report_an_idle_worker_and_the_oldest_queued_job(make_client):
    with make_client() as client:
        worker = client.app.state.worker
        worker.start()
        try:
            jobs_loop = lambda: client.get("/health/details").json()["worker"]["loops"]["jobs"]  # noqa: E731
            assert _wait_for(lambda: jobs_loop()["state"] == "idle")
            assert jobs_loop()["heartbeat_seconds"] < 5
            assert client.get("/health/details").json()["worker"]["lock"] == "held"
        finally:
            worker.stop()
        assert client.get("/health/details").json()["oldest_queued_job_seconds"] is None
        scan_id = create_scan(client)
        _queue(client, scan_id, PROCESS)
        an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        with client.app.state.database.transaction() as connection:
            connection.execute("UPDATE jobs SET queued_at = ?", (an_hour_ago,))
        assert client.get("/health/details").json()["oldest_queued_job_seconds"] >= 3600


def test_a_long_job_looks_busy_not_dead(make_client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(worker_module, "STALLED_AFTER_SECONDS", 0.1)
    monkeypatch.setattr(worker_module, "run_texture", lambda *args: release.wait(timeout=20))
    with make_client() as client:
        scan_id = create_scan(client)
        _queue(client, scan_id, TEXTURE)
        worker = client.app.state.worker
        worker.start()
        try:
            textures = lambda: client.get("/health/details").json()["worker"]["loops"]["textures"]  # noqa: E731
            assert _wait_for(lambda: textures()["state"] == "busy")
            time.sleep(0.3)
            assert textures()["state"] == "busy"
            assert textures()["job"]["kind"] == TEXTURE
            assert client.get("/health").status_code == 200
        finally:
            release.set()
            worker.stop()


def test_health_fails_when_a_worker_loop_has_died(make_client, monkeypatch):
    def die(self, texture_only=None):
        raise SystemExit("the loop is gone")

    monkeypatch.setattr(Worker, "run_once", die)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    with make_client() as client:
        worker = client.app.state.worker
        worker.start()
        try:
            assert _wait_for(lambda: not worker.pulses[False].thread.is_alive())
            response = client.get("/health")
        finally:
            worker.stop()
    assert response.status_code == 503
    assert response.json()["worker"] == "stopped"


def test_a_second_worker_on_the_same_database_refuses_to_run_jobs(make_client, caplog):
    with make_client() as client:
        first = client.app.state.worker
        first.start()
        try:
            scan_id = create_scan(client)
            _queue(client, scan_id, PROCESS)
            with client.app.state.database.transaction() as connection:
                connection.execute("UPDATE jobs SET state = 'running' WHERE scan_id = ?", (scan_id,))
            second = Worker(first.database, first.store, first.stages, first.settings)
            with caplog.at_level(logging.ERROR, logger=worker_module.__name__):
                second.start()
            assert "another worker" in caplog.text
            assert second.status()["lock"] == "standby"
            assert all(pulse.thread is None for pulse in second.pulses.values())
            assert _job(client, scan_id, PROCESS)["state"] == "running"
            assert client.get("/health").status_code == 200
        finally:
            first.stop()
        after_the_first_stopped = Worker(first.database, first.store, first.stages, first.settings)
        after_the_first_stopped.start()
        try:
            assert after_the_first_stopped.status()["lock"] == "held"
        finally:
            after_the_first_stopped.stop()
