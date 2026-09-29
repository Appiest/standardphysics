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
from typing import Callable
from uuid import UUID

from standardphysics_contracts import (
    LidarMesh,
    MeasurementProvider,
    SceneGraph,
    graph_hash,
)
from standardphysics_contracts.precedents import PrecedentDirective
from standardphysics_contracts.rules import Tier

from .assess import Pass, assess
from .fix import combine_rejections
from .loop import Loop, LoopStep, StepResult, _do_fix, run_loop
from .mesh_collision import MeshCollisionIndex
from .precedents import precedent_rejection_for
from .router import LocalPolicyRouter, TypeSafeRouter
from .rules import AgentRulePack, VerificationLedger, load_ledger, load_pack
from .tracing import suspend_tracing
from .workflow_definitions import FunctionalProfile, Workflow
from .workflow_evaluation import WorkflowEvaluation, evaluate_workflow, workflow_candidate_rejection

MAX_WORKFLOW_WORKERS = 1_000


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
    directives: tuple[PrecedentDirective, ...] = (),
) -> WorkflowBatchResult:
    """Run many complete workflow loops with bounded parallelism.

    ``directives`` are the space type's ADA layout directives; a fix that
    breaks one is refused alongside any workflow regression.

    ``samples`` is the total number of TypeSafe trials. Workflow/profile pairs
    are selected round-robin, which makes repeated trials useful for measuring
    router consistency without pretending duplicate geometry is new evidence.
    Each worker keeps its own measurement adapter so mutable grid caches are
    never shared across threads; the expensive raw-mesh index is immutable and
    built once for the entire batch.
    """
    _check_batch_inputs(workflows, profiles, samples, max_workers)
    directive_rejection = precedent_rejection_for(list(directives)) if directives else None

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
            candidate_rejection=combine_rejections(
                lambda before, candidate: workflow_candidate_rejection(
                    before, candidate, workflows=workflows, profiles=profiles,
                    measure=measure, collision_index=mesh_index,
                ),
                directive_rejection,
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

