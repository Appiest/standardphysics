"""Measured feedback for the next furniture-fix attempt."""

from __future__ import annotations

import json

from standardphysics_contracts import SceneGraph

from .checker import TrainingChecker
from .prompt import room_view


def feedback_message(
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
        "room": room_view(graph, checker.scenario, problems),
    }
    return {"role": "user", "content": json.dumps(feedback, separators=(",", ":"))}
