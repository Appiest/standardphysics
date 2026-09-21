"""Persisted, bounded room screening on the existing background worker."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import UUID

from standardphysics_agents import (
    TypeSafeCallBudget,
    analyze_environment_physics,
    assess,
    build_entrance_object_workflows,
    load_pack,
    run_adaptive_redesign,
)
from standardphysics_agents.adaptive_redesign import route_trial_evidence
from standardphysics_agents.evaluation.scan_campaign import run_campaign
from standardphysics_agents.evaluation.scan_tasks import choose_task, propose_tasks
from standardphysics_agents.mesh_collision import MeshCollisionIndex
from standardphysics_agents.router import LocalPolicyRouter, TypeSafeRouter
from standardphysics_agents.simulation_report import simulation_result
from standardphysics_agents.workflows import (
    DEFAULT_PROFILES,
    Interaction,
    WorkflowBatchResult,
    build_workflow_suite,
    run_workflow_batch,
)
from standardphysics_contracts import (
    AdaptiveRoundResult,
    LidarMesh,
    Scenario,
    SceneGraph,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    bounds_the_room,
    graph_hash,
)
from standardphysics_pipeline import PipelineMeasurements

from . import repository as repo
from .errors import ApiProblem
from .scenario import suggest_scenario

SIMULATE = "simulate"


NO_RULES = (
    "TypeSafe has no rules to screen against. Verify the rule pack, or start the API with"
    " SP_PREVIEW_UNVERIFIED_RULES=1 for an unverified preview"
)


def _live_ready(stages, request: SimulationRequest) -> None:
    """Route trials and Astra redesigns may run on preview rules: the result says it is a preview,
    and an Astra layout is only ever a recommendation.

    The exhaustive campaign is still held to rules a person has verified.
    """
    exhaustive = bool(request.exhaustive_evaluations)
    if request.router != "typesafe" and not request.refine_with_astra and not exhaustive:
        return
    if not TypeSafeRouter().configured:
        raise ApiProblem(409, "TypeSafe is not configured; set TYPESAFE_API_KEY on the server")
    ledger = stages.ledger_factory()
    enabled = load_pack().enabled(ledger, max_tier=3)
    if not enabled:
        raise ApiProblem(409, NO_RULES)
    if exhaustive and any("unverified preview" in ledger.entry_for(rule).verified_by for rule in enabled):
        raise ApiProblem(409, "Exhaustive campaigns need human-verified rules; preview rules cannot certify a room")


def queue_simulation(database, stages, worker, scan_id: UUID, body: SimulationRequest) -> SimulationStatus:
    _live_ready(stages, body)
    if (body.refine_with_astra or body.exhaustive_evaluations) and not os.environ.get("OPENROUTER_API_KEY"):
        raise ApiProblem(409, "Astra needs OPENROUTER_API_KEY on the server")
    with database.transaction() as connection:
        if not repo.scan_exists(connection, scan_id):
            raise ApiProblem(404, "no scan")
        latest = repo.get_revision(connection, scan_id)
        scenario = repo.get_scenario(connection, scan_id)
        if latest is None:
            raise ApiProblem(409, "the shop is still being measured")
        graph = repo.graph_of(latest)
        if scenario is None:
            scenario = suggest_scenario(graph)
        if latest["revision"] != body.base_revision:
            raise ApiProblem(409, "a newer layout was saved since this one started")
        active = connection.execute(
            "SELECT 1 FROM jobs WHERE scan_id = ? AND kind = ? AND state IN ('queued', 'running')",
            (str(scan_id), SIMULATE),
        ).fetchone()
        if active:
            raise ApiProblem(409, "a simulation for this room is already queued or running")
        mesh = repo.artifact_of_kind(connection, scan_id, "lidar_mesh")
        connection.execute(
            "INSERT INTO simulations (scan_id, revision, request_json, graph_json, scenario_json, mesh_artifact_id)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(scan_id, revision) DO UPDATE SET"
            " request_json=excluded.request_json, graph_json=excluded.graph_json, scenario_json=excluded.scenario_json,"
            " mesh_artifact_id=excluded.mesh_artifact_id, completed=0, cycle=0,"
            " candidate_graph_json=NULL, result_json=NULL",
            (str(scan_id), body.base_revision, body.model_dump_json(), latest["graph_json"],
             scenario.model_dump_json(), mesh.id if mesh else None),
        )
        repo.queue_job_again(connection, scan_id, SIMULATE, body.base_revision)
    worker.wake()
    return simulation_status(database, scan_id, body.base_revision)


def simulation_status(database, scan_id: UUID, revision: int) -> SimulationStatus:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT s.*, j.state, j.error FROM simulations s JOIN jobs j"
            " ON s.scan_id=j.scan_id AND s.revision=j.revision AND j.kind=?"
            " WHERE s.scan_id=? AND s.revision=?",
            (SIMULATE, str(scan_id), revision),
        ).fetchone()
    if row is None:
        raise ApiProblem(404, "no simulation for this revision")
    request = SimulationRequest.model_validate_json(row["request_json"])
    return SimulationStatus(
        base_revision=revision, state=row["state"], router=request.router,
        samples=request.samples, completed=row["completed"], cycle=row["cycle"],
        candidate_graph=(SceneGraph.model_validate_json(row["candidate_graph_json"])
                         if row["candidate_graph_json"] else None),
        typesafe_call_limit=request.typesafe_call_limit,
        exhaustive_evaluations=request.exhaustive_evaluations,
        error=row["error"],
        result=SimulationResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
    )


def _interactions(graph: SceneGraph, scenario: Scenario) -> list[Interaction]:
    nodes = {node.id: node for node in graph.contents()}
    targets = dict.fromkeys(stop.anchor_node_id for stop in scenario.stops if stop.anchor_node_id in nodes)
    return [Interaction(id=str(key), title=f"Reach {nodes[key].label}", target_node_id=key, height="top")
            for key in targets]


def _progress_stride(request: SimulationRequest) -> int:
    """Expose every live provider result and bounded one-percent local updates."""
    if request.router == "typesafe":
        return 1
    return max(1, request.samples // 100)


@dataclass(frozen=True)
class AccessibilityLoopOutcome:
    """The last fully tested candidate and the evidence needed by the API result."""

    batch: WorkflowBatchResult
    candidate: SceneGraph
    cycles: int
    violating_trials: int
    ada_rule_violations: int
    converged: bool
    adaptive_rounds: tuple[AdaptiveRoundResult, ...]
    astra_calls: int
    stop_reason: str | None = None


def _ada_rule_violation_count(
    graph: SceneGraph, scenario: Scenario, measure, rules, ledger
) -> int:
    return len(
        assess(
            graph,
            scenario,
            measure,
            rules=rules,
            ledger=ledger,
            max_tier=3,
        ).problems
    )


def _run_accessibility_loop(
    measured_graph: SceneGraph,
    scenario: Scenario,
    *,
    workflows,
    profiles,
    request: SimulationRequest,
    measure_factory,
    router_factory,
    rules,
    ledger,
    mesh: LidarMesh | None,
    budget: TypeSafeCallBudget,
    on_progress: Callable[[int], None] | None,
    on_cycle_start: Callable[[int], None] | None = None,
    on_candidate: Callable[[int, SceneGraph], None] | None = None,
) -> AccessibilityLoopOutcome:
    """Run complete trial batches, allowing exactly one Astra repair between them."""
    current = measured_graph
    collision_index = MeshCollisionIndex(mesh) if mesh else None
    adaptive_history: list[AdaptiveRoundResult] = []
    astra_calls = 0
    final_batch = None
    final_candidate = current
    violating_trials = request.samples
    ada_rule_violations = 0
    stop_reason = None

    for cycle in range(1, request.astra_rounds + 2):
        if on_cycle_start is not None:
            on_cycle_start(cycle)
        if on_candidate is not None:
            on_candidate(cycle, current)
        final_batch = run_workflow_batch(
            current,
            workflows=workflows,
            profiles=profiles,
            samples=request.samples,
            max_workers=request.max_workers,
            measure_factory=measure_factory,
            router_factory=router_factory,
            rules=rules,
            ledger=ledger,
            lidar_mesh=mesh,
            on_progress=on_progress,
        )
        final_candidate = final_batch.recommended_graph or current
        if on_candidate is not None:
            on_candidate(cycle, final_candidate)
        violating_trials = final_batch.violating_trials
        ada_rule_violations = _ada_rule_violation_count(
            final_candidate, scenario, measure_factory(), rules, ledger
        )
        if violating_trials == 0 and ada_rule_violations == 0:
            return AccessibilityLoopOutcome(
                batch=final_batch,
                candidate=final_candidate,
                cycles=cycle,
                violating_trials=0,
                ada_rule_violations=0,
                converged=True,
                adaptive_rounds=tuple(adaptive_history),
                astra_calls=astra_calls,
            )
        if cycle > request.astra_rounds:
            stop_reason = "The loop reached its bounded repair limit before all violations cleared."
            break

        trial_result = simulation_result(final_batch, rules, ledger, mesh)
        redesign = run_adaptive_redesign(
            measured_graph,
            starting_graph=final_candidate,
            workflows=workflows,
            profiles=profiles,
            measure=measure_factory(),
            rules=rules,
            ledger=ledger,
            rounds=1,
            budget=budget,
            collision_index=collision_index,
            scenario=scenario,
            route_trials=route_trial_evidence(
                measured_graph, trial_result, request.router
            ),
        )
        astra_calls += redesign.astra_calls
        for item in redesign.rounds:
            adaptive_history.append(
                item.model_copy(update={"round": len(adaptive_history) + 1})
            )

        next_candidate = redesign.graph
        if next_candidate is None and graph_hash(final_candidate) != graph_hash(current):
            # TypeSafe may have produced the best candidate during the batch.
            # Astra has still reviewed its evidence; verify that candidate as a
            # fixed layout before making any convergence claim.
            next_candidate = final_candidate
        if next_candidate is None or graph_hash(next_candidate) == graph_hash(current):
            reasons = redesign.rounds[-1].reasons if redesign.rounds else ()
            detail = ", ".join(reasons) or "no new candidate"
            stop_reason = f"Astra could not produce a new layout: {detail}."
            break
        current = next_candidate

    assert final_batch is not None
    return AccessibilityLoopOutcome(
        batch=final_batch,
        candidate=final_candidate,
        cycles=cycle,
        violating_trials=violating_trials,
        ada_rule_violations=ada_rule_violations,
        converged=False,
        adaptive_rounds=tuple(adaptive_history),
        astra_calls=astra_calls,
        stop_reason=stop_reason,
    )


def run_simulation(database, store, stages, scan_id: UUID, revision: int) -> None:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM simulations WHERE scan_id=? AND revision=?",
            (str(scan_id), revision),
        ).fetchone()
    request = SimulationRequest.model_validate_json(row["request_json"])
    _live_ready(stages, request)
    graph = SceneGraph.model_validate_json(row["graph_json"])
    scenario = Scenario.model_validate_json(row["scenario_json"])
    mesh = (LidarMesh.model_validate_json(store.artifact_path(scan_id, row["mesh_artifact_id"]).read_bytes())
            if row["mesh_artifact_id"] else None)
    ledger = stages.ledger_factory()
    rules = load_pack()

    progress_stride = _progress_stride(request)

    def progress(completed: int) -> None:
        if completed % progress_stride == 0 or completed == request.samples:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE simulations SET completed=? WHERE scan_id=? AND revision=?",
                    (completed, str(scan_id), revision),
                )

    workflows = [
        *build_workflow_suite(graph, scenario, interactions=_interactions(graph, scenario)),
        *build_entrance_object_workflows(graph, scenario),
    ]
    campaign_reserve = 9 if request.exhaustive_evaluations else 0
    workflow_budget = (
        TypeSafeCallBudget(request.typesafe_call_limit - campaign_reserve)
        if request.router == "typesafe" or request.refine_with_astra
        else None
    )
    router_factory = (
        (lambda: TypeSafeRouter(budget=workflow_budget))
        if request.router == "typesafe"
        else LocalPolicyRouter
    )

    def publish_candidate(cycle: int, candidate: SceneGraph) -> None:
        with database.transaction() as connection:
            connection.execute(
                "UPDATE simulations SET completed=0, cycle=?, candidate_graph_json=?"
                " WHERE scan_id=? AND revision=?",
                (cycle, candidate.model_dump_json(), str(scan_id), revision),
            )

    if request.refine_with_astra:
        assert workflow_budget is not None
        loop_outcome = _run_accessibility_loop(
            graph,
            scenario,
            workflows=workflows,
            profiles=list(DEFAULT_PROFILES),
            request=request,
            measure_factory=PipelineMeasurements,
            router_factory=router_factory,
            rules=rules,
            ledger=ledger,
            mesh=mesh,
            budget=workflow_budget,
            on_progress=progress,
            on_candidate=publish_candidate,
        )
        batch = loop_outcome.batch
    else:
        batch = run_workflow_batch(
            graph,
            workflows=workflows,
            profiles=list(DEFAULT_PROFILES),
            samples=request.samples,
            max_workers=request.max_workers,
            measure_factory=PipelineMeasurements,
            router_factory=router_factory,
            rules=rules,
            ledger=ledger,
            lidar_mesh=mesh,
            on_progress=progress,
        )
        candidate = batch.recommended_graph or graph
        loop_outcome = AccessibilityLoopOutcome(
            batch=batch,
            candidate=candidate,
            cycles=1,
            violating_trials=batch.violating_trials,
            ada_rule_violations=_ada_rule_violation_count(
                candidate, scenario, PipelineMeasurements(), rules, ledger
            ),
            converged=False,
            adaptive_rounds=(),
            astra_calls=0,
        )

    result = simulation_result(batch, rules, ledger, mesh)
    typesafe_calls = workflow_budget.used if workflow_budget is not None else 0
    astra_calls = loop_outcome.astra_calls
    limitations = list(result.limitations)
    if loop_outcome.stop_reason:
        limitations.append(loop_outcome.stop_reason)
    physics = analyze_environment_physics(
        graph, scenario, PipelineMeasurements(), mesh=mesh
    )

    exhaustive_count = 0
    exhaustive_outcomes: dict[str, int] = {}
    if request.exhaustive_evaluations:
        campaign_budget = TypeSafeCallBudget(campaign_reserve)
        campaign_path = (
            store.scan_dir(scan_id)
            / "revisions"
            / str(revision)
            / "deep_scan_campaign.json"
        )
        try:
            _validate_exhaustive_inputs(graph, mesh)
            astra_calls += 1
            campaign = _run_exhaustive_campaign(
                graph,
                mesh,
                request.exhaustive_evaluations,
                campaign_path,
                campaign_budget,
            )
            exhaustive_count = campaign["evaluations"]
            exhaustive_outcomes = campaign["outcomes"]
        except (RuntimeError, ValueError) as error:
            limitations.append(
                "Exhaustive route-and-reach campaign skipped: "
                f"{type(error).__name__}: {error}"
            )
        typesafe_calls += campaign_budget.used

    last_round = loop_outcome.adaptive_rounds[-1] if loop_outcome.adaptive_rounds else None
    accepted_round = next(
        (item for item in reversed(loop_outcome.adaptive_rounds) if item.accepted), None
    )
    result = result.model_copy(update={
        "recommended_graph": loop_outcome.candidate,
        "loop_cycles": loop_outcome.cycles,
        "violating_trials": loop_outcome.violating_trials,
        "ada_rule_violations": loop_outcome.ada_rule_violations,
        "converged": loop_outcome.converged,
        "redesign_model": (
            accepted_round.astra_model
            if accepted_round is not None
            else last_round.astra_model if last_round else None
        ),
        "redesign_accepted": accepted_round is not None,
        "redesign_reasons": (
            []
            if loop_outcome.converged
            else list(last_round.reasons) if last_round else []
        ),
        "adaptive_rounds": list(loop_outcome.adaptive_rounds),
    })

    if typesafe_calls > request.typesafe_call_limit:
        raise AssertionError("TypeSafe per-scan call limit was exceeded")
    if graph != SceneGraph.model_validate_json(row["graph_json"]):
        raise AssertionError("simulation changed its measured scan snapshot")
    result = result.model_copy(
        update={
            "typesafe_calls": typesafe_calls,
            "astra_calls": astra_calls,
            "physics": physics,
            "exhaustive_evaluations": exhaustive_count,
            "exhaustive_outcomes": exhaustive_outcomes,
            "limitations": limitations,
        }
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE simulations SET result_json=?, completed=?"
            " WHERE scan_id=? AND revision=?",
            (result.model_dump_json(), request.samples, str(scan_id), revision),
        )


def _run_exhaustive_campaign(
    graph: SceneGraph,
    mesh: LidarMesh | None,
    evaluations: int,
    output: Path,
    budget: TypeSafeCallBudget,
) -> dict:
    _validate_exhaustive_inputs(graph, mesh)
    assert mesh is not None
    suite, source = propose_tasks(graph)
    router = TypeSafeRouter(budget=budget)
    campaign = run_campaign(
        graph,
        mesh,
        suite,
        evaluations=evaluations,
        seed=graph.revision + 20_260_913,
        output=output,
        planner=lambda tasks, history: choose_task(tasks, history, router),
    )
    campaign["task_generation"] = source
    return campaign


def _validate_exhaustive_inputs(
    graph: SceneGraph, mesh: LidarMesh | None
) -> None:
    if mesh is None:
        raise ValueError("a LiDAR mesh is required")
    if mesh.floorY is None:
        raise ValueError("the LiDAR mesh has no floor reference")
    if not any(
        not bounds_the_room(node) and node.raw_category == "table"
        for node in graph.nodes
    ):
        raise ValueError("the scan has no table candidates for generated tasks")
