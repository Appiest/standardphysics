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
from standardphysics_api import worker_handlers, worker_pulse
from standardphysics_api.textures import TEXTURE
from standardphysics_api.worker import Worker
from standardphysics_api.worker_handlers import PROCESS


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
    monkeypatch.setattr(worker_pulse, "FIRST_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(worker_pulse, "LONGEST_RETRY_SECONDS", 0.05)


def _quick_stall_detection(monkeypatch) -> None:
    """A loop is called stalled after 0.25 s without a beat, and an idle one beats every 0.02 s."""
    monkeypatch.setattr(worker_pulse, "STALLED_AFTER_SECONDS", 0.25)
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


def test_a_running_simulation_does_not_hold_up_a_new_scan(make_client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(Worker, "_simulate", lambda self, scan_id, revision, job=None: release.wait(timeout=20))
    monkeypatch.setattr(Worker, "_process", lambda self, scan_id, revision, job=None: False)
    with make_client() as client:
        simulated, measured = create_scan(client), create_scan(client)
        _queue(client, simulated, "simulate")
        _queue(client, measured, PROCESS)
        worker = client.app.state.worker
        worker.start()
        try:
            simulate = lambda: client.get("/health/details").json()["worker"]["loops"]["simulate"]  # noqa: E731
            assert _wait_for(lambda: simulate()["state"] == "busy")
            assert _wait_for(lambda: _job(client, measured, PROCESS)["state"] == "done")
            assert simulate()["job"]["kind"] == "simulate"
        finally:
            release.set()
            worker.stop()


def test_a_long_job_looks_busy_not_dead(make_client, monkeypatch):
    release = threading.Event()
    _quick_stall_detection(monkeypatch)
    monkeypatch.setattr(worker_handlers, "run_texture", lambda *args: release.wait(timeout=20))
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
    monkeypatch.setattr(worker_handlers, "run_texture", lambda *args: release.wait(timeout=20))
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


def test_a_standby_worker_takes_the_queue_once_the_holder_exits(make_client, monkeypatch):
    monkeypatch.setattr(worker_module, "STANDBY_RETRY_SECONDS", 0.05)
    with make_client() as client:
        first = client.app.state.worker
        first.start()
        second = Worker(first.database, first.store, first.stages, first.settings)
        try:
            second.start()
            assert second.status()["lock"] == "standby"
            first.stop()
            assert _wait_for(lambda: second.status()["lock"] == "held", seconds=5)
            assert _wait_for(lambda: all(pulse.thread is not None for pulse in second.pulses.values()), seconds=5)
            scan_id = create_scan(client)
            _queue(client, scan_id, PROCESS)
            second.wake()
            assert _wait_for(lambda: _job(client, scan_id, PROCESS)["state"] != "queued", seconds=10)
        finally:
            second.stop()


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


def _always_raises(error: BaseException):
    calls = {"count": 0}

    def replacement(*args, **kwargs):
        calls["count"] += 1
        raise error

    return replacement, calls


def _run_once_off_thread(worker, seconds: float = 5.0) -> tuple[bool, BaseException | None]:
    """Run one job on a daemon thread: whether it came back within `seconds`, and what it raised.

    A worker that never comes back is stopped, which makes its settlement give up.
    """
    raised: list[BaseException] = []

    def run() -> None:
        try:
            worker.run_once()
        except BaseException as error:
            raised.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    returned = not thread.is_alive()
    if not returned:
        worker.stop()
        thread.join(5)
    return returned, raised[0] if raised else None


def _break_settlement(monkeypatch, error: BaseException) -> tuple[dict, dict]:
    finish, finish_calls = _always_raises(error)
    fail, fail_calls = _always_raises(error)
    monkeypatch.setattr(worker_module.repo, "finish_job", finish)
    monkeypatch.setattr(worker_module.repo, "fail_running_job", fail)
    return finish_calls, fail_calls


def _assert_left_for_restart_and_degraded(client, scan_id: str) -> None:
    assert _job_row(client, scan_id)["state"] == "running"
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    [problem] = ready.json()["problems"]
    assert "could not be recorded" in problem
    assert "restart" in problem
    details = client.get("/health/details").json()
    assert details["status"] == "degraded"
    assert details["problems"] == [problem]


def test_a_permanent_database_error_while_settling_is_not_retried(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    monkeypatch.setattr(Worker, "_simulate", _succeeds)
    with _one_queued_job(make_client) as (client, scan_id):
        finish_calls, fail_calls = _break_settlement(monkeypatch, sqlite3.OperationalError("no such table: jobs"))
        returned, raised = _run_once_off_thread(client.app.state.worker)
        assert returned, "settlement kept retrying a permanent error"
        assert isinstance(raised, sqlite3.OperationalError)
        assert (finish_calls["count"], fail_calls["count"]) == (1, 1)
        _assert_left_for_restart_and_degraded(client, scan_id)


def test_a_database_that_stays_locked_while_settling_is_given_up_on(make_client, monkeypatch):
    _fast_retries(monkeypatch)
    monkeypatch.setattr(worker_module, "SETTLE_PATIENCE_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(Worker, "_simulate", _succeeds)
    with _one_queued_job(make_client) as (client, scan_id):
        finish_calls, fail_calls = _break_settlement(monkeypatch, sqlite3.OperationalError("database is locked"))
        returned, raised = _run_once_off_thread(client.app.state.worker)
        assert returned, "settlement waited on the lock for ever"
        assert isinstance(raised, sqlite3.OperationalError)
        assert finish_calls["count"] > 1
        assert fail_calls["count"] > 1
        _assert_left_for_restart_and_degraded(client, scan_id)


def _restart_during_run(client, scan_id: str, kind: str = PROCESS) -> None:
    """The job is claimed and running when the server stops; the next start recovers it."""
    with client.app.state.database.transaction() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'running', attempts = attempts + 1 WHERE scan_id = ? AND kind = ?",
            (scan_id, kind),
        )
    client.app.state.worker._recover_interrupted_jobs()


def _interruptions(client, scan_id: str, kind: str = PROCESS) -> int:
    with client.app.state.database.connect() as connection:
        return connection.execute(
            "SELECT interruptions FROM jobs WHERE scan_id = ? AND kind = ?", (scan_id, kind)
        ).fetchone()["interruptions"]


def test_an_interrupted_job_is_queued_again_and_its_interruption_is_counted(make_client):
    with _one_queued_job(make_client, PROCESS) as (client, scan_id):
        _restart_during_run(client, scan_id)
        assert _job(client, scan_id, PROCESS)["state"] == "queued"
        assert _interruptions(client, scan_id) == 1


def test_a_job_interrupted_on_every_run_is_stopped_at_the_ceiling(make_client, caplog):
    with _one_queued_job(make_client, PROCESS) as (client, scan_id):
        _restart_during_run(client, scan_id)
        _restart_during_run(client, scan_id)
        assert _job(client, scan_id, PROCESS)["state"] == "queued"
        with caplog.at_level(logging.ERROR, logger=worker_module.__name__):
            _restart_during_run(client, scan_id)
        job = _job(client, scan_id, PROCESS)
        assert job["state"] == "failed"
        assert job["error"].startswith("Stopped after 3 interrupted runs")
        assert client.get(f"/api/scans/{scan_id}").json()["state"] == "failed"
        assert "stopped after 3 interrupted runs" in caplog.text


def test_the_stopped_run_is_kept_in_the_attempt_history(make_client):
    with _one_queued_job(make_client, PROCESS) as (client, scan_id):
        for _ in range(3):
            _restart_during_run(client, scan_id)
        with client.app.state.database.connect() as connection:
            attempts = connection.execute("SELECT attempt, state, error FROM job_attempts").fetchall()
        assert [(row["attempt"], row["state"]) for row in attempts] == [(3, "failed")]
        assert attempts[0]["error"].startswith("Stopped after 3 interrupted runs")


def test_the_ceiling_comes_from_the_settings(make_client):
    with _one_queued_job(lambda: make_client(max_job_interruptions=1), PROCESS) as (client, scan_id):
        _restart_during_run(client, scan_id)
        assert _job(client, scan_id, PROCESS)["error"].startswith("Stopped after 1 interrupted runs")


def test_the_ceiling_is_read_from_the_environment(monkeypatch):
    from standardphysics_api.settings import Settings

    monkeypatch.setenv("SP_MAX_JOB_INTERRUPTIONS", "5")
    assert Settings.from_environment().max_job_interruptions == 5
    monkeypatch.setenv("SP_MAX_JOB_INTERRUPTIONS", "0")
    with pytest.raises(ValueError):
        Settings.from_environment()


def test_a_stopped_derived_job_leaves_the_scan_state_alone(make_client):
    with _one_queued_job(make_client, "display") as (client, scan_id):
        before = client.get(f"/api/scans/{scan_id}").json()["state"]
        for _ in range(3):
            _restart_during_run(client, scan_id, "display")
        assert _job(client, scan_id, "display")["state"] == "failed"
        assert client.get(f"/api/scans/{scan_id}").json()["state"] == before


def test_retrying_a_stopped_scan_queues_the_job_with_a_fresh_count(make_client):
    with _one_queued_job(make_client, PROCESS) as (client, scan_id):
        for _ in range(3):
            _restart_during_run(client, scan_id)
        assert client.post(f"/api/scans/{scan_id}/complete").status_code == 200
        assert _job(client, scan_id, PROCESS)["state"] == "queued"
        assert _interruptions(client, scan_id) == 0
        _restart_during_run(client, scan_id)
        assert _job(client, scan_id, PROCESS)["state"] == "queued"


def test_asking_for_a_derived_job_again_clears_its_count(make_client):
    with _one_queued_job(make_client, "display") as (client, scan_id):
        for _ in range(3):
            _restart_during_run(client, scan_id, "display")
        with client.app.state.database.transaction() as connection:
            repo.queue_job_again(connection, uuid.UUID(scan_id), "display", 1)
        assert _job(client, scan_id, "display")["state"] == "queued"
        assert _interruptions(client, scan_id, "display") == 0
