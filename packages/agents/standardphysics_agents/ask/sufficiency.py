"""Whether what was measured can answer what was asked.

The checks in `verify.py` catch an answer that names something absent or quotes a
figure from nowhere. They do not catch an answer that is about the wrong thing.
Asked what a tabletop is made of, the app replies that the three tables stand in
a row, every entity real and every figure traced, and the person reading it is
told something true and useless in place of "the scan cannot tell you that".

A scan measures shape and position. It does not measure what something is made
of, what it weighs, what it costs or how warm it is, and the list of things it
does not measure has no end, so it is not written down anywhere here. Instead the
question and everything the scan measured are put to a model, and it judges
whether those measurements can answer that question.

What the scan measured, not what this answer happened to work out. Judging by the
answer's own figures asks whether this executor did enough, and refuses "which
table is closest" on the grounds that it would need table lengths, which the scan
has and the executor simply did not return. A weak answer to an answerable
question is a defect somewhere else, and `verify.py` is what catches it.

That judgement is the only thing asked of it. It never sees the answer's prose,
so it cannot be talked round by a confident sentence, and it is never asked for a
fact about the room, so nothing it says can become a number on the screen.
"""

from __future__ import annotations

from standardphysics_contracts import measured_as

from ..models import OpenRouter
from ..router.decision import Rejected
from ..tracing import traced

INSTRUCTION = """A question was asked about a room that has been scanned. Every
region the scan measured is below, with its extent in metres and where it sits.

Judge one thing only: could those measurements answer that question?

Say no only when the question turns on something no measurement of shape,
size or position could settle: what a thing is made of, what it weighs, what it
costs, whether it locks, how it feels. Say yes whenever the answer could be
worked out from the extents and positions you were given, however much arithmetic
it would take.

Do not answer the question. Do not judge whether it is worth asking.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answerable", "missing"],
    "properties": {
        "answerable": {"type": "boolean"},
        "missing": {
            "type": "string",
            "description": "What the scan would have had to measure. Empty when answerable.",
        },
    },
}

CANNOT_TELL = (
    "This scan measures the shape and position of things, and answering that "
    "would need {missing}. Nothing here can tell you."
)
CANNOT_TELL_PLAIN = (
    "This scan measures the shape and position of things, which cannot answer "
    "that. Nothing here can tell you."
)


@traced("ask.sufficiency")
def measured_enough(
    graph, asked: str, models: OpenRouter | None = None
) -> str | None:
    """What the scan would have had to measure, or nothing if it measured enough.

    A model that cannot be reached decides nothing. Refusing every question
    because a server is busy is its own kind of wrong answer.
    """
    models = models or OpenRouter()
    if not models.configured or not asked:
        return None
    verdict = models.structured(
        INSTRUCTION,
        {"asked": asked, "regions": _measured(graph)},
        SCHEMA,
        "scan_can_answer",
    )
    if isinstance(verdict, Rejected) or verdict.payload.get("answerable", True):
        return None
    missing = _mid_sentence(str(verdict.payload.get("missing", "")))
    return CANNOT_TELL.format(missing=missing) if missing else CANNOT_TELL_PLAIN


def _mid_sentence(said: str) -> str:
    """The model writes a sentence; this lands in the middle of one."""
    trimmed = said.strip().rstrip(".")
    if not trimmed:
        return ""
    return trimmed[0].lower() + trimmed[1:] if trimmed[1:2].islower() else trimmed


def _measured(graph) -> list[dict]:
    """Every region the scan measured, and nothing anybody said about them."""
    return [
        {
            "name": node.label,
            "width_m": round(measured_as(node).x, 3),
            "depth_m": round(measured_as(node).y, 3),
            "height_m": round(measured_as(node).z, 3),
            "centre_m": [round(value, 3) for value in node.transform.position.as_tuple()],
        }
        for node in graph.nodes
    ]
