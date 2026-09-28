"""Exact distances between the things people squeeze between.

The occupancy grid is how we find a route and identify what pinches it. It is
not how we report the number: at 25 mm cells the answer carries about an inch
of quantisation, which is enough to pass a 34 inch gap as 36.

So the grid finds the pinch and names the two objects, and the width we report
is the exact distance between those two footprints.
"""

from __future__ import annotations

import math

from standardphysics_contracts import SceneNode

Point = tuple[float, float]
Polygon = list[Point]

DEGENERATE_AREA = 1e-9
"""Square metres below which an outline is a point or a line, not a region."""


def rotation_about_z(node: SceneNode) -> tuple[float, float]:
    """Cosine and sine of the node's rotation about Z, from its transform."""
    m = node.transform.m
    scale = math.hypot(m[0], m[4])
    if scale == 0:
        return 1.0, 0.0
    return m[0] / scale, m[4] / scale


def footprint(node: SceneNode) -> Polygon:
    """The node's floor rectangle, rotated about Z, in world coordinates."""
    return sized_footprint(node, node.dimensions.x, node.dimensions.y)


def sized_footprint(node: SceneNode, size_x: float, size_y: float) -> Polygon:
    """The node's floor rectangle at another size, about the same centre and turned the same way."""
    cos_t, sin_t = rotation_about_z(node)
    centre = node.transform.position
    half_x, half_y = size_x / 2, size_y / 2
    corners = [(-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)]
    return [
        (
            centre.x + local_x * cos_t - local_y * sin_t,
            centre.y + local_x * sin_t + local_y * cos_t,
        )
        for local_x, local_y in corners
    ]


def floor_polygon(node: SceneNode) -> Polygon:
    """The horizontal projection of all eight corners of a floor node.

    RoomPlan floors are frequently zero-depth shells whose local X/Z plane is
    rotated into the world's X/Y plane. Their dimensions alone therefore do
    not say where customers can stand. The measured transform remains the
    authority; this derives a convex ground polygon from it.
    """
    half_x, half_y, half_z = node.dimensions.x / 2, node.dimensions.y / 2, node.dimensions.z / 2
    m = node.transform.m
    points = []
    for local_x in (-half_x, half_x):
        for local_y in (-half_y, half_y):
            for local_z in (-half_z, half_z):
                points.append((
                    m[0] * local_x + m[1] * local_y + m[2] * local_z + m[3],
                    m[4] * local_x + m[5] * local_y + m[6] * local_z + m[7],
                ))
    return convex_hull(points)


def polygon_bounds(polygon: Polygon) -> tuple[float, float, float, float]:
    return (
        min(x for x, _ in polygon), min(y for _, y in polygon),
        max(x for x, _ in polygon), max(y for _, y in polygon),
    )


def contains_point(polygon: Polygon, point: Point, margin: float = 0.0) -> bool:
    """Whether a point is inside a convex polygon, allowing a small edge slack.

    An outline with no area contains nothing. Every edge of one lies on a
    single point or line, so each edge test passes for every point in the
    room, and a photo candidate given no size stood over the whole floor.
    """
    area = signed_area(polygon) if len(polygon) >= 3 else 0.0
    if abs(area) <= DEGENERATE_AREA:
        return False
    direction = 1 if area >= 0 else -1
    x, y = point
    for start, end in _edges(polygon):
        edge_x, edge_y = end[0] - start[0], end[1] - start[1]
        cross = edge_x * (y - start[1]) - edge_y * (x - start[0])
        if direction * cross < -margin * math.hypot(edge_x, edge_y):
            return False
    return True


def distance_outside(polygon: Polygon, point: Point, margin: float = 0.0) -> float:
    """How far a point sits beyond a convex polygon's edge, or zero inside it."""
    if len(polygon) < 3 or contains_point(polygon, point, margin):
        return 0.0
    return min(_point_to_segment(point, start, end) for start, end in _edges(polygon))


def convex_hull(points: list[Point]) -> Polygon:
    """Monotone-chain hull keeps a floor boundary ordered without an AABB."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def turn(origin: Point, first: Point, second: Point) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower: Polygon = []
    for point in unique:
        while len(lower) >= 2 and turn(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: Polygon = []
    for point in reversed(unique):
        while len(upper) >= 2 and turn(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def signed_area(polygon: Polygon) -> float:
    """Positive when the outline runs anticlockwise, as `convex_hull` returns it."""
    return sum(start[0] * end[1] - end[0] * start[1] for start, end in _edges(polygon)) / 2


def convex_intersection(subject: Polygon, clip: Polygon) -> Polygon:
    """The overlap of two anticlockwise convex outlines, by clipping one edge at a time."""
    if len(subject) < 3 or len(clip) < 3:
        return []
    kept = subject
    for start, end in _edges(clip):
        kept = _keep_left_of(kept, start, end)
        if not kept:
            return []
    return kept


def _keep_left_of(polygon: Polygon, start: Point, end: Point) -> Polygon:
    def side(point: Point) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])

    kept: Polygon = []
    for current, following in _edges(polygon):
        here, there = side(current), side(following)
        if here >= 0:
            kept.append(current)
        if (here >= 0) != (there >= 0):
            t = here / (here - there)
            kept.append((current[0] + t * (following[0] - current[0]), current[1] + t * (following[1] - current[1])))
    return kept


def _point_to_segment(point: Point, a: Point, b: Point) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _edges(polygon: Polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def _separated(a: Polygon, b: Polygon) -> bool:
    """Separating axis test over both rectangles' edge normals."""
    for polygon in (a, b):
        for start, end in _edges(polygon):
            axis = (-(end[1] - start[1]), end[0] - start[0])
            a_projected = [axis[0] * x + axis[1] * y for x, y in a]
            b_projected = [axis[0] * x + axis[1] * y for x, y in b]
            if max(a_projected) < min(b_projected) or max(b_projected) < min(a_projected):
                return True
    return False


def gap_between(a: Polygon, b: Polygon) -> float:
    """Shortest distance between two convex footprints. Zero if they touch."""
    if not _separated(a, b):
        return 0.0
    distances = []
    for point in a:
        distances += [_point_to_segment(point, s, e) for s, e in _edges(b)]
    for point in b:
        distances += [_point_to_segment(point, s, e) for s, e in _edges(a)]
    return min(distances)


BOUNDS_SLACK = 1e-6
"""A micrometre, far above rounding at room scale, so a box-only answer never disagrees with `gap_between`."""


def surely_apart(a: Polygon, b: Polygon, by: float = 0.0) -> bool:
    """Whether the bounding boxes alone show `gap_between(a, b) > by`, without measuring the shapes.

    Two shapes are at least as far apart as their bounding boxes, so boxes
    further apart than `by` settle it. Most pairs in a room are nowhere near
    each other, and this spares them the exact distance.
    """
    a_x, a_y = [x for x, _ in a], [y for _, y in a]
    b_x, b_y = [x for x, _ in b], [y for _, y in b]
    box_gap = max(min(b_x) - max(a_x), min(a_x) - max(b_x), min(b_y) - max(a_y), min(a_y) - max(b_y))
    return box_gap > by + BOUNDS_SLACK


def bounds_meet(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    """Whether two `polygon_bounds` boxes touch or overlap."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def touching(a: Polygon, b: Polygon) -> bool:
    """Whether two convex footprints touch or overlap: `gap_between(a, b) == 0`, without measuring the gap."""
    return bounds_meet(polygon_bounds(a), polygon_bounds(b)) and not _separated(a, b)


def closer_than(a: Polygon, b: Polygon, distance: float) -> bool:
    """`gap_between(a, b) < distance`, answered from the bounding boxes when they are clearly further apart."""
    return not surely_apart(a, b, distance) and gap_between(a, b) < distance


def gap_between_nodes(a: SceneNode, b: SceneNode) -> float:
    return gap_between(footprint(a), footprint(b))


def _closest_on_segment(point: Point, a: Point, b: Point) -> Point:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return a
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return (ax + t * dx, ay + t * dy)


def closest_points(a: Polygon, b: Polygon) -> tuple[Point, Point]:
    """The two points a dimension line should run between.

    Measuring a gap is not enough to draw it. The label has to sit across the
    actual facing surfaces, so this returns where on each footprint the
    shortest distance is realised.
    """
    best = None
    for point in a:
        candidate = _closest_on_segment(point, *_nearest_edge(point, b))
        best = _better(best, point, candidate)
    for point in b:
        candidate = _closest_on_segment(point, *_nearest_edge(point, a))
        best = _better(best, candidate, point)
    assert best is not None
    return best[1], best[2]


def _nearest_edge(point: Point, polygon: Polygon) -> tuple[Point, Point]:
    return min(_edges(polygon), key=lambda edge: _point_to_segment(point, *edge))


def _better(best, first: Point, second: Point):
    distance = math.hypot(first[0] - second[0], first[1] - second[1])
    if best is None or distance < best[0]:
        return (distance, first, second)
    return best


def closest_point(polygon: Polygon, point: Point) -> Point:
    """Where on a footprint's outline the given point is nearest."""
    return _closest_on_segment(point, *_nearest_edge(point, polygon))


def ray_distance(origin: Point, direction: Point, polygon: Polygon) -> float | None:
    """How far along a unit ray it first meets a footprint's outline, or None
    when it never does."""
    hits = [
        t
        for start, end in _edges(polygon)
        if (t := _ray_meets_segment(origin, direction, start, end)) is not None
    ]
    return min(hits) if hits else None


def _ray_meets_segment(origin: Point, direction: Point, a: Point, b: Point) -> float | None:
    edge = (b[0] - a[0], b[1] - a[1])
    denominator = direction[0] * edge[1] - direction[1] * edge[0]
    if abs(denominator) < 1e-12:
        return None
    offset = (a[0] - origin[0], a[1] - origin[1])
    along_ray = (offset[0] * edge[1] - offset[1] * edge[0]) / denominator
    along_edge = (offset[0] * direction[1] - offset[1] * direction[0]) / denominator
    if along_ray < 0 or not 0 <= along_edge <= 1:
        return None
    return along_ray
