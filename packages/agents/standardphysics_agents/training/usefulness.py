"""F, whether a layout lets people use what is in it, as three measured shares in [0, 1].

    seats facing    of the seats at a table, desk, counter or ledge, the share whose
                    front turns within `FACING_TOLERANCE_DEGREES` of that surface's
                    nearest side (`snap.facing`, checked against 334 scanned chairs:
                    72% sit within 30 degrees, 79% within 60)
    fronts clear    of the pieces that stand against a wall and are used from the
                    front (shelving, fridges, stations, dispensers on the floor), the
                    share that face out into the room and have the ADA 2010 305.3
                    clear floor space in front of them: 30 by 48 inches for a forward
                    approach, clear of walls and furniture and on the floor
    accessible      226.1 asks for at least five percent of dining surfaces (and never
                    fewer than one) to comply with 902: a top 28 to 34 inches high with
                    a usable side. The share is compliant-and-usable over required,
                    capped at 1

F is the mean of the shares that apply to the room. Each share is also compared
before and after a rearrangement, so a layout that fixes a route by turning
every chair to the wall is caught rather than paid.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from standardphysics_contracts import SceneGraph, SceneNode, Vec3, to_inches, to_meters

from ..checks import roles
from ..checks.dining import required_count, within_range
from ..checks.rectangles import rectangle
from ..fix.use_space import OUTWARD, Room, _approaches, patch
from ..rules import load_pack
from ..snap.facing import backs_onto_wall, facing_error_degrees, is_seat, served_surface, wall_behind

FACING_TOLERANCE_DEGREES = 45.0
FORWARD_APPROACH_WIDTH_INCHES = 30.0
FORWARD_APPROACH_DEPTH_INCHES = 48.0
"""ADA 2010 305.3: clear floor space 30 inches by 48 inches, the 48 running out from the piece for a forward approach."""
AGAINST_WALL_METERS = 0.4
REGRESSION_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Usefulness:
    seats_facing: float | None
    fronts_clear: float | None
    accessible: float | None

    @property
    def score(self) -> float:
        shares = [share for share in (self.seats_facing, self.fronts_clear, self.accessible) if share is not None]
        return sum(shares) / len(shares) if shares else 1.0

    def worse_than(self, before: Usefulness) -> list[str]:
        """The shares this layout lost against `before`."""
        pairs = (("seats_facing", self.seats_facing, before.seats_facing),
                 ("fronts_clear", self.fronts_clear, before.fronts_clear),
                 ("accessible", self.accessible, before.accessible))
        return [name for name, now, was in pairs
                if now is not None and was is not None and now < was - REGRESSION_TOLERANCE]

    def as_dict(self) -> dict:
        return {**asdict(self), "score": round(self.score, 6)}


def _share(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def _xy(node: SceneNode) -> tuple[float, float]:
    return node.transform.position.x, node.transform.position.y


def seats_facing(graph: SceneGraph) -> float | None:
    seated = [node for node in graph.nodes
              if node.kind == "object" and is_seat(node) and served_surface(_xy(node), graph, node.id)]
    return _share([facing_error_degrees(seat, graph) <= FACING_TOLERANCE_DEGREES for seat in seated])


def _front_patch(node: SceneNode):
    yaw = math.radians(math.degrees(math.atan2(node.transform.m[4], node.transform.m[0])) - 90.0)
    direction = (math.cos(yaw), math.sin(yaw))
    depth = to_meters(FORWARD_APPROACH_DEPTH_INCHES)
    reach = node.dimensions.y / 2 + depth / 2
    centre = Vec3(x=node.transform.position.x + direction[0] * reach,
                  y=node.transform.position.y + direction[1] * reach, z=0.0)
    across = (direction[1], -direction[0])
    return rectangle(centre, to_meters(FORWARD_APPROACH_WIDTH_INCHES), depth, across)


def _front_clear(node: SceneNode, graph: SceneGraph, room: Room) -> bool:
    error = facing_error_degrees(node, graph)
    if error is None or error > FACING_TOLERANCE_DEGREES:
        return False
    return room.clear(_front_patch(node), ignoring=node.id)


def fronts_clear(graph: SceneGraph, room: Room) -> float | None:
    fronted = [node for node in graph.nodes
               if node.kind == "object" and backs_onto_wall(node) and wall_behind(
                   _xy(node), graph, node.dimensions.y / 2 + AGAINST_WALL_METERS) is not None]
    return _share([_front_clear(node, graph, room) for node in fronted])


def _usable_side(node: SceneNode, room: Room) -> bool:
    return any(room.clear(patch(node, outward, approach), ignoring=node.id)
               for outward in OUTWARD for approach in _approaches()["surface"])


def accessible_share(graph: SceneGraph, room: Room) -> float | None:
    surfaces = roles.dining_surfaces(graph)
    if not surfaces:
        return None
    rule = load_pack().by_id("dining_surface_height")
    ok = [node for node in surfaces
          if within_range(to_inches(node.transform.position.z + node.dimensions.z / 2), rule)
          and _usable_side(node, room)]
    return min(1.0, len(ok) / max(1, required_count(len(surfaces), rule)))


def usefulness(graph: SceneGraph) -> Usefulness:
    room = Room.of(graph)
    return Usefulness(seats_facing(graph), fronts_clear(graph, room), accessible_share(graph, room))
