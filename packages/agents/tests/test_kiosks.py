"""Self-order kiosks, pickup counters and self-serve stations reach the reach and clear floor rules."""

from __future__ import annotations

import uuid

import pytest
from standardphysics_agents.checks import REGISTRY
from standardphysics_agents.checks.context import CheckContext
from standardphysics_agents.checks.kiosks import kiosks
from standardphysics_agents.checks.roles import kiosks as kiosk_nodes
from standardphysics_agents.checks.roles import point_of_sale, self_service, service_counters
from standardphysics_agents.checks.self_service import self_service_reach
from standardphysics_agents.checks.service_counter import service_counter_height
from standardphysics_agents.findings import asks_of, resolve, to_finding
from standardphysics_agents.rules import VerificationLedger, load_pack
from standardphysics_contracts import Mat4, Scenario, SceneGraph, SceneNode, Stop, Vec3, to_meters
from standardphysics_pipeline.measure import PipelineMeasurements


def _node(label, centre, size, movable=False):
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_OID, f"{label}{centre}"), kind="object", label=label,
        raw_category=label, dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
        transform=Mat4.translation(*centre), movable=movable,
    )


def _graph(*nodes):
    floor = _node("Floor", (0, 0, 0), (10, 10, 0.02)).model_copy(update={"kind": "floor"})
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, *nodes])


def _context(graph):
    stop = Stop(name="Here", position=Vec3(x=0, y=0, z=0))
    return CheckContext(graph, Scenario(name="Test", stops=[stop, stop]), PipelineMeasurements(),
                        load_pack(), VerificationLedger())


def _one(observations, rule_id):
    found = [observation for observation in observations if observation.rule_id == rule_id]
    assert len(found) == 1, found
    return found[0]


def _resolved(graph, observation):
    return resolve(observation, load_pack().by_id(observation.rule_id), graph)


def _kiosk_on_a_counter():
    counter = _node("Ordering counter", (0.0, 0.0, 0.45), (1.5, 0.6, 0.9))
    kiosk = _node("Self-order kiosk", (0.0, 0.0, 0.9 + 0.15), (0.3, 0.3, 0.3), movable=True)
    return counter, kiosk


def test_kiosk_and_self_service_rules_run_in_production():
    covered = frozenset().union(*[rule_ids for rule_ids, _ in REGISTRY])
    for rule_id in ("kiosk_reach", "kiosk_clear_floor", "self_service_reach"):
        assert rule_id in covered
        rule = load_pack().by_id(rule_id)
        assert rule.tier == 1 and rule.measurable
    assert load_pack().by_id("reach_range").tier == 3


@pytest.mark.parametrize("label", [
    "Kiosk", "Self-order kiosk", "Ordering kiosk", "Ordering machine", "Self-service kiosk", "Touchscreen",
    "Order screen", "Self checkout", "Card kiosk",
])
def test_every_name_for_a_kiosk_is_a_kiosk(label):
    assert kiosk_nodes(_graph(_node(label, (0, 0, 0.8), (0.5, 0.5, 1.6))))


@pytest.mark.parametrize("label", [
    "Pickup counter", "Pick-up counter", "Pickup shelf", "Handoff counter", "Hand-off shelf", "Order pickup",
    "Drink pickup", "Beverage pickup", "To-go shelf", "Transaction counter", "Cashier counter",
])
def test_a_pickup_counter_is_a_service_counter(label):
    assert service_counters(_graph(_node(label, (0, 0, 0.5), (1.5, 0.5, 1.0))))


@pytest.mark.parametrize("label", ["Cash drawer", "Cashier drawer", "Cash box", "Tablet POS", "Square reader", "Tip screen"])
def test_a_tip_screen_or_cash_drawer_is_a_point_of_sale(label):
    assert point_of_sale(_graph(_node(label, (0, 0, 1.0), (0.2, 0.2, 0.1))))


def test_a_pickup_counter_too_high_to_reach_is_a_904_problem():
    graph = _graph(_node("Pickup counter", (0.0, 0.0, to_meters(42.0) / 2), (1.5, 0.5, to_meters(42.0))))
    observation = _one(service_counter_height(_context(graph)), "service_counter_height")
    assert _resolved(graph, observation)[0] == "problem"
    assert observation.measured_inches == pytest.approx(42.0)


def test_a_kiosk_on_a_counter_is_within_reach_with_room_in_front():
    graph = _graph(*_kiosk_on_a_counter())
    observations = kiosks(_context(graph))
    reach = _one(observations, "kiosk_reach")
    assert _resolved(graph, reach)[0] == "passes"
    assert reach.facts["top"] == pytest.approx(47.2, abs=0.1)
    assert _resolved(graph, _one(observations, "kiosk_clear_floor"))[0] == "passes"


def test_a_screen_mounted_above_reach_is_a_problem():
    graph = _graph(_node("Touchscreen", (0.0, 0.0, 1.5), (0.5, 0.05, 0.4)))
    outcome, text = _resolved(graph, _one(kiosks(_context(graph)), "kiosk_reach"))
    assert outcome == "problem"
    assert text.title == "The touchscreen is too high to reach"
    assert "48 inches" in text.detail


def test_a_tall_floor_kiosk_asks_where_its_controls_are_rather_than_failing():
    graph = _graph(_node("Self-order kiosk", (0.0, 0.0, 0.8), (0.5, 0.5, 1.6)))
    reach = _one(kiosks(_context(graph)), "kiosk_reach")
    outcome, text = _resolved(graph, reach)
    assert outcome == "question"
    assert asks_of(reach, load_pack().by_id("kiosk_reach"), graph) == "measurement"
    assert text.title == "Measure how high the kiosk's highest control is"


def test_a_kiosk_boxed_in_on_every_side_is_a_problem_naming_what_is_in_the_way():
    kiosk = _node("Self-order kiosk", (0.0, 0.0, 0.8), (0.5, 0.5, 1.6))
    cases = [_node("Display case", (x, y, 0.45), (0.4, 0.4, 0.9), movable=True)
             for x, y in ((0.0, -0.55), (0.0, 0.55), (0.55, 0.0), (-0.55, 0.0))]
    graph = _graph(kiosk, *cases)
    finding = to_finding(_one(kiosks(_context(graph)), "kiosk_clear_floor"),
                         load_pack().by_id("kiosk_clear_floor"), graph, uuid.uuid4())
    assert finding.outcome == "problem"
    assert "display case" in finding.detail.casefold()
    assert "30 by 48 inch" in (finding.fix or "")


def test_a_kiosk_against_a_wall_uses_the_open_side():
    wall = _node("Wall", (0.0, 0.4, 1.5), (4.0, 0.1, 3.0)).model_copy(update={"kind": "wall"})
    kiosk = _node("Ordering kiosk", (0.0, 0.1, 0.8), (0.5, 0.5, 1.6))
    graph = _graph(wall, kiosk)
    assert _resolved(graph, _one(kiosks(_context(graph)), "kiosk_clear_floor"))[0] == "passes"


@pytest.mark.parametrize("label", [
    "Condiment station", "Napkin dispenser", "Straw dispenser", "Lids", "Drink dispenser", "Beverage station",
    "Self-serve station", "Utensils",
])
def test_every_self_serve_station_is_found(label):
    assert self_service(_graph(_node(label, (0, 0, 0.5), (0.8, 0.5, 1.0))))


def test_a_condiment_station_within_reach_passes():
    graph = _graph(_node("Condiment station", (0.0, 0.0, 0.5), (1.0, 0.5, 1.0)))
    assert _resolved(graph, _one(self_service_reach(_context(graph)), "self_service_reach"))[0] == "passes"


def test_straws_on_a_high_shelf_are_a_904_5_1_problem():
    graph = _graph(_node("Straw dispenser", (0.0, 0.0, 1.4), (0.2, 0.2, 0.2)))
    outcome, text = _resolved(graph, _one(self_service_reach(_context(graph)), "self_service_reach"))
    assert outcome == "problem"
    assert text.title == "The straw dispenser is too high to reach"
