"""One room and one routine, built from knobs, run through the same evaluator.

The labelled cases in `dataset.py` fix their geometry, because a score only
means the same thing twice if the room did not move between readings. This is
the other way round: the room is whatever the knobs say, and the point is to
watch a check change its mind while one knob moves.

Nothing here scores a room. `run_knobs` builds a `Case` and hands it to
`run_case`, the same function the Weave evaluation and the W&B grid call, so a
finding seen while dragging a slider is the finding the server would report for
that room.

A knob that sets a dimension also says what a correct measurement of it is.
Setting the aisle to 31 in means the tightest leg of a route that starts inside
the door should come back at 31 in, and `measurement_error_in` reads it that
way. Where a knob stops pinning a dimension it claims nothing: walking in from
the street puts the doorway on the route, so the aisle is no longer the
tightest thing on it and the route carries no expected measurement.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

from standardphysics_contracts import Finding, Scenario, SceneGraph
from standardphysics_contracts.rules import Tier
from standardphysics_fixtures import build_graph, build_scenario
from standardphysics_fixtures.shop import (
    COUNTER_HEIGHT_INCHES,
    build_street_scenario,
)

from ..rules import load_pack
from . import variants as v
from .configuration import Setup, ledger, measurements, router, setup
from .dataset import DOORWAY_INCHES, ROUTE, SCAN_CANNOT_SEE, Case
from .runner import run_case
from .scorers import SCORERS, CaseOutcome

FIXTURE_AISLE_INCHES = 31.0
"""The gap the shipped fixture leaves between its display cases."""


@dataclass(frozen=True)
class Routine:
    """One trip through the shop, and whether the front door is on it."""

    id: str
    name: str
    build: Callable[[], Scenario]
    crosses_the_doorway: bool = False


ROUTINES: dict[str, Routine] = {
    routine.id: routine
    for routine in (
        Routine("drink", "Order a drink", build_scenario),
        Routine(
            "street",
            "Walk in from the street",
            build_street_scenario,
            crosses_the_doorway=True,
        ),
    )
}


def routine_named(routine_id: str) -> Routine:
    found = ROUTINES.get(routine_id)
    if found is None:
        raise KeyError(f"no routine named {routine_id}")
    return found


@dataclass(frozen=True)
class Knobs:
    """The shop as the sliders leave it. Defaults are the fixture as shipped."""

    routine: str = "drink"
    aisle_inches: float = FIXTURE_AISLE_INCHES
    counter_inches: float = COUNTER_HEIGHT_INCHES
    door_inches: float = DOORWAY_INCHES
    counter_side_seating: bool = True
    max_tier: Tier = 1


PINS: dict[str, str] = {
    "aisle_inches": ROUTE,
    "counter_inches": "service_counter_height",
}
"""Which check answers each knob that sets a dimension.

Read one way it says what a sweep of that knob should move. Read the other way
it is where the expected measurements on the case come from.

The doorway knob is absent on purpose. It sets the opening in the wall, and
404.2.3 is about the clear width with the door open, which is always smaller.
The check reports no measurement for that reason, so scoring one against this
knob would score the opening as though it were the clear width.
"""


def room(knobs: Knobs) -> tuple[SceneGraph, Scenario]:
    """The fixture shop with the knobs applied, and the routine to walk."""
    graph = build_graph()
    if not knobs.counter_side_seating:
        graph = v.clear_counter_side(graph)
    graph = v.aisle(graph, knobs.aisle_inches)
    graph = v.counter_height(graph, knobs.counter_inches)
    graph = v.door_width(graph, knobs.door_inches)
    return graph, routine_named(knobs.routine).build()


def expected_inches(knobs: Knobs) -> dict[str, float]:
    """The dimensions these knobs pin, so a measurement of them can be scored."""
    pinned = {check: float(getattr(knobs, knob)) for knob, check in PINS.items()}
    if routine_named(knobs.routine).crosses_the_doorway:
        pinned.pop(ROUTE)
    return pinned


def describe(knobs: Knobs) -> str:
    seating = (
        "seating by the counter"
        if knobs.counter_side_seating
        else "the counter side clear"
    )
    return (
        f"{knobs.aisle_inches:g} in between the cases, "
        f"a {knobs.counter_inches:g} in counter, "
        f"a {knobs.door_inches:g} in doorway and {seating}, "
        f"walked as {routine_named(knobs.routine).name.lower()}."
    )


def case_id(knobs: Knobs) -> str:
    return (
        f"knobs-{knobs.routine}"
        f"-aisle{knobs.aisle_inches:g}"
        f"-counter{knobs.counter_inches:g}"
        f"-door{knobs.door_inches:g}"
        f"-{'seated' if knobs.counter_side_seating else 'cleared'}"
    )


def as_case(knobs: Knobs) -> Case:
    """These knobs as a case the runner and the scorers already understand."""
    graph, scenario = room(knobs)
    return Case(
        id=case_id(knobs),
        description=describe(knobs),
        graph=graph,
        scenario=scenario,
        expected_questions=SCAN_CANNOT_SEE,
        expected_inches=expected_inches(knobs),
        max_tier=knobs.max_tier,
    )


LABELLED_SCORERS = ("measurement_error_in", "question_recall", "label_accuracy")
"""The scorers these knobs give an answer for.

The knobs pin dimensions, and every shop is asked the same things a scan
cannot see, so those three can be read. The knobs do not say which checks
ought to fail. That is what a labelled case in `dataset.py` is for, and reading
precision against a room nobody labelled would score a missing label as a
wrong answer.
"""


def scores(outcome: CaseOutcome) -> dict[str, float | None]:
    return {name: SCORERS[name](outcome) for name in LABELLED_SCORERS}


def run_knobs(knobs: Knobs, configuration: Setup | None = None) -> CaseOutcome:
    """One room through `run_case`, the function the evaluation grid calls."""
    picked = configuration or setup()
    return run_case(
        as_case(knobs),
        measurements(picked.measurements),
        load_pack(),
        ledger(picked.preview_unverified),
        router(picked.router),
        picked.run_fixes,
        fix_candidates=picked.fix_candidates,
    )


def sweep_knob(
    knobs: Knobs,
    knob: str,
    values: Iterable[float],
    configuration: Setup | None = None,
) -> list[tuple[Knobs, CaseOutcome]]:
    """The same room at every value of one knob, everything else held still."""
    if knob not in PINS:
        raise KeyError(f"{knob} does not set a dimension a check can answer")
    moved = [replace(knobs, **{knob: float(value)}) for value in values]
    return [(each, run_knobs(each, configuration)) for each in moved]


@dataclass(frozen=True)
class Reading:
    """What one check said about one room, at one setting of one knob."""

    knob_inches: float
    outcome: str
    measured_inches: float | None
    required_inches: float | None


NOT_MEASURED = "not measured"
"""A check that had nothing to answer with. It is not a pass and not a
problem, and a chart that drew it as either would be inventing a result."""


def reading(outcome: CaseOutcome, knobs: Knobs, knob: str) -> Reading:
    """What the check behind one knob said about the room that knob built."""
    pinned = float(getattr(knobs, knob))
    decided = _decided(
        [f for f in outcome.result.findings if f.check_id == PINS[knob]], pinned
    )
    if decided is None:
        return Reading(pinned, NOT_MEASURED, None, None)
    return Reading(
        pinned, decided.outcome, decided.measured_inches, decided.required_inches
    )


def _decided(findings: list[Finding], pinned: float) -> Finding | None:
    """The finding that settled the check.

    A check can report a measurement per leg of the routine. A problem settles
    it, and where nothing failed the leg nearest the dimension the knob set is
    the one the knob is about.
    """
    problems = [f for f in findings if f.outcome == "problem"]
    if problems:
        return min(problems, key=lambda f: f.measured_inches or 0.0)
    measured = [f for f in findings if f.measured_inches is not None]
    if not measured:
        return findings[0] if findings else None
    return min(measured, key=lambda f: abs((f.measured_inches or 0.0) - pinned))


def readings(
    swept: list[tuple[Knobs, CaseOutcome]], knob: str
) -> list[Reading]:
    """One row per value the sweep visited, for a chart to draw."""
    return [reading(outcome, each, knob) for each, outcome in swept]


__all__ = [
    "FIXTURE_AISLE_INCHES", "LABELLED_SCORERS", "NOT_MEASURED", "PINS",
    "ROUTINES", "Knobs", "Reading", "Routine", "as_case", "case_id",
    "describe", "expected_inches", "reading", "readings", "room",
    "routine_named", "run_knobs", "scores", "sweep_knob",
]
