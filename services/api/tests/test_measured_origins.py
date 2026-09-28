"""Recovering where the scan found a piece, for revisions saved before that was recorded."""

from __future__ import annotations

import math
import uuid

import pytest
from standardphysics_contracts import Mat4, SceneGraph, SceneNode, Vec3

from standardphysics_api.measured_origins import RevisionPoses, recover_origins, with_origins

WALL, FLOOR, CASE, CHAIR = "wall", "floor", "case", "chair"
FIXED = {WALL, FLOOR}


def _m(x: float, y: float, yaw_degrees: float = 0.0) -> list[float]:
    cos_t, sin_t = math.cos(math.radians(yaw_degrees)), math.sin(math.radians(yaw_degrees))
    return [cos_t, -sin_t, 0.0, x, sin_t, cos_t, 0.0, y, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _node(name: str, m: list[float], stamped: tuple[float, float] | None = None) -> dict:
    node = {"id": name, "transform": {"m": m}, "movable": name not in FIXED}
    if stamped:
        node["measured_position"] = {"x": stamped[0], "y": stamped[1], "z": 0.0}
    return node


def _revision(number: int, saved_at: str, **placed) -> RevisionPoses:
    nodes = [_node(name, value if isinstance(value, list) else value[0], None if isinstance(value, list) else value[1])
             for name, value in placed.items()]
    return RevisionPoses.of(number, saved_at, {"nodes": nodes})


def _placed(x: float, y: float, yaw_degrees: float, tx: float, ty: float) -> list[float]:
    """Where a room placement of `yaw` about the origin, then (tx, ty), carries a piece at (x, y)."""
    cos_t, sin_t = math.cos(math.radians(yaw_degrees)), math.sin(math.radians(yaw_degrees))
    return _m(cos_t * x - sin_t * y + tx, sin_t * x + cos_t * y + ty, yaw_degrees)


SCANNED = _revision(0, "2026-09-01T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=_m(1, 0), chair=_m(2, 2))


def _origin(origins, name):
    found = origins[name]
    return None if found is None else (pytest.approx(found.x, abs=1e-9), pytest.approx(found.y, abs=1e-9))


def test_a_drag_keeps_where_the_piece_was_found():
    dragged = _revision(1, "2026-09-02T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=_m(1.8, 0), chair=_m(2, 2))
    assert _origin(recover_origins([SCANNED, dragged]), CASE) == (1, 0)


def test_a_room_placement_carries_where_the_piece_was_found():
    placed = _revision(
        1, "2026-09-02T10:00:00+00:00",
        wall=_placed(0, 3, 90, 5, 0), floor=_placed(0, 0, 90, 5, 0),
        case=_placed(1, 0, 90, 5, 0), chair=_placed(2, 2, 90, 5, 0),
    )
    dragged = _revision(
        2, "2026-09-03T10:00:00+00:00",
        wall=_placed(0, 3, 90, 5, 0), floor=_placed(0, 0, 90, 5, 0),
        case=_m(5, 2, 90), chair=_placed(2, 2, 90, 5, 0),
    )
    assert _origin(recover_origins([SCANNED, placed, dragged]), CASE) == (5, 1)


def test_a_piece_moved_unlike_any_room_in_a_placement_is_not_guessed():
    placed = _revision(
        1, "2026-09-02T10:00:00+00:00",
        wall=_placed(0, 3, 90, 5, 0), floor=_placed(0, 0, 90, 5, 0),
        case=_m(7, 7), chair=_placed(2, 2, 90, 5, 0),
    )
    origins = recover_origins([SCANNED, placed])
    assert origins[CASE] is None
    assert _origin(origins, CHAIR) == (3, 2)


def test_a_rewritten_first_revision_leaves_what_moved_since_unrecovered():
    rescanned = _revision(0, "2026-09-05T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=_m(1, 0), chair=_m(2, 2))
    dragged = _revision(1, "2026-09-02T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=_m(1.8, 0), chair=_m(2, 2))
    origins = recover_origins([rescanned, dragged])
    assert origins[CASE] is None
    assert _origin(origins, CHAIR) == (2, 2)


def test_a_recorded_origin_wins_over_the_walk():
    stamped = _revision(
        1, "2026-09-02T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=(_m(1.8, 0), (0.5, 0.5)), chair=_m(2, 2),
    )
    assert _origin(recover_origins([SCANNED, stamped]), CASE) == (0.5, 0.5)


def test_a_piece_first_seen_later_was_found_where_it_first_appeared():
    added = _revision(
        1, "2026-09-02T10:00:00+00:00", wall=_m(0, 3), floor=_m(0, 0), case=_m(1, 0), chair=_m(2, 2), lamp=_m(-1, -1),
    )
    assert _origin(recover_origins([SCANNED, added]), "lamp") == (-1, -1)


def test_two_nodes_sharing_an_id_are_not_given_an_origin():
    shared = RevisionPoses.of(0, "2026-09-01T10:00:00", {"nodes": [_node(CASE, _m(1, 0)), _node(CASE, _m(3, 0))]})
    assert recover_origins([shared])[CASE] is None


def _scene_node(name: str, m: list[float]) -> SceneNode:
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_URL, name), kind="object", label=name, raw_category="object",
        dimensions=Vec3(x=0.5, y=0.5, z=0.5), transform=Mat4(m=m), movable=True,
    )


def test_only_pieces_away_from_their_origin_are_given_a_record():
    moved, still, lost = _scene_node("moved", _m(2, 0)), _scene_node("still", _m(1, 1)), _scene_node("lost", _m(0, 0))
    graph = SceneGraph(scan_id=uuid.uuid4(), nodes=[moved, still, lost])
    origins = {str(moved.id): Vec3(x=1, y=0, z=0), str(still.id): Vec3(x=1, y=1, z=0), str(lost.id): None}
    recorded, unrecovered = with_origins(graph, origins)
    assert recorded.by_id(moved.id).measured_position == Vec3(x=1, y=0, z=0)
    assert recorded.by_id(still.id).measured_position is None
    assert recorded.by_id(lost.id).measured_position is None
    assert unrecovered == 1
