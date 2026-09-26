"""Deterministic repair search: furniture first, then the smallest wall shift that clears the room.

The furniture search is the bounded `propose_fix` loop the ceiling uses. When
it cannot clear every fixable problem, each side of the room is pushed out by
the maximum construction shift and searched again; a side that clears is then
narrowed to the fewest inches that still clear. The answer is always scored by
`score_completion`, the same checker the model is judged by.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from standardphysics_agents.fix import apply_moves, propose_fix
from standardphysics_agents.redesign import FurnitureMove
from standardphysics_agents.training import score_completion
from standardphysics_agents.training.construction import MAX_WALL_SHIFT_INCHES, WallShift, floor_edges, shift_walls
from standardphysics_agents.training.edits import TrainingEdits, edits_between, edits_json, node_moves
from standardphysics_contracts import SceneGraph

SHIFT_STEPS_INCHES = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, MAX_WALL_SHIFT_INCHES)
SEARCH_LIMIT = 96
SEARCH_ROUNDS = 8
NUDGE_METERS = (0.05, 0.1, 0.15, 0.25, 0.4, 0.6)
NUDGE_DIRECTIONS = tuple((math.cos(math.radians(a)), math.sin(math.radians(a))) for a in range(0, 360, 45))
NUDGE_ROUNDS = 3


@dataclass(frozen=True)
class Solution:
    completion: str
    verdict: dict

    @property
    def clears(self) -> bool:
        return bool(self.verdict["gate_accepts"]) and self.verdict["fixable_left"] == 0

    @property
    def rank(self) -> tuple:
        verdict = self.verdict
        return (verdict["gate_accepts"], verdict["fixable_left"] == 0, -verdict["construction_inches"],
                verdict["shortfall_recovered"], verdict["reward"])


def _furniture_layout(graph: SceneGraph, checker, rejected: Counter) -> SceneGraph:
    def pinned(before, after) -> str | None:
        return "pinned pieces" if any(before.by_id(node_id).transform != after.by_id(node_id).transform
                                      for node_id in checker.pinned) else None

    layout = graph
    for _ in range(SEARCH_ROUNDS):
        before = checker.assess(layout)
        problems = checker.fixable_problems(before)
        if not problems:
            break
        outcome = propose_fix(layout, checker.scenario, checker.measure, problems, rules=checker.rules,
                              ledger=checker.ledger, baseline=before, max_tier=checker.max_tier,
                              limit=SEARCH_LIMIT, offer_relaxation=False, candidate_rejection=pinned)
        rejected.update(outcome.rejected)
        if not outcome.found:
            break
        layout = outcome.graph
    return layout


def _nudgeable(layout: SceneGraph, checker) -> list:
    """Movable, unpinned pieces named by a remaining fixable problem."""
    ids = {node_id for finding in checker.fixable_problems(checker.assess(layout)) if finding.locus
           for node_id in finding.locus.node_ids}
    return [node for node in layout.nodes if node.id in ids and node.movable and node.id not in checker.pinned]


def _nudge_rank(verdict) -> tuple:
    return (verdict.fixable_left == 0, -verdict.fixable_left, verdict.shortfall_recovered, -verdict.disruption_meters)


def _best_nudge(layout: SceneGraph, checker) -> SceneGraph | None:
    """The single slide of one involved piece the checker likes best, or None when none is accepted."""
    best, best_rank = None, None
    for node in _nudgeable(layout, checker):
        for (ux, uy) in NUDGE_DIRECTIONS:
            for meters in NUDGE_METERS:
                edits = TrainingEdits(moves=[FurnitureMove(node_id=node.id, dx=round(ux * meters, 3),
                                                           dy=round(uy * meters, 3), rotation_degrees=0.0)])
                verdict = score_completion(edits_json(edits), layout, checker)
                if verdict.gate_accepts and (best_rank is None or _nudge_rank(verdict) > best_rank):
                    best, best_rank = apply_moves(layout, node_moves(edits)), _nudge_rank(verdict)
    return best


def _nudged(layout: SceneGraph, checker) -> SceneGraph:
    for _ in range(NUDGE_ROUNDS):
        if not checker.fixable_problems(checker.assess(layout)):
            break
        step = _best_nudge(layout, checker)
        if step is None:
            break
        layout = step
    return layout


def _attempt(graph: SceneGraph, checker, shifts: list[WallShift], rejected: Counter) -> Solution | None:
    built = shift_walls(graph, shifts)
    layout = _nudged(_furniture_layout(built, checker, rejected), checker)
    edits = TrainingEdits(moves=edits_between(built, layout).moves, wall_shifts=shifts)
    if not edits.moves and not shifts:
        return None
    completion = edits_json(edits)
    return Solution(completion, score_completion(completion, graph, checker).as_dict())


def _narrowed(graph: SceneGraph, checker, side, cleared: Solution, rejected: Counter) -> Solution:
    """The fewest inches on this side that still clear, assuming more room never hurts."""
    steps = [step for step in SHIFT_STEPS_INCHES if step < MAX_WALL_SHIFT_INCHES]
    best, low, high = cleared, 0, len(steps) - 1
    while low <= high:
        middle = (low + high) // 2
        trial = _attempt(graph, checker, [WallShift(side=side, inches=steps[middle])], rejected)
        if trial is not None and trial.clears:
            best, high = trial, middle - 1
        else:
            low = middle + 1
    return best


def solve(graph: SceneGraph, checker, allow_construction: bool = True) -> tuple[Solution | None, Counter]:
    """The best checker-scored repair found, preferring furniture only, then the smallest construction."""
    rejected: Counter = Counter()
    options = [option for option in [_attempt(graph, checker, [], rejected)] if option is not None]
    if (options and options[0].clears) or not allow_construction:
        return (max(options, key=lambda option: option.rank) if options else None), rejected
    for edge in floor_edges(graph):
        widest = _attempt(graph, checker, [WallShift(side=edge.side, inches=MAX_WALL_SHIFT_INCHES)], rejected)
        if widest is None:
            continue
        options.append(_narrowed(graph, checker, edge.side, widest, rejected) if widest.clears else widest)
    return (max(options, key=lambda option: option.rank) if options else None), rejected
