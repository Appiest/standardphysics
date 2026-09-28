"""Problem windows: the part of a big scan one furniture problem depends on.

A narrow aisle depends only on the furniture a few metres from it, so a floor
of a library is many training rooms rather than one. A window keeps every node
whose ground footprint comes within `WINDOW_RADIUS_METERS` of its centre, the
parents those nodes hang from, and the floor it stands on cropped to the
window's square. Cropping the floor matters: an uncropped floor leaves the
emptied rest of the building walkable, and a route would walk around the
problem instead of through it.

A window's route is local. Where a leg of the scan's own scenario crosses the
window, the window keeps that leg from where it enters to where it leaves. A
window no leg crosses gets a straight crossing route through its middle.

A window is only kept when it is self-consistent: the checker's problems in the
window match its problems in the whole scan measured with the same route
(`reproduction`). The problem the window was cut around has to reappear, and
the window may neither lose a problem the scan has inside it nor invent one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from standardphysics_contracts import Finding, Scenario, SceneGraph, SceneNode, Stop, Vec3, lies_flat
from standardphysics_pipeline import contains_point
from standardphysics_pipeline.footprints import distance_outside, floor_polygon
from standardphysics_pipeline.occupancy import build_grid

from .checker import TrainingChecker
from .scramble import floor_furniture

WINDOW_RADIUS_METERS = 3.5
"""How far from a problem a window reaches. Chosen so windows cut from the
Moffett library floor hold roughly 20 to 40 objects; see the dataset report."""

ROUTE_INSET_METERS = 0.6
"""How far inside the window's edge its route begins and ends."""

MIN_ROUTE_METERS = 1.5
"""A clipped leg shorter than this is not a route through the window."""

SNAP_METERS = 0.75
"""How far a crossing route's end may move to find free floor."""

SAME_PLACE_METERS = 0.35
"""Two findings of one check this close are the same problem."""

SAME_WIDTH_INCHES = 1.0
"""And this close in measured width."""

COVERED_FRACTION = 0.4
"""A piece within this share of the radius of a window's centre is covered by it."""

MIN_MOVABLE_PIECES = 3
"""A cluster window with fewer movable floor pieces has nothing to rearrange."""

UPRIGHT_AXIS = 0.5


@dataclass
class Window:
    window_id: str
    scan_id: str
    centre: tuple[float, float] | None
    graph: SceneGraph
    scenario: Scenario
    route: str
    """"scan" for a whole small scan, "clipped" for a leg of the scan's route, "crossing" for a local one."""
    seed: dict | None = None
    """The problem the window was cut around, or None for a window around a cluster of furniture."""
    reproduction: dict = field(default_factory=dict)
    pinned: list[str] = field(default_factory=list)
    """Ids of pieces in the window the phantom filter holds still."""

    def floor_id(self) -> str | None:
        floor = next((node for node in self.graph.nodes if lies_flat(node)), None)
        return None if floor is None else str(floor.id)

    def as_dict(self) -> dict:
        return {
            "window_id": self.window_id, "scan_id": self.scan_id, "centre": self.centre, "route": self.route,
            "seed": self.seed, "reproduction": self.reproduction, "pinned": self.pinned,
            "floor_id": self.floor_id(), "objects": self.object_count(), "movable": self.movable_count(),
            "graph": self.graph.model_dump(mode="json"), "scenario": self.scenario.model_dump(mode="json"),
        }

    @classmethod
    def from_dict(cls, row: dict) -> Window:
        return cls(
            window_id=row["window_id"], scan_id=row["scan_id"],
            centre=None if row["centre"] is None else tuple(row["centre"]),
            graph=SceneGraph.model_validate(row["graph"]), scenario=Scenario.model_validate(row["scenario"]),
            route=row["route"], seed=row["seed"], reproduction=row["reproduction"], pinned=row.get("pinned", []),
        )

    def object_count(self) -> int:
        return sum(1 for node in self.graph.nodes if not lies_flat(node))

    def movable_count(self) -> int:
        return len(floor_furniture(self.graph))


def _xy(point) -> tuple[float, float]:
    return (point.x, point.y)


def _to_segment(point, start, end) -> float:
    (px, py), (ax, ay), (bx, by) = point, start, end
    length = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length))
    return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))


def ground_distance(node: SceneNode, centre: tuple[float, float]) -> float:
    """How far `centre` lies outside the node's ground footprint.

    A wall's footprint collapses to a line, which a polygon test would call
    zero distance from everywhere.
    """
    hull = floor_polygon(node)
    if len(hull) >= 3:
        return distance_outside(hull, centre)
    return min(_to_segment(centre, hull[0], hull[-1]), math.dist(centre, hull[0]))


def reaches(node: SceneNode, centre: tuple[float, float], radius: float) -> bool:
    return ground_distance(node, centre) <= radius


def floor_under(graph: SceneGraph, centre: tuple[float, float]) -> SceneNode | None:
    floors = [node for node in graph.nodes if lies_flat(node)]
    under = [floor for floor in floors if contains_point(floor_polygon(floor), centre)]
    candidates = under or floors
    if not candidates:
        return None
    return min(candidates, key=lambda floor: ground_distance(floor, centre))


def _rotation(node: SceneNode) -> list[list[float]]:
    m = node.transform.m
    return [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]


def _crop_axis(half: float, centre: float, radius: float) -> tuple[float, float] | None:
    low, high = max(-half, centre - radius), min(half, centre + radius)
    return (low, high) if high > low else None


def cropped_floor(floor: SceneNode, centre: tuple[float, float], radius: float) -> SceneNode | None:
    """The floor cut down to the window's square, in the floor's own frame."""
    rotation, m = _rotation(floor), floor.transform.m
    offset = (centre[0] - m[3], centre[1] - m[7], 0.0)
    local = [sum(rotation[row][axis] * offset[row] for row in range(3)) for axis in range(3)]
    dims, middle = list(floor.dimensions.as_tuple()), [0.0, 0.0, 0.0]
    for axis in range(3):
        if abs(rotation[2][axis]) >= UPRIGHT_AXIS or dims[axis] <= 0:
            continue
        span = _crop_axis(dims[axis] / 2, local[axis], radius)
        if span is None:
            return None
        middle[axis], dims[axis] = (span[0] + span[1]) / 2, span[1] - span[0]
    shift = [sum(rotation[row][axis] * middle[axis] for axis in range(3)) for row in range(3)]
    moved = list(m)
    moved[3], moved[7], moved[11] = m[3] + shift[0], m[7] + shift[1], m[11] + shift[2]
    return floor.model_copy(update={
        "dimensions": Vec3(x=dims[0], y=dims[1], z=dims[2]),
        "transform": floor.transform.model_copy(update={"m": moved}),
    })


def _with_ancestors(graph: SceneGraph, chosen: set) -> set:
    parents = {node.id: node.parent_id for node in graph.nodes}
    flat = {node.id for node in graph.nodes if lies_flat(node)}
    kept = set(chosen)
    for node_id in chosen:
        parent = parents.get(node_id)
        while parent is not None and parent not in kept and parent not in flat:
            kept.add(parent)
            parent = parents.get(parent)
    return kept


def _detached(node: SceneNode, kept: set) -> SceneNode:
    if node.parent_id is None or node.parent_id in kept:
        return node
    return node.model_copy(update={"parent_id": None, "relation": None})


def with_one_floor(graph: SceneGraph, floor: SceneNode) -> SceneGraph:
    """The whole scan standing on one floor, the one the checker will read."""
    nodes = [node for node in graph.nodes if not lies_flat(node) or node.id == floor.id]
    kept = {node.id for node in nodes}
    return graph.model_copy(update={"nodes": [_detached(node, kept) for node in nodes]})


def cut(graph: SceneGraph, centre: tuple[float, float], radius: float = WINDOW_RADIUS_METERS) -> SceneGraph | None:
    """Every node reaching within `radius` of `centre`, standing on its floor cropped to the window."""
    floor = floor_under(graph, centre)
    if floor is None:
        return None
    cropped = cropped_floor(floor, centre, radius)
    if cropped is None:
        return None
    chosen = {node.id for node in graph.nodes if not lies_flat(node) and reaches(node, centre, radius)}
    kept = _with_ancestors(graph, chosen) | {floor.id}
    nodes = [cropped if node.id == floor.id else _detached(node, kept) for node in graph.nodes if node.id in kept]
    return graph.model_copy(update={"nodes": nodes})


def _inside(point, centre: tuple[float, float], radius: float) -> bool:
    return math.dist(_xy(point), centre) <= radius


def _end_stop(original: Stop, point: Vec3, centre, inner: float, side: str) -> Stop:
    if _inside(original.position, centre, inner):
        return original
    return Stop(name=f"{original.name} ({side} of window)", position=Vec3(x=point.x, y=point.y, z=0.0))


def _clipped_leg(path: list[Vec3], stops: tuple[Stop, Stop], centre, inner: float) -> list[Stop] | None:
    inside = [point for point in path if _inside(point, centre, inner)]
    if len(inside) < 2 or math.dist(_xy(inside[0]), _xy(inside[-1])) < MIN_ROUTE_METERS:
        return None
    return [_end_stop(stops[0], inside[0], centre, inner, "entering"),
            _end_stop(stops[1], inside[-1], centre, inner, "leaving")]


def _closest_approach(path: list[Vec3], centre) -> float:
    return min((math.dist(_xy(point), centre) for point in path), default=math.inf)


def clipped_route(scan: SceneGraph, scenario: Scenario, checker: TrainingChecker, centre,
                  radius: float = WINDOW_RADIUS_METERS) -> Scenario | None:
    """The leg of the scan's route passing closest to `centre`, from where it enters the window to where it leaves."""
    inner = radius - ROUTE_INSET_METERS
    legs = []
    for index in range(len(scenario.stops) - 1):
        path = checker.measure.route_clear_width(scan, scenario, index).path
        stops = (scenario.stops[index], scenario.stops[index + 1])
        clipped = _clipped_leg(path, stops, centre, inner)
        if clipped is not None:
            legs.append((_closest_approach(path, centre), clipped))
    if not legs:
        return None
    return Scenario(name=f"{scenario.name} (window)", stops=min(legs, key=lambda leg: leg[0])[1])


def _free_cell_near(grid, point: tuple[float, float]) -> Vec3 | None:
    reach = int(SNAP_METERS / grid.cell_size)
    row, col = grid.to_cell(*point)
    best = None
    for d_row in range(-reach, reach + 1, 2):
        for d_col in range(-reach, reach + 1, 2):
            cell = (row + d_row, col + d_col)
            if not _open(grid, cell):
                continue
            distance = d_row * d_row + d_col * d_col
            if best is None or distance < best[0]:
                best = (distance, cell)
    return None if best is None else grid.to_world(*best[1])


def _open(grid, cell) -> bool:
    if not grid.contains(*cell) or grid.occupied[cell]:
        return False
    return grid.indoors is None or bool(grid.indoors[cell])


def _pieces_near_segment(pieces: list[SceneNode], start, end) -> int:
    return sum(1 for node in pieces if _to_segment(_xy(node.transform.position), start, end) <= 1.0)


CROSSING_HEADINGS = (0.0, 45.0, 90.0, 135.0)


def crossing_routes(window: SceneGraph, centre, radius: float = WINDOW_RADIUS_METERS) -> list[Scenario]:
    """Straight walks through the window, the heading that passes the most furniture first."""
    inner, grid, pieces = radius - ROUTE_INSET_METERS, build_grid(window), floor_furniture(window)
    candidates = []
    for heading in CROSSING_HEADINGS:
        dx, dy = math.cos(math.radians(heading)) * inner, math.sin(math.radians(heading)) * inner
        ends = (centre[0] - dx, centre[1] - dy), (centre[0] + dx, centre[1] + dy)
        start, goal = _free_cell_near(grid, ends[0]), _free_cell_near(grid, ends[1])
        if start is None or goal is None or math.dist(_xy(start), _xy(goal)) < MIN_ROUTE_METERS:
            continue
        route = Scenario(name=f"Walk through at {heading:.0f} degrees",
                         stops=[Stop(name="One side", position=start), Stop(name="Other side", position=goal)])
        candidates.append((-_pieces_near_segment(pieces, ends[0], ends[1]), heading, route))
    return [route for _, _, route in sorted(candidates, key=lambda item: item[:2])]


def _summary(finding: Finding) -> dict:
    point = None if finding.locus is None else [round(finding.locus.point.x, 3), round(finding.locus.point.y, 3)]
    inches = None if finding.measured_inches is None else round(finding.measured_inches, 2)
    return {"check": finding.check_id, "at": point, "measured_inches": inches}


def same_problem(a: dict, b: dict) -> bool:
    if a["check"] != b["check"] or (a["at"] is None) != (b["at"] is None):
        return False
    if a["at"] is not None and math.dist(a["at"], b["at"]) > SAME_PLACE_METERS:
        return False
    if (a["measured_inches"] is None) != (b["measured_inches"] is None):
        return False
    return a["measured_inches"] is None or abs(a["measured_inches"] - b["measured_inches"]) <= SAME_WIDTH_INCHES


def _matched(problem: dict, others: list[dict]) -> bool:
    return any(same_problem(problem, other) for other in others)


def reproduction(window_problems: list[Finding], scan_problems: list[Finding], centre, seed: dict | None,
                 radius: float = WINDOW_RADIUS_METERS) -> dict:
    """Whether the window's furniture-fixable problems are the scan's problems inside it."""
    inner = radius - ROUTE_INSET_METERS
    in_window = [_summary(f) for f in window_problems]
    in_scan = [_summary(f) for f in scan_problems]
    nearby = [p for p in in_scan if p["at"] is not None and math.dist(p["at"], centre) <= inner]
    lost = [p for p in nearby if not _matched(p, in_window)]
    invented = [p for p in in_window if not _matched(p, in_scan)]
    seed_lost = seed is not None and not _matched(seed, in_window)
    return {"ok": not lost and not invented and not seed_lost, "window": in_window, "scan_nearby": nearby,
            "lost": lost, "invented": invented, "seed_reproduced": None if seed is None else not seed_lost}


def summarize_problem(finding: Finding) -> dict:
    return _summary(finding)
