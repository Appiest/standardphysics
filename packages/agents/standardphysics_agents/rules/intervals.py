"""Contract 4 limit comparisons, bound to the legal rules.

The pipeline's `primitives.uncertainty` holds the arithmetic: unknown bounds
never pass, intervals that straddle a limit need verification, and only a
supported bound on the right side of the limit can conclude. This module is
the rules' side of that contract: it translates a `RuleSpec` into the head and
comparison the section states, converts units exactly and visibly, and returns
the same three verdicts an assessment row can carry.

Nothing here guesses an uncertainty. A caller without bounds must pass the
pipeline's `unknown_bounds`, and the answer will be needs_verification, which
is the honest reading of "we measured it but we have no stated accuracy".
"""

from __future__ import annotations

import math
from typing import Literal

from standardphysics_contracts import to_inches
from standardphysics_pipeline.primitives.uncertainty import MeasurementBounds, compare
from standardphysics_pipeline.primitives.uncertainty import Verdict

from .pack import COMPARISON_EPSILON, RuleSpec

KNOWN_UNIT_METERS_PER_UNIT = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "mm": 1e-3,
    "millimetre": 1e-3,
    "millimeter": 1e-3,
    "millimeters": 1e-3,
    "cm": 1e-2,
    "centimeter": 1e-2,
    "centimeters": 1e-2,
}
"""Length units the scan side may speak in, as metres per unit.

Inches are the pack's native unit. Metres convert to inches by an exact legal
factor (1 in = 25.4 mm), so a conversion here adds no uncertainty of its own.
Anything not listed cannot be compared against an inch rule and must ask for
verification instead of pretending.
"""


def _to_inches(value: float | None, unit: str) -> float | None:
    if value is None:
        return None
    metres_per_unit = KNOWN_UNIT_METERS_PER_UNIT.get(unit.casefold())
    if metres_per_unit is None:
        if unit.casefold() == "in":
            return value
        raise UnknownUnit(unit)
    return to_inches(value * metres_per_unit)


class UnknownUnit(ValueError):
    """A measurement whose unit no exact conversion reaches inches."""


def _finite_or_none(value: float | None) -> bool:
    return value is None or math.isfinite(value)


def rule_verdict(bounds: MeasurementBounds, rule: RuleSpec) -> Verdict:
    """The contract's verdict for this measurement against this rule.

    `rule.comparison` picks the head: `at_least` is a minimum, `at_most` a
    maximum. Both are inclusive in the sections the pack ships ("36 inches
    minimum" is met AT 36), and the epsilon used is the pack's documented
    numerical-comparison tolerance, which represents float hygiene rather than
    measurement accuracy and is never mixed into a bound.

    Nonfinite numbers (NaN, infinities) in the measurement, its bounds or the
    rule's own threshold can never conclude: a mutated record must not turn
    into a compliance verdict.
    """
    if not math.isfinite(rule.threshold) or not math.isfinite(COMPARISON_EPSILON):
        return "needs_verification"
    if not (
        _finite_or_none(bounds.estimate)
        and _finite_or_none(bounds.low)
        and _finite_or_none(bounds.high)
    ):
        return "needs_verification"
    unit = bounds.unit.casefold()
    low = _to_inches(bounds.low, unit)
    high = _to_inches(bounds.high, unit)
    estimate = _to_inches(bounds.estimate, unit)

    head: Literal["min", "max"] = (
        "min" if rule.comparison == "at_least" else "max"
    )
    normalized = bounds.model_copy(
        update={"estimate": estimate, "low": low, "high": high, "unit": "in"}
    )
    return compare(
        normalized,
        rule.threshold,
        head,
        inclusive=True,
        eps=COMPARISON_EPSILON,
    )


def verdict_for_estimate(
    estimate: float,
    rule: RuleSpec,
    *,
    unit: str = "in",
    bounds: tuple[float | None, float | None] | None = None,
) -> Verdict:
    """A convenience for callers that hold a number and, maybe, its bounds.

    With `bounds` None the uncertainty stays unknown and the answer is
    needs_verification, never satisfied, no matter how far the estimate sits
    from the threshold. Nonfinite inputs are non-answers the same way.
    """
    if not math.isfinite(estimate):
        return "needs_verification"
    low, high = bounds if bounds is not None else (None, None)
    if not (_finite_or_none(low) and _finite_or_none(high)):
        return "needs_verification"
    if low is not None and high is not None and low > high:
        return "needs_verification"
    record = MeasurementBounds(
        estimate=estimate,
        low=low,
        high=high,
        unit=unit,
        method="caller_supplied",
    )
    try:
        return rule_verdict(record, rule)
    except UnknownUnit:
        return "needs_verification"


def rule_verdict_safe(bounds: MeasurementBounds, rule: RuleSpec) -> Verdict:
    """`rule_verdict`, but a unit nobody can convert is needs_verification."""
    try:
        return rule_verdict(bounds, rule)
    except UnknownUnit:
        return "needs_verification"
