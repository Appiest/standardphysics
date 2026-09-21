from __future__ import annotations

import math
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from standardphysics_agents.router import LocalPolicyRouter, parse_decision
from standardphysics_agents.rules import VerificationLedger
from standardphysics_agents.workflows import (
    DEFAULT_PROFILES,
    LOW_REACH_PROFILE,
    WHEELCHAIR_PROFILE,
    Interaction,
    TypeSafeWorkflowConfigurationError,
    Workflow,
    WorkflowBatchResult,
    build_entrance_object_workflows,
    build_workflow_suite,
    evaluate_workflow,
    run_typesafe_workflow_batch,
    run_workflow_batch,
    workflow_candidate_rejection,
)
from standardphysics_contracts import LidarMesh, LidarMeshPart, Mat4, WidthResult
from standardphysics_fixtures import node_id


def test_wheelchair_uses_the_open_corridor_instead_of_an_unrelated_table_gap(graph, scenario, pipeline):
    workflow = Workflow(
        id="order-and-pick-up",
        title="Order and pick up a drink",
        scenario=scenario,
    )

    result = evaluate_workflow(graph, workflow, WHEELCHAIR_PROFILE, pipeline)

    counter_to_pickup = result.legs[1]
    assert counter_to_pickup.origin == "Counter"
    assert counter_to_pickup.destination == "Pickup"
    assert counter_to_pickup.required_width_inches == 36.0
    measured = pipeline.route_clear_width(graph, scenario, 1)
    assert counter_to_pickup.measured_width_inches == measured.inches
    assert counter_to_pickup.measured_width_inches > 36.0
    assert counter_to_pickup.collision_free is True
    assert counter_to_pickup.meets_clearance is True


def test_captured_mesh_blocks_a_route_missing_from_the_floor_plan(graph, scenario, pipeline):
    # A vertical measured surface crossing the aisle at scene y=-1.0. The raw
    # AR mesh is Y-up, so scene y maps to negative AR z.
    mesh = LidarMesh(
        floorY=0.0,
        parts=[
            LidarMeshPart(
                id=uuid4(),
                transform=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                vertices=[
                    -1, 0, 1,
                    1, 0, 1,
                    -1, 1, 1,
                    1, 1, 1,
                ],
                triangles=[0, 1, 2, 1, 3, 2],
            )
        ],
    )
    workflow = Workflow(id="enter", title="Reach the counter", scenario=scenario)

    result = evaluate_workflow(
        graph, workflow, WHEELCHAIR_PROFILE, pipeline, lidar_mesh=mesh
    )

    entrance_to_counter = result.legs[0]
    assert entrance_to_counter.floor_plan_collision is False
    assert entrance_to_counter.mesh_collision is True
    assert entrance_to_counter.collision_free is False


def test_coffee_on_the_table_is_checked_from_a_collision_free_approach(
    graph, scenario, pipeline
):
    workflow = Workflow(
        id="get-coffee",
        title="Get coffee from the table",
        scenario=scenario,
        interactions=(
            Interaction(
                id="coffee",
                title="Coffee on the table",
                target_node_id=node_id("table_3"),
                height="top",
            ),
        ),
    )

    result = evaluate_workflow(graph, workflow, WHEELCHAIR_PROFILE, pipeline)

    coffee = result.interactions[0]
    assert coffee.needs_measurement is False
    assert coffee.approach_collision_free is True
    assert round(coffee.measured_height_inches, 1) == 29.5
    assert coffee.height_reachable is True
    assert coffee.reachable is True


def test_missing_cashier_drawer_geometry_is_requested_instead_of_guessed(
    graph, scenario, pipeline
):
    workflow = Workflow(
        id="cashier-drawer",
        title="Reach the cashier drawer",
        scenario=scenario,
        interactions=(
            Interaction(
                id="drawer",
                title="Cashier drawer",
                target_node_id=None,
                height="center",
            ),
        ),
    )

    result = evaluate_workflow(graph, workflow, WHEELCHAIR_PROFILE, pipeline)

    drawer = result.interactions[0]
    assert drawer.needs_measurement is True
    assert drawer.reachable is None
    assert result.passed is False


def test_lower_reach_profile_flags_a_countertop_it_cannot_reach(
    graph, scenario, pipeline
):
    workflow = Workflow(
        id="pay",
        title="Pay at the counter",
        scenario=scenario,
        interactions=(
            Interaction(
                id="card-reader",
                title="Card reader on the counter",
                target_node_id=node_id("counter"),
                height="top",
            ),
        ),
    )

    result = evaluate_workflow(graph, workflow, LOW_REACH_PROFILE, pipeline)

    reach = result.interactions[0]
    assert round(reach.measured_height_inches, 1) == 47.0
    assert reach.maximum_reach_inches == 36.0
    assert reach.height_reachable is False
    assert reach.reachable is False


def test_profiles_have_distinct_avatars_and_paths_always_face_forward(
    graph, scenario, pipeline
):
    assert {profile.avatar for profile in DEFAULT_PROFILES} == {
        "wheelchair",
        "mobility-aid",
        "short-stature",
        "larger-body",
    }
    result = evaluate_workflow(
        graph,
        Workflow(id="order", title="Order", scenario=scenario),
        WHEELCHAIR_PROFILE,
        pipeline,
    )
    route = result.legs[0].route
    for here, following in zip(route, route[1:]):
        dx = following.position.x - here.position.x
        dy = following.position.y - here.position.y
        if dx or dy:
            expected = math.degrees(math.atan2(dy, dx))
            assert abs(here.heading_degrees - expected) < 1e-9


def test_one_thousand_ready_workflow_runs_use_bounded_parallelism(
    graph, scenario, stub, pack, ledger
):
    class TrackingRouter:
        provider = "test_typesafe"

        def __init__(self):
            self.lock = threading.Lock()
            self.active = 0
            self.peak = 0

        def decide(self, state):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.001)
            with self.lock:
                self.active -= 1
            return parse_decision({"action": "DONE"}, state.findings, self.provider)

    router = TrackingRouter()
    workflow = Workflow(id="get-coffee", title="Get coffee", scenario=scenario)

    result = run_workflow_batch(
        graph,
        workflows=[workflow],
        profiles=[WHEELCHAIR_PROFILE],
        samples=1_000,
        max_workers=8,
        measure_factory=lambda: stub,
        router_factory=lambda: router,
        rules=pack,
        ledger=ledger,
    )

    assert result.total_runs == 1_000
    assert result.violating_trials == 1_000
    assert result.completed_runs == 1_000
    assert 1 < router.peak <= 8
    assert result.action_counts == {"DONE": 1_000}
    assert result.rejection_rate == 0.0
    feedback = result.feedback[0]
    assert feedback.workflow_id == "get-coffee"
    assert feedback.profile_id == "wheelchair"
    assert feedback.trials == 1_000
    assert feedback.passed_trials == 0
    assert feedback.clearance_failure_trials == 1_000
    assert feedback.floor_plan_collision_trials == 0
    assert feedback.requires_design_change is True
    assert result.best_run is not None
    assert result.recommended_graph == graph
    assert len(result.best_run.suite_evaluations) == 1


def test_one_thousand_trials_can_be_submitted_to_one_thousand_workers(
    graph, scenario, stub, pack, ledger, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor

    requested_workers = []

    def record_executor(*, max_workers):
        requested_workers.append(max_workers)
        # Keep the unit test lightweight while proving production receives the
        # requested all-at-once fan-out size.
        return RealThreadPoolExecutor(max_workers=2)

    monkeypatch.setattr(
        "standardphysics_agents.workflows.ThreadPoolExecutor", record_executor
    )
    workflow = Workflow(id="get-coffee", title="Get coffee", scenario=scenario)

    result = run_workflow_batch(
        graph,
        workflows=[workflow],
        profiles=[WHEELCHAIR_PROFILE],
        samples=1_000,
        max_workers=1_000,
        measure_factory=lambda: stub,
        router_factory=LocalPolicyRouter,
        rules=pack,
        ledger=ledger,
    )

    assert requested_workers == [1_000]
    assert result.total_runs == 1_000


def test_a_trial_with_a_final_ada_problem_is_a_violating_trial():
    evaluation = SimpleNamespace(passed=True)
    run = SimpleNamespace(
        completed=True,
        rejected=None,
        evaluation=evaluation,
        suite_evaluations=(evaluation,),
        steps=(SimpleNamespace(assessment=SimpleNamespace(problems=[object()])),),
    )

    result = WorkflowBatchResult(runs=(run,), max_workers=1)

    assert result.violating_trials == 1


def test_workflow_progress_reports_whichever_trial_finishes_first(
    graph, scenario, stub, pack, ledger, monkeypatch
):
    slow_started = threading.Event()
    fast_finished = threading.Event()
    release_slow = threading.Event()
    progress_seen = threading.Event()

    def fake_loop(graph, scenario, *args, **kwargs):
        if scenario.name == "slow":
            slow_started.set()
            assert release_slow.wait(timeout=2)
        else:
            fast_finished.set()
        return []

    monkeypatch.setattr("standardphysics_agents.workflows.run_loop", fake_loop)
    workflows = [
        Workflow(id="slow", title="Slow", scenario=scenario.model_copy(update={"name": "slow"})),
        Workflow(id="fast", title="Fast", scenario=scenario.model_copy(update={"name": "fast"})),
    ]
    outcome = []

    def run_batch():
        outcome.append(run_workflow_batch(
            graph,
            workflows=workflows,
            profiles=[WHEELCHAIR_PROFILE],
            samples=2,
            max_workers=2,
            measure_factory=lambda: stub,
            router_factory=object,
            rules=pack,
            ledger=ledger,
            on_progress=lambda completed: progress_seen.set(),
        ))

    thread = threading.Thread(target=run_batch)
    thread.start()
    try:
        assert slow_started.wait(timeout=1)
        assert fast_finished.wait(timeout=1)
        assert progress_seen.wait(timeout=1)
    finally:
        release_slow.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert outcome[0].total_runs == 2
    assert outcome[0].violating_trials == 2


def test_typesafe_batch_fails_early_when_the_api_key_is_missing(
    graph, scenario, stub, pack, ledger, monkeypatch
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)

    with pytest.raises(TypeSafeWorkflowConfigurationError, match="TYPESAFE_API_KEY"):
        run_typesafe_workflow_batch(
            graph,
            workflows=[Workflow(id="order", title="Order", scenario=scenario)],
            profiles=[WHEELCHAIR_PROFILE],
            samples=1,
            max_workers=1,
            measure_factory=lambda: stub,
            rules=pack,
            ledger=ledger,
        )


def test_typesafe_batch_refuses_to_claim_ada_results_without_verified_rules(
    graph, scenario, stub, pack, monkeypatch
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://typesafe.example")

    with pytest.raises(
        TypeSafeWorkflowConfigurationError, match="human-verified ADA rule"
    ):
        run_typesafe_workflow_batch(
            graph,
            workflows=[Workflow(id="order", title="Order", scenario=scenario)],
            profiles=[WHEELCHAIR_PROFILE],
            samples=1,
            max_workers=1,
            measure_factory=lambda: stub,
            rules=pack,
            ledger=VerificationLedger(),
        )


def test_a_candidate_layout_cannot_regress_a_previously_passing_workflow(
    graph, scenario
):
    class RevisionMeasurements:
        def route_clear_width(self, candidate, route, leg_index):
            inches = 40.0 if candidate.revision == 0 else 20.0
            return WidthResult(
                inches=inches,
                pinch_point=route.stops[leg_index + 1].position,
                blocking_node_ids=[],
                path=[
                    route.stops[leg_index].position,
                    route.stops[leg_index + 1].position,
                ],
            )

    candidate = graph.model_copy(update={"revision": graph.revision + 1})
    reason = workflow_candidate_rejection(
        graph,
        candidate,
        workflows=[Workflow(id="order", title="Order", scenario=scenario)],
        profiles=[WHEELCHAIR_PROFILE],
        measure=RevisionMeasurements(),
    )

    assert reason == "workflow_regression:order:wheelchair"


def test_suite_builder_covers_every_stop_direction_and_task_approach(
    graph, scenario
):
    coffee = Interaction(
        id="coffee",
        title="Coffee on the table",
        target_node_id=node_id("table_3"),
        height="top",
    )

    workflows = build_workflow_suite(
        graph,
        scenario,
        interactions=[coffee],
    )

    route_pairs = {
        (workflow.scenario.stops[0].name, workflow.scenario.stops[-1].name)
        for workflow in workflows
        if workflow.id.startswith("route:")
    }
    expected_pairs = {
        (origin.name, destination.name)
        for origin in scenario.stops
        for destination in scenario.stops
        if origin != destination
    }
    task_workflows = [item for item in workflows if item.id.startswith("task:coffee")]
    assert route_pairs == expected_pairs
    assert len(task_workflows) == sum(
        stop.anchor_node_id != coffee.target_node_id for stop in scenario.stops
    )
    assert all(item.interactions == (coffee,) for item in task_workflows)
    assert all(
        item.scenario.stops[-1].anchor_node_id == coffee.target_node_id
        for item in task_workflows
    )
    known_approach = next(
        stop for stop in scenario.stops if stop.anchor_node_id == coffee.target_node_id
    )
    assert all(
        item.scenario.stops[-1].position == known_approach.position
        for item in task_workflows
    )


def test_every_inferred_entrance_gets_a_route_to_every_object(
    graph, scenario
):
    front_door = graph.by_id(node_id("door_front"))
    side_opening = front_door.model_copy(
        update={
            "id": uuid4(),
            "kind": "opening",
            "label": "Side opening",
            "transform": Mat4.translation(4.0, 0.0, 1.05),
        }
    )
    graph = graph.model_copy(update={"nodes": [*graph.nodes, side_opening]})
    entrance = scenario.stops[0].model_copy(
        update={"name": "Main entrance", "anchor_node_id": front_door.id}
    )
    duplicate_exit = entrance.model_copy(update={"name": "Exit"})
    inferred = scenario.model_copy(update={"stops": [entrance, duplicate_exit]})

    workflows = build_entrance_object_workflows(graph, inferred)

    object_ids = {node.id for node in graph.nodes if node.kind == "object"}
    entrance_ids = {front_door.id, side_opening.id}
    assert {
        (
            workflow.scenario.stops[0].anchor_node_id,
            workflow.scenario.stops[-1].anchor_node_id,
        )
        for workflow in workflows
    } == {
        (entrance_id, object_id)
        for entrance_id in entrance_ids
        for object_id in object_ids
    }
    assert len(workflows) == len(entrance_ids) * len(object_ids)
    assert next(
        workflow
        for workflow in workflows
        if workflow.scenario.stops[0].anchor_node_id == front_door.id
    ).scenario.stops[0].name == "Main entrance"
