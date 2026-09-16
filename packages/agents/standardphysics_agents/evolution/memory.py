"""Every case the router has been scored on, kept across runs.

Reflection reads the failures in front of it and the past failures most like
them, so a pattern that only shows up across several runs can still become a
lesson. The record holds what the router saw and chose, never the geometry.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from ..checks.roles import room_kind
from ..evaluation.runner import EvaluationResult, action_name
from ..evaluation.scorers import CaseOutcome

DEFAULT_MEMORY_PATH = Path("runs/experience.jsonl")

CONTROL_SCORES = ("router_action_match", "trajectory_ok")
"""The scores a lesson can move. Both are about which action was chosen."""


@dataclass(frozen=True)
class Experience:
    case_id: str
    room_kind: str
    problems: tuple[str, ...]
    actions_taken: tuple[str, ...]
    chose: str | None
    expected: str | None
    scores: dict[str, float | None]
    playbook_version: int

    @property
    def failed(self) -> bool:
        return any(self.scores.get(name) == 0.0 for name in CONTROL_SCORES)

    def brief(self) -> dict:
        """What reflection is shown: the situation and the choice, not the scores' plumbing."""
        return {
            "case": self.case_id,
            "room_kind": self.room_kind,
            "problems": list(self.problems),
            "actions_taken": list(self.actions_taken),
            "chose": self.chose,
            "expected": self.expected,
        }


def experiences(result: EvaluationResult, playbook_version: int) -> list[Experience]:
    return [
        _experience(outcome, result.per_case[outcome.case.id], playbook_version)
        for outcome in result.outcomes
    ]


def _experience(outcome: CaseOutcome, scores: dict, playbook_version: int) -> Experience:
    return Experience(
        case_id=outcome.case.id,
        room_kind=room_kind(outcome.case.graph),
        problems=tuple(sorted(outcome.reported_problems)),
        actions_taken=tuple(outcome.case.actions_taken),
        chose=action_name(outcome),
        expected=outcome.case.expected_action,
        scores=dict(scores),
        playbook_version=playbook_version,
    )


def remember(items: Iterable[Experience], path: Path = DEFAULT_MEMORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(asdict(item)) + "\n")


def load_memory(path: Path = DEFAULT_MEMORY_PATH) -> list[Experience]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [_from_json(json.loads(line)) for line in lines if line.strip()]


def _from_json(data: dict) -> Experience:
    return Experience(
        case_id=data["case_id"],
        room_kind=data["room_kind"],
        problems=tuple(data["problems"]),
        actions_taken=tuple(data["actions_taken"]),
        chose=data["chose"],
        expected=data["expected"],
        scores=data["scores"],
        playbook_version=data["playbook_version"],
    )


def recall(memory: list[Experience], like: list[Experience], limit: int = 5) -> list[Experience]:
    """Past failures most like the current ones: the same kind of room first,
    then the most problems in common."""
    kinds = {item.room_kind for item in like}
    problems = {problem for item in like for problem in item.problems}
    past = [item for item in memory if item.failed]
    ranked = sorted(
        past, key=lambda item: (item.room_kind not in kinds, -len(problems & set(item.problems)))
    )
    return ranked[:limit]
