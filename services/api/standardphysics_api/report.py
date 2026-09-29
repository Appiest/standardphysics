"""The printed report: the scan, its latest assessment, and who verified each rule."""

from __future__ import annotations

import uuid

from standardphysics_agents import VerificationLedger, load_pack
from standardphysics_contracts import Report, ReviewedRule

from . import repository as repo
from . import repository_revisions as revisions_repo
from .db import Database
from .errors import ApiProblem
from .stages import PREVIEW_REVIEWER


def reviewed_rules(ledger: VerificationLedger) -> list[ReviewedRule]:
    reviewed = []
    for rule in load_pack().rules:
        entry = ledger.entry_for(rule)
        if entry is None or not ledger.verifies(rule):
            continue
        check = rule.as_check().model_copy(update={"verified_by_human": entry.verified_by != PREVIEW_REVIEWER})
        reviewed.append(ReviewedRule(
            check=check, verified_by=entry.verified_by, verified_at=entry.verified_at,
            second_check_by=entry.second_check_by,
        ))
    return reviewed


def build_report(database: Database, ledger: VerificationLedger, scan_id: uuid.UUID) -> Report:
    with database.connect() as connection:
        scan = repo.get_scan(connection, scan_id)
        if scan is None:
            raise ApiProblem(404, "no scan")
        revision = revisions_repo.get_revision(connection, scan_id)
        scenario = revisions_repo.get_scenario(connection, scan_id)
        assessment = revisions_repo.latest_assessment(connection, scan_id)
    return Report(
        scan=scan,
        scene=revisions_repo.graph_of(revision) if revision else None,
        scenario=scenario,
        assessment=assessment,
        rules=reviewed_rules(ledger),
        preview=any(entry.verified_by == PREVIEW_REVIEWER for entry in ledger.entries),
    )
