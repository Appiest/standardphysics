"""Deterministic repair search: furniture first, then the smallest wall shift that clears the room.

The furniture search is the bounded `propose_fix` loop the ceiling uses. When
it cannot clear every fixable problem, each side of the room is pushed out by
the maximum construction shift and searched again; a side that clears is then
narrowed to the fewest inches that still clear. The answer is always scored by
`score_completion`, the same checker the model is judged by.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from standardphysics_agents.fix import propose_fix
from standardphysics_agents.training import score_completion
from standardphysics_agents.training.construction import MAX_WALL_SHIFT_INCHES, WallShift, floor_edges, shift_walls
from standardphysics_agents.training.edits import TrainingEdits, edits_between, edits_json
from standardphysics_contracts import SceneGraph

SHIFT_STEPS_INCHES = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, MAX_WALL_SHIFT_INCHES)
SEARCH_LIMIT = 96
SEARCH_ROUNDS = 8


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


def _attempt(graph: SceneGraph, checker, shifts: list[WallShift], rejected: Counter) -> Solution | None:
    built = shift_walls(graph, shifts)
    layout = _furniture_layout(built, checker, rejected)
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
