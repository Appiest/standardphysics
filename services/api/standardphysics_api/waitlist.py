"""Public beta signups and a private list of addresses for TestFlight invites."""

from __future__ import annotations

import csv
import hmac
import io
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, Header, Response
from pydantic import BaseModel, EmailStr

from .accounts import normalize_email
from .db import Database
from .errors import ApiProblem
from .settings import Settings


class WaitlistSignup(BaseModel):
    email: EmailStr
    role: Literal["student", "shop_owner"]


def _csv_export(database: Database) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(("email", "role", "joined_at"))
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT email, role, created_at FROM beta_waitlist ORDER BY created_at, email"
        ).fetchall()
    writer.writerows((row["email"], row["role"], row["created_at"]) for row in rows)
    return output.getvalue()


def install_waitlist_routes(app: FastAPI, database: Database, settings: Settings) -> None:
    @app.post("/api/waitlist", status_code=202)
    def join_waitlist(body: WaitlistSignup) -> Response:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO beta_waitlist (email, role, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(email) DO UPDATE SET role = excluded.role",
                (normalize_email(body.email), body.role, datetime.now(UTC).isoformat()),
            )
        return Response(status_code=202)

    @app.get("/api/waitlist.csv", include_in_schema=False)
    def export_waitlist(authorization: str | None = Header(default=None)) -> Response:
        token = settings.waitlist_admin_token
        if not token:
            raise ApiProblem(404, "not found")
        if not authorization or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise ApiProblem(401, "admin token required")
        return Response(
            content=_csv_export(database),
            media_type="text/csv",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="standardphysics-waitlist.csv"',
            },
        )
