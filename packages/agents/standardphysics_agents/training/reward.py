"""Reward for a proposed rearrangement, decided by the measured checker.

Zero for anything the application would refuse: an answer that does not
parse, edits that move a piece the phantom filter holds still, edits that name
the wrong furniture, a layout that breaks a hard constraint, or one the gate
rejects. An accepted partial fix earns at most 0.55; a fix clearing every
fixable finding earns at least 0.65. Recovery helps rank partial fixes, while
usability and movement break ties within each tier. This makes a complete fix
the training objective without paying for rejected layouts. A fix that needs
construction (a wall shift or a relocated fixture) pays a little less per inch moved, so a furniture-only
fix of the same room always ranks above it.

The training checker treats uncertain scan geometry as measured. Its all-clear
verdict is therefore a training result, not physical verification. Q
(`quality.layout_quality`) is measured and logged but not paid for.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from standardphysics_contracts import SceneGraph

from ..evaluation.gate import accepts
from ..fix import Violation, apply_moves, describe, relocation_violations, violations
from ..fix.strategies import TURN_DISRUPTION_METERS
from .checker import TrainingChecker
from .construction import build, construction_inches
from .edits import TrainingEdits, edit_complaint, node_moves, parse_edits
from .quality import layout_quality
from .usability import usability

ACCEPTED_FLOOR = 0.05
RECOVERY_WEIGHT = 0.30
ALL_CLEAR_FLOOR = 0.80
USABILITY_WEIGHT = 0.20
MOVED_PINNED = "moved_unconfirmed_object"
DISRUPTION_PENALTY_PER_METER = 0.03
MAX_DISRUPTION_PENALTY = 0.15
MIN_ACCEPTED_REWARD = 0.05
TURN_THRESHOLD_DEGREES = 1.0
CONSTRUCTION_PENALTY_PER_INCH = 0.01
MAX_CONSTRUCTION_PENALTY = 0.12


@dataclass(frozen=True)
class Verdict:
    reward: float
    parsed: bool = False
    hard_constraints_pass: bool = False
    gate_accepts: bool = False
    shortfall_recovered: float = 0.0
    fixable_left: int | None = None
    disruption_meters: float = 0.0
    reason: str = ""
    quality: dict | None = None
    """Q and its wall, pairs and sight terms, logged for gate-accepted layouts; not part of the reward."""
    usability: float | None = None
    """U, for gate-accepted layouts."""
    construction_inches: float = 0.0
    """Total wall shift the edits ask for; zero for a furniture-only answer."""

    def as_dict(self) -> dict:
        return asdict(self)


def disruption_meters(moves) -> float:
    slid = sum(math.hypot(move.delta_translation.x, move.delta_translation.y) for move in moves)
    turns = sum(1 for move in moves if abs(move.delta_rotation_z_degrees) >= TURN_THRESHOLD_DEGREES)
    return slid + turns * TURN_DISRUPTION_METERS


def shaped_reward(recovered: float, all_clear: bool, disruption: float, usable: float = 1.0,
                  construction: float = 0.0) -> float:
    penalty = min(MAX_DISRUPTION_PENALTY, DISRUPTION_PENALTY_PER_METER * disruption)
    penalty += min(MAX_CONSTRUCTION_PENALTY, CONSTRUCTION_PENALTY_PER_INCH * construction)
    usability_credit = USABILITY_WEIGHT * max(0.0, min(1.0, usable))
    earned = (ALL_CLEAR_FLOOR if all_clear else ACCEPTED_FLOOR + RECOVERY_WEIGHT * max(0.0, min(1.0, recovered)))
    earned += usability_credit
    return round(max(MIN_ACCEPTED_REWARD, min(1.0, earned - penalty)), 6)


def _recovered(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, min(1.0, (before - after) / before))


def score_completion(completion: str, room: SceneGraph, checker: TrainingChecker) -> Verdict:
    edits = parse_edits(completion)
    if edits is None:
        return Verdict(0.0, reason="unparseable")
    if any(move.node_id in checker.pinned for move in [*edits.moves, *edits.fixture_moves]):
        return Verdict(0.0, parsed=True, reason=MOVED_PINNED)
    complaint = edit_complaint(room, edits)
    if complaint:
        return Verdict(0.0, parsed=True, reason=complaint)
    try:
        candidate, broken = constrained(room, edits)
    except ValueError:
        return Verdict(0.0, parsed=True, reason="unbuildable_construction")
    if broken:
        return Verdict(0.0, parsed=True, reason="; ".join(sorted({describe(item) for item in broken})))
    return _gated(room, candidate, checker, disruption_meters(node_moves(edits)),
                  construction_inches(edits.wall_shifts, edits.fixture_moves))


def constrained(room: SceneGraph, edits: TrainingEdits) -> tuple[SceneGraph, list[Violation]]:
    """The room the edits make, and every hard constraint it breaks; raises ValueError for unbuildable construction.

    This is the one legality test: the scorer refuses what it finds, and the
    menu of moves offers nothing it finds.
    """
    built = build(room, edits.wall_shifts, edits.fixture_moves)
    candidate = apply_moves(built, node_moves(edits))
    relocated = {move.node_id for move in edits.fixture_moves}
    return candidate, [*violations(built, candidate), *relocation_violations(room, candidate, relocated)]


def summarize(verdicts: list[Verdict]) -> dict:
    """The eval report: parse rate, hard-constraint pass rate, gate acceptance, mean reward and recovery."""
    count = len(verdicts)
    if not count:
        return {"samples": 0}

    def share(flag: str) -> float:
        return round(sum(1 for verdict in verdicts if getattr(verdict, flag)) / count, 4)

    return {
        "samples": count,
        "parse_rate": share("parsed"),
        "hard_constraint_pass_rate": share("hard_constraints_pass"),
        "gate_acceptance": share("gate_accepts"),
        "all_fixable_cleared_rate": round(sum(1 for v in verdicts if v.gate_accepts and v.fixable_left == 0) / count, 4),
        "mean_reward": round(sum(verdict.reward for verdict in verdicts) / count, 4),
        "mean_shortfall_recovered": round(
            sum(verdict.shortfall_recovered for verdict in verdicts if verdict.gate_accepts) / count, 4
        ),
        "mean_usability_accepted": _mean_of([v.usability for v in verdicts if v.gate_accepts]),
        "mean_quality_accepted": _mean_of([v.quality["q"] for v in verdicts if v.gate_accepts and v.quality]),
    }


def _mean_of(values: list) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _gated(room: SceneGraph, candidate: SceneGraph, checker: TrainingChecker, disruption: float,
           construction: float = 0.0) -> Verdict:
    before, after = checker.assess(room), checker.assess(candidate)
    gate = accepts(before, after)
    recovered = _recovered(gate.shortfall_before, gate.shortfall_after)
    left = len(checker.fixable_problems(after))
    if not gate:
        return Verdict(0.0, parsed=True, hard_constraints_pass=True, shortfall_recovered=recovered,
                       fixable_left=left, disruption_meters=disruption, reason="; ".join(gate.reasons),
                       construction_inches=construction)
    owner = checker.owner_layout or room
    quality = layout_quality(room, candidate, owner, checker.measure)
    usable = usability(room, candidate, owner, checker.scenario)
    return Verdict(
        shaped_reward(recovered, left == 0, disruption, usable, construction), parsed=True, hard_constraints_pass=True,
        gate_accepts=True, shortfall_recovered=recovered, fixable_left=left, disruption_meters=disruption,
        quality=quality.as_dict(), usability=usable, construction_inches=construction,
    )
