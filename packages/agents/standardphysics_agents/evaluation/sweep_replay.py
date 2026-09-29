"""Bounded, deterministic snapshots of actual sweep evaluations for replay."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from standardphysics_contracts import to_inches
from standardphysics_pipeline import Grid
from standardphysics_pipeline.routes import PathResult

from ..workflow_definitions import FunctionalProfile

Cell = tuple[int, int]


@dataclass(frozen=True)
class SweepReplay:
    layout: int
    route: int
    evaluation: int
    profile_id: str
    body_width_inches: float
    cell_size_inches: float
    occupied: tuple[tuple[bool, ...], ...]
    start: Cell
    goal: Cell
    path: tuple[Cell, ...]
    path_widths_inches: tuple[float, ...]
    bottleneck_width_inches: float
    reachable: bool
    fits: bool
    oracle_agrees: bool


class ReplayCollector:
    """Prefer equal fit/too-tight examples, with passing runs as fallback.

    Every saved run comes from a distinct layout and the first input profile.
    This is a demonstration selection, not a representative statistical sample.
    Recording consumes no randomness and cannot change the sweep's cases.
    """

    def __init__(self, limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 100:
            raise ValueError("record_runs must be an integer between 0 and 100")
        self.limit = limit
        self._fits: list[SweepReplay] = []
        self._blocked: list[SweepReplay] = []
        self._layouts: set[int] = set()

    @property
    def runs(self) -> tuple[SweepReplay, ...]:
        selected = self._blocked + self._fits[: self.limit - len(self._blocked)]
        return tuple(sorted(selected, key=lambda run: run.evaluation))

    def consider(
        self, *, layout: int, route: int, evaluation: int,
        profile: FunctionalProfile, grid: Grid, clearance: np.ndarray,
        start: Cell, goal: Cell, result: PathResult, fits: bool,
        oracle_agrees: bool,
    ) -> None:
        target = self._fits if fits else self._blocked
        quota = self.limit if fits else (self.limit + 1) // 2
        if len(target) >= quota or layout in self._layouts:
            return
        self._layouts.add(layout)
        target.append(SweepReplay(
            layout=layout,
            route=route,
            evaluation=evaluation,
            profile_id=profile.id,
            body_width_inches=profile.body_width_inches,
            cell_size_inches=to_inches(grid.cell_size),
            occupied=tuple(tuple(bool(cell) for cell in row) for row in grid.occupied),
            start=start,
            goal=goal,
            path=tuple(result.path),
            path_widths_inches=tuple(to_inches(float(clearance[cell]) * 2) for cell in result.path),
            bottleneck_width_inches=to_inches(result.width_meters),
            reachable=result.reachable,
            fits=fits,
            oracle_agrees=oracle_agrees,
        ))
