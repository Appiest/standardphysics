"""The worker takes queued jobs in the order they were queued.

It used to take the lowest job id. A re-check queues a shop's render again on
the job row made days before, so every re-queued render went ahead of an
upload that arrived after it, and a new shop waited behind hours of renders.
"""

import uuid

from conftest import create_scan
from standardphysics_api import repository as repo


def test_a_job_queued_again_on_an_old_row_waits_behind_what_was_queued_before_it(client):
    earlier_shop, new_upload = uuid.UUID(create_scan(client)), uuid.UUID(create_scan(client))
    with client.app.state.database.transaction() as connection:
        repo.enqueue_job(connection, earlier_shop, "display", 0)
        render = repo.claim_job(connection)
        repo.finish_job(connection, render["id"])
        repo.enqueue_job(connection, new_upload, "process", 0)
        repo.queue_job_again(connection, earlier_shop, "display", 0)

        first, second = repo.claim_job(connection), repo.claim_job(connection)

    assert (first["scan_id"], first["kind"]) == (str(new_upload), "process")
    assert (second["scan_id"], second["kind"]) == (str(earlier_shop), "display")


def test_a_new_upload_is_measured_before_the_rechecks_a_deploy_queued_ahead_of_it(client):
    older_shops = [uuid.UUID(create_scan(client)) for _ in range(3)]
    new_upload = uuid.UUID(create_scan(client))
    with client.app.state.database.transaction() as connection:
        for shop in older_shops:
            repo.enqueue_job(connection, shop, "assess", 0)
        repo.enqueue_job(connection, new_upload, "process", 0)

        claimed = [repo.claim_job(connection, False) for _ in range(4)]

    assert (claimed[0]["scan_id"], claimed[0]["kind"]) == (str(new_upload), "process")
    assert [job["scan_id"] for job in claimed[1:]] == [str(shop) for shop in older_shops]


def test_jobs_queued_together_still_go_in_the_order_they_were_made(client):
    shop = uuid.UUID(create_scan(client))
    with client.app.state.database.transaction() as connection:
        for kind in ("process", "assess", "display"):
            repo.enqueue_job(connection, shop, kind, 0)

        claimed = [repo.claim_job(connection)["kind"] for _ in range(3)]

    assert claimed == ["process", "assess", "display"]
