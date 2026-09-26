"""Layouts the owner plans, kept apart from the shop as it was scanned.

Dragging furniture in Plan a layout and saving it used to make that layout the
shop's record, so the report described furniture that had never moved. A plan
is saved on its own with every check run against it, and the report keeps
describing the shop as scanned. Walking the shop again after the furniture
really moves is what changes the record.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Response
from standardphysics_contracts import Finding, LayoutCheckRequest, LayoutPlan, NodeMove, PlanList, SavePlanRequest

from .db import Database
from .errors import ApiProblem
from .layout import check_layout
from .stages import Stages


def _plan(row: sqlite3.Row) -> LayoutPlan:
    return LayoutPlan(
        id=uuid.UUID(row["id"]), scan_id=uuid.UUID(row["scan_id"]), base_revision=row["base_revision"],
        name=row["name"], created_at=datetime.fromisoformat(row["created_at"]),
        moves=[NodeMove.model_validate(move) for move in json.loads(row["moves_json"])],
        findings=[Finding.model_validate(finding) for finding in json.loads(row["findings_json"])],
    )


def plans_of(connection: sqlite3.Connection, scan_id: uuid.UUID) -> list[LayoutPlan]:
    rows = connection.execute(
        "SELECT * FROM layout_plans WHERE scan_id = ? ORDER BY created_at", (str(scan_id),)
    ).fetchall()
    return [_plan(row) for row in rows]


def save_plan(database: Database, stages: Stages, scan_id: uuid.UUID, body: SavePlanRequest) -> LayoutPlan:
    check = check_layout(database, stages, scan_id, LayoutCheckRequest(
        base_revision=body.base_revision, sequence=0, moves=body.moves,
    ))
    if check.blocked:
        need = [blocked.detail for blocked in check.blocked]
        raise ApiProblem(409, "that layout breaks a hard constraint", need=need)
    with database.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM layout_plans WHERE scan_id = ?", (str(scan_id),)).fetchone()[0]
        plan = LayoutPlan(
            id=uuid.uuid4(), scan_id=scan_id, base_revision=body.base_revision,
            name=body.name or f"Layout {count + 1}", moves=body.moves,
            created_at=datetime.now(UTC), findings=check.findings,
        )
        connection.execute(
            "INSERT INTO layout_plans (id, scan_id, base_revision, name, moves_json, findings_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(plan.id), str(scan_id), plan.base_revision, plan.name,
             json.dumps([move.model_dump(mode="json") for move in plan.moves]),
             json.dumps([finding.model_dump(mode="json") for finding in plan.findings]),
             plan.created_at.isoformat()),
        )
    return plan


def install_plan_routes(app: FastAPI, database: Database, stages: Stages) -> None:
    @app.post("/api/scans/{scan_id}/plans", status_code=201, response_model=LayoutPlan)
    def create(scan_id: uuid.UUID, body: SavePlanRequest) -> LayoutPlan:
        return save_plan(database, stages, scan_id, body)

    @app.get("/api/scans/{scan_id}/plans", response_model=PlanList)
    def listing(scan_id: uuid.UUID) -> PlanList:
        with database.connect() as connection:
            return PlanList(plans=plans_of(connection, scan_id))

    @app.delete("/api/scans/{scan_id}/plans/{plan_id}", status_code=204)
    def remove(scan_id: uuid.UUID, plan_id: uuid.UUID) -> Response:
        with database.transaction() as connection:
            connection.execute("DELETE FROM layout_plans WHERE scan_id = ? AND id = ?", (str(scan_id), str(plan_id)))
        return Response(status_code=204)
