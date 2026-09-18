"""Scoring an answer against the room it was asked about.

Two judgements, and only one of them is a model's. Whether a number in the
sentence came from somewhere real is arithmetic, so it is checked here in code
against the answer's own data and the scan's measurements. Whether the sentence
actually follows from the room is reading, so a model does that, and it is shown
the room rather than an expected answer. There is no expected answer anywhere in
this suite: a string to match is a string to special-case.

The judge is never told whether a question was written as answerable. It reports
what the answer did, and the scoring below decides whether that was right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import OpenRouter
from ...router.decision import Rejected
from .questions import Question
from .scenes import Scene

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
SPAN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:feet|foot|ft)\s*(\d+(?:\.\d+)?)\s*(?:inches|inch|in)\b")
"""A span written the way it is read out: three feet six inches is one figure.

Counting the three and the six separately marks a correctly rendered
measurement as two inventions, which is the reader misreading the sentence
rather than the answer making anything up."""
ABSOLUTE_TOLERANCE = 0.05
RELATIVE_TOLERANCE = 0.02

INSTRUCTION = """You are shown a question about a room that was really scanned, the
measured regions of that room, and the answer a system gave.

Report what the answer did. Do not decide whether the question was a fair one, and
do not answer it yourself.

- `refused`: the answer declined, said it could not tell, or asked for something
  else instead of answering.
- `supported`: everything the answer asserts follows from the regions shown. An
  answer that names something not in the regions, or claims a relationship the
  extents contradict, is not supported. A refusal is not supported and not
  unsupported; mark it false.
- `confident`: the answer states something as fact rather than hedging.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["refused", "supported", "confident", "reason"],
    "properties": {
        "refused": {"type": "boolean"},
        "supported": {"type": "boolean"},
        "confident": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}


class CouldNotJudge(RuntimeError):
    """Raised rather than counting an unscored answer as a pass."""


@dataclass(frozen=True)
class Verdict:
    question: Question
    refused: bool
    supported: bool
    confident: bool
    numbers_traced: bool
    reason: str
    answer: str = ""
    """What the app said, kept so a run can be read rather than only scored."""

    rejected: str | None = None
    """Why the app would not take the question, when it would not take it.

    A refusal that comes from the resolver failing to parse the sentence is a
    different thing from a system judging that the scan cannot answer it, and
    telling them apart is how the scrambled run stops being ambiguous.
    """

    @property
    def passed(self) -> bool:
        """What counts as getting it right.

        An unanswerable question is passed by saying so. An answerable one is
        passed by an answer the room supports whose every number came from the
        room. Answering an unanswerable question with confidence is the failure
        this suite exists to catch, and it is counted on its own below.
        """
        if not self.question.answerable:
            return self.refused
        return not self.refused and self.supported and self.numbers_traced

    @property
    def hallucinated(self) -> bool:
        return not self.question.answerable and not self.refused and self.confident


def judge(question: Question, answer, scene: Scene, model: OpenRouter | None = None) -> Verdict:
    payload = {
        "question": question.text,
        "answer": answer.text,
        "answer_data": _plain(answer.data),
        "regions": scene.regions(),
    }
    verdict = (model or OpenRouter()).structured(INSTRUCTION, payload, SCHEMA, "held_out_verdict")
    if isinstance(verdict, Rejected):
        raise CouldNotJudge(f"An answer went unscored ({verdict.reason}).")
    return Verdict(
        question=question,
        refused=bool(verdict.payload["refused"]),
        supported=bool(verdict.payload["supported"]),
        confident=bool(verdict.payload["confident"]),
        numbers_traced=numbers_traced(answer, scene),
        reason=str(verdict.payload["reason"]),
        answer=answer.text,
        rejected=getattr(answer, "rejected", None),
    )


def numbers_traced(answer, scene: Scene) -> bool:
    """Whether every number in the sentence came from somewhere measured.

    The model that writes an answer must never supply a figure about the room, so
    a figure that appears in the sentence and nowhere in the data behind it, or in
    the scan, is a figure somebody made up.
    """
    known = _known_values(answer, scene)
    return all(_matches(value, known) for value in _numbers_in(answer.text))


def _numbers_in(text: str) -> list[float]:
    """Every figure the sentence states, with a compound span read as one."""
    said = text or ""
    spans = [
        float(feet) * INCHES_PER_FOOT + float(inches)
        for feet, inches in SPAN.findall(said)
    ]
    return spans + [float(match.group()) for match in NUMBER.finditer(SPAN.sub(" ", said))]


INCHES_PER_METRE = 39.3700787
INCHES_PER_FOOT = 12.0


def _known_values(answer, scene: Scene) -> list[float]:
    """Every figure the scan can account for, in each unit a person is shown.

    The room is measured in metres and read out in inches and feet, so a figure
    on screen is almost never the figure in the model. Without this the check
    calls "20.6 by 18.6 inches" invented while 20.6 inches is a chair's measured
    width, and no correct system could ever score full marks on it.

    What counts is the answer's own data, not any measurement anywhere in the
    room. Every figure in the scene, offered in five units with a tolerance on
    each, covers the number line densely enough that an invented measurement
    lands on something: the check let "the aisle is 1.42 metres" through with no
    data behind it at all. The data is what the engine worked out, so a figure
    that is not in it is a figure nothing worked out.

    The conversion is written out here rather than imported from the app, so an
    app that converts wrongly is caught instead of agreed with.
    """
    computed = list(_floats(answer.data)) + [float(len(scene.graph.nodes))]
    values = list(computed)
    for value in computed:
        values.extend(_as_read(value))
        values.extend(_as_read(value / INCHES_PER_METRE))
    return values


def _as_read(metres: float) -> tuple[float, ...]:
    """One measurement, in each unit an answer is allowed to say it in."""
    inches = metres * INCHES_PER_METRE
    return (metres, inches, inches / INCHES_PER_FOOT, inches % INCHES_PER_FOOT, metres * 100)


def _floats(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [found for item in value.values() for found in _floats(item)]
    if isinstance(value, (list, tuple)):
        return [len(value)] + [found for item in value for found in _floats(item)]
    return []


def _matches(value: float, known: list[float]) -> bool:
    return any(_close(value, candidate) for candidate in known)


def _close(value: float, candidate: float) -> bool:
    """Near enough that rounding for a reader explains the difference."""
    difference = abs(value - candidate)
    if difference <= ABSOLUTE_TOLERANCE:
        return True
    scale = max(abs(value), abs(candidate))
    return scale > 0 and difference / scale <= RELATIVE_TOLERANCE


def _plain(data) -> dict:
    """The answer's own numbers, flattened enough for a model to read."""
    return {key: _readable(value) for key, value in (data or {}).items()}


def _readable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _readable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_readable(item) for item in value]
    return str(value)
