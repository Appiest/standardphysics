"""Guesses that pair a move the gate refused for a problem it brought with a move that clears that problem.

Putting a piece the owner dragged back toward where the scan found it, or
turning it, can open the pinch it made and at once bring back a pinch it made
somewhere else. The gate refuses such a move on its own, since a new problem
appeared. What a person would do is make the move and then clear what it
brought, so this pairs each such move with furniture guesses for the new
problem, measured in the room the move leaves. On a living room scan with its
sofa dragged toward the door, and on a production scan with a sofa dragged
into the middle, no single move was offered and a pair cleared the route.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from standardphysics_contracts import Finding, SceneGraph

from ..assess import Pass
from ..evaluation.gate import GateResult
from .checker import TrainingChecker
from .edits import combined
from .menu_words import _Guess
from .reward import touched

FOLLOW_UP_FIRSTS = 2
"""How many refused moves are each tried with a move for the problem they brought."""
FOLLOW_UP_SECONDS = 4
"""How many moves for that problem each is tried with."""

_FOR_PROBLEM = re.compile(r",? for P\d+$")


@dataclass(frozen=True)
class Tried:
    """A legal guess the menu measured, and what the checker and the gate made of it."""

    guess: _Guess
    candidate: SceneGraph
    after: Pass
    verdict: GateResult


def _bare(wording: str) -> str:
    return _FOR_PROBLEM.sub("", wording)


def _then(first: _Guess, second: _Guess, label: str) -> _Guess:
    return _Guess(combined(first.edits, second.edits), f"{_bare(first.wording)}; {_bare(second.wording)}, for {label}")


def _refused_only_for_new_problems(verdict: GateResult) -> bool:
    return not verdict.accepted and all(reason.startswith(("new problem", "more problems")) for reason in verdict.reasons)


def follow_up_guesses(tried: list[Tried], before: Pass, checker: TrainingChecker, label: str,
                      furniture: Callable[[SceneGraph, Finding, TrainingChecker, str], list[_Guess]]) -> list[_Guess]:
    """Each move refused only for a problem it brought, together with a furniture guess for that problem."""
    known = {finding.id for finding in before.problems}
    found: list[_Guess] = []
    for entry in [entry for entry in tried if _refused_only_for_new_problems(entry.verdict)][:FOLLOW_UP_FIRSTS]:
        brought = next((finding for finding in checker.fixable_problems(entry.after) if finding.id not in known), None)
        if brought is None:
            continue
        seconds = [second for second in furniture(entry.candidate, brought, checker, label)
                   if not touched(second.edits) & touched(entry.guess.edits)]
        found.extend(_then(entry.guess, second, label) for second in seconds[:FOLLOW_UP_SECONDS])
    return found
