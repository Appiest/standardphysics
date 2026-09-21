"""Regressions for audit findings in Lane C's checks.

Each test is a case that returned the wrong answer, or crashed, before its fix.
"""

from standardphysics_agents import assess
from standardphysics_fixtures import build_graph, build_scenario
from standardphysics_pipeline.measure import PipelineMeasurements

from standardphysics_api.stages import preview_ledger

TURN_RULE_ID = "turn_clear_width"


def _fixture_pass():
    return assess(
        build_graph(), build_scenario(), PipelineMeasurements(), ledger=preview_ledger(), pass_number=1
    )


def test_a32_the_fixture_shop_assesses_with_an_unmeasured_turn_zone():
    result = _fixture_pass()
    assert all(finding.measured_inches != 0.0 for finding in result.assessment.findings)


def test_a32_a_partly_measured_turn_is_neither_a_problem_nor_a_pass():
    result = _fixture_pass()
    assert not [finding for finding in result.findings if finding.check_id == TURN_RULE_ID]
