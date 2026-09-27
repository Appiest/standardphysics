import functools
import math

import pytest
from multiroom_data import checker_for
from shop_generator import SHOP_TYPES, SPACE_TYPOLOGIES, generate, space_typology_for
from shop_geometry import Rect, outline
from shop_restroom import turning_circle_fits
from shop_shells import available_scans
from standardphysics_agents.checks import roles
from standardphysics_agents.fix.constraints import violations
from standardphysics_agents.training.quality import front_heading_degrees, wall_segments
from standardphysics_contracts import SceneGraph, SceneNode
from standardphysics_contracts.precedents import SpaceTypology
from synthetic_data import make_room

COUNTER_LABELS = roles.SERVICE_COUNTER_LABELS | {"front desk", "reception desk"}
"""The integration branch adds the two desks to the checker's roles."""
SURFACES = {"Table", "Dining table", "Cafe table", "Bar table", "Accessible table", "Desk", "Coffee table"}
DINING = {"Table", "Dining table", "Cafe table", "Bar table", "Accessible table"}
"""The checker's dining surfaces plus the accessible table the integration branch adds."""
WALL_BACKED = {"Shelving unit", "Bookcase", "Drinks fridge", "Bread rack", "Styling station", "Shampoo sink",
               "File cabinet", "Printer", "Condiment station", "Trash and recycling bins", "ATM", "Fitting room",
               "Drink station", "Tray return", "Toilet", "Self-service kiosk"}
ALWAYS_A_RESTROOM = {"cafe", "restaurant", "fast food restaurant", "small office", "clinic waiting room",
                     "hotel lobby"}
ROOMS = range(2 * len(SHOP_TYPES))


@functools.cache
def room(index: int):
    return generate(index)


def _furniture_free(graph: SceneGraph) -> SceneGraph:
    return graph.model_copy(update={"nodes": [n for n in graph.nodes if n.kind != "object" or not n.movable]})


def _yaw(node: SceneNode) -> float:
    return math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))


def _local(node: SceneNode, point) -> tuple[float, float]:
    angle = math.radians(_yaw(node))
    dx, dy = point[0] - node.transform.m[3], point[1] - node.transform.m[7]
    return dx * math.cos(angle) + dy * math.sin(angle), -dx * math.sin(angle) + dy * math.cos(angle)


def _world_direction(node: SceneNode, local_degrees: float) -> float:
    return _yaw(node) + local_degrees


def _off(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _seat_point(seat: SceneNode, surface: SceneNode):
    """The point of the seat's long midline nearest the surface, so a long bench is judged where it meets it."""
    lx, _ = _local(seat, (surface.transform.m[3], surface.transform.m[7]))
    along = max(-seat.dimensions.x / 2, min(seat.dimensions.x / 2, lx))
    angle = math.radians(_yaw(seat))
    return seat.transform.m[3] + along * math.cos(angle), seat.transform.m[7] + along * math.sin(angle)


def _edge_distance(surface: SceneNode, point) -> float:
    lx, ly = _local(surface, point)
    return math.hypot(max(abs(lx) - surface.dimensions.x / 2, 0), max(abs(ly) - surface.dimensions.y / 2, 0))


def _into_nearest_side(surface: SceneNode, point) -> float:
    lx, ly = _local(surface, point)
    past_x, past_y = abs(lx) - surface.dimensions.x / 2, abs(ly) - surface.dimensions.y / 2
    if past_x > past_y:
        return _world_direction(surface, 180.0 if lx > 0 else 0.0)
    return _world_direction(surface, -90.0 if ly > 0 else 90.0)


def _misfacing_seats(graph: SceneGraph) -> list[str]:
    surfaces = [n for n in graph.nodes if n.kind == "object" and n.label in SURFACES]
    wrong = []
    for seat in (n for n in graph.nodes if n.kind == "object" and roles.is_seating(n)):
        near = [(_edge_distance(s, _seat_point(seat, s)), s) for s in surfaces]
        near = [(d, s) for d, s in near if d <= 1.0]
        if not near:
            continue
        _, surface = min(near, key=lambda pair: pair[0])
        wanted = _into_nearest_side(surface, _seat_point(seat, surface))
        if _off(front_heading_degrees(seat), wanted) > 30.0:
            wrong.append(f"{seat.label} at {surface.label}")
    return wrong


def _to_segment(point, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = point, a, b
    length = (bx - ax) ** 2 + (by - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length)) if length else 0.0
    return math.dist(point, (ax + t * (bx - ax), ay + t * (by - ay)))


def _backs_onto(node: SceneNode, segment) -> tuple[float, float] | None:
    """For a wall running along the piece's width: how far the piece's back is from it, and the way away from it."""
    (ax, ay), (bx, by) = segment
    along = math.degrees(math.atan2(by - ay, bx - ax))
    if _off(along % 180.0, _yaw(node) % 180.0) > 20.0 and _off(along % 180.0 + 180.0, _yaw(node) % 180.0) > 20.0:
        return None
    centre = (node.transform.m[3], node.transform.m[7])
    side = (bx - ax) * (centre[1] - ay) - (by - ay) * (centre[0] - ax)
    away = along + (90.0 if side > 0 else -90.0)
    return _to_segment(centre, *segment) - node.dimensions.y / 2, away


def _wall_backed_facing_in(graph: SceneGraph) -> list[str]:
    walls = wall_segments(graph)
    wrong = []
    for node in (n for n in graph.nodes if n.kind == "object" and n.label in WALL_BACKED):
        backed = [found for found in (_backs_onto(node, wall) for wall in walls) if found and found[0] <= 0.4]
        if not backed:
            continue
        _, away = min(backed, key=lambda found: found[0])
        if _off(front_heading_degrees(node), away) > 30.0:
            wrong.append(node.label)
    return wrong


def test_every_shop_type_builds_a_room_the_checker_can_read():
    for index in range(len(SHOP_TYPES)):
        graph, scenario, shop = room(index)
        counters = [n for n in graph.nodes if n.label.strip().casefold() in COUNTER_LABELS]
        assert counters, shop.name
        assert roles.entrance(graph) is not None, shop.name
        anchors = {stop.anchor_node_id for stop in scenario.stops}
        assert all(anchor in {node.id for node in graph.nodes} for anchor in anchors), shop.name


def test_generated_furniture_passes_the_hard_constraints():
    for index in ROOMS:
        graph, _, shop = room(index)
        movable = frozenset(n.id for n in graph.nodes if n.kind == "object" and n.movable)
        assert not violations(_furniture_free(graph), graph, added=movable), f"#{index} {shop.name}"


def test_seats_face_the_surface_they_sit_at():
    for index in ROOMS:
        graph, _, shop = room(index)
        assert not _misfacing_seats(graph), f"#{index} {shop.name}: {_misfacing_seats(graph)}"


def test_wall_backed_pieces_face_into_the_room():
    for index in ROOMS:
        graph, _, shop = room(index)
        assert not _wall_backed_facing_in(graph), f"#{index} {shop.name}: {_wall_backed_facing_in(graph)}"


def test_a_drawn_restroom_has_a_toilet_a_door_and_a_stop():
    for index in ROOMS:
        graph, scenario, shop = room(index)
        doors = {n.id for n in graph.nodes if n.kind == "door" and n.label == "Restroom door"}
        stops = {s.anchor_node_id for s in scenario.stops if s.name == "Restroom"}
        toilets = [n for n in graph.nodes if n.label == "Toilet"]
        assert stops == doors, f"#{index} {shop.name}"
        assert len(toilets) == len(doors), f"#{index} {shop.name}"
        if shop.name in ALWAYS_A_RESTROOM:
            assert doors, f"#{index} {shop.name}"
        assert [s.name for s in scenario.stops][-1] == "Exit"


def test_dining_rooms_mix_table_heights():
    """Every dining room has a low table; any with room for three tables also has a high one."""
    for index in ROOMS:
        graph, _, shop = room(index)
        if not shop.dining:
            continue
        heights = [n.transform.m[11] + n.dimensions.z / 2 for n in graph.nodes if n.label in DINING]
        assert any(h <= 0.765 for h in heights), f"#{index} {shop.name}"
        assert len(heights) < 3 or any(h >= 1.0 for h in heights), f"#{index} {shop.name}"


def test_rooms_are_reproducible_and_differ_from_each_other():
    first, _, _ = generate(3)
    again, _, _ = generate(3)
    other, _, _ = generate(3 + len(SHOP_TYPES))
    assert first == again
    assert [n.transform for n in first.nodes] != [n.transform for n in other.nodes]


@pytest.mark.skipif(not available_scans(), reason="no real scans on this machine")
def test_some_rooms_start_from_a_real_scan():
    from_scans = [index for index in ROOMS
                  if any(n.kind == "wall" and min(n.dimensions.x, n.dimensions.y) < 0.01 for n in room(index)[0].nodes)]
    assert len(from_scans) >= len(ROOMS) // 5


def test_outline_drops_the_seam_between_joined_rectangles():
    walls = outline([Rect(0, 0, 4, 4), Rect(4, 2, 6, 4)])
    assert round(sum(w.length for w in walls), 6) == 20
    assert not any(w.a[0] == w.b[0] == 4 and min(w.a[1], w.b[1]) >= 2 for w in walls)


def test_turning_circle_needs_sixty_inches_clear():
    assert turning_circle_fits(1.6, 1.6, [])
    assert not turning_circle_fits(1.5, 2.0, [])
    assert not turning_circle_fits(1.6, 1.6, [(0.7, 0.7, 0.9, 0.9)])


def test_every_generated_shop_kind_has_a_space_type_or_none_on_purpose():
    assert set(SPACE_TYPOLOGIES) <= {shop.name for shop in SHOP_TYPES}
    boba = next(shop for shop in SHOP_TYPES if shop.name == "boba tea shop")
    salon = next(shop for shop in SHOP_TYPES if shop.name == "salon")
    assert space_typology_for(boba.errand) == SpaceTypology.QSR_BEVERAGE
    assert space_typology_for(salon.errand) is None
    assert space_typology_for("an errand no generator writes") is None


def test_a_generated_rooms_checker_carries_its_space_type():
    window = make_room(1)
    assert generate(1)[2].name == "boba tea shop"
    assert checker_for(window).space_typology == SPACE_TYPOLOGIES["boba tea shop"]
