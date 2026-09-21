"""Ending an account takes the shops, the files and the sessions with it."""
import pathlib

from conftest import OWNER_EMAIL, OWNER_PASSWORD, create_scan, put_artifact, sign_up
from standardphysics_api import repository as repo


def scan_directory(test_client, scan_id: str) -> pathlib.Path:
    return test_client.app.state.store.scan_dir(scan_id)


def test_deleting_an_account_removes_its_scans_and_their_files(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room.json", b"{}", "room_json")
    directory = scan_directory(client, scan_id)
    assert directory.exists()

    assert client.delete("/api/account").status_code == 204

    assert not directory.exists()
    assert client.get("/api/auth/session").status_code == 401
    assert client.get(f"/api/scans/{scan_id}").status_code == 401


def test_the_email_can_register_again_afterwards(client):
    assert client.delete("/api/account").status_code == 204
    sign_up(client, OWNER_EMAIL, OWNER_PASSWORD)
    assert client.get("/api/auth/session").status_code == 200


def test_the_old_password_no_longer_signs_in(client):
    assert client.delete("/api/account").status_code == 204
    response = client.post(
        "/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert response.status_code == 401


def test_a_signed_out_caller_cannot_delete_an_account(make_client):
    with make_client(sign_in_as_owner=False) as test_client:
        assert test_client.delete("/api/account").status_code == 401


def test_one_account_going_leaves_another_alone(client, stranger):
    mine = create_scan(client)
    theirs = create_scan(stranger)
    put_artifact(stranger, theirs, "room.json", b"{}", "room_json")

    assert client.delete("/api/account").status_code == 204

    assert stranger.get(f"/api/scans/{theirs}").status_code == 200
    assert scan_directory(stranger, theirs).exists()
    assert not scan_directory(stranger, mine).exists()


def test_a_scan_with_a_running_job_does_not_block_deletion(client):
    scan_id = create_scan(client)
    put_artifact(client, scan_id, "room.json", b"{}", "room_json")
    with client.app.state.database.transaction() as connection:
        connection.execute(
            "INSERT INTO jobs (scan_id, kind, revision, state, created_at)"
            " VALUES (?, 'reconstruct', 0, 'running', ?)",
            (scan_id, repo.now()),
        )
    assert client.delete(f"/api/scans/{scan_id}").status_code == 409
    assert client.delete("/api/account").status_code == 204
    assert not scan_directory(client, scan_id).exists()


def test_deleting_an_account_with_no_scans(client):
    assert client.delete("/api/account").status_code == 204
