from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from .geometry import CameraPose, Vec3
from .rules import Citation

AnnotationKind = Literal["dimension_line", "region", "path"]

Outcome = Literal["passes", "problem", "question"]

Asks = Literal["photo", "owner_report", "document", "measurement", "swing", "another_look", "review"]
"""What answers a question: a photo, the owner's word, a document, a number
they measure, which way a door swings, or another walk past the spot.
`review` means the owner has sent what was asked and a person on the team is
checking it, so nothing more is needed from the owner."""


class Annotation(BaseModel):
    kind: AnnotationKind
    points: list[Vec3]
    label: str
    """What the viewer draws beside the geometry, e.g. "31 in"."""

    point_inches: list[float | None] | None = None
    """For a `path`, the clearance at each point in inches, parallel to `points`.
    None where a value means nothing, such as inside a stop's exemption."""


class Locus(BaseModel):
    """Where in the model this finding lives, and how to show it."""

    point: Vec3
    bbox_min: Vec3
    bbox_max: Vec3
    node_ids: list[UUID]
    annotation: Annotation
    camera: CameraPose
    render_url: str | None = None


class Finding(BaseModel):
    id: UUID
    check_id: str
    outcome: Outcome

    title: str
    """Plain language. "The path to the counter is too narrow"."""

    detail: str
    """The measurement and what is needed. "It's 31 inches at the tightest
    point. Wheelchairs need 36 inches." """

    fix: str | None = None
    """What to do. "Move the two tables by the window 5 inches apart." """

    measured_inches: float | None = None
    required_inches: float | None = None
    citation: Citation
    locus: Locus | None = None
    asks: Asks | None = None
    """For a question, what would answer it. None for a pass or a problem."""
