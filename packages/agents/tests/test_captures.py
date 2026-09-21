"""Real captures through the evaluator, and the properties the notebook rests on.

The two Apple samples ship inside the fixtures package, so these run
everywhere. The phone scans live under `datasets/` and are skipped where a
checkout does not carry them.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from standardphysics_agents.evaluation import captures as c
from standardphysics_agents.evaluation.configuration import setup
from standardphysics_agents.fix.constraints import _in_swing, door_keep_clear
from standardphysics_contracts import to_meters

SAMPLE = "apple_livingroom"

ROUTE = "route_clear_width"


@pytest.fixture(scope="module")
def configuration():
    """Every rule read as verified, so the checks run, and no fix search."""
    return setup(preview_unverified=True, run_fixes=False)


@pytest.fixture(scope="module")
def aim():
    return c.default_aim(SAMPLE)


@pytest.fixture(scope="module")
def reviewed(aim, configuration):
    return c.review(aim, configuration)


class TestTheCapturesOnDisk:
    def test_the_shipped_samples_are_always_there(self):
        """They sit inside the fixtures package, so an install carries them."""
        present = {capture.id for capture in c.available()}
        assert {"apple_livingroom", "apple_bedroom3"} <= present

    def test_a_capture_this_checkout_lacks_is_reported_missing(self):
        """`datasets/` is absent from a wheel, and a capture under it says so
        rather than being assumed present."""
        for capture in c.CAPTURES:
            assert capture.present == capture.room_json.is_file()

    def test_a_sample_does_not_claim_we_captured_it(self):
        assert c.capture_named(SAMPLE).provenance() == "Apple RoomPlan sample"

    def test_an_unknown_capture_is_refused(self):
        with pytest.raises(KeyError):
            c.capture_named("no-such-room")

    def test_every_capture_parses_with_its_floor_at_zero(self):
        for capture in c.available():
            graph = c.load(capture.id)
            floor = next(node for node in graph.nodes if node.kind == "floor")
            assert floor.transform.position.z == pytest.approx(0.0, abs=1e-6)


class TestNamingWhatTheScanFound:
    def test_two_of_a_kind_are_told_apart(self, aim):
        named = c.anchors(c.load(aim.capture))
        assert "Sofa 1" in named and "Sofa 2" in named

    def test_a_room_with_one_of_something_does_not_number_it(self):
        assert "Bed" in c.anchors(c.load("apple_bedroom3"))

    def test_a_wall_is_not_a_destination(self, aim):
        named = c.anchors(c.load(aim.capture))
        assert all(node.kind != "wall" for node in named.values())

    def test_only_furniture_can_be_moved(self, aim):
        graph = c.load(aim.capture)
        assert all(node.movable for node in c.movable(graph).values())
        assert "Door" not in c.movable(graph)

    def test_a_plumbed_in_fixture_stays_put(self, aim):
        """Ingest pins an oven whatever else it says about it."""
        assert "Oven" not in c.movable(c.load(aim.capture))


class TestTheTrip:
    def test_the_default_starts_at_a_way_in(self, aim):
        named = c.anchors(c.load(aim.capture))
        assert named[aim.start].kind in ("door", "opening")

    def test_a_trip_needs_two_different_stops(self, aim):
        with pytest.raises(ValueError):
            c.trip(replace(aim, end=aim.start))

    def test_a_stop_this_room_does_not_have_is_refused(self, aim):
        with pytest.raises(KeyError):
            c.trip(replace(aim, end="Escalator"))

    def test_the_stops_carry_the_nodes_they_stand_at(self, aim):
        named = c.anchors(c.load(aim.capture))
        stops = c.trip(aim).stops
        assert [stop.name for stop in stops] == [aim.start, aim.end]
        assert stops[0].anchor_node_id == named[aim.start].id


class TestRearranging:
    def test_a_nudge_moves_one_piece_and_nothing_else(self, aim):
        moved = replace(aim, moved="Sofa 2", shift_y=to_meters(6.0))
        before = {node.id: node.transform.position.y for node in c.load(aim.capture).nodes}
        after = c.room(moved)
        shifted = [
            node.id
            for node in after.nodes
            if abs(node.transform.position.y - before[node.id]) > 1e-9
        ]
        assert shifted == [c.movable(c.load(aim.capture))["Sofa 2"].id]

    def test_a_nudge_never_resizes(self, aim):
        moved = replace(aim, moved="Sofa 2", shift_x=to_meters(9.0))
        sizes = {node.id: node.dimensions.as_tuple() for node in c.load(aim.capture).nodes}
        assert all(
            node.dimensions.as_tuple() == sizes[node.id] for node in c.room(moved).nodes
        )

    def test_a_piece_this_room_cannot_move_is_refused(self, aim):
        with pytest.raises(KeyError):
            c.room(replace(aim, moved="Oven", shift_x=to_meters(6.0)))

    def test_standing_still_is_allowed(self, aim):
        assert c.refused(replace(aim, moved="Sofa 2")) == []

    def test_pushing_a_sofa_into_something_is_not(self, aim):
        """The constraint set that guards a proposal answers here too, so an
        impossible layout is not reported as a measurement."""
        breaches = [
            c.refused(replace(aim, moved="Sofa 2", shift_x=to_meters(inches)))
            for inches in (6.0, 12.0, 18.0, 24.0)
        ]
        assert any(breaches)
        assert all(each for found in breaches for each in found)

    def test_an_overlap_the_scan_already_had_is_not_blamed_on_the_nudge(self, aim):
        """This door is 3.67 m wide, so its keep-clear square covers most of
        the room and Sofa 2 stands in it already. A rearrangement is judged on
        what it changes, not on what the scan walked in with."""
        door = next(node for node in c.load(aim.capture).nodes if node.kind == "door")
        sofa = c.movable(c.load(aim.capture))["Sofa 2"]
        assert _in_swing(sofa, door_keep_clear(door), 0.0)
        assert c.refused(replace(aim, moved="Sofa 2", shift_y=to_meters(24.0))) == []

    def test_a_sweep_needs_a_piece_to_move(self, aim):
        with pytest.raises(ValueError):
            c.sweep_shift(replace(aim, moved=None), "shift_x", [0.0])

    def test_a_direction_that_is_not_one_is_refused(self, aim):
        with pytest.raises(KeyError):
            c.sweep_shift(replace(aim, moved="Sofa 2"), "shift_z", [0.0])

    def test_a_sweep_visits_every_offset(self, aim, configuration):
        offsets = [to_meters(each) for each in (-6.0, 0.0, 6.0)]
        swept = c.sweep_shift(
            replace(aim, moved="Sofa 2"), "shift_y", offsets, configuration
        )
        assert [each.shift_y for each, _ in swept] == offsets


class TestWhatTheChecksSaid:
    def test_a_capture_gets_real_findings(self, reviewed):
        assert reviewed.findings
        assert reviewed.verdicts

    def test_a_scan_holds_back_what_it_is_unsure_of(self, aim, reviewed):
        """RoomPlan is not confident about every piece, and a check resting on
        one of those asks rather than ruling."""
        assert c.unsure(c.load(aim.capture))
        assert c.withheld(reviewed)

    def test_a_rescan_settles_what_confidence_withheld(self, aim, configuration):
        withheld = c.withheld(c.review(aim, configuration))
        cleared = c.review(replace(aim, after_rescan=True), configuration).verdicts
        assert withheld
        assert all(cleared[check] != "question" for check in withheld)

    def test_a_rescan_moves_no_measurement(self, aim, configuration):
        """It writes confidence and nothing else, so it cannot manufacture an
        answer, only stop one from being withheld."""
        before = c.measured(c.review(aim, configuration), ROUTE)
        after = c.measured(
            c.review(replace(aim, after_rescan=True), configuration), ROUTE
        )
        assert before == pytest.approx(after)

    def test_nothing_here_is_scored(self):
        """A capture carries no labels, so there is no scorer to call."""
        assert not hasattr(c, "scores")


class TestWhereTheRouteWent:
    def test_the_walked_path_is_found_by_its_annotation(self, reviewed):
        assert len(c.walked_path(reviewed)) > 1

    def test_a_width_comes_with_the_two_points_it_was_taken_between(self, reviewed):
        ends = c.pinch_line(reviewed, ROUTE)
        assert ends is not None and len(ends) == 2

    def test_an_open_capture_is_still_walked_through_the_room(self, aim, reviewed):
        """These walls do not close, and the ground outside them is the widest
        corridor in the scan. A trip between two stops in the room has to be
        measured through the room anyway."""
        graph = c.room(aim)
        assert c.strayed(graph, c.walked_path(reviewed)) == 0.0

    def test_a_step_on_the_floor_reads_as_on_the_floor(self, aim, reviewed):
        graph = c.room(aim)
        path = c.walked_path(reviewed)
        inside = c.on_the_floor(graph, path)
        assert len(inside) == len(path)
        assert any(inside)

    def test_a_room_with_no_floor_claims_nothing(self, aim, reviewed):
        graph = c.room(aim)
        floorless = graph.model_copy(
            update={"nodes": [n for n in graph.nodes if n.kind != "floor"]}
        )
        assert c.floor_outline(floorless) is None
        assert c.strayed(floorless, c.walked_path(reviewed)) == 0.0
