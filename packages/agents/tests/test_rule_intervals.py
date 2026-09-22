"""Contract 4 comparison semantics, run against the legal rules themselves.

The pipeline owns the arithmetic; these prove the rules' side of the seam:
each comparison head, equality at the threshold, interval straddling, missing
or one-sided uncertainty, and exact unit conversion. Unknown bounds must never
produce satisfied, however far the estimate sits from the limit.
"""

from __future__ import annotations

import pytest
from standardphysics_agents import load_pack
from standardphysics_agents.rules.intervals import (
    rule_verdict,
    rule_verdict_safe,
    verdict_for_estimate,
)

pack = load_pack()

ROUTE = pack.by_id("route_clear_width")          # at_least, 36 in
COUNTER = pack.by_id("service_counter_height")   # at_most, 36 in


def _bounds(estimate, low, high, unit="in"):
    from standardphysics_pipeline.primitives.uncertainty import MeasurementBounds

    return MeasurementBounds(
        estimate=estimate, low=low, high=high, unit=unit, method="test"
    )


class TestBothSides:
    def test_a_minimum_is_satisfied_by_anything_above_it(self):
        assert rule_verdict(_bounds(40.0, 37.0, 43.0), ROUTE) == "satisfied"

    def test_a_minimum_is_violated_by_anything_below_it(self):
        assert rule_verdict(_bounds(33.0, 30.0, 35.0), ROUTE) == "violation"

    def test_a_maximum_is_satisfied_by_anything_under_it(self):
        assert rule_verdict(_bounds(34.0, 32.0, 35.0), COUNTER) == "satisfied"

    def test_a_maximum_is_violated_by_anything_over_it(self):
        assert rule_verdict(_bounds(38.0, 37.0, 39.0), COUNTER) == "violation"


class TestEquality:
    """The sections are inclusive: "36 minimum" is met AT 36."""

    def test_exactly_the_minimum_satisfies(self):
        assert rule_verdict(_bounds(36.0, 36.0, 36.0), ROUTE) == "satisfied"

    def test_exactly_the_maximum_satisfies(self):
        assert rule_verdict(_bounds(36.0, 36.0, 36.0), COUNTER) == "satisfied"

    def test_float_hygiene_does_not_break_exact_equality(self):
        assert rule_verdict(
            _bounds(36.0, 35.9999999999, 36.0), ROUTE
        ) == "satisfied"


class TestThresholdStraddling:
    def test_a_minimum_straddled_asks_for_verification(self):
        assert rule_verdict(_bounds(36.0, 34.0, 38.0), ROUTE) == "needs_verification"

    def test_a_maximum_straddled_asks_for_verification(self):
        assert rule_verdict(_bounds(36.0, 34.0, 38.0), COUNTER) == "needs_verification"

    def test_inverted_bounds_never_conclude(self):
        assert rule_verdict(_bounds(36.0, 39.0, 33.0), ROUTE) == "needs_verification"


class TestMissingUncertainty:
    def test_no_bounds_at_all_asks_for_verification(self):
        from standardphysics_pipeline.primitives.uncertainty import unknown_bounds

        far_away = unknown_bounds(60.0, "in", "test")
        assert rule_verdict(far_away, ROUTE) == "needs_verification"

    def test_a_lower_bound_alone_never_concludes(self):
        assert rule_verdict(_bounds(40.0, 37.0, None), ROUTE) == "needs_verification"

    def test_an_upper_bound_alone_never_concludes(self):
        assert rule_verdict(_bounds(40.0, None, 43.0), ROUTE) == "needs_verification"

    def test_the_estimate_is_not_a_bound(self):
        assert verdict_for_estimate(100.0, ROUTE) == "needs_verification"


class TestMixedUnits:
    def test_metres_convert_exactly(self):
        # 0.9144 m is 36 inches by the exact legal factor.
        assert rule_verdict(_bounds(0.9144, 0.9144, 0.9144, unit="m"), ROUTE) == "satisfied"

    def test_millimetres_convert_exactly(self):
        assert rule_verdict(
            _bounds(914.4, 914.4, 930.0, unit="mm"), ROUTE
        ) == "satisfied"

    def test_centimetres_violate_through_conversion(self):
        # 94 cm is over 36 in, and even its lower bound clears the limit.
        assert rule_verdict(
            _bounds(96.0, 94.0, 98.0, unit="cm"), COUNTER
        ) == "violation"

    def test_an_unknown_unit_asks_for_verification(self):
        from standardphysics_pipeline.primitives.uncertainty import unknown_bounds

        assert rule_verdict_safe(unknown_bounds(3.0, "cubits", "test"), ROUTE) == "needs_verification"

    def test_a_known_unit_with_missing_bounds_stays_unknown(self):
        assert rule_verdict(_bounds(1.0, None, None, unit="m"), ROUTE) == "needs_verification"


class TestNonfiniteInputsNeverConclude:
    """A mutated record must not turn into a compliance verdict."""

    @staticmethod
    def _mutated(**updates):
        from standardphysics_pipeline.primitives.uncertainty import MeasurementBounds

        record = MeasurementBounds(
            estimate=1.0, low=1.0, high=1.0, unit="m", method="test"
        )
        for field, value in updates.items():
            record.__dict__[field] = value
        return record

    def test_nan_low_straddling_a_maximum_never_satisfies(self):
        wrecked = self._mutated(low=float("nan"))
        assert rule_verdict(wrecked, COUNTER) == "needs_verification"

    def test_nan_high_straddling_a_minimum_never_satisfies(self):
        wrecked = self._mutated(high=float("nan"))
        assert rule_verdict(wrecked, ROUTE) == "needs_verification"

    def test_infinite_bounds_never_satisfy(self):
        wrecked = self._mutated(low=float("inf"), high=float("inf"))
        assert rule_verdict(wrecked, ROUTE) == "needs_verification"

    def test_a_nan_estimate_never_concludes(self):
        assert verdict_for_estimate(float("nan"), ROUTE) == "needs_verification"

    def test_nan_bounds_through_the_convenience_path(self):
        assert (
            verdict_for_estimate(36.0, ROUTE, bounds=(float("nan"), 40.0))
            == "needs_verification"
        )
