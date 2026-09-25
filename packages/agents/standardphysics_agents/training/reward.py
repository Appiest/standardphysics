"""Reward for a proposed rearrangement, decided by the measured checker after snapping.

The model's moves are requests. `snap.snap` places each one at the nearest spot
the room allows, so the layout that gets scored never breaks a hard constraint;
the reward then asks whether that layout is better and still usable.

Zero for:
    an answer that does not parse, or names unknown, duplicate or fixed pieces,
    or a piece the phantom filter holds still
    a request the solver could place none of
    a layout an ADA layout directive for this space type refuses
    a layout the gate rejects: a new failure, or nothing measurable improved
    a layout that loses usefulness against the room it started from: fewer seats
    facing what they serve, fewer wall pieces with a clear front, or less of the
    accessible dining share (`usefulness.Usefulness.worse_than`)

An accepted layout earns

    clamp(0.15 + 0.45 * recovered + 0.10 * all_clear + 0.15 * U + 0.15 * F
          - 0.03 * moved - 0.10 * snapped, 0.05, 1)

    U  `usability.usability`: usable sides and seats of every table, desk and
       counter the layout affected, against the owner's layout
    F  `usefulness.Usefulness.score` after the edits
    moved    metres slid plus a fixed cost per turn, measured on the snapped layout
             rather than the request, and not capped: more moving always costs more
    snapped  mean metres the solver had to shift requests to make them legal, so
             the model still learns to ask for places that work

Q (`quality.layout_quality`) is measured and logged on every accepted layout and
not paid for: it agreed with a person's choice in 12 of 24 rated pairs. The
checker that pays the reward also grades held-out rooms, so held-out reports
also carry Q and should be read with the person-rated pairs, not alone.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from standardphysics_contracts import SceneGraph

from ..evaluation.gate import accepts
from ..fix import violations
from ..fix.strategies import TURN_DISRUPTION_METERS
from ..snap import Snapped, snap
from .checker import TrainingChecker
from .edits import edit_complaint, edits_between, node_moves, parse_edits
from .quality import layout_quality
from .usability import usability
from .usefulness import usefulness

ACCEPTED_FLOOR = 0.15
RECOVERY_WEIGHT = 0.45
ALL_CLEAR_BONUS = 0.10
USABILITY_WEIGHT = 0.15
USEFULNESS_WEIGHT = 0.15
MOVED_PINNED = "moved_unconfirmed_object"
NOTHING_PLACED = "no_legal_spot_for_any_move"
DISRUPTION_PENALTY_PER_METER = 0.03
SNAP_PENALTY_PER_METER = 0.10
MIN_ACCEPTED_REWARD = 0.05
TURN_THRESHOLD_DEGREES = 1.0


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
    usefulness: dict | None = None
    """F and its three shares, for layouts the gate accepted."""
    snapped_meters: float = 0.0
    """Mean distance the solver moved requests to make them legal."""
    unplaced: int = 0
    """Requested moves the solver found no legal spot for."""

    def as_dict(self) -> dict:
        return asdict(self)


def disruption_meters(moves) -> float:
    slid = sum(math.hypot(move.delta_translation.x, move.delta_translation.y) for move in moves)
    turns = sum(1 for move in moves if abs(move.delta_rotation_z_degrees) >= TURN_THRESHOLD_DEGREES)
    return slid + turns * TURN_DISRUPTION_METERS


def shaped_reward(recovered: float, all_clear: bool, disruption: float, usable: float = 1.0,
                  useful: float = 1.0, snapped: float = 0.0) -> float:
    penalty = DISRUPTION_PENALTY_PER_METER * disruption + SNAP_PENALTY_PER_METER * snapped
    earned = (ACCEPTED_FLOOR + RECOVERY_WEIGHT * recovered + (ALL_CLEAR_BONUS if all_clear else 0.0)
              + USABILITY_WEIGHT * _unit(usable) + USEFULNESS_WEIGHT * _unit(useful))
    return round(max(MIN_ACCEPTED_REWARD, min(1.0, earned - penalty)), 6)


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _recovered(before: float, after: float) -> float:
    if before <= 0:
        return 0.0
    return max(0.0, min(1.0, (before - after) / before))


def _request_complaint(edits, room: SceneGraph, checker: TrainingChecker) -> str | None:
    if any(move.node_id in checker.pinned for move in edits.moves):
        return MOVED_PINNED
    return edit_complaint(room, edits)


def score_completion(completion: str, room: SceneGraph, checker: TrainingChecker) -> Verdict:
    edits = parse_edits(completion)
    if edits is None:
        return Verdict(0.0, reason="unparseable")
    complaint = _request_complaint(edits, room, checker)
    if complaint:
        return Verdict(0.0, parsed=True, reason=complaint)
    snapped = snap(room, node_moves(edits), checker.directive_rejection(room))
    placed = len(snapped.placements) - len(snapped.unplaced)
    if not placed:
        return Verdict(0.0, parsed=True, reason=NOTHING_PLACED, unplaced=len(snapped.unplaced))
    broken = violations(room, snapped.graph)
    if broken:
        return Verdict(0.0, parsed=True, reason=",".join(sorted({item.kind for item in broken})))
    if snapped.refused:
        return Verdict(0.0, parsed=True, hard_constraints_pass=True, reason=snapped.refused)
    moved = disruption_meters(node_moves(edits_between(room, snapped.graph)))
    return _gated(room, snapped, checker, moved)


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
        "mean_usefulness_accepted": _mean_of([v.usefulness["score"] for v in verdicts
                                              if v.gate_accepts and v.usefulness]),
        "mean_snapped_meters": round(sum(v.snapped_meters for v in verdicts) / count, 4),
        "refused_by_directive": share_reason(verdicts, "precedent_violation"),
        "refused_as_less_useful": share_reason(verdicts, "less_useful"),
    }


def share_reason(verdicts: list[Verdict], prefix: str) -> float:
    return round(sum(1 for v in verdicts if v.reason.startswith(prefix)) / len(verdicts), 4)


def _mean_of(values: list) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _gated(room: SceneGraph, snapped: Snapped, checker: TrainingChecker, disruption: float) -> Verdict:
    candidate = snapped.graph
    before, after = checker.assess(room), checker.assess(candidate)
    gate = accepts(before, after)
    recovered = _recovered(gate.shortfall_before, gate.shortfall_after)
    left = len(checker.fixable_problems(after))
    common = {"parsed": True, "hard_constraints_pass": True, "shortfall_recovered": recovered, "fixable_left": left,
              "disruption_meters": disruption, "snapped_meters": snapped.mean_snap_meters,
              "unplaced": len(snapped.unplaced)}
    if not gate:
        return Verdict(0.0, reason="; ".join(gate.reasons), **common)
    useful_before, useful_after = usefulness(room), usefulness(candidate)
    lost = useful_after.worse_than(useful_before)
    if lost:
        return Verdict(0.0, reason="less_useful:" + ",".join(lost), usefulness=useful_after.as_dict(), **common)
    owner = checker.owner_layout or room
    quality = layout_quality(room, candidate, owner, checker.measure)
    usable = usability(room, candidate, owner, checker.scenario)
    reward = shaped_reward(recovered, left == 0, disruption, usable, useful_after.score, snapped.mean_snap_meters)
    return Verdict(reward, gate_accepts=True, quality=quality.as_dict(), usability=usable,
                   usefulness=useful_after.as_dict(), **common)
