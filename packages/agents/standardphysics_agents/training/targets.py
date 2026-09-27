"""Supervised targets from the deterministic search.

`propose_fix` clears one finding per call. Calling it again on its own result,
a few rounds, clears as many as it can; the edits between the variant and the
last layout are the target. The target is then applied from scratch, rounded
the way a model would write it, and must pass the same constraints and gate
that score a model's answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from standardphysics_contracts import SceneGraph

from ..fix import propose_fix
from ..redesign import RoomEdits
from .checker import TrainingChecker
from .edits import edits_between, edits_json
from .reward import Verdict, score_completion

MAX_ROUNDS = 4


@dataclass(frozen=True)
class SearchTarget:
    edits: RoomEdits
    verdict: Verdict

    @property
    def completion(self) -> str:
        return edits_json(self.edits)


def _one_round(layout: SceneGraph, checker: TrainingChecker) -> SceneGraph | None:
    current = checker.assess(layout)
    fixable = checker.rearrangeable_problems(current)
    if not fixable:
        return None
    outcome = propose_fix(
        layout, checker.scenario, checker.measure, fixable, rules=checker.rules, ledger=checker.ledger,
        baseline=current, max_tier=checker.max_tier, offer_relaxation=False,
    )
    return outcome.graph if outcome.found else None


def searched_layout(room: SceneGraph, checker: TrainingChecker, rounds: int = MAX_ROUNDS) -> SceneGraph:
    layout = room
    for _ in range(rounds):
        improved = _one_round(layout, checker)
        if improved is None:
            break
        layout = improved
    return layout


def search_target(room: SceneGraph, checker: TrainingChecker) -> SearchTarget | None:
    """The search's rearrangement as a model answer, or None when the search cannot improve the room."""
    edits = edits_between(room, searched_layout(room, checker))
    if not edits.moves:
        return None
    verdict = score_completion(edits_json(edits), room, checker)
    return SearchTarget(edits, verdict) if verdict.gate_accepts else None
