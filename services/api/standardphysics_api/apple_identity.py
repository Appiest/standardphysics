"""Sign in with Apple: check the identity token the phone gets from Apple.

The token is a JSON Web Token signed with one of Apple's published RSA keys.
It proves who the person is only when the signature is Apple's, it was issued
for this app, and it hasn't expired. Anything else gets refused, and nothing in
it is trusted before the signature checks out.

The keys come from https://appleid.apple.com/auth/keys and are kept for a day.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ISSUER = "https://appleid.apple.com"
KEYS_URL = "https://appleid.apple.com/auth/keys"
KEYS_KEPT_SECONDS = 24 * 3600
CLOCK_LEEWAY_SECONDS = 60


class NotFromApple(Exception):
    pass


@dataclass(frozen=True)
class AppleIdentity:
    subject: str
    """Apple's stable id for this person and this developer team."""
    email: str | None
    email_verified: bool = False


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _integer(part: str) -> int:
    return int.from_bytes(_decode(part), "big")


def fetch_keys() -> dict:
    with urllib.request.urlopen(KEYS_URL, timeout=10) as response:  # noqa: S310 - a fixed https address
        return json.load(response)


class _KeyCache:
    def __init__(self) -> None:
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    def key(self, kid: str) -> rsa.RSAPublicKey:
        with self._lock:
            stale = time.time() - self._fetched > KEYS_KEPT_SECONDS
            if stale or kid not in self._keys:
                self._keys = {entry["kid"]: _public_key(entry) for entry in fetch_keys()["keys"]}
                self._fetched = time.time()
        if kid not in self._keys:
            raise NotFromApple("unknown signing key")
        return self._keys[kid]

    def forget(self) -> None:
        with self._lock:
            self._keys, self._fetched = {}, 0.0


def _public_key(entry: dict) -> rsa.RSAPublicKey:
    return rsa.RSAPublicNumbers(_integer(entry["e"]), _integer(entry["n"])).public_key()


KEYS = _KeyCache()


def _signed_parts(token: str) -> tuple[dict, dict, bytes, bytes]:
    try:
        header_part, claims_part, signature_part = token.split(".")
        header, claims = json.loads(_decode(header_part)), json.loads(_decode(claims_part))
    except (ValueError, json.JSONDecodeError) as exc:
        raise NotFromApple("not a token") from exc
    return header, claims, f"{header_part}.{claims_part}".encode(), _decode(signature_part)


def _check_claims(claims: dict, audiences: frozenset[str], now: float) -> None:
    if claims.get("iss") != ISSUER:
        raise NotFromApple("wrong issuer")
    if claims.get("aud") not in audiences:
        raise NotFromApple("issued for another app")
    if float(claims.get("exp", 0)) < now - CLOCK_LEEWAY_SECONDS:
        raise NotFromApple("expired")
    if not claims.get("sub"):
        raise NotFromApple("no subject")


def verify(token: str, audiences: frozenset[str], now: float | None = None) -> AppleIdentity:
    header, claims, signed, signature = _signed_parts(token)
    if header.get("alg") != "RS256":
        raise NotFromApple("unexpected algorithm")
    try:
        KEYS.key(str(header.get("kid"))).verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise NotFromApple("bad signature") from exc
    _check_claims(claims, audiences, time.time() if now is None else now)
    email = claims.get("email")
    verified = str(claims.get("email_verified", "")).lower() == "true"
    return AppleIdentity(subject=str(claims["sub"]), email=str(email) if email else None, email_verified=verified)
