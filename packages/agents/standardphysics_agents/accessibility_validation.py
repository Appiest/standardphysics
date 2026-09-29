"""Checks that a Jev answer has the type it was asked for, and bounds on what a batch may hold."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import cast, get_args

from .accessibility_judgments import ChoiceJudgment, ScoreJudgment
from .accessibility_vocabulary import MAX_BATCH_ITEMS, GoalKind
from .router.systemone import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
)


def goal_kind(value: str) -> GoalKind:
    if value not in get_args(GoalKind):
        raise TypeError("validated answer type changed unexpectedly")
    return cast(GoalKind, value)


def choice_answer(answer) -> ChoiceJudgment:
    if not isinstance(answer, ChoiceAnswer):
        raise TypeError("validated answer type changed unexpectedly")
    return ChoiceJudgment(
        value=answer.choice,
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
    )


def score_answer(answer) -> ScoreJudgment:
    if not isinstance(answer, ScoreAnswer):
        raise TypeError("validated answer type changed unexpectedly")
    return ScoreJudgment(
        value=answer.score,
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
    )


def noul_value(answer) -> float:
    if not isinstance(answer, NoulAnswer):
        raise TypeError("validated answer type changed unexpectedly")
    return answer.noul


def noul_certainty(value: float) -> float:
    return abs(value - 0.5) * 2.0


def bounded_count(items: Sequence) -> None:
    if not items:
        raise ValueError("at least one item is required")
    if len(items) > MAX_BATCH_ITEMS:
        raise ValueError(f"a judgment batch cannot exceed {MAX_BATCH_ITEMS} items")


def validated_items(items: Sequence, id_getter=lambda item: item.id):
    result = list(items)
    bounded_count(result)
    ids = [str(id_getter(item)) for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate IDs must be unique")
    return result


def probability_threshold(value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError("probability or confidence must be between zero and one")


NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def quantity_candidates(text: str) -> tuple[int, ...]:
    found: list[int] = []
    for match in re.finditer(r"\b\d{1,4}\b", text):
        value = int(match.group())
        if value not in found:
            found.append(value)
    for word in re.findall(r"[a-z]+", text.casefold()):
        if word in NUMBER_WORDS and NUMBER_WORDS[word] not in found:
            found.append(NUMBER_WORDS[word])
    return tuple(found)

