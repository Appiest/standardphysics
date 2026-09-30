"""`is_allowed` answers exactly what `violations` would, and stops at the first broken constraint to get there."""

import json
import pathlib
import uuid

import pytest
from standardphysics_agents.fix import constraints
from standardphysics_agents.fix.constraints import is_allowed, violations
from standardphysics_agents.fix.moves import apply_moves
from standardphysics_contracts import Mat4, NodeMove, SceneGraph, Vec3
from standardphysics_pipeline.ingest import parse_room_json
from standardphysics_pipeline.occupancy import UNCLAIMED_SURFACE

SCAN = pathlib.Path(__file__).resolve().parents[3] / "datasets/phone/test1/room.json"


@pytest.fixture(scope="module")
def scanned() -> SceneGraph:
    return parse_room_json(json.loads(SCAN.read_text()))


def _slides(graph: SceneGraph) -> list[SceneGraph]:
    movable = [node for node in graph.nodes if node.movable]
    steps = [-1.5, -0.5, 0.25, 1.0, 2.5]
    return [
        apply_moves(graph, [NodeMove(node_id=node.id, delta_translation=Vec3(x=dx, y=dy, z=0.0))])
        for node in movable for dx in steps for dy in steps
    ]


def test_it_agrees_with_violations_on_every_slide_of_every_piece_in_a_real_scan(scanned):
    candidates = _slides(scanned)
    answers = [(is_allowed(scanned, candidate), not violations(scanned, candidate)) for candidate in candidates]
    assert all(fast == full for fast, full in answers)
    assert {full for _, full in answers} == {True, False}


def test_a_move_already_too_far_never_reaches_the_costly_checks(scanned, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("checked after an earlier constraint had already broken")

    monkeypatch.setattr(constraints, "_lost_room_to_use", never)
    monkeypatch.setattr(constraints, "_collisions", never)
    piece = next(node for node in scanned.nodes if node.movable)
    far = apply_moves(scanned, [NodeMove(node_id=piece.id, delta_translation=Vec3(x=40.0, y=0.0, z=0.0))])
    assert not is_allowed(scanned, far)


def _box_like(node, **changes):
    return node.model_copy(update={"id": uuid.uuid4(), **changes})


def _moved(graph: SceneGraph, piece, dx: float, dy: float) -> SceneGraph:
    return apply_moves(graph, [NodeMove(node_id=piece.id, delta_translation=Vec3(x=dx, y=dy, z=0.0))])


def _free_slide(graph: SceneGraph):
    """A piece and a straight 0.4 m slide the real scan allows on its own."""
    steps = [(0.4, 0.0), (-0.4, 0.0), (0.0, 0.4), (0.0, -0.4)]
    return next((piece, dx, dy) for piece in graph.nodes if piece.movable for dx, dy in steps
                if is_allowed(graph, _moved(graph, piece, dx, dy)))


def test_an_upright_sheet_of_unclaimed_lidar_faces_is_no_wall_to_collide_with(scanned):
    piece, dx, dy = _free_slide(scanned)
    at, reach = piece.transform.position, max(piece.dimensions.x, piece.dimensions.y) / 2 + 0.1
    across = Vec3(x=0.02, y=1.5, z=2.0) if dx else Vec3(x=1.5, y=0.02, z=2.0)
    lidar = _box_like(piece, raw_category=UNCLAIMED_SURFACE, kind="surface_candidate", movable=False,
                      transform=Mat4.translation(at.x + (reach if dx > 0 else -reach if dx else 0.0),
                                                 at.y + (reach if dy > 0 else -reach if dy else 0.0), 1.0),
                      dimensions=across)
    room = scanned.model_copy(update={"nodes": [*scanned.nodes, lidar]})
    assert is_allowed(room, room)
    assert is_allowed(room, _moved(room, piece, dx, dy))


def test_a_piece_put_back_where_the_scan_found_it_may_overlap_what_it_overlapped_there(scanned):
    piece = next(node for node in scanned.nodes if node.movable)
    duplicate = _box_like(piece, label=f"{piece.label} seen twice", movable=False)
    room = scanned.model_copy(update={"nodes": [*scanned.nodes, duplicate]})
    dragged = _moved(room, piece, 1.0, 0.0)
    put_back = _moved(dragged, piece, -1.0, 0.0)
    assert is_allowed(dragged, put_back) and not violations(dragged, put_back)
