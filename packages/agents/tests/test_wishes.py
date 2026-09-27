"""Owner wishes: inferred from the layout or stated, and checked against a proposed room."""

import json
from pathlib import Path

import pytest
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.wishes import broken, infer_wishes, kept, kept_share, stays_near, stays_put
from standardphysics_contracts import NodeMove, SceneGraph, Vec3
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
