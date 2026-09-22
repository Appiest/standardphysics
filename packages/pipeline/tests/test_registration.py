"""Registration helpers: repeatable fits, held-out checks, honest refusal."""

import math

import numpy as np
import pytest
from standardphysics_pipeline.registration import (
    AmbiguousRegistration,
    align_points,
    check_landmarks,
)


def moved(points, yaw_degrees, tx, ty):
    cos_t, sin_t = math.cos(math.radians(yaw_degrees)), math.sin(math.radians(yaw_degrees))
    return [(cos_t * x - sin_t * y + tx, sin_t * x + cos_t * y + ty) for x, y in points]


class TestExactFits:
    def test_recovers_a_nonidentity_yaw_and_translation(self):
        source = [(0.0, 0.0), (3.1, -0.4), (-1.2, 2.0), (2.0, 2.4), (0.3, -1.7)]
        target = moved(source, 37.0, 0.65, -0.85)
        fit = align_points(source, target, tolerance=0.01)
        assert fit.yaw == pytest.approx(math.radians(37.0), abs=1e-9)
        assert fit.translation == pytest.approx((0.65, -0.85), abs=1e-9)
        assert fit.residual_max == pytest.approx(0.0, abs=1e-9)

    def test_interior_points_are_recovered_too(self):
        source = [(0.0, 0.0), (2.0, 0.0), (0.0, 1.0), (1.0, 0.5)]
        target = moved(source, -41.0, 0.1, 0.2)
        fit = align_points(source, target, tolerance=0.01)
        for point, expected in zip(source, target):
            assert fit.apply(point) == pytest.approx(expected, abs=1e-9)

    def test_inverse_restores_the_original(self):
        source = [(0.0, 0.0), (3.0, 1.0), (-1.0, 2.0)]
        target = moved(source, 22.0, -0.4, 0.9)
        fit = align_points(source, target, tolerance=0.01)
        for point, expected in zip(source, target):
            assert fit.inverse().apply(fit.apply(point)) == pytest.approx(point, abs=1e-9)
            assert fit.inverse().apply(expected) == pytest.approx(point, abs=1e-9)


class TestHeldOut:
    def test_held_out_landmarks_never_enter_the_fit(self):
        fitted = [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)]
        target_fitted = moved(fitted, 15.0, 0.5, 0.5)
        fit = align_points(fitted, target_fitted, tolerance=0.01)

        held = [((1.0, 1.0), moved([(1.0, 1.0)], 15.0, 0.5, 0.5)[0]),
                ((-2.0, 0.5), moved([(-2.0, 0.5)], 15.0, 0.5, 0.5)[0])]
        report = check_landmarks(fit, held)
        assert report.count == 2
        assert report.residual_max == pytest.approx(0.0, abs=1e-9)
        assert report.conservative_bound(0.02) == pytest.approx(0.02, abs=1e-9)

    def test_a_poor_held_out_landmark_stays_in_the_report(self):
        fitted = [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)]
        fit = align_points(fitted, moved(fitted, 0.0, 0.0, 0.0), tolerance=0.01)
        held = [((1.0, 0.0), (1.0, 0.0)), ((-1.0, -1.0), (-1.0, -1.0)),
                ((2.5, 1.0), (4.0, 1.0))]
        report = check_landmarks(fit, held)
        assert report.count == 3
        assert report.residual_max == pytest.approx(1.5)

    def test_no_held_out_landmarks_is_a_refusal(self):
        fit = align_points(
            [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)],
            moved([(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)], 0.0, 0.0, 0.0),
            tolerance=0.01,
        )
        with pytest.raises(AmbiguousRegistration):
            check_landmarks(fit, [])


class TestRefusals:
    def test_two_correspondences_are_not_enough_to_verify(self):
        with pytest.raises(AmbiguousRegistration):
            align_points([(0.0, 0.0), (2.0, 0.0)], [(1.0, 1.0), (3.0, 1.0)], tolerance=0.01)

    def test_coincident_points_carry_no_direction(self):
        with pytest.raises(AmbiguousRegistration):
            align_points(
                [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
                [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)],
                tolerance=0.01,
            )

    def test_contradicting_captures_refuse_under_tolerance(self):
        source = [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)]
        target = moved(source, 0.0, 0.0, 0.0)
        target[1] = (target[1][0] + 0.6, target[1][1])
        with pytest.raises(AmbiguousRegistration, match="4x"):
            align_points(source, target, tolerance=0.01)

    def test_a_reflected_pairing_is_rejected_not_mirrored(self):
        source = [(0.0, 0.0), (3.0, 0.0), (0.0, 2.0)]
        target = [(0.0, 0.0), (0.0, 3.0), (2.0, 0.0)]  # mirror across the diagonal
        with pytest.raises(AmbiguousRegistration):
            align_points(source, target, tolerance=0.05)

    def test_fit_residual_within_tolerance_passes(self):
        rng = np.random.default_rng(3)
        source = [(float(x), float(y)) for x, y in rng.uniform(-2, 2, (6, 2))]
        target = moved(source, 12.0, 0.3, -0.2)
        noisy = [(qx + float(e), qy + float(e)) for (qx, qy), (e, _) in
                 zip(target, rng.normal(0, 0.003, (6, 2)))]
        fit = align_points(source, noisy, tolerance=0.02)
        assert fit.yaw == pytest.approx(math.radians(12.0), abs=1e-2)
        assert fit.residual_max < 0.08
