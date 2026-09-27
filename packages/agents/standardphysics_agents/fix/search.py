"""The bounded search for an arrangement that works.

Guess, check the hard constraints, measure, keep it if the finding cleared and
nothing else broke. The order of those four steps matters: measuring a
candidate that puts a display case inside a wall wastes the measurement, and
accepting one without re-measuring would be trusting arithmetic over geometry.

Keeping the owner's furniture is a hard default. When the search runs out, the
answer is never that it cannot be done — it is one specific thing the owner
could allow, tested first so the offer is real.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from standardphysics_contracts import (
    Finding,
    MeasurementProvider,
    NodeMove,
    Proposal,
    Scenario,
    SceneGraph,
    graph_hash,
    to_inches,
)
from standardphysics_contracts.rules import Tier

from ..assess import Pass, assess
from ..copy import no_arrangement, proposal_rationale, relaxation_question
from ..hashing import inventory
from ..rules import AgentRulePack, VerificationLedger
from ..tracing import traced
from .constraints import violations
from .moves import apply_moves, unlocked, without
from .pinch import Pinch, pinch_from
from .placement import placements
from .strategies import Candidate, candidates
from .surfaces import SurfaceMove, lower_surface_moves

PROPOSAL_NAMESPACE = uuid.UUID("7b3c1f04-5e2a-4c6b-9d18-000000000004")

CANDIDATE_LIMIT = 24
SET_DOWN_LIMIT = 6

RELAXATION_LIMIT = 6
"""A smaller ladder when testing whether a relaxation would even help."""

RelaxationKind = Literal["unlock", "set_aside"]
CandidateRejection = Callable[[SceneGraph, SceneGraph], str | None]


@dataclass(frozen=True)
class Relaxation:
    kind: RelaxationKind
    node_ids: tuple[UUID, ...]
    labels: tuple[str, ...]
    question: str


@dataclass(frozen=True)
class FixOutcome:
    proposal: Proposal | None = None
    graph: SceneGraph | None = None
    measured: int = 0
    """How many candidates survived the constraints and got measured."""

    rejected: tuple[str, ...] = ()
    """Which hard constraints the discarded candidates ran into."""

    message: str = ""
    relaxation: Relaxation | None = None
    targets: tuple[UUID, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        return self.proposal is not None


def proposal_id(base: str, moves: list[NodeMove]) -> uuid.UUID:
    """The same layout and the same moves always get the same proposal id."""
    shape = "|".join(
        f"{m.node_id}:{m.delta_translation.x:.4f},{m.delta_translation.y:.4f},"
        f"{m.delta_rotation_z_degrees:.1f}"
        for m in moves
    )
    return uuid.uuid5(PROPOSAL_NAMESPACE, f"{base}|{shape}")


def _moved_inches(moves: list[NodeMove]) -> float:
    return to_inches(
        sum(
            (m.delta_translation.x**2 + m.delta_translation.y**2) ** 0.5
            for m in moves
        )
    )


def _labels(graph: SceneGraph, moves: list[NodeMove]) -> list[str]:
    found = []
    for move in moves:
        try:
            found.append(graph.by_id(move.node_id).label)
        except KeyError:
            continue
    return found


def _build_proposal(
    graph: SceneGraph,
    candidate: SceneGraph,
    picked: Candidate,
    targets: tuple[UUID, ...],
) -> Proposal:
    base = graph_hash(graph)
    return Proposal(
        id=proposal_id(base, picked.moves),
        base_graph_hash=base,
        moves=picked.moves,
        targets=list(targets),
        rationale=proposal_rationale(
            picked.strategy, _labels(graph, picked.moves), _moved_inches(picked.moves)
        ),
        inventory_before=inventory(graph),
        inventory_after=inventory(candidate),
    )


def _resolves(baseline: set[UUID], target: UUID, after: Pass) -> bool:
    """Cleared the finding, and broke nothing that was working."""
    problems = {finding.id for finding in after.problems}
    return target not in problems and not (problems - baseline)


def _pinches(findings: list[Finding], graph: SceneGraph) -> list[Pinch]:
    found = [pinch_from(finding, graph) for finding in findings]
    fixable = [pinch for pinch in found if pinch is not None]
    return sorted(fixable, key=lambda pinch: -pinch.deficit_meters)


@dataclass
class _Search:
    """One run of the ladder against one layout."""

    graph: SceneGraph
    scenario: Scenario
    measure: MeasurementProvider
    rules: AgentRulePack
    ledger: VerificationLedger
    max_tier: Tier
    baseline: Pass
    candidate_rejection: CandidateRejection | None = None
    deadline: float = math.inf
    """`time.monotonic()` past which no more candidates are measured."""
    measured: int = 0
    rejected: list[str] = field(default_factory=list)

    def run(self, pinch: Pinch, limit: int) -> tuple[Candidate, SceneGraph] | None:
        return self.check(pinch.finding_id, candidates(pinch, limit))

    def set_down(self, finding: Finding) -> tuple[Candidate, SceneGraph] | None:
        """An item that sits too high, carried to a lower surface: no floor space opens, so no pinch exists.

        The nearest spot on each surface comes first, then the next nearest, and
        only SET_DOWN_LIMIT are measured: neighbouring spots on one surface almost
        always measure alike, and each costs a full assessment.
        """
        spots = _surface_by_surface(lower_surface_moves(self.graph, finding))[:SET_DOWN_LIMIT]
        return self.check(finding.id, (move.candidate for move in spots))

    def check(self, finding_id: UUID, guesses: Iterable[Candidate]) -> tuple[Candidate, SceneGraph] | None:
        from ..evaluation.gate import accepts

        known = {finding.id for finding in self.baseline.problems}
        for candidate in guesses:
            if time.monotonic() > self.deadline:
                return None
            rearranged = apply_moves(self.graph, candidate.moves)
            broken = violations(self.graph, rearranged)
            if broken:
                self.rejected.extend(item.kind for item in broken)
                continue
            self.measured += 1
            after = assess(
                rearranged,
                self.scenario,
                self.measure,
                rules=self.rules,
                ledger=self.ledger,
                max_tier=self.max_tier,
            )
            if not _resolves(known, finding_id, after) or not accepts(self.baseline, after):
                continue
            if self.candidate_rejection is not None:
                reason = self.candidate_rejection(self.graph, rearranged)
                if reason:
                    self.rejected.append(reason)
                    continue
            return candidate, rearranged
        return None


@traced("fix.propose")
def propose_fix(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    targets: list[Finding],
    *,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    baseline: Pass | None = None,
    max_tier: Tier = 1,
    limit: int = CANDIDATE_LIMIT,
    offer_relaxation: bool = True,
    candidate_rejection: CandidateRejection | None = None,
    deadline: float = math.inf,
) -> FixOutcome:
    """One arrangement that clears a named finding, or one thing to ask about.

    Past `deadline`, a `time.monotonic()` value, nothing more is measured and
    the search reports what it found by then.
    """
    problems = [finding for finding in targets if finding.outcome == "problem"
                and rules.by_id(finding.check_id).rearrangeable]
    before = baseline or assess(
        graph, scenario, measure, rules=rules, ledger=ledger, max_tier=max_tier
    )
    target_ids = tuple(finding.id for finding in problems)

    search = _Search(
        graph, scenario, measure, rules, ledger, max_tier,
        baseline=before, candidate_rejection=candidate_rejection, deadline=deadline,
    )
    for pinch in _pinches(problems, graph):
        result = search.run(pinch, limit)
        if result is None and limit > 0 and time.monotonic() <= deadline:
            finding = next(f for f in problems if f.id == pinch.finding_id)
            result = search.check(pinch.finding_id, placements(graph, pinch, finding, rules, limit * 4))
        if result is not None:
            return _found(graph, search, result, target_ids)
    for finding in problems:
        result = search.set_down(finding) if time.monotonic() <= deadline else None
        if result is not None:
            return _found(graph, search, result, target_ids)

    relaxation = (
        _find_relaxation(
            graph, scenario, measure, problems,
            rules=rules, ledger=ledger, max_tier=max_tier, baseline=before,
            candidate_rejection=candidate_rejection,
        )
        if offer_relaxation
        else None
    )
    return FixOutcome(
        measured=search.measured,
        rejected=tuple(dict.fromkeys(search.rejected)),
        message=no_arrangement(relaxation.question if relaxation else None),
        relaxation=relaxation,
        targets=target_ids,
    )


def _surface_by_surface(moves: list[SurfaceMove]) -> list[SurfaceMove]:
    """Nearest-first moves reordered so each surface's nearest spot comes before any surface's second."""
    rank: dict[UUID, int] = {}
    ordered = []
    for index, move in enumerate(moves):
        turn = rank.get(move.surface.id, 0)
        rank[move.surface.id] = turn + 1
        ordered.append((turn, index, move))
    return [move for _, _, move in sorted(ordered, key=lambda entry: entry[:2])]


def _found(graph: SceneGraph, search: _Search, result: tuple[Candidate, SceneGraph],
           target_ids: tuple[UUID, ...]) -> FixOutcome:
    picked, rearranged = result
    proposal = _build_proposal(graph, rearranged, picked, target_ids)
    return FixOutcome(
        proposal=proposal,
        graph=rearranged,
        measured=search.measured,
        rejected=tuple(dict.fromkeys(search.rejected)),
        message=proposal.rationale,
        targets=target_ids,
    )


def _relaxation(kind: RelaxationKind, nodes) -> Relaxation:
    labels = tuple(node.label for node in nodes)
    return Relaxation(
        kind=kind,
        node_ids=tuple(node.id for node in nodes),
        labels=labels,
        question=relaxation_question(kind, list(labels)),
    )


def _find_relaxation(
    graph: SceneGraph,
    scenario: Scenario,
    measure: MeasurementProvider,
    problems: list[Finding],
    *,
    rules: AgentRulePack,
    ledger: VerificationLedger,
    max_tier: Tier,
    baseline: Pass,
    candidate_rejection: CandidateRejection | None,
) -> Relaxation | None:
    """One thing the owner could allow, tested before it is offered.

    Unlocking something comes first, because it keeps every piece of furniture.
    Setting a piece aside is only offered when nothing else works, and it is a
    question rather than a proposal: inventory is not ours to change.
    """
    for pinch in _pinches(problems, graph):
        found = _try_unlocking(
            graph, scenario, measure, pinch,
            rules=rules, ledger=ledger, max_tier=max_tier, baseline=baseline,
            candidate_rejection=candidate_rejection,
        ) or _try_setting_aside(
            graph, scenario, measure, pinch,
            rules=rules, ledger=ledger, max_tier=max_tier, baseline=baseline,
            candidate_rejection=candidate_rejection,
        )
        if found:
            return found
    return None


def _try_unlocking(
    graph, scenario, measure, pinch, *, rules, ledger, max_tier, baseline,
    candidate_rejection,
) -> Relaxation | None:
    for node in pinch.fixed:
        opened = unlocked(graph, [node.id])
        target = next(
            (f for f in baseline.problems if f.id == pinch.finding_id), None
        )
        if target is None:
            continue
        outcome = propose_fix(
            opened, scenario, measure, [target],
            rules=rules, ledger=ledger, baseline=baseline,
            max_tier=max_tier, limit=RELAXATION_LIMIT, offer_relaxation=False,
            candidate_rejection=candidate_rejection,
        )
        if outcome.found:
            return _relaxation("unlock", [node])
    return None


def _try_setting_aside(
    graph, scenario, measure, pinch, *, rules, ledger, max_tier, baseline,
    candidate_rejection,
) -> Relaxation | None:
    from ..evaluation.gate import accepts

    known = {finding.id for finding in baseline.problems}
    for node in pinch.movable:
        candidate = without(graph, [node.id])
        after = assess(
            candidate, scenario, measure,
            rules=rules, ledger=ledger, max_tier=max_tier,
        )
        rejected = (
            candidate_rejection(graph, candidate)
            if candidate_rejection is not None
            else None
        )
        if _resolves(known, pinch.finding_id, after) and accepts(baseline, after) and not rejected:
            return _relaxation("set_aside", [node])
    return None
