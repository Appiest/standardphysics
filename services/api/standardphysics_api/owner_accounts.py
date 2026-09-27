"""Accounts the phone makes before anyone signs in, and the two ways to keep them.

The walk uploads under a guest account the phone makes on first launch, so the
owner is never stopped to sign in (docs/UX.md, first-run rules). During the
measuring wait they can keep it with Sign in with Apple or an email and a
password. Signing in to an account that already exists moves the guest's shops
into it.
"""

from __future__ import annotations

import re
import sqlite3

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, EmailStr, Field
from standardphysics_contracts import DeviceRegistration, Session

from . import accounts, notifications
from .accounts import Owner
from .apple_identity import AppleIdentity, NotFromApple, verify
from .auth import AttemptLimiter, resolve_owner, save_guest, session_of, set_session_cookie, signed_in
from .db import Database
from .errors import ApiProblem

DEVICE_TOKEN = re.compile(r"^[0-9a-fA-F]{32,200}$")
GUESTS_PER_ADDRESS = 20
GUEST_WINDOW_SECONDS = 3600


class SaveRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=accounts.MIN_PASSWORD_LENGTH, max_length=1024)
    shop_name: str | None = Field(default=None, min_length=1, max_length=120)


class AppleSignIn(BaseModel):
    identity_token: str = Field(min_length=1, max_length=8192)
    full_name: str | None = Field(default=None, max_length=120)


def _linked_by_email(connection: sqlite3.Connection, identity: AppleIdentity) -> Owner | None:
    """An account that already has this person's Apple email, which Apple has confirmed is theirs."""
    if not identity.email or not identity.email_verified:
        return None
    owner = accounts.owner_by_email(connection, identity.email)
    if owner is None or owner.guest:
        return None
    return accounts.attach_apple(connection, owner, identity.subject, None)


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


def install_account_routes(
    app: FastAPI, database: Database, team_emails: frozenset[str], apple_audiences: frozenset[str]
) -> None:
    guests = AttemptLimiter(limit=GUESTS_PER_ADDRESS, window=GUEST_WINDOW_SECONDS,
                            message="Too many new accounts from this network. Try again in an hour.")

    @app.post("/api/auth/guest", status_code=201, response_model=Session)
    def guest(request: Request, response: Response) -> Session:
        """A new guest, or whoever is already signed in on this phone. A handful an hour per network."""
        current = resolve_owner(database, request)
        if current is not None:
            return session_of(database, current, team_emails)
        address = request.client.host if request.client else "unknown"
        guests.check(address)
        guests.record(address)
        with database.transaction() as connection:
            owner = accounts.create_guest(connection)
            token = accounts.open_session(connection, owner.id, accounts.GUEST_SESSION_LIFETIME)
        set_session_cookie(response, request, token, accounts.GUEST_SESSION_LIFETIME)
        return session_of(database, owner, team_emails)

    @app.post("/api/auth/save", response_model=Session)
    def save(body: SaveRequest, request: Request) -> Session:
        owner = save_guest(database, signed_in(database, request), body.email, body.password, body.shop_name)
        return session_of(database, owner, team_emails)

    _install_device_routes(app, database)

    @app.post("/api/auth/apple", response_model=Session)
    def apple(body: AppleSignIn, request: Request, response: Response) -> Session:
        try:
            identity = verify(body.identity_token, apple_audiences)
        except NotFromApple:
            raise ApiProblem(400, "Sign in with Apple didn't go through. Try again.") from None
        current = resolve_owner(database, request)
        with database.transaction() as connection:
            owner = _apple_owner(connection, identity, current)
            token = accounts.open_session(connection, owner.id)
        set_session_cookie(response, request, token)
        return session_of(database, owner, team_emails)


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
