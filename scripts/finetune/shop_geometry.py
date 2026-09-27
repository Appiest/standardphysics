"""Plane geometry shared by the shop generator: boxes, wall faces, outlines, transforms."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from standardphysics_contracts import Mat4
from standardphysics_pipeline.footprints import Polygon

EDGE = 1e-3
Point = tuple[float, float]


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    d: float
    heading: float = 0.0

    def corners(self, grow: float = 0.0) -> Polygon:
        c, s = math.cos(math.radians(self.heading)), math.sin(math.radians(self.heading))
        hw, hd = self.w / 2 + grow, self.d / 2 + grow
        return [(self.cx + x * c - y * s, self.cy + x * s + y * c)
                for x, y in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]

    @property
    def centre(self) -> Point:
        return self.cx, self.cy

    def to_local(self, point: Point) -> Point:
        c, s = math.cos(math.radians(self.heading)), math.sin(math.radians(self.heading))
        dx, dy = point[0] - self.cx, point[1] - self.cy
        return dx * c + dy * s, -dx * s + dy * c

    def distance_to(self, point: Point) -> float:
        lx, ly = self.to_local(point)
        return math.hypot(max(abs(lx) - self.w / 2, 0.0), max(abs(ly) - self.d / 2, 0.0))


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def inside(self, x: float, y: float) -> bool:
        return self.x0 + EDGE < x < self.x1 - EDGE and self.y0 + EDGE < y < self.y1 - EDGE

    def polygon(self) -> Polygon:
        return [(self.x0, self.y0), (self.x1, self.y0), (self.x1, self.y1), (self.x0, self.y1)]

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def intersect(self, other: Rect) -> Rect | None:
        made = Rect(max(self.x0, other.x0), max(self.y0, other.y0), min(self.x1, other.x1), min(self.y1, other.y1))
        return made if made.x1 > made.x0 and made.y1 > made.y0 else None


@dataclass(frozen=True)
class Wall:
    """An inner wall face from a to b, with the room on its left."""

    a: Point
    b: Point

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def heading(self) -> float:
        return math.degrees(math.atan2(self.b[1] - self.a[1], self.b[0] - self.a[0]))

    def point(self, t: float, out: float = 0.0) -> Point:
        ux, uy = (self.b[0] - self.a[0]) / self.length, (self.b[1] - self.a[1]) / self.length
        return (self.a[0] + ux * t - uy * out, self.a[1] + uy * t + ux * out)

    def reversed(self) -> Wall:
        return Wall(self.b, self.a)

    def distance_to(self, point: Point) -> float:
        return segment_distance(point, self.a, self.b)


def segment_distance(point: Point, a: Point, b: Point) -> float:
    (px, py), (ax, ay), (bx, by) = point, a, b
    length = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length))
    return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))


def _inside_any(rects: list[Rect], x: float, y: float) -> bool:
    return any(rect.inside(x, y) for rect in rects)


def _edges(rect: Rect) -> list[Wall]:
    """Counter-clockwise, so the room is on each edge's left."""
    corners = rect.polygon()
    return [Wall(corners[i], corners[(i + 1) % 4]) for i in range(4)]


def _split(edge: Wall, cuts: list[float]) -> list[Wall]:
    horizontal = edge.a[1] == edge.b[1]
    lo, hi = sorted((edge.a[0], edge.b[0]) if horizontal else (edge.a[1], edge.b[1]))
    points = sorted({lo, hi, *[c for c in cuts if lo < c < hi]})
    if (edge.b[0] if horizontal else edge.b[1]) < (edge.a[0] if horizontal else edge.a[1]):
        points.reverse()
    fixed = edge.a[1] if horizontal else edge.a[0]
    as_point: Callable[[float], Point] = (lambda v: (v, fixed)) if horizontal else (lambda v: (fixed, v))
    return [Wall(as_point(p), as_point(q)) for p, q in zip(points, points[1:])]


def _merge(walls: list[Wall]) -> list[Wall]:
    merged: list[Wall] = []
    for wall in walls:
        last = merged[-1] if merged else None
        if last and last.b == wall.a and abs(last.heading - wall.heading) < 1e-6:
            merged[-1] = Wall(last.a, wall.b)
        else:
            merged.append(wall)
    return merged


def outline(rects: list[Rect]) -> list[Wall]:
    """The wall faces around a union of rectangles, dropping the seams between them."""
    xs = [v for r in rects for v in (r.x0, r.x1)]
    ys = [v for r in rects for v in (r.y0, r.y1)]
    walls = []
    for rect in rects:
        for edge in _edges(rect):
            for piece in _split(edge, xs if edge.a[1] == edge.b[1] else ys):
                mx, my = piece.point(piece.length / 2, -0.01)
                if not _inside_any(rects, mx, my):
                    walls.append(piece)
    return _merge(walls)


def clip_segment(a: Point, b: Point, rect: Rect) -> tuple[Point, Point] | None:
    """The part of segment ab inside the rectangle (Liang-Barsky), or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    low, high = 0.0, 1.0
    for p, q in ((-dx, a[0] - rect.x0), (dx, rect.x1 - a[0]), (-dy, a[1] - rect.y0), (dy, rect.y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            low = max(low, r)
        else:
            high = min(high, r)
    if low >= high:
        return None
    return (a[0] + low * dx, a[1] + low * dy), (a[0] + high * dx, a[1] + high * dy)


def transform(x: float, y: float, z: float, heading: float) -> Mat4:
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    return Mat4(m=[c, -s, 0, x, s, c, 0, y, 0, 0, 1, z, 0, 0, 0, 1])


def turned(m: list[float], turn: float, shift: tuple[float, float, float]) -> list[float]:
    """A full 4x4 transform turned about the vertical axis through the origin, then shifted.

    Scan nodes carry rotations that tip local axes out of the floor plane, so
    this multiplies the whole 3x3 rather than only its plan part.
    """
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    rows = [m[0:4], m[4:8], m[8:12]]
    top = [c * rows[0][i] - s * rows[1][i] for i in range(4)]
    middle = [s * rows[0][i] + c * rows[1][i] for i in range(4)]
    bottom = list(rows[2])
    top[3] += shift[0]
    middle[3] += shift[1]
    bottom[3] += shift[2]
    return [*top, *middle, *bottom, 0.0, 0.0, 0.0, 1.0]
