"""Every request that queues a job answers to the same queue limit as finishing a walk.

Seeding the sample shop queues its checks, so with a limit of one the queue is
already full when each request below arrives. A refused request changes
nothing and says when to try again; the jobs a finished job queues for itself
are never refused.
"""

import pytest
from standardphysics_contracts import to_meters
from standardphysics_fixtures import FIX_SHIFT_INCHES, node_id

from conftest import create_scan, drain
from standardphysics_api import repository as repo
from standardphysics_api.worker import ASSESS

CASE_EAST = str(node_id("case_east"))
SHIFT = {"x": to_meters(FIX_SHIFT_INCHES), "y": 0.0, "z": 0.0}


def _seeded_shop(client) -> str:
    return client.get("/api/scans").json()["scans"][0]["id"]


def _save_layout(client, scan_id):
    move = {"node_id": CASE_EAST, "delta_translation": SHIFT, "delta_rotation_z_degrees": 0.0}
    return client.post(f"/api/scans/{scan_id}/revisions", json={"base_revision": 0, "moves": [move]})


def _mark_counter(client, scan_id):
    return client.put(f"/api/scans/{scan_id}/revisions/0/counters/{CASE_EAST}")


def _confirm_route(client, scan_id):
    route = client.get(f"/api/scans/{scan_id}/scenario/suggestion").json()
    return client.put(f"/api/scans/{scan_id}/scenario", json=route)


def _simulate(client, scan_id):
    return client.post(f"/api/scans/{scan_id}/simulations", json={"base_revision": 0, "samples": 2})


def _queued(client) -> int:
    with client.app.state.database.connect() as connection:
        return repo.queued_job_count(connection)


def _latest_revision(client, scan_id) -> int:
    return client.get(f"/api/scans/{scan_id}/scene").json()["revision"]


@pytest.mark.parametrize("ask", [_save_layout, _mark_counter, _confirm_route, _simulate])
def test_a_full_queue_refuses_new_work_with_a_retry_and_keeps_nothing(make_client, ask):
    with make_client(seed=True, team=True, max_queued_jobs=1) as client:
        scan_id = _seeded_shop(client)
        refused = ask(client, scan_id)
        queued_after = _queued(client)
        revision_after = _latest_revision(client, scan_id)
    assert refused.status_code == 503, refused.text
    assert int(refused.headers["retry-after"]) > 0
    assert queued_after == 1
    assert revision_after == 0


def test_the_same_request_goes_through_once_the_queue_has_room(make_client):
    with make_client(seed=True, max_queued_jobs=1) as client:
        scan_id = _seeded_shop(client)
        assert _save_layout(client, scan_id).status_code == 503
        drain(client)
        saved = _save_layout(client, scan_id)
    assert saved.status_code == 201, saved.text


def test_a_finished_job_still_queues_its_follow_up_when_the_queue_is_full(make_client):
    with make_client(seed=True, max_queued_jobs=1) as client:
        scan_id = _seeded_shop(client)
        waiting = create_scan(client)
        with client.app.state.database.transaction() as connection:
            repo.enqueue_job(connection, waiting, ASSESS, 0)
        drain(client)
        with client.app.state.database.connect() as connection:
            display = connection.execute(
                "SELECT state FROM jobs WHERE scan_id = ? AND kind = 'display'", (scan_id,)
            ).fetchone()
    assert display is not None and display["state"] == "done"
