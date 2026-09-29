"""Furniture refinement runs only where SP_FURNITURE_PYTHON and SP_FURNITURE_SOURCE name a SPAR3D runtime."""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import sys
import uuid

from test_textures import _room, _scan_build, furniture_settings

from conftest import FIXTURE_DATA, drain
from standardphysics_api import furniture
from standardphysics_api import repository_jobs as jobs_repo
from standardphysics_api.furniture import FURNITURE, UNAVAILABLE, FurnitureRuntime, furniture_runtime
from standardphysics_api.settings import Settings
from standardphysics_api.textures import build_dir, finish_build, record_build, staged_build_dir

TESTS = pathlib.Path(__file__).resolve().parent


def _painted_build(client) -> tuple[str, int]:
    """A finished photo build with LiDAR and frames behind it, the kind furniture refinement starts from."""
    scan_id, graph = _room(client)
    key = hashlib.sha256(b"painted for furniture").hexdigest()
    staged = staged_build_dir(client.app.state.store, scan_id)
    shutil.copyfile(FIXTURE_DATA / "shop.glb", staged / "scan.glb")
    shutil.copyfile(FIXTURE_DATA / "shop.glb", staged / "scene.glb")
    result = _scan_build(scan_id, graph, key)
    finish_build(staged, build_dir(client.app.state.store, scan_id) / key, result)
    record_build(client.app.state.database, scan_id, key, graph, {"lidar": "mesh", "frames": {"f": "p"}}, result)
    with client.app.state.database.connect() as connection:
        build_id = connection.execute("SELECT id FROM texture_builds WHERE scan_id=?", (scan_id,)).fetchone()[0]
    return scan_id, build_id


def _furniture_jobs(client) -> list:
    with client.app.state.database.connect() as connection:
        return connection.execute("SELECT state, error FROM jobs WHERE kind=?", (FURNITURE,)).fetchall()


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_an_unconfigured_server_queues_no_furniture_and_says_it_is_unavailable(make_client):
    with make_client() as client:
        scan_id, build_id = _painted_build(client)
        furniture.queue_furniture(client.app.state.database, client.app.state.worker, uuid.UUID(scan_id), build_id)
        status = client.get(f"/api/scans/{scan_id}/furniture").json()
        retry = client.post(f"/api/scans/{scan_id}/furniture")
        health = client.get("/health/details").json()["furniture_refinement"]
        jobs = _furniture_jobs(client)
    assert jobs == []
    assert status["state"] == "unavailable"
    assert retry.status_code == 503 and retry.json()["error"] == UNAVAILABLE
    assert health == {"available": False, "reason": "SP_FURNITURE_PYTHON and SP_FURNITURE_SOURCE are not set"}


def test_a_furniture_job_left_from_before_ends_at_once_with_a_plain_reason(make_client):
    with make_client() as client:
        scan_id, build_id = _painted_build(client)
        with client.app.state.database.transaction() as connection:
            jobs_repo.enqueue_job(connection, uuid.UUID(scan_id), FURNITURE, build_id)
        drain(client)
        jobs = _furniture_jobs(client)
        status = client.get(f"/api/scans/{scan_id}/furniture").json()
    assert [job["state"] for job in jobs] == ["failed"]
    assert UNAVAILABLE in jobs[0]["error"]
    assert status["state"] == "unavailable"


def test_a_configured_server_queues_furniture_and_reports_it_available(make_client, tmp_path):
    with make_client(**furniture_settings(tmp_path)) as client:
        scan_id, build_id = _painted_build(client)
        furniture.queue_furniture(client.app.state.database, client.app.state.worker, uuid.UUID(scan_id), build_id)
        status = client.get(f"/api/scans/{scan_id}/furniture").json()
        health = client.get("/health/details").json()["furniture_refinement"]
    assert status["state"] == "queued"
    assert health == {"available": True, "reason": None}


def test_a_configured_path_that_is_missing_turns_furniture_off(tmp_path):
    settings = Settings(furniture_python=tmp_path / "no-python", furniture_source=tmp_path)
    assert furniture_runtime(settings) is None
    assert furniture.furniture_health(settings) == {"available": False, "reason": "SP_FURNITURE_PYTHON is not a file"}


def test_the_environment_names_the_runtime_and_the_script_is_handed_it(monkeypatch, tmp_path):
    source = tmp_path / "spar3d"
    source.mkdir()
    monkeypatch.setenv("SP_FURNITURE_PYTHON", sys.executable)
    monkeypatch.setenv("SP_FURNITURE_SOURCE", str(source))
    monkeypatch.setattr(furniture, "INFERENCE_SCRIPT", TESTS / "echo_furniture_script.py")
    runtime = furniture_runtime(Settings.from_environment())
    node = uuid.uuid4()
    reports = furniture._run_candidates(uuid.uuid4(), [node], tmp_path / "work", runtime)
    assert runtime == FurnitureRuntime(pathlib.Path(sys.executable), source, Settings.furniture_timeout_seconds)
    assert reports == [{"node_id": str(node), "status": "rejected", "python": sys.executable, "source": str(source)}]


def test_a_furniture_script_that_hangs_is_stopped_at_its_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(furniture, "INFERENCE_SCRIPT", TESTS / "hanging_furniture_script.py")
    runtime = FurnitureRuntime(pathlib.Path(sys.executable), tmp_path, timeout_seconds=3)
    node = uuid.uuid4()
    reports = furniture._run_candidates(uuid.uuid4(), [node], tmp_path / "work", runtime)
    assert reports == [
        {"node_id": str(node), "status": "failed", "error": "SPAR3D process was stopped after 3 seconds"}
    ]
    assert _gone(int((tmp_path / "work" / "script.pid").read_text()))
