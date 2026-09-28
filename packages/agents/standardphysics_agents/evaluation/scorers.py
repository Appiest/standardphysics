"""How a case is scored.

Every scorer returns `None` when the case has nothing to say about it, and the
runner leaves those out of the mean rather than counting them as zero. A shop
with no doorway should not drag down door accuracy.

`measurement_error_in` is an error, not a score. Lower is better, and the gate
reads it that way.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from standardphysics_contracts import Decision

from ..assess import Pass
from ..checks import roles
from ..fix.search import FixOutcome
from ..router.decision import Rejected
from .dataset import Case
from .gate import GateResult


def _entrance_ids(graph) -> set[UUID]:
    door = roles.entrance(graph)
    return {door.id} if door else set()


ROLE_FINDERS = {
    "service_counter": lambda graph: {n.id for n in roles.service_counters(graph)},
    "entrance": _entrance_ids,
}


@dataclass(frozen=True)
class CaseOutcome:
    case: Case
    result: Pass
    decision: Decision | Rejected | None = None
    fix: FixOutcome | None = None
    after: Pass | None = None
    gate: GateResult | None = None
    error: str | None = None

    @property
    def reported_problems(self) -> set[str]:
        return {finding.check_id for finding in self.result.problems}

    @property
    def reported_questions(self) -> set[str]:
        return {finding.check_id for finding in self.result.questions}


Scorer = Callable[[CaseOutcome], float | None]


def _share(part: AbstractSet, whole: AbstractSet) -> float:
    return len(part) / len(whole)


def finding_precision(outcome: CaseOutcome) -> float | None:
    """Of the problems reported, the share the case expected."""
    reported = outcome.reported_problems
    if not reported:
        return None
    return _share(reported & outcome.case.expected_problems, reported)


def finding_recall(outcome: CaseOutcome) -> float | None:
    """Of the problems the case expected, the share reported."""
    expected = outcome.case.expected_problems
    if not expected:
        return None
    return _share(expected & outcome.reported_problems, expected)


def question_recall(outcome: CaseOutcome) -> float | None:
    """Every shop gets asked the same five things, plus anything unsure."""
    expected = outcome.case.expected_questions
    if not expected:
        return None
    return _share(expected & outcome.reported_questions, expected)


MISSING_MEASUREMENT_INCHES = 120.0
"""What a dimension the case expected and nobody reported counts as.

Ten feet of error, so a check that went quiet cannot score better than one that
answered badly.
"""


def measurement_error_in(outcome: CaseOutcome) -> float | None:
    """Mean inches between what was measured and what the case says is there.

    A check can report several measurements on one shop, one per leg, and a
    case names a particular dimension. So this scores the reported measurement
    nearest the expected one: whether the tightest thing in the room was found
    is what precision and recall are for, and this asks whether the dimension
    the case is about came out right.
    """
    expected = outcome.case.expected_inches
    if not expected:
        return None
    errors = [
        _error(outcome, check_id, inches) for check_id, inches in expected.items()
    ]
    return sum(errors) / len(errors)


def _error(outcome: CaseOutcome, check_id: str, expected: float) -> float:
    reported = _measurements(outcome, check_id)
    if not reported:
        return MISSING_MEASUREMENT_INCHES
    return min(abs(measured - expected) for measured in reported)


def _measurements(outcome: CaseOutcome, check_id: str) -> list[float]:
    return [
        finding.measured_inches
        for finding in outcome.result.findings
        if finding.check_id == check_id and finding.measured_inches is not None
    ]


def label_accuracy(outcome: CaseOutcome) -> float | None:
    """Whether a check attached to the thing the rule is about.

    A counter Astra called a cabinet is the case that matters. 904.4.1 has
    nothing to run against, and a check that guessed anyway would be measuring
    a piece of furniture chosen at random.
    """
    expected = outcome.case.expected_roles
    if not expected:
        return None
    correct = sum(
        1
        for role, node_ids in expected.items()
        if _resolved(outcome, role) == set(node_ids)
    )
    return correct / len(expected)


def _resolved(outcome: CaseOutcome, role: str) -> set[UUID]:
    finder = ROLE_FINDERS.get(role)
    return finder(outcome.case.graph) if finder else set()


def router_action_match(outcome: CaseOutcome) -> float | None:
    """Whether the router picked the action the case calls for."""
    if outcome.case.expected_action is None or outcome.decision is None:
        return None
    if isinstance(outcome.decision, Rejected):
        return 0.0
    return 1.0 if outcome.decision.action == outcome.case.expected_action else 0.0


def fix_resolves_finding(outcome: CaseOutcome) -> float | None:
    """Whether a rearrangement actually cleared what it targeted."""
    if not outcome.case.fix_should_resolve or outcome.fix is None:
        return None
    if not outcome.fix.found or outcome.gate is None:
        return 0.0
    return 1.0 if outcome.gate.accepted else 0.0


SCORERS: dict[str, Scorer] = {
    "finding_precision": finding_precision,
    "finding_recall": finding_recall,
    "question_recall": question_recall,
    "measurement_error_in": measurement_error_in,
    "label_accuracy": label_accuracy,
    "router_action_match": router_action_match,
    "fix_resolves_finding": fix_resolves_finding,
}

LOWER_IS_BETTER = frozenset({"measurement_error_in"})
