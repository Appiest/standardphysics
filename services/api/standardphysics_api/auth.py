"""Who is asking, and may they see this shop.

Every route in this service lives under `/api/scans`, so ownership is settled
in one middleware rather than in each handler. A route added later is covered
the moment it is registered, which is the opposite of the usual arrangement
where a forgotten decorator quietly publishes someone's floor plan.

The browser sends a session cookie. The phone sends the same token as a bearer
header, because a native upload has no cookie jar worth keeping.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from starlette.middleware.base import BaseHTTPMiddleware

from . import accounts
from . import repository as repo
from .accounts import EmailAlreadyRegistered, Owner, WeakPassword
from .db import Database
from .errors import ApiProblem
from .store import ArtifactStore

COOKIE_NAME = "sp_session"
GUARDED_PREFIX = "/api/scans"
BEARER = re.compile(r"^Bearer\s+(?P<token>[A-Za-z0-9_\-]+)$")
SCAN_IN_PATH = re.compile(r"^/api/scans/(?P<scan_id>[0-9a-fA-F-]{36})(?:/|$)")

SIGN_IN_ATTEMPTS = 10
SIGN_IN_WINDOW_SECONDS = 300


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=accounts.MIN_PASSWORD_LENGTH, max_length=1024)
    shop_name: str = Field(min_length=1, max_length=120)


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class Session(BaseModel):
    """What the browser is told about the signed-in owner. Never the token."""

    owner_id: uuid.UUID
    email: str
    shop_name: str


@dataclass
class AttemptLimiter:
    """Slows password guessing within this process.

    One API process owns one database (see `worker`), so a per-process counter
    covers the whole deployment. It is memory only: a restart forgives.
    """

    limit: int = SIGN_IN_ATTEMPTS
    window: int = SIGN_IN_WINDOW_SECONDS
    attempts: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str) -> None:
        now = time.monotonic()
        recent = [at for at in self.attempts.get(key, []) if now - at < self.window]
        self.attempts[key] = recent
        if len(recent) >= self.limit:
            raise ApiProblem(429, "too many sign-in attempts, wait a few minutes")

    def record(self, key: str) -> None:
        self.attempts.setdefault(key, []).append(time.monotonic())

    def forget(self, key: str) -> None:
        self.attempts.pop(key, None)


def token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    match = BEARER.match(header)
    if match:
        return match.group("token")
    return request.cookies.get(COOKIE_NAME)


def owner_of(request: Request) -> Owner:
    """The signed-in owner. Only call this from a route the middleware guards."""
    owner = getattr(request.state, "owner", None)
    if owner is None:
        raise ApiProblem(401, "sign in to continue")
    return owner


def _scan_id_in(path: str) -> uuid.UUID | None:
    match = SCAN_IN_PATH.match(path)
    return uuid.UUID(match.group("scan_id")) if match else None


def _resolve_owner(database: Database, request: Request) -> Owner | None:
    token = token_from(request)
    if token is None:
        return None
    with database.connect() as connection:
        return accounts.owner_for_session(connection, token)


def _owns_scan(database: Database, scan_id: uuid.UUID, owner: Owner) -> bool:
    """True when this owner may act on the scan, or when no scan is there yet.

    A scan that does not exist is left alone so the handler can answer 404 with
    its own wording. A scan owned by someone else is treated the same way: the
    caller learns nothing about a shop that is not theirs.
    """
    with database.connect() as connection:
        holder = repo.scan_owner(connection, scan_id)
        if holder is not None:
            return holder == owner.id
        return not repo.scan_exists(connection, scan_id)


def install_auth(app: FastAPI, database: Database, store: ArtifactStore) -> None:
    limiter = AttemptLimiter()

    class RequireOwner(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if not request.url.path.startswith(GUARDED_PREFIX):
                return await call_next(request)
            owner = _resolve_owner(database, request)
            if owner is None:
                return _problem(401, "sign in to continue")
            scan_id = _scan_id_in(request.url.path)
            if scan_id is not None and not _owns_scan(database, scan_id, owner):
                return _problem(404, "no scan")
            request.state.owner = owner
            return await call_next(request)

    app.add_middleware(RequireOwner)
    _install_auth_routes(app, database, store, limiter)


def _problem(status: int, message: str) -> JSONResponse:
    return JSONResponse(ApiProblem(status, message).body.model_dump(exclude_none=True), status_code=status)


def _session_of(owner: Owner) -> Session:
    return Session(owner_id=owner.id, email=owner.email, shop_name=owner.shop_name)


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(accounts.SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def _open(database: Database, owner: Owner) -> tuple[Owner, str]:
    with database.transaction() as connection:
        return owner, accounts.open_session(connection, owner.id)


def _register(database: Database, body: SignUpRequest) -> Owner:
    with database.transaction() as connection:
        try:
            return accounts.register(connection, body.email, body.password, body.shop_name)
        except EmailAlreadyRegistered:
            raise ApiProblem(409, "that email already has an account") from None
        except WeakPassword:
            raise ApiProblem(400, f"use at least {accounts.MIN_PASSWORD_LENGTH} characters") from None


def _authenticate(database: Database, body: SignInRequest, limiter: AttemptLimiter) -> Owner:
    key = accounts.normalize_email(body.email)
    limiter.check(key)
    with database.connect() as connection:
        owner = accounts.authenticate(connection, body.email, body.password)
    if owner is None:
        limiter.record(key)
        raise ApiProblem(401, "that email and password do not match")
    limiter.forget(key)
    return owner


def _erase_owner(database: Database, owner: Owner) -> list[uuid.UUID]:
    """Drop every row belonging to this owner, and say which scans to unfile.

    One transaction, so a half-deleted account cannot be signed into. The files
    are left to the caller for the reason `delete_scan` gives: a stored file
    with no row is invisible and reclaimable, while a row whose files have gone
    is a listing that breaks the moment anyone opens it.

    A running job is not waited for. Deleting a scan refuses while one is in
    flight, but an account has to be deletable whatever the worker is doing, so
    the job row goes with everything else and any bytes it writes afterwards
    land in a directory nothing points at.
    """
    with database.transaction() as connection:
        scan_ids = [scan.id for scan in repo.list_scans(connection, owner.id)]
        for scan_id in scan_ids:
            repo.delete_scan(connection, scan_id)
        accounts.delete_owner(connection, owner.id)
    return scan_ids


def _install_auth_routes(
    app: FastAPI, database: Database, store: ArtifactStore, limiter: AttemptLimiter
) -> None:
    @app.post("/api/auth/sign-up", status_code=201, response_model=Session)
    def sign_up(body: SignUpRequest, request: Request, response: Response) -> Session:
        owner, token = _open(database, _register(database, body))
        _set_cookie(response, request, token)
        return _session_of(owner)

    @app.post("/api/auth/sign-in", response_model=Session)
    def sign_in(body: SignInRequest, request: Request, response: Response) -> Session:
        owner, token = _open(database, _authenticate(database, body, limiter))
        _set_cookie(response, request, token)
        return _session_of(owner)

    @app.post("/api/auth/sign-out", status_code=204)
    def sign_out(request: Request) -> Response:
        token = token_from(request)
        if token:
            with database.transaction() as connection:
                accounts.close_session(connection, token)
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.delete("/api/account", status_code=204)
    def delete_account(request: Request) -> Response:
        """Erase the account, its shops and everything measured in them.

        Apple requires an owner who can create an account in the app to be able
        to end it there too, and an owner who walked us around their shop is
        owed the same. There is no undo and no grace period: the scans are gone
        when this returns.
        """
        owner = _resolve_owner(database, request)
        if owner is None:
            raise ApiProblem(401, "sign in to continue")
        for scan_id in _erase_owner(database, owner):
            store.remove_scan(scan_id)
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/auth/session", response_model=Session)
    def current_session(request: Request) -> Session:
        owner = _resolve_owner(database, request)
        if owner is None:
            raise ApiProblem(401, "sign in to continue")
        return _session_of(owner)
