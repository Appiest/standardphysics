"""Training rooms from one scan: the whole scan when it is small, windows when it is not.

`plan_centres` says where a big scan's windows go: first around every
furniture-fixable problem that names a piece somebody could move, then around
clusters of movable furniture the problem windows left uncovered. `build_window`
cuts one and keeps it only if it reproduces the scan (`windows.reproduction`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from standardphysics_contracts import Scenario, SceneGraph, bounds_the_room

from .checker import TrainingChecker
from .phantoms import phantoms, pin, scan_errors, unmeasured, without_nodes, without_unmeasured
from .scramble import floor_furniture
from .windows import (
    COVERED_FRACTION,
    MIN_MOVABLE_PIECES,
    WINDOW_RADIUS_METERS,
    Window,
    clipped_route,
    crossing_routes,
    cut,
    floor_under,
    reaches,
    reproduction,
    summarize_problem,
    with_one_floor,
)

MAX_ROUTES_TRIED = 3
"""Routes tried per window before it is dropped; each costs a whole-scan assessment."""

MAX_PHANTOM_SHARE = 0.5
"""A window is dropped when pinned or removed phantoms make up more than half of its
furniture: the room left for a model to rearrange is mostly boxes nobody can move,
many of them floating, and what it learns there is how to work around scan errors.
On A-102 the share runs from 25 to 78 per cent across window centres; 0.4 would
keep only a dozen windows, so the line sits at a majority."""

SMALL_SCAN_NODES = 80
"""A scan with at most this many nodes is one room, and one window."""


@dataclass
class ScanPlan:
    scan_id: str
    graph: SceneGraph
    """The scan with its phantoms pinned."""
    scenario: Scenario
    pinned: list = field(default_factory=list)
    unmeasured: list[dict] = field(default_factory=list)
    removed: list = field(default_factory=list)
    """Floating furniture taken out of the scan as scan errors (`phantoms.scan_errors`)."""
    seeds: list[dict] = field(default_factory=list)
    skipped_seeds: list[dict] = field(default_factory=list)
    clusters: list[tuple[float, float]] = field(default_factory=list)

    @property
    def small(self) -> bool:
        return len(self.graph.nodes) <= SMALL_SCAN_NODES


def movable_named(graph: SceneGraph, problem) -> bool:
    movable = {node.id for node in graph.nodes if node.movable}
    return problem.locus is not None and any(node_id in movable for node_id in problem.locus.node_ids)


def _far_from(centre, taken: list[tuple[float, float]], distance: float) -> bool:
    return all(math.dist(centre, other) > distance for other in taken)


def seed_problems(graph: SceneGraph, checker: TrainingChecker, radius: float) -> tuple[list[dict], list[dict]]:
    """Problems to cut windows around, one window for problems closer than half a radius, and what was passed over."""
    seeds, skipped = [], []
    for problem in checker.fixable_problems(checker.assess(graph)):
        summary = summarize_problem(problem)
        if not movable_named(graph, problem):
            skipped.append({**summary, "why": "names no movable piece"})
        elif not _far_from(tuple(summary["at"]), [tuple(s["at"]) for s in seeds], radius / 2):
            skipped.append({**summary, "why": "inside another problem's window"})
        else:
            seeds.append(summary)
    return seeds, skipped


def _cluster_around(piece, pieces, radius: float) -> list:
    here = (piece.transform.position.x, piece.transform.position.y)
    return [other for other in pieces if math.dist(here, (other.transform.position.x, other.transform.position.y)) <= radius]


def cluster_centres(graph: SceneGraph, taken: list[tuple[float, float]], radius: float) -> list[tuple[float, float]]:
    """Centres of furniture clusters no window covers yet, densest first."""
    covered_within = radius * COVERED_FRACTION
    uncovered = [piece for piece in floor_furniture(graph)
                 if _far_from((piece.transform.position.x, piece.transform.position.y), taken, covered_within)]
    centres = []
    while uncovered:
        densest = max(uncovered, key=lambda piece: len(_cluster_around(piece, uncovered, radius)))
        group = _cluster_around(densest, uncovered, radius)
        centre = (sum(p.transform.position.x for p in group) / len(group),
                  sum(p.transform.position.y for p in group) / len(group))
        uncovered = [p for p in uncovered if math.dist(centre, (p.transform.position.x, p.transform.position.y))
                     > covered_within and p.id != densest.id]
        if len(group) >= MIN_MOVABLE_PIECES:
            centres.append(centre)
    return centres


def plan_scan(scan_id: str, graph: SceneGraph, scenario: Scenario, radius: float = WINDOW_RADIUS_METERS) -> ScanPlan:
    dropped = [{"node_id": str(node.id), "kind": node.kind, "label": node.label} for node in unmeasured(graph)]
    measured = without_unmeasured(graph)
    pinned = phantoms(measured)
    removed = scan_errors(measured, pinned)
    cleaned = without_nodes(measured, {node.id for node in removed})
    plan = ScanPlan(scan_id, pin(cleaned, pinned), scenario, pinned, dropped, removed)
    if plan.small:
        return plan
    plan.seeds, plan.skipped_seeds = seed_problems(plan.graph, TrainingChecker(scenario), radius)
    plan.clusters = cluster_centres(plan.graph, [tuple(seed["at"]) for seed in plan.seeds], radius)
    return plan


def _pinned_in(plan: ScanPlan, graph: SceneGraph) -> list[str]:
    present = {node.id for node in graph.nodes}
    return [str(item.node_id) for item in plan.pinned if item.node_id in present]


def phantom_share(plan: ScanPlan, graph: SceneGraph, centre=None, radius: float = WINDOW_RADIUS_METERS) -> float:
    """Pinned and removed phantoms as a share of the furniture a window started with."""
    pinned = len(_pinned_in(plan, graph))
    removed = sum(1 for node in plan.removed if centre is None or reaches(node, centre, radius))
    movable = sum(1 for node in graph.nodes if node.movable and not bounds_the_room(node))
    total = pinned + removed + movable
    return (pinned + removed) / total if total else 0.0


def _too_phantom(share: float, log: dict) -> dict | None:
    if share <= MAX_PHANTOM_SHARE:
        return None
    return {**log, "kept": False, "why": f"phantoms are {share:.0%} of the furniture", "phantom_share": round(share, 3)}


def whole_scan(plan: ScanPlan) -> tuple[Window | None, dict]:
    window_id = f"{plan.scan_id}:whole"
    share = phantom_share(plan, plan.graph)
    log = {"window_id": window_id, "phantom_share": round(share, 3)}
    dropped = _too_phantom(share, log)
    if dropped:
        return None, dropped
    window = Window(window_id, plan.scan_id, None, plan.graph, plan.scenario, "scan",
                    pinned=_pinned_in(plan, plan.graph))
    return window, {**log, "kept": True, "route": "scan", "objects": window.object_count(),
                    "movable": window.movable_count()}


def candidate_routes(plan: ScanPlan, reference: SceneGraph, window: SceneGraph, centre,
                     radius: float) -> list[tuple[Scenario, str]]:
    clipped = clipped_route(reference, plan.scenario, TrainingChecker(plan.scenario), centre, radius)
    crossing = [(route, "crossing") for route in crossing_routes(window, centre, radius)]
    return ([(clipped, "clipped")] if clipped is not None else []) + crossing


def _tried(window_id: str, plan: ScanPlan, graph: SceneGraph, reference: SceneGraph, centre, seed, route) -> Window:
    scenario, kind = route
    checker = TrainingChecker(scenario)
    matched = reproduction(checker.fixable_problems(checker.assess(graph)),
                           checker.fixable_problems(checker.assess(reference)), centre, seed)
    return Window(window_id, plan.scan_id, centre, graph, scenario, kind, seed, matched, _pinned_in(plan, graph))


def build_window(plan: ScanPlan, centre: tuple[float, float], seed: dict | None, window_id: str,
                 radius: float = WINDOW_RADIUS_METERS) -> tuple[Window | None, dict]:
    """One window and a log entry saying whether it was kept and why.

    Routes are tried in order, the scan's own leg first, until one gives a
    window that reproduces the scan.
    """
    log = {"window_id": window_id, "centre": [round(centre[0], 3), round(centre[1], 3)], "seed": seed}
    graph = cut(plan.graph, centre, radius)
    if graph is None:
        return None, {**log, "kept": False, "why": "no floor under the centre"}
    share = phantom_share(plan, graph, centre, radius)
    log["phantom_share"] = round(share, 3)
    dropped = _too_phantom(share, log)
    if dropped:
        return None, dropped
    reference = with_one_floor(plan.graph, floor_under(plan.graph, centre))
    attempts = []
    for route in candidate_routes(plan, reference, graph, centre, radius)[:MAX_ROUTES_TRIED]:
        window = _tried(window_id, plan, graph, reference, centre, seed, route)
        attempts.append({"route": route[0].name, **window.reproduction})
        if window.reproduction["ok"]:
            return window, {**log, "kept": True, "route": window.route, "scenario": route[0].name,
                            "objects": window.object_count(), "movable": window.movable_count(), "attempts": attempts}
    why = "no route reproduces the scan" if attempts else "no free floor for a route"
    return None, {**log, "kept": False, "why": why, "objects": len(graph.nodes) - 1, "attempts": attempts}
