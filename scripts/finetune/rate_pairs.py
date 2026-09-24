"""Blind human rating tool for the 30 layout-quality pairs in rating_pairs.jsonl.

Serves one page at http://127.0.0.1:8791 showing two top-down floor plans side
by side: "which rearrangement looks better?" Which one lands on the left is
randomised per pair with a fixed seed, so this file's own contents settle it
rather than the reviewer's guess about it, and the page never shows the
source, Q, or reward that produced either layout.

    python scripts/finetune/rate_pairs.py            # start the server
    python scripts/finetune/rate_pairs.py --score     # print agreement with Q

Every answer is appended to RUN/ratings.jsonl the moment it's made, so closing
the tab loses nothing; reloading resumes at the first pair without an answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.edits import node_moves, parse_edits
from standardphysics_agents.training.quality import front_heading_degrees
from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_pipeline.footprints import floor_polygon, footprint, rotation_about_z

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/finetune/multiroom"
PAIRS_PATH = RUN / "rating_pairs.jsonl"
VARIANTS_PATH = RUN / "variants.jsonl"
RATINGS_PATH = RUN / "ratings.jsonl"

HOST = "127.0.0.1"
PORT = 8791

LEFT_SEED = "rate-pairs-left-assignment-v1"
"""Mixed into the pair id to pick which side is shown on the left. Fixed, so a
rerun of the server reproduces the same layout instead of reshuffling it."""

PANEL_PX = 640
PADDING_PX = 44
WALL_THICKNESS_M = 0.09
DOOR_SWING_TERMS = ("wall", "pairs", "sight")
LABEL_FONT_PX = 13
LABEL_CHAR_WIDTH = 0.56
"""Rough average glyph width as a fraction of font size, for deciding whether a
label fits inside its footprint without ever measuring rendered text."""

FACING_WORDS = ("chair", "sofa", "bench", "stool", "table", "desk", "counter", "shelf", "display")
"""What gets a front marker. Matched the same way `training/quality.py` finds
seats and tables: a case-insensitive substring of the label or raw category."""


def _load_pairs() -> list[dict]:
    with PAIRS_PATH.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _load_variant_graphs() -> dict[str, dict]:
    graphs: dict[str, dict] = {}
    with VARIANTS_PATH.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if "graph" in row:
                graphs[row["variant_id"]] = row["graph"]
    return graphs


def left_is_a(pair_id: str) -> bool:
    """Deterministic, fixed-seed coin flip: whether side `a` renders on the left."""
    digest = hashlib.sha256(f"{LEFT_SEED}:{pair_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 2 == 0


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


class RatingStore:
    """Every answer ever given, keeping only the latest per pair so "back" can overwrite one."""

    def __init__(self, path: pathlib.Path):
        self.path = path

    def latest(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        latest: dict[str, dict] = {}
        with self.path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                latest[row["pair_id"]] = row
        return latest

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(entry) + "\n")


def _picked_side(picked_lr: str, left_was: str) -> str:
    """Translate a "left"/"right"/"tie" click back into "a"/"b"/"tie"."""
    if picked_lr == "tie":
        return "tie"
    right_was = "b" if left_was == "a" else "a"
    return left_was if picked_lr == "left" else right_was


# --- geometry -----------------------------------------------------------


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


def render_pair(pair: dict, base_graph_json: dict) -> dict:
    base_graph = SceneGraph.model_validate(base_graph_json)
    layout_a = _build_layout(pair["pair_id"], base_graph, pair["a"])
    layout_b = _build_layout(pair["pair_id"], base_graph, pair["b"])
    points = _room_points(layout_a.graph) + _room_points(layout_b.graph)
    rotation = _dominant_wall_rotation(base_graph)
    scale = Scale(points, rotation)
    left_a = left_is_a(pair["pair_id"])
    left_layout, right_layout = (layout_a, layout_b) if left_a else (layout_b, layout_a)
    return {
        "pair_id": pair["pair_id"],
        "left_was": "a" if left_a else "b",
        "left_svg": _room_svg(left_layout, scale),
        "right_svg": _room_svg(right_layout, scale),
    }


# --- scoring --------------------------------------------------------------


def _agreement(rows: list[dict], term: str) -> tuple[int, int]:
    matches, total = 0, 0
    for row in rows:
        if row["picked"] == "tie":
            continue
        pair = row["_pair"]
        picked_value = pair[row["picked"]]["q"][term]
        other_side = "b" if row["picked"] == "a" else "a"
        other_value = pair[other_side]["q"][term]
        if picked_value == other_value:
            continue
        total += 1
        matches += picked_value > other_value
    return matches, total


def run_score() -> None:
    pairs_by_id = {row["pair_id"]: row for row in _load_pairs()}
    ratings = RatingStore(RATINGS_PATH).latest()
    rows = []
    ties = 0
    for pair_id, entry in ratings.items():
        if pair_id not in pairs_by_id:
            continue
        picked = entry["picked"]
        if picked == "tie":
            ties += 1
        rows.append({"picked": picked, "_pair": pairs_by_id[pair_id]})

    print(f"{len(rows)} rated pairs, {ties} ties")
    for term in ("q", *DOOR_SWING_TERMS):
        matches, total = _agreement(rows, term)
        label = "overall" if term == "q" else term
        if total == 0:
            print(f"  {label}: no non-tie pairs with a difference")
            continue
        share = matches / total
        flag = "OK" if share >= 0.75 else "below 0.75 bar"
        print(f"  {label}: {matches}/{total} = {share:.1%} ({flag})")


# --- http server ------------------------------------------------------------


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Rate layouts</title>
<style>
  :root {
    --surface: #f6f4ef;
    --panel: #ffffff;
    --ink: #201d18;
    --ink-muted: #6b6459;
    --line: #ded8cb;
    --floor-fill: #efe9dc;
    --wall: #3c362c;
    --portal-gap: #efe9dc;
    --window-mark: #7fa6bd;
    --door-line: #a08a5f;
    --object-fill: #e4d9c2;
    --object-fill-moved: #cbb98e;
    --object-stroke: #b7a67d;
    --ghost-fill: #d8d2c4;
    --ghost-stroke: #8a8272;
    --accent: #c9622f;
    --control-bg: #eee8db;
    --control-bg-hover: #e4dcc9;
    --control-selected: #201d18;
  }
  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body {
    margin: 0;
    background: var(--surface);
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
    width: 100%;
    padding: 28px 20px 40px;
  }
  .wide-only { display: inline; }
  .narrow-only { display: none; }
  .top {
    width: 100%;
    max-width: 1180px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
    margin-bottom: 18px;
  }
  button.back {
    background: transparent;
    border: none;
    color: var(--ink-muted);
    font-size: 0.9rem;
    cursor: pointer;
    padding: 10px 4px;
    order: -1;
  }
  button.back:hover { color: var(--ink); }
  button.back:disabled { opacity: 0.35; cursor: default; }
  h1 { font-size: 1.15rem; font-weight: 600; margin: 0; flex: 1 1 200px; min-width: 0; }
  .progress { color: var(--ink-muted); font-size: 0.95rem; white-space: nowrap; }
  .panels {
    display: flex;
    gap: 24px;
    max-width: 1180px;
    width: 100%;
    justify-content: center;
  }
  .panel {
    background: var(--panel);
    border-radius: 16px;
    box-shadow: 0 1px 2px rgba(32,29,24,0.08), 0 8px 24px rgba(32,29,24,0.06);
    padding: 16px;
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: center;
    cursor: pointer;
    transition: box-shadow 150ms ease-out, transform 150ms ease-out;
  }
  .panel:hover { box-shadow: 0 1px 2px rgba(32,29,24,0.1), 0 10px 28px rgba(32,29,24,0.1); }
  .panel:active { transform: scale(0.99); }
  .panel svg { max-width: 100%; height: auto; }
  .floor { fill: var(--floor-fill); stroke: var(--line); stroke-width: 1.5; }
  .wall { stroke: var(--wall); stroke-linecap: square; }
  .portal-gap { stroke: var(--portal-gap); stroke-linecap: butt; }
  .window-mark { stroke: var(--window-mark); stroke-linecap: butt; }
  .door-leaf { stroke: var(--door-line); stroke-width: 1.5; }
  .door-arc { fill: none; stroke: var(--door-line); stroke-width: 1; stroke-dasharray: 3 3; }
  .object { fill: var(--object-fill); stroke: var(--object-stroke); stroke-width: 1; }
  .object.moved { fill: var(--object-fill-moved); }
  .object-label {
    font-size: 13px;
    fill: var(--ink);
    text-anchor: middle;
    dominant-baseline: middle;
    pointer-events: none;
  }
  .ghost { fill: var(--ghost-fill); fill-opacity: 0.5; stroke: var(--ghost-stroke); stroke-width: 1.5; stroke-dasharray: 5 4; }
  .ghost-trail { stroke: var(--ghost-stroke); stroke-width: 2; opacity: 0.85; }
  .front-marker { stroke: var(--accent); stroke-width: 4; stroke-linecap: round; }
  .front-marker.ghost { stroke: var(--ghost-stroke); stroke-width: 3; }
  .controls {
    display: flex;
    gap: 12px;
    justify-content: center;
    width: 100%;
    max-width: 1180px;
    margin-top: 22px;
  }
  button.choice {
    background: var(--control-bg);
    border: none;
    border-radius: 10px;
    padding: 12px 22px;
    font-size: 0.95rem;
    line-height: 1.2;
    color: var(--ink);
    cursor: pointer;
    transition: background 150ms ease-out, box-shadow 150ms ease-out;
    box-shadow: 0 1px 2px rgba(32,29,24,0.06);
    flex: 1 1 0;
  }
  button.choice:hover { background: var(--control-bg-hover); }
  button.choice:active { transform: scale(0.96); }
  .hint { color: var(--ink-muted); font-size: 0.82rem; margin-top: 10px; text-align: center; }
  .summary {
    max-width: 480px;
    text-align: center;
    margin-top: 80px;
  }
  .summary h2 { font-size: 1.4rem; margin-bottom: 10px; }
  .summary p { color: var(--ink-muted); line-height: 1.5; }

  @media (max-width: 900px) {
    body { padding: 20px 14px 32px; }
    .wide-only { display: none; }
    .narrow-only { display: inline; }
    .panels { flex-direction: column; gap: 16px; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    button.choice { padding: 12px 8px; font-size: 0.88rem; }
  }
</style>
</head>
<body>
<div id="app"></div>
<script>
const app = document.getElementById("app");
let state = { index: 0, total: 30, done: false };
let currentPair = null;
let pending = null;

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

async function loadState() {
  state = await fetchJSON("/api/state");
  if (state.done) { renderSummary(); return; }
  await loadPair(state.index);
}

async function loadPair(index) {
  currentPair = await fetchJSON("/api/pair/" + index);
  pending = null;
  render();
}

function render() {
  if (!currentPair) return;
  app.innerHTML = `
    <div class="top">
      <button class="back" id="back" ${state.index === 0 ? "disabled" : ""}>Back</button>
      <h1>Which rearrangement looks better?</h1>
      <div class="progress">${state.index + 1} of ${state.total}</div>
    </div>
    <div class="panels">
      <div class="panel" data-side="left">${currentPair.left_svg}</div>
      <div class="panel" data-side="right">${currentPair.right_svg}</div>
    </div>
    <div class="controls">
      <button class="choice" data-pick="left">
        <span class="wide-only">Left looks better</span>
        <span class="narrow-only">Top looks better</span>
      </button>
      <button class="choice" data-pick="right">
        <span class="wide-only">Right looks better</span>
        <span class="narrow-only">Bottom looks better</span>
      </button>
      <button class="choice" data-pick="tie">Can't tell</button>
    </div>
    <div class="hint">
      <span class="wide-only">Left/right arrows or 1/2 pick a side. Down arrow or 3 is can't tell.</span>
      <span class="narrow-only">Up/down arrows or 1/2 pick a side. 3 is can't tell.</span>
    </div>
  `;
  app.querySelector('[data-side="left"]').addEventListener("click", () => choose("left"));
  app.querySelector('[data-side="right"]').addEventListener("click", () => choose("right"));
  app.querySelectorAll("button.choice").forEach((button) => {
    button.addEventListener("click", () => choose(button.dataset.pick));
  });
  const back = document.getElementById("back");
  if (!back.disabled) back.addEventListener("click", goBack);
}

async function choose(picked) {
  if (pending) return;
  pending = picked;
  await fetchJSON("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pair_id: currentPair.pair_id, picked, left_was: currentPair.left_was }),
  });
  state.index += 1;
  if (state.index >= state.total) { renderSummary(); return; }
  await loadPair(state.index);
}

async function goBack() {
  if (state.index === 0) return;
  state.index -= 1;
  await loadPair(state.index);
}

function renderSummary() {
  state.done = true;
  app.innerHTML = `
    <div class="summary">
      <h2>All ${state.total} ratings saved</h2>
      <p>Your answers were written as they were made. You can close this tab.</p>
    </div>
  `;
}

const stackedQuery = window.matchMedia("(max-width: 900px)");

window.addEventListener("keydown", (event) => {
  if (state.done) return;
  const map = stackedQuery.matches
    ? { ArrowUp: "left", "1": "left", ArrowDown: "right", "2": "right", "3": "tie" }
    : { ArrowLeft: "left", "1": "left", ArrowRight: "right", "2": "right", ArrowDown: "tie", "3": "tie" };
  if (event.key in map) { choose(map[event.key]); return; }
  if (event.key === "Backspace") goBack();
});

loadState();
</script>
</body>
</html>
"""


class Server:
    def __init__(self):
        self.pairs = _load_pairs()
        self.variant_graphs = _load_variant_graphs()
        self.ratings = RatingStore(RATINGS_PATH)
        self._rendered: dict[str, dict] = {}

    def render(self, index: int) -> dict:
        pair = self.pairs[index]
        if pair["pair_id"] not in self._rendered:
            self._rendered[pair["pair_id"]] = render_pair(pair, self.variant_graphs[pair["variant_id"]])
        return self._rendered[pair["pair_id"]]

    def state(self) -> dict:
        answered = self.ratings.latest()
        index = 0
        for pair in self.pairs:
            if pair["pair_id"] not in answered:
                break
            index += 1
        return {"index": index, "total": len(self.pairs), "done": index >= len(self.pairs)}

    def record_answer(self, pair_id: str, picked_lr: str, left_was: str) -> None:
        picked = _picked_side(picked_lr, left_was)
        self.ratings.append(
            {
                "pair_id": pair_id,
                "picked": picked,
                "left_was": left_was,
                "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            }
        )


def make_handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib method name
            path = urlparse(self.path).path
            if path == "/":
                body = PAGE_TEMPLATE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/state":
                self._json(server.state())
                return
            if path.startswith("/api/pair/"):
                try:
                    index = int(path.rsplit("/", 1)[-1])
                    payload = server.render(index)
                except (ValueError, IndexError, KeyError):
                    self._json({"error": "no such pair"}, status=404)
                    return
                self._json(payload)
                return
            self._json({"error": "not found"}, status=404)

        def do_POST(self):  # noqa: N802 - stdlib method name
            path = urlparse(self.path).path
            if path != "/api/answer":
                self._json({"error": "not found"}, status=404)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            server.record_answer(body["pair_id"], body["picked"], body["left_was"])
            self._json({"ok": True})

    return Handler


def run_server() -> None:
    server = Server()
    httpd = ThreadingHTTPServer((HOST, PORT), make_handler(server))
    print(f"rate_pairs serving on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true", help="print agreement between human picks and Q, then exit")
    args = parser.parse_args()
    if args.score:
        run_score()
        return
    run_server()


if __name__ == "__main__":
    main()
