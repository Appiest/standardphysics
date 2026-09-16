"""Axis and layout conversion. A flipped sign here is invisible until every
measurement is wrong, so these pin both.
"""

import pytest
from standardphysics_pipeline.coords import (
    dimensions_to_z_up,
    point_to_y_up,
    point_to_z_up,
    transform_from_arkit,
)


def arkit_translation(x: float, y: float, z: float) -> list[float]:
    """Column-major identity with translation in the fourth column."""
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def test_up_becomes_z():
    up = point_to_z_up(0.0, 1.0, 0.0)
    assert (up.x, up.y, up.z) == (0.0, 0.0, 1.0)


def test_forward_becomes_positive_y():
    """ARKit looks down -Z, which is forward, and forward is +Y for us."""
    forward = point_to_z_up(0.0, 0.0, -1.0)
    assert (forward.x, forward.y, forward.z) == (0.0, 1.0, 0.0)


def test_right_stays_right():
    assert point_to_z_up(1.0, 0.0, 0.0).x == 1.0


def test_point_conversion_round_trips():
    back = point_to_y_up(*point_to_z_up(1.0, 2.0, 3.0).as_tuple())
    assert (back.x, back.y, back.z) == (1.0, 2.0, 3.0)


def test_dimensions_swap_height_into_z():
    dims = dimensions_to_z_up(3.2, 1.1, 0.7)
    assert (dims.x, dims.y, dims.z) == (3.2, 0.7, 1.1)


def test_dimensions_stay_positive():
    dims = dimensions_to_z_up(0.6, 0.75, 0.6)
    assert all(v > 0 for v in dims.as_tuple())


def test_translation_comes_out_of_the_fourth_column():
    """Read column-major as row-major and every object lands at the origin."""
    mat = transform_from_arkit(arkit_translation(1.0, 2.0, 3.0))
    position = mat.position
    assert (position.x, position.y, position.z) == (1.0, -3.0, 2.0)


def test_identity_survives():
    mat = transform_from_arkit(arkit_translation(0.0, 0.0, 0.0))
    assert mat.m == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def test_rotation_about_arkit_up_becomes_rotation_about_our_z():
    """A quarter turn about ARKit's Y is a quarter turn about our Z."""
    columns = [0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1]
    mat = transform_from_arkit(columns)
    rows = [mat.m[i : i + 4] for i in range(0, 16, 4)]
    assert rows[2][2] == pytest.approx(1.0)
    assert rows[0][0] == pytest.approx(0.0)


def test_wrong_length_is_rejected():
    with pytest.raises(ValueError):
        transform_from_arkit([1, 0, 0, 0])
