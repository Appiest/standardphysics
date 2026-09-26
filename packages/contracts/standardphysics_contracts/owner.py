"""What the owner's side of the product reads and sends.

The first run in docs/UX.md asks the owner for answers and photos, tracks a
checklist of fixes, says where each shop is in the journey, shares the report
and keeps planned layouts apart from the shop as scanned. These are the wire
shapes for all of it. The iPhone app and the web both read them, so a change
here is a change to both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .findings import Finding
from .geometry import Vec3
from .loop import NodeMove

Role = Literal["owner", "team"]


class Session(BaseModel):
    """Who is signed in. Never the token.

    A guest is an account made on the phone's first launch, before the owner
    has given an email. It works like any other account until it is saved.
    `deletes_at` is when a guest's shops will be deleted if nobody opens them,
    and it is null for a saved account.
    """

    owner_id: UUID
    email: str | None
    shop_name: str
    role: Role = "owner"
    guest: bool = False
    deletes_at: datetime | None = None


RequestKind = Literal["yes_no", "photo", "number", "another_look"]
"""What the owner does to answer. `another_look` has no answer: the next walk
of the shop clears it."""

RequestStatus = Literal["open", "skipped", "answered", "checked", "not_applicable"]
"""`skipped` stays open in the report but stops asking in the app. `answered`
means we have the answer and a person still has to look at a photo. `checked`
means the answer has been turned into a result. `not_applicable` means another
answer made this one unnecessary, like a photo of a restroom nobody uses."""

RequestTiming = Literal["in_shop", "follow_up"]
"""`in_shop` requests are known before the shop is measured, so the app asks
them while the owner is still standing in it. `follow_up` requests come from a
check that needed something only after measuring."""

Unit = Literal["in", "lb"]


class RequestAnswer(BaseModel):
    yes: bool | None = None
    number: float | None = None
    photo_url: str | None = None
    answered_at: datetime


class OwnerRequest(BaseModel):
    """One thing the app asks the owner for: an answer, a photo or a number."""

    id: str
    """Stable for the shop: "restroom", "door_hardware", or "finding-<uuid>"."""
    kind: RequestKind
    timing: RequestTiming
    title: str
    detail: str
    unit: Unit | None = None
    status: RequestStatus
    answer: RequestAnswer | None = None
    finding_id: UUID | None = None
    """The finding this request resolves, once the shop has been measured."""
    review: Literal["passes", "problem"] | None = None


class ShopRequests(BaseModel):
    requests: list[OwnerRequest]


class AnswerRequest(BaseModel):
    """A yes or no, or a number in the request's unit. Exactly one."""

    yes: bool | None = None
    number: float | None = Field(default=None, gt=0, lt=10_000)

    @model_validator(mode="after")
    def one_answer(self) -> AnswerRequest:
        if (self.yes is None) == (self.number is None):
            raise ValueError("send either yes or number")
        return self


class ReviewAnswer(BaseModel):
    """A team member's verdict on a photo the owner sent."""

    outcome: Literal["passes", "problem"]


class PendingReview(BaseModel):
    scan_id: UUID
    shop_name: str
    request: OwnerRequest


class ReviewQueue(BaseModel):
    reviews: list[PendingReview]


ChecklistStatus = Literal["to_do", "done", "not_doing", "needs_pro"]


class ChecklistItem(BaseModel):
    finding_id: UUID
    status: ChecklistStatus = "to_do"
    updated_at: datetime | None = None


class Checklist(BaseModel):
    """One item per thing to fix. `done` counts every item that isn't To do."""

    items: list[ChecklistItem]
    done: int
    total: int


class ChecklistUpdate(BaseModel):
    status: ChecklistStatus


JourneyStage = Literal["walk", "fill_in_the_gaps", "results", "fix", "tools"]
"""Set up happens on the phone before the server knows the shop, so it has no
stage here."""

NextStepKind = Literal[
    "upload", "answers", "photos", "measuring", "counter", "path", "follow_ups",
    "results", "checklist", "done", "failed",
]


class NextStep(BaseModel):
    kind: NextStepKind
    title: str
    """Ready to show: "Take 2 more photos", "1 of 3 done. Next: move the display case"."""
    count: int | None = None


class Journey(BaseModel):
    scan_id: UUID
    shop_name: str
    stage: JourneyStage
    next_step: NextStep
    tools_unlocked: bool


class JourneyList(BaseModel):
    journeys: list[Journey]


Destination = Literal["seating", "restroom", "fitting_room", "shelves", "pickup"]
"""Where else customers go, besides in, to the counter and out again."""


class ShareLink(BaseModel):
    """A read-only report link. `path` is on the web, like "/r/<token>"."""

    path: str
    expires_at: datetime


class SavePlanRequest(BaseModel):
    base_revision: int
    moves: list[NodeMove] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=80)


class LayoutPlan(BaseModel):
    """A layout the owner planned. It never changes the shop as scanned."""

    id: UUID
    scan_id: UUID
    base_revision: int
    name: str
    moves: list[NodeMove]
    created_at: datetime
    findings: list[Finding]
    """Every check, run against the planned layout."""


class PlanList(BaseModel):
    plans: list[LayoutPlan]


class DeviceRegistration(BaseModel):
    environment: Literal["production", "sandbox"] = "production"


class FunnelStep(BaseModel):
    key: str
    label: str
    """What the owner did, in the team's words: "Finished the upload"."""
    shops: int


class Funnel(BaseModel):
    """How far owners get, from the first walk to the first fix. Team only."""

    steps: list[FunnelStep]
    median_minutes_to_results: float | None
    """From the start of the walk to results ready, over shops that got there."""
    median_hours_to_first_fix: float | None
    """From results ready to the first item marked done, over shops that got there."""


class RouteLeg(BaseModel):
    """How a customer walks from one stop to the next, around what's in the way."""

    from_stop: str
    to_stop: str
    path: list[Vec3]
    reachable: bool


class RouteLegs(BaseModel):
    legs: list[RouteLeg]
