"""The owner's list of things to fix, and what they've done about each one.

An item is a problem finding. Its status is the owner's own record: marking it
done doesn't change the finding, because the finding describes the shop as it
was scanned. A finding's id stays the same for a scan across passes, so a status
survives the checks running again.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from standardphysics_contracts import Assessment, Checklist, ChecklistItem
from standardphysics_contracts.owner import ChecklistStatus

from .errors import ApiProblem


def problems_of(assessment: Assessment | None) -> list:
    return [finding for finding in assessment.findings if finding.outcome == "problem"] if assessment else []


def checklist(connection: sqlite3.Connection, scan_id: uuid.UUID, assessment: Assessment | None) -> Checklist:
    rows = connection.execute(
        "SELECT finding_id, status, updated_at FROM checklist_items WHERE scan_id = ?", (str(scan_id),)
    ).fetchall()
    marked = {row["finding_id"]: row for row in rows}
    items = [_item(finding.id, marked.get(str(finding.id))) for finding in problems_of(assessment)]
    done = sum(1 for item in items if item.status != "to_do")
    return Checklist(items=items, done=done, total=len(items))


def _item(finding_id: uuid.UUID, row: sqlite3.Row | None) -> ChecklistItem:
    if row is None:
        return ChecklistItem(finding_id=finding_id)
    updated = datetime.fromisoformat(row["updated_at"])
    return ChecklistItem(finding_id=finding_id, status=row["status"], updated_at=updated)


def mark(
    connection: sqlite3.Connection, scan_id: uuid.UUID, assessment: Assessment | None,
    finding_id: uuid.UUID, status: ChecklistStatus,
) -> ChecklistItem:
    if finding_id not in {finding.id for finding in problems_of(assessment)}:
        raise ApiProblem(404, "That isn't on your list.")
    now = datetime.now(UTC)
    connection.execute(
        "INSERT INTO checklist_items (scan_id, finding_id, status, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (scan_id, finding_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
        (str(scan_id), str(finding_id), status, now.isoformat()),
    )
    return ChecklistItem(finding_id=finding_id, status=status, updated_at=now)
