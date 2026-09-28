"""Checker feedback in plain words, a map of open floor, and the multi-turn proposal loop.

A rejected proposal is never applied, so every round starts from the room as
first described. `diagnose` scores a completion exactly as training did
(`score_completion`) and, when the room is refused, says why in terms a model
can act on: which piece hit which, where a new problem appeared, or that
nothing measurable changed. `open_floor_rectangles` lists the largest empty
axis-aligned rectangles of floor, for the arm that is told where free space is.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from standardphysics_agents.evaluation.gate import MIN_MEANINGFUL_SHORTFALL_INCHES, total_shortfall
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.constraints import MAX_TRAVEL_METERS, door_keep_clear, interior_bounds
from standardphysics_agents.fix.moves import measured_position
from standardphysics_agents.training import TrainingChecker
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.snapped_reward import MOVED_PINNED, Verdict, score_completion
from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room, lies_flat
from standardphysics_pipeline.footprints import floor_polygon, footprint, gap_between
from standardphysics_pipeline.occupancy import blocks_floor

EDIT_COMPLAINTS = {
    "no_supported_furniture_move": "The answer contained no moves.",
    "duplicate_objects": "The answer moved the same piece twice; give each piece one move.",
    "unknown_objects": "The answer used IDs that are not in `movable_objects`.",
    "no_op_moves": "Every move was zero, so nothing would change.",
}
GATE_CATEGORIES = (
    ("new problem", "new_problem"),
    ("more problems", "new_problem"),
    ("stopped", "lost_coverage"),
    ("lost its measured answer", "lost_coverage"),
    ("turned from a question", "lost_coverage"),
    ("within measurement noise", "noise"),
    ("nothing measurable changed", "nothing_changed"),
)
CLOSING = ("Nothing was moved, so the room is still exactly as first described. Propose a revised set of moves, "
           "measured from the original positions, as JSON only in the same format.")
CELL_METERS = 0.1
MIN_RECTANGLE_SQUARE_METERS = 0.25


def _r(value: float) -> float:
    return round(value, 2)


def _at(node: SceneNode) -> list[float]:
    return [_r(node.transform.position.x), _r(node.transform.position.y)]


@dataclass(frozen=True)
class Attempt:
    verdict: Verdict
    category: str
    notes: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.verdict.gate_accepts


# --- naming things --------------------------------------------------------------


@dataclass(frozen=True)
class Names:
    room: SceneGraph
    movable_ids: frozenset = field(default_factory=frozenset)

    @classmethod
    def of(cls, room: SceneGraph) -> Names:
        return cls(room, frozenset(node.id for node in room.nodes if node.movable and not bounds_the_room(node)))

    def piece(self, node: SceneNode) -> str:
        if node.id in self.movable_ids:
            return f"{node.label} ({node.id})"
        return f"the fixed {node.label}"

    def node(self, node_id) -> SceneNode | None:
        return next((node for node in self.room.nodes if str(node.id) == str(node_id)), None)


# --- hard constraints -----------------------------------------------------------


def _nearest_named(candidate: SceneGraph, moved: SceneNode, label: str) -> SceneNode | None:
    shape = footprint(moved)
    others = [node for node in candidate.nodes if node.label == label and node.id != moved.id]
    return min(others, key=lambda other: gap_between(shape, footprint(other)), default=None)


def _collision_note(violation, candidate: SceneGraph, names: Names) -> str:
    moved = next(node for node in candidate.nodes if str(node.id) == violation.node_id)
    where = f"would end at {_at(moved)}"
    if violation.blocker in (None, "Wall", "wall") or violation.kind == "left_the_floor":
        return f"{names.piece(moved)} {where}, which runs into a wall or off the floor."
    blocker = _nearest_named(candidate, moved, violation.blocker)
    if blocker is None:
        return f"{names.piece(moved)} {where}, which overlaps the {violation.blocker}."
    verb = "blocks the swing of" if violation.kind == "blocked_a_door" else "overlaps"
    return f"{names.piece(moved)} {where}, which {verb} {names.piece(blocker)} at {_at(blocker)}."


def _travel_note(violation, candidate: SceneGraph, room: SceneGraph, names: Names) -> str:
    moved = next(node for node in candidate.nodes if str(node.id) == violation.node_id)
    origin = measured_position(names.node(violation.node_id) or moved)
    travelled = math.hypot(moved.transform.position.x - origin.x, moved.transform.position.y - origin.y)
    return (f"{names.piece(moved)} would end {travelled:.2f} m from where the scan found it; "
            f"the limit is {MAX_TRAVEL_METERS:.2f} m.")


def _violation_note(violation, candidate: SceneGraph, room: SceneGraph, names: Names) -> str:
    if violation.kind in ("collided", "blocked_a_door", "left_the_floor"):
        return _collision_note(violation, candidate, names)
    if violation.kind == "moved_too_far":
        return _travel_note(violation, candidate, room, names)
    node = names.node(violation.node_id)
    piece = names.piece(node) if node else violation.node_id
    if violation.kind == "no_room_to_use":
        return f"{piece} would be left with no room for a person to pull up to it and use it."
    return f"{violation.kind.replace('_', ' ')}: {violation.detail} ({piece})."


def explain_constraints(completion: str, room: SceneGraph) -> tuple[str, tuple[str, ...]]:
    """The first broken constraint's kind, and every broken constraint in plain words."""
    candidate = apply_moves(room, node_moves(parse_edits(completion)))
    broken = violations(room, candidate)
    names = Names.of(room)
    notes = tuple(dict.fromkeys(_violation_note(item, candidate, room, names) for item in broken))
    return (broken[0].kind if broken else "hard_constraint"), notes


# --- the gate -------------------------------------------------------------------


def _finding_note(finding, names: Names) -> str:
    locus = finding.locus
    where = "" if locus is None or locus.point is None else f" at {[_r(locus.point.x), _r(locus.point.y)]}"
    involved = [] if locus is None else [names.node(node_id) for node_id in locus.node_ids]
    pieces = ", ".join(dict.fromkeys(names.piece(node) for node in involved if node is not None))
    measured = ("" if finding.measured_inches is None or finding.required_inches is None
                else f" ({finding.measured_inches:.1f} in where {finding.required_inches:.0f} in is needed)")
    return f"{finding.title}{where}{measured}" + (f", between {pieces}" if pieces else "")


def gate_category(reasons: list[str]) -> str:
    for needle, category in GATE_CATEGORIES:
        if any(needle in reason for reason in reasons):
            return category
    return "gate_other"


def _gate_notes(category: str, before, after, names: Names) -> list[str]:
    if category == "new_problem":
        old = {finding.id for finding in before.problems}
        return [f"New problem: {_finding_note(finding, names)}." for finding in after.problems if finding.id not in old]
    shortfall = f"total shortfall {total_shortfall(before):.1f} in before, {total_shortfall(after):.1f} in after"
    if category == "noise":
        return [f"The problems improved by less than {MIN_MEANINGFUL_SHORTFALL_INCHES:.0f} inch ({shortfall}), "
                "which is inside the measurement's noise; the change has to be larger."]
    if category == "nothing_changed":
        return [f"Nothing measurable changed: the problems are as short as before ({shortfall}). "
                "The moves did not touch what causes them."]
    return ["The layout stopped some checks from giving an answer, so it cannot be judged."]


def _gate_attempt(verdict: Verdict, completion: str, room: SceneGraph, checker: TrainingChecker) -> Attempt:
    candidate = apply_moves(room, node_moves(parse_edits(completion)))
    before, after = checker.assess(room), checker.assess(candidate)
    reasons = [part.strip() for part in verdict.reason.split(";") if part.strip()]
    category = gate_category(reasons)
    return Attempt(verdict, category, tuple(_gate_notes(category, before, after, Names.of(room))))


# --- everything else ------------------------------------------------------------


def _pinned_attempt(verdict: Verdict, completion: str, room: SceneGraph, checker: TrainingChecker) -> Attempt:
    names = Names.of(room)
    held = [names.node(move.node_id) for move in parse_edits(completion).moves if move.node_id in checker.pinned]
    notes = tuple(f"{node.label} ({node.id}) must stay where it is: the scan is not sure it is really there."
                  for node in held if node is not None)
    return Attempt(verdict, "pinned", notes)


def diagnose(completion: str, room: SceneGraph, checker: TrainingChecker) -> Attempt:
    """The training verdict, and when it is a refusal, the reasons in plain words."""
    verdict = score_completion(completion, room, checker)
    if verdict.gate_accepts:
        return Attempt(verdict, "accepted")
    if verdict.reason == "unparseable":
        return Attempt(verdict, "unparseable", ("The answer was not the JSON the format asks for.",))
    if verdict.reason == MOVED_PINNED:
        return _pinned_attempt(verdict, completion, room, checker)
    if verdict.reason in EDIT_COMPLAINTS:
        return Attempt(verdict, "bad_edit", (EDIT_COMPLAINTS[verdict.reason],))
    if not verdict.hard_constraints_pass:
        return Attempt(verdict, *explain_constraints(completion, room))
    return _gate_attempt(verdict, completion, room, checker)


def feedback_message(attempt: Attempt) -> str:
    lines = "\n".join(f"- {note}" for note in attempt.notes) or "- It was refused."
    return f"The application measured that proposal and refused it:\n{lines}\n{CLOSING}"


# --- open floor -----------------------------------------------------------------


def _inside_convex(polygon, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    points = np.asarray(polygon, dtype=float)
    signs = []
    for (ax, ay), (bx, by) in zip(points, np.roll(points, -1, axis=0)):
        signs.append((bx - ax) * (ys - ay) - (by - ay) * (xs - ax))
    stacked = np.stack(signs)
    return np.all(stacked >= 0, axis=0) | np.all(stacked <= 0, axis=0)


def _obstacles(room: SceneGraph) -> list:
    shapes = [footprint(node) for node in room.nodes
              if not lies_flat(node) and (node.kind == "wall" or (blocks_floor(node) and not bounds_the_room(node)))]
    return shapes + [door_keep_clear(node) for node in room.nodes if node.kind == "door"]


SAMPLE_OFFSETS = [(dx, dy) for dx in (-0.5, 0.0, 0.5) for dy in (-0.5, 0.0, 0.5)]
"""Corners, edge midpoints and centre of a cell, in cells: a cell an object only grazes is not free."""


def _covered(shape, xs: np.ndarray, ys: np.ndarray, cell: float) -> np.ndarray:
    hits = [_inside_convex(shape, xs + dx * cell, ys + dy * cell) for dx, dy in SAMPLE_OFFSETS]
    return np.any(np.stack(hits), axis=0)


def _all_inside(shape, xs: np.ndarray, ys: np.ndarray, cell: float) -> np.ndarray:
    hits = [_inside_convex(shape, xs + dx * cell, ys + dy * cell) for dx, dy in SAMPLE_OFFSETS]
    return np.all(np.stack(hits), axis=0)


def free_grid(room: SceneGraph, cell: float = CELL_METERS) -> tuple[np.ndarray, float, float] | None:
    """True for a cell wholly on the floor and touching nothing, with the grid's origin corner."""
    bounds = interior_bounds(room)
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    columns, rows = max(1, int((max_x - min_x) / cell)), max(1, int((max_y - min_y) / cell))
    xs, ys = np.meshgrid(min_x + (np.arange(columns) + 0.5) * cell, min_y + (np.arange(rows) + 0.5) * cell)
    free = np.ones((rows, columns), dtype=bool)
    floor = next((node for node in room.nodes if lies_flat(node)), None)
    if floor is not None:
        free &= _all_inside(floor_polygon(floor), xs, ys, cell)
    for shape in _obstacles(room):
        free &= ~_covered(shape, xs, ys, cell)
    return free, min_x, min_y


def _best_in_histogram(heights: np.ndarray) -> tuple[int, int, int, int]:
    """(area, left, right, height) of the largest rectangle under a histogram."""
    best, stack = (0, 0, 0, 0), []
    for index, height in enumerate([*heights.tolist(), 0]):
        start = index
        while stack and stack[-1][1] >= height:
            start, top = stack.pop()
            best = max(best, (top * (index - start), start, index, top))
        stack.append((start, height))
    return best


def largest_empty_rectangle(free: np.ndarray) -> tuple[int, int, int, int, int] | None:
    """(area, row_start, row_end, column_start, column_end) in cells, ends exclusive."""
    heights = np.zeros(free.shape[1], dtype=int)
    best = None
    for row in range(free.shape[0]):
        heights = np.where(free[row], heights + 1, 0)
        area, left, right, height = _best_in_histogram(heights)
        if area and (best is None or area > best[0]):
            best = (area, row + 1 - height, row + 1, left, right)
    return best


def open_floor_rectangles(room: SceneGraph, count: int = 15, cell: float = CELL_METERS) -> list[list[float]]:
    """Up to `count` disjoint empty floor rectangles, [x_min, y_min, x_max, y_max], largest first."""
    grid = free_grid(room, cell)
    if grid is None:
        return []
    free, min_x, min_y = grid
    free = free.copy()
    found = []
    while len(found) < count:
        best = largest_empty_rectangle(free)
        if best is None or best[0] * cell * cell < MIN_RECTANGLE_SQUARE_METERS:
            break
        _, top, bottom, left, right = best
        free[top:bottom, left:right] = False
        found.append([_r(min_x + left * cell), _r(min_y + top * cell), _r(min_x + right * cell),
                      _r(min_y + bottom * cell)])
    return found


def with_open_floor(messages: list[dict], room: SceneGraph, count: int = 15) -> list[dict]:
    """The same prompt with the open floor rectangles added to the room description."""
    view = json.loads(messages[-1]["content"])
    view["open_floor_note"] = "empty floor right now, as [x_min,y_min,x_max,y_max], largest first"
    view["open_floor"] = open_floor_rectangles(room, count)
    return [*messages[:-1], {"role": "user", "content": json.dumps(view, separators=(",", ":"))}]


# --- the loop -------------------------------------------------------------------


@dataclass
class Chain:
    variant: str
    messages: list[dict]
    rounds: list[dict] = field(default_factory=list)
    accepted_at: int | None = None

    @property
    def open(self) -> bool:
        return self.accepted_at is None


Propose = Callable[[list[list[dict]]], list[str]]
Judge = Callable[[str, str], Attempt]


def attempt_record(round_index: int, completion: str, attempt: Attempt, feedback: str | None) -> dict:
    return {"round": round_index, "completion": completion, "category": attempt.category,
            "notes": list(attempt.notes), "feedback": feedback, **attempt.verdict.as_dict()}


def run_chains(chains: list[Chain], propose: Propose, judge: Judge, rounds: int = 4,
               after_round: Callable[[int], None] | None = None) -> list[Chain]:
    """Up to `rounds` proposals per room, each refusal answered with the checker's reasons."""
    for round_index in range(1, rounds + 1):
        active = [chain for chain in chains if chain.open]
        if not active:
            break
        completions = propose([chain.messages for chain in active])
        for chain, completion in zip(active, completions):
            _advance(chain, round_index, completion, judge(completion, chain.variant), last=round_index == rounds)
        if after_round:
            after_round(round_index)
    return chains


def _advance(chain: Chain, round_index: int, completion: str, attempt: Attempt, last: bool) -> None:
    if attempt.accepted:
        chain.accepted_at = round_index
        chain.rounds.append(attempt_record(round_index, completion, attempt, None))
        return
    feedback = None if last else feedback_message(attempt)
    chain.rounds.append(attempt_record(round_index, completion, attempt, feedback))
    if feedback:
        chain.messages = [*chain.messages, {"role": "assistant", "content": completion},
                          {"role": "user", "content": feedback}]
