"""Reading the shape of a route out of the stops it visits.

Two rules only apply in particular places: 403.5.2 at a 180 degree turn, and
304.3 in the rooms that call for a turning space. The first needs to know where
the route doubles back, which is a question about the route and not about any
rule; the second needs to know which stops are those rooms.

It is answered from the stops rather than from the measured path on purpose. A
path on a 25 mm grid detours around whatever is in the way, so the first half
metre of the leg out of a tight counter slot runs back the way the customer
came in. That makes every narrow approach look like a dead end. Net travel
between one stop and the next does not have that problem: a dead end is a place
you leave in the direction you arrived from, and that is a fact about the stops.
"""

from __future__ import annotations

import math
from itertools import pairwise

from standardphysics_contracts import Stop, Vec3, WidthResult

from ..scenario_suggestion import DESTINATIONS

REVERSAL_TOLERANCE_DEGREES = 45.0
"""How far from a straight about-face still counts as doubling back."""

FEET_PER_METER = 3.28084


def _unit(start: Vec3, end: Vec3) -> tuple[float, float] | None:
    dx, dy = end.x - start.x, end.y - start.y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    return dx / length, dy / length


def travel_heading(stops: list[Stop], leg_index: int) -> tuple[float, float] | None:
    """Which way the customer is going on this leg, start to finish."""
    if leg_index < 0 or leg_index + 1 >= len(stops):
        return None
    return _unit(stops[leg_index].position, stops[leg_index + 1].position)


def is_reversal(
    incoming: tuple[float, float] | None,
    outgoing: tuple[float, float] | None,
    tolerance_degrees: float = REVERSAL_TOLERANCE_DEGREES,
) -> bool:
    """True when leaving a stop means heading back where you came from."""
    if incoming is None or outgoing is None:
        return False
    alignment = -(incoming[0] * outgoing[0] + incoming[1] * outgoing[1])
    return alignment >= math.cos(math.radians(tolerance_degrees))


def reversal_stops(stops: list[Stop]) -> list[int]:
    """Stop indices the route doubles back out of.

    The first and last stops are doorways rather than dead ends, so a stop
    qualifies only when it has a leg on each side of it.
    """
    return [
        index
        for index in range(1, len(stops) - 1)
        if is_reversal(travel_heading(stops, index - 1), travel_heading(stops, index))
    ]


ROOMS_NEEDING_TURNING_SPACE = (*DESTINATIONS["restroom"][1], *DESTINATIONS["fitting_room"][1], "dressing")
"""Words naming a room whose own section requires a turning space: 603.2.1 for
toilet rooms, 803.2 for dressing and fitting rooms. 304 is scoped by the
sections that call for it, and nothing calls for it at a counter, a seat or the
end of an aisle in a sales area; the Access Board's guide to chapter 3 says so
in as many words."""


def stops_needing_turning_space(stops: list[Stop]) -> list[int]:
    """Stop indices at a room the Standards require a turning space in."""
    return [
        index
        for index, stop in enumerate(stops)
        if any(word in stop.name.casefold() for word in ROOMS_NEEDING_TURNING_SPACE)
    ]


def setback_point(stops: list[Stop], index: int, setback_meters: float) -> Vec3:
    """A stop, backed off toward where the customer arrived from.

    Measuring a turning circle hard against a counter fails every counter,
    because standing at one puts you within arm's reach of it. Backing off by
    the depth of a clear floor space measures the space you actually turn in,
    which is the convention Lane B uses for route width.
    """
    stop = stops[index].position
    heading = travel_heading(stops, index - 1)
    if heading is None:
        return stop
    return Vec3(
        x=stop.x - heading[0] * setback_meters,
        y=stop.y - heading[1] * setback_meters,
        z=stop.z,
    )


def path_length_meters(path: list[Vec3]) -> float:
    return sum(math.dist((a.x, a.y), (b.x, b.y)) for a, b in pairwise(path))


def route_length_feet(legs: list[WidthResult]) -> float:
    return sum(path_length_meters(leg.path) for leg in legs) * FEET_PER_METER


def sample_path(path: list[Vec3], spacing_meters: float) -> list[Vec3]:
    """Points along a path, no closer together than `spacing_meters`.

    Passing space asks whether a 60 inch square exists anywhere on the route.
    Testing every cell of a 25 mm path is wasted work, and testing only the
    stops would miss the open middle of the room.
    """
    if not path:
        return []
    kept = [path[0]]
    for point in path[1:]:
        last = kept[-1]
        if math.dist((last.x, last.y), (point.x, point.y)) >= spacing_meters:
            kept.append(point)
    if kept[-1] is not path[-1]:
        kept.append(path[-1])
    return kept
