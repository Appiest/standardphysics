from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from standardphysics_agents import (
    WHEELCHAIR_PROFILE,
    TypeSafeCallBudget,
    Workflow,
    analyze_environment_physics,
    graph_hash,
    run_adaptive_redesign,
)
from standardphysics_agents.adaptive_redesign import _layout_evidence
from standardphysics_agents.redesign import RedesignResult
from standardphysics_agents.router import (
    ChoiceQuestion,
    SystemOneClient,
    SystemOneError,
)
from standardphysics_contracts import LidarMesh, LidarMeshPart
from standardphysics_fixtures import (
    FixtureMeasurements,
    build_graph,
    build_scenario,
    node_id,
)


def _mesh(*application_triangles) -> LidarMesh:
    vertices = []
    indices = []
    for triangle in application_triangles:
        for x, y, z in triangle:
            vertices.extend((x, z, -y))
            indices.append(len(indices))
    return LidarMesh(
        floorY=0,
        parts=[
            LidarMeshPart(
                id=uuid4(),
                transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                vertices=vertices,
                triangles=indices,
            )
        ],
    )


def test_one_inch_riser_slope_roll_tip_and_environment_routes_are_screened():
    slope = math.tan(math.radians(40))
    mesh = _mesh(
        ((0, 0, 0), (1, 0, slope), (0, 1, 0)),
        ((2, 0, 0), (2, 0, 0.0254), (2.5, 0, 0)),
    )
    result = analyze_environment_physics(
        build_graph(), build_scenario(), FixtureMeasurements(), mesh=mesh
    )

    riser = next(item for item in result.observations if item.kind == "level_change")
    roll = next(item for item in result.observations if item.kind == "uncontrolled_roll")
    tip = next(item for item in result.observations if item.kind == "wheelchair_tip")
    assert riser.measured_value == pytest.approx(1.0)
    assert riser.status == "potential_barrier"
    assert roll.measured_value > 0
    assert tip.status == "potential_barrier"
    assert any(route.purpose == "evacuation" for route in result.routes)
    assert any(route.purpose == "seat_to_cashier" for route in result.routes)
    assert all(
        route.distance_inches > 0
        for route in result.routes
        if route.reachable and route.distance_inches is not None
    )
    assert result.seats_found == 6
    assert result.cashiers_found >= 1
    assert result.exits_found >= 1


def test_every_customer_destination_is_screened_from_every_other_destination():
    graph = build_graph()
    sofa = graph.by_id(node_id("table_1")).model_copy(
        update={"id": uuid4(), "label": "Sofa", "raw_category": "sofa"}
    )
    computer = graph.by_id(node_id("table_2")).model_copy(
        update={"id": uuid4(), "label": "Computer", "raw_category": "computer"}
    )
    graph = graph.model_copy(update={"nodes": [*graph.nodes, sofa, computer]})

    result = analyze_environment_physics(
        graph, build_scenario(), FixtureMeasurements()
    )

    customer_target_ids = {
        node.id
        for node in graph.nodes
        if node.kind in {"door", "opening", "object"}
    } | {
        stop.anchor_node_id
        for stop in build_scenario().stops
        if stop.anchor_node_id is not None
    }
    expected_pairs = {
        (origin_id, destination_id)
        for origin_id in customer_target_ids
        for destination_id in customer_target_ids
        if origin_id != destination_id
    }
    actual_pairs = {
        (route.origin_node_id, route.destination_node_id)
        for route in result.routes
    }
    purposes = {
        (route.origin_node_id, route.destination_node_id): route.purpose
        for route in result.routes
    }

    assert actual_pairs == expected_pairs
    assert purposes[(node_id("chair_1"), node_id("door_front"))] == "evacuation"
    assert purposes[(node_id("counter"), node_id("door_front"))] == "evacuation"
    assert purposes[(node_id("chair_1"), node_id("counter"))] == "seat_to_cashier"
    assert purposes[(node_id("table_1"), node_id("door_front"))] == "customer_access"


def test_missing_lidar_floor_reference_degrades_to_a_limitation():
    mesh = _mesh(((0, 0, 0), (1, 0, 0), (0, 1, 0))).model_copy(
        update={"floorY": None}
    )
    result = analyze_environment_physics(
        build_graph(), build_scenario(), FixtureMeasurements(), mesh=mesh
    )
    assert result.mesh_triangles_checked == 0
    assert any("no floor reference" in item for item in result.limitations)


def test_adaptive_redesign_stops_on_rejection_and_never_mutates_scan(monkeypatch):
    measured = build_graph()
    original_hash = graph_hash(measured)
    moved = measured.model_copy(
        update={
            "revision": 1,
            "nodes": [
                node.model_copy(
                    update={
                        "transform": node.transform.model_copy(
                            update={
                                "m": [
                                    value + (0.1 if index == 3 else 0)
                                    for index, value in enumerate(node.transform.m)
                                ]
                            }
                        )
                    }
                )
                if node.id == node_id("chair_1")
                else node
                for node in measured.nodes
            ],
        }
    )
    proposals = [
        RedesignResult(moved, "astra-test", True, ()),
        RedesignResult(None, "astra-test", False, ("nothing_measurable_improved",)),
    ]

    def propose(*args, **kwargs):
        return proposals.pop(0)

    class PreferCandidate:
        def rank_layouts(self, candidates):
            return (SimpleNamespace(candidate=candidates[1]), SimpleNamespace(candidate=candidates[0]))

    monkeypatch.setattr(
        "standardphysics_agents.adaptive_redesign.propose_redesign", propose
    )
    from standardphysics_agents import DEFAULT_PROFILES, build_workflow_suite, load_pack

    from standardphysics_api.stages import preview_ledger

    workflows = build_workflow_suite(measured, build_scenario())
    result = run_adaptive_redesign(
        measured,
        workflows=workflows,
        profiles=list(DEFAULT_PROFILES),
        measure=FixtureMeasurements(),
        rules=load_pack(),
        ledger=preview_ledger(),
        rounds=4,
        budget=TypeSafeCallBudget(4),
        intelligence=PreferCandidate(),
    )
    assert [item.accepted for item in result.rounds] == [True, False]
    assert result.astra_calls == 2
    assert result.graph == moved
    assert graph_hash(measured) == original_hash


def test_layout_evidence_names_measured_actionable_blockers():
    from standardphysics_agents import DEFAULT_PROFILES, build_workflow_suite

    graph = build_graph()
    evidence = _layout_evidence(
        graph,
        graph,
        build_workflow_suite(graph, build_scenario()),
        list(DEFAULT_PROFILES),
        FixtureMeasurements(),
        None,
    )
    assert evidence["actionable_failure_count"] > 0
    failure = evidence["actionable_failures"][0]
    assert failure["measured_width_inches"] < failure["required_width_inches"]
    assert failure["movable_blocker_ids"]
    assert failure["movable_blocker_labels"]


def test_adaptive_redesign_skips_astra_without_actionable_furniture_failure(
    monkeypatch,
):
    measured = build_graph()

    def evidence(*args, **kwargs):
        return {
            "actionable_failures": [],
            "disruption": {
                "moved_object_count": 0,
                "total_translation_meters": 0,
                "fixed_objects_moved": 0,
                "inventory_preserved": True,
            },
        }

    def unexpected(*args, **kwargs):
        raise AssertionError("Astra must not be called without an actionable failure")

    monkeypatch.setattr(
        "standardphysics_agents.adaptive_redesign._layout_evidence", evidence
    )
    monkeypatch.setattr(
        "standardphysics_agents.adaptive_redesign.propose_redesign", unexpected
    )
    result = run_adaptive_redesign(
        measured,
        workflows=[
            Workflow(id="room", title="Room", scenario=build_scenario())
        ],
        profiles=[WHEELCHAIR_PROFILE],
        measure=FixtureMeasurements(),
        rules=SimpleNamespace(),
        ledger=SimpleNamespace(),
        rounds=4,
        budget=TypeSafeCallBudget(4),
    )
    assert result.astra_calls == 0
    assert result.rounds[0].reasons == ["no_actionable_furniture_failure"]


class _SystemOneTransport:
    def __init__(self):
        self.calls = 0

    def post(self, url, body, headers):
        self.calls += 1
        request = json.loads(body)
        options = list(request["questions"]["q"]["criteria"])
        return json.dumps(
            {
                "model": "jev-test",
                "answers": {
                    "q": {
                        "type": "choice",
                        "choice": options[0],
                        "probabilities": {options[0]: 1.0, options[1]: 0.0},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ).encode()


def test_typesafe_budget_is_thread_safe_and_exhaustion_makes_no_provider_call():
    budget = TypeSafeCallBudget(73)
    with ThreadPoolExecutor(max_workers=16) as executor:
        reserved = list(executor.map(lambda _: budget.reserve(), range(1000)))
    assert sum(reserved) == 73
    assert budget.used == 73

    transport = _SystemOneTransport()
    single = TypeSafeCallBudget(1)
    client = SystemOneClient(api_key="test", transport=transport, budget=single)
    question = ChoiceQuestion(instructions="Choose", criteria={"a": None, "b": None})
    client.evaluate({}, {"q": question})
    with pytest.raises(SystemOneError, match="typesafe_call_budget_exhausted"):
        client.evaluate({}, {"q": question})
    assert transport.calls == 1


def test_astra_works_on_remaining_rule_problems_with_what_the_route_trials_learned(monkeypatch):
    from standardphysics_agents import DEFAULT_PROFILES, build_workflow_suite, load_pack

    from standardphysics_api.stages import preview_ledger

    measured = build_graph()
    scenario = build_scenario()
    card_reader = {"check_id": "protruding_objects", "movable_node_ids": [str(node_id("chair_1"))]}
    trials = {"decided_by": "typesafe", "kept_moves": [{"label": "Display case"}]}
    no_route_failures = {
        "actionable_failures": [],
        "disruption": {"moved_object_count": 0, "total_translation_meters": 0, "fixed_objects_moved": 0, "inventory_preserved": True},
    }
    monkeypatch.setattr("standardphysics_agents.adaptive_redesign._layout_evidence", lambda *args: dict(no_route_failures))
    monkeypatch.setattr(
        "standardphysics_agents.adaptive_redesign._rule_problem_evidence",
        lambda *args: {"rule_problem_count": 1, "actionable_rule_problems": [card_reader], "actionable_rule_problem_count": 1},
    )
    astra_saw = []

    def propose(current, workflows, profiles, feedback, measure, **kwargs):
        astra_saw.extend(feedback)
        return RedesignResult(measured.model_copy(update={"revision": 1}), "astra-test", True, ())

    class RecordingJev:
        seen = []

        def rank_layouts(self, candidates):
            self.seen.extend(candidates)
            return (SimpleNamespace(candidate=candidates[0]), SimpleNamespace(candidate=candidates[1]))

    monkeypatch.setattr("standardphysics_agents.adaptive_redesign.propose_redesign", propose)
    jev = RecordingJev()
    result = run_adaptive_redesign(
        measured,
        workflows=build_workflow_suite(measured, scenario),
        profiles=list(DEFAULT_PROFILES),
        measure=FixtureMeasurements(),
        rules=load_pack(),
        ledger=preview_ledger(),
        rounds=2,
        budget=TypeSafeCallBudget(2),
        intelligence=jev,
        scenario=scenario,
        route_trials=trials,
    )
    assert result.astra_calls == 1
    assert astra_saw[0]["route_trials"] == trials
    assert astra_saw[0]["actionable_rule_problems"] == [card_reader]
    assert all(candidate.usability_evidence["actionable_rule_problem_count"] == 1 for candidate in jev.seen)
    assert "route_trials" not in jev.seen[0].usability_evidence


def test_route_trial_evidence_names_kept_moves_and_failing_journeys():
    from standardphysics_agents.adaptive_redesign import route_trial_evidence

    measured = build_graph()
    case = node_id("case_east")
    moved = measured.model_copy(update={"nodes": [
        node.model_copy(update={"transform": node.transform.model_copy(update={
            "m": [value + (0.14 if index == 3 else 0) for index, value in enumerate(node.transform.m)]
        })}) if node.id == case else node
        for node in measured.nodes
    ]})
    failing = SimpleNamespace(workflow_title="Order a drink", profile_title="Wheelchair user", passed_trials=1, trials=4, blocking_node_ids=[str(case)])
    passing = SimpleNamespace(workflow_title="Leave", profile_title="Walker", passed_trials=4, trials=4, blocking_node_ids=[])
    result = SimpleNamespace(recommended_graph=moved, feedback=[failing, passing], total_runs=8, rejected_runs=1, action_counts={"FIX": 9})

    evidence = route_trial_evidence(measured, result, "typesafe")

    assert evidence["decided_by"] == "typesafe"
    assert evidence["action_counts"] == {"FIX": 9}
    [kept] = evidence["kept_moves"]
    assert kept["node_id"] == str(case)
    assert evidence["failing_workflow_count"] == 1
    assert evidence["failing_workflows"][0]["blocker_labels"] == [next(n.label for n in measured.nodes if n.id == case)]


def test_only_floor_placement_problems_count_as_astra_work():
    from standardphysics_agents import load_pack
    from standardphysics_agents.adaptive_redesign import PLACEMENT_CHECKS, _rule_problem_evidence

    from standardphysics_api.stages import preview_ledger

    graph = build_graph()
    evidence = _rule_problem_evidence(graph, build_scenario(), FixtureMeasurements(), load_pack(), preview_ledger())

    assert evidence["actionable_rule_problem_count"] >= 1
    assert all(item["check_id"] in PLACEMENT_CHECKS for item in evidence["actionable_rule_problems"])
    assert all(item["movable_labels"] for item in evidence["actionable_rule_problems"])
    assert evidence["rule_problem_count"] >= evidence["actionable_rule_problem_count"]
    assert _rule_problem_evidence(graph, None, FixtureMeasurements(), load_pack(), preview_ledger())["actionable_rule_problems"] == []
