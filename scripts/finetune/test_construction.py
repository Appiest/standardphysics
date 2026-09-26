import functools
import math

import pytest
from shop_generator import generate
from standardphysics_agents.training.construction import (
    FixtureMove,
    WallShift,
    construction_inches,
    fixture_ids,
    floor_edges,
    move_fixtures,
    shift_walls,
)
from standardphysics_agents.training.edits import edit_complaint, edits_json, parse_edits
from standardphysics_agents.training.prices import wall_shift_price
from standardphysics_agents.training.prompt import _wall, room_view
from standardphysics_agents.training.reward import shaped_reward
from standardphysics_contracts import Mat4, SceneNode, Vec3, to_meters

SHIFT = WallShift(side="x+", inches=12)


@functools.cache
def shop():
    return generate(0)[0]


def _offset(node, edge) -> float:
    position = node.transform.position
    return (position.x - edge.centre[0]) * edge.outward[0] + (position.y - edge.centre[1]) * edge.outward[1]


def test_shift_grows_the_floor_on_one_side_only():
    graph = shop()
    edge = {edge.side: edge for edge in floor_edges(graph)}["x+"]
    shifted = shift_walls(graph, [SHIFT])
    before, after = graph.ground(), shifted.ground()
    grown = [b - a for a, b in zip(before.dimensions.as_tuple(), after.dimensions.as_tuple())]
    assert sorted(round(value, 4) for value in grown) == [0.0, 0.0, round(to_meters(12), 4)]
    assert _offset(after, edge) == pytest.approx(to_meters(12) / 2)


def test_walls_and_doors_on_the_pushed_edge_move_out_with_it():
    graph = shop()
    edges = {edge.side: edge for edge in floor_edges(graph)}
    edge = max(edges.values(), key=lambda e: sum(abs(_offset(n, e) - e.half_extent) < 0.35
                                                   for n in graph.nodes if n.kind == "wall"))
    shifted = {node.id: node for node in shift_walls(graph, [WallShift(side=edge.side, inches=12)]).nodes}
    carried = [node for node in graph.nodes if node.kind in {"wall", "door"}
               and abs(_offset(node, edge) - edge.half_extent) < 0.35
               and shifted[node.id].dimensions == node.dimensions]
    assert carried
    for node in carried:
        assert _offset(shifted[node.id], edge) - _offset(node, edge) == pytest.approx(to_meters(12))
    untouched = [node for node in graph.nodes if node.kind == "object"]
    assert all(shifted[node.id].transform == node.transform for node in untouched)


def test_answers_carry_wall_shifts_and_refuse_oversized_ones():
    edits = parse_edits('{"moves":[],"wall_shifts":[{"side":"y-","inches":3}]}')
    assert edits is not None and edits.wall_shifts[0].inches == 3
    assert parse_edits('{"moves":[],"wall_shifts":[{"side":"y-","inches":13}]}') is None
    assert parse_edits('{"moves":[],"wall_shifts":[{"side":"north","inches":3}]}') is None
    plain = parse_edits('{"moves":[]}')
    assert plain is not None and "wall_shifts" not in edits_json(plain)


def test_prompt_draws_a_wall_along_its_long_extent():
    wall = SceneNode(id="00000000-0000-0000-0000-000000000001", kind="wall", label="Wall", raw_category="wall",
                     dimensions=Vec3(x=0.1, y=8.0, z=3.0), transform=Mat4.identity())
    x1, y1, x2, y2 = _wall(wall)
    assert math.hypot(x2 - x1, y2 - y1) == pytest.approx(8.0)


def test_prompt_lists_the_sides_a_model_may_push():
    graph = shop()
    view = room_view(graph, generate(0)[1], [])
    assert sorted(side["side"] for side in view["walls_you_can_move"]) == ["x+", "x-", "y+", "y-"]


def test_construction_pays_less_than_the_same_fix_without_it():
    six_inches = wall_shift_price(6)
    assert shaped_reward(1.0, True, 0.5, 1.0, construction_cost=six_inches) < shaped_reward(1.0, True, 0.5, 1.0)
    assert shaped_reward(1.0, True, 0.5, 1.0, construction_cost=wall_shift_price(12)) > shaped_reward(0.9, False, 0.0, 1.0)


def test_fitting_edits_round_trip_and_refuse_refitting_one_piece_twice():
    table = "00000000-0000-0000-0000-000000000003"
    answer = ('{"height_changes":[{"node_id":"%s","top_inches":30}],'
              '"replacements":[{"node_id":"%s","catalog_item":"accessible_two_top"}]}' % (table, table))
    edits = parse_edits(answer)
    assert edits is not None and parse_edits(edits_json(edits)) == edits
    assert edit_complaint(shop(), edits) == "duplicate_refits"
    assert "add_lowered_section" not in edits_json(edits)


def test_fixture_moves_slide_built_ins_and_refuse_furniture():
    graph = shop()
    fixtures = fixture_ids(graph)
    fixture = next(node for node in graph.nodes if node.id in fixtures and node.kind == "object")
    moved = move_fixtures(graph, [FixtureMove(node_id=fixture.id, dx_inches=12, dy_inches=0)])
    before, after = fixture.transform.position, moved.by_id(fixture.id).transform.position
    assert (after.x - before.x, after.y - before.y) == pytest.approx((to_meters(12), 0.0))
    furniture = next(node for node in graph.nodes if node.movable)
    with pytest.raises(ValueError):
        move_fixtures(graph, [FixtureMove(node_id=furniture.id, dx_inches=3, dy_inches=0)])


def test_exterior_walls_are_not_fixtures():
    graph = shop()
    edges = floor_edges(graph)
    exterior = [node for node in graph.nodes if node.kind == "wall"
                and any(abs(_offset(node, edge) - edge.half_extent) < 0.35 for edge in edges)]
    assert exterior and not {node.id for node in exterior} & fixture_ids(graph)


def test_construction_inches_count_fixture_slides():
    move = FixtureMove(node_id="00000000-0000-0000-0000-000000000002", dx_inches=3, dy_inches=4)
    assert construction_inches([WallShift(side="x-", inches=2)], [move]) == 7.0
