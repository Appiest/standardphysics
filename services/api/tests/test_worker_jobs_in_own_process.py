"""Process, assess, display and simulate jobs run in a process of their own, killed at their kind's deadline."""

from __future__ import annotations

import os
import pathlib

import child_stages
import hanging_child
import pytest
from test_job_lifecycle import _complete_geometry, _complete_semantics

from conftest import create_scan, drain
from standardphysics_api.settings import Settings
from standardphysics_api.worker import ChildFailed, in_own_process


def _seeded_shop(client) -> str:
    return client.get("/api/scans").json()["scans"][0]["id"]


def _job(client, scan_id: str, kind: str):
    with client.app.state.database.connect() as connection:
        return connection.execute(
            "SELECT state, error FROM jobs WHERE scan_id = ? AND kind = ? ORDER BY id DESC LIMIT 1", (scan_id, kind)
        ).fetchone()


def _findings(client, scan_id: str) -> list[dict]:
    return client.get(f"/api/scans/{scan_id}/assessment").json()["findings"]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def jobs_client(make_client):
    def build(stages_in_child=child_stages.without_blender, **settings):
        client = make_client(jobs_in_own_process=True, **settings)
        client.app.state.worker.stages_in_child = stages_in_child
        return client

    return build


def _queue_process_job(client) -> str:
    scan_id = create_scan(client)
    _complete_geometry(client, scan_id)
    _complete_semantics(client, scan_id)
    client.post(f"/api/scans/{scan_id}/complete")
    return scan_id


def test_a_stage_that_hangs_is_killed_at_its_deadline_and_the_next_job_still_runs(jobs_client, tmp_path):
    hung = jobs_client(
        child_stages.hanging_at_labeling, seed=True, evidence_settle_seconds=0.0, process_timeout_seconds=5.0
    )
    with hung as client:
        sample = _seeded_shop(client)
        stuck = _queue_process_job(client)
        drain(client)
        process, display = _job(client, stuck, "process"), _job(client, sample, "display")
        findings = _findings(client, sample)
    pid_file = tmp_path / "var" / child_stages.HUNG_STAGE_PID
    assert process["state"] == "failed"
    assert process["error"] == "The process job did not finish within 5 seconds and was stopped"
    assert _gone(int(pid_file.read_text()))
    assert display["state"] == "done", display["error"]
    assert any(finding["locus"] and finding["locus"]["render_url"] for finding in findings)


def test_a_process_job_in_its_own_process_saves_its_revision_and_checks_it(jobs_client):
    with jobs_client(evidence_settle_seconds=0.0) as client:
        scan_id = _queue_process_job(client)
        drain(client)
        process, assess = _job(client, scan_id, "process"), _job(client, scan_id, "assess")
        scan = client.get(f"/api/scans/{scan_id}").json()
        scene = client.get(f"/api/scans/{scan_id}/scene")
    assert process["state"] == "done", process["error"]
    assert assess is None or assess["state"] == "done"
    assert scan["state"] == "ready"
    assert scene.status_code == 200


def test_an_assess_job_in_its_own_process_saves_its_assessment(jobs_client):
    with jobs_client(seed=True) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        assess = _job(client, scan_id, "assess")
        findings = _findings(client, scan_id)
    assert assess["state"] == "done", assess["error"]
    assert findings


def test_a_display_job_in_its_own_process_draws_its_stills(jobs_client):
    with jobs_client(seed=True) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        display = _job(client, scan_id, "display")
        findings = _findings(client, scan_id)
    assert display["state"] == "done", display["error"]
    assert any(finding["locus"] and finding["locus"]["render_url"] for finding in findings)


def test_a_simulation_in_its_own_process_saves_its_result(jobs_client):
    with jobs_client(seed=True, team=True) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        response = client.post(f"/api/scans/{scan_id}/simulations", json={"base_revision": 0, "samples": 2})
        assert response.status_code == 202, response.text
        drain(client)
        state = client.get(f"/api/scans/{scan_id}/simulations?revision=0").json()
    assert state["state"] == "done", state["error"]
    assert state["result"]["total_runs"] == 2


def test_a_locked_database_in_the_child_runs_the_job_again(jobs_client):
    with jobs_client(child_stages.locked_on_the_first_try, seed=True) as client:
        scan_id = _seeded_shop(client)
        drain(client)
        assess = _job(client, scan_id, "assess")
    assert assess["state"] == "done", assess["error"]


def test_what_the_child_raised_reaches_the_parent():
    with pytest.raises(ChildFailed, match="ValueError: no room to measure") as raised:
        in_own_process(hanging_child.raise_value_error, "no room to measure")
    assert not raised.value.transient


def test_what_the_child_returned_reaches_the_parent():
    assert in_own_process(hanging_child.double, 21) == 42


def test_a_kill_reaches_what_the_child_started(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    with pytest.raises(RuntimeError, match="did not finish within 5 seconds"):
        in_own_process(hanging_child.hang_in_a_grandchild, str(pid_file), timeout_seconds=5)
    assert _gone(int(pathlib.Path(pid_file).read_text()))


def test_the_server_runs_jobs_in_their_own_process_unless_told_not_to(monkeypatch):
    monkeypatch.delenv("SP_JOBS_IN_PROCESS", raising=False)
    assert Settings.from_environment().jobs_in_own_process
    monkeypatch.setenv("SP_JOBS_IN_PROCESS", "1")
    assert not Settings.from_environment().jobs_in_own_process

