"""Occupant profiles: named defaults, adjustable personal dimensions, swept sampling.

The profile is a screening assumption about a person. The two properties the
suite pins down are that adjusting it is explicit and never mutates a default,
and that sweeping the body along a path cannot tunnel through a thin obstacle
or accept a diagonal corner cut that endpoint distance alone would miss.
"""

from __future__ import annotations

import math
from uuid import uuid4

import pytest
from standardphysics_agents.fix.occupancy import (
    DEFAULT_OCCUPANTS,
    MANUAL_WHEELCHAIR,
    OCCUPANTS,
    HorizontalReach,
    OccupantProfile,
    ensure_spacing,
    occupant,
    resize,
)
from standardphysics_agents.mesh_collision import MeshCollisionIndex
from standardphysics_agents.rules import load_pack
from standardphysics_contracts import LidarMesh, LidarMeshPart, Vec3


def _mesh_collision(vertices, triangles) -> MeshCollisionIndex:
    mesh = LidarMesh(
        floorY=0,
        parts=[
            LidarMeshPart(
                id=uuid4(),
                transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                vertices=vertices,
                triangles=triangles,
            )
        ],
    )
    return MeshCollisionIndex(mesh)


def _thin_wall() -> MeshCollisionIndex:
    """A tall, paper-thin wall across the plan origin, spanning y in [-0.2, 0.2]."""
    return _mesh_collision(
        [
            0.0, 0.25, -0.2,
            0.0, 0.25, 0.2,
            0.0, 1.0, 0.2,
            0.0, 1.0, -0.2,
        ],
        [0, 1, 2, 0, 2, 3],
    )


class TestNamedDefaults:
    def test_every_named_profile_is_lookupable(self):
        for profile in DEFAULT_OCCUPANTS:
            assert occupant(profile.id) is profile

    def test_unknown_profile_names_the_choices(self):
        with pytest.raises(KeyError) as raised:
            occupant("a-person")
        assert "manual-wheelchair" in str(raised.value)

    def test_defaults_carry_body_turning_and_personal_reach(self):
        for profile in DEFAULT_OCCUPANTS:
            assert profile.body_width_inches > 0
            assert profile.body_length_inches > 0
            assert profile.turning_diameter_inches > 0
            assert profile.personal_reach_inches is not None

    def test_defaults_never_carry_a_horizontal_reach(self):
        """A hand-reach distance is a fact about the person. No default is
        allowed to invent one from the chair."""
        for profile in DEFAULT_OCCUPANTS:
            assert profile.horizontal_reach is None

    def test_personal_reach_is_an_assumption_not_a_rule_limit(self):
        """The default reach is a screening number about the person. Nothing
        in the shipped rule pack may move when it is adjusted."""
        before = load_pack()
        resize(MANUAL_WHEELCHAIR, personal_reach_inches=7.5)
        after = load_pack()
        assert before == after


class TestAdjustment:
    def test_resize_returns_a_new_profile_and_never_mutates_the_default(self):
        original = MANUAL_WHEELCHAIR
        adjusted = resize(original, body_width_inches=40.0)
        assert adjusted is not original
        assert adjusted.body_width_inches == 40.0
        assert original.body_width_inches == 26.0

    def test_unmentioned_dimensions_stay(self):
        adjusted = resize(MANUAL_WHEELCHAIR, turning_diameter_inches=90.0)
        assert adjusted.body_width_inches == MANUAL_WHEELCHAIR.body_width_inches
        assert adjusted.turning_diameter_inches == 90.0

    def test_personal_reach_can_be_dropped_to_not_assumed(self):
        adjusted = resize(MANUAL_WHEELCHAIR, personal_reach_inches=None)
        assert adjusted.personal_reach_inches is None

    @pytest.mark.parametrize(
        "field",
        ["body_width_inches", "body_length_inches", "turning_diameter_inches"],
    )
    def test_non_positive_body_dimensions_are_refused(self, field):
        with pytest.raises(ValueError):
            resize(MANUAL_WHEELCHAIR, **{field: 0.0})

    def test_non_positive_reach_is_refused(self):
        with pytest.raises(ValueError):
            resize(MANUAL_WHEELCHAIR, personal_reach_inches=-1.0)

    def test_non_finite_dimensions_are_refused(self):
        with pytest.raises(ValueError):
            resize(MANUAL_WHEELCHAIR, body_width_inches=math.inf)

    def test_envelope_radius_contains_the_footprint_in_every_orientation(self):
        width, length = 26.0, 43.0
        radius = OccupantProfile(
            id="x", title="x",
            body_width_inches=width, body_length_inches=length,
            turning_diameter_inches=60.0,
        ).envelope_radius_inches
        assert radius == math.hypot(width, length) / 2

    def test_adjusting_reach_does_not_touch_body_dimensions(self):
        adjusted = resize(MANUAL_WHEELCHAIR, personal_reach_inches=30.0)
        assert (
            adjusted.body_width_inches,
            adjusted.body_length_inches,
            adjusted.turning_diameter_inches,
        ) == (
            MANUAL_WHEELCHAIR.body_width_inches,
            MANUAL_WHEELCHAIR.body_length_inches,
            MANUAL_WHEELCHAIR.turning_diameter_inches,
        )

    def test_changing_body_dimensions_does_not_touch_reach_assumptions(self):
        widened = resize(MANUAL_WHEELCHAIR, body_width_inches=40.0)
        assert widened.horizontal_reach is None
        assert widened.personal_reach_inches == MANUAL_WHEELCHAIR.personal_reach_inches


class TestHorizontalReach:
    def test_it_carries_a_value_and_provenance(self):
        reach = HorizontalReach(30.0, "owner measured fingertip reach")
        assert reach.inches == 30.0
        assert "owner" in reach.provenance

    def test_a_value_without_provenance_is_refused(self):
        with pytest.raises(ValueError):
            HorizontalReach(30.0, "")

    @pytest.mark.parametrize("bad", [0.0, -5.0, math.inf])
    def test_non_positive_or_non_finite_values_are_refused(self, bad):
        with pytest.raises(ValueError):
            HorizontalReach(bad, "test suite")

    def test_a_profile_keeps_an_explicit_reach_and_can_drop_it(self):
        provided = HorizontalReach(30.0, "test suite")
        with_reach = resize(MANUAL_WHEELCHAIR, horizontal_reach=provided)
        assert with_reach.horizontal_reach == provided
        assert MANUAL_WHEELCHAIR.horizontal_reach is None

    def test_the_chair_width_is_not_the_reach(self):
        """The two quantities live on separate fields and neither resize
        path mixes them."""
        wide = resize(MANUAL_WHEELCHAIR, body_width_inches=60.0)
        assert wide.horizontal_reach is None
        with_reach = resize(wide, horizontal_reach=HorizontalReach(30.0, "test suite"))
        assert with_reach.body_width_inches == 60.0
        assert with_reach.horizontal_reach.inches == 30.0


class TestSweptSampling:
    def test_refined_points_are_never_further_than_half_a_radius_apart(self):
        path = [Vec3(x=0.0, y=0.0, z=0.0), Vec3(x=0.3, y=0.4, z=0.0)]
        refined = ensure_spacing(path, radius_meters=0.1)
        for left, right in zip(refined, refined[1:]):
            assert math.dist((left.x, left.y), (right.x, right.y)) <= 0.05 + 1e-9

    def test_a_single_point_path_comes_back_unchanged(self):
        point = Vec3(x=1.0, y=1.0, z=0.0)
        assert ensure_spacing([point], 0.1) == [point]

    def test_an_empty_path_stays_empty(self):
        assert ensure_spacing([], 0.1) == []

    def test_corner_cut_diagonal_collides_where_endpoints_would_say_clear(self):
        """Endpoints on either side of a thin wall are both far from it; only
        the swept chord between them touches it."""
        wall = _thin_wall()
        diagonal = [Vec3(x=-1.0, y=-1.0, z=0.0), Vec3(x=1.0, y=1.0, z=0.0)]
        dense = ensure_spacing(diagonal, radius_meters=0.33)
        assert wall.collides(dense, 13.0)

    def test_the_route_around_the_same_wall_does_not_collide(self):
        wall = _thin_wall()
        around = [
            Vec3(x=-1.0, y=-1.0, z=0.0),
            Vec3(x=-0.6, y=1.0, z=0.0),
            Vec3(x=0.6, y=1.0, z=0.0),
            Vec3(x=1.0, y=-1.0, z=0.0),
        ]
        dense = ensure_spacing(around, radius_meters=0.33)
        assert not wall.collides(dense, 13.0)

    def test_refinement_never_turns_a_clear_straight_run_into_a_collision(self):
        wall = _thin_wall()
        clear = [Vec3(x=2.0, y=-1.0, z=0.0), Vec3(x=2.0, y=1.0, z=0.0)]
        dense = ensure_spacing(clear, radius_meters=0.33)
        assert not wall.collides(dense, 13.0)
