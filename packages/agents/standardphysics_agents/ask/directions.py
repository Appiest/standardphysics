"""Back, front, left and right, in the shop's own terms.

An owner says "move the seating to the back". Back means away from the door
they came in through, which is a fact about the route rather than about the
compass. The scenario knows where the door is and where the counter is, so the
axes come from there and a shop scanned facing any direction works the same.
"""

from __future__ import annotations

import math
from typing import Literal

from standardphysics_contracts import Scenario, SceneGraph, SceneNode, Vec3, lies_flat

Direction = Literal["back", "front", "left", "right", "apart", "together"]

DIRECTIONS: tuple[Direction, ...] = (
    "back", "front", "left", "right", "apart", "together",
)

RELATIVE: frozenset[str] = frozenset({"apart", "together"})
"""Directions measured between the named pieces rather than across the room."""


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    return (0.0, 1.0) if length < 1e-9 else (dx / length, dy / length)


def _route_axis(scenario: Scenario) -> tuple[float, float]:
    entrance = scenario.stops[0].position
    far = max(
        (stop.position for stop in scenario.stops[1:]),
        key=lambda p: math.dist((p.x, p.y), (entrance.x, entrance.y)),
        default=entrance,
    )
    return _unit(far.x - entrance.x, far.y - entrance.y)


def _room_axes(graph: SceneGraph | None) -> list[tuple[float, float]]:
    """The four directions the room's own walls run in."""
    if graph is None:
        return []
    from standardphysics_pipeline.footprints import rotation_about_z

    floors = [node for node in graph.nodes if lies_flat(node)]
    if not floors:
        return []
    cos_t, sin_t = rotation_about_z(floors[0])
    return [(cos_t, sin_t), (-cos_t, -sin_t), (-sin_t, cos_t), (sin_t, -cos_t)]


def shop_axes(
    scenario: Scenario, graph: SceneGraph | None = None
) -> tuple[tuple[float, float], tuple[float, float]]:
    """(back, right). Back runs along the room, away from the entrance.

    The route says which end is the back, and the room says which way the back
    runs. Taking the direction straight off the route would tilt every answer
    by however far off centre the counter happens to sit, so "two rows 15 feet
    apart" would come out at 14 foot 11 because the door is not centred.
    """
    heading = _route_axis(scenario)
    candidates = _room_axes(graph)
    back = (
        max(candidates, key=lambda axis: axis[0] * heading[0] + axis[1] * heading[1])
        if candidates
        else heading
    )
    return back, (back[1], -back[0])


def axis_for(direction: Direction, scenario: Scenario) -> tuple[float, float] | None:
    """The way to slide, or None when the direction is between the pieces."""
    back, right = shop_axes(scenario)
    return {
        "back": back,
        "front": (-back[0], -back[1]),
        "right": right,
        "left": (-right[0], -right[1]),
    }.get(direction)


def centroid(nodes: list[SceneNode]) -> Vec3:
    return Vec3(
        x=sum(n.transform.position.x for n in nodes) / len(nodes),
        y=sum(n.transform.position.y for n in nodes) / len(nodes),
        z=0.0,
    )


def outward(node: SceneNode, middle: Vec3) -> tuple[float, float]:
    """Which way this piece sits from the middle of the group."""
    return _unit(
        node.transform.position.x - middle.x, node.transform.position.y - middle.y
    )
