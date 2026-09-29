"""Free-form moves snapped onto legal floor."""

import json
import math
from pathlib import Path

import pytest
from standardphysics_agents.fix import apply_moves, snap_moves, violations
from standardphysics_contracts import NodeMove, SceneGraph, Vec3
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import touching


@pytest.fixture(scope="module")
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"])


def _movable(graph):
    return [node for node in graph.nodes if node.movable]


def _closest_pair(graph):
    """Two movable pieces close enough that one may travel onto the other."""
    pieces = _movable(graph)
    pairs = [(a, b) for a in pieces for b in pieces if a.id != b.id]
    return min(pairs, key=lambda pair: math.dist(
        (pair[0].transform.position.x, pair[0].transform.position.y),
        (pair[1].transform.position.x, pair[1].transform.position.y)))


def _toward(node, other, share=1.0):
    here, there = node.transform.position, other.transform.position
    return NodeMove(node_id=node.id, delta_translation=Vec3(x=(there.x - here.x) * share,
                                                            y=(there.y - here.y) * share, z=0.0))


def test_a_move_into_another_piece_is_nudged_to_legal_floor(room):
    piece, other = _closest_pair(room)
    asked = _toward(piece, other)
    assert violations(room, apply_moves(room, [asked]))
    snapped = snap_moves(room, [asked])
    assert len(snapped.kept) == 1 and not snapped.dropped
    assert not violations(room, apply_moves(room, snapped.kept))
    assert snapped.nudged_meters[piece.id] > 0


def test_a_nudge_never_lands_in_a_patch_it_must_avoid(room):
    chair = next(node for node in _movable(room) if node.label == "Chair")
    stay = NodeMove(node_id=chair.id, delta_translation=Vec3(x=0.0, y=0.0, z=0.0))
    here = chair.transform.position
    avoid = [(here.x - 0.01, here.y - 0.01), (here.x + 0.01, here.y - 0.01),
             (here.x + 0.01, here.y + 0.01), (here.x - 0.01, here.y + 0.01)]
    snapped = snap_moves(room, [stay], avoid=avoid)
    assert len(snapped.kept) == 1 and snapped.nudged_meters[chair.id] > 0
    landed = apply_moves(room, snapped.kept)
    assert not touching(footprint(landed.by_id(chair.id)), avoid) and not violations(room, landed)


def test_a_legal_move_is_kept_exactly_as_asked(room):
    piece = _movable(room)[0]
    asked = NodeMove(node_id=piece.id, delta_translation=Vec3(x=0.0, y=0.0, z=0.0), delta_rotation_z_degrees=5.0)
    if violations(room, apply_moves(room, [asked])):
        pytest.skip("the fixture room has no room to turn this piece")
    snapped = snap_moves(room, [asked])
    assert snapped.kept == [asked] and snapped.nudged_meters[piece.id] == 0.0


def test_a_move_with_nothing_legal_nearby_is_dropped(room):
    piece = _movable(room)[0]
    far = NodeMove(node_id=piece.id, delta_translation=Vec3(x=40.0, y=40.0, z=0.0))
    snapped = snap_moves(room, [far])
    assert snapped.kept == [] and snapped.dropped == [far]


def test_snapped_moves_are_legal_together_and_never_repeat_a_piece(room):
    first, second = _closest_pair(room)
    moves = [_toward(first, second, 0.5), _toward(second, first, 0.5), _toward(first, second, 0.2)]
    snapped = snap_moves(room, moves)
    assert len({move.node_id for move in snapped.kept}) == len(snapped.kept)
    assert moves[2] in snapped.dropped
    assert not violations(room, apply_moves(room, snapped.kept))
