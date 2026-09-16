"""The tier 1 checks, against both measurement providers."""

from __future__ import annotations

from pytest import approx
from standardphysics_agents import VerificationLedger, assess
from standardphysics_agents.checks.route_geometry import is_reversal, reversal_stops
from standardphysics_agents.checks.route_width import route_width_verdict
from standardphysics_agents.checks.turn_width import turn_verdict
from standardphysics_contracts import Mat4, Stop, Vec3, to_meters
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario
from standardphysics_fixtures.shop import FIX_SHIFT_INCHES, node_id

PINCH_INCHES = 31.0

CASE_WEST = node_id("case_west")
CASE_EAST = node_id("case_east")
COUNTER = node_id("counter")


def _route_findings(result):
    return [f for f in result.findings if f.check_id == "route_clear_width"]


def _problems(result, check_id):
    return [f for f in result.problems if f.check_id == check_id]


def _at_the_pinch(result):
    """The findings about the 31 inch aisle.

    Matched on the rounded measurement because the two providers reach it by
    different arithmetic and one of them lands a float step short.
    """
    return [
        f
        for f in result.findings
        if f.measured_inches is not None
        and round(f.measured_inches, 3) == PINCH_INCHES
    ]


def _shift(graph, target, dx=0.0, dy=0.0):
    """Move one node. Dimensions never change, so only the transform moves."""
    moved = []
    for node in graph.nodes:
        if node.id != target:
            moved.append(node)
            continue
        position = node.transform.position
        moved.append(
            node.model_copy(
                update={
                    "transform": Mat4.translation(
                        position.x + dx, position.y + dy, position.z
                    )
                }
            )
        )
    return graph.model_copy(update={"nodes": moved, "revision": graph.revision + 1})


def test_the_fixture_pinch_produces_exactly_one_route_width_finding(
    graph, scenario, stub, ledger
):
    result = assess(graph, scenario, stub, ledger=ledger)
    problems = _problems(result, "route_clear_width")
    assert len(problems) == 1
    assert problems[0].measured_inches == approx(PINCH_INCHES)
    assert problems[0].required_inches == 36.0


def test_the_pinch_finding_names_both_display_cases(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    pinch = _at_the_pinch(result)[0]
    assert set(pinch.locus.node_ids) == {CASE_WEST, CASE_EAST}


def test_the_pinch_finding_cites_the_section_it_came_from(
    graph, scenario, measure, ledger
):
    result = assess(graph, scenario, measure, ledger=ledger)
    pinch = _at_the_pinch(result)[0]
    assert pinch.citation.authority == "ADA_2010"
    assert pinch.citation.section == "403.5.1"


def test_the_pinch_finding_draws_the_measurement_across_the_gap(
    graph, scenario, measure, ledger
):
    result = assess(graph, scenario, measure, ledger=ledger)
    pinch = _at_the_pinch(result)[0]
    assert pinch.locus.annotation.kind == "dimension_line"
    assert pinch.locus.annotation.label == "31 in"
    assert len(pinch.locus.annotation.points) == 2


def test_the_pinch_finding_reads_the_way_the_plan_says_it_should(
    graph, scenario, measure, ledger
):
    result = assess(graph, scenario, measure, ledger=ledger)
    pinch = _at_the_pinch(result)[0]
    assert pinch.title == "The path to the counter is too narrow"
    assert pinch.detail == (
        "It's 31 inches at the tightest point. Wheelchairs need 36 inches."
    )
    assert pinch.fix == "Move the two display cases 5 inches apart."


def test_widening_the_aisle_clears_the_finding(graph, scenario, pipeline, ledger):
    opened = _shift(graph, CASE_EAST, dx=to_meters(FIX_SHIFT_INCHES))
    result = assess(opened, scenario, pipeline, ledger=ledger)
    assert not _at_the_pinch(result)
    widened = next(
        f
        for f in result.findings
        if f.check_id == "route_clear_width" and set(f.locus.node_ids) == {CASE_WEST, CASE_EAST}
    )
    assert widened.outcome == "passes"
    assert widened.measured_inches == approx(36.0)


def test_two_legs_through_one_gap_are_one_finding(graph, scenario, stub, ledger):
    """Leg 0 and leg 2 both squeeze between the same two cases."""
    result = assess(graph, scenario, stub, ledger=ledger)
    at_the_pinch = [f for f in _route_findings(result) if f in _at_the_pinch(result)]
    assert len(at_the_pinch) == 1


def test_the_counter_height_is_a_problem_with_a_locus(
    graph, scenario, measure, ledger
):
    result = assess(graph, scenario, measure, ledger=ledger)
    height = _problems(result, "service_counter_height")
    assert len(height) == 1
    assert round(height[0].measured_inches, 1) == 47.0
    assert height[0].required_inches == 36.0
    assert height[0].citation.section == "904.4.1"
    assert height[0].locus.node_ids == [COUNTER]
    assert height[0].locus.annotation.kind == "dimension_line"


def test_a_wide_enough_opening_still_asks_for_the_clear_width(graph, scenario, measure, ledger):
    """404.2.3 measures with the door open 90 degrees, and the scan sees the hole.

    The leaf, its hardware and the stop all sit inside that hole, so an opening
    over the requirement settles nothing and the owner is asked for the one
    number a tape measure gives.
    """
    result = assess(graph, scenario, measure, ledger=ledger)
    door = next(f for f in result.findings if f.check_id == "door_clear_width")
    assert door.outcome == "question"
    assert door.fix is None


def test_thin_coverage_becomes_a_request_not_a_red_finding(
    graph, scenario, measure, ledger
):
    unsure = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"quality": "needs_another_look"})
                if node.id == CASE_EAST
                else node
                for node in graph.nodes
            ]
        }
    )
    result = assess(unsure, scenario, measure, ledger=ledger)
    pinch = next(f for f in result.findings if f.check_id == "route_clear_width"
                 and CASE_EAST in (f.locus.node_ids if f.locus else []))
    assert pinch.outcome == "question"
    assert pinch.title.startswith("Point the phone at")
    assert pinch.fix is None


def test_things_a_scan_cannot_see_become_questions(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    asked = {f.check_id for f in result.questions}
    assert asked == {
        "door_clear_width", "entrance_threshold", "door_hardware",
        "door_opening_force", "floor_surface", "restroom_turning_space",
    }


def test_a_question_never_carries_a_fix(graph, scenario, measure, ledger):
    result = assess(graph, scenario, measure, ledger=ledger)
    assert all(f.fix is None for f in result.questions)


def test_problems_come_before_questions_and_questions_before_passes(
    graph, scenario, measure, ledger
):
    result = assess(graph, scenario, measure, ledger=ledger)
    ranks = {"problem": 0, "question": 1, "passes": 2}
    order = [ranks[f.outcome] for f in result.findings]
    assert order == sorted(order)


def test_a_finding_keeps_its_id_between_passes(graph, scenario, measure, ledger):
    first = assess(graph, scenario, measure, ledger=ledger, pass_number=1)
    second = assess(graph, scenario, measure, ledger=ledger, pass_number=2)
    assert {f.id for f in first.findings} == {f.id for f in second.findings}


def test_nothing_runs_without_a_person_verifying_the_rules(graph, scenario, measure):
    result = assess(graph, scenario, measure, ledger=VerificationLedger())
    assert result.findings == []


def test_the_assessment_records_what_it_judged(graph, scenario, measure, ledger, pack):
    result = assess(graph, scenario, measure, ledger=ledger, pass_number=2)
    assert result.assessment.rulepack_version == pack.version
    assert result.assessment.pass_number == 2
    assert result.assessment.graph_revision == graph.revision
    assert len(result.assessment.graph_hash) == 64


def test_moving_a_node_changes_the_layout_fingerprint(graph, scenario, stub, ledger):
    before = assess(graph, scenario, stub, ledger=ledger)
    after = assess(_shift(graph, CASE_EAST, dx=0.1), scenario, stub, ledger=ledger)
    assert before.assessment.graph_hash != after.assessment.graph_hash


class TestRouteWidthRule:
    """ADA 2010 403.5.1 and its exception, without any geometry."""

    def test_at_the_minimum_it_passes(self, pack):
        verdict = route_width_verdict(36.0, None, pack.by_id("route_clear_width"))
        assert verdict.satisfied
        assert verdict.reason == "meets_minimum"

    def test_below_the_reduced_minimum_it_fails_outright(self, pack):
        verdict = route_width_verdict(31.0, None, pack.by_id("route_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "below_minimum"

    def test_a_short_reduced_run_is_permitted(self, pack):
        verdict = route_width_verdict(32.0, 24.0, pack.by_id("route_clear_width"))
        assert verdict.satisfied
        assert verdict.reason == "reduction_permitted"

    def test_a_long_reduced_run_is_not(self, pack):
        verdict = route_width_verdict(32.0, 25.0, pack.by_id("route_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "reduction_too_long"

    def test_an_unmeasured_run_length_fails_closed(self, pack):
        """The exception has a length condition. Passing without checking it
        would be a pass nobody evaluated."""
        verdict = route_width_verdict(34.0, None, pack.by_id("route_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "reduction_length_unknown"


class TestTurnRule:
    """ADA 2010 403.5.2, without any geometry."""

    def test_a_wide_element_is_out_of_scope(self, pack):
        verdict = turn_verdict(48.0, 30.0, 30.0, 30.0, pack.by_id("turn_clear_width"))
        assert not verdict.applies
        assert verdict.reason == "element_wide_enough"

    def test_sixty_inches_at_the_turn_is_the_exception(self, pack):
        verdict = turn_verdict(12.0, 30.0, 60.0, 30.0, pack.by_id("turn_clear_width"))
        assert not verdict.applies
        assert verdict.reason == "exempt_wide_turn"

    def test_all_three_zones_at_their_minimum_pass(self, pack):
        verdict = turn_verdict(12.0, 42.0, 48.0, 42.0, pack.by_id("turn_clear_width"))
        assert verdict.applies and verdict.satisfied

    def test_a_tight_approach_fails(self, pack):
        verdict = turn_verdict(12.0, 41.0, 48.0, 42.0, pack.by_id("turn_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "approaching_too_tight"

    def test_a_tight_turn_fails(self, pack):
        verdict = turn_verdict(12.0, 42.0, 47.0, 42.0, pack.by_id("turn_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "at_turn_too_tight"

    def test_a_tight_exit_fails(self, pack):
        verdict = turn_verdict(12.0, 42.0, 48.0, 41.0, pack.by_id("turn_clear_width"))
        assert not verdict.satisfied
        assert verdict.reason == "leaving_too_tight"


class TestDeadEnds:
    """Where 304.3 applies: a stop you have to reverse out of."""

    def test_going_back_the_way_you_came_is_a_reversal(self):
        assert is_reversal((1.0, 0.0), (-1.0, 0.0))

    def test_turning_a_corner_is_not(self):
        assert not is_reversal((1.0, 0.0), (0.0, 1.0))

    def test_carrying_straight_on_is_not(self):
        assert not is_reversal((1.0, 0.0), (1.0, 0.0))

    def test_an_out_and_back_errand_has_one_dead_end(self):
        stops = [
            Stop(name="Entrance", position=Vec3(x=0.0, y=0.0, z=0.0)),
            Stop(name="Restroom", position=Vec3(x=0.0, y=4.0, z=0.0)),
            Stop(name="Exit", position=Vec3(x=0.0, y=0.0, z=0.0)),
        ]
        assert reversal_stops(stops) == [1]

    def test_the_fixture_route_never_doubles_back(self, scenario):
        assert reversal_stops(scenario.stops) == []


def test_an_unmeasured_turn_is_a_gap_not_a_finding(graph, scenario, pipeline, ledger):
    """A-34. A turn with a missing zone used to vanish. The team is told."""
    result = assess(graph, scenario, pipeline, ledger=ledger)
    gaps = {gap.rule_id: gap.waiting_on for gap in result.unevaluated}
    assert "turn_clear_width" in gaps
    assert not [f for f in result.findings if f.check_id == "turn_clear_width"]


def test_a_lowered_section_meets_the_height_rule(pipeline, ledger):
    """904.4.1 asks for a portion, not that every stretch of counter is 36 in."""
    result = assess(
        build_lawsuit_graph(), build_lawsuit_scenario(), pipeline, ledger=ledger
    )
    height = [f for f in result.findings if f.check_id == "service_counter_height"]
    assert height and height[0].outcome == "passes"
    assert height[0].measured_inches == approx(36.0)


def test_a_card_reader_on_the_high_counter_is_the_finding(pipeline, ledger):
    result = assess(
        build_lawsuit_graph(), build_lawsuit_scenario(), pipeline, ledger=ledger
    )
    pos = [f for f in result.problems if f.check_id == "point_of_sale_height"]
    assert len(pos) == 1
    assert pos[0].measured_inches == approx(47.0)
    assert pos[0].fix == "Move the card reader to the lowered counter section."


def test_the_plain_shop_has_no_register_finding(graph, scenario, pipeline, ledger):
    result = assess(graph, scenario, pipeline, ledger=ledger)
    assert not [f for f in result.findings if f.check_id == "point_of_sale_height"]
