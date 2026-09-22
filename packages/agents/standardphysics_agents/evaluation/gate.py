"""Whether a candidate layout is allowed to replace the one it came from.

Four conditions, and all four have to hold. Three of them are about not making
things worse, which sounds obvious until you watch a search that only counts
problems: dropping a check, losing a measurement, or moving a pinch somewhere
new all reduce the count.

- **completes**: no rule stopped being answerable
- **no lost coverage**: every check that answered before still answers
- **no new failures**: no problem appears that was not there before
- **strictly improves**: fewer problems, or the same number with less to make up

The fourth is what makes it a gate rather than a filter. A rearrangement that
changes nothing measurable is not an improvement, and accepting it would let
the loop declare progress forever.
"""

from __future__ import annotations

from dataclasses import dataclass

from standardphysics_contracts import Finding

from ..assess import Pass

UNMEASURED_SHORTFALL_INCHES = 120.0
"""What a problem with no measurement counts as, when comparing two layouts.

A route with no way through has no width to report. It is worse than any narrow
one, so it is scored as ten feet short: larger than any real shortfall a shop
can produce, which keeps "blocked" from looking like an improvement on "tight".
"""

MIN_MEANINGFUL_SHORTFALL_INCHES = 1.0
"""The smallest shortfall change that outruns the measurement's own noise.

The occupancy grid quantises at 25 mm, which is about an inch, and a move that
"wins" less than a grid cell has not improved anything measurable: it is the
same room with a different rounding. Accepting such a win lets the loop report
progress forever on numerical drift, so it must be refused.
"""


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reasons: tuple[str, ...]
    problems_before: int
    problems_after: int
    shortfall_before: float
    shortfall_after: float

    def __bool__(self) -> bool:
        return self.accepted


def _shortfall(finding: Finding) -> float:
    if finding.measured_inches is None or finding.required_inches is None:
        return UNMEASURED_SHORTFALL_INCHES
    return abs(finding.required_inches - finding.measured_inches)


def total_shortfall(result: Pass) -> float:
    """How many inches of change it would take to clear everything."""
    return sum(_shortfall(finding) for finding in result.problems)


def answered_checks(result: Pass) -> set[str]:
    """Which checks produced an answer, of any kind."""
    return {finding.check_id for finding in result.findings}


def _unevaluated(result: Pass) -> set[str]:
    return {gap.rule_id for gap in result.unevaluated}


def _failures(result: Pass) -> set:
    return {finding.id for finding in result.problems}


def _completeness(before: Pass, after: Pass) -> list[str]:
    new_gaps = _unevaluated(after) - _unevaluated(before)
    return [f"{rule_id} stopped being answerable" for rule_id in sorted(new_gaps)]


def _coverage(before: Pass, after: Pass) -> list[str]:
    lost = answered_checks(before) - answered_checks(after)
    reasons = [f"{check_id} stopped reporting" for check_id in sorted(lost)]
    answered = {f.id: f for f in before.findings if f.outcome != "question"}
    for finding in after.questions:
        if finding.id in answered:
            reasons.append(f"{finding.check_id} lost its measured answer")
    return reasons


def _new_failures(before: Pass, after: Pass) -> list[str]:
    appeared = _failures(after) - _failures(before)
    titles = {f.id: f.title for f in after.problems}
    return [f"new problem: {titles[finding_id]}" for finding_id in sorted(appeared, key=str)]


def _improvement(before: Pass, after: Pass) -> list[str]:
    if len(after.problems) < len(before.problems):
        return []
    if len(after.problems) > len(before.problems):
        return ["more problems than before"]
    after_short, before_short = total_shortfall(after), total_shortfall(before)
    if after_short <= before_short - MIN_MEANINGFUL_SHORTFALL_INCHES:
        return []
    if after_short < before_short:
        return ["improvement within measurement noise"]
    return ["nothing measurable changed"]


def _answered_unknowns(before: Pass, after: Pass) -> list[str]:
    """A question that turned into an answer without new evidence.

    Sliding furniture cannot produce a measurement the scan never took, so a
    finding that was a question (needs verification) may stay a question
    between one layout and the next, but never quietly become a pass or a
    problem. Letting it do so is the cheapest way for a candidate to look
    compliant, and the gate is where that has to be caught.
    """
    asked = {finding.id: finding for finding in before.questions}
    return [
        f"{finding.check_id} turned from a question into an answer"
        for finding in after.findings
        if finding.outcome != "question" and finding.id in asked
    ]


SAFETY = (_completeness, _coverage, _new_failures, _answered_unknowns)
"""Conditions about not making things worse. Always checked."""


def accepts(before: Pass, after: Pass, require_improvement: bool = True) -> GateResult:
    """`require_improvement` is off for something the owner asked for.

    A rearrangement the loop proposed has to earn its place, so a candidate
    that changes nothing measurable is refused. A rearrangement the owner asked
    for has already earned it — they want the seating at the back — and the
    gate's job there is only to stop it breaking something.
    """
    reasons: list[str] = []
    for condition in SAFETY:
        reasons.extend(condition(before, after))
    if require_improvement:
        reasons.extend(_improvement(before, after))
    return GateResult(
        accepted=not reasons,
        reasons=tuple(reasons),
        problems_before=len(before.problems),
        problems_after=len(after.problems),
        shortfall_before=total_shortfall(before),
        shortfall_after=total_shortfall(after),
    )
