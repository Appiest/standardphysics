"""A room built from knobs, and the same evaluator reading it back.

The claim these tests hold up is that a knob and a measurement are the same
number. If a slider says 31 in and the pipeline measures the room at 31 in,
then a finding somebody watched appear while dragging that slider is the
finding the server would report for that room.
"""

from __future__ import annotations

import pytest
from standardphysics_agents.assess import STRENGTH
from standardphysics_agents.evaluation import scenarios as s
from standardphysics_agents.evaluation.configuration import setup
from standardphysics_agents.evaluation.dataset import ROUTE, SCAN_CANNOT_SEE

ROUTE_MINIMUM_INCHES = 36.0
"""ADA 2010 403.5.1, which is where the sweep should change its mind."""

TOLERANCE_INCHES = 0.05


@pytest.fixture(scope="module")
def configuration():
    """Every rule read, no fix search: the knobs are about measuring."""
    return setup(preview_unverified=True, run_fixes=False)


AISLE_WIDTHS = [24.0, 30.0, 35.9, 36.0, 42.0]
"""Either side of 403.5.1's minimum, and a tenth of an inch short of it."""


@pytest.fixture(scope="module")
def outcome(configuration):
    """The fixture shop as shipped, read once."""
    return s.run_knobs(s.Knobs(), configuration)


@pytest.fixture(scope="module")
def swept(configuration):
    """One sweep of the aisle, read by every test in `TestSweepingOneKnob`."""
    return s.readings(
        s.sweep_knob(
            s.Knobs(counter_inches=34.0), "aisle_inches", AISLE_WIDTHS, configuration
        ),
        "aisle_inches",
    )


class TestTheRoomTheKnobsBuild:
    def test_the_defaults_are_the_fixture_as_shipped(self):
        knobs = s.Knobs()
        assert knobs.aisle_inches == pytest.approx(31.0)
        assert knobs.counter_side_seating is True

    def test_clearing_the_counter_side_takes_the_seating_out(self):
        seated, _ = s.room(s.Knobs(counter_side_seating=True))
        cleared, _ = s.room(s.Knobs(counter_side_seating=False))
        assert len(cleared.nodes) < len(seated.nodes)

    def test_each_routine_brings_its_own_stops(self):
        _, drink = s.room(s.Knobs(routine="drink"))
        _, street = s.room(s.Knobs(routine="street"))
        assert drink.name != street.name
        assert [stop.name for stop in street.stops][0] == "Street"

    def test_an_unknown_routine_says_so(self):
        with pytest.raises(KeyError):
            s.room(s.Knobs(routine="teleport"))


class TestWhatTheKnobsClaim:
    def test_a_knob_that_sets_a_dimension_expects_that_measurement(self):
        expected = s.expected_inches(s.Knobs(aisle_inches=33.0, door_inches=34.0))
        assert expected[ROUTE] == pytest.approx(33.0)

    def test_the_doorway_knob_claims_no_measurement(self):
        """It sets the hole in the wall, and 404.2.3 is about the door in it."""
        assert "door_clear_width" not in s.expected_inches(s.Knobs(door_inches=34.0))

    def test_the_doorway_on_the_route_retires_the_aisle_claim(self):
        """Walking in from the street makes the doorway the tightest thing."""
        assert ROUTE not in s.expected_inches(s.Knobs(routine="street"))

    def test_every_room_is_asked_what_a_scan_cannot_see(self):
        assert s.as_case(s.Knobs()).expected_questions == SCAN_CANNOT_SEE

    def test_two_settings_are_two_cases(self):
        assert s.case_id(s.Knobs(aisle_inches=31.0)) != s.case_id(
            s.Knobs(aisle_inches=36.0)
        )

    def test_the_description_reads_in_inches(self):
        described = s.describe(s.Knobs(aisle_inches=31.0))
        assert "31 in" in described
        assert "Order a drink".lower() in described


class TestTheSameEvaluatorReadsItBack:
    def test_the_pipeline_measures_the_aisle_the_knob_set(self, configuration):
        reading = s.reading(
            s.run_knobs(s.Knobs(aisle_inches=29.0), configuration),
            s.Knobs(aisle_inches=29.0),
            "aisle_inches",
        )
        assert reading.measured_inches == pytest.approx(29.0, abs=TOLERANCE_INCHES)

    def test_the_scorers_agree_the_room_was_measured(self, configuration):
        scores = s.scores(s.run_knobs(s.Knobs(aisle_inches=42.0), configuration))
        assert scores["measurement_error_in"] < TOLERANCE_INCHES
        assert scores["question_recall"] == pytest.approx(1.0)

    def test_only_the_scorers_the_knobs_labelled_are_read(self, configuration):
        """Nothing here says which checks ought to fail, so nothing here
        scores precision. A room with no label is not a wrong answer."""
        scores = s.scores(s.run_knobs(s.Knobs(), configuration))
        assert "finding_precision" not in scores
        assert set(scores) == set(s.LABELLED_SCORERS)

    def test_a_high_counter_is_reported_as_a_problem(self, configuration):
        outcome = s.run_knobs(s.Knobs(counter_inches=47.0), configuration)
        assert "service_counter_height" in outcome.reported_problems

    def test_a_counter_low_enough_to_order_from_is_not(self, configuration):
        outcome = s.run_knobs(s.Knobs(counter_inches=34.0), configuration)
        assert "service_counter_height" not in outcome.reported_problems


class TestSweepingOneKnob:
    def test_it_visits_every_value(self, swept):
        assert [reading.knob_inches for reading in swept] == AISLE_WIDTHS

    def test_the_check_changes_its_mind_at_the_cited_minimum(self, swept):
        verdicts = {reading.knob_inches: reading.outcome for reading in swept}
        assert verdicts[35.9] == "problem"
        assert verdicts[36.0] == "passes"

    def test_every_row_carries_the_number_it_was_measured_against(self, swept):
        assert {reading.required_inches for reading in swept} == {
            ROUTE_MINIMUM_INCHES
        }

    def test_a_knob_no_check_answers_is_refused(self, configuration):
        with pytest.raises(KeyError):
            s.sweep_knob(s.Knobs(), "counter_side_seating", [True], configuration)


class TestWhatEachCheckSaid:
    def test_one_verdict_per_check(self, outcome):
        assert set(outcome.result.verdicts.values()) <= set(STRENGTH)

    def test_a_problem_on_any_leg_is_the_verdict(self, outcome):
        """The route measures every leg. The shipped shop passes most of them
        and pinches at 31 in, and the pinch is what the check said."""
        legs = {
            finding.outcome
            for finding in outcome.result.findings
            if finding.check_id == ROUTE
        }
        assert legs == {"problem", "passes"}
        assert outcome.result.verdicts[ROUTE] == "problem"

    def test_a_held_rule_says_what_it_is_waiting_on(self, outcome):
        waiting = outcome.result.held
        assert waiting
        assert all(reason for reason in waiting.values())

    def test_holding_does_not_hide_what_a_rule_did_answer(self, outcome):
        """`exit_path` measures a width and holds the rest of its section."""
        assert "exit_path" in outcome.result.held
        assert outcome.result.verdicts["exit_path"] == "passes"
