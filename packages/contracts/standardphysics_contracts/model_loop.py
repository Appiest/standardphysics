"""A language model fixing a layout turn by turn from the menu of legal moves, streamed to the web app.

Each turn the model reads the menu for every problem still left, picks, and
the pick is applied and re-checked. The stream reports each turn as it
finishes, then the combined moves, so the owner can open the result in the
plan and decide whether to keep it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .loop import NodeMove
from .wishes import ProposalExplanation


class ModelLoopInfo(BaseModel):
    """Whether a model is set up to run the loop, and what to call it on the button."""

    available: bool
    label: str = ""


class ModelLoopRequest(BaseModel):
    base_revision: int


class ModelLoopEvent(BaseModel):
    kind: Literal["started", "turn", "finished", "failed"]
    turn: int | None = None
    picked: list[str] = []
    """The options the model chose this turn, in the owner's words."""
    why: str = ""
    """The model's own reason for this turn's pick."""
    fixable_left: int | None = None
    moves: list[NodeMove] = []
    """On `finished`, every move the loop made, measured from the layout it started with."""
    explanation: ProposalExplanation | None = None
    message: str = ""
