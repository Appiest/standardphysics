"""No request waits unbounded for the assess or search lock, and a streamed loop lets go of the search lock between
passes, so a slow reader can't hold up another search."""

import pytest
from standardphysics_fixtures import node_id

from conftest import drain, no_blender_stages
from standardphysics_api import stages as stages_module


@pytest.fixture
def busy_soon(monkeypatch):
    monkeypatch.setattr(stages_module, "LOCK_WAIT_SECONDS", 0.1)


def _sample(make_client, stages, team: bool = False):
    client = make_client(seed=True, stages=stages, team=team).__enter__()
    drain(client)
    return client, client.get("/api/scans").json()["scans"][0]["id"]


def _still(node: str) -> dict:
    return {"node_id": node, "delta_translation": {"x": 0.0, "y": 0.0, "z": 0.0}, "delta_rotation_z_degrees": 0.0}


def test_a_drag_check_behind_a_held_assess_lock_is_told_to_retry(make_client, busy_soon):
    stages = no_blender_stages()
    client, scan_id = _sample(make_client, stages)
    body = {"base_revision": 0, "sequence": 1, "moves": [_still(str(node_id("case_east")))]}
    with stages._assess_lock:
        refused = client.post(f"/api/scans/{scan_id}/layout-checks", json=body)
    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == str(stages_module.BUSY_RETRY_SECONDS)
    assert refused.json()["error"] == stages_module.BUSY
    assert client.post(f"/api/scans/{scan_id}/layout-checks", json=body).status_code == 200


def test_a_question_behind_a_held_search_lock_is_told_to_retry(make_client, busy_soon):
    stages = no_blender_stages()
    client, scan_id = _sample(make_client, stages, team=True)
    with stages._search_lock:
        refused = client.post(f"/api/scans/{scan_id}/ask", json={"text": "How wide is the aisle?", "base_revision": 0})
    assert refused.status_code == 503
    assert "Retry-After" in refused.headers


def test_the_loop_holds_the_search_lock_only_while_a_pass_runs(monkeypatch):
    stages = no_blender_stages()
    held_while_running: list[bool] = []

    def two_passes(*_args, **_kwargs):
        for index in range(2):
            held_while_running.append(stages._search_lock.locked())
            yield index

    monkeypatch.setattr(stages_module, "loop_steps", two_passes)
    steps = stages._loop_steps(graph=None, scenario=None, router=None, rejection=None)
    assert next(steps) == 0
    assert stages._search_lock.acquire(blocking=False), "the lock stayed held while the first pass was handed on"
    stages._search_lock.release()
    assert list(steps) == [1]
    assert held_while_running == [True, True]
