"""Reward for a proposed rearrangement, decided by the measured checker.

Zero for anything the application would refuse: an answer that does not
parse, edits that move a piece the phantom filter holds still, edits that name
the wrong furniture, a layout that breaks a hard constraint, one an ADA layout
directive for the room's space type refuses, or one the gate rejects. An accepted partial fix earns at most 0.55; a fix clearing every
fixable finding earns at least 0.60. Recovery helps rank partial fixes, while
usability, the owner's wishes (`WISH_WEIGHT`) and wall placement (`WALL_PLACEMENT_WEIGHT`) break ties within each tier. This makes a complete fix
the training objective without paying for rejected layouts. A fix that needs
construction (a wall shift or a relocated fixture) pays a little less per inch moved, so a furniture-only
fix of the same room always ranks above it.

The training checker treats uncertain scan geometry as measured. Its all-clear
verdict is therefore a training result, not physical verification. Q
(`quality.layout_quality`) is measured and logged on the Verdict as a whole,
but only its wall term is paid: an audit of 299 training rooms found the
top-paid menu choice in about 24 of them left a moved piece diagonal or
crosswise in open floor (a display case turned 30 or 90 degrees off its
wall), still earning 0.955, because Q never reached the reward at all. The
wall term needs only the moved piece and its nearest wall segment, so it is
cheap to trust; the pair and sight terms still guess which chair belongs to
which table and cast rays from a counter that may not exist, so they stay
logged, not paid, until they earn the same confidence. `WALL_PLACEMENT_WEIGHT`
is funded by taking the same amount out of `ALL_CLEAR_FLOOR` and out of
`RECOVERY_WEIGHT`, so a fix that keeps every wall relation (`wall` == 1) scores
exactly what it did before this change; a fix that turns a piece away from its
wall now loses up to `WALL_PLACEMENT_WEIGHT` of reward, in whichever tier it
falls into.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from standardphysics_contracts import SceneGraph

from ..evaluation.gate import accepts
from ..fix import CandidateRejection, Violation, apply_moves, describe, relocation_violations, violations
from ..fix.strategies import TURN_DISRUPTION_METERS
from .checker import TrainingChecker
from .construction import build, construction_inches
from .edits import TrainingEdits, edit_complaint, node_moves, parse_edits
from .quality import layout_quality
from .usability import usability

ACCEPTED_FLOOR = 0.05
RECOVERY_WEIGHT = 0.25
ALL_CLEAR_FLOOR = 0.75
USABILITY_WEIGHT = 0.10
WISH_WEIGHT = 0.10
"""Share of the reward for keeping the owner's layout: of the wishes it showed (seats at tables, pieces
against walls, the counter's view) that the room still kept, the share the fix keeps. The model never sees
the owner's layout, so earning this means reading the owner's choices from where things stand."""
WALL_PLACEMENT_WEIGHT = 0.05
"""Share of the reward for how the pieces the answer actually moved sit against their nearest wall,
angle and distance compared with the owner's own layout (`quality.wall_term`). Pays for the same
signal `layout_quality` already logs as `quality["wall"]`, so a display case turned diagonal into
open floor earns less credit even when the fix clears every finding. Funded by taking 0.05 out of
`ALL_CLEAR_FLOOR` and 0.05 out of `RECOVERY_WEIGHT`, so keeping every wall relation (`wall` == 1)
reproduces the reward this module paid before wall placement was priced in."""
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
    """Q and its wall, pairs and sight terms, logged for gate-accepted layouts. Only `quality["wall"]`
    (also `wall`, below) is paid; pairs and sight are not part of the reward."""
    usability: float | None = None
    """U, for gate-accepted layouts."""
    construction_inches: float = 0.0
    """Total wall shift the edits ask for; zero for a furniture-only answer."""
    wishes_kept: float | None = None
    """The share of the owner's wishes the fix keeps, for gate-accepted layouts."""
    wall: float | None = None
    """How the moved pieces sit against their nearest wall versus the owner's layout (`quality["wall"]`),
    for gate-accepted layouts. Paid via `WALL_PLACEMENT_WEIGHT`."""

    def as_dict(self) -> dict:
        return asdict(self)


def disruption_meters(moves) -> float:
    slid = sum(math.hypot(move.delta_translation.x, move.delta_translation.y) for move in moves)
    turns = sum(1 for move in moves if abs(move.delta_rotation_z_degrees) >= TURN_THRESHOLD_DEGREES)
    return slid + turns * TURN_DISRUPTION_METERS


def shaped_reward(recovered: float, all_clear: bool, disruption: float, usable: float = 1.0,
                  construction: float = 0.0, wishes: float = 1.0, wall: float = 1.0) -> float:
    penalty = min(MAX_DISRUPTION_PENALTY, DISRUPTION_PENALTY_PER_METER * disruption)
    penalty += min(MAX_CONSTRUCTION_PENALTY, CONSTRUCTION_PENALTY_PER_INCH * construction)
    credit = (USABILITY_WEIGHT * max(0.0, min(1.0, usable)) + WISH_WEIGHT * max(0.0, min(1.0, wishes))
             + WALL_PLACEMENT_WEIGHT * max(0.0, min(1.0, wall)))
    earned = (ALL_CLEAR_FLOOR if all_clear else ACCEPTED_FLOOR + RECOVERY_WEIGHT * max(0.0, min(1.0, recovered)))
    earned += credit
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
        legality = constrained(room, edits, checker.directive_veto(room))
    except ValueError:
        return Verdict(0.0, parsed=True, reason="unbuildable_construction")
    if legality.refusal:
        return Verdict(0.0, parsed=True, hard_constraints_pass=not legality.broken, reason=legality.refusal)
    return _gated(room, legality.candidate, checker, disruption_meters(node_moves(edits)),
                  construction_inches(edits.wall_shifts, edits.fixture_moves))


@dataclass(frozen=True)
class Legality:
    """The room some edits make, and everything that would make the application refuse it."""

    candidate: SceneGraph
    broken: list[Violation]
    vetoed: str | None = None
    """Why an ADA layout directive refuses the room; asked only once no hard constraint is broken."""

    @property
    def refusal(self) -> str | None:
        if self.broken:
            return "; ".join(sorted({describe(item) for item in self.broken}))
        return self.vetoed


def constrained(room: SceneGraph, edits: TrainingEdits, veto: CandidateRejection | None = None) -> Legality:
    """The room the edits make, what it breaks and any directive veto; raises ValueError if unbuildable.

    This is the one legality test: the scorer refuses what it finds, and the
    menu of moves offers nothing it finds. `veto` is the room's directive
    refusal (`TrainingChecker.directive_veto`), judged against the room before
    any edit, so a moved built-in counter counts as moved.
    """
    built = build(room, edits.wall_shifts, edits.fixture_moves)
    candidate = apply_moves(built, node_moves(edits))
    relocated = {move.node_id for move in edits.fixture_moves}
    broken = [*violations(built, candidate), *relocation_violations(room, candidate, relocated)]
    return Legality(candidate, broken, veto(room, candidate) if veto and not broken else None)


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
    wishes = round(checker.owner_wishes.kept_share(room, candidate, checker.measure), 4)
    return Verdict(
        shaped_reward(recovered, left == 0, disruption, usable, construction, wishes, quality.wall), parsed=True,
        hard_constraints_pass=True, gate_accepts=True, shortfall_recovered=recovered, fixable_left=left,
        disruption_meters=disruption, quality=quality.as_dict(), usability=usable, construction_inches=construction,
        wishes_kept=wishes, wall=quality.wall,
    )
