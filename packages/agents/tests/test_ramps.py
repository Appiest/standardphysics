"""ADA 2010 405 and 505.4: a ramp measured from its box, and handrails asked about rather than failed."""

from __future__ import annotations

import uuid

import pytest
from standardphysics_agents.checks import REGISTRY
from standardphysics_agents.checks.context import CheckContext
from standardphysics_agents.checks.ramps import ramp_run, ramps
from standardphysics_agents.checks.roles import ramps as ramp_nodes
from standardphysics_agents.findings import asks_of, resolve, to_finding
from standardphysics_agents.rules import VerificationLedger, load_pack
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, to_inches, to_meters
from standardphysics_pipeline.measure import PipelineMeasurements

RAMP_WIDTH_METERS = 1.0


def _node(label, centre, size, movable=False):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"{label}{centre}"), kind="object", label=label,
        raw_category=label, dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*centre), movable=movable,
    )


def _ramp(run_meters, rise_meters, width_meters=RAMP_WIDTH_METERS, label="Ramp"):
    """A ramp running along x, centred on the origin."""
    return _node(label, (0.0, 0.0, rise_meters / 2), (run_meters, width_meters, rise_meters))


def _railings(ramp, height_above_ramp_inches=36.0, sides=(1, -1)):
    top = ramp.dimensions.z + to_meters(height_above_ramp_inches)
    offset = ramp.dimensions.y / 2 + 0.05
    return [_node("Handrail", (0.0, side * offset, top / 2), (ramp.dimensions.x, 0.05, top)) for side in sides]


def _graph(*nodes, floor_meters=12.0):
    floor = _node("Floor", (0, 0, 0), (floor_meters, floor_meters, 0.02)).model_copy(update={"kind": "floor"})
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, *nodes])


def _context(graph):
    stop = Stop(name="Here", position=Vec3(x=0, y=0, z=0))
    return CheckContext(graph, Scenario(name="Test", stops=[stop, stop]), PipelineMeasurements(),
                        load_pack(), VerificationLedger())


def _observed(graph, rule_id):
    found = [observation for observation in ramps(_context(graph)) if observation.rule_id == rule_id]
    assert len(found) == 1, found
    return found[0]


def _outcome(graph, rule_id):
    observation = _observed(graph, rule_id)
    return resolve(observation, load_pack().by_id(rule_id), graph)


GENTLE = dict(run_meters=3.6, rise_meters=0.3)
"""About 12 inches of rise over 142 inches of run: 1 in 12 exactly."""


def test_the_ramp_rules_have_a_check_behind_them():
    covered = frozenset().union(*[rule_ids for rule_ids, _ in REGISTRY])
    for rule_id in ("ramp_running_slope", "ramp_rise", "ramp_clear_width", "ramp_landing_length",
                    "ramp_handrails", "handrail_height"):
        assert rule_id in covered
        assert load_pack().by_id(rule_id).tier == 1


@pytest.mark.parametrize("label", ["Ramp", "Accessible ramp", "Wheelchair ramp", "Incline", "Slope"])
def test_every_name_for_a_ramp_is_a_ramp(label):
    assert ramp_nodes(_graph(_ramp(**GENTLE, label=label)))


def test_rise_run_and_width_come_from_the_box():
    run = ramp_run(_ramp(3.6, 0.3, width_meters=1.0))
    assert run.rise == pytest.approx(to_inches(0.3))
    assert run.run == pytest.approx(to_inches(3.6))
    assert run.width == pytest.approx(to_inches(1.0))


def test_a_one_in_twelve_ramp_with_rails_and_clear_landings_passes_everything():
    ramp = _ramp(**GENTLE)
    graph = _graph(ramp, *_railings(ramp))
    for rule_id in ("ramp_running_slope", "ramp_rise", "ramp_clear_width", "ramp_landing_length", "ramp_handrails"):
        assert _outcome(graph, rule_id)[0] == "passes", rule_id


def test_a_steep_ramp_is_a_problem_with_the_length_it_needs():
    graph = _graph(_ramp(run_meters=2.4, rise_meters=0.3))
    outcome, text = _outcome(graph, "ramp_running_slope")
    assert outcome == "problem"
    observation = _observed(graph, "ramp_running_slope")
    assert observation.required_inches == pytest.approx(12 * to_inches(0.3))
    assert text.title == "The ramp is too steep"
    assert "1 inch for every 12 inches" in text.detail


def test_a_ramp_that_climbs_more_than_30_inches_in_one_run_is_a_problem():
    graph = _graph(_ramp(run_meters=10.0, rise_meters=0.8), floor_meters=30.0)
    outcome, text = _outcome(graph, "ramp_rise")
    assert outcome == "problem"
    assert "30 inches" in text.detail


def test_a_narrow_ramp_is_a_problem():
    graph = _graph(_ramp(**GENTLE, width_meters=0.8))
    outcome, text = _outcome(graph, "ramp_clear_width")
    assert outcome == "problem"
    assert text.title == "The ramp is too narrow"
    assert "36 inches" in text.detail


def test_a_ramp_without_railings_in_the_scan_asks_for_a_photo_and_never_fails():
    graph = _graph(_ramp(**GENTLE))
    observation = _observed(graph, "ramp_handrails")
    outcome, text = resolve(observation, load_pack().by_id("ramp_handrails"), graph)
    assert outcome == "question"
    assert asks_of(observation, load_pack().by_id("ramp_handrails"), graph) == "photo"
    assert "photo" in text.title


def test_a_railing_on_one_side_only_is_still_a_question():
    ramp = _ramp(**GENTLE)
    graph = _graph(ramp, *_railings(ramp, sides=(1,)))
    assert _outcome(graph, "ramp_handrails")[0] == "question"


def test_a_ramp_that_climbs_six_inches_or_less_needs_no_handrails():
    graph = _graph(_ramp(run_meters=2.0, rise_meters=to_meters(5.0)))
    observation = _observed(graph, "ramp_handrails")
    assert observation.satisfied
    assert observation.facts["applies"] is False
    assert _outcome(graph, "ramp_handrails")[1].title == "The ramp is low enough to go without handrails"


def test_a_chair_in_the_landing_is_a_problem_that_names_it():
    ramp = _ramp(**GENTLE)
    chair = _node("Chair", (1.8 + 0.6, 0.0, 0.45), (0.45, 0.45, 0.9), movable=True)
    graph = _graph(ramp, *_railings(ramp), chair)
    finding = to_finding(_observed(graph, "ramp_landing_length"), load_pack().by_id("ramp_landing_length"),
                         graph, uuid.uuid4())
    assert finding.outcome == "problem"
    assert "chair" in finding.detail.casefold()
    assert "chair" in (finding.fix or "").casefold()
    assert chair.id in finding.locus.node_ids


def test_a_landing_past_the_edge_of_the_scan_is_asked_about():
    graph = _graph(_ramp(**GENTLE), floor_meters=4.0)
    assert _outcome(graph, "ramp_landing_length")[0] == "question"


def test_a_named_landing_too_short_is_a_problem():
    ramp = _ramp(**GENTLE)
    landing = _node("Ramp landing", (1.8 + 0.5, 0.0, 0.15), (1.0, 1.0, 0.3))
    graph = _graph(ramp, *_railings(ramp), landing)
    outcome, text = _outcome(graph, "ramp_landing_length")
    assert outcome == "problem"
    assert text.title == "The landing at one end of the ramp is too short"


@pytest.mark.parametrize(("height", "outcome"), [(36.0, "passes"), (42.0, "problem"), (30.0, "problem")])
def test_a_ramp_handrail_is_measured_above_the_ramp_surface(height, outcome):
    ramp = _ramp(**GENTLE)
    graph = _graph(ramp, *_railings(ramp, height_above_ramp_inches=height, sides=(1,)))
    assert _outcome(graph, "handrail_height")[0] == outcome
    assert _observed(graph, "handrail_height").measured_inches == pytest.approx(height)
