"""Sign in with Apple: check the identity token the phone gets from Apple.

The token is a JSON Web Token signed with one of Apple's published RSA keys.
It proves who the person is only when the signature is Apple's, it was issued
for this app, and it hasn't expired. Anything else gets refused, and nothing in
it is trusted before the signature checks out.

The keys come from https://appleid.apple.com/auth/keys and are kept for a day.
A token signed with a key id we don't hold makes us fetch them again, since
Apple rotates keys, but no more than once every KEY_REFRESH_COOLDOWN_SECONDS.
Within that time an unknown key id is refused without asking Apple, so a flood
of tokens with made-up key ids costs one request to Apple a minute.
"""

from __future__ import annotations

import base64
import json
import logging
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
KEY_REFRESH_COOLDOWN_SECONDS = 60
CLOCK_LEEWAY_SECONDS = 60

log = logging.getLogger(__name__)


class NotFromApple(Exception):
    pass


class AppleUnavailable(Exception):
    """Apple's signing keys could not be fetched, so no token can be checked right now."""


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
        self._tried = float("-inf")
        self._last_fetch_failed = False
        self._lock = threading.Lock()

    def key(self, kid: str) -> rsa.RSAPublicKey:
        with self._lock:
            now = time.time()
            if self._refresh_due(kid, now):
                self._refresh(now)
            key, unavailable = self._keys.get(kid), self._last_fetch_failed
        if key is not None:
            return key
        if unavailable:
            raise AppleUnavailable("Apple's signing keys could not be fetched")
        raise NotFromApple("unknown signing key")

    def forget(self) -> None:
        with self._lock:
            self._keys, self._fetched = {}, 0.0
            self._tried, self._last_fetch_failed = float("-inf"), False

    def _refresh_due(self, kid: str, now: float) -> bool:
        if now - self._tried < KEY_REFRESH_COOLDOWN_SECONDS:
            return False
        return kid not in self._keys or now - self._fetched > KEYS_KEPT_SECONDS

    def _refresh(self, now: float) -> None:
        """Fetch the keys, keeping the ones held when the fetch fails, since they may still be Apple's."""
        self._tried = now
        try:
            self._keys = {entry["kid"]: _public_key(entry) for entry in fetch_keys()["keys"]}
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("could not fetch Apple's signing keys", exc_info=True)
            self._last_fetch_failed = True
            return
        self._fetched, self._last_fetch_failed = now, False


def _public_key(entry: dict) -> rsa.RSAPublicKey:
    return rsa.RSAPublicNumbers(_integer(entry["e"]), _integer(entry["n"])).public_key()


KEYS = _KeyCache()


def _signed_parts(token: str) -> tuple[dict, dict, bytes, bytes]:
    try:
        header_part, claims_part, signature_part = token.split(".")
        header, claims = json.loads(_decode(header_part)), json.loads(_decode(claims_part))
        signature = _decode(signature_part)
    except ValueError as exc:
        raise NotFromApple("not a token") from exc
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise NotFromApple("not a token")
    return header, claims, f"{header_part}.{claims_part}".encode(), signature


def _check_claims(claims: dict, audiences: frozenset[str], now: float) -> None:
    if claims.get("iss") != ISSUER:
        raise NotFromApple("wrong issuer")
    if claims.get("aud") not in audiences:
        raise NotFromApple("issued for another app")
    if _expiry(claims) < now - CLOCK_LEEWAY_SECONDS:
        raise NotFromApple("expired")
    if not claims.get("sub"):
        raise NotFromApple("no subject")


def _expiry(claims: dict) -> float:
    try:
        return float(claims.get("exp", 0))
    except (TypeError, ValueError):
        raise NotFromApple("unreadable expiry") from None


def verify(token: str, audiences: frozenset[str], now: float | None = None) -> AppleIdentity:
    """Who the token says signed in, once its signature and claims check out.

    Raises NotFromApple for any token that isn't a valid one from Apple for this
    app, and AppleUnavailable when Apple's keys can't be fetched to check it.
    """
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
