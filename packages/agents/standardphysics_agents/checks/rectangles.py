"""Whether a named patch of floor is clear.

Several sections ask about a rectangle rather than a circle: 305.3's clear
floor space, 404.2.4's maneuvering clearance at a door. `MeasurementProvider`
answers circles through `turning_space` and one particular rectangle through
`counter_approach`, so this composes Lane B's published footprint helpers into
the general question.

Asked for as `clear_floor` in docs/archive/handoffs/C-to-B.md. It belongs beside the
other measurements and this goes when it lands.
"""

from __future__ import annotations

from uuid import UUID

from standardphysics_contracts import ClearFloorResult, SceneGraph, Vec3, to_meters
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import Polygon, rotation_about_z, touching
from standardphysics_pipeline.occupancy import blocks_floor

EDGE_TOLERANCE = 0.005
"""How much to pull a patch in on every side before testing it.

`gap_between` reads touching and overlapping the same way, and a patch of
floor in front of a door starts at the wall the door sits in. Without this,
every doorway in every shop reports the wall behind it as standing in its own
clearance. 5 mm is a fifth of an inch, well under anything a scan resolves.
"""


def rectangle(
    centre: Vec3, width: float, depth: float, rotation: tuple[float, float] = (1.0, 0.0)
) -> Polygon:
    """A width by depth patch centred on `centre`, turned by `rotation`."""
    half_w, half_d = width / 2, depth / 2
    cos_t, sin_t = rotation
    corners = ((-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d))
    return [
        (centre.x + x * cos_t - y * sin_t, centre.y + x * sin_t + y * cos_t)
        for x, y in corners
    ]


def intruders(
    graph: SceneGraph, space: Polygon, ignoring: frozenset[UUID] = frozenset()
) -> list[UUID]:
    """Everything standing in that patch of floor."""
    return [
        node.id
        for node in graph.nodes
        if node.id not in ignoring
        and blocks_floor(node)
        and touching(footprint(node), space)
    ]


def facing(graph: SceneGraph, node) -> tuple[float, float]:
    """The node's own rotation about Z, for turning a patch to match it."""
    return rotation_about_z(node)


def clear_floor(
    graph: SceneGraph,
    centre: Vec3,
    width_inches: float,
    depth_inches: float,
    rotation: tuple[float, float] = (1.0, 0.0),
    ignoring: frozenset[UUID] = frozenset(),
) -> ClearFloorResult:
    """The space asked for, and whether anything is standing in it.

    The reported size is the size asked for. What gets tested is that patch
    pulled in by `EDGE_TOLERANCE`, so something merely touching its edge is not
    counted as standing in it.
    """
    space = rectangle(
        centre,
        to_meters(width_inches) - 2 * EDGE_TOLERANCE,
        to_meters(depth_inches) - 2 * EDGE_TOLERANCE,
        rotation,
    )
    return ClearFloorResult(
        inches_wide=width_inches,
        inches_deep=depth_inches,
        center=centre,
        fits=not intruders(graph, space, ignoring),
    )
