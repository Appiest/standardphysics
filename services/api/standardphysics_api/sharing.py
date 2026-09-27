"""Read-only report links, for a contractor, a landlord or an inspector.

The owner makes a link from the report, and anyone who has it can read that
report until it expires 30 days later, with no account. The token is kept only
as its digest, the same way session tokens are, so a copy of the database
opens no report. The literal token "example" opens the sample shop, which is
what "See an example shop" shows.
"""

from __future__ import annotations

import secrets
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from standardphysics_contracts import Report, ShareLink
from standardphysics_fixtures import build_lawsuit_graph

from . import repository as repo
from .accounts import token_digest
from .db import Database
from .errors import ApiProblem

SHARE_LIFETIME = timedelta(days=30)
EXAMPLE = "example"
EXPIRED = "This link has expired. Ask the shop for a new one."


def create_link(connection: sqlite3.Connection, scan_id: uuid.UUID) -> ShareLink:
    token = secrets.token_urlsafe(24)
    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO share_links (token_hash, scan_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_digest(token), str(scan_id), now.isoformat(), (now + SHARE_LIFETIME).isoformat()),
    )
    return ShareLink(path=f"/r/{token}", expires_at=now + SHARE_LIFETIME)


def revoke_links(connection: sqlite3.Connection, scan_id: uuid.UUID) -> None:
    connection.execute("DELETE FROM share_links WHERE scan_id = ?", (str(scan_id),))


def shared_scan(connection: sqlite3.Connection, token: str) -> uuid.UUID:
    if token == EXAMPLE:
        sample = build_lawsuit_graph().scan_id
        if not repo.scan_exists(connection, sample):
            raise ApiProblem(404, "The example shop isn't here right now.")
        return sample
    row = connection.execute(
        "SELECT scan_id FROM share_links WHERE token_hash = ? AND expires_at > ?",
        (token_digest(token), datetime.now(UTC).isoformat()),
    ).fetchone()
    if row is None:
        raise ApiProblem(404, EXPIRED)
    return uuid.UUID(row["scan_id"])


def for_sharing(report: Report, token: str) -> Report:
    """The report as a link shows it: pictures at the link's own address, and no team-only scope."""
    if report.assessment is None:
        return report
    findings = [
        finding.model_copy(update={"locus": finding.locus.model_copy(
            update={"render_url": f"/api/shared/{token}/renders/{finding.id}.png"}
        )}) if finding.locus and finding.locus.render_url else finding
        for finding in report.assessment.findings
    ]
    assessment = report.assessment.model_copy(update={"findings": findings, "scope": None})
    return report.model_copy(update={"assessment": assessment})


def install_share_routes(
    app: FastAPI,
    database: Database,
    report_of: Callable[[uuid.UUID], Report],
    glb_of: Callable[[uuid.UUID], Response],
    render_of: Callable[[uuid.UUID, uuid.UUID], FileResponse],
) -> None:
    def scan_behind(token: str) -> uuid.UUID:
        with database.connect() as connection:
            return shared_scan(connection, token)

    @app.post("/api/scans/{scan_id}/shares", status_code=201, response_model=ShareLink)
    def share(scan_id: uuid.UUID) -> ShareLink:
        with database.transaction() as connection:
            if not repo.scan_exists(connection, scan_id):
                raise ApiProblem(404, "no scan")
            return create_link(connection, scan_id)

    @app.delete("/api/scans/{scan_id}/shares", status_code=204)
    def stop_sharing(scan_id: uuid.UUID) -> Response:
        with database.transaction() as connection:
            revoke_links(connection, scan_id)
        return Response(status_code=204)

    @app.get("/api/shared/{token}", response_model=Report)
    def shared_report(token: str) -> Report:
        return for_sharing(report_of(scan_behind(token)), token)

    @app.head("/api/shared/{token}/scene.glb")
    @app.get("/api/shared/{token}/scene.glb")
    def shared_glb(token: str) -> Response:
        return glb_of(scan_behind(token))

    @app.get("/api/shared/{token}/renders/{finding_id}.png")
    def shared_render(token: str, finding_id: uuid.UUID) -> FileResponse:
        return render_of(scan_behind(token), finding_id)
