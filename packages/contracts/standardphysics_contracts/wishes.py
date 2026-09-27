"""What an owner wants kept when the rearranger changes their shop, and how a proposal explains itself.

An owner states a wish when they turn a proposal down: keep this piece where
it is, or keep it within some inches of another. Stated wishes are saved on
the scan and bind every later proposal, the same way the room's ADA layout
directives do. A proposal also says, in plain words, what moved, what it
fixed, and which of the owner's choices it kept or had to bend, with a
ready-made wish for each bent one so the owner can say "keep that" in a tap.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_NEAR_INCHES = 240.0


class OwnerWish(BaseModel):
    """One thing the owner wants kept."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["stays_put", "stays_near"]
    node_id: UUID
    anchor_id: UUID | None = None
    """For `stays_near`, the piece to stay near."""
    inches: float | None = Field(default=None, gt=0, le=MAX_NEAR_INCHES)
    """For `stays_near`, how near."""
    text: str = Field(default="", max_length=200)
    """The wish in the owner's words, for showing back to them."""

    @model_validator(mode="after")
    def _near_needs_an_anchor_and_a_distance(self) -> OwnerWish:
        if self.kind == "stays_near" and (self.anchor_id is None or self.inches is None):
            raise ValueError("stays_near needs anchor_id and inches")
        return self


class OwnerWishesRequest(BaseModel):
    """Every wish the owner wants kept for this shop, replacing the ones saved before."""

    model_config = ConfigDict(extra="forbid")
    wishes: list[OwnerWish] = Field(default_factory=list, max_length=50)


class BentWish(BaseModel):
    """A choice the owner's layout showed that a proposal breaks, and the wish that would keep it."""

    text: str
    keep: OwnerWish | None = None
    """What to save if the owner wants this kept; None when no single piece can hold it."""


class ProposalExplanation(BaseModel):
    """A proposal in the owner's words, built only from what was measured."""

    moves: list[str] = []
    fixed: list[str] = []
    kept: list[str] = []
    bent: list[BentWish] = []
