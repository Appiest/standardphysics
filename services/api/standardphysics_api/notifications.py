"""Telling the owner's phone when something is ready, through Apple's push service.

Three things send a push: results that are ready for the first time, a photo a
person on the team has checked, and the reminder three days before a guest's
shops are deleted. Each goes to every phone the account has registered.

Apple's service speaks HTTP/2 and takes a short-lived token signed with the
team's push key. Without a key configured, pushes are logged and dropped, so
the server runs the same everywhere else.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

log = logging.getLogger(__name__)

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
TOKEN_REUSE_SECONDS = 50 * 60
"""Apple refuses a push token older than an hour and throttles one made more
often than every twenty minutes, so one is made and kept for fifty."""
GONE = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


@dataclass(frozen=True)
class Push:
    title: str
    body: str
    scan_id: uuid.UUID | None = None


class Notifier(Protocol):
    def send(self, database, owner_id: uuid.UUID, push: Push) -> None: ...


def register(connection: sqlite3.Connection, owner_id: uuid.UUID, token: str, environment: str) -> None:
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO devices (token, owner_id, environment, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (token) DO UPDATE SET owner_id = excluded.owner_id, environment = excluded.environment,"
        " last_seen_at = excluded.last_seen_at",
        (token.lower(), str(owner_id), environment, now, now),
    )


def forget(connection: sqlite3.Connection, token: str, owner_id: uuid.UUID | None = None) -> None:
    if owner_id is None:
        connection.execute("DELETE FROM devices WHERE token = ?", (token.lower(),))
        return
    connection.execute("DELETE FROM devices WHERE token = ? AND owner_id = ?", (token.lower(), str(owner_id)))


def devices_of(connection: sqlite3.Connection, owner_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = connection.execute("SELECT token, environment FROM devices WHERE owner_id = ?", (str(owner_id),))
    return [(row["token"], row["environment"]) for row in rows]


class LoggedNotifier:
    """Stands in when no push key is configured: the push is written to the log and goes nowhere."""

    def send(self, database, owner_id: uuid.UUID, push: Push) -> None:
        log.info("push for %s not sent, no APNs key configured: %s", owner_id, push.title)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def provider_token(key_pem: bytes, key_id: str, team_id: str, issued_at: int) -> str:
    """The ES256 token Apple asks for, signed with the team's .p8 push key."""
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("an APNs key is an elliptic curve key")
    header = _b64(json.dumps({"alg": "ES256", "kid": key_id}).encode())
    claims = _b64(json.dumps({"iss": team_id, "iat": issued_at}).encode())
    signed = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signed, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def payload(push: Push) -> dict:
    body: dict = {"aps": {"alert": {"title": push.title, "body": push.body}, "sound": "default"}}
    if push.scan_id is not None:
        body["scan_id"] = str(push.scan_id)
    return body


class ApnsNotifier:
    def __init__(self, key_pem: bytes, key_id: str, team_id: str, topic: str, client: httpx.Client | None = None):
        self.key_pem, self.key_id, self.team_id, self.topic = key_pem, key_id, team_id, topic
        self.client = client or httpx.Client(http2=True, timeout=10)
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _bearer(self) -> str:
        with self._lock:
            if self._token is None or time.time() - self._token[1] > TOKEN_REUSE_SECONDS:
                issued = int(time.time())
                self._token = (provider_token(self.key_pem, self.key_id, self.team_id, issued), issued)
            return self._token[0]

    def _deliver(self, device: str, environment: str, push: Push) -> bool:
        """Sends one push. False when Apple says the device token is no good any more."""
        response = self.client.post(
            f"{HOSTS.get(environment, HOSTS['production'])}/3/device/{device}",
            json=payload(push),
            headers={
                "authorization": f"bearer {self._bearer()}", "apns-topic": self.topic,
                "apns-push-type": "alert", "apns-priority": "10",
            },
        )
        if response.status_code == 200:
            return True
        reason = (response.json() if response.content else {}).get("reason", "")
        log.warning("push to %s… refused with %s %s", device[:8], response.status_code, reason)
        return reason not in GONE

    def send(self, database, owner_id: uuid.UUID, push: Push) -> None:
        with database.connect() as connection:
            devices = devices_of(connection, owner_id)
        for device, environment in devices:
            try:
                kept = self._deliver(device, environment, push)
            except httpx.HTTPError:
                log.warning("push to %s… could not reach Apple", device[:8])
                continue
            if not kept:
                with database.transaction() as connection:
                    forget(connection, device)


def notifier_from(settings) -> Notifier:
    key = settings.apns_key
    if not (key and settings.apns_key_id and settings.apns_team_id):
        return LoggedNotifier()
    return ApnsNotifier(key.encode(), settings.apns_key_id, settings.apns_team_id, settings.apns_topic)
