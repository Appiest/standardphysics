"""What a rearrangement costs the owner, and how the room looks afterwards.

The gate asks one question of a layout: does it pass the checks. Plenty of
layouts do. A search that measures guesses in order of distance moved takes
the first one that passes, and the first one is often a table shoved against
another table at sixty degrees, because that was a short slide. Every guess is
now ranked by this cost before it is measured, so a square, uncrowded room with
each table's chairs still at it is tried first. Nothing here accepts a layout;
the gate still does.

The cost is in metres of sliding. Effort weights a slide by the size of the
piece, and each thing an owner would object to adds a fixed number of metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from standardphysics_contracts import NodeMove, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline import footprint, gap_between
from standardphysics_pipeline.footprints import rotation_about_z
from standardphysics_pipeline.occupancy import blocks_floor

from ..checks import roles
from .constraints import interior_bounds, overlapping
from .moves import apply_moves
from .strategies import TURN_DISRUPTION_METERS

BREATHING_ROOM_METERS = 0.45
"""18 inches. Furniture nearer than this to other furniture reads as crammed,
and the gap left over is too narrow for anyone to use."""

FLUSH_METERS = 0.10
"""Nearer than this to a wall counts as standing against it, which is tidy."""

SEAT_REACH_METERS = 0.35
"""A chair further than this from its table is no longer seated at it."""

LIGHTEST_WEIGHT_M2 = 0.25
"""The smallest footprint effort is weighted by, so moving a stool is never free."""

CRAMMED_WEIGHT = 4.0
"""Metres of effort per metre of breathing room lost."""

STRANDED_CHAIR_METERS = 1.5
CROOKED_METERS_PER_45_DEGREES = 2.0
OUTSIDE_WEIGHT = 10.0


def room_axis_degrees(graph: SceneGraph) -> float:
    """Which way the walls run, as an angle within a quarter turn.

    Each wall's heading is folded four times so walls at right angles agree,
    then averaged with longer walls counting for more.
    """
    east = north = 0.0
    for wall in (node for node in graph.nodes if node.kind == "wall"):
        cos_t, sin_t = rotation_about_z(wall)
        folded = 4 * math.atan2(sin_t, cos_t)
        length = max(wall.dimensions.x, wall.dimensions.y)
        east += length * math.cos(folded)
        north += length * math.sin(folded)
    return math.degrees(math.atan2(north, east)) / 4


def squaring_turn(node: SceneNode, axis_degrees: float) -> float:
    """The smallest turn, in degrees, that lines a piece up with the walls."""
    cos_t, sin_t = rotation_about_z(node)
    heading = math.degrees(math.atan2(sin_t, cos_t))
    return (axis_degrees - heading + 45.0) % 90.0 - 45.0


def tables_by_chair(graph: SceneGraph) -> dict[UUID, UUID]:
    """Each movable chair pulled up to a movable table, and that table."""
    tables = [node for node in roles.dining_surfaces(graph) if node.movable]
    found: dict[UUID, UUID] = {}
    for chair in (node for node in roles.seating(graph) if node.movable):
        gaps = [
            (gap_between(footprint(chair), footprint(table)), table.id)
            for table in tables
            if _clearance(chair, table) <= SEAT_REACH_METERS
        ]
        nearest = min(gaps, default=None, key=lambda pair: pair[0])
        if nearest is not None and nearest[0] <= SEAT_REACH_METERS:
            found[chair.id] = nearest[1]
    return found


def carried(table: SceneNode, move: NodeMove, chairs) -> list[NodeMove]:
    """The chairs' moves when their table slides and turns, so each keeps its place at it."""
    pivot = table.transform.position
    radians = math.radians(move.delta_rotation_z_degrees)
    cos_d, sin_d = math.cos(radians), math.sin(radians)
    moves = []
    for chair in chairs:
        at = chair.transform.position
        off_x, off_y = at.x - pivot.x, at.y - pivot.y
        x = pivot.x + move.delta_translation.x + off_x * cos_d - off_y * sin_d
        y = pivot.y + move.delta_translation.y + off_x * sin_d + off_y * cos_d
        moves.append(NodeMove(
            node_id=chair.id,
            delta_translation=Vec3(x=x - at.x, y=y - at.y, z=0.0),
            delta_rotation_z_degrees=move.delta_rotation_z_degrees,
        ))
    return moves


def seating_groups(graph: SceneGraph) -> list[dict[str, object]]:
    """Each table and the chairs currently pulled up to it, for model evidence."""
    chairs_at: dict[UUID, list[str]] = {}
    for chair_id, table_id in tables_by_chair(graph).items():
        chairs_at.setdefault(table_id, []).append(str(chair_id))
    return [
        {"table_id": str(table_id), "chair_ids": chair_ids}
        for table_id, chair_ids in chairs_at.items()
    ]


def expand_moves_with_seating(graph: SceneGraph, moves: list[NodeMove]) -> list[NodeMove]:
    """Carry unnamed chairs when their table moves so a redesign cannot strand them.

    An explicit chair move still wins for that chair, which lets a proposal
    reseat it; `lost_seating` then rejects the result if the chair ends up
    away from every table.
    """
    seated = tables_by_chair(graph)
    named = {move.node_id for move in moves}
    carried_moves: list[NodeMove] = []
    for move in moves:
        chairs = [
            graph.by_id(chair_id)
            for chair_id, table_id in seated.items()
            if table_id == move.node_id and chair_id not in named
        ]
        if chairs:
            carried_moves.extend(carried(graph.by_id(move.node_id), move, chairs))
    return [*moves, *carried_moves]


def lost_seating(before: SceneGraph, after: SceneGraph) -> bool:
    """True when a chair that was at a table is no longer at any table."""
    now = tables_by_chair(after)
    return any(chair_id not in now for chair_id in tables_by_chair(before))


def _reach(node: SceneNode) -> float:
    return math.hypot(node.dimensions.x, node.dimensions.y) / 2


def _clearance(a: SceneNode, b: SceneNode) -> float:
    """A lower bound on the gap, from the circles around each footprint."""
    apart = math.dist(
        (a.transform.position.x, a.transform.position.y),
        (b.transform.position.x, b.transform.position.y),
    )
    return apart - _reach(a) - _reach(b)


def _tightness(piece: SceneNode, other: SceneNode) -> float:
    if _clearance(piece, other) >= BREATHING_ROOM_METERS:
        return 0.0
    gap = gap_between(footprint(piece), footprint(other))
    if other.kind == "wall" and gap <= FLUSH_METERS:
        return 0.0
    return max(BREATHING_ROOM_METERS - gap, 0.0)


def _outside_by(node: SceneNode, bounds: tuple[float, float, float, float]) -> float:
    low_x, low_y, high_x, high_y = bounds
    return max(
        max(low_x - x, x - high_x, low_y - y, y - high_y, 0.0)
        for x, y in footprint(node)
    )


@dataclass(frozen=True)
class Composition:
    """One room's walls and seating, worked out once for every guess ranked against it."""

    graph: SceneGraph
    axis_degrees: float
    table_of: dict[UUID, UUID]
    interior: tuple[float, float, float, float] | None

    @classmethod
    def of(cls, graph: SceneGraph) -> Composition:
        return cls(graph, room_axis_degrees(graph), tables_by_chair(graph), interior_bounds(graph))

    def cost(self, moves: list[NodeMove]) -> float:
        """Effort, plus how much worse the moved pieces look than before they moved."""
        if not moves:
            return 0.0
        moved = {move.node_id for move in moves}
        after = apply_moves(self.graph, moves)
        return self.effort(moves) + self._looks(after, moved) - self._looks(self.graph, moved)

    def leaves_overlaps(self, moves: list[NodeMove]) -> bool:
        """Whether a moved piece ends up inside another piece, even one it was already inside.

        The hard constraints let a move keep an overlap the scan recorded, so a
        chair captured half inside a table can still be nudged. A layout we
        propose is ours to answer for, though, and furniture stacked into
        furniture is not a room anyone can set up.
        """
        moved = {move.node_id for move in moves}
        pieces = [node for node in apply_moves(self.graph, moves).nodes if blocks_floor(node)]
        return any(
            overlapping(piece, other)
            for piece in pieces
            if piece.id in moved
            for other in pieces
            if other.id != piece.id
        )

    def ranked(self, guesses: list) -> list:
        """Candidates in order of cost. Sorting is stable, so equal costs keep their order."""
        return sorted(guesses, key=lambda guess: self.cost(guess.moves))

    def effort(self, moves: list[NodeMove]) -> float:
        total = 0.0
        for move in moves:
            node = self.graph.by_id(move.node_id)
            weight = max(node.dimensions.x * node.dimensions.y, LIGHTEST_WEIGHT_M2)
            slide = math.hypot(move.delta_translation.x, move.delta_translation.y)
            turn = abs(move.delta_rotation_z_degrees) / 90.0 * TURN_DISRUPTION_METERS
            total += weight * (slide + turn)
        return total

    def _looks(self, layout: SceneGraph, moved: set[UUID]) -> float:
        pieces = [node for node in layout.nodes if node.id in moved]
        seated = self.table_of if layout is self.graph else tables_by_chair(layout)
        return (
            CRAMMED_WEIGHT * self._crammed(layout, pieces, seated)
            + STRANDED_CHAIR_METERS * self._stranded(seated, moved)
            + CROOKED_METERS_PER_45_DEGREES * self._crooked(pieces)
            + OUTSIDE_WEIGHT * self._outside(pieces)
        )

    def _crammed(self, layout: SceneGraph, pieces: list[SceneNode], seated: dict[UUID, UUID]) -> float:
        """Breathing room lost around the moved pieces.

        A chair at its own table and chairs standing side by side are how
        seating is meant to look, so neither counts.
        """
        others = [node for node in layout.nodes if node.kind == "wall" or blocks_floor(node)]
        seen: set[UUID] = set()
        total = 0.0
        for piece in pieces:
            seen.add(piece.id)
            total += sum(
                _tightness(piece, other)
                for other in others
                if other.id not in seen and not self._belong_together(piece, other, seated)
            )
        return total

    @staticmethod
    def _belong_together(a: SceneNode, b: SceneNode, seated: dict[UUID, UUID]) -> bool:
        if roles.is_seating(a) and roles.is_seating(b):
            return True
        return seated.get(a.id, a.id) == seated.get(b.id, b.id)

    def _stranded(self, seated: dict[UUID, UUID], moved: set[UUID]) -> int:
        """Chairs that were at a table before the move and are at none after it."""
        return sum(
            chair not in seated
            for chair, table in self.table_of.items()
            if chair in moved or table in moved
        )

    def _crooked(self, pieces: list[SceneNode]) -> float:
        return sum(abs(squaring_turn(piece, self.axis_degrees)) / 45.0 for piece in pieces)

    def _outside(self, pieces: list[SceneNode]) -> float:
        if self.interior is None:
            return 0.0
        return sum(_outside_by(piece, self.interior) for piece in pieces)
