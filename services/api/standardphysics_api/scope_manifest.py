"""The scope-manifest producer: one visible outcome per requested pair.

K froze the contract (item 7b) and this minimal producer; Lane A hardens the
semantics behind G06/G13. The invariants the manifest now holds:

- every requirement a check answered leaves a row, linked to the measured item
  it was read against when the finding names one;
- every enabled requirement that produced no finding leaves an unobserved row
  with the next capture action, never a disappeared obligation;
- every unevaluated rule (waiting on a reader or on a check implementation)
  leaves a needs_verification row with the waiting reason;
- every requested target class leaves a coverage row, so a class with no
  detections stays visible as unobserved rather than "none in the room";
- outcomes come from the rule engine's findings, while applicability and
  legal review travel separately: no row becomes not_applicable on its own,
  and a reviewed row can only say so because a review was supplied;
- a measurement on a row records its bounds explicitly; when a producer has
  no bounds the row says so instead of pretending the estimate is exact.

Rows are ordered deterministically (pack order first) so the manifest hash
means the same thing twice.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Mapping

from standardphysics_contracts import (
    Assessment,
    Check,
    Finding,
    LegalReviewStatus,
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
    "Service counter: which side is the customer side, and what accessible section exists",
    "Service counter: which approach applies (parallel or forward side approach)",
    "Doorway and route: which door and route serve customers",
]

UNRESOLVED_DEFAULTS = [
    "No independent human inventory of target classes exists for this scan; absence of detections is not absence of objects.",
    "Surveyed and unobserved areas are not declared; area-level coverage questions stay open.",
    "Restroom fixture, door, maneuvering, surface and operation checks are not implemented yet; until they are, every restroom question stays a question.",
]

_ITEM_KINDS_FOR_NODES = {
    "door": "route",
    "opening": "route",
}


def build_scope_manifest(
    graph: SceneGraph,
    scenario: Scenario,
    assessment: Assessment,
    checks: list[Check],
    waiting: dict[str, str],
    created_at: datetime | None = None,
    *,
    reviews: Mapping[str, LegalReviewStatus] | None = None,
) -> ScopeManifest:
    supplied_reviews: dict[str, LegalReviewStatus] = {}
    for requirement_id, status in (reviews or {}).items():
        if status not in ("unreviewed_preview", "needs_review", "reviewer_supplied"):
            raise ValueError(f"unknown legal review status for {requirement_id}: {status}")
        supplied_reviews[requirement_id] = status

    by_check: dict[str, list[Finding]] = {}
    for finding in assessment.findings:
        by_check.setdefault(finding.check_id, []).append(finding)

    rows: list[ScopeRow] = []
    known = {check.id for check in checks}
    for check in checks:
        findings = by_check.get(check.id, [])
        if not findings:
            rows.append(_unobserved_row(check, waiting.get(check.id)))
        else:
            rows.extend(_finding_rows(check, findings, graph))
    for check_id in sorted(set(by_check) - known):
        for finding in by_check[check_id]:
            rows.extend(
                _finding_rows(_stand_in_check(check_id, finding), [finding], graph)
            )
    rowed = {row.requirement_id for row in rows}
    for rule_id in sorted(set(waiting) - rowed):
        rows.append(_waiting_row(rule_id, waiting[rule_id]))

    class_rows = _class_rows(graph)
    rows.extend(class_rows)
    requested_requirements = sorted(
        {check.id for check in checks}
        | set(by_check)
        | set(waiting)
        | {f"coverage:{name}" for name in PILOT_TARGET_CLASSES}
    )
    unresolved = [
        *UNRESOLVED_DEFAULTS,
        *(f"{rule_id}: {reason}" for rule_id, reason in sorted(waiting.items())),
    ]

    _apply_reviews(rows, supplied_reviews)

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
                row.legal_review_status,
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


def _apply_reviews(
    rows: list[ScopeRow], reviews: Mapping[str, LegalReviewStatus]
) -> None:
    """Legal review state travels separately from the calculation outcome.

    A row whose calculation says needs_verification still reads needs_review
    while the requirement's legal logic is unreviewed; only an explicitly
    supplied review can say reviewer_supplied. Nothing here invents a reviewer.
    """
    for row in rows:
        supplied = reviews.get(row.requirement_id)
        if supplied is not None:
            row.legal_review_status = supplied
        elif row.outcome == "needs_verification":
            row.legal_review_status = "needs_review"
        else:
            row.legal_review_status = "unreviewed_preview"


def _finding_rows(check: Check, findings: list[Finding], graph: SceneGraph) -> list[ScopeRow]:
    rows = []
    for index, finding in enumerate(findings, start=1):
        rows.append(_finding_row(check, finding, index, len(findings), graph))
    return rows


def _finding_row(
    check: Check, finding: Finding, index: int, total: int, graph: SceneGraph
) -> ScopeRow:
    item = _item_for(check, finding, index, total, graph)
    outcome = _FINDING_OUTCOME.get(finding.outcome, "needs_verification")
    return ScopeRow(
        item=item,
        requirement_id=check.id,
        requested=True,
        applicability="unknown",
        applicability_reason=(
            "applicability has not been established with evidence; the outcome below "
            "is a calculation only and never a verified legal conclusion"
        ),
        outcome=outcome,
        reason=finding.detail or finding.title,
        evidence_refs=_evidence_refs(check, finding),
        measurement=_measurement_ref(finding),
        source_version=check.citation.edition if check.citation else None,
        legal_review_status=(
            "needs_review" if outcome == "needs_verification" else "unreviewed_preview"
        ),
    )


_SUBJECT_MATCHES = {
    "door": lambda node: node.kind in {"door", "opening"}
    or "door" in (node.raw_category or "").casefold(),
    "entrance": lambda node: node.kind in {"door", "opening"}
    or "door" in (node.raw_category or "").casefold(),
    "service_counter": lambda node: node.kind in {"service_counter", "counter"}
    or "counter" in (node.raw_category or "").casefold()
    or "counter" in node.label.casefold(),
    "point_of_sale": lambda node: node.kind in {"point_of_sale", "counter"}
    or "counter" in (node.raw_category or "").casefold()
    or "counter" in node.label.casefold(),
    "dining_surface": lambda node: "table" in (node.raw_category or "").casefold(),
    "floor": lambda node: node.kind == "floor"
    or "floor" in (node.raw_category or "").casefold(),
    "wall_mounted": lambda node: node.kind == "wall"
    or "wall" in (node.raw_category or "").casefold(),
    "restroom": lambda node: "restroom" in (node.raw_category or "").casefold()
    or "restroom" in node.label.casefold(),
}
"""Requirement subjects read from the pack's applies_to, matched against the
graph's own categories and labels. A locus that mixes a subject with its
blockers (the counter and the chairs crowding its approach) must row the
subject, never whatever node happens to come first."""


def _item_for(
    check: Check, finding: Finding, index: int, total: int, graph: SceneGraph
) -> ScopeItem:
    """The item a finding is about, rooted in the graph's own categories.

    A locus that names one node becomes that node. A locus that names several
    (a counter plus the chairs crowding its clear floor space) prefers the
    node matching the requirement's subject, and only falls back to the first
    locus node when nothing matches. A finding with no locus keeps the site
    slug it always had. A question finding observes nothing yet: whatever the
    row names, it stays unobserved until a capture answers it.
    """
    nodes = _locus_nodes(finding, graph)
    if nodes:
        node = _subject_node(nodes, getattr(check, "applies_to", []) or [])
        kind = _ITEM_KINDS_FOR_NODES.get(node.kind, "object")
        observed = finding.outcome != "question"
        return ScopeItem(
            item_id=node.id,
            item_slug=f"{check.id}:{node.id}",
            item_kind=kind,
            label=node.label,
            observed=observed,
            source="measured" if observed else "requested_not_observed",
        )
    slug = f"site:{check.id}" if total == 1 else f"site:{check.id}:{index}"
    observed = finding.outcome != "question" and finding.measured_inches is not None
    return ScopeItem(
        item_slug=slug,
        item_kind="site",
        label=finding.title or check.title,
        observed=observed,
        source="measured" if observed else "requested_not_observed",
    )


def _subject_node(nodes: list, applies_to: list[str]):
    matchers = [_SUBJECT_MATCHES[subject] for subject in applies_to if subject in _SUBJECT_MATCHES]
    for matcher in matchers:
        for node in nodes:
            if matcher(node):
                return node
    return nodes[0]


def _locus_nodes(finding: Finding, graph: SceneGraph) -> list:
    if finding.locus is None:
        return []
    return [node for node in graph.nodes if node.id in finding.locus.node_ids]


def _evidence_refs(check: Check, finding: Finding) -> list[str]:
    refs: list[str] = []
    section = (finding.citation or check.citation).section if (finding.citation or check.citation) else None
    if section:
        refs.append(section)
    url = (finding.citation or check.citation).url if (finding.citation or check.citation) else None
    if url:
        refs.append(url)
    return refs


def _measurement_ref(finding: Finding) -> dict | None:
    if finding.measured_inches is None:
        return None
    return {
        "estimate_inches": finding.measured_inches,
        "required_inches": finding.required_inches,
        "units": "in",
        "bounds": "unknown",
        "method": "scan-derived (no stated accuracy)",
    }


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
        applicability_reason=(
            "applicability has not been established with evidence; the outcome below "
            "is a calculation only and never a verified legal conclusion"
        ),
        outcome="unobserved",
        reason=waiting_reason or "the check produced no finding in this pass; the requirement stays unobserved until it is evaluated",
        evidence_refs=[],
        source_version=check.citation.edition if check.citation else None,
        legal_review_status="unreviewed_preview",
    )


def _class_rows(graph: SceneGraph) -> list[ScopeRow]:
    """One row per requested target class, so nothing disappears for want of a
    detection. These are coverage obligations, not legal requirements: a row
    with an observed item says the coverage question has an answer; a row
    without one stays unobserved, and neither says anything about compliance.
    """
    rows: list[ScopeRow] = []
    present: dict[str, list] = {name: [] for name in PILOT_TARGET_CLASSES}
    for node in graph.nodes:
        for name in PILOT_TARGET_CLASSES:
            if _class_matches(node, name):
                present[name].append(node)
    for name in PILOT_TARGET_CLASSES:
        nodes = present[name]
        observed = bool(nodes)
        rows.append(
            ScopeRow(
                item=ScopeItem(
                    item_slug=f"class:{name}",
                    item_kind="class",
                    label=f"{name} (target class coverage)",
                    observed=observed,
                    source="measured" if observed else "requested_not_observed",
                ),
                requirement_id=f"coverage:{name}",
                requested=True,
                applicability="unknown",
                applicability_reason=(
                    "which items serve customers is an owner question; coverage records "
                    "what the scan can see, and never that the class is absent"
                ),
                outcome="satisfied" if observed else "unobserved",
                reason=(
                    f"at least one {name} item was found"
                    if observed
                    else (
                        f"no {name} item was detected in this scan; absence is not "
                        "established without an independent human inventory over the declared area"
                    )
                ),
                evidence_refs=[],
                legal_review_status="unreviewed_preview",
            )
        )
    return rows


def _class_matches(node, class_name: str) -> bool:
    label = node.label.casefold()
    tokens = {
        "outlet": (
            node.kind in {"outlet", "candidate_outlet"}
            or node.raw_category in {"outlet", "electrical"}
            or "outlet" in label
        ),
        "television": (
            node.kind in {"television", "tv"}
            or node.raw_category in {"television", "tv"}
            or "television" in label
        ),
        "service_counter": (
            node.kind in {"service_counter", "counter"}
            or node.raw_category in {"service_counter", "counter"}
            or "counter" in label
        ),
        "restroom_entrance": (
            node.kind in {"restroom_entrance", "restroom"}
            or node.raw_category in {"restroom_entrance", "restroom"}
            or "restroom" in label
        ),
    }
    return tokens[class_name]


def _waiting_row(rule_id: str, waiting_reason: str) -> ScopeRow:
    """A rule the pass could not evaluate: a visible question, never a gone row."""
    return ScopeRow(
        item=ScopeItem(
            item_slug=f"site:{rule_id}",
            item_kind="site",
            label=rule_id.replace("_", " ").capitalize(),
            observed=False,
            source="requested_not_observed",
        ),
        requirement_id=rule_id,
        requested=True,
        applicability="unknown",
        applicability_reason=(
            "the rule could not be evaluated in this pass; unevaluated and "
            "unsupported checks surface as needs_verification"
        ),
        outcome="needs_verification",
        reason=waiting_reason,
        evidence_refs=[],
        legal_review_status="needs_review",
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


def build_evidence_dossier(
    manifest: ScopeManifest,
    assessment: Assessment,
    *,
    site: Mapping[str, str],
    control_measurement_gaps: list[str],
    recapture_notes: list[str],
    before_after: list | None = None,
) -> dict:
    """The scoped evidence dossier G13 describes, from a frozen manifest.

    Every requested requirement carries its visible rows with provenance
    (item identity and source), the calculation outcome, the reason or the
    exact missing input, the source edition and the legal-review status.
    Outcome coverage over the requested denominator is computed and refused
    unless complete, so an unreviewed or unevaluated requirement can never
    fall out of the dossier.

    The assessment is bound to the manifest by identity before anything is
    published: an assessment from another scan, revision, graph or rulepack is
    a DossierIdentityError, never a dossier.

    `before_after` holds recommendation evidence; any nonempty entry must
    carry real before AND after measurements with provenance or the dossier
    refuses, so an unmeasured claim can never be marked justified.
    """
    from collections import defaultdict

    allowed = {"satisfied", "violation", "needs_verification", "not_applicable", "unobserved"}
    _bind_assessment_identity(manifest, assessment)
    before_after = _validated_before_after(before_after)
    by_requirement: dict[str, list[dict]] = defaultdict(list)
    for row in manifest.rows:
        if not row.requested:
            continue
        by_requirement[row.requirement_id].append(
            {
                "item": {
                    "slug": row.item.item_slug,
                    "kind": row.item.item_kind,
                    "item_id": str(row.item.item_id) if row.item.item_id else None,
                    "label": row.item.label,
                    "observed": row.item.observed,
                    "source": row.item.source,
                },
                "outcome": row.outcome,
                "reason": row.reason,
                "applicability": row.applicability,
                "applicability_reason": row.applicability_reason,
                "evidence_refs": row.evidence_refs,
                "measurement": row.measurement,
                "source_version": row.source_version,
                "legal_review_status": row.legal_review_status,
            }
        )

    requested = sorted(manifest.requested_requirements)
    covered = [req for req in requested if by_requirement.get(req)]
    fraction = len(covered) / max(len(requested), 1)
    if fraction < 1.0:
        missing = sorted(set(requested) - set(covered))
        raise ValueError(
            f"dossier cannot omit requested requirements: {missing}; "
            f"coverage {fraction:g} < 1.0"
        )
    for req in requested:
        for entry in by_requirement[req]:
            if entry["outcome"] not in allowed:
                raise ValueError(f"row for {req} has outcome {entry['outcome']!r}")
            if not entry.get("reason"):
                raise ValueError(f"row for {req} has no reason or missing-input note")

    return {
        "schema_version": 1,
        "kind": "moffett_scoped_evidence_dossier",
        "pinned": {
            "scan_id": str(manifest.scan_id),
            "graph_revision": manifest.graph_revision,
            "graph_hash": manifest.graph_hash,
            "rulepack_version": manifest.rulepack_version,
            "manifest_id": str(manifest.id),
            "manifest_hash": manifest.manifest_hash,
            "assessment_id": str(assessment.id),
            "assessment_pass_number": assessment.pass_number,
            "created_at": manifest.created_at.isoformat(),
        },
        "site": dict(site),
        "requested_requirements": requested,
        "requested_classes": manifest.requested_classes,
        "route_endpoints": manifest.route_endpoints,
        "applicability_questions": manifest.applicability_questions,
        "unresolved_questions": manifest.unresolved_questions,
        "requirements": {
            req: by_requirement[req] for req in requested
        },
        "legal_review": {
            "status": "no human reviews recorded at this revision",
            "per_row_statuses_present": True,
            "note": "every row carries its own legal_review_status; no row may claim reviewer_supplied unless a review was supplied",
        },
        "control_measurements": {
            "independent_field_controls": [],
            "gaps": list(control_measurement_gaps),
        },
        "recapture_and_verification": list(recapture_notes),
        "before_after": {
            "justified": bool(before_after),
            "entries": list(before_after or []),
            "note": (
                "recommendations appear only where a before/after measurement "
                "set supports them; an empty list means none is justified at this revision"
            ),
        },
        "coverage": {
            "requested_requirements": len(requested),
            "requirements_with_explicit_outcome": len(covered),
            "outcome_coverage_fraction": fraction,
            "unsupported_whole_site_compliance_claims": 0,
            "note": "a complete dossier can truthfully contain violations and unknowns, and never certifies the site",
        },
    }


class DossierIdentityError(ValueError):
    """The assessment does not belong to the manifest it is being published with."""

    def __init__(self, field: str, manifest_value, assessment_value) -> None:
        self.field = field
        self.manifest_value = manifest_value
        self.assessment_value = assessment_value
        super().__init__(
            f"assessment and scope manifest disagree on {field}: "
            f"manifest {manifest_value!r} vs assessment {assessment_value!r}"
        )


class DossierProvenanceError(ValueError):
    """A before/after recommendation carries no measured provenance."""


def _bind_assessment_identity(manifest: ScopeManifest, assessment: Assessment) -> None:
    """The assessment the dossier publishes must be the manifest's own."""
    pairs = [
        ("scan_id", str(manifest.scan_id), str(assessment.scan_id)),
        ("graph_revision", manifest.graph_revision, assessment.graph_revision),
        ("graph_hash", manifest.graph_hash, assessment.graph_hash),
        ("rulepack_version", manifest.rulepack_version, assessment.rulepack_version),
    ]
    for field, manifest_value, assessment_value in pairs:
        if manifest_value != assessment_value:
            raise DossierIdentityError(field, manifest_value, assessment_value)


def _validated_before_after(entries: list | None) -> list:
    """Entries without measured before/after provenance are refused outright."""
    clean = list(entries or [])
    for entry in clean:
        if not isinstance(entry, dict):
            raise DossierProvenanceError(
                f"before_after entry is not a record: {entry!r}"
            )
        for side in ("before", "after"):
            record = entry.get(side)
            if not isinstance(record, dict) or not record.get("measurement") or not record.get("provenance"):
                raise DossierProvenanceError(
                    f"before_after entry {entry!r} lacks a measured {side} "
                    "record with measurement and provenance"
                )
    return clean


def _hash(**fields) -> str:
    payload = json.dumps(fields, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
