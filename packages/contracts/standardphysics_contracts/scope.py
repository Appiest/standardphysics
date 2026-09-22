"""What was asked, what was observed, and one visible outcome for every pair.

The scope manifest is frozen before an assessment runs and hashed. Every
requested requirement leaves a row: a satisfied check, a violation, a question
that needs verification, a supported not-applicable, or an unobserved item with
the next capture action. No row disappears because detection failed, and no
unknown becomes a pass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .geometry import Vec3

OutcomeState = Literal["satisfied", "violation", "needs_verification", "not_applicable", "unobserved"]

Applicability = Literal["applicable", "not_applicable", "unknown"]

LegalReviewStatus = Literal["unreviewed_preview", "needs_review", "reviewer_supplied"]

MeasurementRef = dict
"""A measurement's provenance record, shaped by the measurement contract on the
producer side. Kept as a mapping here so the scope row does not fork a second
measurement model; `packages/contracts/standardphysics_contracts/measurement.py`
is authoritative."""


class ScopeItem(BaseModel):
    """One thing an outcome can be about: an object, an area, or a route leg.

    `item_id` names a SceneNode when the item was found. An unresolved class or
    an unobserved area keeps a stable slug so the obligation survives every
    later revision.
    """

    item_id: UUID | None = None
    item_slug: str
    item_kind: Literal["object", "area", "route", "site", "class"]
    label: str
    observed: bool = True
    source: Literal["measured", "owner_confirmed", "manual_photo", "requested_not_observed"] = "measured"


class ScopeRow(BaseModel):
    """One requested requirement applied to one item, with its outcome."""

    item: ScopeItem
    requirement_id: str
    requested: bool = True
    applicability: Applicability = "unknown"
    applicability_reason: str | None = None
    applicability_facts: list[str] = []
    outcome: OutcomeState
    reason: str | None = None
    evidence_refs: list[str] = []
    measurement: MeasurementRef | None = None
    source_version: str | None = None
    """Rulepack/source identity this row's requirement came from."""
    legal_review_status: LegalReviewStatus = "unreviewed_preview"

    @property
    def is_pass(self) -> bool:
        return self.outcome == "satisfied"

    @property
    def needs_person(self) -> bool:
        return self.outcome == "needs_verification"


class ScopeManifest(BaseModel):
    """The immutable, hashed list of everything this assessment was asked to cover."""

    id: UUID
    scan_id: UUID
    version: int = 1
    created_at: datetime
    graph_revision: int
    graph_hash: str
    rulepack_version: str
    manifest_hash: str
    surveyed_areas: list[str] = []
    unobserved_areas: list[str] = []
    route_endpoints: list[str] = []
    requested_classes: list[str] = []
    requested_requirements: list[str] = []
    applicability_questions: list[str] = []
    unresolved_questions: list[str] = []
    rows: list[ScopeRow] = []

    def row_for(self, requirement_id: str, item_slug: str) -> ScopeRow | None:
        for row in self.rows:
            if row.requirement_id == requirement_id and row.item.item_slug == item_slug:
                return row
        return None


class PointRef(BaseModel):
    """A named point a row's evidence is about, for a map marker or a crop link."""

    name: str
    position: Vec3
