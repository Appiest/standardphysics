"""Repair a failing room: seating arrangements first, Astra only if needed.

ADA compliance is a hard gate. Among layouts that keep every previously seated
chair at a table and clear the measured problems, the cheapest composition cost
wins so the loop does not prefer shoving tables aside.
"""

from __future__ import annotations

import math

from standardphysics_contracts import (
    AdaptiveRoundResult,
    NodeMove,
    Scenario,
    SceneGraph,
    Vec3,
    graph_hash,
)
from standardphysics_contracts.precedents import PrecedentDirective
from standardphysics_pipeline.footprints import rotation_about_z

from .adaptive_redesign import AdaptiveRedesignResult, run_adaptive_redesign
from .assess import assess
from .evaluation.gate import accepts
from .fix import apply_moves, propose_fix
from .fix.composition import Composition, lost_seating
from .fix.furnishing import arrangements
from .workflow_evaluation import evaluate_workflow, workflow_candidate_rejection


def moves_between(before: SceneGraph, after: SceneGraph) -> list[NodeMove]:
    """Floor slides and turns that take `before` to `after` for shared objects."""
    later = {node.id: node for node in after.nodes}
    moves: list[NodeMove] = []
    for node in before.nodes:
        if node.kind != "object" or node.id not in later:
            continue
        other = later[node.id]
        dx = other.transform.position.x - node.transform.position.x
        dy = other.transform.position.y - node.transform.position.y
        cos_a, sin_a = rotation_about_z(node)
        cos_b, sin_b = rotation_about_z(other)
        turn = math.degrees(math.atan2(sin_b, cos_b) - math.atan2(sin_a, cos_a))
        turn = (turn + 180.0) % 360.0 - 180.0
        if abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(turn) < 1e-6:
            continue
        moves.append(
            NodeMove(
                node_id=node.id,
                delta_translation=Vec3(x=dx, y=dy, z=0.0),
                delta_rotation_z_degrees=turn,
            )
        )
    return moves


def aesthetics_cost(base: SceneGraph, layout: SceneGraph) -> float:
    """How expensive the layout looks relative to `base` (lower is better)."""
    return Composition.of(base).cost(moves_between(base, layout))


def prefer_layout(base: SceneGraph, candidates: list[SceneGraph]) -> SceneGraph:
    """Pick the ADA-ready candidate with the lowest aesthetic cost."""
    return min(
        candidates,
        key=lambda layout: (aesthetics_cost(base, layout), graph_hash(layout)),
    )


def seating_broken(measured: SceneGraph, layout: SceneGraph) -> bool:
    """True when a repair stranded a chair that was seated in the measured room."""
    return lost_seating(measured, layout)


def _routes_improved(
    before: SceneGraph,
    after: SceneGraph,
    *,
    workflows,
    profiles,
    measure,
    collision_index,
) -> bool:
    improved = False
    for workflow in workflows:
        for profile in profiles:
            prior = evaluate_workflow(
                before, workflow, profile, measure, collision_index=collision_index
            )
            next_eval = evaluate_workflow(
                after, workflow, profile, measure, collision_index=collision_index
            )
            if any(
                old.passed and not new.passed
                for old, new in zip(prior.legs, next_eval.legs)
            ):
                return False
            if any(
                (old.reachable is True and new.reachable is not True)
                or (not old.needs_measurement and new.needs_measurement)
                for old, new in zip(prior.interactions, next_eval.interactions)
            ):
                return False
            improved = improved or any(
                not old.passed and new.passed
                for old, new in zip(prior.legs, next_eval.legs)
            )
            improved = improved or any(
                old.reachable is not True and new.reachable is True
                for old, new in zip(prior.interactions, next_eval.interactions)
            )
    return improved


def _accepts_repair(
    before_graph: SceneGraph,
    candidate: SceneGraph,
    *,
    scenario: Scenario,
    workflows,
    profiles,
    measure,
    rules,
    ledger,
    collision_index,
) -> bool:
    if lost_seating(before_graph, candidate):
        return False
    before = assess(
        before_graph, scenario, measure, rules=rules, ledger=ledger, max_tier=3
    )
    after = assess(
        candidate, scenario, measure, rules=rules, ledger=ledger, max_tier=3
    )
    if not accepts(before, after, require_improvement=False):
        return False
    if workflow_candidate_rejection(
        before_graph,
        candidate,
        workflows=workflows,
        profiles=profiles,
        measure=measure,
        collision_index=collision_index,
    ):
        return False
    if accepts(before, after):
        return True
    return _routes_improved(
        before_graph,
        candidate,
        workflows=workflows,
        profiles=profiles,
        measure=measure,
        collision_index=collision_index,
    )


def try_deterministic_repair(
    graph: SceneGraph,
    *,
    scenario: Scenario,
    workflows,
    profiles,
    measure,
    rules,
    ledger,
    collision_index=None,
) -> SceneGraph | None:
    """Whole-room seating plans and local fixes before any provider call."""

    def reject(prior: SceneGraph, candidate: SceneGraph) -> str | None:
        if lost_seating(prior, candidate):
            return "stranded_seating"
        return workflow_candidate_rejection(
            prior,
            candidate,
            workflows=workflows,
            profiles=profiles,
            measure=measure,
            collision_index=collision_index,
        )

    before = assess(graph, scenario, measure, rules=rules, ledger=ledger, max_tier=3)
    rearrangeable = [
        finding
        for finding in before.problems
        if rules.by_id(finding.check_id).rearrangeable
    ]
    if rearrangeable:
        outcome = propose_fix(
            graph,
            scenario,
            measure,
            rearrangeable,
            rules=rules,
            ledger=ledger,
            baseline=before,
            max_tier=3,
            offer_relaxation=False,
            candidate_rejection=reject,
        )
        if outcome.graph is not None:
            return outcome.graph

    accepted: list[SceneGraph] = []
    for plan in arrangements(graph, scenario, rules):
        candidate = apply_moves(graph, plan.moves)
        if not _accepts_repair(
            graph,
            candidate,
            scenario=scenario,
            workflows=workflows,
            profiles=profiles,
            measure=measure,
            rules=rules,
            ledger=ledger,
            collision_index=collision_index,
        ):
            continue
        accepted.append(candidate)
    if not accepted:
        return None
    return prefer_layout(graph, accepted)


def run_layout_repair(
    measured_graph: SceneGraph,
    *,
    workflows,
    profiles,
    measure,
    rules,
    ledger,
    budget,
    collision_index=None,
    starting_graph: SceneGraph | None = None,
    astra_client=None,
    scenario: Scenario | None = None,
    route_trials: dict | None = None,
    rounds: int = 1,
    directives: tuple[PrecedentDirective, ...] = (),
) -> AdaptiveRedesignResult:
    """Deterministic seating repair first; Astra, held to the room's ADA `directives`, only when that cannot help."""
    current = starting_graph or measured_graph
    if scenario is not None:
        deterministic = try_deterministic_repair(
            current,
            scenario=scenario,
            workflows=workflows,
            profiles=profiles,
            measure=measure,
            rules=rules,
            ledger=ledger,
            collision_index=collision_index,
        )
        if deterministic is not None:
            return AdaptiveRedesignResult(
                graph=prefer_layout(measured_graph, [deterministic]),
                rounds=(
                    AdaptiveRoundResult(
                        round=1,
                        base_graph_hash=graph_hash(current),
                        astra_model=None,
                        accepted=True,
                        reasons=["deterministic_seating_arrangement"],
                    ),
                ),
                astra_calls=0,
            )
    return run_adaptive_redesign(
        measured_graph,
        workflows=workflows,
        profiles=profiles,
        measure=measure,
        rules=rules,
        ledger=ledger,
        rounds=rounds,
        budget=budget,
        collision_index=collision_index,
        starting_graph=starting_graph,
        astra_client=astra_client,
        scenario=scenario,
        route_trials=route_trials,
        directives=directives,
    )
