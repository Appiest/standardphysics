"""Whether a person can still pull up to a table or a counter and use it.

ADA 2010 305.3 sizes the clear floor space a wheelchair user needs at an
element: 30 by 48 inches. 902.2 has them pull up to a dining or work surface
face-on, so the 48 runs straight out from its edge; 904.4 lets them pull up to
a service counter side-on or face-on. A piece keeps its use while at least one
of its sides still has that patch free of walls, other furniture and the edge
of the floor.

Without this, sliding a table flush into a corner is a very good way to widen
an aisle, and nobody can sit at the table afterwards.

The numbers are the ones `service_counter_approach` already carries for 305.3,
read from the rule pack rather than restated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, lies_flat, to_meters
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import Polygon, distance_outside, floor_polygon, rotation_about_z, touching
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks import roles
from ..checks.rectangles import EDGE_TOLERANCE, rectangle
from ..checks.walls import upright_walls
from ..rules import load_pack

CLEAR_FLOOR_RULE = "service_counter_approach"
"""The rule that carries 305.3's 30 by 48 inch clear floor space."""

FLOOR_EDGE_SLACK = 0.01
"""A centimetre, so a patch that ends exactly on the floor's edge is still on the floor."""

OUTWARD = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))
"""The four sides of a piece, as the direction each one faces in the piece's own frame."""


@dataclass(frozen=True)
class Approach:
    along: float
    """Metres of floor running beside the edge."""
    out: float
    """Metres of floor running straight out from the edge."""


@lru_cache(maxsize=1)
def _approaches() -> dict[str, tuple[Approach, ...]]:
    rule = load_pack().by_id(CLEAR_FLOOR_RULE)
    long_side = to_meters(rule.parameter("clear_width_min_inches"))
    short_side = to_meters(rule.parameter("clear_depth_min_inches"))
    face_on = Approach(along=short_side, out=long_side)
    side_on = Approach(along=long_side, out=short_side)
    return {"surface": (face_on,), "counter": (side_on, face_on)}


def reach(role: roles.UsedFromTheFloor) -> float:
    """How far from a piece's edge its clear floor space can extend."""
    return max(approach.out for approach in _approaches()[role])


@dataclass(frozen=True)
class Room:
    """What can stand in a patch of clear floor, gathered once per layout."""

    obstacles: tuple[tuple[UUID, Polygon], ...]
    floor: Polygon | None

    @classmethod
    def of(cls, graph: SceneGraph) -> Room:
        walls = {node.id for node in upright_walls(graph)}
        obstacles = tuple(
            (node.id, footprint(node))
            for node in graph.nodes
            if (node.id in walls or blocks_floor(node)) and not roles.is_seating(node)
        )
        floor = next((floor_polygon(node) for node in graph.nodes if lies_flat(node)), None)
        return cls(obstacles=obstacles, floor=floor)

    def clear(self, patch: Polygon, ignoring: UUID) -> bool:
        return self._on_the_floor(patch) and not any(
            touching(shape, patch) for node_id, shape in self.obstacles if node_id != ignoring
        )

    def _on_the_floor(self, patch: Polygon) -> bool:
        if self.floor is None:
            return True
        return all(distance_outside(self.floor, corner, FLOOR_EDGE_SLACK) == 0.0 for corner in patch)


def patch(node: SceneNode, outward: tuple[float, float], approach: Approach) -> Polygon:
    """The clear floor space against one side of the piece, pulled in by `EDGE_TOLERANCE`.

    Pulling it in is what lets the piece itself, or a wall the patch merely
    touches, not count as standing in it.
    """
    cos_t, sin_t = rotation_about_z(node)
    facing_x = outward[0] != 0.0
    half_depth = (node.dimensions.x if facing_x else node.dimensions.y) / 2
    offset = half_depth + approach.out / 2
    local_x, local_y = outward[0] * offset, outward[1] * offset
    centre = node.transform.position
    at = Vec3(
        x=centre.x + local_x * cos_t - local_y * sin_t,
        y=centre.y + local_x * sin_t + local_y * cos_t,
        z=centre.z,
    )
    across, deep = (approach.out, approach.along) if facing_x else (approach.along, approach.out)
    return rectangle(at, across - 2 * EDGE_TOLERANCE, deep - 2 * EDGE_TOLERANCE, (cos_t, sin_t))


def has_room_to_use(room: Room, node: SceneNode, role: roles.UsedFromTheFloor) -> bool:
    """Whether any side of the piece still has its clear floor space."""
    return any(
        room.clear(patch(node, outward, approach), ignoring=node.id)
        for approach in _approaches()[role]
        for outward in OUTWARD
    )
