"""Do I have space for a 97 inch couch?

One length is not a couch. A couch has a depth and a height, and testing a
guess at either would answer a question nobody asked, so a missing dimension
comes back as one specific thing to go and measure rather than as a number we
made up.

With all three, the candidate is tested at exactly the size given. It is placed
against a wall, turned to lie along it, and checked against every obstacle, the
door's swing, the floor boundary and every route in the shop. It is never
shrunk to make it fit. An answer that only works because the couch got smaller
is a wrong answer about a different couch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from pydantic import BaseModel
from standardphysics_contracts import (
    Mat4,
    MeasurementProvider,
    Scenario,
    SceneGraph,
    SceneNode,
    Vec3,
    to_meters,
)
from standardphysics_contracts.rules import Tier

from ..assess import Pass, assess
from ..evaluation.gate import accepts
from ..fix.constraints import Violation, interior_bounds, violations
from ..numbers import by_size, size
from ..rules import AgentRulePack, VerificationLedger
from ..tracing import traced
from .answer import Answer, AskContext
from .locus import subject_locus
from .query import Query

WALL_CLEARANCE = 0.02
"""Two centimetres off the wall, so a piece standing against one does not read
as standing in one."""

PLACEMENT_STEP = 0.25
"""How far apart to try positions along a wall, in metres. Ten inches."""

MISSING_QUESTIONS = {
    "depth_inches": "Measure how deep it is, front to back, and tell us.",
    "height_inches": "Measure how tall it is and tell us.",
}


class FitRequest(BaseModel):
    """A thing the owner is thinking of buying, at exactly its real size."""

    model_config = {"extra": "forbid"}

    label: str = "couch"
    length_inches: float
    depth_inches: float | None = None
    height_inches: float | None = None

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in ("depth_inches", "height_inches")
            if getattr(self, name) is None
        )


@dataclass(frozen=True)
class FitAnswer:
    request: FitRequest
    placement: Vec3 | None = None
    rotation_degrees: float = 0.0
    missing: tuple[str, ...] = ()
    question: str | None = None
    blocked_by: str | None = None
    message: str = ""
    tried: int = 0
    node: SceneNode | None = field(default=None, repr=False)

    @property
    def fits(self) -> bool:
        return self.placement is not None


def _candidate(request: FitRequest, centre: Vec3, degrees: float) -> SceneNode:
    """The piece at exactly the size given, turned to `degrees` about Z."""
    import math
    import uuid

    cos_t = math.cos(math.radians(degrees))
    sin_t = math.sin(math.radians(degrees))
    height = to_meters(request.height_inches or 0.0)
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"fit:{request.label}"),
        kind="object",
        label=request.label,
        raw_category="prospective",
        dimensions=Vec3(
            x=to_meters(request.length_inches),
            y=to_meters(request.depth_inches or 0.0),
            z=height,
        ),
        transform=Mat4(
            m=[
                cos_t, -sin_t, 0.0, centre.x,
                sin_t, cos_t, 0.0, centre.y,
                0.0, 0.0, 1.0, height / 2,
                0.0, 0.0, 0.0, 1.0,
            ]
        ),
        movable=True,
    )


def _wall_placements(
    graph: SceneGraph, request: FitRequest
) -> Iterator[tuple[Vec3, float]]:
    """Positions along each wall, lying flat against it.

    A 97 inch couch goes against a wall. Searching the open middle of a room
    would find placements nobody would ever choose and take four times as long
    to do it.
    """
    bounds = interior_bounds(graph)
    if bounds is None:
        return
    min_x, min_y, max_x, max_y = bounds
    depth = to_meters(request.depth_inches or 0.0)
    inset = depth / 2 + WALL_CLEARANCE

    yield from _along(min_x, max_x, min_y + inset, 0.0, horizontal=True)
    yield from _along(min_x, max_x, max_y - inset, 180.0, horizontal=True)
    yield from _along(min_y, max_y, min_x + inset, 90.0, horizontal=False)
    yield from _along(min_y, max_y, max_x - inset, 270.0, horizontal=False)


def _along(
    low: float, high: float, fixed: float, degrees: float, horizontal: bool
) -> Iterator[tuple[Vec3, float]]:
    steps = max(int((high - low) / PLACEMENT_STEP), 1)
    for index in range(steps + 1):
        moving = low + index * PLACEMENT_STEP
        centre = (
            Vec3(x=moving, y=fixed, z=0.0)
            if horizontal
            else Vec3(x=fixed, y=moving, z=0.0)
        )
        yield centre, degrees


def _first_blocker(broken: list[Violation]) -> str | None:
    for item in broken:
        if item.blocker:
            return item.blocker
    return broken[0].kind.replace("_", " ") if broken else None


@traced("fit")
def fits(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    request: FitRequest,
    *,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    baseline: Pass | None = None,
    max_tier: Tier = 1,
) -> FitAnswer:
    """A placement that works, or the thing it keeps running into."""
    if request.missing:
        return _needs_measuring(request)

    before = baseline or assess(
        graph, scenario, measure, rules=rules, ledger=ledger, max_tier=max_tier
    )
    tried, blocked_by = 0, None

    for centre, degrees in _wall_placements(graph, request):
        tried += 1
        candidate = _candidate(request, centre, degrees)
        crowded = graph.model_copy(update={"nodes": [*graph.nodes, candidate]})
        broken = violations(graph, crowded, added=frozenset({candidate.id}))
        if broken:
            blocked_by = blocked_by or _first_blocker(broken)
            continue
        after = assess(
            crowded, scenario, measure, rules=rules, ledger=ledger, max_tier=max_tier
        )
        if not accepts(before, after, require_improvement=False).accepted:
            blocked_by = blocked_by or "walkway"
            continue
        return _room_for_it(request, centre, degrees, candidate, tried)

    return _no_room(request, blocked_by, tried)


def _needs_measuring(request: FitRequest) -> FitAnswer:
    question = MISSING_QUESTIONS[request.missing[0]]
    return FitAnswer(
        request=request,
        missing=request.missing,
        question=question,
        message=f"Give us one more number about the {request.label}. {question}",
    )


def _room_for_it(
    request: FitRequest,
    centre: Vec3,
    degrees: float,
    candidate: SceneNode,
    tried: int,
) -> FitAnswer:
    footprint = by_size(request.length_inches, request.depth_inches or 0.0)
    return FitAnswer(
        request=request,
        placement=centre,
        rotation_degrees=degrees,
        message=f"Yes. A {footprint} {request.label} goes against the wall "
        "here, and every path still works.",
        tried=tried,
        node=candidate,
    )


def _no_room(request: FitRequest, blocked_by: str | None, tried: int) -> FitAnswer:
    length = size(request.length_inches)
    running_into = f" It runs into the {blocked_by.casefold()}." if blocked_by else ""
    return FitAnswer(
        request=request,
        blocked_by=blocked_by,
        message=f"A {length} {request.label} does not go anywhere along a wall "
        f"in this shop.{running_into} Tell us the size you can get it in and "
        "we will try that.",
        tried=tried,
    )


@traced("ask.space")
def space(query: Query, context: AskContext) -> Answer:
    """Do I have room for a 97 inch couch."""
    request = FitRequest(
        label=query.thing or "piece",
        length_inches=query.length_inches or 0.0,
        depth_inches=query.depth_inches,
        height_inches=query.height_inches,
    )
    answer = fits(
        context.graph,
        context.scenario,
        context.measure,
        request,
        rules=context.rules,
        ledger=context.ledger,
        baseline=context.baseline(),
        max_tier=context.max_tier,
    )
    return Answer(
        text=answer.message,
        kind="SPACE",
        query=query,
        locus=subject_locus([answer.node], request.label) if answer.node else None,
        data={
            "fits": answer.fits,
            "missing": list(answer.missing),
            "blocked_by": answer.blocked_by,
            "placements_tried": answer.tried,
            "length_inches": request.length_inches,
            "depth_inches": request.depth_inches,
            "height_inches": request.height_inches,
        },
        graph=(
            context.graph.model_copy(
                update={"nodes": [*context.graph.nodes, answer.node]}
            )
            if answer.node is not None
            else None
        ),
    )
