"""`is_allowed` answers exactly what `violations` would, and stops at the first broken constraint to get there."""

import json
import pathlib

import pytest
from standardphysics_agents.fix import constraints
from standardphysics_agents.fix.constraints import is_allowed, violations
from standardphysics_agents.fix.moves import apply_moves
from standardphysics_contracts import NodeMove, SceneGraph, Vec3
from standardphysics_pipeline.ingest import parse_room_json

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
