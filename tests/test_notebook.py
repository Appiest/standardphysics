"""The reactive notebook still runs, and still runs on the real evaluator.

Nothing else imports `notebooks/scenario_sweep.py`, so a rename in the
evaluation package would break it quietly. marimo executes every cell when the
notebook is run as a program, which is enough to catch a name that moved, a
value defined in two cells, and a cell that references another cell's local.

It also pins the two properties the notebook is built on: the sweep's cache key
does not move when the slider being swept moves, and a knob that sets a
dimension does not claim one the routine took away.
"""

from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "scenario_sweep.py"

SWEPT_KNOB = "aisle_inches"


@pytest.fixture(scope="module")
def notebook():
    pytest.importorskip(
        "marimo",
        reason='needs the notebook extra: pip install -e "packages/agents[notebook]"',
    )
    spec = importlib.util.spec_from_file_location("scenario_sweep", NOTEBOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ran(notebook):
    """Every cell, executed. marimo raises if the graph is not sound."""
    return notebook.app.run()[1]


class TestTheNotebookRuns:
    def test_it_measures_the_room_its_sliders_built(self, ran):
        scored = ran["scenarios"].scores(ran["outcome"])
        assert scored["measurement_error_in"] < 0.05

    def test_the_sweep_visited_every_reading(self, ran):
        assert len(ran["sweep"]) == ran["steps"].value

    def test_the_chart_names_itself_for_a_screen_reader(self, ran):
        assert "<title>" in ran["sweep_view"]().text

    def test_the_findings_lead_with_the_plain_sentence(self, ran):
        problems = ran["outcome"].result.problems
        assert problems
        assert problems[0].title in ran["findings_list"](ran["outcome"])


class TestWhatTheNotebookRestsOn:
    def test_moving_the_swept_slider_leaves_the_cache_key_alone(self, ran):
        parked = ran["parked"]
        knobs = ran["knobs"]
        assert parked(knobs, SWEPT_KNOB) == parked(
            replace(knobs, **{SWEPT_KNOB: 58.5}), SWEPT_KNOB
        )

    def test_moving_another_slider_does_not(self, ran):
        parked, knobs = ran["parked"], ran["knobs"]
        assert parked(knobs, SWEPT_KNOB) != parked(
            replace(knobs, counter_inches=34.0), SWEPT_KNOB
        )

    def test_a_routine_that_crosses_the_doorway_cites_nothing(self, ran):
        """The doorway becomes the pinch, so 403.5.1 on an aisle axis would be
        pointing at the wrong dimension."""
        street = replace(ran["knobs"], routine="street")
        assert ran["scenarios"].PINS[SWEPT_KNOB] not in ran["scenarios"].expected_inches(
            street
        )

    def test_every_knob_has_a_name_and_a_range(self, ran):
        for _, (name, lowest, highest, step) in ran["KNOBS"].items():
            assert name and lowest < highest and step > 0

    def test_a_knob_that_answers_no_check_cites_nothing(self, ran):
        """The doorway slider sets the opening in the wall, and 404.2.3 is
        about the clear width with the door open. `PINS` leaves it out, so the
        sweep draws no reference line rather than raising on the lookup."""
        unpinned = [
            knob for knob in ran["KNOBS"] if knob not in ran["scenarios"].PINS
        ]
        assert unpinned == ["door_inches"]
        assert all(ran["cited_inches"](knob) is None for knob in unpinned)


SWEPT_AXIS = "shift_y"


class TestTheCaptureSection:
    def test_it_reads_a_real_scan(self, ran):
        """The plan is drawn from the capture, not from a fixture."""
        scanned = ran["scanned"]
        assert scanned.nodes
        assert all(node.labeled_by == "roomplan" for node in scanned.nodes)

    def test_the_shipped_samples_are_offered(self, ran):
        assert {each.id for each in ran["scans"]} >= {
            "apple_livingroom",
            "apple_bedroom3",
        }

    def test_the_trip_runs_between_two_things_the_scan_found(self, ran):
        places = ran["places"]
        assert ran["aim"].start in places
        assert ran["aim"].end in places

    def test_the_plan_names_itself_for_a_screen_reader(self, ran):
        assert "<title>" in ran["plan_view"]().text

    def test_the_plan_squares_the_room_to_its_longest_wall(self, ran):
        """A capture is oriented to wherever the phone stood, so the drawing
        turns it flat before fitting it."""
        import math

        turn = ran["square_to_the_walls"](ran["scanned"])
        assert abs(turn) <= math.pi / 4

    def test_a_wall_is_stroked_because_the_scan_gives_it_no_thickness(self, ran):
        """RoomPlan returns walls as zero-thickness planes, so a filled shape
        would come out invisible."""
        walls = [node for node in ran["scanned"].nodes if node.kind == "wall"]
        assert walls
        assert all(min(node.dimensions.x, node.dimensions.y) == 0.0 for node in walls)
        assert "stroke-width" in ran["plan_view"]().text

    def test_the_trip_is_drawn_through_the_room(self, ran):
        """This capture's walls do not close and the ground outside them is
        wide open, so a route offered that ground would be a walk around the
        block rather than the trip."""
        captures, aim, survey = ran["captures"], ran["aim"], ran["survey"]
        path = captures.walked_path(survey)
        assert path
        assert all(captures.on_the_floor(captures.room(aim), path))

    def test_a_route_that_does_leave_the_floor_is_drawn_as_an_aside(self, ran):
        """A stop outside, or two the floor cannot join, still puts a stretch
        of the route outdoors. Drawing that like the rest would present a walk
        around the building as the trip."""
        path = ran["captures"].walked_path(ran["survey"])
        half = len(path) // 2
        inside = [True] * half + [False] * (len(path) - half)
        runs = ran["runs_of"](path, inside)
        assert {on_floor for on_floor, _ in runs} == {True, False}

    def test_the_headline_number_says_where_it_was_taken(self, ran):
        reading = ran["reading_block"](ran["survey"])
        assert "tightest point of this trip" in reading
        assert "off the scanned floor" not in reading

    def test_a_confidence_the_scan_withheld_is_named(self, ran):
        """A check that measured something and asked anyway is listed by name,
        because a count alone does not say what is unresolved."""
        waiting = ran["captures"].withheld(ran["survey"])
        assert waiting
        block = ran["withheld_block"](ran["survey"])
        assert all(ran["rule_title"](check) in block for check in waiting)


class TestWhatTheCaptureSectionRestsOn:
    def test_moving_the_nudged_slider_leaves_the_cache_key_alone(self, ran):
        parked_nudge, aim = ran["parked_nudge"], ran["aim"]
        assert parked_nudge(aim, SWEPT_AXIS) == parked_nudge(
            replace(aim, shift_y=0.5), SWEPT_AXIS
        )

    def test_picking_another_piece_does_not(self, ran):
        parked_nudge, aim = ran["parked_nudge"], ran["aim"]
        assert parked_nudge(aim, SWEPT_AXIS) != parked_nudge(
            replace(aim, moved="Sofa 3"), SWEPT_AXIS
        )

    def test_a_capture_is_never_scored(self, ran):
        """`run_case` scores, and there is nothing here to score against, so
        the section reads findings off a pass instead."""
        assert not hasattr(ran["survey"], "case")
        assert ran["survey"].verdicts
