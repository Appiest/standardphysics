"""Conservative measurement bounds and limit comparisons, Contract 4 semantics.

A scan gives a best estimate, never an accuracy. These bounds record what is
known about the true value: a lower bound, an upper bound, or neither. Rules
compare bounds against limits, and the verdict rules are exactly the ones the
contract pins down:

- unknown or missing bounds can never produce satisfied or violation, however
  far the estimate sits from the limit;
- a straddling interval yields needs_verification;
- for a minimum, only an adequately supported lower bound above the
  requirement supports satisfied, and an upper bound below supports
  violation; reversed for maxima;
- equality follows the actual inclusive/exclusive rule, and the numerical
  epsilon a rule compares with is documented separately from measured
  accuracy and is never an uncertainty source.

Nothing here reads a model or a scan. It is arithmetic that stays honest when
the inputs are vague.
"""

from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

Limits = Literal["min", "max"]
Verdict = Literal["satisfied", "violation", "needs_verification"]


class MeasurementBounds(BaseModel):
    """What is conservatively known about a dimension, as absolute values.

    `low` and `high` bound the true value. Both None means the uncertainty is
    unknown: the number exists, its accuracy does not. None is never read as
    zero, and no caller may turn the estimate itself into a bound.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["bounds"] = "bounds"
    estimate: float
    """The best value the measurement produced, displayed as the number."""
    low: Optional[float] = None
    """Conservative lower bound; None means unknown, not infinite."""
    high: Optional[float] = None
    """Conservative upper bound; None means unknown, not infinite."""
    unit: str
    method: str
    """How the estimate was obtained: roomplan_extents, taperule, laser..."""
    source_revision: Optional[int] = None
    """The graph revision the estimate was read from, when it came from a scan."""
    controls: int = 0
    """Distinct field/control observations behind these bounds, if any."""

    @property
    def bounded(self) -> bool:
        return self.low is not None and self.high is not None

    @property
    def unknown(self) -> bool:
        return self.low is None and self.high is None

    def widened(self, lower: float, upper: float) -> MeasurementBounds:
        """Grow the interval by margins, keeping unknown unknown.

        Margins are absolute and non-negative. They push bounds outward but can
        never create a bound where there was none.
        """
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ValueError("margins must be finite numbers")
        if lower < 0 or upper < 0:
            raise ValueError("margins are magnitudes and cannot be negative")
        return MeasurementBounds(
            estimate=self.estimate,
            low=None if self.low is None else self.low - lower,
            high=None if self.high is None else self.high + upper,
            unit=self.unit,
            method=self.method,
            source_revision=self.source_revision,
            controls=self.controls,
        )


def unknown_bounds(
    estimate: float,
    unit: str,
    method: str,
    source_revision: Optional[int] = None,
) -> MeasurementBounds:
    """A measured number with no stated accuracy, which is honest, not zero."""
    return MeasurementBounds(
        estimate=estimate, low=None, high=None, unit=unit,
        method=method, source_revision=source_revision,
    )


def from_controls(
    estimate: float,
    unit: str,
    low: float,
    high: float,
    controls: int,
    method: str = "taperule",
) -> MeasurementBounds:
    """Bounds derived from independent field or check controls."""
    return MeasurementBounds(
        estimate=estimate, low=low, high=high, unit=unit,
        method=method, controls=max(controls, 0),
    )


def combine(*bounds: MeasurementBounds) -> MeasurementBounds:
    """Sum several dimensions conservatively, without inventing independence.

    Summing bounds is only sound for quantities whose errors may accumulate
    the same direction, which is the conservative reading the contract keeps.
    Units must agree. An unknown bound stays unknown and a combined record can
    never grow a bound no input had.
    """
    if not bounds:
        raise ValueError("nothing to combine")
    unit = bounds[0].unit
    if any(bound.unit != unit for bound in bounds):
        units = ", ".join(sorted({bound.unit for bound in bounds}))
        raise ValueError(f"cannot combine across units: {unit} vs {units}")
    estimate = sum(bound.estimate for bound in bounds)
    lows = [bound.low for bound in bounds]
    highs = [bound.high for bound in bounds]
    return MeasurementBounds(
        estimate=estimate,
        low=sum(lows) if all(low is not None for low in lows) else None,
        high=sum(highs) if all(high is not None for high in highs) else None,
        unit=unit,
        method=" + ".join(bound.method for bound in bounds),
        source_revision=max(
            (bound.source_revision for bound in bounds if bound.source_revision is not None),
            default=None,
        ),
        controls=sum(bound.controls for bound in bounds),
    )


def _clean_verdict(
    low: float,
    high: float,
    limit: float,
    eps: float,
    head: Limits,
    inclusive: bool,
) -> Verdict:
    """The eight-way table with finite bounds, limit and tolerance supplied."""
    if head == "min":
        if inclusive:
            satisfied = low >= limit - eps
            violation = high < limit - eps
        else:
            satisfied = low > limit + eps
            violation = high <= limit - eps
    else:
        if inclusive:
            satisfied = high <= limit + eps
            violation = low > limit + eps
        else:
            satisfied = high < limit - eps
            violation = low >= limit - eps
    if satisfied and violation:
        return "needs_verification"
    if satisfied:
        return "satisfied"
    if violation:
        return "violation"
    return "needs_verification"


def compare(
    bounds: MeasurementBounds,
    limit: float,
    head: Limits,
    *,
    inclusive: bool = False,
    eps: float = 0.0,
) -> Verdict:
    """The contract's verdict rules, with no shortcuts.

    Values within `eps` of the limit are indistinguishable from the limit
    itself, so equality goes with the requirement's own inclusive/exclusive
    reading; elsewhere strict separation decides. `eps` documents the rule's
    numerical comparison tolerance and is never an accuracy claim.
    """
    if eps < 0:
        raise ValueError("the numerical tolerance cannot be negative")
    if not math.isfinite(limit) or not math.isfinite(eps):
        raise ValueError("limit and tolerance must be finite numbers")

    if bounds.unknown or not bounds.bounded:
        return "needs_verification"

    low, high = bounds.low, bounds.high  # not None past this point
    assert low is not None and high is not None
    if low > high:
        return "needs_verification"
    if not math.isfinite(low) or not math.isfinite(high):
        return "needs_verification"

    return _clean_verdict(low, high, limit, eps, head, inclusive)
