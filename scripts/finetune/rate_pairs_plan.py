"""Top-down SVG floor plans for the rating tool: room geometry, the shared
meters-to-pixels mapping, and the drawing of walls, portals, ghosts and pieces."""

from __future__ import annotations

import math
from dataclasses import dataclass

from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.quality import front_heading_degrees
from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_pipeline.footprints import floor_polygon, footprint, rotation_about_z

PANEL_PX = 640
PADDING_PX = 44
WALL_THICKNESS_M = 0.09
LABEL_FONT_PX = 13
LABEL_CHAR_WIDTH = 0.56
"""Rough average glyph width as a fraction of font size, for deciding whether a
label fits inside its footprint without ever measuring rendered text."""

FACING_WORDS = ("chair", "sofa", "bench", "stool", "shelf", "display")
"""What gets a front marker: pieces with one side you use them from. Tables, desks
and counters are used from any open side, so a bar on one edge would claim a
front they do not have. Matched as a case-insensitive substring of the label or
raw category."""


@dataclass(frozen=True)
class Layout:
    pair_id: str
    base_graph: SceneGraph
    graph: SceneGraph
    moved_node_ids: frozenset


def _build_layout(pair_id: str, base_graph: SceneGraph, side: dict) -> Layout:
    edits = parse_edits(side["edits"])
    moves = node_moves(edits) if edits else []
    graph = apply_moves(base_graph, moves) if moves else base_graph
    return Layout(pair_id, base_graph, graph, frozenset(move.node_id for move in moves))



def _bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _room_points(graph: SceneGraph) -> list[tuple[float, float]]:
    """Walls plus every object's footprint. The floor sheet itself is excluded:
    it commonly extends well past the walls, and cropping to it would waste
    most of the panel on empty margin."""
    points: list[tuple[float, float]] = []
    for node in graph.nodes:
        if node.kind == "floor":
            continue
        points += footprint(node)
    return points


def _dominant_wall_rotation(graph: SceneGraph) -> tuple[float, float]:
    """cos/sin of the turn that makes the longest wall axis-aligned.

    RoomPlan scans rarely come back with a wall running exactly along X or Y,
    so the plan reads as tilted even when the room itself is a rectangle. This
    finds the longest wall's angle and rotates the whole scene by whatever
    turn lands that angle on the nearest multiple of 90 degrees.
    """
    walls = [n for n in graph.nodes if n.kind == "wall"]
    if not walls:
        return 1.0, 0.0
    longest = max(walls, key=lambda n: n.dimensions.x)
    cos_t, sin_t = rotation_about_z(longest)
    angle = math.degrees(math.atan2(sin_t, cos_t))
    delta = round(angle / 90.0) * 90.0 - angle
    radians = math.radians(delta)
    return math.cos(radians), math.sin(radians)


def _wall_endpoints(node: SceneNode) -> tuple[tuple[float, float], tuple[float, float]]:
    cos_t, sin_t = rotation_about_z(node)
    cx, cy = node.transform.position.x, node.transform.position.y
    half = node.dimensions.x / 2
    return (cx - half * cos_t, cy - half * sin_t), (cx + half * cos_t, cy + half * sin_t)


def _wall_thickness(node: SceneNode) -> float:
    return node.dimensions.y if 0.03 < node.dimensions.y < 0.5 else WALL_THICKNESS_M


class Scale:
    """Maps room-frame meters to panel pixels, shared by both sides of a pair.

    Rotation is folded into the same mapping: every point handed to `point()`
    is first turned by (cos_d, sin_d) around the origin, so the dominant wall
    lands axis-aligned, and every other piece of geometry (footprints, wall
    endpoints, door swings) keeps working in the room's own original frame
    without knowing rotation happens at all.
    """

    def __init__(self, points: list[tuple[float, float]], rotation: tuple[float, float] = (1.0, 0.0)):
        self.cos_d, self.sin_d = rotation
        rotated = [self._rotate(x, y) for x, y in points] if points else [(0.0, 0.0), (1.0, 1.0)]
        min_x, min_y, max_x, max_y = _bounds(rotated)
        span_x, span_y = max(max_x - min_x, 0.5), max(max_y - min_y, 0.5)
        usable = PANEL_PX - 2 * PADDING_PX
        self.factor = usable / max(span_x, span_y)
        self.min_x, self.min_y = min_x, min_y
        self.height_px = span_y * self.factor + 2 * PADDING_PX
        self.width_px = span_x * self.factor + 2 * PADDING_PX

    def _rotate(self, x: float, y: float) -> tuple[float, float]:
        return x * self.cos_d - y * self.sin_d, x * self.sin_d + y * self.cos_d

    def point(self, x: float, y: float) -> tuple[float, float]:
        rx, ry = self._rotate(x, y)
        return (
            PADDING_PX + (rx - self.min_x) * self.factor,
            self.height_px - PADDING_PX - (ry - self.min_y) * self.factor,
        )


def _polygon_points(scale: Scale, poly: list[tuple[float, float]]) -> str:
    return " ".join(f"{px:.1f},{py:.1f}" for px, py in (scale.point(x, y) for x, y in poly))


def _svg_polygon(scale: Scale, poly, **attrs) -> str:
    attr_str = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<polygon points="{_polygon_points(scale, poly)}" {attr_str} />'


def _svg_line(scale: Scale, a, b, **attrs) -> str:
    (x1, y1), (x2, y2) = scale.point(*a), scale.point(*b)
    attr_str = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {attr_str} />'


def _door_swing_arc(scale: Scale, hinge, opening_width: float, inward: tuple[float, float]) -> str:
    """A quarter-circle sweep plus the leaf line, drawn open into the room."""
    perpendicular = (-inward[1], inward[0])
    leaf_end = (hinge[0] + opening_width * inward[0], hinge[1] + opening_width * inward[1])
    arc_end = (hinge[0] + opening_width * perpendicular[0], hinge[1] + opening_width * perpendicular[1])
    hx, hy = scale.point(*hinge)
    lx, ly = scale.point(*leaf_end)
    ax, ay = scale.point(*arc_end)
    radius = opening_width * scale.factor
    leaf = f'<line x1="{hx:.1f}" y1="{hy:.1f}" x2="{lx:.1f}" y2="{ly:.1f}" class="door-leaf" />'
    arc = (
        f'<path d="M {lx:.1f} {ly:.1f} A {radius:.1f} {radius:.1f} 0 0 1 {ax:.1f} {ay:.1f}" '
        'class="door-arc" />'
    )
    return leaf + arc


def _portal_inward(node: SceneNode, wall: SceneNode | None, floor_centroid: tuple[float, float]) -> tuple:
    cos_t, sin_t = rotation_about_z(wall or node)
    normal = (-sin_t, cos_t)
    to_centre = (floor_centroid[0] - node.transform.position.x, floor_centroid[1] - node.transform.position.y)
    sign = 1.0 if (normal[0] * to_centre[0] + normal[1] * to_centre[1]) >= 0 else -1.0
    return (normal[0] * sign, normal[1] * sign)


def _floor_centroid(graph: SceneGraph) -> tuple[float, float]:
    points = [p for n in graph.nodes if n.kind == "floor" for p in floor_polygon(n)]
    if not points:
        return (0.0, 0.0)
    return (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _floor_parts(graph: SceneGraph, scale: Scale) -> list[str]:
    return [_svg_polygon(scale, floor_polygon(n), **{"class": "floor"}) for n in graph.nodes if n.kind == "floor"]


def _wall_parts(graph: SceneGraph, scale: Scale) -> list[str]:
    parts = []
    for node in graph.nodes:
        if node.kind != "wall":
            continue
        thickness = _wall_thickness(node)
        start, end = _wall_endpoints(node)
        parts.append(
            _svg_line(scale, start, end, **{"class": "wall", "stroke-width": f"{thickness * scale.factor:.1f}"})
        )
    return parts


def _portal_parts(graph: SceneGraph, scale: Scale, floor_centroid: tuple[float, float]) -> list[str]:
    by_id = {n.id: n for n in graph.nodes}
    parts = []
    for node in graph.nodes:
        if node.kind not in ("door", "opening", "window"):
            continue
        wall = by_id.get(node.parent_id) if node.parent_id else None
        thickness = _wall_thickness(wall) if wall else WALL_THICKNESS_M
        start, end = _wall_endpoints(node)
        gap_width = f"{thickness * scale.factor + 1:.1f}"
        parts.append(_svg_line(scale, start, end, **{"class": "portal-gap", "stroke-width": gap_width}))
        if node.kind == "window":
            mark_width = f"{thickness * scale.factor * 0.5:.1f}"
            parts.append(_svg_line(scale, start, end, **{"class": "window-mark", "stroke-width": mark_width}))
        if node.kind == "door":
            inward = _portal_inward(node, wall, floor_centroid)
            width = max(((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5, 0.5)
            parts.append(_door_swing_arc(scale, start, width, inward))
    return parts


def _ghost_parts(layout: Layout, scale: Scale) -> list[str]:
    base_by_id = {n.id: n for n in layout.base_graph.nodes}
    by_id = {n.id: n for n in layout.graph.nodes}
    parts = []
    for node_id in layout.moved_node_ids:
        original = base_by_id.get(node_id)
        current = by_id.get(node_id)
        if original is None or current is None:
            continue
        parts.append(_svg_polygon(scale, footprint(original), **{"class": "ghost"}))
        if _has_a_facing(original):
            parts.append(_front_marker(scale, original, "front-marker ghost"))
        ox, oy = original.transform.position.x, original.transform.position.y
        cx, cy = current.transform.position.x, current.transform.position.y
        parts.append(_svg_line(scale, (ox, oy), (cx, cy), **{"class": "ghost-trail"}))
    return parts


def _has_a_facing(node: SceneNode) -> bool:
    text = f"{node.label} {node.raw_category}".casefold()
    return any(word in text for word in FACING_WORDS)


def _front_edge(node: SceneNode) -> tuple[tuple[float, float], tuple[float, float]]:
    """The two corners of the footprint on the node's `front_heading_degrees` side.

    Same heading `training/quality.py`'s `_facing_off` scores a seat's facing
    against, which is the local -Y edge (`dimensions.y` is the depth a chair
    or table faces along; `dimensions.x` is its width), so the marker drawn
    here is showing exactly what Q measures, not a separate guess.
    """
    cx, cy = node.transform.position.x, node.transform.position.y
    heading = math.radians(front_heading_degrees(node))
    forward = (math.cos(heading), math.sin(heading))
    sideways = (-forward[1], forward[0])
    half_forward, half_side = node.dimensions.y / 2, node.dimensions.x / 2
    front_centre = (cx + forward[0] * half_forward, cy + forward[1] * half_forward)
    return (
        (front_centre[0] - sideways[0] * half_side, front_centre[1] - sideways[1] * half_side),
        (front_centre[0] + sideways[0] * half_side, front_centre[1] + sideways[1] * half_side),
    )


def _front_marker(scale: Scale, node: SceneNode, css_class: str) -> str:
    start, end = _front_edge(node)
    return _svg_line(scale, start, end, **{"class": css_class})


def _label_point(scale: Scale, node: SceneNode) -> tuple[float, float]:
    """Centred inside the footprint when the label fits, else just above it."""
    pixels = [scale.point(x, y) for x, y in footprint(node)]
    min_y = min(y for _, y in pixels)
    width_px = max(x for x, _ in pixels) - min(x for x, _ in pixels)
    height_px = max(y for _, y in pixels) - min_y
    cx, cy = scale.point(node.transform.position.x, node.transform.position.y)
    label_width = len(node.label) * LABEL_FONT_PX * LABEL_CHAR_WIDTH
    fits = label_width <= width_px - 4 and height_px >= LABEL_FONT_PX + 4
    return (cx, cy) if fits else (cx, min_y - LABEL_FONT_PX / 2 - 4)


def _object_parts(layout: Layout, scale: Scale) -> list[str]:
    parts = []
    for node in layout.graph.nodes:
        if node.kind in ("wall", "floor", "door", "opening", "window"):
            continue
        css_class = "object moved" if node.id in layout.moved_node_ids else "object"
        parts.append(_svg_polygon(scale, footprint(node), **{"class": css_class}))
        if _has_a_facing(node):
            parts.append(_front_marker(scale, node, "front-marker"))
        lx, ly = _label_point(scale, node)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="object-label">{_escape(node.label)}</text>')
    return parts


def _room_svg(layout: Layout, scale: Scale) -> str:
    graph = layout.graph
    floor_centroid = _floor_centroid(graph)
    parts = (
        _floor_parts(graph, scale)
        + _wall_parts(graph, scale)
        + _portal_parts(graph, scale, floor_centroid)
        + _ghost_parts(layout, scale)
        + _object_parts(layout, scale)
    )
    return (
        f'<svg viewBox="0 0 {scale.width_px:.0f} {scale.height_px:.0f}" '
        f'width="{scale.width_px:.0f}" height="{scale.height_px:.0f}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(parts)
        + "</svg>"
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
