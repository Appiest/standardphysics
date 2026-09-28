"""Where staff work, so the customer checks leave it alone.

Nothing in a scan says which floor is the kitchen. The usual shop puts it
behind the service counter, on the side away from the front door, so that is
the guess: the floor from the counter's back face to the walls behind it, as
wide as the room. The owner moves or removes it; once they have, their answer
is the scenario's and the guess is never made again.
"""

from __future__ import annotations

import math

from standardphysics_contracts import Scenario, SceneGraph, SceneNode, StaffArea, Vec3
from standardphysics_pipeline.footprints import rotation_about_z

from .checks import roles
from .checks.observation import Observation
from .scenario_suggestion import outline_points

SHALLOWEST = 1.0
"""Metres of floor behind a counter before it is somewhere to work, not the gap to a wall."""

DEEPEST = 6.0
"""Metres at most, for a scan whose back walls were never captured."""

Axis = tuple[float, float]


def staff_areas(scenario: Scenario, graph: SceneGraph) -> list[StaffArea]:
    """The owner's areas when they have said, otherwise the guess."""
    if scenario.staff_only is not None:
        return scenario.staff_only
    return guess_staff_areas(graph, scenario)


def with_staff_areas(scenario: Scenario, graph: SceneGraph) -> Scenario:
    return scenario.model_copy(update={"staff_only": staff_areas(scenario, graph)})


def in_staff_area(observation: Observation, areas: list[StaffArea]) -> bool:
    point = observation.locus.point if observation.locus else None
    return point is not None and any(area.holds(point.x, point.y) for area in areas)


def guess_staff_areas(graph: SceneGraph, scenario: Scenario) -> list[StaffArea]:
    outline = outline_points(graph)
    if not outline:
        return []
    door = scenario.stops[0].position
    areas: list[StaffArea] = []
    for counter in roles.service_counters(graph):
        area = _behind(counter, (door.x, door.y), outline)
        if area is None or _overlaps(area, areas) or _holds_a_stop(area, scenario):
            continue
        areas.append(area)
    return areas


def _axes(counter: SceneNode) -> tuple[Axis, Axis, float]:
    """Along the counter, across it, and its depth across."""
    cos_t, sin_t = rotation_about_z(counter)
    along, across = (cos_t, sin_t), (-sin_t, cos_t)
    if counter.dimensions.x >= counter.dimensions.y:
        return along, across, counter.dimensions.y
    return across, along, counter.dimensions.x


def _dot(a: Axis, b: Axis) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _behind(counter: SceneNode, door: Axis, outline: list[Axis]) -> StaffArea | None:
    along, across, thickness = _axes(counter)
    centre = counter.transform.position
    to_door = (door[0] - centre.x, door[1] - centre.y)
    back = across if _dot(across, to_door) < 0 else (-across[0], -across[1])
    face = (centre.x + back[0] * thickness / 2, centre.y + back[1] * thickness / 2)
    offsets = [(x - face[0], y - face[1]) for x, y in outline]
    depth = min(DEEPEST, max(_dot(offset, back) for offset in offsets))
    if depth < SHALLOWEST:
        return None
    spans = [_dot(offset, along) for offset in offsets]
    lo, hi = min(spans), max(spans)
    middle = (lo + hi) / 2
    return StaffArea(
        centre=Vec3(
            x=face[0] + back[0] * depth / 2 + along[0] * middle,
            y=face[1] + back[1] * depth / 2 + along[1] * middle,
            z=0.0,
        ),
        width=hi - lo,
        depth=depth,
        rotation_z_degrees=math.degrees(math.atan2(along[1], along[0])),
    )


def _overlaps(area: StaffArea, areas: list[StaffArea]) -> bool:
    return any(other.holds(area.centre.x, area.centre.y) or area.holds(other.centre.x, other.centre.y) for other in areas)


def _holds_a_stop(area: StaffArea, scenario: Scenario) -> bool:
    return any(area.holds(stop.position.x, stop.position.y) for stop in scenario.stops)
