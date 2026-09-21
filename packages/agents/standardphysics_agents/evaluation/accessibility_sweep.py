"""High-volume, deterministic route checks over generated floor plans.

This exercises the production widest-path search without making network calls.
Each generated room has perimeter walls and random rectangular furniture.  A
connected-component oracle independently checks whether each functional
profile can travel between the same two clear staging points.

The sweep is geometry evidence, not a legal determination.  It deliberately
does not load the rule ledger or label a generated layout ADA compliant.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage
from standardphysics_contracts import to_meters
from standardphysics_pipeline import Grid, clearance_map, widest_path

from ..workflows import DEFAULT_PROFILES, FunctionalProfile
from .sweep_replay import ReplayCollector, SweepReplay

DEFAULT_EVALUATIONS = 1_000_000
DEFAULT_ROUTES_PER_LAYOUT = 5
DEFAULT_SEED = 2_026_091_2
DEFAULT_OUTPUT_PATH = Path("runs/accessibility-sweep.json")

GRID_SIZE = 24
MIN_ROOM_SIZE = 18
CELL_SIZE_METERS = to_meters(12.0)
MAX_FURNITURE = 8
MAX_FURNITURE_SIZE = 4
MAX_FAILURE_EXAMPLES = 20
FLOAT_TOLERANCE = 1e-12
EIGHT_CONNECTED = np.ones((3, 3), dtype=np.int8)


@dataclass(frozen=True)
class SweepFailure:
    layout: int
    route: int
    profile_id: str
    reason: str


@dataclass(frozen=True)
class AccessibilitySweepResult:
    seed: int
    requested_evaluations: int
    evaluations: int
    layouts: int
    routes: int
    failures: int
    reachable_routes: int
    profile_evaluations: dict[str, int]
    profile_route_fits: dict[str, int]
    elapsed_seconds: float
    digest: str
    failure_examples: tuple[SweepFailure, ...] = field(default_factory=tuple)
    recorded_runs: tuple[SweepReplay, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.evaluations == self.requested_evaluations and self.failures == 0

    def as_dict(self) -> dict:
        return {
            **asdict(self),
            "passed": self.passed,
        }


def _check_inputs(evaluations: int, routes_per_layout: int, profiles: tuple) -> None:
    if evaluations < 1:
        raise ValueError("evaluations must be positive")
    if routes_per_layout < 1:
        raise ValueError("routes_per_layout must be positive")
    if not profiles:
        raise ValueError("at least one functional profile is required")


def _profile_verdict(profile, result, component_labels, start, goal, route_errors):
    """Whether this body fits the route, and everything wrong with the answer.

    Connected components are the independent oracle: a route the width search
    says fits has to join the same free component at that body width. A
    disagreement means one of the two is wrong, so it is recorded rather than
    resolved here.
    """
    reference_fit = _same_component(component_labels[profile.body_width_inches], start, goal)
    measured_fit = (
        result.reachable
        and result.clearance_radius + FLOAT_TOLERANCE >= to_meters(profile.body_width_inches / 2)
    )
    reasons = list(route_errors)
    if measured_fit != reference_fit:
        reasons.append(
            f"width oracle disagreed: widest_path={measured_fit}, components={reference_fit}"
        )
    return measured_fit, reasons


def _remember(examples: list, layout: int, route: int, profile, reasons: list[str]) -> None:
    if len(examples) < MAX_FAILURE_EXAMPLES:
        examples.append(
            SweepFailure(layout=layout, route=route, profile_id=profile.id, reason="; ".join(reasons))
        )


def run_accessibility_sweep(
    *,
    evaluations: int = DEFAULT_EVALUATIONS,
    seed: int = DEFAULT_SEED,
    routes_per_layout: int = DEFAULT_ROUTES_PER_LAYOUT,
    profiles: tuple[FunctionalProfile, ...] = DEFAULT_PROFILES,
    record_runs: int = 0,
) -> AccessibilitySweepResult:
    """Check exactly ``evaluations`` generated layout/route/profile cases."""
    _check_inputs(evaluations, routes_per_layout, profiles)
    recorder = ReplayCollector(record_runs)

    started = time.perf_counter()
    rng = np.random.default_rng(seed)
    digest = hashlib.sha256()
    profile_evaluations = {profile.id: 0 for profile in profiles}
    profile_route_fits = {profile.id: 0 for profile in profiles}
    failures = 0
    examples: list[SweepFailure] = []
    completed = 0
    layout_index = 0
    route_count = 0
    reachable_routes = 0
    maximum_radius = to_meters(
        max(profile.body_width_inches for profile in profiles) / 2
    )

    while completed < evaluations:
        grid, clearance, staging = _generated_layout(rng, maximum_radius)
        digest.update(grid.occupied.tobytes())
        component_labels = _component_labels(grid, clearance, profiles)
        free_components = ndimage.label(
            ~grid.occupied, structure=EIGHT_CONNECTED
        )[0]

        for route_index in range(routes_per_layout):
            if completed >= evaluations:
                break
            start, goal = _route_pair(rng, staging)
            digest.update(np.asarray((*start, *goal), dtype=np.int16).tobytes())
            result = widest_path(
                grid,
                clearance,
                start,
                goal,
                endpoint_exemption=0.0,
            )
            route_count += 1
            reachable_routes += int(result.reachable)
            route_errors = _route_errors(
                grid, clearance, free_components, start, goal, result
            )

            for profile in profiles:
                if completed >= evaluations:
                    break
                profile_evaluations[profile.id] += 1
                measured_fit, reasons = _profile_verdict(
                    profile, result, component_labels, start, goal, route_errors
                )
                profile_route_fits[profile.id] += int(measured_fit)
                if reasons:
                    failures += 1
                    _remember(examples, layout_index, route_index, profile, reasons)
                if profile == profiles[0]:
                    recorder.consider(
                        layout=layout_index,
                        route=route_index,
                        evaluation=completed + 1,
                        profile=profile,
                        grid=grid,
                        clearance=clearance,
                        start=start,
                        goal=goal,
                        result=result,
                        fits=bool(measured_fit),
                        oracle_agrees=not reasons,
                    )
                digest.update(
                    f"{profile.id}:{int(measured_fit)}:{result.clearance_radius:.12f}"
                    .encode("ascii")
                )
                completed += 1
        layout_index += 1

    return AccessibilitySweepResult(
        seed=seed,
        requested_evaluations=evaluations,
        evaluations=completed,
        layouts=layout_index,
        routes=route_count,
        failures=failures,
        reachable_routes=reachable_routes,
        profile_evaluations=profile_evaluations,
        profile_route_fits=profile_route_fits,
        elapsed_seconds=time.perf_counter() - started,
        digest=digest.hexdigest(),
        failure_examples=tuple(examples),
        recorded_runs=recorder.runs,
    )


def save_accessibility_sweep(
    result: AccessibilitySweepResult,
    path: Path = DEFAULT_OUTPUT_PATH,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _generated_layout(
    rng: np.random.Generator,
    maximum_radius: float,
) -> tuple[Grid, np.ndarray, np.ndarray]:
    """Generate until two wheelchair-clear, well-separated staging cells exist."""
    while True:
        occupied = np.ones((GRID_SIZE, GRID_SIZE), dtype=bool)
        rows = int(rng.integers(MIN_ROOM_SIZE, GRID_SIZE + 1))
        cols = int(rng.integers(MIN_ROOM_SIZE, GRID_SIZE + 1))
        row0 = (GRID_SIZE - rows) // 2
        col0 = (GRID_SIZE - cols) // 2
        row1, col1 = row0 + rows, col0 + cols
        occupied[row0 + 1 : row1 - 1, col0 + 1 : col1 - 1] = False

        furniture_count = int(rng.integers(1, MAX_FURNITURE + 1))
        for _ in range(furniture_count):
            height = int(rng.integers(1, MAX_FURNITURE_SIZE + 1))
            width = int(rng.integers(1, MAX_FURNITURE_SIZE + 1))
            row = int(rng.integers(row0 + 1, row1 - 1))
            col = int(rng.integers(col0 + 1, col1 - 1))
            occupied[row : min(row + height, row1 - 1),
                     col : min(col + width, col1 - 1)] = True

        owner = np.full(occupied.shape, -1, dtype=np.int32)
        grid = Grid(0.0, 0.0, CELL_SIZE_METERS, occupied, owner, [])
        clearance = clearance_map(grid)
        staging = np.argwhere((~occupied) & (clearance >= maximum_radius))
        if len(staging) >= 2 and _has_long_pair(staging):
            return grid, clearance, staging


def _has_long_pair(cells: np.ndarray) -> bool:
    span = cells.max(axis=0) - cells.min(axis=0)
    return int(span.sum()) >= MIN_ROOM_SIZE // 2


def _route_pair(
    rng: np.random.Generator, staging: np.ndarray
) -> tuple[tuple[int, int], tuple[int, int]]:
    minimum_distance = MIN_ROOM_SIZE // 2
    for _ in range(32):
        first, second = rng.choice(len(staging), size=2, replace=False)
        start = staging[int(first)]
        goal = staging[int(second)]
        if int(np.abs(start - goal).sum()) >= minimum_distance:
            return _cell(start), _cell(goal)

    start = staging[int(rng.integers(0, len(staging)))]
    distances = np.abs(staging - start).sum(axis=1)
    return _cell(start), _cell(staging[int(np.argmax(distances))])


def _cell(value: np.ndarray) -> tuple[int, int]:
    return int(value[0]), int(value[1])


def _component_labels(
    grid: Grid,
    clearance: np.ndarray,
    profiles: tuple[FunctionalProfile, ...],
) -> dict[float, np.ndarray]:
    labels: dict[float, np.ndarray] = {}
    for width in {profile.body_width_inches for profile in profiles}:
        radius = to_meters(width / 2)
        traversable = (~grid.occupied) & (
            clearance + FLOAT_TOLERANCE >= radius
        )
        labels[width] = ndimage.label(
            traversable, structure=EIGHT_CONNECTED
        )[0]
    return labels


def _same_component(
    labels: np.ndarray, start: tuple[int, int], goal: tuple[int, int]
) -> bool:
    component = int(labels[start])
    return component != 0 and component == int(labels[goal])


def _route_errors(
    grid, clearance, free_components, start, goal, result
) -> tuple[str, ...]:
    expected_reachable = _same_component(free_components, start, goal)
    errors: list[str] = []
    if result.reachable != expected_reachable:
        errors.append(
            "reachability oracle disagreed: "
            f"widest_path={result.reachable}, flood_fill={expected_reachable}"
        )
    if not result.reachable:
        if result.path:
            errors.append("unreachable result returned a path")
        return tuple(errors)
    if not result.path:
        errors.append("reachable result returned no path")
        return tuple(errors)
    if result.path[0] != start or result.path[-1] != goal:
        errors.append("path endpoints do not match the requested route")
    if any(grid.occupied[cell] for cell in result.path):
        errors.append("path crosses occupied furniture or a wall")
    if any(
        max(abs(a[0] - b[0]), abs(a[1] - b[1])) != 1
        for a, b in zip(result.path, result.path[1:])
    ):
        errors.append("path contains a disconnected step")

    measured_cells = result.path[1:-1] or result.path
    path_radius = min(float(clearance[cell]) for cell in measured_cells)
    if not math.isclose(
        result.clearance_radius,
        path_radius,
        rel_tol=FLOAT_TOLERANCE,
        abs_tol=FLOAT_TOLERANCE,
    ):
        errors.append("reported bottleneck does not match the returned path")
    return tuple(errors)
