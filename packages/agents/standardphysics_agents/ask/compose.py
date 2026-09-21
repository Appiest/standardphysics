"""A question answered by a plan the model writes and the engine runs.

The eight question kinds are why "is the widest chair wider than the narrowest
storage unit" comes back as "the chair and the storage run from 24 inches to 33.3
inches wide". There is an executor for measuring and none for comparing, so a
comparison is served a range and the reader is left to do the arithmetic. Adding a
ninth kind for comparisons buys one question and leaves the next one stranded.

So the model writes steps instead. It may pick out regions, narrow a set by a
measurement, take the largest or smallest of them, read a figure, and do
arithmetic on figures it has already asked for. Every step is evaluated here.

The split that matters: the model says which regions to compare and how, and
never what the comparison returned. The sentence it writes has holes in it, the
engine fills them from what it computed, and a figure that did not come out of a
step cannot reach the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import SceneGraph, SceneNode, measured_as, to_inches

from ..models import OpenRouter
from ..router.decision import Rejected
from ..tracing import traced

FIELDS = (
    "width_m", "depth_m", "height_m", "footprint_m2",
    "bottom_m", "top_m", "x_m", "y_m", "z_m",
)
"""What can be read off a region. Shape and where it is, and nothing else."""

NUMBER = re.compile(r"\d+(?:\.\d+)?")
SLOT = re.compile(r"\{([A-Za-z0-9_]+)(\?[^}]*)?\}")

CLOSER_THAN_THE_SCAN = " (within a centimetre, closer than this scan can tell apart)"

SAME_WITHIN = 0.01
"""Metres within which two measurements count as the same.

A centimetre, which is about what a phone can tell. It was two per cent, which
on a pair of two-metre shelves is four centimetres, so one shelf a centimetre
and a half taller than another was reported as the same height."""

METRES = "m"
SQUARE = "m2"
VERDICT = "verdict"
PLAIN = ""
"""What a figure is. A count of chairs, a span and an area do not read alike, and
multiplying two spans gives the third of them."""

INSTRUCTION = """A question has been asked about a room that was scanned. Every
region it measured is listed, with its extent in metres and where its middle sits.

Write the steps that answer it. Do not answer it yourself, and never write a
measurement of the room into the sentence: put a hole where each figure goes and
the engine will fill it from what your steps worked out.

Steps, each with an `id` you can refer to later:
  every    every region in the room, so you never have to list them all
  regions  the ids of the regions this step is about, chosen from the list
  without  an earlier step with the regions of another step taken out of it
  where    narrow an earlier step by a field: how is lt, gt, lte, gte, eq or ne.
           Measure against `other`, naming a step that worked a figure out; or
           against `value`, which may only be a number the question itself gave
  pick     take one region from an earlier step: max or min, by a field
  value    read a field off a step that holds exactly one region
  arith    combine two earlier values: sub, add, mul, ratio, or gap
  count    how many regions an earlier step holds
  sum      add a field up over every region an earlier step holds
  extreme  the largest or smallest value of a field over an earlier step
  nearest  from an earlier step, the region closest to another step holding one
           region. min for the closest, max for the furthest, as with pick
  compare  two earlier values, giving which of them is the greater
  given    a quantity the question itself states, in metres, so it can be used
           in the arithmetic. Only a number the asker wrote: never a measurement
           of the room, which you must take off a region instead.

Use `every` and then narrow it whenever a question is about the room at large:
"what is closest to X" is every region, without X, then nearest to X. Listing ids
by hand and missing one is how an answer comes back naming something far away.

`gap` takes two steps each holding one region and returns the straight-line
distance in metres between their middles; `floor_gap` the same distance measured
across the floor, ignoring how high each one sits. Use `gap` for "centre to
centre" and `floor_gap` for "on the floor" or "looking down". `nearest` ranks by
the straight line too, unless its field is "floor". `mul` of two lengths is an area.

Every region offers the same fields: width_m, depth_m and height_m as a tape
measure would read them; footprint_m2, the floor area it covers; bottom_m and
top_m, how high its lowest and highest points sit above the floor; and x_m, y_m,
z_m for where its middle is. Use footprint_m2 for an area rather than multiplying
two extents yourself, and bottom_m to tell whether something reaches the floor.

Then write `say`, a sentence with {holes} naming value, arith or count steps, and
{holes} naming a region step to print what it is called. Say plainly what was
found.

A hole may carry wording that depends on a `compare`: write
{verdict?is wider than|is the same width as|is narrower than} and the engine says
whichever of the three the comparison found. That is how a question answered yes
or no gets answered.

Do not write a unit beside a hole. The engine decides whether a length reads
better in inches or feet and writes the unit itself, so "{difference} wider"
is right and "{difference} metres wider" comes out saying metres about inches.

If the steps cannot answer the question, return no steps at all.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps", "say"],
    "properties": {
        "say": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                # Strict schemas require every property to be required, so the
                # ones a given step does not use are sent as null rather than
                # left out. Marking them optional has the whole request refused.
                "required": ["id", "op", "ids", "of", "other", "field", "how", "value"],
                "properties": {
                    "id": {"type": "string"},
                    "op": {
                        "type": "string",
                        "enum": [
                            "every", "regions", "without", "where", "pick",
                            "value", "arith", "count",
                            "sum", "extreme", "nearest", "compare", "given",
                        ],
                    },
                    "ids": {"type": ["array", "null"], "items": {"type": "string"}},
                    "of": {"type": ["string", "null"]},
                    "other": {"type": ["string", "null"]},
                    "field": {"type": ["string", "null"]},
                    "how": {"type": ["string", "null"]},
                    "value": {"type": ["number", "null"]},
                },
            },
        },
    },
}


class CannotCompose(ValueError):
    """The plan did not hold together, so nothing from it is shown."""


@dataclass(frozen=True)
class Composed:
    text: str
    figures: dict[str, float]
    regions: tuple[UUID, ...]


@traced("ask.compose")
def compose(asked: str, graph: SceneGraph, models: OpenRouter | None = None) -> Composed | None:
    """Steps from the model, run here, or nothing if they did not hold together."""
    models = models or OpenRouter()
    if not models.configured:
        return None
    written = models.structured(
        INSTRUCTION, {"asked": asked, "regions": _regions(graph)}, SCHEMA, "answer_plan"
    )
    if isinstance(written, Rejected) or not written.payload.get("steps"):
        return None
    try:
        return _run(written.payload, graph, asked)
    except (CannotCompose, KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _regions(graph: SceneGraph) -> list[dict]:
    """The room as the planner sees it, each region under a short handle.

    The handles used to be the scan's own identifiers, thirty-six characters of
    hexadecimal apiece. A plan naming the wrong region because a character was
    copied wrong is a wrong answer, and a room whose names mean something hides
    it: pick the wrong table and the sentence still reads oddly. Somewhere whose
    names mean nothing, nothing catches it.
    """
    return [
        {"id": _handle(index), "name": node.label}
        | {name: round(value, 3) for name, value in _measurements(node).items()}
        for index, node in enumerate(graph.nodes)
    ]


def _handle(index: int) -> str:
    return f"r{index}"


def _measurements(node: SceneNode) -> dict[str, float]:
    """Every figure a region offers, from one place.

    The planner reads these and the engine evaluates them, and they used to be
    written out twice. When the engine started reading extents the way a tape
    measure would and the planner's copy did not, the planner saw a floor nine
    metres tall, multiplied its width by that, and the engine answered that the
    floor covered nothing.
    """
    at, size = node.transform.position, measured_as(node)
    return {
        "width_m": size.x,
        "depth_m": size.y,
        "height_m": size.z,
        "footprint_m2": size.x * size.y,
        "bottom_m": at.z - size.z / 2,
        "top_m": at.z + size.z / 2,
        "x_m": at.x,
        "y_m": at.y,
        "z_m": at.z,
    }


def _field(node: SceneNode, name: str) -> float:
    if name not in FIELDS:
        raise CannotCompose(f"nothing measures {name}")
    return _measurements(node)[name]


def _unit(name: str) -> str:
    return SQUARE if name.endswith("_m2") else METRES


def _run(plan: dict, graph: SceneGraph, asked: str) -> Composed:
    known = {_handle(index): node for index, node in enumerate(graph.nodes)}
    sets: dict[str, list[SceneNode]] = {}
    figures: dict[str, float] = {}
    for step in plan["steps"]:
        if step["op"] == "given":
            figures[step["id"]] = _given(step, asked)
        elif step["op"] == "where":
            sets[step["id"]] = _where(sets[step["of"]], step, figures, asked)
        else:
            _step(step, known, sets, figures)
    return _say(plan.get("say", ""), sets, figures)


def _step(step: dict, known: dict, sets: dict, figures: dict) -> None:
    """One step, run by what it says it is rather than by a chain of questions."""
    name, op = step["id"], step["op"]
    if op in _REGION_STEPS:
        sets[name] = _REGION_STEPS[op](step, known, sets)
    elif op in _FIGURE_STEPS:
        figures[name] = _FIGURE_STEPS[op](step, sets, figures)
    else:
        raise CannotCompose(f"no step does {op}")


_REGION_STEPS = {
    "every": lambda step, known, sets: list(known.values()),
    "without": lambda step, known, sets: [
        node
        for node in sets[step["of"]]
        if node.id not in {other.id for other in sets[step["other"]]}
    ],
    "regions": lambda step, known, sets: [
        known[one] for one in (step.get("ids") or []) if one in known
    ],
    "pick": lambda step, known, sets: [_pick(sets[step["of"]], step)],
    "nearest": lambda step, known, sets: [
        _nearest(sets[step["of"]], sets[step["other"]], step)
    ],
}

_FIGURE_STEPS = {
    "value": lambda step, sets, figures: (
        _only(sets[step["of"]], step), _unit(step["field"])
    ),
    "count": lambda step, sets, figures: (float(len(sets[step["of"]])), PLAIN),
    "arith": lambda step, sets, figures: _arith(step, sets, figures),
    "sum": lambda step, sets, figures: (
        sum(_field(node, step["field"]) for node in sets[step["of"]]),
        _unit(step["field"]),
    ),
    "extreme": lambda step, sets, figures: (
        _field(_pick(sets[step["of"]], step), step["field"]), _unit(step["field"])
    ),
    "compare": lambda step, sets, figures: _verdict(figures[step["of"]], figures[step["other"]]),
}


def _given(step: dict, asked: str) -> tuple[float, str]:
    """A quantity the question stated, checked against the question.

    The asker is allowed to bring a number: "with twenty centimetres between
    them" is theirs, not the room's, and no measurement can supply it. The model
    is not allowed to bring one, so the digits have to be there in the sentence
    they wrote it from. Without that check this step is a way to say anything
    about the room and have it treated as arithmetic.
    """
    value = float(step.get("value") or 0.0)
    if not _written_in(value, asked):
        raise CannotCompose(f"{value} is not a number the question gave")
    return (value, METRES)


IN_WORDS = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "half": 0.5, "quarter": 0.25, "twelve": 12.0, "twenty": 20.0, "thirty": 30.0,
}
"""Quantities people write out instead of writing down.

"Less than a metre tall" states a number as plainly as "less than 1 m", and
reading only digits refused the plan and dropped the question to whatever could
answer it without one."""


def _written_in(value: float, asked: str) -> bool:
    """Whether the question really does state this quantity, in digits or words."""
    said = [float(found) for found in NUMBER.findall(asked or "")]
    words = (asked or "").casefold().replace("-", " ").split()
    said.extend(IN_WORDS[word] for word in words if word in IN_WORDS)
    said.extend(
        IN_WORDS[first] * IN_WORDS[second]
        for first, second in zip(words, words[1:])
        if first in IN_WORDS and second in IN_WORDS
    )
    scales = (1.0, 0.01, 0.001, 0.0254, 0.3048)
    return any(
        abs(value - number * scale) <= max(abs(value) * 0.02, 1e-6)
        for number in said
        for scale in scales
    )


COMPARISONS = {
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
    "lte": lambda a, b: a <= b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: abs(a - b) <= SAME_WITHIN,
    "ne": lambda a, b: abs(a - b) > SAME_WITHIN,
}


def _where(found: list[SceneNode], step: dict, figures: dict, asked: str) -> list[SceneNode]:
    """Narrow a set by a measurement, against a threshold that came from somewhere.

    Two things were wrong here. An operator nobody implemented fell through to
    greater-than, so a plan asking which regions are the same width as another
    got the ones wider than it and reported none; and the threshold was a number
    written straight into the plan, which is how a measurement of the room could
    reach an answer without the engine ever working it out.
    """
    how = step.get("how") or "gt"
    if how not in COMPARISONS:
        raise CannotCompose(f"nothing compares by {how}")
    return [
        node
        for node in found
        if COMPARISONS[how](_field(node, step["field"]), _threshold(step, figures, asked))
    ]


def _threshold(step: dict, figures: dict, asked: str) -> float:
    """What to measure against: a figure already worked out, or the asker's own number."""
    against = step.get("other")
    if against:
        return figures[against][0]
    return _given(step, asked)[0]


def _pick(found: list[SceneNode], step: dict) -> SceneNode:
    if not found:
        raise CannotCompose("nothing to pick from")
    chosen = max if (step.get("how") or "max") == "max" else min
    return chosen(found, key=lambda node: _field(node, step["field"]))


def _only(found: list[SceneNode], step: dict) -> float:
    if len(found) != 1:
        raise CannotCompose("a figure needs exactly one region")
    return _field(found[0], step["field"])


def _arith(step: dict, sets: dict, figures: dict) -> tuple[float, str]:
    """A count is a number of things and a span is a length, and they do not read
    the same way, so each figure carries what it is."""
    how = step.get("how") or "sub"
    if how in DISTANCES:
        return (_gap(sets[step["of"]], sets[step["other"]], DISTANCES[how]), METRES)
    left, right = figures[step["of"]], figures[step["other"]]
    if how == "ratio":
        if right[0] == 0:
            raise CannotCompose("nothing divides by nothing")
        return (left[0] / right[0], PLAIN)
    if how == "mul":
        return (left[0] * right[0], _times(left[1], right[1]))
    unit = left[1] if left[1] == right[1] else PLAIN
    return (left[0] + right[0] if how == "add" else left[0] - right[0], unit)


def _times(one: str, other: str) -> str:
    """Two lengths make an area; a length and a plain number make a length."""
    if one == METRES and other == METRES:
        return SQUARE
    return one if other == PLAIN else other


def _nearest(found: list[SceneNode], to: list[SceneNode], step: dict) -> SceneNode:
    """The member of a set closest to, or furthest from, one other region.

    Ranking a set by a distance nobody has measured yet is what "which table is
    closest to the storage" needs, and picking by a field cannot do it.
    """
    if not found or len(to) != 1:
        raise CannotCompose("nothing to measure from")
    anchor = to[0]
    chosen = max if _wants_the_furthest(step) else min
    flat = step.get("field") == "floor"
    return chosen(found, key=lambda node: _gap([node], [anchor], flat))


DISTANCES = {"gap": False, "floor_gap": True}
"""The two ways two middles can be apart: in a straight line, or across the floor."""

FURTHEST = frozenset({"max", "far", "furthest", "farthest"})
"""The words that mean the far one.

`pick` takes max and min, so a plan says min here too and means nearest. Reading
anything that is not one word as its opposite is how "what is closest" came back
naming the thing on the other side of the room.
"""


def _wants_the_furthest(step: dict) -> bool:
    return str(step.get("how") or "").strip().casefold() in FURTHEST


def _compare(left: tuple, right: tuple) -> float:
    """Which of two figures is the greater, as -1, 0 or 1."""
    difference = left[0] - right[0]
    if abs(difference) <= SAME_WITHIN:
        return 0.0
    return 1.0 if difference > 0 else -1.0


def _verdict(left: tuple, right: tuple) -> tuple[float, str, bool]:
    """A comparison, and whether "the same" is covering a measured difference."""
    found = _compare(left, right)
    return (found, VERDICT, found == 0 and left[0] != right[0])


def _gap(one: list[SceneNode], other: list[SceneNode], across_floor: bool = False) -> float:
    """The distance between two middles, in a straight line or across the floor.

    It was only ever across the floor, which is right for "how far do I walk" and
    wrong for "how far apart are their centres": a shelf's middle sits a metre
    above a stool's, and leaving that out gave the wrong distance and sometimes
    the wrong nearest thing.
    """
    if len(one) != 1 or len(other) != 1:
        raise CannotCompose("a gap is between two things")
    here, there = one[0].transform.position, other[0].transform.position
    rise = 0.0 if across_floor else (here.z - there.z) ** 2
    return ((here.x - there.x) ** 2 + (here.y - there.y) ** 2 + rise) ** 0.5


def _say(sentence: str, sets: dict, figures: dict) -> Composed:
    """The model's sentence with the engine's figures put into it.

    A hole naming nothing the steps produced leaves the plan unusable, which is
    how a sentence carrying a figure nobody worked out is stopped.
    """
    if not sentence.strip():
        raise CannotCompose("no sentence")
    used: dict[str, float] = {}

    def fill(found: re.Match) -> str:
        key, wording = found.group(1), found.group(2)
        if key in figures:
            used[key] = figures[key][0]
            if wording:
                return _whichever(figures[key], wording)
            return _read(*figures[key][:2])
        if key in sets and len(sets[key]) == 1:
            return sets[key][0].label
        raise CannotCompose(f"nothing worked out {key}")

    text = SLOT.sub(fill, sentence)
    if SLOT.search(sentence) is None:
        raise CannotCompose("a sentence with no figures in it is not an answer")
    return Composed(text=text, figures=used, regions=_once(sets))


def _once(sets: dict) -> tuple:
    """Every region a step touched, each named once however often it was used."""
    seen: dict = {}
    for found in sets.values():
        for node in found:
            seen[node.id] = None
    return tuple(seen)


def _whichever(figure: tuple[float, str], wording: str) -> str:
    """The wording the comparison found true, from the three the model wrote.

    "The same" is said of two figures within a centimetre, because a phone cannot
    tell them apart. When they did measure differently, that is said too: calling
    a table 81.1 cm tall and one 81.3 cm tall simply the same height hides a
    difference the scan recorded, however small.
    """
    choices = wording[1:].split("|")
    if figure[1] != VERDICT or len(choices) != 3:
        raise CannotCompose("wording needs a comparison and three ways to say it")
    if figure[0] > 0:
        return choices[0]
    if figure[0] < 0:
        return choices[2]
    return choices[1] + (CLOSER_THAN_THE_SCAN if figure[2] else "")


def _read(value: float, unit: str) -> str:
    """A figure as a person reads it, from the figure the engine holds."""
    if unit == SQUARE:
        return f"{value:.1f} square metres"
    if unit != METRES:
        return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"
    if abs(value) < 0.01:
        return "nothing"
    inches = to_inches(value)
    return f"{inches:.1f} inches" if abs(inches) < 36 else f"{inches / 12:.1f} feet"
