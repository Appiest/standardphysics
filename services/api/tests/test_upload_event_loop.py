"""An upload waiting on the database never stalls the rest of the server.

SQLite waits up to 30 seconds for a write lock. The upload route is async, so a
wait on the event loop would hold every other request, /health included, for as
long as the lock is held. Its database work runs on worker threads instead.
"""

import contextlib
import sqlite3
import threading
import time

from conftest import create_scan, put_artifact
from standardphysics_api import upload_routes

LOCK_HELD_FOR = 3.0


@contextlib.contextmanager
def _write_lock_held(database_path):
    """Another writer holding SQLite's write lock, released after LOCK_HELD_FOR seconds at the latest."""
    connection = sqlite3.connect(database_path, isolation_level=None, check_same_thread=False)
    connection.execute("BEGIN IMMEDIATE")
    released = threading.Lock()

    def release():
        with released:
            if connection.in_transaction:
                connection.execute("ROLLBACK")

    timer = threading.Timer(LOCK_HELD_FOR, release)
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        release()
        connection.close()


def test_the_server_answers_health_while_an_upload_waits_for_the_write_lock(make_client, monkeypatch, tmp_path):
    storing = threading.Event()
    store_artifact = upload_routes._accept_staged

    def noted(*arguments):
        storing.set()
        return store_artifact(*arguments)

    monkeypatch.setattr(upload_routes, "_accept_staged", noted)
    with make_client() as phone:
        scan_id = create_scan(phone)
        responses = []
        upload = threading.Thread(
            target=lambda: responses.append(put_artifact(phone, scan_id, "room-json", b'{"walls": []}', "room_json"))
        )
        with _write_lock_held(tmp_path / "var" / "standardphysics.sqlite3"):
            upload.start()
            assert storing.wait(timeout=5)
            time.sleep(0.2)
            asked = time.monotonic()
            health = phone.get("/health")
            waited = time.monotonic() - asked
            upload_still_waiting = upload.is_alive()
        upload.join(timeout=10)
    assert health.status_code == 200
    assert upload_still_waiting and waited < 1.0
    assert [response.status_code for response in responses] == [201]
