"""The seam between Lane B and Lane C.

Lane B implements this against real geometry. Lane C codes against it from the
first hour using packages/fixtures/stub_measurements.py, and swaps the
constructor argument when Lane B's PROGRESS_B.json lists it ready.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from .geometry import Vec3
from .scene import Scenario, SceneGraph


class WidthResult(BaseModel):
    """The bottleneck of a route leg, and where it is."""

    inches: float
    pinch_point: Vec3
    blocking_node_ids: list[UUID]
    path: list[Vec3] = []
    reachable: bool = True
    needs_measurement: bool = False
    """The scan cannot settle this width, such as a door's clear opening at 90
    degrees. Checks turn it into a question with a photo request, never a pass.
    """


class ClearFloorResult(BaseModel):
    inches_wide: float
    inches_deep: float
    center: Vec3
    fits: bool


class HeightResult(BaseModel):
    inches: float
    node_id: UUID
    measured_at: Vec3
    uncertainty_inches: float | None = None
    needs_measurement: bool = False


class MeasurementProvider(Protocol):
    """Every geometric question the checks ask.

    Implementations return the value, and the location that value came from,
    because every finding needs a locus to point the camera at.
    """

    def route_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult: ...

    def turn_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult: ...

    def turning_space(self, graph: SceneGraph, at: Vec3) -> ClearFloorResult: ...

    def door_clear_width(self, graph: SceneGraph, door_id: UUID) -> WidthResult: ...

    def counter_height(self, graph: SceneGraph, counter_id: UUID) -> HeightResult: ...

    def counter_approach(
        self, graph: SceneGraph, counter_id: UUID
    ) -> ClearFloorResult: ...
