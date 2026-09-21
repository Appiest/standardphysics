"""Strict client for TypeSafe System One's three typed primitives.

The provider may judge language, but it cannot add candidates or change an
answer's meaning. Every response is checked against the questions that code
sent before a caller can use it.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .typesafe import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_PATH,
    MODEL_ENV,
    Transport,
    TypeSafeCallBudget,
    UrllibTransport,
)

PROBABILITY_SUM_TOLERANCE = 1e-4
PROVIDER_DECIMAL_PRECISION = 2


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["choice"] = "choice"
    instructions: str | dict | list
    criteria: dict[str, str | None] = Field(min_length=2)


class ScoreQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["score"] = "score"
    instructions: str | dict | list
    criteria: list[str] = Field(min_length=2)


class NoulQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["noul"] = "noul"
    instructions: str | dict | list
    criteria: dict[Literal["true", "false"], str] | None = None


Question = Annotated[
    ChoiceQuestion | ScoreQuestion | NoulQuestion,
    Field(discriminator="type"),
]


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)


class ScoreAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float = Field(ge=0.0, le=1.0)


class NoulAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)

    type: Literal["noul"]
    noul: float = Field(ge=0.0, le=1.0)


Answer = ChoiceAnswer | ScoreAnswer | NoulAnswer


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@dataclass(frozen=True)
class SystemOneResult:
    model: str
    answers: dict[str, Answer]
    usage: Usage


class SystemOneError(RuntimeError):
    """No provider output should be consumed after this error."""


class SystemOneClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        path: str = DEFAULT_PATH,
        model: str | None = None,
        transport: Transport | None = None,
        budget: TypeSafeCallBudget | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get(API_KEY_ENV)
        self.base_url = (
            base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
        ).rstrip("/")
        self.path = path or DEFAULT_PATH
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.transport = transport or UrllibTransport()
        self.budget = budget

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def evaluate(
        self,
        state: str | dict | list,
        questions: Mapping[str, ChoiceQuestion | ScoreQuestion | NoulQuestion],
    ) -> SystemOneResult:
        if not self.configured:
            raise SystemOneError("typesafe_not_configured")
        if not questions:
            raise SystemOneError("questions_empty")
        if any(not question_id.strip() for question_id in questions):
            raise SystemOneError("question_id_empty")

        body = {
            "state": state,
            "model": self.model,
            "questions": {
                question_id: question.model_dump(mode="json", exclude_none=True)
                for question_id, question in questions.items()
            },
        }
        if self.budget is not None and not self.budget.reserve():
            raise SystemOneError("typesafe_call_budget_exhausted")
        try:
            raw = self.transport.post(
                f"{self.base_url}{self.path}",
                json.dumps(body).encode("utf-8"),
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as error:
            raise SystemOneError("transport_error") from error
        return _parse_response(raw, questions)


def _parse_response(
    raw: bytes | str | dict,
    questions: Mapping[str, ChoiceQuestion | ScoreQuestion | NoulQuestion],
) -> SystemOneResult:
    try:
        payload: Any = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise SystemOneError("response_not_json") from error
    if not isinstance(payload, dict):
        raise SystemOneError("response_not_an_object")
    if set(payload) != {"model", "answers", "usage"}:
        raise SystemOneError("response_fields_invalid")
    model = payload.get("model")
    answers = payload.get("answers")
    if not isinstance(model, str) or not model.strip():
        raise SystemOneError("response_model_invalid")
    if not isinstance(answers, dict):
        raise SystemOneError("response_answers_invalid")
    if set(answers) != set(questions):
        raise SystemOneError("response_answer_ids_mismatch")
    try:
        usage = Usage.model_validate(payload.get("usage"))
    except ValidationError as error:
        raise SystemOneError("response_usage_invalid") from error

    parsed: dict[str, Answer] = {}
    for question_id, question in questions.items():
        parsed[question_id] = _parse_answer(answers[question_id], question)
    return SystemOneResult(model=model, answers=parsed, usage=usage)


def _parse_answer(
    raw: object,
    question: ChoiceQuestion | ScoreQuestion | NoulQuestion,
) -> Answer:
    try:
        if isinstance(question, ChoiceQuestion):
            answer = ChoiceAnswer.model_validate(raw)
            _validate_choice(answer, question)
            return answer
        if isinstance(question, ScoreQuestion):
            answer = ScoreAnswer.model_validate(raw)
            _validate_score(answer, question)
            return answer
        return NoulAnswer.model_validate(raw)
    except ValidationError as error:
        raise SystemOneError("response_answer_invalid") from error


def _validate_choice(answer: ChoiceAnswer, question: ChoiceQuestion) -> None:
    options = set(question.criteria)
    if answer.choice not in options:
        raise SystemOneError("response_choice_unknown")
    _validate_distribution(answer.probabilities, options)
    maximum = max(answer.probabilities.values())
    if not math.isclose(
        answer.probabilities[answer.choice], maximum, rel_tol=0.0, abs_tol=1e-9
    ):
        raise SystemOneError("response_choice_not_most_likely")


def _validate_score(answer: ScoreAnswer, question: ScoreQuestion) -> None:
    levels = {str(index) for index in range(len(question.criteria))}
    expected_legend = {
        str(index): description for index, description in enumerate(question.criteria)
    }
    if answer.legend != expected_legend:
        raise SystemOneError("response_score_legend_mismatch")
    _validate_distribution(answer.probabilities, levels)
    if not 0.0 <= answer.score <= len(question.criteria) - 1:
        raise SystemOneError("response_score_out_of_range")
    weighted_score = sum(
        int(level) * probability
        for level, probability in answer.probabilities.items()
    )
    # System One currently serializes scores and level probabilities to two
    # decimals independently. A score is computed before the probabilities are
    # rounded, so reconstructing it from the displayed distribution can differ
    # by the combined rounding error of every weighted level.
    rounding_unit = 0.5 * 10 ** (-PROVIDER_DECIMAL_PRECISION)
    rounding_tolerance = rounding_unit * (
        1 + sum(range(len(question.criteria)))
    )
    if not math.isclose(
        answer.score, weighted_score, rel_tol=0.0, abs_tol=rounding_tolerance
    ):
        raise SystemOneError("response_score_inconsistent")


def _validate_distribution(probabilities: dict[str, float], expected: set[str]) -> None:
    if set(probabilities) != expected:
        raise SystemOneError("response_probability_keys_mismatch")
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in probabilities.values()):
        raise SystemOneError("response_probability_invalid")
    if not math.isclose(
        sum(probabilities.values()),
        1.0,
        rel_tol=0.0,
        abs_tol=PROBABILITY_SUM_TOLERANCE,
    ):
        raise SystemOneError("response_probabilities_do_not_sum_to_one")


__all__ = [
    "Answer",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "NoulAnswer",
    "NoulQuestion",
    "ScoreAnswer",
    "ScoreQuestion",
    "SystemOneClient",
    "SystemOneError",
    "SystemOneResult",
    "Usage",
]
