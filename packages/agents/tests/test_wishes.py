"""Owner wishes: inferred from the layout or stated, and checked against a proposed room."""

import json
import math
from pathlib import Path

import pytest
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training.quality import wall_segments, wall_term
from standardphysics_agents.training.reward import shaped_reward
from standardphysics_agents.training.wishes import broken, infer_wishes, kept, kept_share, stays_near, stays_put
from standardphysics_contracts import NodeMove, Scenario, SceneGraph, Vec3
from standardphysics_pipeline import PipelineMeasurements


@pytest.fixture(scope="module")
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"])


@pytest.fixture(scope="module")
def measure():
    return PipelineMeasurements()


def _slide(graph, node_id, dx, dy=0.0, turn=0.0):
    move = NodeMove(node_id=node_id, delta_translation=Vec3(x=dx, y=dy, z=0.0), delta_rotation_z_degrees=turn)
    return apply_moves(graph, [move])


def _wish(wishes, kind):
    return next(wish for wish in wishes if wish.kind == kind)


def test_the_layout_shows_seats_at_tables_pieces_against_walls_and_the_counters_view(room, measure):
    wishes = infer_wishes(room, measure)
    assert {wish.kind for wish in wishes} == {"with_table", "against_wall", "clear_view"}
    assert all(wish.source == "inferred" and not wish.hard for wish in wishes)
    assert "stays at the Table" in _wish(wishes, "with_table").text


def test_an_untouched_room_keeps_every_wish(room, measure):
    wishes = infer_wishes(room, measure)
    assert broken(wishes, room, room, measure) == []
    assert kept_share(wishes, room, room, measure) == 1.0


def test_a_seat_carried_away_from_its_table_breaks_only_its_own_wish(room, measure):
    wishes = [wish for wish in infer_wishes(room, measure) if wish.kind == "with_table"]
    seat = wishes[0].subjects[0]
    after = _slide(room, seat, 1.0)
    assert broken(wishes, room, after, measure) == [wishes[0]]
    assert kept(wishes[0], room, _slide(room, seat, 0.1), measure)


def test_a_seat_turned_its_back_on_the_table_has_left_it(room, measure):
    wish = _wish(infer_wishes(room, measure), "with_table")
    assert not kept(wish, room, _slide(room, wish.subjects[0], 0.0, turn=180.0), measure)


def test_a_piece_pulled_off_its_wall_breaks_the_wall_wish(room, measure):
    wish = _wish(infer_wishes(room, measure), "against_wall")
    piece = wish.subjects[0]
    pulled = [_slide(room, piece, dx, dy) for dx, dy in ((0.8, 0.0), (-0.8, 0.0), (0.0, 0.8), (0.0, -0.8))]
    assert any(not kept(wish, room, after, measure) for after in pulled)


def test_stated_wishes_are_hard_and_hold_the_owner_to_their_words(room, measure):
    chair = next(node for node in room.nodes if node.movable and node.label == "Chair")
    table = next(node for node in room.nodes if node.label == "Table")
    put = stays_put(chair)
    assert put.hard and put.source == "stated"
    assert kept(put, room, room, measure)
    assert not kept(put, room, _slide(room, chair.id, 0.05), measure)
    distance = ((chair.transform.position.x - table.transform.position.x) ** 2
                + (chair.transform.position.y - table.transform.position.y) ** 2) ** 0.5
    assert kept(stays_near(chair, table, distance + 0.1), room, room, measure)
    assert not kept(stays_near(chair, table, distance - 0.1), room, room, measure)


def test_the_reward_pays_for_keeping_the_owners_layout(room, measure, pack, ledger):
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    checker = TrainingChecker(Scenario.model_validate(data["scenario"]), rules=pack, ledger=ledger, measure=measure,
                              owner_layout=room)
    assert checker.owner_wishes.wishes == infer_wishes(room, measure)
    assert shaped_reward(0.5, False, 0.0, 1.0, wishes=1.0) > shaped_reward(0.5, False, 0.0, 1.0, wishes=0.0)
    assert shaped_reward(1.0, True, 0.0, 1.0, wishes=1.0) == 1.0
    seat = _wish(infer_wishes(room, measure), "with_table").subjects[0]
    assert checker.owner_wishes.kept_share(room, _slide(room, seat, 0.05), measure) == 1.0
    assert checker.owner_wishes.kept_share(room, _slide(room, seat, 1.0), measure) < 1.0
    verdict = score_completion(json.dumps({"moves": [{"node_id": str(seat), "dx": 1.0, "dy": 0.0,
                                                      "rotation_degrees": 0}]}), room, checker)
    assert verdict.wishes_kept is None or verdict.wishes_kept < 1.0


def _perpendicular_distance(point, segment):
    (px, py), ((ax, ay), (bx, by)) = point, segment
    length_squared = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0.0 if length_squared == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length_squared))
    return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))


def _wall_tangent(node, owner):
    """A unit vector along the nearest wall to `node`, so a slide by this vector keeps its wall distance."""
    position = (node.transform.position.x, node.transform.position.y)
    (ax, ay), (bx, by) = min(wall_segments(owner), key=lambda segment: _perpendicular_distance(position, segment))
    length = math.hypot(bx - ax, by - ay) or 1.0
    return (bx - ax) / length, (by - ay) / length


def test_turning_a_moved_piece_diagonal_pays_less_than_the_same_slide_along_its_wall(room, measure):
    """The audit case: a display case slid 0.3 m along its wall keeps its wall relation and is paid in
    full; turned 90 degrees crosswise over that same slide, it stands diagonally in open floor and the
    reward should pay clearly less for it, even though nothing else about the edit changed."""
    piece_id = _wish(infer_wishes(room, measure), "against_wall").subjects[0]
    piece = room.by_id(piece_id)
    tx, ty = _wall_tangent(piece, room)
    slid = _slide(room, piece_id, tx * 0.3, ty * 0.3)
    turned_too = _slide(room, piece_id, tx * 0.3, ty * 0.3, turn=90.0)

    wall_slid = wall_term(room, slid, {piece_id})
    wall_turned = wall_term(room, turned_too, {piece_id})
    assert wall_turned < wall_slid - 0.2

    reward_slid = shaped_reward(1.0, True, 0.0, wall=wall_slid)
    reward_turned = shaped_reward(1.0, True, 0.0, wall=wall_turned)
    assert reward_turned < reward_slid
