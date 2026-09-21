"""Task journeys through one measured space.

The interface stays deliberately small: a workflow is an ordered scenario, a
functional profile says how much clear space its avatar needs, and
``evaluate_workflow`` returns every measured leg.  Legal checks still come from
the verified rule pack; profile-specific results are usability evidence and do
not invent new ADA thresholds.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Literal
from uuid import UUID

from standardphysics_contracts import (
    LidarMesh,
    MeasurementProvider,
    Scenario,
    SceneGraph,
    SceneNode,
    Stop,
    Vec3,
    graph_hash,
    to_inches,
)
from standardphysics_contracts.rules import Tier

from .assess import Pass, assess
from .loop import Loop, LoopStep, StepResult, _do_fix, run_loop
from .mesh_collision import MeshCollisionIndex
from .router import LocalPolicyRouter, TypeSafeRouter
from .rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from .tracing import suspend_tracing

MAX_WORKFLOW_WORKERS = 1_000


@dataclass(frozen=True)
class FunctionalProfile:
    id: str
    title: str
    avatar: str
    body_width_inches: float
    required_width_inches: float
    turning_diameter_inches: float
    maximum_reach_inches: float | None = None
    compliance_profile: bool = False


WHEELCHAIR_PROFILE = FunctionalProfile(
    id="wheelchair",
    title="Wheelchair user",
    avatar="wheelchair",
    body_width_inches=30.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
    compliance_profile=True,
)

MOBILITY_AID_PROFILE = FunctionalProfile(
    id="mobility-aid",
    title="Mobility aid user",
    avatar="mobility-aid",
    body_width_inches=24.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
)

LOW_REACH_PROFILE = FunctionalProfile(
    id="lower-reach",
    title="Short stature or lower reach",
    avatar="short-stature",
    body_width_inches=18.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=36.0,
)

LARGER_BODY_PROFILE = FunctionalProfile(
    id="larger-body",
    title="Larger body",
    avatar="larger-body",
    body_width_inches=36.0,
    required_width_inches=36.0,
    turning_diameter_inches=60.0,
    maximum_reach_inches=48.0,
)

DEFAULT_PROFILES = (
    WHEELCHAIR_PROFILE,
    MOBILITY_AID_PROFILE,
    LOW_REACH_PROFILE,
    LARGER_BODY_PROFILE,
)

@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    scenario: Scenario
    interactions: tuple[Interaction, ...] = ()


@dataclass(frozen=True)
class Interaction:
    """Something a person must reach after arriving at its anchored stop."""

    id: str
    title: str
    target_node_id: UUID | None
    height: Literal["center", "top"]


@dataclass(frozen=True)
class RoutePose:
    position: Vec3
    heading_degrees: float


def build_workflow_suite(
    graph: SceneGraph,
    scenario: Scenario,
    *,
    interactions: list[Interaction] | tuple[Interaction, ...] = (),
) -> list[Workflow]:
    """Expand known stops and task targets into broad scan-space coverage."""
    workflows = [
        Workflow(
            id="journey:full",
            title=scenario.name,
            scenario=scenario,
            interactions=tuple(interactions),
        )
    ]
    for origin_index, origin in enumerate(scenario.stops):
        for destination_index, destination in enumerate(scenario.stops):
            if origin_index == destination_index:
                continue
            workflows.append(
                Workflow(
                    id=f"route:{origin_index}:{destination_index}",
                    title=f"{origin.name} to {destination.name}",
                    scenario=Scenario(
                        name=f"{origin.name} to {destination.name}",
                        stops=[origin, destination],
                    ),
                )
            )
    for interaction in interactions:
        workflows.extend(_interaction_workflows(graph, scenario, interaction))
    return workflows


def build_entrance_object_workflows(
    graph: SceneGraph, scenario: Scenario
) -> list[Workflow]:
    """Route every inferred or Astra-labelled entrance to every scanned object."""
    by_id = {node.id: node for node in graph.nodes}
    entrance_terms = {"door", "entrance", "entry", "exit", "opening"}
    scenario_entrances = [
        stop
        for stop in scenario.stops
        if (
            stop.anchor_node_id in by_id
            and by_id[stop.anchor_node_id].kind in {"door", "opening"}
        )
        or entrance_terms & set(stop.name.casefold().replace("-", " ").split())
    ]
    entrances: list[Stop] = []
    anchored_entrances: set[UUID] = set()
    unanchored_positions: set[tuple[float, float, float]] = set()

    for stop in scenario_entrances:
        if stop.anchor_node_id is not None:
            if stop.anchor_node_id in anchored_entrances:
                continue
            anchored_entrances.add(stop.anchor_node_id)
        else:
            position = stop.position.as_tuple()
            if position in unanchored_positions:
                continue
            unanchored_positions.add(position)
        entrances.append(stop)

    for node in graph.nodes:
        if node.kind not in {"door", "opening"} or node.id in anchored_entrances:
            continue
        anchored_entrances.add(node.id)
        entrances.append(_stop_at_node(node))

    existing_stops = {
        stop.anchor_node_id: stop
        for stop in scenario.stops
        if stop.anchor_node_id is not None
    }
    workflows = []
    for entrance_index, entrance in enumerate(entrances):
        for target in (node for node in graph.contents()):
            destination = existing_stops.get(target.id) or _stop_at_node(target)
            workflows.append(
                Workflow(
                    id=f"entrance-object:{entrance_index}:{target.id}",
                    title=f"{entrance.name} to {target.label}",
                    scenario=Scenario(
                        name=f"{entrance.name} to {target.label}",
                        stops=[entrance, destination],
                    ),
                )
            )
    return workflows


def _stop_at_node(node: SceneNode) -> Stop:
    position = node.transform.position
    return Stop(
        name=node.label,
        position=Vec3(x=position.x, y=position.y, z=0.0),
        anchor_node_id=node.id,
    )


def _interaction_workflows(
    graph: SceneGraph,
    scenario: Scenario,
    interaction: Interaction,
) -> list[Workflow]:
    if interaction.target_node_id is None:
        return [
            Workflow(
                id=f"task:{interaction.id}:needs-measurement",
                title=interaction.title,
                scenario=scenario,
                interactions=(interaction,),
            )
        ]
    try:
        target = graph.by_id(interaction.target_node_id)
    except KeyError:
        return [
            Workflow(
                id=f"task:{interaction.id}:needs-measurement",
                title=interaction.title,
                scenario=scenario,
                interactions=(interaction,),
            )
        ]
    anchor_ids = {target.id, target.parent_id} - {None}
    existing_destination = next(
        (stop for stop in scenario.stops if stop.anchor_node_id in anchor_ids),
        None,
    )
    if existing_destination is None:
        position = target.transform.position
        destination = Stop(
            name=interaction.title,
            position=Vec3(x=position.x, y=position.y, z=0.0),
            anchor_node_id=target.id,
        )
    else:
        destination = existing_destination.model_copy(update={"name": interaction.title})
    return [
        Workflow(
            id=f"task:{interaction.id}:{origin_index}",
            title=f"{origin.name} to {interaction.title}",
            scenario=Scenario(
                name=f"{origin.name} to {interaction.title}",
                stops=[origin, destination],
            ),
            interactions=(interaction,),
        )
        for origin_index, origin in enumerate(scenario.stops)
        if origin.anchor_node_id not in anchor_ids
    ]


@dataclass(frozen=True)
class WorkflowLeg:
    origin: str
    destination: str
    reachable: bool
    measured_width_inches: float
    required_width_inches: float
    meets_clearance: bool
    floor_plan_collision: bool
    mesh_collision: bool
    collision_free: bool
    blocking_node_ids: tuple[UUID, ...]
    route: tuple[RoutePose, ...]

    @property
    def passed(self) -> bool:
        return self.collision_free and self.meets_clearance


@dataclass(frozen=True)
class InteractionEvaluation:
    interaction: Interaction
    needs_measurement: bool
    approach_collision_free: bool | None
    measured_height_inches: float | None
    maximum_reach_inches: float | None
    height_reachable: bool | None
    reachable: bool | None


@dataclass(frozen=True)
class WorkflowEvaluation:
    workflow: Workflow
    profile: FunctionalProfile
    legs: tuple[WorkflowLeg, ...]
    interactions: tuple[InteractionEvaluation, ...]

    @property
    def passed(self) -> bool:
        return (
            all(leg.passed for leg in self.legs)
            and all(item.reachable is True for item in self.interactions)
        )


def evaluate_workflow(
    graph: SceneGraph,
    workflow: Workflow,
    profile: FunctionalProfile,
    measure: MeasurementProvider,
    *,
    lidar_mesh: LidarMesh | None = None,
    collision_index: MeshCollisionIndex | None = None,
) -> WorkflowEvaluation:
    """Measure every leg and keep the ordered path an avatar should face along."""
    if lidar_mesh is not None and collision_index is not None:
        raise ValueError("pass lidar_mesh or collision_index, not both")
    mesh_index = collision_index or (
        MeshCollisionIndex(lidar_mesh) if lidar_mesh is not None else None
    )
    legs: list[WorkflowLeg] = []
    stops = workflow.scenario.stops
    for index in range(len(stops) - 1):
        measured = measure.route_clear_width(graph, workflow.scenario, index)
        floor_plan_collision = (
            not measured.reachable or measured.inches < profile.body_width_inches
        )
        mesh_collision = bool(
            mesh_index
            and mesh_index.collides(measured.path, profile.body_width_inches / 2)
        )
        meets_clearance = (
            measured.reachable and measured.inches >= profile.required_width_inches
        )
        legs.append(
            WorkflowLeg(
                origin=stops[index].name,
                destination=stops[index + 1].name,
                reachable=measured.reachable,
                measured_width_inches=measured.inches,
                required_width_inches=profile.required_width_inches,
                meets_clearance=meets_clearance,
                floor_plan_collision=floor_plan_collision,
                mesh_collision=mesh_collision,
                collision_free=not floor_plan_collision and not mesh_collision,
                blocking_node_ids=tuple(measured.blocking_node_ids),
                route=_route_poses(measured.path),
            )
        )
    interactions = tuple(
        _evaluate_interaction(graph, workflow, interaction, profile, legs)
        for interaction in workflow.interactions
    )
    return WorkflowEvaluation(
        workflow=workflow,
        profile=profile,
        legs=tuple(legs),
        interactions=interactions,
    )


def workflow_candidate_rejection(
    before: SceneGraph,
    candidate: SceneGraph,
    *,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    measure: MeasurementProvider,
    lidar_mesh: LidarMesh | None = None,
    collision_index: MeshCollisionIndex | None = None,
) -> str | None:
    """Reject a layout that breaks access a prior layout already provided."""
    if lidar_mesh is not None and collision_index is not None:
        raise ValueError("pass lidar_mesh or collision_index, not both")
    mesh_index = collision_index or (
        MeshCollisionIndex(lidar_mesh) if lidar_mesh is not None else None
    )
    for workflow in workflows:
        for profile in profiles:
            prior = evaluate_workflow(
                before, workflow, profile, measure, collision_index=mesh_index
            )
            after = evaluate_workflow(
                candidate, workflow, profile, measure, collision_index=mesh_index
            )
            lost_leg = any(old.passed and not new.passed for old, new in zip(prior.legs, after.legs))
            lost_interaction = any(
                (old.reachable is True and new.reachable is not True)
                or (not old.needs_measurement and new.needs_measurement)
                for old, new in zip(prior.interactions, after.interactions)
            )
            if lost_leg or lost_interaction:
                return f"workflow_regression:{workflow.id}:{profile.id}"
    return None


def _evaluate_interaction(
    graph: SceneGraph,
    workflow: Workflow,
    interaction: Interaction,
    profile: FunctionalProfile,
    legs: list[WorkflowLeg],
) -> InteractionEvaluation:
    if interaction.target_node_id is None:
        return _unknown_interaction(interaction, profile)
    try:
        target = graph.by_id(interaction.target_node_id)
    except KeyError:
        return _unknown_interaction(interaction, profile)

    anchor_ids = {target.id, target.parent_id} - {None}
    stop_index = next(
        (
            index
            for index, stop in enumerate(workflow.scenario.stops)
            if stop.anchor_node_id in anchor_ids
        ),
        None,
    )
    if stop_index is None:
        return _unknown_interaction(interaction, profile)
    approach_clear = stop_index == 0 or legs[stop_index - 1].collision_free
    center = target.transform.position.z
    height_meters = center + target.dimensions.z / 2 if interaction.height == "top" else center
    measured_height = to_inches(height_meters)
    height_reachable = (
        None
        if profile.maximum_reach_inches is None
        else measured_height <= profile.maximum_reach_inches
    )
    reachable = approach_clear and height_reachable is not False
    return InteractionEvaluation(
        interaction=interaction,
        needs_measurement=False,
        approach_collision_free=approach_clear,
        measured_height_inches=measured_height,
        maximum_reach_inches=profile.maximum_reach_inches,
        height_reachable=height_reachable,
        reachable=reachable,
    )


def _unknown_interaction(
    interaction: Interaction, profile: FunctionalProfile
) -> InteractionEvaluation:
    return InteractionEvaluation(
        interaction=interaction,
        needs_measurement=True,
        approach_collision_free=None,
        measured_height_inches=None,
        maximum_reach_inches=profile.maximum_reach_inches,
        height_reachable=None,
        reachable=None,
    )


@dataclass(frozen=True)
class WorkflowRun:
    index: int
    evaluation: WorkflowEvaluation
    steps: tuple[LoopStep, ...]
    suite_evaluations: tuple[WorkflowEvaluation, ...] = ()

    @property
    def completed(self) -> bool:
        return bool(self.steps and self.steps[-1].action == "DONE")

    @property
    def rejected(self) -> str | None:
        return self.steps[-1].rejected if self.steps else "no_steps"

    @property
    def final_graph(self) -> SceneGraph:
        return self.steps[-1].graph


@dataclass(frozen=True)
class WorkflowFeedback:
    """Aggregated evidence a layout generator or report can consume."""

    workflow_id: str
    workflow_title: str
    profile_id: str
    profile_title: str
    trials: int
    passed_trials: int
    clearance_failure_trials: int
    floor_plan_collision_trials: int
    mesh_collision_trials: int
    unreachable_interaction_trials: int
    needs_measurement_trials: int
    blocking_node_ids: tuple[UUID, ...]

    @property
    def pass_rate(self) -> float:
        return self.passed_trials / self.trials if self.trials else 0.0

    @property
    def requires_design_change(self) -> bool:
        return any(
            (
                self.clearance_failure_trials,
                self.floor_plan_collision_trials,
                self.mesh_collision_trials,
                self.unreachable_interaction_trials,
            )
        )


@dataclass(frozen=True)
class WorkflowBatchResult:
    runs: tuple[WorkflowRun, ...]
    max_workers: int

    @property
    def total_runs(self) -> int:
        return len(self.runs)

    @property
    def completed_runs(self) -> int:
        return sum(run.completed for run in self.runs)

    @property
    def rejected_runs(self) -> int:
        return sum(run.rejected is not None for run in self.runs)

    @property
    def rejection_rate(self) -> float:
        return self.rejected_runs / self.total_runs if self.total_runs else 0.0

    @property
    def violating_trials(self) -> int:
        """Runs incomplete, rejected, or failing final ADA/workflow evaluation."""
        return sum(
            not run.completed
            or run.rejected is not None
            or bool(run.steps[-1].assessment.problems)
            or any(
                not evaluation.passed
                for evaluation in (
                    run.suite_evaluations or (run.evaluation,)
                )
            )
            for run in self.runs
        )

    @property
    def action_counts(self) -> dict[str, int]:
        return dict(
            Counter(
                step.action
                for run in self.runs
                for step in run.steps
                if step.action is not None
            )
        )

    @property
    def rejection_counts(self) -> dict[str, int]:
        return dict(Counter(run.rejected for run in self.runs if run.rejected))

    @property
    def feedback(self) -> tuple[WorkflowFeedback, ...]:
        grouped: dict[tuple[str, str], list[WorkflowEvaluation]] = defaultdict(list)
        for run in self.runs:
            evaluations = run.suite_evaluations or (run.evaluation,)
            for evaluation in evaluations:
                key = (evaluation.workflow.id, evaluation.profile.id)
                grouped[key].append(evaluation)
        return tuple(_workflow_feedback(items) for items in grouped.values())

    @property
    def best_run(self) -> WorkflowRun | None:
        eligible = [run for run in self.runs if run.rejected is None]
        return max(eligible, key=_run_score) if eligible else None

    @property
    def recommended_graph(self) -> SceneGraph | None:
        best = self.best_run
        return best.final_graph if best is not None else None


class TypeSafeWorkflowConfigurationError(RuntimeError):
    """A live batch was explicitly requested but cannot make valid claims."""


def _workflow_feedback(evaluations: list[WorkflowEvaluation]) -> WorkflowFeedback:
    first = evaluations[0]
    blockers = {
        node_id
        for evaluation in evaluations
        for leg in evaluation.legs
        for node_id in leg.blocking_node_ids
        if not leg.passed
    }
    return WorkflowFeedback(
        workflow_id=first.workflow.id,
        workflow_title=first.workflow.title,
        profile_id=first.profile.id,
        profile_title=first.profile.title,
        trials=len(evaluations),
        passed_trials=sum(evaluation.passed for evaluation in evaluations),
        clearance_failure_trials=sum(
            any(not leg.meets_clearance for leg in evaluation.legs)
            for evaluation in evaluations
        ),
        floor_plan_collision_trials=sum(
            any(leg.floor_plan_collision for leg in evaluation.legs)
            for evaluation in evaluations
        ),
        mesh_collision_trials=sum(
            any(leg.mesh_collision for leg in evaluation.legs)
            for evaluation in evaluations
        ),
        unreachable_interaction_trials=sum(
            any(item.reachable is False for item in evaluation.interactions)
            for evaluation in evaluations
        ),
        needs_measurement_trials=sum(
            any(item.needs_measurement for item in evaluation.interactions)
            for evaluation in evaluations
        ),
        blocking_node_ids=tuple(sorted(blockers, key=str)),
    )


def _run_score(run: WorkflowRun) -> tuple[int, int, int, int, int]:
    evaluations = run.suite_evaluations or (run.evaluation,)
    passed = sum(evaluation.passed for evaluation in evaluations)
    missing = sum(
        item.needs_measurement
        for evaluation in evaluations
        for item in evaluation.interactions
    )
    failed_parts = sum(
        not leg.passed
        for evaluation in evaluations
        for leg in evaluation.legs
    ) + sum(
        item.reachable is False
        for evaluation in evaluations
        for item in evaluation.interactions
    )
    return (
        passed,
        -missing,
        -failed_parts,
        -run.final_graph.revision,
        -run.index,
    )


def _check_batch_inputs(workflows: list, profiles: list, samples: int, max_workers: int) -> None:
    if not workflows:
        raise ValueError("at least one workflow is required")
    if not profiles:
        raise ValueError("at least one functional profile is required")
    if not 1 <= samples <= 10000:
        raise ValueError("samples must be between 1 and 10000")
    if not 1 <= max_workers <= MAX_WORKFLOW_WORKERS:
        raise ValueError(f"max_workers must be between 1 and {MAX_WORKFLOW_WORKERS}")


def _collect_runs(
    run: Callable[[int], "WorkflowRun"],
    samples: int,
    max_workers: int,
    on_progress: Callable[[int], None] | None,
) -> tuple["WorkflowRun", ...]:
    """Run every sample with bounded parallelism, returned in index order.

    Progress is reported as each trial lands, but the results are put back in
    the order they were submitted so a batch does not depend on thread timing.
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run, index) for index in range(samples)]
        collected: dict[int, WorkflowRun] = {}
        for completed, future in enumerate(as_completed(futures), start=1):
            item = future.result()
            collected[item.index] = item
            if on_progress is not None:
                on_progress(completed)
    return tuple(collected[index] for index in range(samples))


def run_workflow_batch(
    graph: SceneGraph,
    *,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    samples: int,
    max_workers: int,
    measure_factory,
    router_factory,
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    lidar_mesh: LidarMesh | None = None,
    max_tier: Tier = 3,
    on_progress: Callable[[int], None] | None = None,
) -> WorkflowBatchResult:
    """Run many complete workflow loops with bounded parallelism.

    ``samples`` is the total number of TypeSafe trials. Workflow/profile pairs
    are selected round-robin, which makes repeated trials useful for measuring
    router consistency without pretending duplicate geometry is new evidence.
    Each worker keeps its own measurement adapter so mutable grid caches are
    never shared across threads; the expensive raw-mesh index is immutable and
    built once for the entire batch.
    """
    _check_batch_inputs(workflows, profiles, samples, max_workers)

    selected_rules = rules or load_pack()
    selected_ledger = ledger if ledger is not None else load_ledger()
    cases = [(workflow, profile) for workflow in workflows for profile in profiles]
    mesh_index = MeshCollisionIndex(lidar_mesh) if lidar_mesh is not None else None
    local = threading.local()
    suite_cache: dict[str, tuple[WorkflowEvaluation, ...]] = {}
    suite_cache_lock = threading.Lock()
    local_steps_cache: dict[str, tuple[LoopStep, ...]] = {}
    local_steps_lock = threading.Lock()
    initial_measure = measure_factory()
    initial_passes = {
        workflow.id: assess(
            graph,
            workflow.scenario,
            initial_measure,
            rules=selected_rules,
            ledger=selected_ledger,
            max_tier=max_tier,
        )
        for workflow in workflows
    }
    fix_cache: dict[tuple[str, tuple[str, ...]], StepResult] = {}
    fix_cache_lock = threading.Lock()

    def cached_fix(loop: Loop, current: Pass, decision) -> StepResult:
        key = (
            graph_hash(loop.graph),
            tuple(sorted(str(item) for item in decision.target_finding_ids)),
        )
        with fix_cache_lock:
            if key not in fix_cache:
                fix_cache[key] = _do_fix(loop, current, decision)
            return fix_cache[key]

    def evaluate_suite(
        candidate: SceneGraph, measure: MeasurementProvider
    ) -> tuple[WorkflowEvaluation, ...]:
        key = graph_hash(candidate)
        with suite_cache_lock:
            if key not in suite_cache:
                suite_cache[key] = tuple(
                    evaluate_workflow(
                        candidate,
                        case_workflow,
                        case_profile,
                        measure,
                        collision_index=mesh_index,
                    )
                    for case_workflow, case_profile in cases
                )
            return suite_cache[key]

    def execute(workflow: Workflow, measure, router) -> tuple[LoopStep, ...]:
        return tuple(run_loop(
            graph, workflow.scenario, measure, router, rules=selected_rules,
            ledger=selected_ledger, max_tier=max_tier,
            candidate_rejection=lambda before, candidate: workflow_candidate_rejection(
                before, candidate, workflows=workflows, profiles=profiles,
                measure=measure, collision_index=mesh_index,
            ),
            initial_pass=initial_passes[workflow.id],
            fix_handler=cached_fix,
        ))

    def run(index: int) -> WorkflowRun:
        with suspend_tracing():
            workflow, profile = cases[index % len(cases)]
            if not hasattr(local, "measure"):
                local.measure = measure_factory()
            router = router_factory()
            if isinstance(router, LocalPolicyRouter):
                # This policy is deterministic and independent of avatar profile.
                # Live TypeSafe trials always call the provider for fresh judgments.
                with local_steps_lock:
                    key = workflow.scenario.model_dump_json()
                    if key not in local_steps_cache:
                        local_steps_cache[key] = execute(workflow, local.measure, router)
                    steps = local_steps_cache[key]
            else:
                steps = execute(workflow, local.measure, router)
            final_graph = steps[-1].graph if steps else graph
            suite_evaluations = evaluate_suite(final_graph, local.measure)
            evaluation = next(
                item
                for item in suite_evaluations
                if item.workflow.id == workflow.id and item.profile.id == profile.id
            )
            return WorkflowRun(
                index=index,
                evaluation=evaluation,
                steps=steps,
                suite_evaluations=suite_evaluations,
            )

    runs = _collect_runs(run, samples, max_workers, on_progress)
    return WorkflowBatchResult(runs=runs, max_workers=max_workers)


def run_typesafe_workflow_batch(
    graph: SceneGraph,
    *,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    samples: int,
    max_workers: int,
    measure_factory: Callable[[], MeasurementProvider],
    rules: AgentRulePack | None = None,
    ledger: VerificationLedger | None = None,
    lidar_mesh: LidarMesh | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    path: str | None = None,
    model: str | None = None,
    max_tier: Tier = 3,
) -> WorkflowBatchResult:
    """Run a live TypeSafe batch or fail before starting any trial."""
    selected_rules = rules or load_pack()
    selected_ledger = ledger if ledger is not None else load_ledger()
    def router_factory() -> TypeSafeRouter:
        if path is None:
            return TypeSafeRouter(
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        return TypeSafeRouter(
            api_key=api_key,
            base_url=base_url,
            path=path,
            model=model,
        )

    probe = router_factory()
    missing = []
    if not probe.api_key:
        missing.append("TYPESAFE_API_KEY")
    if not probe.base_url:
        missing.append("TYPESAFE_BASE_URL")
    if missing:
        raise TypeSafeWorkflowConfigurationError(
            f"TypeSafe workflow batch requires {', '.join(missing)}"
        )
    if not selected_rules.enabled(selected_ledger, max_tier=max_tier):
        raise TypeSafeWorkflowConfigurationError(
            "TypeSafe workflow batch requires at least one human-verified ADA rule"
        )
    return run_workflow_batch(
        graph,
        workflows=workflows,
        profiles=profiles,
        samples=samples,
        max_workers=max_workers,
        measure_factory=measure_factory,
        router_factory=router_factory,
        rules=selected_rules,
        ledger=selected_ledger,
        lidar_mesh=lidar_mesh,
        max_tier=max_tier,
    )


def _route_poses(points: list[Vec3]) -> tuple[RoutePose, ...]:
    """Face each avatar toward the next point, never backward along the route."""
    import math

    if not points:
        return ()
    poses: list[RoutePose] = []
    last_heading = 0.0
    for index, point in enumerate(points):
        if index + 1 < len(points):
            following = points[index + 1]
            dx, dy = following.x - point.x, following.y - point.y
            if dx or dy:
                last_heading = math.degrees(math.atan2(dy, dx))
        poses.append(RoutePose(position=point, heading_degrees=last_heading))
    return tuple(poses)
