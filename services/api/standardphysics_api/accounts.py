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
GUEST_SESSION_LIFETIME = timedelta(days=365)
"""A guest has no password to sign in again with, so its session has to outlast
the 30 days after which an unopened guest shop is deleted anyway."""

GUEST_SHOPS_KEPT = timedelta(days=30)

GUEST_EMAIL_DOMAIN = "guests.standardphysics.app"
"""A guest has no email yet, but the owners table needs a unique one. This
domain is ours and receives no mail, and no screen ever shows the address."""

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


class NotAGuest(Exception):
    pass


@dataclass(frozen=True)
class Owner:
    id: uuid.UUID
    email: str
    shop_name: str
    guest: bool = False

    @property
    def shown_email(self) -> str | None:
        return None if self.email.endswith(f"@{GUEST_EMAIL_DOMAIN}") else self.email


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


def owner_from_row(row: sqlite3.Row) -> Owner:
    return _owner(row)


def _owner(row: sqlite3.Row) -> Owner:
    return Owner(id=uuid.UUID(row["id"]), email=row["email"], shop_name=row["shop_name"], guest=bool(row["guest"]))


def _owner_where(connection: sqlite3.Connection, column: str, value: str) -> Owner | None:
    row = connection.execute(f"SELECT * FROM owners WHERE {column} = ?", (value,)).fetchone()
    return _owner(row) if row else None


def create_guest(connection: sqlite3.Connection, shop_name: str = "My shop") -> Owner:
    """An account for an owner who hasn't given an email yet. Nobody can sign in to it with a password."""
    owner_id = uuid.uuid4()
    email = f"guest-{owner_id}@{GUEST_EMAIL_DOMAIN}"
    connection.execute(
        "INSERT INTO owners (id, email, shop_name, password_hash, created_at, guest) VALUES (?, ?, ?, ?, ?, 1)",
        (str(owner_id), email, shop_name, hash_password(secrets.token_urlsafe(32)), _now().isoformat()),
    )
    return Owner(id=owner_id, email=email, shop_name=shop_name, guest=True)


def save_guest(
    connection: sqlite3.Connection, owner: Owner, email: str, password: str, shop_name: str | None = None
) -> Owner:
    """Give a guest an email and a password, so it becomes an account like any other."""
    if not owner.guest:
        raise NotAGuest
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword
    address = normalize_email(email)
    name = (shop_name or owner.shop_name).strip()
    try:
        connection.execute(
            "UPDATE owners SET email = ?, password_hash = ?, shop_name = ?, guest = 0 WHERE id = ?",
            (address, hash_password(password), name, str(owner.id)),
        )
    except sqlite3.IntegrityError as exc:
        raise EmailAlreadyRegistered from exc
    return Owner(id=owner.id, email=address, shop_name=name)


def owner_by_email(connection: sqlite3.Connection, email: str) -> Owner | None:
    return _owner_where(connection, "email", normalize_email(email))


def owner_by_apple(connection: sqlite3.Connection, subject: str) -> Owner | None:
    return _owner_where(connection, "apple_sub", subject)


def attach_apple(connection: sqlite3.Connection, owner: Owner, subject: str, email: str | None) -> Owner:
    """Link an Apple ID to this account. A guest takes the Apple email, when it has one and nobody else does."""
    address = normalize_email(email) if email and owner.guest and owner_by_email(connection, email) is None else None
    connection.execute(
        "UPDATE owners SET apple_sub = ?, guest = 0, email = COALESCE(?, email) WHERE id = ?",
        (subject, address, str(owner.id)),
    )
    return Owner(id=owner.id, email=address or owner.email, shop_name=owner.shop_name)


def create_apple_owner(connection: sqlite3.Connection, subject: str, email: str | None, shop_name: str) -> Owner:
    guest = create_guest(connection, shop_name)
    return attach_apple(connection, guest, subject, email)


def move_shops(connection: sqlite3.Connection, source: Owner, destination: Owner) -> None:
    """Hand every shop from one account to another, then close the old account."""
    connection.execute("UPDATE scans SET owner_id = ? WHERE owner_id = ?", (str(destination.id), str(source.id)))
    connection.execute("UPDATE devices SET owner_id = ? WHERE owner_id = ?", (str(destination.id), str(source.id)))
    delete_owner(connection, source.id)


def guest_deletes_at(connection: sqlite3.Connection, owner: Owner) -> datetime | None:
    """When a guest's shops go, 30 days after the most recent was last opened."""
    if not owner.guest:
        return None
    row = connection.execute(
        "SELECT MAX(COALESCE(scans.last_opened_at, scans.created_at)) AS opened FROM scans WHERE owner_id = ?",
        (str(owner.id),),
    ).fetchone()
    created = connection.execute("SELECT created_at FROM owners WHERE id = ?", (str(owner.id),)).fetchone()
    latest = row["opened"] or created["created_at"]
    return datetime.fromisoformat(latest) + GUEST_SHOPS_KEPT


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


def open_session(
    connection: sqlite3.Connection, owner_id: uuid.UUID, lifetime: timedelta = SESSION_LIFETIME
) -> str:
    """Start a session and return the token to hand back to the browser."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    opened = _now()
    connection.execute(
        "INSERT INTO sessions (token_hash, owner_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_digest(token), str(owner_id), opened.isoformat(), (opened + lifetime).isoformat()),
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


def delete_owner(connection: sqlite3.Connection, owner_id: uuid.UUID) -> None:
    """Remove the account itself, after its scans have already gone.

    The sessions go first so that a token cannot outlive the row it points at,
    even for the moment between the two statements.
    """
    connection.execute("DELETE FROM sessions WHERE owner_id = ?", (str(owner_id),))
    connection.execute("DELETE FROM devices WHERE owner_id = ?", (str(owner_id),))
    connection.execute("DELETE FROM owners WHERE id = ?", (str(owner_id),))


def drop_expired_sessions(connection: sqlite3.Connection) -> int:
    cursor = connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))
    return cursor.rowcount
