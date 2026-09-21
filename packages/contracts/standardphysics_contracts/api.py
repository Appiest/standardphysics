"""Request and response bodies the API adds around the domain models.

The upload shapes match `services/api/openapi.json`, which Lane A codes
against, and the mock server's error bodies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel

from .findings import Finding, Locus
from .loop import Assessment, NodeMove, Proposal, RouterAction
from .rules import Check
from .scan import Scan
from .scene import Scenario, SceneGraph


class CreateScanRequest(BaseModel):
    name: str
    device_model: str
    duration_seconds: float


class ScanList(BaseModel):
    scans: list[Scan]


class ApiError(BaseModel):
    error: str
    need: list[str] | None = None


class Blocked(BaseModel):
    """A hard constraint a layout breaks, from Lane C's fix constraints."""

    node_id: str
    reason: str
    """moved_something_fixed, resized, inventory_changed, left_the_floor, collided or blocked_a_door."""
    detail: str


class LayoutCheckRequest(BaseModel):
    """Every move so far against one saved revision, never just the latest drag.

    `sequence` rises with each drop, so the client can ignore an answer that
    arrives after a newer one.
    """

    base_revision: int
    sequence: int
    moves: list[NodeMove]


class LayoutCheckResult(BaseModel):
    sequence: int
    graph_hash: str
    findings: list[Finding]
    blocked: list[Blocked]


class SaveLayoutRequest(BaseModel):
    base_revision: int
    moves: list[NodeMove]


class ReviewedRule(BaseModel):
    """A rule that ran, and the person who read its section and confirmed the number."""

    check: Check
    verified_by: str
    verified_at: datetime
    second_check_by: str | None = None


class Report(BaseModel):
    """Everything the printed report shows, in one response."""

    scan: Scan
    scene: SceneGraph | None
    scenario: Scenario | None
    assessment: Assessment | None
    rules: list[ReviewedRule]
    preview: bool = False
    """Built from rules no person has reviewed, for development only."""


class ProposalRequest(BaseModel):
    """Ask the fix agent for a layout that clears these findings."""

    base_revision: int
    finding_ids: list[UUID]


class ProposalResult(BaseModel):
    base_revision: int
    proposal: Proposal | None
    message: str
    """The sentence to show: the fix, or that no arrangement works."""
    question: str | None = None
    """One thing the owner could allow, when nothing works as things stand."""


class AskRequest(BaseModel):
    base_revision: int
    text: str = Field(min_length=1, max_length=500)


class AskAnswer(BaseModel):
    """Lane C's answer to a question about the shop, in wire form."""

    text: str
    understood: bool
    kind: str | None = None
    subjects: list[UUID] = []
    locus: Locus | None = None
    data: dict[str, Any] = {}
    proposal: Proposal | None = None
    findings: list[Finding] = []


class LoopRequest(BaseModel):
    """Run Lane C's loop on this layout until it clears what it can or stops."""

    base_revision: int


class LoopPass(BaseModel):
    """One trip round the loop: what the router chose and what came of it."""

    number: int
    action: RouterAction | None
    """None when the router's answer did not validate, so nothing was allowed to happen."""
    problems: int
    questions: int
    message: str
    kept: bool | None = None
    """Whether the gate kept a new layout. None when no layout was tried."""
    inches_short_before: float | None = None
    inches_short_after: float | None = None
    moves: list[NodeMove] = []
    """This pass's moves, only when the gate kept them."""
    question: str | None = None


class LoopResult(BaseModel):
    base_revision: int
    decided_by: str
    """The router that chose each action: TypeSafe, or the labelled local policy."""
    passes: list[LoopPass]
    moves: list[NodeMove]
    """Every kept move from the base layout, combined per piece."""


class LoopStarted(BaseModel):
    """Sent before the first pass runs."""

    kind: Literal["started"] = "started"
    base_revision: int
    decided_by: str


class LoopPassFinished(BaseModel):
    kind: Literal["pass"] = "pass"
    loop_pass: LoopPass


class LoopFinished(BaseModel):
    kind: Literal["finished"] = "finished"
    result: LoopResult


class LoopFailed(BaseModel):
    """The loop broke partway through; nothing was saved."""

    kind: Literal["failed"] = "failed"
    error: str


class LoopEvent(
    RootModel[Annotated[LoopStarted | LoopPassFinished | LoopFinished | LoopFailed, Field(discriminator="kind")]]
):
    """One line of `POST /api/scans/{scan_id}/loop/stream`, which reports each pass as it finishes."""
