"""Checker feedback for rejected rearrangement proposals.

A rejected proposal is never applied, so every round starts from the room as
first described. `diagnose` scores a completion exactly as training did
(`reward.judge`, which snaps each request to the nearest legal spot) and,
when the room is refused, says why in terms a model can act on: which piece hit which, where a new problem appeared, or that
nothing measurable changed.

`measured_feedback_message` is the JSON form used by the correction training
rows: the checker's verdict fields plus the room as the prompt describes it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from standardphysics_contracts import SceneGraph, SceneNode, bounds_the_room
from standardphysics_pipeline.footprints import footprint, gap_between

from standardphysics_agents.evaluation.gate import MIN_MEANINGFUL_SHORTFALL_INCHES, total_shortfall
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.constraints import MAX_TRAVEL_METERS
from standardphysics_agents.fix.moves import measured_position

from .checker import TrainingChecker
from .edits import node_moves, parse_edits
from .prompt import room_view
from .snapped_reward import MOVED_PINNED, Verdict, judge

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
    layout: SceneGraph | None = None
    """The snapped layout the verdict scored, when it was accepted."""

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
    failure = violation.kind
    if violation.blocker in (None, "Wall", "wall") or failure == "left_the_floor":
        return f"{names.piece(moved)} {where}, which runs into a wall or off the floor."
    blocker = _nearest_named(candidate, moved, violation.blocker)
    if blocker is None:
        return f"{names.piece(moved)} {where}, which overlaps the {violation.blocker}."
    verb = "blocks the swing of" if failure == "blocked_a_door" else "overlaps"
    return f"{names.piece(moved)} {where}, which {verb} {names.piece(blocker)} at {_at(blocker)}."


def _travel_note(violation, candidate: SceneGraph, room: SceneGraph, names: Names) -> str:
    moved = next(node for node in candidate.nodes if str(node.id) == violation.node_id)
    origin = measured_position(names.node(violation.node_id) or moved)
    travelled = math.hypot(moved.transform.position.x - origin.x, moved.transform.position.y - origin.y)
    return (f"{names.piece(moved)} would end {travelled:.2f} m from where the scan found it; "
            f"the limit is {MAX_TRAVEL_METERS:.2f} m.")


def _violation_note(violation, candidate: SceneGraph, room: SceneGraph, names: Names) -> str:
    failure = violation.kind
    if failure in ("collided", "blocked_a_door", "left_the_floor"):
        return _collision_note(violation, candidate, names)
    if failure == "moved_too_far":
        return _travel_note(violation, candidate, room, names)
    node = names.node(violation.node_id)
    piece = names.piece(node) if node else violation.node_id
    if failure == "no_room_to_use":
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
    judged = judge(completion, room, checker)
    verdict = judged.verdict
    if verdict.gate_accepts:
        return Attempt(verdict, "accepted", layout=judged.layout)
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


def measured_feedback_message(
    graph: SceneGraph,
    checker: TrainingChecker,
    *,
    accepted: bool,
    reason: str,
    fixable_left: int | None,
    parsed: bool,
    hard_constraints_pass: bool,
    step_usability: float | None,
    candidate_baseline_usability: float | None,
    current_baseline_usability: float,
) -> dict:
    """Tell the model why its last proposal was refused or what remains to fix."""
    problems = checker.fixable_problems(checker.assess(graph))
    feedback = {
        "checker_feedback": {
            "accepted": accepted,
            "reason": reason,
            "fixable_left": fixable_left,
            "parsed": parsed,
            "hard_constraints_pass": hard_constraints_pass,
            "step_usability": step_usability,
            "candidate_baseline_usability": candidate_baseline_usability,
            "current_baseline_usability": current_baseline_usability,
        },
        "room": room_view(graph, checker.scenario, problems, checker.scope),
    }
    return {"role": "user", "content": json.dumps(feedback, separators=(",", ":"))}
