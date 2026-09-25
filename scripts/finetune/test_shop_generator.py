from shop_generator import SHOP_TYPES, Rect, generate, outline
from standardphysics_agents.checks import roles
from standardphysics_agents.fix.constraints import violations
from standardphysics_contracts import SceneGraph


def _furniture_free(graph: SceneGraph) -> SceneGraph:
    return graph.model_copy(update={"nodes": [n for n in graph.nodes if n.kind != "object" or not n.movable]})


def test_every_shop_type_builds_a_room_the_checker_can_read():
    for index in range(len(SHOP_TYPES)):
        graph, scenario, shop = generate(index)
        assert roles.service_counters(graph), shop.name
        assert roles.entrance(graph) is not None, shop.name
        anchors = {stop.anchor_node_id for stop in scenario.stops}
        assert all(anchor in {node.id for node in graph.nodes} for anchor in anchors), shop.name


def test_generated_furniture_passes_the_hard_constraints():
    for index in range(len(SHOP_TYPES)):
        graph, _, shop = generate(index)
        movable = frozenset(n.id for n in graph.nodes if n.kind == "object" and n.movable)
        assert not violations(_furniture_free(graph), graph, added=movable), shop.name


def test_rooms_are_reproducible_and_differ_from_each_other():
    first, _, _ = generate(3)
    again, _, _ = generate(3)
    other, _, _ = generate(3 + len(SHOP_TYPES))
    assert first == again
    assert [n.transform for n in first.nodes] != [n.transform for n in other.nodes]


def test_outline_drops_the_seam_between_joined_rectangles():
    walls = outline([Rect(0, 0, 4, 4), Rect(4, 2, 6, 4)])
    assert round(sum(w.length for w in walls), 6) == 20
    assert not any(w.a[0] == w.b[0] == 4 and min(w.a[1], w.b[1]) >= 2 for w in walls)
