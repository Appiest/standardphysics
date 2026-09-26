"""The standable floor inside the walls, in rooms that are not square to the world."""

import math

from standardphysics_agents.ask.space import FitRequest, _wall_placements
from standardphysics_agents.fix import interior_polygon
from standardphysics_contracts import Mat4, SceneNode, Vec3
from standardphysics_fixtures.shop import build_graph, node_id
from standardphysics_pipeline import contains_point

INNER_CORNERS = {(-2.95, -3.95), (2.95, -3.95), (2.95, 3.95), (-2.95, 3.95)}
TURN_DEGREES = 30.0


def _turn_point(point, degrees):
    cos_t, sin_t = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return point[0] * cos_t - point[1] * sin_t, point[0] * sin_t + point[1] * cos_t


def _turn_node(node: SceneNode, degrees: float) -> SceneNode:
    cos_t, sin_t = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    turn = [cos_t, -sin_t, 0, 0, sin_t, cos_t, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    m = node.transform.m
    product = [sum(turn[row * 4 + k] * m[k * 4 + column] for k in range(4)) for row in range(4) for column in range(4)]
    return node.model_copy(update={"transform": Mat4(m=product)})


def _turned_shop(degrees=TURN_DEGREES):
    graph = build_graph()
    return graph.model_copy(update={"nodes": [_turn_node(node, degrees) for node in graph.nodes]})


def _rounded(corners):
    return {(round(x, 3), round(y, 3)) for x, y in corners}


def test_a_square_room_is_trimmed_to_its_walls_inner_faces():
    assert _rounded(interior_polygon(build_graph())) == INNER_CORNERS


def test_a_turned_room_keeps_its_own_shape_instead_of_a_world_box():
    expected = {_turn_point(corner, TURN_DEGREES) for corner in INNER_CORNERS}
    assert _rounded(interior_polygon(_turned_shop())) == _rounded(expected)


def test_a_partition_inside_the_room_does_not_cut_the_floor():
    graph = build_graph()
    partition = SceneNode(id=node_id("partition"), kind="wall", label="Partition wall", raw_category="wall",
                          dimensions=Vec3(x=0.1, y=3.0, z=2.4), transform=Mat4.translation(-1.0, 0.0, 1.2))
    graph = graph.model_copy(update={"nodes": [*graph.nodes, partition]})
    assert _rounded(interior_polygon(graph)) == INNER_CORNERS


def test_couch_spots_in_a_turned_room_stay_inside_its_walls():
    graph = _turned_shop()
    inside = interior_polygon(graph)
    spots = list(_wall_placements(graph, FitRequest(length_inches=84, depth_inches=36)))
    assert spots and all(contains_point(inside, (centre.x, centre.y)) for centre, _ in spots)


def test_couch_spots_run_along_the_wall_they_stand_against():
    graph = _turned_shop()
    headings = {round(degrees % 180) for _, degrees in _wall_placements(graph, FitRequest(length_inches=84))}
    assert headings == {round(TURN_DEGREES), round(TURN_DEGREES + 90)}


def test_no_floor_means_no_interior():
    graph = build_graph()
    assert interior_polygon(graph.model_copy(update={"nodes": [n for n in graph.nodes if n.kind != "floor"]})) is None
