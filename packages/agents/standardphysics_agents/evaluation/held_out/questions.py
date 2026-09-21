"""Questions written against a scene, fresh every run.

Nothing is stored between runs on purpose. A question file in the repository is
a question somebody can special-case, and the whole point of this suite is to
ask things nobody prepared for. The run seed makes a run reproducible for anyone
who has the seed, and reproduces nothing for the code being scored.

Some of the questions have no answer in the scan. Saying so is the pass, and
answering one confidently is the failure the rest of the suite cannot catch.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ...models import OpenRouter
from ...router.decision import Rejected
from .scenes import Scene

UNANSWERABLE_SHARE = 0.125
"""About one in eight, per `docs/MISSION.md`."""

BATCH = 6
"""Questions asked for in one call.

Eighty in a single request is one long generation that times out and takes the
whole run with it. Small batches run at the same time, and a batch that fails
costs six questions rather than the run. The batches overlap in approach, and
the diversity filter below is what makes them one set rather than several.
"""

INSTRUCTION = """You are given the measured regions of a room that was really scanned.
Each region has an extent in metres and a name somebody coined for it. You know
nothing else about the room.

Write questions a person standing in this room would really ask. Follow all of these:

- No two questions may be answerable by the same approach. Say how each one would
  be answered in the `approach` field, in your own words, and make every approach
  genuinely different: counting, measuring a span, comparing two extents, reading
  something off a surface, reasoning about what holds what up, planning a
  rearrangement, and whatever else the room suggests.
- Exactly the requested number of questions must be ones this scan CANNOT answer,
  marked `answerable: false`. Make them plausible things to ask about this room
  rather than obvious nonsense, for instance about something outside what was
  scanned, or about a property nothing here measured.
- Never mention a region id. Ask the way a person talks.
- Do not assume what kind of place this is. Do not assume it has a counter, a
  till, a restroom or a front door unless a region's extent says so.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["questions"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "approach", "answerable"],
                "properties": {
                    "text": {"type": "string"},
                    "approach": {"type": "string"},
                    "answerable": {"type": "boolean"},
                },
            },
        }
    },
}


class CouldNotWriteQuestions(RuntimeError):
    """Raised rather than scoring against questions nobody wrote."""


@dataclass(frozen=True)
class Question:
    text: str
    approach: str
    answerable: bool
    scene_id: str


def write(
    scene: Scene,
    count: int,
    seed: int,
    model: OpenRouter | None = None,
    workers: int = 4,
) -> list[Question]:
    """`count` questions about this scene, none answerable the way another is."""
    model = model or OpenRouter()
    batches = [
        (index, min(BATCH, count - index * BATCH))
        for index in range((count + BATCH - 1) // BATCH)
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        written = pool.map(lambda batch: _batch(scene, seed, model, *batch), batches)
    return _distinct([item for group in written for item in group], scene)


def _batch(scene: Scene, seed: int, model: OpenRouter, index: int, count: int) -> list[dict]:
    """One call's worth, or nothing if that call did not come back."""
    payload = {
        "run_seed": seed,
        "batch": index,
        "how_many": count,
        "how_many_unanswerable": max(1, round(count * UNANSWERABLE_SHARE)),
        "regions": scene.regions(),
    }
    answer = model.structured(INSTRUCTION, payload, SCHEMA, "held_out_questions")
    return [] if isinstance(answer, Rejected) else answer.payload.get("questions", [])


COMMON = frozenset(
    "a an and are be been can could do does for from has have here how in into is it "
    "its me much my of on or that the there these this to was were what when where "
    "which who why will with would you your".split()
)
"""Words that carry no subject, so two questions that differ only in these are
the same question asked twice."""


def _subject(text: str) -> frozenset[str]:
    """What a question is about, with the grammar taken out.

    Batches run at the same time and cannot see each other, so the easy question
    gets written several times over. "How many chairs are here?" and "How many
    chairs are in here?" differ by one word and take four slots between them,
    which counts one executor four times and flatters the score.
    """
    words = "".join(character if character.isalnum() else " " for character in text.lower())
    return frozenset(words.split()) - COMMON


def _distinct(written: list[dict], scene: Scene) -> list[Question]:
    """One question per approach, and one per subject.

    The diversity constraint is in the instruction and enforced here, because a
    suite of eighty questions all answered by counting measures one executor.
    """
    seen: set[str] = set()
    subjects: list[frozenset[str]] = []
    questions: list[Question] = []
    for item in written:
        approach = " ".join(str(item.get("approach", "")).lower().split())
        if not approach or approach in seen or not item.get("text"):
            continue
        subject = _subject(str(item["text"]))
        if any(subject == already for already in subjects):
            continue
        seen.add(approach)
        subjects.append(subject)
        questions.append(
            Question(
                text=str(item["text"]),
                approach=approach,
                answerable=bool(item.get("answerable", True)),
                scene_id=scene.scan_id,
            )
        )
    if not questions:
        raise CouldNotWriteQuestions(
            "No questions were written. This suite reports nothing rather than "
            "scoring against an empty set."
        )
    return questions
