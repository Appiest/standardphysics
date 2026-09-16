"""Reading what went wrong and proposing one lesson.

Astra, through OpenRouter, is shown the failed cases, similar past failures and
the lessons already in the playbook, and writes one sentence about which action
to choose and when. When Astra is unavailable or its lesson is refused, a local
rule writes one from the commonest wrong turn and labels it as local, the same
way the local policy is labelled.

A lesson that talks about thresholds, inches or unlocking furniture is refused
outright. The router has no say over any of them, and a lesson that pretends
otherwise would be teaching it to argue with the measurements.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from ..models import OpenRouter
from ..router.decision import ACTIONS, Rejected
from ..router.typesafe import ACTION_CRITERIA
from ..tracing import traced
from .memory import Experience
from .playbook import MAX_LESSON_CHARS, Lesson, Playbook

ROOM_KINDS = ("service", "home", "general")

REFLECTION_INSTRUCTION = (
    "You improve the playbook of a router that picks the next step of an "
    "accessibility review. You are shown cases where its choice was wrong or "
    "repeated work, similar failures from earlier runs, and the lessons it "
    "already has. Write one new lesson, a single sentence under 240 characters, "
    "that would have led it to the expected action. Say which action to choose "
    "and when, using the state fields it sees: actions_taken, room_kind, "
    "furniture_can_fix, wants_another_look. Never mention measurements, "
    "thresholds, inches, rules or unlocking furniture."
)

LESSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lesson", "room_kinds"],
    "properties": {
        "lesson": {"type": "string"},
        "room_kinds": {"type": "array", "items": {"type": "string", "enum": list(ROOM_KINDS)}},
    },
}

REFLECTION_MAX_TOKENS = 400
"""A lesson is one sentence. Asking for the model's whole output budget gets a
call refused on a nearly empty account before it is ever answered."""

OFF_LIMITS = ("threshold", "inches", "measured_inches", "required_inches", "unlock", "movable", "rule pack")


@traced("evolve.reflect")
def reflect(
    failures: list[Experience],
    recalled: list[Experience],
    playbook: Playbook,
    *,
    avoid: frozenset[str] = frozenset(),
    model=None,
) -> Lesson | None:
    """One new lesson, or `None` when there is nothing new to say."""
    lessons_from = _learnable(failures)
    if not lessons_from:
        return None
    client = model or OpenRouter()
    answer = client.structured(
        REFLECTION_INSTRUCTION,
        _payload(lessons_from, recalled, playbook),
        LESSON_SCHEMA,
        "playbook_lesson",
        max_tokens=REFLECTION_MAX_TOKENS,
    )
    proposed = None if isinstance(answer, Rejected) else _astra_lesson(answer, lessons_from, playbook, avoid)
    return proposed or local_lesson(lessons_from, playbook, avoid=avoid)


def _learnable(failures: Iterable[Experience]) -> list[Experience]:
    """Failures where a real action was chosen and a right one is known.

    A rejected answer or a spent call budget is not a wrong choice, so there is
    nothing in it to learn about choosing."""
    return [item for item in failures if item.chose in ACTIONS and item.expected in ACTIONS]


def _payload(failures: list[Experience], recalled: list[Experience], playbook: Playbook) -> dict:
    return {
        "failures": [item.brief() for item in failures],
        "similar_past_failures": [item.brief() for item in recalled],
        "current_lessons": [lesson.text for lesson in playbook.lessons],
        "actions": ACTION_CRITERIA,
    }


def _astra_lesson(answer, failures: list[Experience], playbook: Playbook, avoid: frozenset[str]) -> Lesson | None:
    text = str(answer.payload.get("lesson", "")).strip()
    if not acceptable(text, playbook, avoid):
        return None
    kinds = tuple(kind for kind in answer.payload.get("room_kinds", []) if kind in ROOM_KINDS)
    return Lesson(
        text=text,
        room_kinds=kinds,
        learned_from=tuple(item.case_id for item in failures),
        source=f"astra:{answer.model or 'openrouter'}",
    )


def acceptable(text: str, playbook: Playbook, avoid: frozenset[str] = frozenset()) -> bool:
    lowered = text.casefold()
    return (
        bool(text)
        and len(text) <= MAX_LESSON_CHARS
        and not playbook.knows(text)
        and lowered not in avoid
        and not any(word in lowered for word in OFF_LIMITS)
    )


def local_lesson(
    failures: list[Experience], playbook: Playbook, *, avoid: frozenset[str] = frozenset()
) -> Lesson | None:
    """The commonest wrong turn not already written down, as a rule of thumb."""
    turns = Counter(_turn(item) for item in _learnable(failures))
    for (chose, expected, repeated), _ in turns.most_common():
        text = _repeat_text(chose, expected) if repeated else _mismatch_text(chose, expected)
        if acceptable(text, playbook, avoid):
            taught_by = tuple(item.case_id for item in failures if _turn(item) == (chose, expected, repeated))
            return Lesson(text=text, learned_from=taught_by, source="local_reflection")
    return None


def _turn(item: Experience) -> tuple[str | None, str | None, bool]:
    return item.chose, item.expected, item.chose in item.actions_taken


def _repeat_text(chose: str, expected: str) -> str:
    return (
        f"When {chose} is already in actions_taken, that request has been made; "
        f"do not choose {chose} again, choose {expected}."
    )


def _mismatch_text(chose: str, expected: str) -> str:
    return f"Prefer {expected} over {chose} when this holds: {ACTION_CRITERIA[expected]}"
