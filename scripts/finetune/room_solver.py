"""Deterministic repair search: furniture first, then the least construction that clears the room.

Problems only a fitting edit clears (a counter too high, tables at the wrong
height, a control out of reach) are fitted before any wall or fixture moves:
for each, the candidates in `fitting_candidates` are scored alone and the best
one kept, and everything later builds on the fitted room.

The furniture search is the bounded `propose_fix` loop the ceiling uses. When
it cannot clear every fixable problem, a turning circle that is too tight is
cleared directly: every object reaching inside the required circle is pushed
straight out of it, furniture as a move, a built-in as a fixture move and an
exterior wall as a wall shift. After that, two kinds of construction are tried:
each built-in fixture named by a remaining problem is slid in eight directions
by a few sizes (the most promising slides then get the furniture search on
top), and each side of the room is pushed out by the maximum wall shift and,
when that clears, narrowed to the fewest inches that still clear. The answer
is always scored by `score_completion`, the same checker the model is judged by.
"""

from __future__ import annotations

import math
import os
import time
from collections import Counter
from dataclasses import dataclass

from fitting_candidates import fitting_candidates
from standardphysics_agents.fix import propose_fix
from standardphysics_agents.redesign import FurnitureMove
from standardphysics_agents.training import score_completion
from standardphysics_agents.training.construction import (
    MAX_FIXTURE_MOVE_INCHES,
    MAX_WALL_SHIFT_INCHES,
    FixtureMove,
    WallShift,
    fixture_ids,
    walled_edges,
)
from standardphysics_agents.training.edits import TrainingEdits, built_room, combined, edits_between, edits_json
from standardphysics_agents.training.prices import construction_price
from standardphysics_contracts import SceneGraph, lies_flat, to_inches, to_meters
from standardphysics_pipeline import contains_point
from standardphysics_pipeline.footprints import footprint

SHIFT_STEPS_INCHES = (1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, MAX_WALL_SHIFT_INCHES)
SEARCH_LIMIT = 96
SEARCH_ROUNDS = 8
FIXTURE_STEPS_INCHES = (3.0, 6.0, 12.0, 18.0, 24.0)
FIXTURE_DIRECTIONS = tuple((math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                           for angle in range(0, 360, 45))
FIXTURE_FINALISTS = 3
FITTING_ALTERNATIVES = 4
CIRCLE_CHECKS = frozenset({"turning_space"})
PUSH_MARGIN_INCHES = 1.5
BUDGET_SECONDS = float(os.environ.get("SOLVER_BUDGET_SECONDS", "inf"))
"""Wall-clock budget for construction search per room; the best answer found so far is returned when it runs out."""


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
        return (verdict["gate_accepts"], verdict["fixable_left"] == 0, -verdict["construction_cost"],
                verdict["shortfall_recovered"], verdict["reward"])


def _furniture_layout(graph: SceneGraph, checker, rejected: Counter) -> SceneGraph:
    def pinned(before, after) -> str | None:
        return "pinned pieces" if any(before.by_id(node_id).transform != after.by_id(node_id).transform
                                      for node_id in checker.pinned) else None

    layout = graph
    for _ in range(SEARCH_ROUNDS):
        before = checker.assess(layout)
        problems = checker.rearrangeable_problems(before)
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


def _scored(graph: SceneGraph, checker, edits: TrainingEdits) -> Solution:
    completion = edits_json(edits)
    return Solution(completion, score_completion(completion, graph, checker).as_dict())


def _attempt(graph: SceneGraph, checker, shifts: list[WallShift], rejected: Counter,
             fixtures: list[FixtureMove] = (), fittings: TrainingEdits = TrainingEdits()) -> Solution | None:
    construction = combined(TrainingEdits(wall_shifts=shifts, fixture_moves=list(fixtures)), fittings)
    try:
        built = built_room(graph, construction)
    except ValueError:
        return None
    layout = _furniture_layout(built, checker, rejected)
    edits = combined(construction, TrainingEdits(moves=edits_between(built, layout).moves))
    if edits == TrainingEdits():
        return None
    return _scored(graph, checker, edits)


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


def _nearest(centre: tuple[float, float], polygon: list) -> tuple[float, tuple[float, float]]:
    """Distance from the centre to the polygon's outline, and the closest point on it."""
    best = (math.inf, centre)
    for (x1, y1), (x2, y2) in zip(polygon, [*polygon[1:], polygon[0]]):
        dx, dy = x2 - x1, y2 - y1
        along = max(0.0, min(1.0, ((centre[0] - x1) * dx + (centre[1] - y1) * dy) / ((dx * dx + dy * dy) or 1.0)))
        point = (x1 + along * dx, y1 + along * dy)
        best = min(best, (math.hypot(point[0] - centre[0], point[1] - centre[1]), point))
    return best


@dataclass
class _Push:
    moves: dict
    fixtures: dict
    shifts: dict

    def add(self, graph, checker, fixtures: set, node, push: tuple[float, float], meters: float) -> bool:
        """Record the push for this node; False when nothing may move it that far."""
        if node.id in checker.pinned:
            return False
        if node.movable:
            self.moves[node.id] = FurnitureMove(node_id=node.id, dx=round(push[0] * meters, 3),
                                                dy=round(push[1] * meters, 3), rotation_degrees=0.0)
            return True
        inches = to_inches(meters)
        if node.id in fixtures:
            if inches > MAX_FIXTURE_MOVE_INCHES:
                return False
            self.fixtures[node.id] = FixtureMove(node_id=node.id, dx_inches=round(push[0] * inches, 1),
                                                 dy_inches=round(push[1] * inches, 1))
            return True
        if node.kind == "wall" and inches <= MAX_WALL_SHIFT_INCHES:
            edges = walled_edges(graph)
            if not edges:
                return False
            edge = max(edges, key=lambda e: e.outward[0] * push[0] + e.outward[1] * push[1])
            self.shifts[edge.side] = max(self.shifts.get(edge.side, 0.0), math.ceil(inches))
            return True
        return False

    def edits(self) -> TrainingEdits:
        return TrainingEdits(moves=list(self.moves.values()), fixture_moves=list(self.fixtures.values()),
                             wall_shifts=[WallShift(side=side, inches=inches) for side, inches in self.shifts.items()])


def _circle_push(graph: SceneGraph, checker) -> TrainingEdits | None:
    """Every object inside a too-tight turning circle, pushed straight out of it; None if one cannot move."""
    fixtures, push = fixture_ids(graph), _Push({}, {}, {})
    tight = [finding for finding in checker.fixable_problems(checker.assess(graph))
             if finding.check_id in CIRCLE_CHECKS and finding.locus and finding.locus.point]
    for finding in tight:
        centre = (finding.locus.point.x, finding.locus.point.y)
        radius = to_meters(finding.required_inches) / 2
        for node in graph.nodes:
            if lies_flat(node) or node.kind == "door":
                continue
            shape = footprint(node)
            distance, point = _nearest(centre, list(shape))
            covers = contains_point(shape, centre)
            if (distance >= radius and not covers) or distance == 0:
                continue
            toward = ((point[0] - centre[0]) / distance, (point[1] - centre[1]) / distance)
            # A piece covering the centre leaves through its nearest edge's opposite side: sliding it
            # back by that edge's distance plus the radius puts the edge on the circle.
            away = (-toward[0], -toward[1]) if covers else toward
            meters = (distance if covers else -distance) + radius + to_meters(PUSH_MARGIN_INCHES)
            if not push.add(graph, checker, fixtures, node, away, meters):
                return None
    edits = push.edits()
    return edits if edits.moves or edits.fixture_moves or edits.wall_shifts else None


def _circle_options(graph: SceneGraph, checker, rejected: Counter) -> list[Solution]:
    edits = _circle_push(graph, checker)
    if edits is None:
        return []
    completion = edits_json(edits)
    pushed = Solution(completion, score_completion(completion, graph, checker).as_dict())
    if pushed.clears or not (edits.fixture_moves or edits.wall_shifts):
        return [pushed]
    searched = _attempt(graph, checker, edits.wall_shifts, rejected, edits.fixture_moves)
    return [pushed, *([searched] if searched else [])]


def _blocking_fixtures(graph: SceneGraph, checker) -> list:
    fixtures = fixture_ids(graph) - set(checker.pinned)
    named = [node_id for finding in checker.rearrangeable_problems(checker.assess(graph)) if finding.locus
             for node_id in finding.locus.node_ids if node_id in fixtures]
    return list(dict.fromkeys(named))


def _fixture_slides(graph: SceneGraph, checker) -> list[Solution]:
    """Every single-fixture slide, scored alone, best first."""
    slides = []
    for node_id in _blocking_fixtures(graph, checker):
        for ux, uy in FIXTURE_DIRECTIONS:
            for inches in FIXTURE_STEPS_INCHES:
                move = FixtureMove(node_id=node_id, dx_inches=round(ux * inches, 1), dy_inches=round(uy * inches, 1))
                completion = edits_json(TrainingEdits(fixture_moves=[move]))
                slides.append(Solution(completion, score_completion(completion, graph, checker).as_dict()))
    return sorted(slides, key=lambda slide: slide.rank, reverse=True)


def _fixture_options(graph: SceneGraph, checker, rejected: Counter) -> list[Solution]:
    slides = _fixture_slides(graph, checker)
    cleared = [slide for slide in slides if slide.clears]
    if cleared:
        return cleared[:1]
    options = []
    for slide in [slide for slide in slides if slide.verdict["gate_accepts"]][:FIXTURE_FINALISTS]:
        moves = TrainingEdits.model_validate_json(slide.completion).fixture_moves
        option = _attempt(graph, checker, [], rejected, moves)
        if option is not None:
            options.append(option)
    return options


def _fitting_rank(solution: Solution) -> tuple:
    verdict = solution.verdict
    left = verdict["fixable_left"] if verdict["fixable_left"] is not None else math.inf
    return (verdict["hard_constraints_pass"], verdict["gate_accepts"], -left, -verdict["construction_cost"])


def _best_fitting(graph: SceneGraph, checker, chosen: TrainingEdits, candidates: list[TrainingEdits]) -> list:
    """This problem's candidates that break no hard constraint on top of what is chosen, best first."""
    scored = [(_scored(graph, checker, combined(chosen, candidate)), candidate) for candidate in candidates]
    kept = [pair for pair in scored if pair[0].verdict["hard_constraints_pass"]]
    return [candidate for _, candidate in sorted(kept, key=lambda pair: _fitting_rank(pair[0]), reverse=True)]


def _fitting_options(graph: SceneGraph, checker, rejected: Counter) -> tuple[TrainingEdits, list[Solution]]:
    """The fittings chosen greedily problem by problem, and the answers built on them with furniture on top."""
    chosen, alternatives = TrainingEdits(), []
    for candidates in fitting_candidates(graph, checker):
        ranked = _best_fitting(graph, checker, chosen, candidates)
        if ranked:
            alternatives += [(chosen, other) for other in ranked[1:]]
            chosen = combined(chosen, ranked[0])
    if chosen == TrainingEdits():
        return chosen, []
    options = [_scored(graph, checker, chosen)]
    if not options[0].clears:
        options.append(_attempt(graph, checker, [], rejected, fittings=chosen))
    for before, other in alternatives[:FITTING_ALTERNATIVES]:
        trial = combined(before, other)
        if construction_price(graph, trial) < _cheapest_clear(options):
            options.append(_attempt(graph, checker, [], rejected, fittings=trial))
    return chosen, [option for option in options if option is not None]


def _cheapest_clear(options: list[Solution | None]) -> float:
    return min((option.verdict["construction_cost"] for option in options if option is not None and option.clears),
               default=math.inf)


def _on_top_of(graph: SceneGraph, checker, fitted: TrainingEdits, option: Solution) -> Solution:
    """An answer found on the fitted room, with the fittings put back in front of it."""
    return _scored(graph, checker, combined(fitted, TrainingEdits.model_validate_json(option.completion)))


def solve(graph: SceneGraph, checker, allow_construction: bool = True) -> tuple[Solution | None, Counter]:
    """The best checker-scored repair found, preferring furniture only, then the cheapest construction."""
    rejected: Counter = Counter()
    if not checker.fixable_problems(checker.assess(graph)):
        return None, rejected
    options = [option for option in [_attempt(graph, checker, [], rejected)] if option is not None]
    if (options and options[0].clears) or not allow_construction:
        return (max(options, key=lambda option: option.rank) if options else None), rejected
    fitted, fitting_options = _fitting_options(graph, checker, rejected)
    options.extend(fitting_options)
    if not any(option.clears for option in options):
        room = graph if fitted == TrainingEdits() else built_room(graph, fitted)
        layout_options = _layout_construction(room, checker, rejected)
        options.extend(option if room is graph else _on_top_of(graph, checker, fitted, option)
                       for option in layout_options)
    return (max(options, key=lambda option: option.rank) if options else None), rejected


def _layout_construction(graph: SceneGraph, checker, rejected: Counter) -> list[Solution]:
    """Turning-circle pushes, fixture slides and wall shifts, stopping at the first that clears."""
    options: list[Solution] = []
    deadline = time.monotonic() + BUDGET_SECONDS
    options.extend(_circle_options(graph, checker, rejected))
    if not any(option.clears for option in options):
        options.extend(_fixture_options(graph, checker, rejected))
    for edge in walled_edges(graph):
        if time.monotonic() > deadline or any(option.clears for option in options):
            break
        widest = _attempt(graph, checker, [WallShift(side=edge.side, inches=MAX_WALL_SHIFT_INCHES)], rejected)
        if widest is None:
            continue
        options.append(_narrowed(graph, checker, edge.side, widest, rejected) if widest.clears else widest)
    return options
