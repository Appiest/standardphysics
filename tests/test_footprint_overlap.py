"""How much of one footprint lies inside another, measured exactly rather than on the occupancy grid."""

import pytest
from standardphysics_pipeline.footprints import covered_fraction


def square(x0, y0, side):
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]


def test_disjoint_footprints_cover_nothing():
    assert covered_fraction(square(0, 0, 1), square(2, 0, 1)) == 0.0


def test_footprints_that_only_touch_cover_nothing():
    assert covered_fraction(square(0, 0, 1), square(1, 0, 1)) == pytest.approx(0.0)


def test_a_footprint_inside_another_is_wholly_covered_but_not_the_reverse():
    assert covered_fraction(square(0.25, 0.25, 0.5), square(0, 0, 1)) == pytest.approx(1.0)
    assert covered_fraction(square(0, 0, 1), square(0.25, 0.25, 0.5)) == pytest.approx(0.25)


def test_half_overlap_is_measured_exactly():
    assert covered_fraction(square(0, 0, 1), square(0.5, 0, 1)) == pytest.approx(0.5)


def test_a_rotated_footprint_is_clipped_by_its_real_outline():
    diamond = [(0.5, -0.5), (1.5, 0.5), (0.5, 1.5), (-0.5, 0.5)]
    assert covered_fraction(square(0, 0, 1), diamond) == pytest.approx(1.0)
    assert covered_fraction(diamond, square(0, 0, 1)) == pytest.approx(0.5)


def test_a_degenerate_footprint_covers_nothing():
    assert covered_fraction([(0, 0), (1, 0), (2, 0)], square(0, 0, 2)) == 0.0
