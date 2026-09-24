"""U, how usable the tables, desks and counters a rearrangement touched still are, in [0, 1].

For each piece used from the floor (`roles.used_from_the_floor`: a dining or
work surface, or a service counter) that moved, or had something moved within
`NEARBY_METERS` of it, U compares what can still be used after the edits with
the owner's own layout:

    usable sides  a side whose ADA 305.3 clear floor space (30 by 48 inches,
                  read from the rule pack exactly as `no_room_to_use` reads
                  it) is free of walls and furniture, stays on the floor, and
                  joins the walkable floor the room's route starts from
    usable seats  a seat paired with the piece (`quality.seat_table_pairs`)
                  that still has `PULL_OUT_METERS` of free floor behind it and
                  still faces the piece within `FACING_TOLERANCE_DEGREES`,
                  using the seat's visible front (`front_heading_degrees`)

A piece's score is (sides + seats after) / (sides + seats in the owner's
layout), capped at 1; U is the mean over affected pieces, and 1 when nothing
used from the floor was affected. A table slid from the middle of the room
into a corner keeps two of its four sides and scores 0.5.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from standardphysics_contracts import Scenario, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline import gap_between
from standardphysics_pipeline.footprints import Polygon, footprint
from standardphysics_pipeline.occupancy import build_grid

from ..checks import roles
from ..checks.rectangles import rectangle
from ..fix.use_space import OUTWARD, Room, _approaches, patch
from .quality import _xy, front_heading_degrees, moved_ids, seat_table_pairs

NEARBY_METERS = 1.2
"""A moved piece this close to a table, before or after the move, affects the table."""

PULL_OUT_METERS = 0.45
"""Free floor a seat needs behind it to be pulled out and sat in."""

FACING_TOLERANCE_DEGREES = 60.0
"""A seat still faces its table when its front points within this of the table's centre.
Wide, because RoomPlan's chair headings are noisy."""

SAMPLES_PER_SIDE = 5


class Walkable:
    """The floor a route can reach, labelled once per layout."""

    def __init__(self, graph: SceneGraph, scenario: Scenario):
        self.grid = build_grid(graph)
        free = ~self.grid.occupied
        if self.grid.indoors is not None:
            free &= self.grid.indoors
        self.labels, _ = ndimage.label(free)
        self.route_label = self._label_near(scenario.stops[0].position, free)

    def _label_near(self, point: Vec3, free: np.ndarray) -> int:
        cells = np.argwhere(free)
        if cells.size == 0:
            return 0
        row, col = self.grid.to_cell(point.x, point.y)
        nearest = cells[int(np.argmin(np.abs(cells[:, 0] - row) + np.abs(cells[:, 1] - col)))]
        return int(self.labels[nearest[0], nearest[1]])

    def joins(self, area: Polygon) -> bool:
        """Whether any sampled point of the area stands on floor the route can reach."""
        (x0, y0), (x1, y1), _, (x3, y3) = area
        steps = [i / (SAMPLES_PER_SIDE - 1) for i in range(SAMPLES_PER_SIDE)]
        for u in steps:
            for v in steps:
                x, y = x0 + u * (x1 - x0) + v * (x3 - x0), y0 + u * (y1 - y0) + v * (y3 - y0)
                row, col = self.grid.to_cell(x, y)
                if self.grid.contains(row, col) and self.route_label and self.labels[row, col] == self.route_label:
                    return True
        return False


def usable_sides(node: SceneNode, room: Room, floor: Walkable) -> int:
    role = roles.used_from_the_floor(node)
    return sum(
        1 for outward in OUTWARD
        if any(room.clear(area, ignoring=node.id) and floor.joins(area)
               for area in (patch(node, outward, approach) for approach in _approaches()[role]))
    )


def _heading_gap(a: float, b: float) -> float:
    difference = abs(a - b) % 360.0
    return min(difference, 360.0 - difference)


def seat_usable(seat: SceneNode, table: SceneNode, room: Room) -> bool:
    (sx, sy), (tx, ty) = _xy(seat), _xy(table)
    front = front_heading_degrees(seat)
    if _heading_gap(front, math.degrees(math.atan2(ty - sy, tx - sx))) > FACING_TOLERANCE_DEGREES:
        return False
    back = math.radians(front + 180.0)
    reach = seat.dimensions.y / 2 + PULL_OUT_METERS / 2
    behind = Vec3(x=sx + math.cos(back) * reach, y=sy + math.sin(back) * reach, z=0.0)
    area = rectangle(behind, PULL_OUT_METERS, seat.dimensions.x, (math.cos(back), math.sin(back)))
    return room.clear(area, ignoring=seat.id)


def _usable(graph: SceneGraph, node_id, seats: list, room: Room, floor: Walkable) -> int:
    nodes = {node.id: node for node in graph.nodes}
    piece = nodes[node_id]
    seated = sum(1 for seat in seats if seat in nodes and seat_usable(nodes[seat], piece, room))
    return usable_sides(piece, room, floor) + seated


def _near(a: SceneNode, b: SceneNode) -> bool:
    return gap_between(footprint(a), footprint(b)) <= NEARBY_METERS


def affected_pieces(before: SceneGraph, after: SceneGraph, moved: set) -> list:
    """Ids of pieces used from the floor that moved or had a moved piece come or go beside them."""
    layouts = [{node.id: node for node in graph.nodes} for graph in (before, after)]
    found = []
    for node in after.nodes:
        if roles.used_from_the_floor(node) is None:
            continue
        if node.id in moved or any(
            _near(layout[node.id], layout[other]) for layout in layouts for other in moved if other in layout
        ):
            found.append(node.id)
    return found


def usability(before: SceneGraph, after: SceneGraph, owner: SceneGraph, scenario: Scenario) -> float:
    """U for moving from `before` to `after`, against the owner's layout."""
    affected = affected_pieces(before, after, moved_ids(before, after))
    owned = {node.id for node in owner.nodes}
    affected = [node_id for node_id in affected if node_id in owned]
    if not affected:
        return 1.0
    pairs = seat_table_pairs(owner)
    measured = [(owner, Room.of(owner), Walkable(owner, scenario)), (after, Room.of(after), Walkable(after, scenario))]
    scores = []
    for node_id in affected:
        seats = [seat for seat, table in pairs if table == node_id]
        had, has = (_usable(graph, node_id, seats, room, floor) for graph, room, floor in measured)
        scores.append(1.0 if had == 0 else min(1.0, has / had))
    return round(sum(scores) / len(scores), 6)
