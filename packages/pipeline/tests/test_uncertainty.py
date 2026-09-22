"""Contract 4 measurement bounds: unknown stays unknown, no noise-passes.

The verdict table is the contract's, verified from both sides of every
boundary. These are number semantics, not a scan: no fixture becomes a
physical accuracy claim.
"""

import math

import pytest
from pydantic import ValidationError
from standardphysics_pipeline.primitives.uncertainty import (
    MeasurementBounds,
    combine,
    compare,
    from_controls,
    unknown_bounds,
)


def bounds(low, high, estimate=None, unit="in", method="test"):
    return MeasurementBounds(
        estimate=estimate if estimate is not None else (low + high) / 2,
        low=low, high=high, unit=unit, method=method,
    )


class TestUnknownNeverPasses:
    @pytest.mark.parametrize("head", ["min", "max"])
    @pytest.mark.parametrize("inclusive", [True, False])
    def test_unknown_bounds_cannot_pass_or_fail(self, head, inclusive):
        unknown = unknown_bounds(estimate=99.0, unit="in", method="roomplan_extents")
        assert compare(unknown, 36.0, head, inclusive=inclusive) == "needs_verification"

    @pytest.mark.parametrize("head", ["min", "max"])
    def test_a_half_missing_bound_cannot_pass(self, head):
        low_only = MeasurementBounds(
            estimate=40.0, low=39.0, high=None, unit="in", method="test")
        assert compare(low_only, 36.0, head) == "needs_verification"
        high_only = MeasurementBounds(
            estimate=40.0, low=None, high=41.0, unit="in", method="test")
        assert compare(high_only, 36.0, head) == "needs_verification"

    def test_an_inverted_interval_is_never_a_verdict(self):
        inverted = bounds(41.0, 39.0)
        assert compare(inverted, 40.0, "min") == "needs_verification"
        assert compare(inverted, 40.0, "max") == "needs_verification"


class TestMinimum:
    def test_exclusive_needs_strict_margins(self):
        assert compare(bounds(36.1, 36.5), 36.0, "min") == "satisfied"
        assert compare(bounds(35.5, 35.9), 36.0, "min") == "violation"
        assert compare(bounds(35.9, 36.4), 36.0, "min") == "needs_verification"

    def test_exclusive_equality_is_not_a_pass(self):
        assert compare(bounds(36.0, 36.4), 36.0, "min") == "needs_verification"
        assert compare(bounds(35.5, 36.0), 36.0, "min") == "violation"

    def test_inclusive_equality_passes(self):
        assert compare(bounds(36.0, 36.4), 36.0, "min", inclusive=True) == "satisfied"
        assert compare(bounds(35.5, 36.0), 36.0, "min", inclusive=True) == "needs_verification"
        assert compare(bounds(35.5, 35.9), 36.0, "min", inclusive=True) == "violation"

    def test_the_tolerance_band_resolves_equality_only(self):
        assert compare(bounds(35.94, 36.4), 36.0, "min", inclusive=True, eps=0.125) == "satisfied"
        assert compare(bounds(35.94, 36.4), 36.0, "min", eps=0.125) == "needs_verification"
        assert compare(bounds(35.5, 35.94), 36.0, "min", inclusive=True, eps=0.125) == "needs_verification"

    def test_a_far_away_estimate_does_not_beat_unknown_bounds(self):
        assert compare(unknown_bounds(estimate=100.0, unit="in", method="roomplan_extents"),
                       36.0, "min") == "needs_verification"


class TestMaximum:
    def test_exclusive_needs_strict_margins(self):
        assert compare(bounds(1.0, 1.9), 2.0, "max") == "satisfied"
        assert compare(bounds(2.1, 2.5), 2.0, "max") == "violation"
        assert compare(bounds(1.9, 2.4), 2.0, "max") == "needs_verification"

    def test_inclusive_equality_passes(self):
        assert compare(bounds(1.0, 2.0), 2.0, "max", inclusive=True) == "satisfied"
        assert compare(bounds(2.0, 2.4), 2.0, "max", inclusive=True) == "needs_verification"
        assert compare(bounds(2.1, 2.5), 2.0, "max", inclusive=True) == "violation"

    def test_exclusive_equality_does_not_pass(self):
        assert compare(bounds(1.0, 2.0), 2.0, "max") == "needs_verification"
        assert compare(bounds(2.0, 2.4), 2.0, "max") == "violation"

    def test_the_tolerance_band_resolves_equality_only(self):
        assert compare(bounds(1.0, 2.06), 2.0, "max", inclusive=True, eps=0.125) == "satisfied"
        assert compare(bounds(1.0, 2.06), 2.0, "max", eps=0.125) == "needs_verification"


class TestCombiningAndWidening:
    def test_sums_are_conservative_and_keep_units_matched(self):
        left = bounds(1.0, 1.5, method="wall_fit")
        right = bounds(0.5, 1.0, method="wall_fit")
        total = combine(left, right)
        assert total.estimate == pytest.approx(2.0)
        assert (total.low, total.high) == pytest.approx((1.5, 2.5))

    def test_an_unknown_addend_keeps_the_sum_unknown(self):
        total = combine(bounds(1.0, 1.5), unknown_bounds(2.0, unit="in", method="x"))
        assert total.low is None and total.high is None
        assert total.estimate == pytest.approx(3.25)

    def test_units_must_agree(self):
        with pytest.raises(ValueError):
            combine(bounds(1.0, 1.5, unit="in"), bounds(1.0, 1.5, unit="m"))

    def test_widening_grows_bounds_but_never_creates_them(self):
        widened = bounds(2.0, 3.0).widened(0.25, 0.5)
        assert (widened.low, widened.high) == pytest.approx((1.75, 3.5))
        unknown = unknown_bounds(2.0, unit="in", method="x").widened(0.25, 0.5)
        assert unknown.low is None and unknown.high is None

    def test_negative_margins_are_refused(self):
        with pytest.raises(ValueError):
            bounds(2.0, 3.0).widened(-0.1, 0.1)

    def test_provenance_survives_widening_and_combining(self):
        control = from_controls(36.0, "in", 35.9, 36.2, controls=3, method="taperule")
        widened = control.widened(0.1, 0.1)
        assert widened.method == "taperule"
        assert widened.controls == 3
        total = combine(bounds(1.0, 1.5, method="wall_fit"), control)
        assert total.method == "wall_fit + taperule"
        assert total.controls == 3


class TestFiniteArithmetic:
    """Supervisor defect 2026-09-22T01:24Z: NaN/Inf never produces a verdict."""

    def test_nan_low_bound_is_refused_not_satisfied(self):
        with pytest.raises(ValidationError):
            MeasurementBounds(estimate=1.0, low=float("nan"), high=1.0, unit="m", method="x")

    def test_nan_high_bound_is_refused_not_satisfied(self):
        with pytest.raises(ValidationError):
            MeasurementBounds(estimate=1.0, low=1.0, high=float("nan"), unit="m", method="x")

    def test_infinite_bounds_are_refused_not_satisfied(self):
        with pytest.raises(ValidationError):
            MeasurementBounds(estimate=1.0, low=float("inf"), high=float("inf"), unit="m", method="x")

    def test_nan_estimate_is_refused(self):
        with pytest.raises(ValidationError):
            MeasurementBounds(estimate=float("nan"), low=0.9, high=1.0, unit="m", method="x")

    def test_nan_limit_or_tolerance_is_refused(self):
        fine = bounds(0.9, 1.0)
        with pytest.raises(ValueError):
            compare(fine, float("nan"), "min")
        with pytest.raises(ValueError):
            compare(fine, 1.0, "min", eps=float("inf"))
        with pytest.raises(ValueError):
            compare(fine, float("inf"), "max")

    def test_widened_refuses_nonfinite_margins(self):
        fine = bounds(0.9, 1.0)
        with pytest.raises(ValueError):
            fine.widened(float("nan"), 0.0)
        with pytest.raises(ValueError):
            fine.widened(0.0, float("nan"))
        with pytest.raises(ValueError):
            fine.widened(0.0, float("inf"))
        with pytest.raises(ValueError):
            fine.widened(float("-inf"), 0.0)

    def test_widened_output_is_revalidated(self):
        """Supervisor 2026-09-22T01:37Z reproduction: widened must never emit
        a NaN bound that compare() then turns into satisfied."""
        fine = bounds(0.9, 1.0)
        grown = fine.widened(0.1, 0.2)
        assert math.isfinite(grown.low) and math.isfinite(grown.high)
        assert compare(grown, 1.1, "max", inclusive=True) in ("needs_verification", "violation", "satisfied")

    def test_compare_never_satisfies_mutated_nonfinite_bounds(self):
        """compare() is the last line of defence, even for attribute-mutated
        models that skipped validation."""
        fine = bounds(0.9, 1.0)
        fine.low = float("nan")
        assert compare(fine, 1.1, "max", inclusive=True) == "needs_verification"
        fine.low = 0.9
        fine.high = float("nan")
        assert compare(fine, 0.8, "min") == "needs_verification"
        fine.high = float("inf")
        assert compare(fine, 0.8, "min") == "needs_verification"


class TestScenarioShapes:
    def test_counter_height_minimum_contract_shape(self):
        """36 in minimum, inclusive: the near-threshold shapes the contract names."""
        assert compare(bounds(36.1, 36.5), 36.0, "min", inclusive=True) == "satisfied"
        assert compare(bounds(35.5, 35.9), 36.0, "min", inclusive=True) == "violation"
        assert compare(bounds(35.9, 36.4), 36.0, "min", inclusive=True) == "needs_verification"

    def test_a_wider_uncertain_interval_must_not_pass(self):
        """A wider interval straddling the limit cannot sneak through."""
        assert compare(bounds(30.0, 42.0), 36.0, "min", inclusive=True) == "needs_verification"
        assert compare(bounds(30.0, 42.0), 36.0, "min") == "needs_verification"
