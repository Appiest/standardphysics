"""Finding 180 degree turns in a route, and measuring the three widths one needs.

ADA 2010 403.5.2 asks for more than a single number. Where a route turns 180
degrees around an element narrower than 48 inches, it wants 42 inches
approaching the turn, 48 at the turn, and 42 leaving it. The exception is that
none of it applies when the turn itself is 60 inches or wider.

So a turn has three measurements and a question about the thing being walked
around, and `route_clear_width` cannot express any of that.

**Where this approximates.** The rule says "an element which is less than 48
inches wide" without saying which of its dimensions counts. We report the
pivot's smaller horizontal extent, which is the conservative reading: it treats
more turns as in scope rather than fewer. Person C confirms the pivot and the
reading before a finding goes out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from standardphysics_contracts import SceneGraph, Vec3, to_inches, to_meters

from .footprints import footprint, gap_between
from .occupancy import Grid, blocks_floor

TURN_THRESHOLD_DEGREES = 120.0
"""Angular floor for a reversal.

This is deliberately well below 180. The widest path sweeps a generous arc
around a pivot rather than hugging it, so a genuine turn around a partition end
measures about 134 degrees at the scale where it reads most strongly. The
angle alone is a weak signal; `MAX_CHORD_RATIO` does the real separating."""

MIN_TURN_PATH = 2.5
"""Metres of route needed before looking for a turn at all.

403.5.2 measures approaching, at, and leaving the turn. A leg with less route
than that cannot supply the zones, and a short one trimmed at both ends leaves
a stub where noise reads as a reversal. Counter to Pickup is 1.6 m and produced
exactly that: a turn nobody takes, with an approach zone containing no route."""

ENDPOINT_TRIM = 0.9
"""Metres of route dropped at each end before looking for a turn."""

ZONE_LENGTH = to_meters(48.0)
"""How far either side of the turn counts as approaching and leaving."""

@dataclass
class Turn:
    """What a 180 degree turn measures.

    Measurements only. Whether they pass is Lane C's business: the thresholds
    in ADA 2010 403.5.2 live in the rule pack, with the quoted sentence and the
    name of the person who checked it against the source. A second copy here
    would be a copy nobody verified, free to drift from the one that was.
    """

    pivot_id: UUID | None
    pivot_width_inches: float | None
    approach_inches: float | None
    at_turn_inches: float | None
    leaving_inches: float | None
    apex: Vec3

    @property
    def fully_measured(self) -> bool:
        """Whether all three zones exist. A zone that ran off the end of the
        route is `None`, which is a question for the owner rather than a
        failure to report."""
        return None not in (
            self.approach_inches, self.at_turn_inches, self.leaving_inches
        )


def _resample(points: list[Vec3], spacing: float = 0.2) -> list[Vec3]:
    """Even out a path so direction estimates are not dominated by cell steps."""
    if len(points) < 2:
        return points
    kept = [points[0]]
    for point in points[1:]:
        if math.dist((point.x, point.y), (kept[-1].x, kept[-1].y)) >= spacing:
            kept.append(point)
    if kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


def _direction(a: Vec3, b: Vec3) -> tuple[float, float] | None:
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return None
    return dx / length, dy / length


def _angle_between(first: tuple[float, float], second: tuple[float, float]) -> float:
    dot = max(-1.0, min(1.0, first[0] * second[0] + first[1] * second[1]))
    return math.degrees(math.acos(dot))


LOOK_DISTANCES = (1.0, 1.5, 2.0, 2.75, 3.5, 4.5)
"""How far either side of a point to average direction over.

One distance is not enough, and the useful range is wider than it first looks.
A route squeezing around a narrow pivot reverses within a metre. The widest
path around a long partition does not: it takes the turn at a generous radius,
and the same turn measures 87 degrees at one metre, 108 at two, and 122 at
three and a half. Both are 180 degree turns to the person walking them, so the
detector tries several scales and keeps the strongest reversal.
"""

MAX_CHORD_RATIO = 0.65
"""How much a turn has to double back.

Straight-line distance between the start and end of the turn, over the distance
actually walked. A 180 collapses this toward zero. A square corner sits near
0.71, so this threshold excludes ordinary corners without needing the angle to
approach 180. A wide loop around a room also fails it, correctly."""


def find_turn(points: list[Vec3]) -> tuple[int, int] | None:
    """The stretch where the route comes back on itself.

    Summing per-step heading changes does not work here: a path found on a grid
    zigzags between cells, and those alternating deltas accumulate into a false
    180 on a straight corridor. This compares average direction before a point
    against average direction after it, at several scales, and keeps the
    strongest reversal that also doubles back.
    """
    if len(points) < 4:
        return None

    best = None
    for look in LOOK_DISTANCES:
        candidate = _best_reversal_at(points, look)
        if candidate and (best is None or candidate[0] > best[0]):
            best = candidate

    if best is None:
        return None
    return best[1], best[2]


def _best_reversal_at(points: list[Vec3], look: float):
    best = None
    for index in range(len(points)):
        before = _span_end(points, index, look, forward=False)
        after = _span_end(points, index, look, forward=True)
        if before is None or after is None:
            continue

        incoming = _direction(points[before], points[index])
        outgoing = _direction(points[index], points[after])
        if incoming is None or outgoing is None:
            continue

        reversal = _angle_between(incoming, outgoing)
        if reversal < TURN_THRESHOLD_DEGREES:
            continue
        if not _doubles_back(points, before, after):
            continue
        if best is None or reversal > best[0]:
            best = (reversal, before, after)
    return best


def _doubles_back(points: list[Vec3], before: int, after: int) -> bool:
    arc = sum(
        math.dist((points[i].x, points[i].y), (points[i + 1].x, points[i + 1].y))
        for i in range(before, after)
    )
    if arc <= 0:
        return False
    chord = math.dist(
        (points[before].x, points[before].y), (points[after].x, points[after].y)
    )
    return chord / arc < MAX_CHORD_RATIO


def _span_end(points: list[Vec3], anchor: int, length: float, forward: bool):
    """Index `length` metres along the path from `anchor`, or None if the path
    ends first."""
    step = 1 if forward else -1
    travelled = 0.0
    index = anchor
    while 0 <= index + step < len(points):
        nxt = index + step
        travelled += math.dist(
            (points[index].x, points[index].y), (points[nxt].x, points[nxt].y)
        )
        index = nxt
        if travelled >= length:
            return index
    return None


def _zone_width(
    grid: Grid, clearance: np.ndarray, points: list[Vec3]
) -> float | None:
    """Narrowest point in a zone, or None when the zone has nothing in it.

    A zone runs off the end of a short route and ends up empty. Reporting that
    as 0.0 inches turns "we could not measure this" into "this is impossibly
    tight", which is the worst way to be wrong: it reads as the most severe
    finding in the report.
    """
    if not points:
        return None
    widths = []
    for point in points:
        row, col = grid.to_cell(point.x, point.y)
        if grid.contains(row, col):
            widths.append(float(clearance[row, col]) * 2)
    return to_inches(min(widths)) if widths else None


def _slice_by_length(points: list[Vec3], anchor: int, length: float, forward: bool):
    step = 1 if forward else -1
    collected = []
    travelled = 0.0
    index = anchor
    while 0 <= index + step < len(points) and travelled < length:
        nxt = index + step
        travelled += math.dist(
            (points[index].x, points[index].y), (points[nxt].x, points[nxt].y)
        )
        collected.append(points[nxt])
        index = nxt
    return collected


def _pivot(graph: SceneGraph, apex: Vec3, search: float = 1.5):
    """The element the route bends around: whatever solid sits nearest the apex."""
    probe = [
        (apex.x - 0.01, apex.y - 0.01), (apex.x + 0.01, apex.y - 0.01),
        (apex.x + 0.01, apex.y + 0.01), (apex.x - 0.01, apex.y + 0.01),
    ]
    nearest, best = None, search
    for node in graph.nodes:
        if not blocks_floor(node):
            continue
        distance = gap_between(footprint(node), probe)
        if distance < best:
            nearest, best = node, distance
    if nearest is None:
        return None, None
    width = to_inches(min(nearest.dimensions.x, nearest.dimensions.y))
    return nearest.id, width


def trim_endpoints(path: list[Vec3], radius: float) -> list[Vec3]:
    """Drop the wandering tails at each end of a route.

    Inside the endpoint exemption every cell has the same effective clearance,
    so the search has no reason to prefer one neighbour over another and the
    retraced path can loop around near a stop. Harmless for measurement, which
    ignores those cells, but it looks exactly like a 180 to a turn detector.
    """
    if len(path) < 3:
        return path
    head, tail = path[0], path[-1]
    kept = [
        point
        for point in path
        if math.dist((point.x, point.y), (head.x, head.y)) > radius
        and math.dist((point.x, point.y), (tail.x, tail.y)) > radius
    ]
    return kept if len(kept) >= 4 else path


def _arc_length(points: list[Vec3]) -> float:
    return sum(
        math.dist((a.x, a.y), (b.x, b.y)) for a, b in zip(points, points[1:])
    )


def measure_turn(
    graph: SceneGraph, grid: Grid, clearance: np.ndarray, path: list[Vec3]
) -> Turn | None:
    trimmed = trim_endpoints(path, ENDPOINT_TRIM)
    if _arc_length(trimmed) < MIN_TURN_PATH:
        return None

    points = _resample(trimmed, spacing=0.3)
    span = find_turn(points)
    if span is None:
        return None

    start, end = span
    apex = points[(start + end) // 2]
    turn_points = points[start : end + 1]

    pivot_id, pivot_width = _pivot(graph, apex)
    return Turn(
        pivot_id=pivot_id,
        pivot_width_inches=pivot_width,
        approach_inches=_zone_width(
            grid, clearance, _slice_by_length(points, start, ZONE_LENGTH, forward=False)
        ),
        at_turn_inches=_zone_width(grid, clearance, turn_points),
        leaving_inches=_zone_width(
            grid, clearance, _slice_by_length(points, end, ZONE_LENGTH, forward=True)
        ),
        apex=apex,
    )
