"""Turning a measurement into something the viewer can fly to and draw.

A number on its own does not help anyone. `31 in` matters when the camera is
looking at the actual gap, the two objects causing it are lit up, and the
measurement is drawn across the space it describes. This builds that.

Lane C attaches a `Locus` to each finding. Lane D's viewer reads `camera` to
tween, `node_ids` to highlight, and `annotation` to draw.
"""

from __future__ import annotations

import math

from standardphysics_contracts import (
    Annotation,
    CameraPose,
    ClearFloorResult,
    HeightResult,
    Locus,
    SceneGraph,
    SceneNode,
    Vec3,
    WidthResult,
    lies_flat,
    to_meters,
)

from .footprints import closest_points, floor_polygon, footprint, rotation_about_z

EYE_PITCH_DEGREES = 55.0
"""How far above the floor the camera sits, in degrees from horizontal.

Steep, because these are interiors. A low angle puts the near wall between the
camera and the subject and spends half the frame on the outside of the
building. From 55 degrees the camera clears a three metre wall and looks down
into the room."""

FRAMING_MARGIN = 1.15
"""Air around the subject. Tight enough to keep the camera indoors."""

DEFAULT_FOV = 65.0
"""A wide lens, so framing a three metre subject does not push the camera ten
metres back and out through the wall behind it."""

MIN_CAMERA_DISTANCE = 1.2
MIN_EYE_HEIGHT = 1.1


def format_inches(value: float) -> str:
    """`31 in`, not `31.000000 in` and not `0.7874 m`."""
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded} in"
    return f"{value:.1f} in"


def _midpoint(a: Vec3, b: Vec3) -> Vec3:
    return Vec3(x=(a.x + b.x) / 2, y=(a.y + b.y) / 2, z=(a.z + b.z) / 2)


def _perpendicular(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length == 0:
        return 1.0, 0.0
    return -dy / length, dx / length


def _viewing_side(
    subject: Vec3, across: tuple[float, float], approach: Vec3 | None
) -> tuple[float, float]:
    """Look from where the customer comes from, when we know."""
    if approach is None:
        return across
    toward = (approach.x - subject.x, approach.y - subject.y)
    if toward[0] * across[0] + toward[1] * across[1] < 0:
        return (-across[0], -across[1])
    return across


def _standing_room(graph: SceneGraph, subject: Vec3) -> Vec3 | None:
    """Somewhere inside the room to look from, for a measurement no route reaches.

    A doorway has no customer path leading to it, so nothing said which side to
    stand on, and half the time the camera was put outside the building looking
    at the back of a wall. The middle of the floor is always somewhere a person
    could stand and see in.
    """
    floor = next((node for node in graph.nodes if lies_flat(node)), None)
    if floor is None:
        return None
    polygon = floor_polygon(floor)
    if not polygon:
        return None
    return Vec3(
        x=sum(point[0] for point in polygon) / len(polygon),
        y=sum(point[1] for point in polygon) / len(polygon),
        z=subject.z,
    )


def camera_for(
    subject: Vec3,
    radius: float,
    view_direction: tuple[float, float],
    fov_degrees: float = DEFAULT_FOV,
) -> CameraPose:
    """Frame a sphere of `radius` around `subject` from the given direction."""
    half_fov = math.radians(fov_degrees) / 2
    distance = max(radius * FRAMING_MARGIN / math.tan(half_fov), MIN_CAMERA_DISTANCE)
    pitch = math.radians(EYE_PITCH_DEGREES)

    ground = distance * math.cos(pitch)
    height = max(distance * math.sin(pitch), MIN_EYE_HEIGHT)
    return CameraPose(
        position=Vec3(
            x=subject.x + view_direction[0] * ground,
            y=subject.y + view_direction[1] * ground,
            z=subject.z + height,
        ),
        target=subject,
        fov_degrees=fov_degrees,
    )


def _bbox(points: list[Vec3], padding: float) -> tuple[Vec3, Vec3]:
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    zs = [p.z for p in points]
    return (
        Vec3(x=min(xs) - padding, y=min(ys) - padding, z=min(zs)),
        Vec3(x=max(xs) + padding, y=max(ys) + padding, z=max(zs) + padding),
    )


def width_locus(
    graph: SceneGraph, result: WidthResult, draw_height: float = 0.05
) -> Locus:
    """A dimension line drawn across the actual gap, seen from the approach."""
    endpoints = _measurement_endpoints(graph, result, draw_height)
    start, end = endpoints
    subject = _midpoint(start, end)

    across = _perpendicular(end.x - start.x, end.y - start.y)
    approach = result.path[0] if result.path else _standing_room(graph, subject)
    direction = _viewing_side(subject, across, approach)

    radius = _framing_radius(graph, result, subject, span_between=start, and_=end)

    return Locus(
        point=result.pinch_point,
        bbox_min=_bbox([start, end], padding=0.6)[0],
        bbox_max=_bbox([start, end], padding=0.6)[1],
        node_ids=list(result.blocking_node_ids),
        annotation=Annotation(
            kind="dimension_line",
            points=[start, end],
            label=format_inches(result.inches),
        ),
        camera=camera_for(subject, radius, direction),
    )


CONTEXT_RADIUS = 2.4
"""Metres of surroundings to keep in shot.

A tight crop on a gap is unreadable: the viewer sees two coloured shapes and no
shop. The frame has to show enough of what is pinching the route for a person
to recognise where they are standing.
"""


def _framing_radius(
    graph: SceneGraph, result: WidthResult, subject: Vec3, span_between: Vec3, and_: Vec3
) -> float:
    """Wide enough to show the blockers and the space around them."""
    reach = math.dist((span_between.x, span_between.y), (and_.x, and_.y)) / 2
    for node_id in result.blocking_node_ids:
        try:
            node = graph.by_id(node_id)
        except KeyError:
            continue
        for corner_x, corner_y in footprint(node):
            reach = max(reach, math.dist((corner_x, corner_y), (subject.x, subject.y)))
    return min(max(reach, CONTEXT_RADIUS), 6.0)


def _measurement_endpoints(
    graph: SceneGraph, result: WidthResult, draw_height: float
) -> tuple[Vec3, Vec3]:
    """Where the dimension line actually attaches.

    With two named blockers the line runs between their facing surfaces.

    With one, the width belongs to that thing, so the line lies along its own
    width axis. A doorway turned forty degrees out of the world grid is still a
    doorway, and a line drawn along world X across it measures nothing a person
    could walk through, even when its length is right.

    With none, a span centred on the pinch is the most that can honestly be drawn.
    """
    if len(result.blocking_node_ids) == 2:
        a, b = (graph.by_id(node_id) for node_id in result.blocking_node_ids)
        point_a, point_b = closest_points(footprint(a), footprint(b))
        return (
            Vec3(x=point_a[0], y=point_a[1], z=draw_height),
            Vec3(x=point_b[0], y=point_b[1], z=draw_height),
        )

    half = to_meters(result.inches) / 2
    pinch = result.pinch_point
    along_x, along_y = _width_axis(graph, result.blocking_node_ids)
    return (
        Vec3(x=pinch.x - half * along_x, y=pinch.y - half * along_y, z=draw_height),
        Vec3(x=pinch.x + half * along_x, y=pinch.y + half * along_y, z=draw_height),
    )


def _width_axis(graph: SceneGraph, blocking_node_ids: list) -> tuple[float, float]:
    """The direction a single blocker's width runs in, or world X when there is none."""
    if len(blocking_node_ids) != 1:
        return (1.0, 0.0)
    try:
        node = graph.by_id(blocking_node_ids[0])
    except KeyError:
        return (1.0, 0.0)
    cos_t, sin_t = rotation_about_z(node)
    if node.dimensions.y > node.dimensions.x:
        return (-sin_t, cos_t)
    return (cos_t, sin_t)


CIRCLE_SEGMENTS = 32


def _rectangle_outline(half_w: float, half_d: float) -> list[tuple[float, float]]:
    return [(-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d)]


def _circle_outline(radius: float) -> list[tuple[float, float]]:
    step = 2 * math.pi / CIRCLE_SEGMENTS
    return [
        (radius * math.cos(i * step), radius * math.sin(i * step))
        for i in range(CIRCLE_SEGMENTS)
    ]


def region_locus(
    result: ClearFloorResult,
    node_ids: list,
    draw_height: float = 0.02,
    rotation: tuple[float, float] = (1.0, 0.0),
    circle: bool = False,
) -> Locus:
    """A floor patch, for turning space and clear floor space checks.

    `rotation` is the cosine and sine of the object the space sits against, so
    the patch and the camera turn with it. A turning space passes `circle=True`.
    """
    half_w = to_meters(result.inches_wide) / 2
    half_d = to_meters(result.inches_deep) / 2
    centre = result.center
    cos_t, sin_t = rotation
    outline = _circle_outline(half_w) if circle else _rectangle_outline(half_w, half_d)
    corners = [
        Vec3(
            x=centre.x + x * cos_t - y * sin_t,
            y=centre.y + x * sin_t + y * cos_t,
            z=draw_height,
        )
        for x, y in outline
    ]
    bbox_min, bbox_max = _bbox(corners, padding=0.4)
    return Locus(
        point=centre,
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        node_ids=list(node_ids),
        annotation=Annotation(
            kind="region",
            points=corners,
            label=format_inches(min(result.inches_wide, result.inches_deep)),
        ),
        camera=camera_for(centre, max(half_w, half_d) * 2, (sin_t, -cos_t)),
    )


def path_locus(
    result: WidthResult,
    label: str | None = None,
    point_inches: list[float | None] | None = None,
) -> Locus:
    """The whole route, for before and after replay.

    Pass `measure.route_path_clearances(...)` as `point_inches` and the viewer
    can colour the line by how tight the route is at each point.
    """
    points = result.path or [result.pinch_point]
    bbox_min, bbox_max = _bbox(points, padding=0.5)
    centre = _midpoint(bbox_min, bbox_max)
    extent = max(bbox_max.x - bbox_min.x, bbox_max.y - bbox_min.y)
    return Locus(
        point=result.pinch_point,
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        node_ids=list(result.blocking_node_ids),
        annotation=Annotation(
            kind="path",
            points=points,
            label=label or format_inches(result.inches),
            point_inches=point_inches,
        ),
        camera=camera_for(centre, extent / 2, (0.0, -1.0), fov_degrees=70.0),
    )


def height_locus(node: SceneNode, result: HeightResult) -> Locus:
    """A vertical dimension line, for a counter or a reach range.

    The horizontal version draws across a gap on the floor. A height is
    measured up the face of something, so the line runs from the floor to the
    top edge and the camera stands off to the side at eye level rather than
    looking down: from above, a vertical line is a dot.
    """
    top = node.transform.position.z + node.dimensions.z / 2
    face = _front_face(node)
    start = Vec3(x=face.x, y=face.y, z=0.0)
    end = Vec3(x=face.x, y=face.y, z=top)
    subject = Vec3(x=face.x, y=face.y, z=top / 2)

    outward = _outward_normal(node)
    radius = max(top, node.dimensions.x / 2, 1.0) * 1.2

    return Locus(
        point=result.measured_at,
        bbox_min=Vec3(x=face.x - radius, y=face.y - radius, z=0.0),
        bbox_max=Vec3(x=face.x + radius, y=face.y + radius, z=top + 0.3),
        node_ids=[node.id],
        annotation=Annotation(
            kind="dimension_line",
            points=[start, end],
            label=format_inches(result.inches),
        ),
        camera=_side_on_camera(subject, radius, outward),
    )


def _outward_normal(node: SceneNode) -> tuple[float, float]:
    """The direction a customer stands in, away from the node's local -Y face."""
    m = node.transform.m
    cos_t, sin_t = m[0], m[4]
    length = math.hypot(cos_t, sin_t) or 1.0
    cos_t, sin_t = cos_t / length, sin_t / length
    return (sin_t, -cos_t)


def _front_face(node: SceneNode) -> Vec3:
    outward = _outward_normal(node)
    centre = node.transform.position
    reach = node.dimensions.y / 2
    return Vec3(
        x=centre.x + outward[0] * reach, y=centre.y + outward[1] * reach, z=centre.z
    )


def _side_on_camera(
    subject: Vec3, radius: float, outward: tuple[float, float]
) -> CameraPose:
    """Low and off to one side, so a vertical measurement has length on screen."""
    half_fov = math.radians(DEFAULT_FOV) / 2
    distance = max(radius * FRAMING_MARGIN / math.tan(half_fov), MIN_CAMERA_DISTANCE)
    swing = math.radians(35.0)
    direction = (
        outward[0] * math.cos(swing) - outward[1] * math.sin(swing),
        outward[0] * math.sin(swing) + outward[1] * math.cos(swing),
    )
    return CameraPose(
        position=Vec3(
            x=subject.x + direction[0] * distance,
            y=subject.y + direction[1] * distance,
            z=subject.z + 0.55,
        ),
        target=subject,
        fov_degrees=DEFAULT_FOV,
    )
