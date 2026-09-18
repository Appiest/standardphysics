"""Nothing reaches a person unless the scan can stand behind it.

Three things are checked, and an answer that fails any of them is replaced by a
sentence saying what could not be established. An empty screen is a bug and a
confident wrong answer is worse.

  Every thing named exists in this scan.
  Every figure came from the scan rather than from a sentence.
  A count of nothing is only a count when the scan could have counted it.

The last one is the reason this file exists. A scan's model carries names for
whatever the scanner recognised and nothing else, so a room full of books whose
model has no word for a book will say there are none. Zero and "not in the
vocabulary" are different answers and only one of them is true.
"""

from __future__ import annotations

import dataclasses
import re

from standardphysics_contracts import SceneGraph, to_inches, to_meters

from ..numbers import INCHES_PER_FOOT
from .answer import Answer
from .sufficiency import measured_enough

NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
ABSOLUTE_TOLERANCE = 0.05
RELATIVE_TOLERANCE = 0.02

UNKNOWN_THING = (
    "This scan does not have a name for {asked}, so it cannot tell you how many "
    "there are. It only names what the scanner recognised, and none of it is {asked}."
)
UNKNOWN_FIGURE = (
    "There is a measurement in that answer the scan cannot account for, so it is "
    "not worth showing. Ask again and it will be measured rather than guessed."
)
UNKNOWN_THINGS = (
    "That answer referred to something this scan has no record of, so it is not "
    "worth showing."
)


def verified(answer: Answer, graph: SceneGraph, asked: str = "") -> Answer:
    """The answer, or a refusal that says what could not be established."""
    for problem, reason in (
        (_invented_things(answer, graph), UNKNOWN_THINGS),
        (_absent_is_not_zero(answer), None),
        (_invented_figures(answer, graph), UNKNOWN_FIGURE),
        (measured_enough(graph, asked), None),
    ):
        if problem:
            return _refuse(answer, problem if reason is None else reason)
    return answer


def _refuse(answer: Answer, text: str) -> Answer:
    return dataclasses.replace(answer, text=text, rejected="could_not_establish")


def _invented_things(answer: Answer, graph: SceneGraph) -> bool:
    known = {node.id for node in graph.nodes}
    return any(subject not in known for subject in answer.subjects)


def _absent_is_not_zero(answer: Answer) -> str | None:
    """A count of something the scan has no word for is unknown, not nothing.

    The scan names what the scanner recognised. Everything else in the room is
    still in the room, so reporting zero of it states as a fact something the
    scan never looked for.
    """
    data = answer.data or {}
    if data.get("counts") != {} or not data.get("asked_about"):
        return None
    return UNKNOWN_THING.format(asked=data["asked_about"])


def _invented_figures(answer: Answer, graph: SceneGraph) -> bool:
    known = _known(answer, graph)
    return any(not _matches(value, known) for value in _numbers(answer.text))


def _numbers(text: str) -> list[float]:
    return [float(found.group()) for found in NUMBER.finditer(text or "")]


def _known(answer: Answer, graph: SceneGraph) -> list[float]:
    """Every figure the scan can account for, in any unit a person is shown.

    The room is measured in metres and read out in inches and feet, so a figure
    on screen is almost never the figure in the model. Each measurement is
    offered in all of them, and a figure matching none of them came from nowhere.
    """
    measured: list[float] = []
    for node in graph.nodes:
        measured.extend(node.dimensions.as_tuple())
        measured.extend(node.transform.position.as_tuple())
    values = list(_floats(answer.data)) + [float(len(graph.nodes))]
    for metres in measured:
        values.extend(_as_read(metres))
    for value in list(values):
        values.extend(_as_read(to_meters(value)))
    return values


def _as_read(metres: float) -> tuple[float, ...]:
    """One measurement, in each unit the app is willing to say it in."""
    inches = to_inches(metres)
    return (metres, inches, inches / INCHES_PER_FOOT, inches % INCHES_PER_FOOT, metres * 100)


def _floats(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [found for item in value.values() for found in _floats(item)]
    if isinstance(value, (list, tuple)):
        return [float(len(value))] + [found for item in value for found in _floats(item)]
    return []


def _matches(value: float, known: list[float]) -> bool:
    return any(_close(value, candidate) for candidate in known)


def _close(value: float, candidate: float) -> bool:
    """Near enough that rounding for a reader explains the difference.

    Inches and feet are read off metres, so a figure on screen is rarely the
    figure in the model to the last decimal.
    """
    difference = abs(value - candidate)
    if difference <= ABSOLUTE_TOLERANCE:
        return True
    scale = max(abs(value), abs(candidate))
    return scale > 0 and difference / scale <= RELATIVE_TOLERANCE
