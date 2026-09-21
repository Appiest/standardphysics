"""A working MeasurementProvider for the fixture shop.

Lane C builds every check against this from the first hour, then swaps the
constructor argument for Lane B's real implementation when PROGRESS_B.json
lists it ready. Nothing else in Lane C changes.

This computes widths from the graph rather than returning constants, so moving
a display case changes the answer. That makes the whole fix loop testable
before the real pipeline exists.

It is deliberately simple: axis-aligned footprints and a straight corridor
between stops. Lane B's version rasterizes an occupancy grid and binary
searches the distance transform, which handles geometry this one cannot.
"""

from __future__ import annotations

from uuid import UUID

from standardphysics_contracts import (
    ClearFloorResult,
    HeightResult,
    Scenario,
    SceneGraph,
    Vec3,
    WidthResult,
    to_inches,
)


def _footprint(node) -> tuple[float, float, float, float]:
    p = node.transform.position
    hx, hy = node.dimensions.x / 2, node.dimensions.y / 2
    return (p.x - hx, p.x + hx, p.y - hy, p.y + hy)


class _Barrier:
    """A run of obstacles with no passage through it.

    Each edge remembers which node owns it, because a finding points the camera
    at the two objects forming the gap, not at whatever merged last.
    """

    __slots__ = ("x0", "x1", "left_owner", "right_owner", "left_y", "right_y")

    def __init__(self, x0: float, x1: float, y: float, node_id: UUID):
        self.x0, self.x1 = x0, x1
        self.left_owner = self.right_owner = node_id
        self.left_y = self.right_y = y

    def extend(self, x1: float, y: float, node_id: UUID) -> None:
        self.x1, self.right_owner, self.right_y = x1, node_id, y


def _barriers_crossing(graph: SceneGraph, start: Vec3, end: Vec3) -> list[_Barrier]:
    lo_y, hi_y = sorted((start.y, end.y))
    found = []
    for node in graph.obstacles():
        if node.kind in ("floor", "door"):
            continue
        x0, x1, y0, y1 = _footprint(node)
        if y1 >= lo_y and y0 <= hi_y:
            found.append(_Barrier(x0, x1, node.transform.position.y, node.id))
    return found


def _merge(barriers: list[_Barrier]) -> list[_Barrier]:
    """Overlapping obstacles form one barrier, so the space between them is
    not a passage."""
    ordered = sorted(barriers, key=lambda b: b.x0)
    merged = [ordered[0]]
    for barrier in ordered[1:]:
        last = merged[-1]
        if barrier.x0 <= last.x1:
            if barrier.x1 > last.x1:
                last.extend(barrier.x1, barrier.right_y, barrier.right_owner)
        else:
            merged.append(barrier)
    return merged


def _widest_gap(merged: list[_Barrier]) -> tuple[_Barrier, _Barrier] | None:
    gaps = list(zip(merged, merged[1:]))
    if not gaps:
        return None
    return max(gaps, key=lambda pair: pair[1].x0 - pair[0].x1)


class FixtureMeasurements:
    """Implements the MeasurementProvider protocol for the synthetic shop."""

    def route_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult:
        start = scenario.stops[leg_index].position
        end = scenario.stops[leg_index + 1].position
        barriers = _barriers_crossing(graph, start, end)
        if not barriers:
            return WidthResult(
                inches=to_inches(6.0), pinch_point=end, blocking_node_ids=[]
            )

        merged = _merge(barriers)
        gap = _widest_gap(merged)
        if gap is None:
            return WidthResult(
                inches=0.0, pinch_point=end, blocking_node_ids=[], reachable=False
            )

        left, right = gap
        center_x = (left.x1 + right.x0) / 2
        pinch_y = (left.right_y + right.left_y) / 2
        pinch = Vec3(x=center_x, y=pinch_y, z=0.0)
        return WidthResult(
            inches=to_inches(right.x0 - left.x1),
            pinch_point=pinch,
            blocking_node_ids=[left.right_owner, right.left_owner],
            path=[start, pinch, end],
        )

    def turn_clear_width(
        self, graph: SceneGraph, scenario: Scenario, leg_index: int
    ) -> WidthResult:
        return self.route_clear_width(graph, scenario, leg_index)

    def turning_space(self, graph: SceneGraph, at: Vec3) -> ClearFloorResult:
        clear = 1.6
        for node in graph.obstacles():
            if node.kind in ("floor", "door"):
                continue
            x0, x1, y0, y1 = _footprint(node)
            dx = max(x0 - at.x, 0.0, at.x - x1)
            dy = max(y0 - at.y, 0.0, at.y - y1)
            clear = min(clear, max((dx * dx + dy * dy) ** 0.5, 0.0))
        side = to_inches(clear * 2)
        return ClearFloorResult(
            inches_wide=side, inches_deep=side, center=at, fits=side >= 60.0
        )

    def door_clear_width(self, graph: SceneGraph, door_id: UUID) -> WidthResult:
        door = graph.by_id(door_id)
        return WidthResult(
            inches=to_inches(door.dimensions.x),
            pinch_point=door.transform.position,
            blocking_node_ids=[door_id],
            needs_measurement=True,
        )

    def counter_height(self, graph: SceneGraph, counter_id: UUID) -> HeightResult:
        counter = graph.by_id(counter_id)
        return HeightResult(
            inches=to_inches(counter.dimensions.z),
            node_id=counter_id,
            measured_at=counter.transform.position,
        )

    def counter_approach(
        self, graph: SceneGraph, counter_id: UUID
    ) -> ClearFloorResult:
        counter = graph.by_id(counter_id)
        p = counter.transform.position
        front = Vec3(x=p.x, y=p.y - counter.dimensions.y / 2 - 0.5, z=0.0)
        return self.turning_space(graph, front)
