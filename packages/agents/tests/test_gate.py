"""The gate. Four conditions, and all four have to hold."""

from __future__ import annotations

from dataclasses import replace

import pytest
from standardphysics_agents import assess
from standardphysics_agents.assess import Pass
from standardphysics_agents.checks.observation import Unevaluated
from standardphysics_agents.evaluation import variants as v
from standardphysics_agents.evaluation.gate import (
    UNMEASURED_SHORTFALL_INCHES,
    accepts,
    answered_checks,
    total_shortfall,
)
from standardphysics_agents.fix import apply_moves
from standardphysics_contracts import NodeMove, Vec3, to_meters
from standardphysics_fixtures.shop import FIX_SHIFT_INCHES


def _look(graph, scenario, measure, pack, ledger) -> Pass:
    return assess(graph, scenario, measure, rules=pack, ledger=ledger)


@pytest.fixture(scope="module")
def before(graph, scenario, pipeline, pack, ledger):
    return _look(graph, scenario, pipeline, pack, ledger)


@pytest.fixture(scope="module")
def widened(graph, scenario, pipeline, pack, ledger):
    opened = apply_moves(
        graph,
        [
            NodeMove(
                node_id=v.CASE_EAST,
                delta_translation=Vec3(x=to_meters(FIX_SHIFT_INCHES), y=0.0, z=0.0),
            )
        ],
    )
    return _look(opened, scenario, pipeline, pack, ledger)


def _all_clear(result: Pass) -> Pass:
    """Every problem turned into a pass, which is what fixing one looks like.

    A check that clears still reports. It does not stop answering, and the gate
    is right to treat a finding that vanished as coverage lost rather than as a
    shop that improved.
    """
    kept = [
        f.model_copy(update={"outcome": "passes", "fix": None})
        if f.outcome == "problem"
        else f
        for f in result.findings
    ]
    return Pass(
        assessment=result.assessment.model_copy(update={"findings": kept}),
        unevaluated=result.unevaluated,
    )


def _dropping(result: Pass, check_id: str) -> Pass:
    kept = [f for f in result.findings if f.check_id != check_id]
    return Pass(
        assessment=result.assessment.model_copy(update={"findings": kept}),
        unevaluated=result.unevaluated,
    )


class TestShortfall:
    def test_it_adds_up_how_far_short_everything_is(self, before):
        assert total_shortfall(before) > 0

    def test_a_problem_with_no_measurement_counts_as_the_worst_kind(self, before):
        blocked = before.problems[0].model_copy(
            update={"measured_inches": None, "required_inches": None}
        )
        one = Pass(
            assessment=before.assessment.model_copy(update={"findings": [blocked]})
        )
        assert total_shortfall(one) == UNMEASURED_SHORTFALL_INCHES

    def test_nothing_wrong_is_nothing_to_make_up(self, before):
        assert total_shortfall(_all_clear(before)) == 0.0


class TestAcceptance:
    def test_widening_the_aisle_is_accepted(self, before, widened):
        gate = accepts(before, widened)
        assert gate.accepted
        assert gate.shortfall_after < gate.shortfall_before
        assert gate.reasons == ()

    def test_the_same_layout_twice_is_not_an_improvement(self, before):
        """A rearrangement that changes nothing measurable is not progress, and
        accepting it would let the loop declare progress forever."""
        gate = accepts(before, before)
        assert not gate.accepted
        assert "nothing measurable changed" in gate.reasons

    def test_going_backwards_is_rejected(self, before, widened):
        gate = accepts(widened, before)
        assert not gate.accepted

    def test_a_check_that_stopped_reporting_is_lost_coverage(self, before, widened):
        """Fewer problems can mean a check went quiet rather than a shop
        getting better."""
        quiet = _dropping(widened, "door_clear_width")
        gate = accepts(before, quiet)
        assert not gate.accepted
        assert any("stopped reporting" in reason for reason in gate.reasons)

    def test_a_rule_that_stopped_being_answerable_means_incomplete(
        self, before, widened
    ):
        gaps = Pass(
            assessment=widened.assessment,
            unevaluated=[*widened.unevaluated, Unevaluated("passing_space", "a person")],
        )
        gate = accepts(before, gaps)
        assert not gate.accepted
        assert any("stopped being answerable" in reason for reason in gate.reasons)

    def test_a_new_problem_is_rejected_even_when_the_total_improves(
        self, before, widened
    ):
        invented = before.problems[0].model_copy(
            update={
                "id": v.node_id("a_problem_that_was_not_there"),
                "measured_inches": 35.5,
                "title": "The path to the seats is too narrow",
            }
        )
        worse = Pass(
            assessment=widened.assessment.model_copy(
                update={"findings": [*widened.findings, invented]}
            ),
            unevaluated=widened.unevaluated,
        )
        gate = accepts(before, worse)
        assert not gate.accepted
        assert any(reason.startswith("new problem") for reason in gate.reasons)

    def test_clearing_everything_is_accepted(self, before):
        gate = accepts(before, _all_clear(before))
        assert gate.accepted
        assert gate.problems_after == 0

    @pytest.mark.parametrize("require_improvement", [True, False])
    def test_turning_a_problem_into_a_question_loses_coverage(
        self, before, require_improvement
    ):
        target = before.problems[0]
        findings = [
            f.model_copy(update={"outcome": "question", "fix": None})
            if f.id == target.id else f
            for f in before.findings
        ]
        after = replace(
            before, assessment=before.assessment.model_copy(update={"findings": findings})
        )
        gate = accepts(before, after, require_improvement=require_improvement)
        assert not gate.accepted
        assert f"{target.check_id} lost its measured answer" in gate.reasons

    def test_a_question_turned_into_a_pass_is_a_fabricated_answer(self, before):
        """Moving furniture cannot create a measurement the scan never took,
        so a needs-verification finding may not quietly become a pass when
        the layout changes."""
        target = next(f for f in before.questions)
        findings = [
            f.model_copy(update={"outcome": "passes", "fix": None})
            if f.id == target.id else f
            for f in before.findings
        ]
        after = replace(
            before, assessment=before.assessment.model_copy(update={"findings": findings})
        )
        gate = accepts(before, after)
        assert not gate.accepted
        assert f"{target.check_id} turned from a question into an answer" in gate.reasons

    def test_a_question_turned_into_a_problem_is_also_fabricated(self, before):
        target = next(f for f in before.questions)
        findings = [
            f.model_copy(update={"outcome": "problem"})
            if f.id == target.id else f
            for f in before.findings
        ]
        after = replace(
            before, assessment=before.assessment.model_copy(update={"findings": findings})
        )
        gate = accepts(before, after)
        assert not gate.accepted
        assert f"{target.check_id} turned from a question into an answer" in gate.reasons

    def test_a_question_that_stayed_a_question_does_not_block(self, before, widened):
        """The unchanged unknowns stay visible without vetoing a real fix."""
        gate = accepts(before, widened)
        assert gate.accepted

    def _bumped(self, result: Pass, delta: float) -> Pass:
        """Every solved-by-widening problem improved by `delta` inches."""
        findings = [
            f.model_copy(update={"measured_inches": f.measured_inches + delta})
            if (
                f.outcome == "problem"
                and f.measured_inches is not None
                and f.required_inches is not None
                and f.measured_inches < f.required_inches
            )
            else f
            for f in result.findings
        ]
        return replace(
            result,
            assessment=result.assessment.model_copy(update={"findings": findings}),
        )

    def test_a_shortfall_improvement_within_measurement_noise_is_rejected(
        self, before
    ):
        """The grid quantises at about an inch. A candidate that wins less
        than that has not improved the room; it has rounded it differently."""
        gate = accepts(before, self._bumped(before, 0.4))
        assert not gate.accepted
        assert "improvement within measurement noise" in gate.reasons

    def test_a_shortfall_improvement_past_the_noise_is_accepted(self, before):
        gate = accepts(before, self._bumped(before, 6.0))
        assert gate.accepted

    def test_going_backwards_is_rejected(self, before, widened):
        gate = accepts(widened, before)
        assert not gate.accepted
        assert any(
            "improvement within measurement noise" in reason
            or "more problems than before" in reason
            or "nothing measurable changed" in reason
            for reason in gate.reasons
        )

    def test_losing_a_pass_is_rejected_even_when_another_problem_clears(self, before):
        cleared = _all_clear(before)
        target = next(f for f in before.findings if f.outcome == "passes")
        findings = [
            f.model_copy(update={"outcome": "question"}) if f.id == target.id else f
            for f in cleared.findings
        ]
        after = replace(
            cleared, assessment=cleared.assessment.model_copy(update={"findings": findings})
        )
        assert not accepts(before, after)

    def test_a_finding_that_vanished_is_lost_coverage_not_a_fix(self, before):
        """Deleting a check is the cheapest way to make a shop look compliant."""
        gone = _dropping(before, "service_counter_height")
        gate = accepts(before, gone)
        assert not gate.accepted
        assert "service_counter_height stopped reporting" in gate.reasons

    def test_a_gate_result_reads_as_a_boolean(self, before, widened):
        assert accepts(before, widened)
        assert not accepts(before, before)

    def test_it_reports_what_it_compared(self, before, widened):
        gate = accepts(before, widened)
        assert gate.problems_before == len(before.problems)
        assert gate.problems_after == len(widened.problems)


def test_answered_checks_counts_every_outcome(before):
    assert "door_clear_width" in answered_checks(before)
    assert "route_clear_width" in answered_checks(before)
