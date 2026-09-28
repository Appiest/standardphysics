"""Sign in with Apple under load: a throttle per network, a bounded key refresh, and clear refusals.

Apple's signing keys are never fetched from the network here; `fetch_keys` is
replaced in every test and counts its calls.
"""

import base64
import json
import time
import urllib.error
import uuid

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from standardphysics_api import apple_identity, owner_accounts

APP = "com.standardphysics.capture"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(key, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256",
            "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")), "e": _b64(numbers.e.to_bytes(3, "big"))}


def _token(kid: str = "test", signer=KEY, **changes) -> str:
    claims = {"iss": "https://appleid.apple.com", "aud": APP, "sub": "apple-001", "exp": time.time() + 600,
              "email": "owner@privaterelay.appleid.com", "email_verified": "true"} | changes
    signing_input = f"{_b64(json.dumps({'alg': 'RS256', 'kid': kid}).encode())}.{_b64(json.dumps(claims).encode())}"
    return f"{signing_input}.{_b64(signer.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


class _Keys:
    """A stand-in for Apple's key endpoint that serves whatever `served` holds and counts fetches."""

    def __init__(self, *served: dict):
        self.served: dict | BaseException = {"keys": list(served)}
        self.fetches = 0

    def __call__(self) -> dict:
        self.fetches += 1
        if isinstance(self.served, BaseException):
            raise self.served
        return self.served


@pytest.fixture
def apple_keys(monkeypatch):
    keys = _Keys(_jwk(KEY, "test"))
    apple_identity.KEYS.forget()
    monkeypatch.setattr(apple_identity, "fetch_keys", keys)
    yield keys
    apple_identity.KEYS.forget()


def _sign_in(phone, token: str):
    return phone.post("/api/auth/apple", json={"identity_token": token})


def test_a_flood_of_unknown_key_ids_fetches_apples_keys_once(make_client, apple_keys):
    with make_client(sign_in_as_owner=False) as phone:
        refusals = [_sign_in(phone, _token(kid=uuid.uuid4().hex)).status_code for _ in range(20)]
    assert refusals == [401] * 20
    assert apple_keys.fetches == 1


def test_a_rotated_key_is_picked_up_once_the_cooldown_has_passed(make_client, apple_keys, monkeypatch):
    with make_client(sign_in_as_owner=False) as phone:
        assert _sign_in(phone, _token()).status_code == 200
        apple_keys.served = {"keys": [_jwk(KEY, "test"), _jwk(OTHER_KEY, "rotated")]}
        rotated = _token(kid="rotated", signer=OTHER_KEY)
        assert _sign_in(phone, rotated).status_code == 401
        assert apple_keys.fetches == 1
        monkeypatch.setattr(apple_identity, "KEY_REFRESH_COOLDOWN_SECONDS", 0)
        assert _sign_in(phone, rotated).status_code == 200
    assert apple_keys.fetches == 2


@pytest.mark.parametrize("token", [
    "not.a.token",
    "two.parts",
    f"{_b64(b'[]')}.{_b64(b'{}')}.{_b64(b'x')}",
    f"{_b64(b'{}')}.{_b64(b'7')}.{_b64(b'x')}",
    "!!!.???.***",
    _token(exp="soon"),
])
def test_a_malformed_token_is_refused_with_401(make_client, apple_keys, token):
    with make_client(sign_in_as_owner=False) as phone:
        refused = _sign_in(phone, token)
    assert refused.status_code == 401, refused.text
    assert "Sign in with Apple" in refused.json()["error"]


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("unreachable"),
    TimeoutError("timed out"),
    json.JSONDecodeError("bad", "<html>", 0),
    {"no keys": []},
])
def test_apple_being_unreachable_is_a_503_not_a_500(make_client, apple_keys, failure):
    apple_keys.served = failure
    with make_client(sign_in_as_owner=False) as phone:
        refused = _sign_in(phone, _token())
    assert refused.status_code == 503, refused.text
    assert int(refused.headers["retry-after"]) > 0


def test_one_network_can_only_try_apple_sign_in_so_often(make_client, apple_keys, monkeypatch):
    monkeypatch.setattr(owner_accounts, "APPLE_SIGN_INS_PER_ADDRESS", 3)
    with make_client(sign_in_as_owner=False) as phone:
        statuses = [_sign_in(phone, _token(kid="unknown")).status_code for _ in range(3)]
        throttled = _sign_in(phone, _token())
    assert statuses == [401] * 3
    assert throttled.status_code == 429
