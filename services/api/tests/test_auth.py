"""Nobody reaches a shop that is not theirs.

The middleware in `auth` is the only thing standing between one owner's floor
plan and everybody else, so these tests walk the boundary rather than the happy
path: every scan route is checked against a signed-in stranger, because a guard
that covers most of the surface covers none of it.
"""

import uuid

import pytest
from conftest import OWNER_EMAIL, OWNER_PASSWORD, create_scan, sign_in, sign_out, sign_up

from standardphysics_api import accounts
from standardphysics_api.auth import COOKIE_NAME


def test_signing_up_returns_the_owner_and_sets_a_session(client):
    body = client.get("/api/auth/session").json()
    assert body["email"] == OWNER_EMAIL
    assert body["shop_name"] == "Corner cafe"
    assert uuid.UUID(body["owner_id"])


def test_the_session_cookie_is_not_readable_by_scripts(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        response = fresh.post(
            "/api/auth/sign-up",
            json={"email": "cookie@example.com", "password": "a-long-enough-password", "shop_name": "Cookie"},
        )
    cookie = response.headers["set-cookie"]
    assert COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_the_token_is_never_in_the_response_body(client):
    assert "token" not in client.get("/api/auth/session").text.lower()


def test_a_second_account_cannot_take_the_same_email(client):
    response = client.post(
        "/api/auth/sign-up",
        json={"email": OWNER_EMAIL.upper(), "password": "a-different-password", "shop_name": "Copycat"},
    )
    assert response.status_code == 409


def test_a_short_password_is_refused(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        response = fresh.post(
            "/api/auth/sign-up", json={"email": "short@example.com", "password": "abc", "shop_name": "Short"}
        )
    assert response.status_code == 400


def test_an_address_that_is_not_an_email_is_refused(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        response = fresh.post(
            "/api/auth/sign-up",
            json={"email": "not-an-email", "password": "a-long-enough-password", "shop_name": "Nope"},
        )
    assert response.status_code == 400


def test_the_wrong_password_does_not_sign_anyone_in(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        sign_up(fresh)
        sign_out(fresh)
        assert fresh.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": "wrong"}).status_code == 401


def test_signing_in_again_works_after_signing_out(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        sign_up(fresh)
        scan_id = create_scan(fresh)
        sign_out(fresh)
        assert fresh.get("/api/scans").status_code == 401
        sign_in(fresh, OWNER_EMAIL, OWNER_PASSWORD)
        assert scan_id in [s["id"] for s in fresh.get("/api/scans").json()["scans"]]


def test_signing_out_kills_the_token_everywhere(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        sign_up(fresh)
        stolen = fresh.cookies[COOKIE_NAME]
        sign_out(fresh)
        assert fresh.get("/api/scans", headers={"Authorization": f"Bearer {stolen}"}).status_code == 401


def test_the_phone_can_use_the_same_token_as_a_bearer_header(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        sign_up(fresh)
        token = fresh.cookies[COOKIE_NAME]
        fresh.cookies.clear()
        assert fresh.get("/api/scans", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_a_made_up_token_is_not_a_session(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        assert fresh.get("/api/scans", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_repeated_wrong_passwords_are_slowed_down(make_client):
    with make_client(sign_in_as_owner=False) as fresh:
        sign_up(fresh)
        attempt = {"email": OWNER_EMAIL, "password": "wrong-password"}
        codes = [fresh.post("/api/auth/sign-in", json=attempt).status_code for _ in range(12)]
    assert codes[0] == 401
    assert codes[-1] == 429


SCAN_ROUTES = [
    ("get", ""),
    ("delete", ""),
    ("get", "/scene"),
    ("get", "/assessment"),
    ("get", "/report"),
    ("get", "/scenario"),
    ("get", "/scenario/suggestion"),
    ("post", "/complete"),
    ("get", "/scene.glb"),
    ("get", "/simulations"),
    ("get", "/textures"),
]


@pytest.mark.parametrize("method,suffix", SCAN_ROUTES)
def test_a_stranger_cannot_reach_another_shop(client, stranger, method, suffix):
    scan_id = create_scan(client)
    response = getattr(stranger, method)(f"/api/scans/{scan_id}{suffix}")
    assert response.status_code == 404, f"{method.upper()} {suffix} leaked with {response.status_code}"


@pytest.mark.parametrize("method,suffix", SCAN_ROUTES)
def test_nobody_signed_out_reaches_a_shop(client, make_client, method, suffix):
    scan_id = create_scan(client)
    with make_client(sign_in_as_owner=False) as anonymous:
        assert getattr(anonymous, method)(f"/api/scans/{scan_id}{suffix}").status_code == 401


def test_a_stranger_cannot_upload_into_another_shop(client, stranger):
    scan_id = create_scan(client)
    response = stranger.put(
        f"/api/scans/{scan_id}/artifacts/room.json",
        content=b"{}",
        headers={"X-Artifact-Kind": "room_json", "X-Checksum-SHA256": "0" * 64},
    )
    assert response.status_code == 404


def test_the_scan_list_holds_only_your_own_shops(client, stranger):
    mine = create_scan(client)
    theirs = create_scan(stranger)
    assert [s["id"] for s in client.get("/api/scans").json()["scans"]] == [mine]
    assert [s["id"] for s in stranger.get("/api/scans").json()["scans"]] == [theirs]


def test_a_missing_scan_is_not_found_rather_than_forbidden(client):
    assert client.get(f"/api/scans/{uuid.uuid4()}").status_code == 404


def test_a_stored_password_is_not_the_password(client):
    stored = accounts.hash_password("a-long-enough-password")
    assert "a-long-enough-password" not in stored
    assert stored.startswith("scrypt$")
    assert accounts.verify_password("a-long-enough-password", stored)
    assert not accounts.verify_password("a-long-enough-passwore", stored)


def test_two_owners_with_the_same_password_do_not_share_a_hash(client):
    assert accounts.hash_password("same-password-here") != accounts.hash_password("same-password-here")


def test_a_damaged_hash_verifies_nothing(client):
    assert not accounts.verify_password("anything", "not-a-hash")
    assert not accounts.verify_password("anything", "scrypt$bad$8$1$aa$bb")
