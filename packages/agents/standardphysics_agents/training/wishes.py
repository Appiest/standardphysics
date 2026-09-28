"""What the owner wants kept, read from where things already stand or told to us directly.

A shop's layout is the owner's own work. Chairs sit at a table because people
eat there, shelving stands against a wall because that is where the owner put
it, and the counter looks out over the floor so staff can see customers. A fix
that clears an accessibility problem by breaking one of those is a fix the
owner will undo. So each relation becomes a `Wish` that any proposed room can
be checked against.

Inferred wishes come from the layout itself (`infer_wishes`): a seat stays at
its table, a piece that stands against a wall stays against one, and the view
from the counter stays clear. The relations are the ones `quality.py` already
scores, turned into yes-or-no checks with a stated tolerance.

Stated wishes come from the owner (`stays_put`, `stays_near`). They are hard:
an option that breaks one is never offered. Inferred wishes are soft, because
they are a guess about what the owner meant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from standardphysics_contracts import MeasurementProvider, SceneGraph, SceneNode, to_meters
from standardphysics_pipeline import footprint

from .quality import (
    SEAT_WORDS,
    _angle_between,
    _facing_off,
    _named,
    _to_segment,
    isovist_area,
    seat_table_pairs,
    viewpoint,
    wall_relation,
    wall_segments,
)

WishKind = Literal["with_table", "against_wall", "clear_view", "stays_put", "stays_near"]

TABLE_STRETCH_METERS = to_meters(12.0)
"""A seat that ends up a foot further from (or closer to) its table has left it."""
TABLE_TURN_DEGREES = 45.0
"""A seat turned more than this away from its table no longer faces it."""
WALL_GAP_METERS = to_meters(6.0)
"""A piece whose outline comes within six inches of a wall stands against it."""
WALL_SQUARE_DEGREES = 10.0
"""A piece against a wall lies square to it, within this."""
VIEW_KEPT_SHARE = 0.9
"""The view from the counter is kept while nine tenths of the floor it saw stays visible."""


@dataclass(frozen=True)
class Wish:
    kind: WishKind
    subjects: tuple[UUID, ...]
    text: str
    source: Literal["inferred", "stated"] = "inferred"
    meters: float = 0.0
    """How near `stays_near` keeps its subject to its anchor."""

    @property
    def hard(self) -> bool:
        return self.source == "stated"


def _name(node: SceneNode) -> str:
    return f"{node.label} [{str(node.id)[:4]}]"


def _xy(node: SceneNode) -> tuple[float, float]:
    return node.transform.position.x, node.transform.position.y


def _wall_gap(node: SceneNode, walls) -> float:
    return min((_to_segment(corner, wall) for corner in footprint(node) for wall in walls), default=math.inf)


def _against_a_wall(node: SceneNode, walls) -> bool:
    if not walls or _wall_gap(node, walls) > WALL_GAP_METERS:
        return False
    angle, _ = wall_relation(node, walls)
    return _angle_between(angle, 0.0, 90.0) <= WALL_SQUARE_DEGREES


def _table_wishes(graph: SceneGraph) -> list[Wish]:
    nodes = {node.id: node for node in graph.nodes}
    return [Wish("with_table", (seat, table), f"{_name(nodes[seat])} stays at the {nodes[table].label}")
            for seat, table in seat_table_pairs(graph)]


def _wall_wishes(graph: SceneGraph) -> list[Wish]:
    walls = wall_segments(graph)
    return [Wish("against_wall", (node.id,), f"{_name(node)} stays against the wall")
            for node in graph.nodes
            if node.movable and not _named(node, SEAT_WORDS) and _against_a_wall(node, walls)]


def infer_wishes(graph: SceneGraph, measure: MeasurementProvider) -> list[Wish]:
    """The relations the layout shows the owner chose: seats at tables, pieces against walls, the counter's view."""
    view = [Wish("clear_view", (), "Staff at the counter can still see the shop floor")] if viewpoint(
        graph, measure) else []
    return [*_table_wishes(graph), *_wall_wishes(graph), *view]


def stays_put(node: SceneNode) -> Wish:
    return Wish("stays_put", (node.id,), f"{_name(node)} stays where it is", source="stated")


def stays_near(node: SceneNode, anchor: SceneNode, meters: float) -> Wish:
    inches = round(meters / 0.0254)
    return Wish("stays_near", (node.id, anchor.id), f"{_name(node)} stays within {inches} in of the {anchor.label}",
                source="stated", meters=meters)


@dataclass(frozen=True)
class _Rooms:
    """The layout a wish was read from, and the one being judged."""

    reference: SceneGraph
    after: SceneGraph
    measure: MeasurementProvider

    def node(self, graph: SceneGraph, node_id: UUID) -> SceneNode | None:
        return next((node for node in graph.nodes if node.id == node_id), None)


def _pair(rooms: _Rooms, graph: SceneGraph, seat_id: UUID, table_id: UUID) -> tuple[SceneNode, SceneNode] | None:
    seat, table = rooms.node(graph, seat_id), rooms.node(graph, table_id)
    return None if seat is None or table is None else (seat, table)


def _with_table(wish: Wish, rooms: _Rooms) -> bool:
    seat_id, table_id = wish.subjects
    was = _pair(rooms, rooms.reference, seat_id, table_id)
    now = _pair(rooms, rooms.after, seat_id, table_id)
    if was is None or now is None:
        return True
    stretch = math.dist(_xy(now[0]), _xy(now[1])) - math.dist(_xy(was[0]), _xy(was[1]))
    turn = _angle_between(_facing_off(*was), _facing_off(*now), 360.0)
    return abs(stretch) <= TABLE_STRETCH_METERS and turn <= TABLE_TURN_DEGREES


def _against_wall(wish: Wish, rooms: _Rooms) -> bool:
    node = rooms.node(rooms.after, wish.subjects[0])
    return node is None or _against_a_wall(node, wall_segments(rooms.after))


def _clear_view(wish: Wish, rooms: _Rooms) -> bool:
    eye = viewpoint(rooms.reference, rooms.measure)
    if eye is None:
        return True
    was = isovist_area(rooms.reference, eye)
    return was <= 0 or isovist_area(rooms.after, eye) >= VIEW_KEPT_SHARE * was


def _stays_put(wish: Wish, rooms: _Rooms) -> bool:
    was, now = rooms.node(rooms.reference, wish.subjects[0]), rooms.node(rooms.after, wish.subjects[0])
    return was is None or now is None or was.transform.m == now.transform.m


def _stays_near(wish: Wish, rooms: _Rooms) -> bool:
    node, anchor = rooms.node(rooms.after, wish.subjects[0]), rooms.node(rooms.after, wish.subjects[1])
    return node is None or anchor is None or math.dist(_xy(node), _xy(anchor)) <= wish.meters


KEPT = {"with_table": _with_table, "against_wall": _against_wall, "clear_view": _clear_view,
        "stays_put": _stays_put, "stays_near": _stays_near}


def kept(wish: Wish, reference: SceneGraph, after: SceneGraph, measure: MeasurementProvider) -> bool:
    """Whether `after` still honours a wish read from, or stated about, `reference`."""
    return KEPT[wish.kind](wish, _Rooms(reference, after, measure))


def broken(wishes: list[Wish], reference: SceneGraph, after: SceneGraph, measure: MeasurementProvider) -> list[Wish]:
    return [wish for wish in wishes if not kept(wish, reference, after, measure)]


def kept_share(wishes: list[Wish], reference: SceneGraph, after: SceneGraph, measure: MeasurementProvider) -> float:
    """The share of wishes `after` keeps, 1.0 when there are none."""
    if not wishes:
        return 1.0
    return 1.0 - len(broken(wishes, reference, after, measure)) / len(wishes)
