"""Guest accounts the phone makes on first launch, and the ways to keep them."""

import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from conftest import OWNER_EMAIL, create_scan, sign_up
from standardphysics_api import apple_identity

APP = "com.standardphysics.capture"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(key, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256",
            "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")), "e": _b64(numbers.e.to_bytes(3, "big"))}


def _token(subject: str = "apple-001", email: str | None = "owner@privaterelay.appleid.com", signer=KEY, **changes) -> str:
    claims = {"iss": "https://appleid.apple.com", "aud": APP, "sub": subject, "exp": time.time() + 600,
              "iat": time.time(), "email": email, "email_verified": "true"} | changes
    signing_input = f"{_b64(json.dumps({'alg': 'RS256', 'kid': 'test'}).encode())}.{_b64(json.dumps(claims).encode())}"
    return f"{signing_input}.{_b64(signer.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


@pytest.fixture(autouse=True)
def apple_keys(monkeypatch):
    apple_identity.KEYS.forget()
    monkeypatch.setattr(apple_identity, "fetch_keys", lambda: {"keys": [_jwk(KEY, "test")]})
    yield
    apple_identity.KEYS.forget()


@pytest.fixture
def guest(make_client):
    with make_client(sign_in_as_owner=False) as test_client:
        made = test_client.post("/api/auth/guest")
        assert made.status_code == 201
        yield test_client


def test_a_guest_can_upload_before_giving_an_email(guest):
    session = guest.get("/api/auth/session").json()
    assert session["guest"] is True
    assert session["email"] is None
    assert session["shop_name"] == "My shop"
    deletes_at = datetime.fromisoformat(session["deletes_at"])
    assert timedelta(days=29) < deletes_at - datetime.now(UTC) <= timedelta(days=30)
    assert create_scan(guest)


def test_asking_for_a_guest_twice_keeps_the_same_account(guest):
    first = guest.get("/api/auth/session").json()["owner_id"]
    assert guest.post("/api/auth/guest").json()["owner_id"] == first


def test_saving_a_guest_keeps_its_shops_and_its_session(guest):
    scan_id = create_scan(guest)
    saved = guest.post("/api/auth/save", json={"email": "new@example.com", "password": "a-long-enough-password"})
    assert saved.status_code == 200
    assert saved.json()["guest"] is False
    assert saved.json()["email"] == "new@example.com"
    assert saved.json()["deletes_at"] is None
    assert guest.get(f"/api/scans/{scan_id}").status_code == 200
    guest.post("/api/auth/sign-out")
    assert guest.post("/api/auth/sign-in", json={"email": "new@example.com", "password": "a-long-enough-password"}).status_code == 200


def test_saving_with_an_email_that_has_an_account_says_to_sign_in(client, make_client):
    with make_client(sign_in_as_owner=False) as phone:
        phone.post("/api/auth/guest")
        taken = phone.post("/api/auth/save", json={"email": OWNER_EMAIL, "password": "a-long-enough-password"})
    assert taken.status_code == 409
    assert "Sign in with it" in taken.json()["error"]


def test_a_saved_account_cannot_be_saved_again(client):
    assert client.post("/api/auth/save", json={"email": "x@example.com", "password": "a-long-enough-password"}).status_code == 409


def test_signing_in_as_a_guest_moves_the_guest_shops_into_the_account(client, make_client):
    with make_client(sign_in_as_owner=False) as phone:
        phone.post("/api/auth/guest")
        scan_id = create_scan(phone)
        signed = phone.post("/api/auth/sign-in", json={"email": OWNER_EMAIL, "password": "a-long-enough-password"})
        assert signed.status_code == 200
        assert signed.json()["guest"] is False
        assert [scan["id"] for scan in phone.get("/api/scans").json()["scans"]] == [scan_id]


def test_signing_up_as_a_guest_saves_the_guest(guest):
    owner_id = guest.get("/api/auth/session").json()["owner_id"]
    scan_id = create_scan(guest)
    assert sign_up(guest, "fresh@example.com", "a-long-enough-password") == owner_id
    assert guest.get(f"/api/scans/{scan_id}").status_code == 200


def test_apple_makes_an_account_for_someone_new(make_client):
    with make_client(sign_in_as_owner=False) as phone:
        session = phone.post("/api/auth/apple", json={"identity_token": _token()})
        assert session.status_code == 200
        assert session.json()["guest"] is False
        assert session.json()["email"] == "owner@privaterelay.appleid.com"
        assert phone.get("/api/auth/session").status_code == 200


def test_apple_keeps_a_guest_and_its_shops(guest):
    owner_id = guest.get("/api/auth/session").json()["owner_id"]
    scan_id = create_scan(guest)
    session = guest.post("/api/auth/apple", json={"identity_token": _token()}).json()
    assert session["owner_id"] == owner_id
    assert session["guest"] is False
    assert guest.get(f"/api/scans/{scan_id}").status_code == 200


def test_apple_on_a_second_phone_brings_the_guest_shops_home(make_client):
    with make_client(sign_in_as_owner=False) as first:
        first_owner = first.post("/api/auth/apple", json={"identity_token": _token()}).json()["owner_id"]
    with make_client(sign_in_as_owner=False) as second:
        second.post("/api/auth/guest")
        scan_id = create_scan(second)
        session = second.post("/api/auth/apple", json={"identity_token": _token()}).json()
        assert session["owner_id"] == first_owner
        assert [scan["id"] for scan in second.get("/api/scans").json()["scans"]] == [scan_id]


def test_apple_finds_the_account_that_already_has_its_email(client, make_client):
    owner_id = client.get("/api/auth/session").json()["owner_id"]
    with make_client(sign_in_as_owner=False) as phone:
        session = phone.post("/api/auth/apple", json={"identity_token": _token(email=OWNER_EMAIL)}).json()
    assert session["owner_id"] == owner_id


@pytest.mark.parametrize("token", [
    _token(signer=OTHER_KEY),
    _token(aud="com.someone.else"),
    _token(exp=time.time() - 3600),
    _token(iss="https://example.com"),
    "not.a.token",
])
def test_a_token_apple_did_not_issue_for_this_app_is_refused(make_client, token):
    with make_client(sign_in_as_owner=False) as phone:
        refused = phone.post("/api/auth/apple", json={"identity_token": token})
    assert refused.status_code == 400


def test_opening_a_shop_pushes_back_when_a_guest_shop_is_deleted(guest):
    scan_id = create_scan(guest)
    database = guest.app.state.database
    long_ago = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    with database.connect() as connection:
        connection.execute("UPDATE scans SET created_at = ?, last_opened_at = ? WHERE id = ?", (long_ago, long_ago, scan_id))
    before = datetime.fromisoformat(guest.get("/api/auth/session").json()["deletes_at"])
    guest.get(f"/api/scans/{scan_id}")
    after = datetime.fromisoformat(guest.get("/api/auth/session").json()["deletes_at"])
    assert after - before > timedelta(days=19)


def test_a_saved_account_is_never_scheduled_for_deletion(client):
    session = client.get("/api/auth/session").json()
    assert session["guest"] is False
    assert session["deletes_at"] is None


def test_one_network_cannot_make_endless_guests(make_client):
    with make_client(sign_in_as_owner=False) as phone:
        codes = []
        for _ in range(21):
            phone.cookies.clear()
            codes.append(phone.post("/api/auth/guest").status_code)
    assert codes[:20] == [201] * 20
    assert codes[20] == 429
