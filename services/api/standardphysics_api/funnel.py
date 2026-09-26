"""How far owners get, from the first walk to the first fix (docs/UX.md, phase 6).

Everything here is read from what the server already keeps: when a scan was
made and finished uploading, the in-shop answers, when results were first
ready, the path, the checklist and the share links. Nothing extra is recorded
about the owner, and only the team can read it.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime

from standardphysics_contracts import Funnel, FunnelStep

STEPS: tuple[tuple[str, str, str], ...] = (
    ("walked", "Walked a shop", "SELECT id FROM scans"),
    ("uploaded", "Finished the upload", "SELECT id FROM scans WHERE state != 'uploading'"),
    ("answered", "Answered the quick questions",
     "SELECT scan_id FROM owner_requests WHERE request_id IN ('restroom', 'inside_doors') AND status = 'checked'"
     " GROUP BY scan_id HAVING COUNT(*) = 2"),
    ("results", "Saw results", "SELECT id FROM scans WHERE results_told_at IS NOT NULL"),
    ("path", "Checked the customer path", "SELECT scan_id FROM scenarios"),
    ("fixing", "Started the checklist", "SELECT DISTINCT scan_id FROM checklist_items"),
    ("fixed", "Fixed something", "SELECT DISTINCT scan_id FROM checklist_items WHERE status = 'done'"),
    ("shared", "Shared the report", "SELECT DISTINCT scan_id FROM share_links"),
)


def _minutes_between(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def funnel(connection: sqlite3.Connection) -> Funnel:
    steps = [
        FunnelStep(key=key, label=label, shops=len(connection.execute(query).fetchall()))
        for key, label, query in STEPS
    ]
    to_results = [
        _minutes_between(row["created_at"], row["results_told_at"])
        for row in connection.execute("SELECT created_at, results_told_at FROM scans WHERE results_told_at IS NOT NULL")
    ]
    to_fix = [
        _minutes_between(row["results_told_at"], row["first_done"]) / 60
        for row in connection.execute(
            "SELECT scans.results_told_at, MIN(checklist_items.updated_at) AS first_done FROM scans"
            " JOIN checklist_items ON checklist_items.scan_id = scans.id AND checklist_items.status = 'done'"
            " WHERE scans.results_told_at IS NOT NULL GROUP BY scans.id"
        )
    ]
    return Funnel(steps=steps, median_minutes_to_results=_median(to_results), median_hours_to_first_fix=_median(to_fix))
