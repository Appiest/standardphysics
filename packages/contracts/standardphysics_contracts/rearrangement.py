"""Asking the fine-tuned rearrangement model for a layout, and what came back.

The model runs as a background job because a deployment scaled to zero can
take minutes to start. The page starts a job with `RearrangementRequest` and
polls `RearrangementStatus` until `state` is done or failed. Nothing here is
ever saved as a revision: accepted moves go to the owner, who saves them
through the ordinary layout save or discards them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .findings import Finding
from .loop import NodeMove


class RearrangementRequest(BaseModel):
    base_revision: int


class RewardParts(BaseModel):
    """The training reward for the chosen attempt, and the terms it is made of."""

    reward: float
    recovered: float = Field(description="Share of the measured shortfall the moves recover, 0 to 1.")
    all_clear: bool = Field(description="Whether every problem furniture can fix is gone.")
    usability: float = Field(description="U: how usable the touched tables, desks and counters stay, 0 to 1.")
    disruption_meters: float = Field(description="Metres slid plus a fixed cost per turn.")


class RearrangementAttempt(BaseModel):
    accepted: bool
    reward: float
    reason: str = Field(description="Why the attempt was turned down, in plain words; empty when accepted.")


class RearrangementSuggestion(BaseModel):
    base_revision: int
    accepted: bool
    message: str = Field(description="One sentence for the owner: what the suggestion does, or why there is none.")
    moves: list[NodeMove] = Field(default_factory=list)
    graph_hash: str | None = Field(default=None, description="Hash of the layout the moves produce.")
    findings_before: list[Finding] = Field(default_factory=list)
    findings_after: list[Finding] = Field(default_factory=list)
    reward: RewardParts | None = None
    attempts: list[RearrangementAttempt] = Field(default_factory=list)


class RearrangementStatus(BaseModel):
    base_revision: int
    available: bool
    unavailable_reason: str | None = None
    state: Literal["idle", "queued", "running", "done", "failed"]
    phase: Literal["waiting", "starting_model", "asking_model", "checking"] | None = None
    """While running: waiting for the worker, starting a cold model, asking it, or checking its answers."""
    error: str | None = None
    result: RearrangementSuggestion | None = None
