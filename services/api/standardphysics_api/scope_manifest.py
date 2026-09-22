"""The minimal scope-manifest producer for the pilot contract (K freeze, item 7b).

A lane hardens applicability and per-item logic behind G06/G13; this producer
only guarantees that every requested requirement leaves a visible row, that
unevaluated checks appear as unobserved, and that unknowns stay unknowns.
Outcomes are mapped from the findings the rule engine already produced:
passes -> satisfied, problem -> violation, question -> needs_verification.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from standardphysics_contracts import (
    Assessment,
    Check,
    Finding,
    Scenario,
    SceneGraph,
    ScopeItem,
    ScopeManifest,
    ScopeRow,
)

PILOT_TARGET_CLASSES = ["outlet", "television", "service_counter", "restroom_entrance"]

SCOPE_NAMESPACE = uuid.UUID("f2a61c3d-11a1-4c9b-9b2f-3c1b0000a000")

_FINDING_OUTCOME = {
    "passes": "satisfied",
    "problem": "violation",
    "question": "needs_verification",
}

APPLICABILITY_QUESTIONS = [
    "Site jurisdiction and applicable code edition",
    "Which areas are customer-facing versus staff-only",
    "Whether a public restroom exists and its availability to customers",
]

UNRESOLVED_DEFAULTS = [
    "No independent human inventory of target classes exists for this scan; absence of detections is not absence of objects.",
    "Surveyed and unobserved areas are not declared; area-level coverage questions stay open.",
]


def build_scope_manifest(
    graph: SceneGraph,
    scenario: Scenario,
    assessment: Assessment,
    checks: list[Check],
    waiting: dict[str, str],
    created_at: datetime | None = None,
) -> ScopeManifest:
    by_check: dict[str, list[Finding]] = {}
    for finding in assessment.findings:
        by_check.setdefault(finding.check_id, []).append(finding)

    rows: list[ScopeRow] = []
    for check in checks:
        findings = by_check.get(check.id, [])
        if not findings:
            rows.append(_unobserved_row(check, waiting.get(check.id)))
        else:
            for index, finding in enumerate(findings, start=1):
                rows.append(_finding_row(check, finding, index, len(findings)))
    known = {check.id for check in checks}
    for check_id, findings in by_check.items():
        if check_id in known:
            continue
        for index, finding in enumerate(findings, start=1):
            rows.append(_finding_row(_stand_in_check(check_id, finding), finding, index, len(findings)))

    requested_requirements = sorted({check.id for check in checks} | set(by_check))
    unresolved = [
        *UNRESOLVED_DEFAULTS,
        *(f"{rule_id}: {reason}" for rule_id, reason in sorted(waiting.items())),
    ]

    manifest_hash = _hash(
        graph_revision=assessment.graph_revision,
        graph_hash=assessment.graph_hash,
        rulepack_version=assessment.rulepack_version,
        requested_requirements=requested_requirements,
        requested_classes=PILOT_TARGET_CLASSES,
        route_endpoints=[stop.name for stop in scenario.stops],
        unresolved_questions=unresolved,
        rows=[
            [
                row.item.item_slug,
                row.requirement_id,
                row.item.label,
                row.outcome,
                row.reason,
                row.applicability,
                row.requested,
            ]
            for row in rows
        ],
    )

    return ScopeManifest(
        id=uuid.uuid5(
            SCOPE_NAMESPACE,
            f"{assessment.scan_id}|{assessment.graph_revision}|{assessment.rulepack_version}|{manifest_hash}",
        ),
        scan_id=assessment.scan_id,
        version=1,
        created_at=created_at or datetime.now(UTC),
        graph_revision=assessment.graph_revision,
        graph_hash=assessment.graph_hash,
        rulepack_version=assessment.rulepack_version,
        manifest_hash=manifest_hash,
        surveyed_areas=[],
        unobserved_areas=[],
        route_endpoints=[stop.name for stop in scenario.stops],
        requested_classes=PILOT_TARGET_CLASSES,
        requested_requirements=requested_requirements,
        applicability_questions=APPLICABILITY_QUESTIONS,
        unresolved_questions=unresolved,
        rows=rows,
    )


def _finding_row(check: Check, finding: Finding, index: int, total: int) -> ScopeRow:
    slug = f"site:{check.id}" if total == 1 else f"site:{check.id}:{index}"
    return ScopeRow(
        item=ScopeItem(
            item_slug=slug,
            item_kind="site",
            label=finding.title or check.title,
            observed=True,
            source="measured",
        ),
        requirement_id=check.id,
        requested=True,
        applicability="unknown",
        applicability_reason=(
            "applicability has not been established with evidence; the outcome below "
            "is a calculation only and never a verified legal conclusion"
        ),
        outcome=_FINDING_OUTCOME.get(finding.outcome, "needs_verification"),
        reason=finding.detail or finding.title,
        evidence_refs=[finding.citation.section] if finding.citation else [],
        source_version=check.citation.edition if check.citation else None,
    )


def _unobserved_row(check: Check, waiting_reason: str | None) -> ScopeRow:
    return ScopeRow(
        item=ScopeItem(
            item_slug=f"site:{check.id}",
            item_kind="site",
            label=check.title,
            observed=False,
            source="requested_not_observed",
        ),
        requirement_id=check.id,
        requested=True,
        applicability="unknown",
        outcome="unobserved",
        reason=waiting_reason or "the check produced no finding in this pass; the requirement stays unobserved until it is evaluated",
    )


def _stand_in_check(check_id: str, finding: Finding) -> Check:
    """A display-only Check for a finding whose check is not in the enabled set.

    It carries the finding's own citation, so nothing here fabricates a source.
    """
    return Check(
        id=check_id,
        title=finding.title or check_id,
        citation=finding.citation,
        tier=1,
        threshold=0.0,
        unit="in",
    )


def _hash(**fields) -> str:
    payload = json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
