"""The layout screen tries many legal moves on the typed shop graph."""

from standardphysics_agents.fix.pinch import pinch_from
from standardphysics_agents.simulate import DEFAULT_SAMPLES, dense_candidates, screen_layouts


def test_the_screen_finds_the_fixture_aisle_fix(graph, scenario, pipeline, pack, ledger):
    from standardphysics_agents import assess

    findings = assess(graph, scenario, pipeline, rules=pack, ledger=ledger).findings
    problems = [finding for finding in findings if finding.outcome == "problem"]
    report = screen_layouts(
        graph, scenario, pipeline, problems, rules=pack, ledger=ledger, samples=32
    )
    assert report.found
    assert report.best is not None
    assert report.measured >= 1


def test_dense_candidates_stay_within_the_sample_cap(graph, scenario, pipeline, pack, ledger):
    from standardphysics_agents import assess

    findings = assess(graph, scenario, pipeline, rules=pack, ledger=ledger).findings
    pinches = (pinch_from(finding, graph) for finding in findings)
    pinch = next(pinch for pinch in pinches if pinch and pinch.fixable)
    found = dense_candidates(pinch, 8)
    assert len(found) == 8
    assert {item.strategy for item in found} <= {"screen_across", "screen_along"}


def test_a_room_with_no_problems_screens_nothing(graph, scenario, pipeline, pack, ledger):
    report = screen_layouts(
        graph, scenario, pipeline, [], rules=pack, ledger=ledger, samples=16
    )
    assert not report.found
    assert report.samples == 0
    assert DEFAULT_SAMPLES == 256
