"""The worker keeps running jobs through a locked database, reports its pulse, and refuses to share the queue."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
import urllib.error
import uuid
from datetime import UTC, datetime, timedelta

import pytest

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


def _quick_stall_detection(monkeypatch) -> None:
    """A loop is called stalled after 0.25 s without a beat, and an idle one beats every 0.02 s."""
    monkeypatch.setattr(worker_module, "STALLED_AFTER_SECONDS", 0.25)
    monkeypatch.setattr(worker_module, "IDLE_WAIT_SECONDS", 0.02)


def test_the_loop_survives_a_locked_database_and_keeps_claiming(make_client, monkeypatch, caplog):
    _fast_retries(monkeypatch)
    real_claim = repo.claim_job
    calls = {"claim": 0}

    def locked_twice(connection, texture_only=None, *, kind=None):
        calls["claim"] += 1
        if calls["claim"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real_claim(connection, texture_only, kind=kind)

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
    _quick_stall_detection(monkeypatch)
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
            assert client.get("/health/ready").status_code == 200
        finally:
            release.set()
            worker.stop()


def test_a_job_past_its_deadline_degrades_readiness_but_not_liveness(make_client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(worker_module, "run_texture", lambda *args: release.wait(timeout=20))
    with make_client(bake_timeout_seconds=0.2) as client:
        scan_id = create_scan(client)
        _queue(client, scan_id, TEXTURE)
        worker = client.app.state.worker
        worker.start()
        try:
            details = lambda: client.get("/health/details").json()  # noqa: E731
            assert _wait_for(lambda: details()["worker"]["loops"]["textures"]["state"] == "overdue")
            assert details()["status"] == "degraded"
            assert details()["problems"] == ["the textures loop is overdue"]
            ready = client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["problems"] == ["the textures loop is overdue"]
            live = client.get("/health")
            assert live.status_code == 200
            assert live.json()["worker"] == "overdue"
        finally:
            release.set()
            worker.stop()


def test_a_loop_stuck_outside_any_job_is_reported_stalled(make_client, monkeypatch):
    stuck = threading.Event()
    release = threading.Event()

    def hang(self):
        stuck.set()
        release.wait(timeout=20)

    _quick_stall_detection(monkeypatch)
    monkeypatch.setattr(Worker, "_sweep_due_settled", hang)
    with make_client() as client:
        worker = client.app.state.worker
        worker.start()
        try:
            assert stuck.wait(timeout=10)
            assert _wait_for(lambda: worker.summary() == "stalled")
            assert client.get("/health").status_code == 200
            ready = client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json() == {"status": "degraded", "problems": ["the jobs loop is stalled"]}
        finally:
            release.set()
            worker.stop()


def test_an_idle_worker_is_ready(make_client):
    with make_client() as client:
        worker = client.app.state.worker
        worker.start()
        try:
            assert _wait_for(lambda: client.get("/health/details").json()["worker"]["loops"]["jobs"]["state"] == "idle")
            ready = client.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ready", "problems": []}
            assert client.get("/health/details").json()["status"] == "ok"
        finally:
            worker.stop()


def test_health_fails_when_a_worker_loop_has_died(make_client, monkeypatch):
    def die(self, texture_only=None, kind=None):
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


@contextlib.contextmanager
def _one_queued_job(make_client, kind: str = "simulate"):
    """A client with one queued job, so a test can break the worker only after the API has set it up."""
    with make_client() as client:
        scan_id = create_scan(client)
        _queue(client, scan_id, kind)
        yield client, scan_id


def _job_row(client, scan_id: str, kind: str = "simulate"):
    with client.app.state.database.connect() as connection:
        return connection.execute(
            "SELECT state, error, attempts FROM jobs WHERE scan_id = ? AND kind = ?", (scan_id, kind)
        ).fetchone()


def _fails_on_calls(real, failing_calls: set[int], error: BaseException):
    calls = {"count": 0}

    def replacement(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] in failing_calls:
            raise error
        return real(*args, **kwargs)

    return replacement


def _succeeds(self, scan_id, revision, job=None) -> bool:
    return False


def _interrupted(self, scan_id, revision, job=None) -> bool:
    raise KeyboardInterrupt


def test_a_job_whose_start_check_fails_goes_back_to_the_queue(make_client, monkeypatch):
    monkeypatch.setattr(Worker, "_simulate", _succeeds)
    with _one_queued_job(make_client) as (client, scan_id):
        broken = _fails_on_calls(repo.marked_for_deletion, {1}, RuntimeError("disk"))
        monkeypatch.setattr(worker_module.repo, "marked_for_deletion", broken)
        with pytest.raises(RuntimeError):
            client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "queued"
        client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "done"


def test_a_job_that_keeps_failing_its_start_check_is_failed_after_its_last_claim(make_client, monkeypatch):
    def always_broken(connection, scan_id):
        raise RuntimeError("the deletion check is broken")

    with _one_queued_job(make_client) as (client, scan_id):
        monkeypatch.setattr(worker_module.repo, "marked_for_deletion", always_broken)
        for _ in range(worker_module.MAX_CLAIMS_BEFORE_START):
            with pytest.raises(RuntimeError):
                client.app.state.worker.run_once()
        job = _job_row(client, scan_id)
    assert job["state"] == "failed"
    assert job["attempts"] == worker_module.MAX_CLAIMS_BEFORE_START
    assert "the deletion check is broken" in job["error"]


def test_a_failure_while_marking_the_scan_failed_still_fails_the_job(make_client, monkeypatch):
    def broken_stage(self, scan_id, revision, job=None):
        raise ValueError("the stage broke")

    monkeypatch.setattr(Worker, "_assess", broken_stage)
    with _one_queued_job(make_client, "assess") as (client, scan_id):
        monkeypatch.setattr(worker_module.repo, "set_state", _fails_on_calls(repo.set_state, {1}, RuntimeError("io")))
        with pytest.raises(RuntimeError):
            client.app.state.worker.run_once()
        job = _job_row(client, scan_id, "assess")
    assert job["state"] == "failed"
    assert "io" in job["error"]


def test_an_unexpected_error_while_recording_the_outcome_fails_the_job(make_client, monkeypatch):
    monkeypatch.setattr(Worker, "_simulate", _succeeds)
    with _one_queued_job(make_client) as (client, scan_id):
        broken = _fails_on_calls(repo.record_job_attempt, {1}, KeyError("attempt"))
        monkeypatch.setattr(worker_module.repo, "record_job_attempt", broken)
        with pytest.raises(KeyError):
            client.app.state.worker.run_once()
        job = _job_row(client, scan_id)
    assert job["state"] == "failed"
    assert "attempt" in job["error"]


def test_a_job_interrupted_mid_run_is_not_left_running(make_client, monkeypatch):
    monkeypatch.setattr(Worker, "_simulate", _interrupted)
    with _one_queued_job(make_client) as (client, scan_id):
        with pytest.raises(KeyboardInterrupt):
            client.app.state.worker.run_once()
        job = _job_row(client, scan_id)
    assert job["state"] == "failed"
    assert "KeyboardInterrupt" in job["error"]


def test_a_failure_after_the_outcome_is_written_leaves_the_job_settled(make_client, monkeypatch):
    monkeypatch.setattr(Worker, "_simulate", _succeeds)
    with _one_queued_job(make_client) as (client, scan_id):
        broken = _fails_on_calls(repo.marked_for_deletion, {2}, RuntimeError("disk"))
        monkeypatch.setattr(worker_module.repo, "marked_for_deletion", broken)
        with pytest.raises(RuntimeError):
            client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "done"


def test_a_failed_follow_up_leaves_the_job_settled(make_client, monkeypatch):
    def broken_follow_up(self, scan_id):
        raise RuntimeError("follow-up broke")

    monkeypatch.setattr(Worker, "_simulate", lambda self, scan_id, revision, job=None: True)
    monkeypatch.setattr(Worker, "_queue_follow_up_if_due", broken_follow_up)
    with _one_queued_job(make_client) as (client, scan_id):
        with pytest.raises(RuntimeError):
            client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "done"


def test_settling_an_interrupted_job_waits_out_a_locked_database(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    monkeypatch.setattr(Worker, "_simulate", _interrupted)
    with _one_queued_job(make_client) as (client, scan_id):
        locked = sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(worker_module.repo, "fail_running_job", _fails_on_calls(repo.fail_running_job, {1, 2}, locked))
        with pytest.raises(KeyboardInterrupt):
            client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "failed"


def _counting_stage(errors: list[BaseException | None]):
    """A stage that raises each error in turn, then succeeds; None in the list is a success."""
    calls = {"count": 0}

    def stage(self, scan_id, revision, job=None) -> bool:
        calls["count"] += 1
        error = errors[calls["count"] - 1] if calls["count"] <= len(errors) else None
        if error is not None:
            raise error
        return False

    return stage, calls


def test_a_job_that_meets_a_locked_database_is_tried_again(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    stage, calls = _counting_stage([sqlite3.OperationalError("database is locked")])
    monkeypatch.setattr(Worker, "_simulate", stage)
    with _one_queued_job(make_client) as (client, scan_id):
        client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "done"
    assert calls["count"] == 2


def test_a_provider_timeout_is_tried_again(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    stage, calls = _counting_stage([urllib.error.URLError(TimeoutError("timed out"))])
    monkeypatch.setattr(Worker, "_simulate", stage)
    with _one_queued_job(make_client) as (client, scan_id):
        client.app.state.worker.run_once()
        assert _job_row(client, scan_id)["state"] == "done"
    assert calls["count"] == 2


def test_transient_retries_are_bounded(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    stage, calls = _counting_stage([TimeoutError("read timed out")] * 10)
    monkeypatch.setattr(Worker, "_assess", stage)
    with _one_queued_job(make_client, "assess") as (client, scan_id):
        client.app.state.worker.run_once()
        job = _job_row(client, scan_id, "assess")
    assert job["state"] == "failed"
    assert "read timed out" in job["error"]
    assert calls["count"] == worker_module.TRANSIENT_ATTEMPTS


def test_an_ordinary_failure_is_not_tried_again(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    stage, calls = _counting_stage([ValueError("bad input"), sqlite3.OperationalError("no such table: x")])
    monkeypatch.setattr(Worker, "_assess", stage)
    with _one_queued_job(make_client, "assess") as (client, scan_id):
        client.app.state.worker.run_once()
        assert _job_row(client, scan_id, "assess")["state"] == "failed"
    assert calls["count"] == 1
