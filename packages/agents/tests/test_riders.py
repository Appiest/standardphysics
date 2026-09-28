"""What sits on a piece goes with it: the card reader rides the counter it is on."""

import math

import pytest
from standardphysics_agents.fix import apply_moves, carried_along
from standardphysics_agents.fix.moves import riders_of, top_of, underside
from standardphysics_contracts import NodeMove, Vec3
from standardphysics_fixtures import build_lawsuit_graph


@pytest.fixture(scope="module")
def shop():
    graph = build_lawsuit_graph()
    reader = next(node for node in graph.nodes if node.label == "Card reader")
    counter = next(node for node in graph.nodes if node.label == "Ordering counter" and reader in riders_of(graph, node))
    return graph, counter, reader


def _move(node_id, dx=0.0, dy=0.0, degrees=0.0) -> NodeMove:
    return NodeMove(node_id=node_id, delta_translation=Vec3(x=dx, y=dy, z=0.0), delta_rotation_z_degrees=degrees)


def test_sliding_the_counter_slides_the_reader_with_it(shop):
    graph, counter, reader = shop
    moved = apply_moves(graph, carried_along(graph, [_move(counter.id, dx=0.4)]))
    after = moved.by_id(reader.id).transform.position
    assert after.x == pytest.approx(reader.transform.position.x + 0.4)
    assert after.y == pytest.approx(reader.transform.position.y)
    assert underside(moved.by_id(reader.id)) == pytest.approx(top_of(moved.by_id(counter.id)))


def test_turning_the_counter_swings_the_reader_about_the_counter_centre(shop):
    graph, counter, reader = shop
    moved = apply_moves(graph, carried_along(graph, [_move(counter.id, degrees=90.0)]))
    centre, before = counter.transform.position, reader.transform.position
    after = moved.by_id(reader.id).transform.position
    assert math.dist((after.x, after.y), (centre.x, centre.y)) == pytest.approx(math.dist((before.x, before.y), (centre.x, centre.y)))
    assert after.x - centre.x == pytest.approx(-(before.y - centre.y))


def test_a_rider_the_owner_moved_keeps_its_own_move(shop):
    graph, counter, reader = shop
    own = _move(reader.id, dx=-0.1)
    moves = carried_along(graph, [_move(counter.id, dx=0.4), own])
    assert [move for move in moves if move.node_id == reader.id] == [own]
