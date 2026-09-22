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
from .geometry import Vec3
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


class FrameEntry(BaseModel):
    """One stored source-resolution frame a photo review can open.

    `width` and `height` are the stored sensor pixels this frame was captured
    at, read from the bytes the server holds - never EXIF display orientation.
    `image_url` is the route serving that exact frame's original bytes.
    """

    frame_id: str
    width: int
    height: int
    image_url: str


class FrameListing(BaseModel):
    frames: list[FrameEntry]
    unreadable: list[str] = []
    """Real stored frame artifacts whose bytes are not a readable image.

    Their dimensions stay unknown, their original bytes remain downloadable
    through the frame route, and the valid photos around them stay usable.
    """


class ApproachRequest(BaseModel):
    """One measured journey to one target, evaluated with the owner's profile.

    Nothing reaches here by default: without a person-provided horizontal
    reach the evaluation reports it unmeasured, and an unmeasured input is
    never an answer.
    """

    target_node_id: UUID
    occupant_profile: str = "manual-wheelchair"
    """One of the screening catalog ids (manual-wheelchair, power-wheelchair,
    walker, cane-user, person)."""
    horizontal_reach_inches: float | None = None
    """The person's actual sideways grasp distance, when someone provided it."""
    horizontal_reach_provenance: str | None = None
    """Who provided the number, e.g. 'owner measured'. Required with a value."""
    approach_stop: Vec3 | None = None


class ReachReport(BaseModel):
    occupant_title: str
    target_height_inches: float | None
    vertical_status: str
    horizontal_distance_inches: float | None
    horizontal_reach_inches: float | None
    horizontal_reach_provenance: str | None
    horizontal_status: str


class ApproachReport(BaseModel):
    """Conservative screening answer, never a legal claim (contract 4/6)."""

    target_id: UUID
    status: str
    """clear | blocked | needs_verification."""
    reasons: list[str]
    approach_stop: Vec3 | None
    path: list[Vec3] | None
    aisle_width_inches: float | None
    turning_space_inches: float | None
    obstruction_labels: list[str]
    floor_supported: bool | None
    mesh_checked: bool
    mesh_collision: bool
    reaches: list[ReachReport]
    unverified: list[str]


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
