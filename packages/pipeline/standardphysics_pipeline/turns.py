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

import itertools
import math
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from standardphysics_contracts import SceneGraph, SceneNode, Vec3, to_inches, to_meters

from .footprints import (
    closest_point,
    contains_point,
    footprint,
    gap_between,
    ray_distance,
    rotation_about_z,
)
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


MAX_ACROSS = 10.0
"""Metres a width is measured out to before the room is called open."""


@dataclass(frozen=True)
class _Across:
    """Widths across the route at a turn, measured out from the pivot.

    403.5.2's widths are the gaps the route runs through: between the pivot and
    the wall facing it along each leg, and between the pivot's end and whatever
    faces that at the turn. Twice the distance to the nearest obstacle is not
    that. Beside the pivot's end the nearest obstacle is the pivot's own corner,
    so a turn with 43 inch lanes and 49 inches at the turn read 0, 43 and 43,
    and a 61 inch turn read 35 and could never claim the 60 inch exception.

    So each width is a ray from the nearest point on the pivot, out through the
    route, to the first obstacle beyond it. The grid finds what the ray meets,
    which keeps a doorway cut through a wall open, and the obstacle's footprint
    gives the exact distance.
    """

    graph: SceneGraph
    grid: Grid
    clearance: np.ndarray
    pivot: SceneNode | None

    def _known_pivot(self) -> SceneNode:
        """The pivot, on the paths that only run once there is one."""
        assert self.pivot is not None
        return self.pivot

    def narrowest(self, points: list[Vec3]) -> float | None:
        """Narrowest point in a zone, or None when the zone has nothing in it.

        A zone runs off the end of a short route and ends up empty. Reporting
        that as 0.0 inches turns "we could not measure this" into "this is
        impossibly tight", which is the worst way to be wrong: it reads as the
        most severe finding in the report.
        """
        widths = [
            width for point in points if (width := self.width_at(point)) is not None
        ]
        return to_inches(min(widths)) if widths else None

    def off_the_end(self, end: _PivotEnd, apex: Vec3) -> float | None:
        """The width at the turn, straight off the end of the pivot.

        Measured along the pivot's axis that points at the turn, from the
        pivot's nearest point to it. A ray through the apex instead runs
        diagonally off the pivot's corner whenever the route swings wide.
        """
        origin = closest_point(footprint(self._known_pivot()), (apex.x, apex.y))
        metres = self._across_from(origin, end.axis)
        return None if metres is None else to_inches(metres)

    def width_at(self, point: Vec3) -> float | None:
        if self.pivot is None:
            return self._twice_the_clearance(point)
        origin = closest_point(footprint(self.pivot), (point.x, point.y))
        direction = _unit(point.x - origin[0], point.y - origin[1])
        if direction is None:
            return self._twice_the_clearance(point)
        return self._across_from(origin, direction)

    def _across_from(self, origin, direction) -> float | None:
        """Metres along a ray from the pivot to the first obstacle beyond it."""
        hit = self._first_hit(origin, direction)
        if hit is None:
            return None
        cell, travelled = hit
        owner = self.grid.owner_at(*cell)
        if owner is None:
            return travelled
        exact = ray_distance(origin, direction, footprint(self.graph.by_id(owner)))
        return travelled if exact is None else exact

    def _first_hit(self, origin, direction):
        """The first occupied cell along the ray that is not the pivot, and
        how far along the ray it lies."""
        outline = footprint(self._known_pivot())
        step = self.grid.cell_size / 2
        travelled = 0.0
        while travelled < MAX_ACROSS:
            travelled += step
            point = (origin[0] + direction[0] * travelled, origin[1] + direction[1] * travelled)
            cell = self.grid.to_cell(*point)
            if not self.grid.contains(*cell):
                return None
            if self.grid.occupied[cell] and not self._is_pivot(cell, point, outline):
                return cell, travelled
        return None

    def _is_pivot(self, cell, point, outline) -> bool:
        return self.grid.owner_at(*cell) == self._known_pivot().id or contains_point(
            outline, point, self.grid.cell_size
        )

    def _twice_the_clearance(self, point: Vec3) -> float | None:
        row, col = self.grid.to_cell(point.x, point.y)
        if not self.grid.contains(row, col):
            return None
        return float(self.clearance[row, col]) * 2


@dataclass(frozen=True)
class _PivotEnd:
    """The end of the pivot the route turns around.

    `axis` runs along the pivot's length toward the turn, and `reach` is how
    far along it the pivot extends. Route beyond `reach` is the turn; route
    short of it runs beside the pivot, which is where approaching and leaving
    are measured.
    """

    axis: tuple[float, float]
    reach: float

    @classmethod
    def facing(cls, pivot: SceneNode, apex: Vec3) -> _PivotEnd | None:
        """The end the apex lies beyond, or None when it lies beside the pivot.

        403.5.2's turn goes along one side of an element, round its end and
        back along the other, so the end is across the element's width and the
        apex lies past it along its length. A route that doubles back beside
        the long side of a display case has not gone round anything, however
        much its direction reverses: the widest path does that when it wanders
        north of an aisle and comes back.
        """
        centre = pivot.transform.position
        cos_t, sin_t = rotation_about_z(pivot)
        offset = (apex.x - centre.x, apex.y - centre.y)
        lengthwise = [
            (axis, half)
            for axis, half, across in (
                ((cos_t, sin_t), pivot.dimensions.x / 2, pivot.dimensions.y / 2),
                ((-sin_t, cos_t), pivot.dimensions.y / 2, pivot.dimensions.x / 2),
            )
            if half >= across
        ]
        beyond = [
            (abs(projected) - half, (axis[0] * math.copysign(1, projected), axis[1] * math.copysign(1, projected)))
            for axis, half in lengthwise
            if abs(projected := axis[0] * offset[0] + axis[1] * offset[1]) > half
        ]
        if not beyond:
            return None
        _, axis = max(beyond)
        reach = max(axis[0] * x + axis[1] * y for x, y in footprint(pivot))
        return cls(axis, reach)

    def beside(self, point: Vec3) -> bool:
        return self.axis[0] * point.x + self.axis[1] * point.y <= self.reach


def _zone_beside(points: list[Vec3], apex_index: int, forward: bool, end: _PivotEnd):
    """`ZONE_LENGTH` of route alongside the pivot, from where the route comes
    back beside it after rounding the end.

    The turn detector's span is the stretch whose direction reverses, and on a
    long partition it can start at the route's first point, which left nothing
    before it to call the approach.
    """
    step = 1 if forward else -1
    index = apex_index
    collected: list[Vec3] = []
    travelled = 0.0
    while 0 <= index + step < len(points) and travelled < ZONE_LENGTH:
        nxt = index + step
        if end.beside(points[nxt]):
            if collected:
                travelled += math.dist((points[index].x, points[index].y), (points[nxt].x, points[nxt].y))
            collected.append(points[nxt])
        elif collected:
            break
        index = nxt
    return collected


def _unit(dx: float, dy: float) -> tuple[float, float] | None:
    length = math.hypot(dx, dy)
    return None if length < 1e-9 else (dx / length, dy / length)


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


def _solids_near(graph: SceneGraph, apex: Vec3, search: float = 1.5) -> list[SceneNode]:
    """Every solid within `search` metres of the apex, nearest first."""
    probe = [
        (apex.x - 0.01, apex.y - 0.01), (apex.x + 0.01, apex.y - 0.01),
        (apex.x + 0.01, apex.y + 0.01), (apex.x - 0.01, apex.y + 0.01),
    ]
    near = [
        (distance, index, node)
        for index, node in enumerate(graph.nodes)
        if blocks_floor(node) and (distance := gap_between(footprint(node), probe)) < search
    ]
    return [node for _, _, node in sorted(near, key=lambda each: each[:2])]


def _pivot(nearby: list[SceneNode], apex: Vec3) -> tuple[SceneNode, _PivotEnd] | None:
    """The element the route turns around: the nearest one whose end the apex
    lies beyond.

    Nearest alone is not it. Coming round the end of a shelf, the route runs
    between the shelf and a display case, and the case can sit nearer the
    apex than the shelf does.
    """
    for node in nearby:
        end = _PivotEnd.facing(node, apex)
        if end is not None:
            return node, end
    return None


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
        math.dist((a.x, a.y), (b.x, b.y)) for a, b in itertools.pairwise(points)
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
    apex_index = (start + end) // 2
    apex = points[apex_index]
    nearby = _solids_near(graph, apex)
    if not nearby:
        across = _Across(graph, grid, clearance, None)
        return Turn(
            pivot_id=None,
            pivot_width_inches=None,
            approach_inches=across.narrowest(
                _slice_by_length(points, start, ZONE_LENGTH, forward=False)
            ),
            at_turn_inches=across.narrowest(points[start : end + 1]),
            leaving_inches=across.narrowest(
                _slice_by_length(points, end, ZONE_LENGTH, forward=True)
            ),
            apex=apex,
        )

    found = _pivot(nearby, apex)
    if found is None:
        return None
    pivot, pivot_end = found
    across = _Across(graph, grid, clearance, pivot)
    return Turn(
        pivot_id=pivot.id,
        pivot_width_inches=to_inches(min(pivot.dimensions.x, pivot.dimensions.y)),
        approach_inches=across.narrowest(
            _zone_beside(points, apex_index, forward=False, end=pivot_end)
        ),
        at_turn_inches=across.off_the_end(pivot_end, apex),
        leaving_inches=across.narrowest(
            _zone_beside(points, apex_index, forward=True, end=pivot_end)
        ),
        apex=apex,
    )
