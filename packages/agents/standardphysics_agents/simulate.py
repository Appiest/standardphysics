"""Many legal layouts, measured with the same checker the loop uses.

TypeSafe picks the action. This module is the 3D search that action can spend:
hundreds of small slides along a pinch, each assessed or rejected by the
existing constraints. It does not invent geometry and it does not call the
router once per candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from standardphysics_contracts import (
    Finding,
    MeasurementProvider,
    NodeMove,
    Scenario,
    SceneGraph,
    Vec3,
)

from .assess import assess
from .evaluation.gate import accepts
from .fix.constraints import violations
from .fix.moves import apply_moves
from .fix.pinch import Pinch, pinch_from
from .fix.search import _resolves
from .fix.strategies import Candidate
from .rules import AgentRulePack, VerificationLedger
from .tracing import traced

DEFAULT_SAMPLES = 256
MAX_SAMPLES = 1024
STEP_COUNT = 16


@dataclass(frozen=True)
class ScreenedLayout:
    candidate: Candidate
    graph: SceneGraph
    measured: int


@dataclass(frozen=True)
class ScreenReport:
    samples: int = 0
    measured: int = 0
    rejected: tuple[str, ...] = ()
    best: ScreenedLayout | None = None
    widths: tuple[float, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.best is not None


def _pinches(findings: list[Finding], graph: SceneGraph) -> list[Pinch]:
    found = [pinch_from(finding, graph) for finding in findings]
    return sorted((pinch for pinch in found if pinch is not None), key=lambda pinch: -pinch.deficit_meters)


def _slide(node_id: UUID, axis: tuple[float, float], distance: float) -> NodeMove:
    return NodeMove(
        node_id=node_id,
        delta_translation=Vec3(x=axis[0] * distance, y=axis[1] * distance, z=0.0),
    )


def _outward(node, pinch: Pinch) -> float:
    centre = node.transform.position
    side = (centre.x - pinch.centre.x) * pinch.across[0] + (
        centre.y - pinch.centre.y
    ) * pinch.across[1]
    return 1.0 if side >= 0 else -1.0


def dense_candidates(pinch: Pinch, samples: int) -> list[Candidate]:
    """A grid of slides along and across the pinch, capped at `samples`."""
    if not pinch.fixable:
        return []
    budget = min(max(samples, 1), MAX_SAMPLES)
    needed = pinch.deficit_meters + 0.0127
    found: list[Candidate] = []
    fractions = [(index + 1) / STEP_COUNT for index in range(STEP_COUNT)]
    for node in pinch.movable:
        sign = _outward(node, pinch)
        for fraction in fractions:
            found.append(
                Candidate(
                    "screen_across",
                    [_slide(node.id, pinch.across, sign * needed * (0.5 + fraction * 2.0))],
                    needed * (0.5 + fraction * 2.0),
                )
            )
            found.append(
                Candidate(
                    "screen_along",
                    [_slide(node.id, pinch.along, sign * max(node.dimensions.y, 0.3) * fraction)],
                    max(node.dimensions.y, 0.3) * fraction,
                )
            )
            if len(found) >= budget:
                return found[:budget]
    return found[:budget]


@traced("layout.screen")
def screen_layouts(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    targets: list[Finding],
    *,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    samples: int = DEFAULT_SAMPLES,
) -> ScreenReport:
    """Try many legal moves. Return the first that clears a targeted problem."""
    problems = [finding for finding in targets if finding.outcome == "problem"]
    baseline = assess(graph, scenario, measure, rules=rules, ledger=ledger)
    known = {finding.id for finding in baseline.problems}
    rejected: list[str] = []
    widths: list[float] = []
    measured = 0
    tried = 0

    for pinch in _pinches(problems, graph):
        for candidate in dense_candidates(pinch, samples):
            tried += 1
            rearranged = apply_moves(graph, candidate.moves)
            broken = violations(graph, rearranged)
            if broken:
                rejected.extend(item.kind for item in broken)
                continue
            measured += 1
            after = assess(rearranged, scenario, measure, rules=rules, ledger=ledger)
            if after.problems:
                worst = min(
                    (item.measured_inches for item in after.problems if item.measured_inches is not None),
                    default=None,
                )
                if worst is not None:
                    widths.append(worst)
            if _resolves(known, pinch.finding_id, after) and accepts(baseline, after):
                picked = ScreenedLayout(candidate=candidate, graph=rearranged, measured=measured)
                return ScreenReport(
                    samples=tried,
                    measured=measured,
                    rejected=tuple(dict.fromkeys(rejected)),
                    best=picked,
                    widths=tuple(widths),
                )
            if tried >= min(samples, MAX_SAMPLES):
                break
    return ScreenReport(
        samples=tried,
        measured=measured,
        rejected=tuple(dict.fromkeys(rejected)),
        widths=tuple(widths),
    )
