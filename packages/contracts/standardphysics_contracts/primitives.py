"""What a primitive returns, and what it is allowed to be asked.

A primitive is one thing the app knows how to read off a room: the height of a
surface, what is standing on a desk, the clear width along a route. Rules
compose them and so do questions, which is why the result shape lives here
rather than in either lane.

Three properties hold for every primitive, and the rest of the system leans on
all three.

Every result carries its evidence. A number with no subject cannot point a
camera at anything, and an answer nobody can look at is not an answer.

Not measurable is a result, not an error. A door's clear opening needs the door
swung to ninety degrees and a scan cannot settle it, so the primitive says so
and the caller turns that into a question rather than a pass.

Nothing here reads a model. A primitive is the half of the system that knows
what is true about the room; judgment about which primitive to call belongs to
the caller.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .geometry import Vec3

Quality = Literal["measured", "needs_another_look", "not_measurable"]
"""measured: read off the scan at confidence.
needs_another_look: the geometry is there but thin, so it becomes a request.
not_measurable: no scan can settle this, whatever its quality.
"""


class Evidence(BaseModel):
    """What a result was read off, and where to look to see it."""

    model_config = ConfigDict(extra="forbid")
    subjects: list[UUID] = Field(default_factory=list)
    at: Vec3 | None = None
    """Where a camera should point. A width has a pinch point; a height has the
    spot on the surface the tape touched."""
    frames: list[str] = Field(default_factory=list)
    """Capture frames behind a result that came from imagery rather than mesh."""


class Quantity(BaseModel):
    """One measured number, in the unit the standard is written in."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["quantity"] = "quantity"
    value: float
    unit: str


class NodeSet(BaseModel):
    """Nodes the primitive selected, such as everything standing on a desk."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["nodes"] = "nodes"
    node_ids: list[UUID] = Field(default_factory=list)


class TextSet(BaseModel):
    """Words read off surfaces in the room."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["texts"] = "texts"
    texts: list[str] = Field(default_factory=list)


class Truth(BaseModel):
    """A yes or no the geometry settled, such as whether a body fits."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["truth"] = "truth"
    value: bool


Payload = Annotated[Union[Quantity, NodeSet, TextSet, Truth], Field(discriminator="type")]


class PrimitiveResult(BaseModel):
    """One primitive's answer, with everything needed to show or cite it."""

    model_config = ConfigDict(extra="forbid")
    primitive: str
    payload: Payload | None = None
    """None only when quality is not_measurable: there is no number to give."""
    evidence: Evidence = Field(default_factory=Evidence)
    quality: Quality = "measured"
    note: str | None = Field(default=None, max_length=300)
    """Why a result is thin or impossible, in words an owner can read."""

    @property
    def measured(self) -> bool:
        return self.quality == "measured" and self.payload is not None

    def number(self) -> float | None:
        return self.payload.value if isinstance(self.payload, Quantity) else None

    def nodes(self) -> list[UUID]:
        return list(self.payload.node_ids) if isinstance(self.payload, NodeSet) else []


class PrimitiveSpec(BaseModel):
    """A primitive as a model is shown it: a name, what it does, its arguments.

    This is the whole vocabulary a planner gets. A plan naming anything outside
    it is refused before it runs.
    """

    model_config = ConfigDict(extra="forbid")
    name: str
    summary: str
    """One sentence, written for whoever is choosing between primitives."""
    arguments: dict = Field(default_factory=dict)
    """JSON Schema for the arguments, generated from the argument model."""
    returns: Literal["quantity", "nodes", "texts", "truth"]
