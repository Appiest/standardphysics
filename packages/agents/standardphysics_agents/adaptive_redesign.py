"""Bounded Astra proposals with deterministic validation and Jev ranking."""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_contracts import AdaptiveRoundResult, Scenario, SceneGraph, SimulationResult, graph_hash

from .accessibility_intelligence import AccessibilityIntelligence, LayoutCandidate
from .assess import assess
from .checks.protrusions import is_mounted
from .mesh_collision import MeshCollisionIndex
from .redesign import propose_redesign
from .router import SystemOneClient, SystemOneError, TypeSafeCallBudget
from .workflows import FunctionalProfile, Workflow, evaluate_workflow

MAX_FAILURE_EVIDENCE = 64

PLACEMENT_CHECKS = frozenset({
    "route_clear_width",
    "turn_clear_width",
    "passing_space",
    "turning_space",
    "exit_path",
    "door_maneuvering_clearance",
    "service_counter_approach",
})
"""Checks whose answer depends on where floor-standing furniture sits.

Astra only slides furniture across the floor, so a height problem or a mounted
object sticking out is not work it can do, however movable the object is.
"""


@dataclass(frozen=True)
class AdaptiveRedesignResult:
    """The best recommendation; the measured graph is never modified."""

    graph: SceneGraph | None
    rounds: tuple[AdaptiveRoundResult, ...]
    astra_calls: int


def run_adaptive_redesign(
    measured_graph: SceneGraph,
    *,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    measure,
    rules,
    ledger,
    rounds: int,
    budget: TypeSafeCallBudget,
    collision_index: MeshCollisionIndex | None = None,
    starting_graph: SceneGraph | None = None,
    astra_client=None,
    intelligence: AccessibilityIntelligence | None = None,
    scenario: Scenario | None = None,
    route_trials: dict | None = None,
) -> AdaptiveRedesignResult:
    """Try one candidate per round and stop at the first unsafe or unhelpful step.

    With `scenario`, rule problems that involve movable furniture count as work
    for Astra and as evidence for Jev, so a layout the route trials already
    cleared still gets its remaining furniture problems looked at.
    `route_trials` is what those trials learned; Astra builds on it.
    """
    if not 1 <= rounds <= 8:
        raise ValueError("adaptive redesign rounds must be between 1 and 8")
    if not workflows or not profiles:
        raise ValueError("adaptive redesign needs workflows and profiles")

    original_hash = graph_hash(measured_graph)
    current = starting_graph or measured_graph
    accepted_any = False
    history: list[AdaptiveRoundResult] = []
    astra_calls = 0
    jev = intelligence or AccessibilityIntelligence(SystemOneClient(budget=budget))
    redesign_collision_index = (
        collision_index.excluding_nodes(measured_graph.movable())
        if collision_index is not None
        else None
    )

    def evidence_for(layout: SceneGraph) -> dict:
        route_evidence = _layout_evidence(
            measured_graph, layout, workflows, profiles, measure, redesign_collision_index
        )
        return route_evidence | _rule_problem_evidence(layout, scenario, measure, rules, ledger)

    for round_number in range(1, rounds + 1):
        base_hash = graph_hash(current)
        current_evidence = evidence_for(current)
        if not current_evidence["actionable_failures"] and not current_evidence["actionable_rule_problems"]:
            history.append(
                AdaptiveRoundResult(
                    round=round_number,
                    base_graph_hash=base_hash,
                    astra_model=None,
                    accepted=False,
                    reasons=["no_actionable_furniture_failure"],
                )
            )
            break
        astra_calls += 1
        astra_evidence = current_evidence | ({"route_trials": route_trials} if route_trials else {})
        proposal = propose_redesign(
            current,
            workflows,
            profiles,
            [astra_evidence],
            measure,
            rules=rules,
            ledger=ledger,
            collision_index=redesign_collision_index,
            model=astra_client,
        )
        if not proposal.accepted or proposal.graph is None:
            history.append(
                AdaptiveRoundResult(
                    round=round_number,
                    base_graph_hash=base_hash,
                    astra_model=proposal.model,
                    accepted=False,
                    reasons=list(proposal.reasons),
                )
            )
            break

        candidate = proposal.graph
        candidate_evidence = evidence_for(candidate)
        current_id = f"current:{base_hash}"
        candidate_id = f"candidate:{graph_hash(candidate)}"
        try:
            ranking = jev.rank_layouts(
                [
                    LayoutCandidate(
                        id=current_id,
                        deterministic_checks_passed=True,
                        usability_evidence=current_evidence,
                        disruption_evidence=current_evidence["disruption"],
                    ),
                    LayoutCandidate(
                        id=candidate_id,
                        deterministic_checks_passed=True,
                        usability_evidence=candidate_evidence,
                        disruption_evidence=candidate_evidence["disruption"],
                    ),
                ]
            )
        except SystemOneError as error:
            history.append(
                AdaptiveRoundResult(
                    round=round_number,
                    base_graph_hash=base_hash,
                    astra_model=proposal.model,
                    accepted=False,
                    reasons=[str(error)],
                )
            )
            break

        preferred = ranking[0].candidate.id
        accepted = preferred == candidate_id
        history.append(
            AdaptiveRoundResult(
                round=round_number,
                base_graph_hash=base_hash,
                astra_model=proposal.model,
                accepted=accepted,
                reasons=(
                    ["deterministic_improvement_and_jev_preference"]
                    if accepted
                    else ["jev_preferred_current_layout"]
                ),
                jev_preferred_candidate=preferred,
            )
        )
        if not accepted:
            break
        current = candidate
        accepted_any = True

    if graph_hash(measured_graph) != original_hash:
        raise AssertionError("adaptive redesign changed the measured scan")
    return AdaptiveRedesignResult(
        graph=current if accepted_any else None,
        rounds=tuple(history),
        astra_calls=astra_calls,
    )


def route_trial_evidence(measured: SceneGraph, result: SimulationResult, router: str) -> dict:
    """What the route trials already learned about this room, in the terms Astra works in."""
    labels = {str(node.id): node.label for node in measured.nodes}
    original = {node.id: node.transform.position for node in measured.nodes}
    kept_layout = result.recommended_graph.nodes if result.recommended_graph is not None else []
    kept_moves = [
        {
            "node_id": str(node.id),
            "label": node.label,
            "dx_meters": round(node.transform.position.x - original[node.id].x, 3),
            "dy_meters": round(node.transform.position.y - original[node.id].y, 3),
        }
        for node in kept_layout
        if node.id in original and node.transform.position != original[node.id]
    ]
    failing = [item for item in result.feedback if item.passed_trials < item.trials]
    return {
        "decided_by": router,
        "trials": result.total_runs,
        "rejected_trials": result.rejected_runs,
        "action_counts": dict(result.action_counts),
        "kept_moves": kept_moves,
        "failing_workflow_count": len(failing),
        "failing_workflows": [
            {
                "workflow_title": item.workflow_title,
                "profile_title": item.profile_title,
                "passed_trials": item.passed_trials,
                "trials": item.trials,
                "blocker_labels": [labels.get(str(blocker), str(blocker)) for blocker in item.blocking_node_ids],
            }
            for item in failing[:MAX_FAILURE_EVIDENCE]
        ],
    }


def _rule_problem_evidence(layout: SceneGraph, scenario: Scenario | None, measure, rules, ledger) -> dict:
    """Rule problems on this layout, and the ones sliding floor furniture could address."""
    if scenario is None:
        return {"rule_problem_count": 0, "actionable_rule_problems": [], "actionable_rule_problem_count": 0}
    problems = assess(layout, scenario, measure, rules=rules, ledger=ledger, max_tier=3).assessment.problems
    nodes = {node.id: node for node in layout.nodes}
    placement = [_rule_problem(problem, nodes) for problem in problems if problem.check_id in PLACEMENT_CHECKS]
    actionable = [item for item in placement if item["movable_node_ids"]]
    return {
        "rule_problem_count": len(problems),
        "actionable_rule_problems": actionable[:MAX_FAILURE_EVIDENCE],
        "actionable_rule_problem_count": len(actionable),
    }


def _rule_problem(problem, nodes: dict) -> dict:
    located = [nodes[node_id] for node_id in (problem.locus.node_ids if problem.locus else []) if node_id in nodes]
    floor_movable = [node for node in located if node.movable and not is_mounted(node)]
    return {
        "check_id": problem.check_id,
        "title": problem.title,
        "detail": problem.detail,
        "measured_inches": problem.measured_inches,
        "required_inches": problem.required_inches,
        "movable_node_ids": [str(node.id) for node in floor_movable],
        "movable_labels": [node.label for node in floor_movable],
        "fixed_labels": [node.label for node in located if node not in floor_movable],
    }


def _layout_evidence(
    measured: SceneGraph,
    layout: SceneGraph,
    workflows: list[Workflow],
    profiles: list[FunctionalProfile],
    measure,
    collision_index: MeshCollisionIndex | None,
) -> dict:
    evaluations = [
        (
            workflow,
            profile,
            evaluate_workflow(
                layout,
                workflow,
                profile,
                measure,
                collision_index=collision_index,
            ),
        )
        for workflow in workflows
        for profile in profiles
    ]
    legs = [leg for _, _, evaluation in evaluations for leg in evaluation.legs]
    interactions = [
        interaction
        for _, _, evaluation in evaluations
        for interaction in evaluation.interactions
    ]
    nodes = {node.id: node for node in layout.nodes}
    actionable_failures = []
    non_furniture_failures = []
    unlocalized_mesh_failures = []
    for workflow, profile, evaluation in evaluations:
        for leg in evaluation.legs:
            if leg.passed:
                continue
            movable_blockers = [
                blocker_id
                for blocker_id in leg.blocking_node_ids
                if blocker_id in nodes and nodes[blocker_id].movable
            ]
            fixed_blockers = [
                blocker_id
                for blocker_id in leg.blocking_node_ids
                if blocker_id in nodes and not nodes[blocker_id].movable
            ]
            evidence = {
                "workflow_id": workflow.id,
                "workflow_title": workflow.title,
                "profile_id": profile.id,
                "profile_title": profile.title,
                "origin": leg.origin,
                "destination": leg.destination,
                "reachable": leg.reachable,
                "measured_width_inches": round(leg.measured_width_inches, 3),
                "required_width_inches": round(leg.required_width_inches, 3),
                "meets_clearance": leg.meets_clearance,
                "floor_plan_collision": leg.floor_plan_collision,
                "mesh_collision": leg.mesh_collision,
                "movable_blocker_ids": [str(item) for item in movable_blockers],
                "movable_blocker_labels": [nodes[item].label for item in movable_blockers],
                "fixed_blocker_ids": [str(item) for item in fixed_blockers],
                "fixed_blocker_labels": [nodes[item].label for item in fixed_blockers],
            }
            if movable_blockers and (
                leg.floor_plan_collision or not leg.meets_clearance
            ):
                actionable_failures.append(evidence)
            elif leg.mesh_collision and not leg.floor_plan_collision:
                unlocalized_mesh_failures.append(evidence)
            else:
                non_furniture_failures.append(evidence)

    evidence_gaps = []
    for workflow, profile, evaluation in evaluations:
        for interaction in evaluation.interactions:
            if interaction.needs_measurement or interaction.reachable is not True:
                evidence_gaps.append(
                    {
                        "workflow_id": workflow.id,
                        "workflow_title": workflow.title,
                        "profile_id": profile.id,
                        "profile_title": profile.title,
                        "interaction_id": interaction.interaction.id,
                        "interaction_title": interaction.interaction.title,
                        "needs_measurement": interaction.needs_measurement,
                        "reachable": interaction.reachable,
                    }
                )
    original = {node.id: node for node in measured.nodes}
    moved = [
        node
        for node in layout.nodes
        if node.id in original and node.transform != original[node.id].transform
    ]
    translation = sum(
        math.dist(
            (node.transform.position.x, node.transform.position.y),
            (
                original[node.id].transform.position.x,
                original[node.id].transform.position.y,
            ),
        )
        for node in moved
    )
    return {
        "graph_hash": graph_hash(layout),
        "workflow_profile_evaluations": len(evaluations),
        "passed_evaluations": sum(item.passed for _, _, item in evaluations),
        "route_legs": len(legs),
        "passed_route_legs": sum(leg.passed for leg in legs),
        "reachable_interactions": sum(item.reachable is True for item in interactions),
        "unreachable_interactions": sum(item.reachable is False for item in interactions),
        "interactions_needing_measurement": sum(
            item.needs_measurement for item in interactions
        ),
        "actionable_failures": actionable_failures[:MAX_FAILURE_EVIDENCE],
        "actionable_failure_count": len(actionable_failures),
        "non_furniture_failures": non_furniture_failures[:MAX_FAILURE_EVIDENCE],
        "non_furniture_failure_count": len(non_furniture_failures),
        "unlocalized_mesh_failures": unlocalized_mesh_failures[:MAX_FAILURE_EVIDENCE],
        "unlocalized_mesh_failure_count": len(unlocalized_mesh_failures),
        "evidence_gaps": evidence_gaps[:MAX_FAILURE_EVIDENCE],
        "evidence_gap_count": len(evidence_gaps),
        "disruption": {
            "moved_object_count": len(moved),
            "total_translation_meters": translation,
            "fixed_objects_moved": sum(not node.movable for node in moved),
            "inventory_preserved": {node.id for node in measured.nodes}
            == {node.id for node in layout.nodes},
        },
    }


__all__ = ["AdaptiveRedesignResult", "route_trial_evidence", "run_adaptive_redesign"]
