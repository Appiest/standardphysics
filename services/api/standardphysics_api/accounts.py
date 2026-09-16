"""Shop owners, their passwords and their sessions. Nothing here knows about HTTP.

Passwords are stored as scrypt digests. scrypt is memory-hard, so a stolen
database cannot be attacked with the parallelism a GPU brings to a plain hash,
and it ships with Python, so the server gains no dependency to keep patched.

Session tokens are stored as their SHA-256 digest. The plain token exists only
in the cookie on the owner's machine: a copy of this database hands an attacker
no live session.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

SESSION_LIFETIME = timedelta(days=30)

SCRYPT_COST = 2**14
SCRYPT_BLOCK_SIZE = 8
SCRYPT_PARALLELISM = 1
_SALT_BYTES = 16
_TOKEN_BYTES = 32

MIN_PASSWORD_LENGTH = 10
"""Long enough that the scrypt cost is not the only thing standing in the way."""


class EmailAlreadyRegistered(Exception):
    pass


class WeakPassword(Exception):
    pass


@dataclass(frozen=True)
class Owner:
    id: uuid.UUID
    email: str
    shop_name: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt)
    return f"scrypt${SCRYPT_COST}${SCRYPT_BLOCK_SIZE}${SCRYPT_PARALLELISM}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        cost, block_size, parallelism = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
    except ValueError:
        return False
    candidate = _scrypt(password, salt, cost, block_size, parallelism)
    return hmac.compare_digest(candidate, expected)


def _scrypt(
    password: str,
    salt: bytes,
    cost: int = SCRYPT_COST,
    block_size: int = SCRYPT_BLOCK_SIZE,
    parallelism: int = SCRYPT_PARALLELISM,
) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=cost,
        r=block_size,
        p=parallelism,
        maxmem=256 * 1024 * 1024,
        dklen=32,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _owner(row: sqlite3.Row) -> Owner:
    return Owner(id=uuid.UUID(row["id"]), email=row["email"], shop_name=row["shop_name"])


def register(connection: sqlite3.Connection, email: str, password: str, shop_name: str) -> Owner:
    """Create an owner. Raises if the email is taken or the password is too short."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword
    address = normalize_email(email)
    owner_id = uuid.uuid4()
    try:
        connection.execute(
            "INSERT INTO owners (id, email, shop_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(owner_id), address, shop_name.strip(), hash_password(password), _now().isoformat()),
        )
    except sqlite3.IntegrityError as exc:
        raise EmailAlreadyRegistered from exc
    return Owner(id=owner_id, email=address, shop_name=shop_name.strip())


def authenticate(connection: sqlite3.Connection, email: str, password: str) -> Owner | None:
    """The owner behind these credentials, or None.

    A missing email still pays for one scrypt call. Answering "no such account"
    faster than "wrong password" would tell an attacker which emails are worth
    guessing at.
    """
    row = connection.execute("SELECT * FROM owners WHERE email = ?", (normalize_email(email),)).fetchone()
    if row is None:
        verify_password(password, hash_password(secrets.token_hex(8)))
        return None
    return _owner(row) if verify_password(password, row["password_hash"]) else None


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(connection: sqlite3.Connection, owner_id: uuid.UUID) -> str:
    """Start a session and return the token to hand back to the browser."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    opened = _now()
    connection.execute(
        "INSERT INTO sessions (token_hash, owner_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_digest(token), str(owner_id), opened.isoformat(), (opened + SESSION_LIFETIME).isoformat()),
    )
    return token


def close_session(connection: sqlite3.Connection, token: str) -> None:
    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))


def owner_for_session(connection: sqlite3.Connection, token: str) -> Owner | None:
    row = connection.execute(
        "SELECT owners.* FROM sessions"
        " JOIN owners ON owners.id = sessions.owner_id"
        " WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
        (token_digest(token), _now().isoformat()),
    ).fetchone()
    return _owner(row) if row else None


def drop_expired_sessions(connection: sqlite3.Connection) -> int:
    cursor = connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))
    return cursor.rowcount
