"""Accounts the phone makes before anyone signs in, and the two ways to keep them.

The walk uploads under a guest account the phone makes on first launch, so the
owner is never stopped to sign in (docs/UX.md, first-run rules). During the
measuring wait they can keep it with Sign in with Apple or an email and a
password. Signing in to an account that already exists moves the guest's shops
into it.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, EmailStr, Field
from standardphysics_contracts import DeviceRegistration, Session

from . import accounts, notifications
from .accounts import Owner
from .apple_identity import AppleIdentity, AppleUnavailable, NotFromApple, verify
from .attempt_limiter import AttemptLimiter
from .auth import (
    SIGN_IN_THROTTLED,
    SIGN_IN_WINDOW_SECONDS,
    client_address,
    resolve_owner,
    save_guest,
    session_of,
    set_session_cookie,
    signed_in,
)
from .db import Database
from .errors import ApiProblem

log = logging.getLogger(__name__)

DEVICE_TOKEN = re.compile(r"^[0-9a-fA-F]{32,200}$")
GUESTS_PER_ADDRESS = 20
GUEST_WINDOW_SECONDS = 3600
APPLE_SIGN_INS_PER_ADDRESS = 30
"""Apple sign-in attempts one network may make in SIGN_IN_WINDOW_SECONDS, the same as email sign-ins.
Each one can cost a signature check, and an unknown key id can cost a request to Apple."""
APPLE_RETRY_SECONDS = 60


class SaveRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=accounts.MIN_PASSWORD_LENGTH, max_length=1024)
    shop_name: str | None = Field(default=None, min_length=1, max_length=120)


class AppleSignIn(BaseModel):
    identity_token: str = Field(min_length=1, max_length=8192)
    full_name: str | None = Field(default=None, max_length=120)


def _linked_by_email(connection: sqlite3.Connection, identity: AppleIdentity) -> Owner | None:
    """An account that already has this person's Apple email, which Apple has confirmed is theirs.

    Nothing confirmed the email when the account was made, so the Apple ID
    takes it over and the password and sessions it had are revoked.
    """
    if not identity.email or not identity.email_verified:
        return None
    owner = accounts.owner_by_email(connection, identity.email)
    if owner is None or owner.guest:
        return None
    revoked = accounts.claim_for_apple(connection, owner, identity.subject)
    log.warning(
        "account %s was claimed by the Apple ID with its verified email: its password, %d session(s) and "
        "%d push token(s) were revoked", owner.id, revoked.sessions, revoked.devices,
    )
    return owner


def revoke_passwords_left_on_apple_accounts(database: Database) -> None:
    with database.transaction() as connection:
        revoked = accounts.revoke_passwords_on_apple_accounts(connection)
    if revoked:
        log.warning("revoked the password on %d account(s) that Apple signs in to", revoked)


def _apple_owner(connection: sqlite3.Connection, identity: AppleIdentity, current: Owner | None) -> Owner:
    guest = current if current is not None and current.guest else None
    owner = accounts.owner_by_apple(connection, identity.subject) or _linked_by_email(connection, identity)
    if owner is None and guest is not None:
        return accounts.attach_apple(connection, guest, identity.subject, identity.email)
    if owner is None:
        return accounts.create_apple_owner(connection, identity.subject, identity.email, "My shop")
    if guest is not None and guest.id != owner.id:
        accounts.move_shops(connection, guest, owner)
    return owner


def _apple_identity(token: str, audiences: frozenset[str]) -> AppleIdentity:
    try:
        return verify(token, audiences)
    except NotFromApple:
        raise ApiProblem(401, "Sign in with Apple didn't go through. Try again.") from None
    except AppleUnavailable:
        raise ApiProblem(503, "Sign in with Apple isn't reachable right now. Try again in a minute.",
                         headers={"Retry-After": str(APPLE_RETRY_SECONDS)}) from None


def install_account_routes(app: FastAPI, database: Database, apple_audiences: frozenset[str]) -> None:
    guests = AttemptLimiter(limit=GUESTS_PER_ADDRESS, window=GUEST_WINDOW_SECONDS,
                            message="Too many new accounts from this network. Try again in an hour.")
    apple_attempts = AttemptLimiter(limit=APPLE_SIGN_INS_PER_ADDRESS, window=SIGN_IN_WINDOW_SECONDS,
                                    message=SIGN_IN_THROTTLED)

    @app.post("/api/auth/guest", status_code=201, response_model=Session)
    def guest(request: Request, response: Response) -> Session:
        """A new guest, or whoever is already signed in on this phone. A handful an hour per network."""
        current = resolve_owner(database, request)
        if current is not None:
            return session_of(database, current)
        guests.admit(client_address(request))
        with database.transaction() as connection:
            owner = accounts.create_guest(connection)
            token = accounts.open_session(connection, owner.id, accounts.GUEST_SESSION_LIFETIME)
        set_session_cookie(response, request, token, accounts.GUEST_SESSION_LIFETIME)
        return session_of(database, owner)

    @app.post("/api/auth/save", response_model=Session)
    def save(body: SaveRequest, request: Request) -> Session:
        owner = save_guest(database, signed_in(database, request), body.email, body.password, body.shop_name)
        return session_of(database, owner)

    _install_device_routes(app, database)

    @app.post("/api/auth/apple", response_model=Session)
    def apple(body: AppleSignIn, request: Request, response: Response) -> Session:
        apple_attempts.admit(client_address(request))
        identity = _apple_identity(body.identity_token, apple_audiences)
        current = resolve_owner(database, request)
        with database.transaction() as connection:
            owner = _apple_owner(connection, identity, current)
            token = accounts.open_session(connection, owner.id)
        set_session_cookie(response, request, token)
        return session_of(database, owner)


def _install_device_routes(app: FastAPI, database: Database) -> None:
    @app.put("/api/devices/{token}", status_code=204)
    def add_device(token: str, body: DeviceRegistration, request: Request) -> Response:
        """The phone's push token, so results and reminders reach it."""
        owner = signed_in(database, request)
        if not DEVICE_TOKEN.fullmatch(token):
            raise ApiProblem(400, "That isn't a device token.")
        with database.transaction() as connection:
            notifications.register(connection, owner.id, token, body.environment)
        return Response(status_code=204)

    @app.delete("/api/devices/{token}", status_code=204)
    def remove_device(token: str, request: Request) -> Response:
        owner = signed_in(database, request)
        with database.transaction() as connection:
            notifications.forget(connection, token, owner.id)
        return Response(status_code=204)
