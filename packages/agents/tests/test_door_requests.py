"""A shop with more than one door gets one clearly named request per door, never two identical cards."""

from standardphysics_agents.checks import roles
from standardphysics_agents.checks.observation import Observation
from standardphysics_agents.findings import resolve
from standardphysics_agents.rules import load_pack
from standardphysics_fixtures import build_graph
from standardphysics_fixtures.shop import node_id


def _with_second_door(graph, label="Door"):
    second = graph.by_id(node_id("door_front")).model_copy(
        deep=True, update={"id": node_id("door_second"), "label": label})
    second.transform.m[3], second.transform.m[7] = 2.4, 4.0
    return graph.model_copy(update={"nodes": [*graph.nodes, second]})


def _unlabelled(graph):
    return graph.model_copy(update={"nodes": [
        node.model_copy(update={"label": "Door"}) if node.kind == "door" else node for node in graph.nodes]})


def _width_request(graph, door):
    observation = Observation(rule_id="door_clear_width", satisfied=False, required_inches=32.0,
                              relied_on=(door.id,), facts={"door": door.label, "door_name": roles.door_name(graph, door)},
                              asks_for="measurement")
    return resolve(observation, load_pack().by_id("door_clear_width"), graph)[1]


def test_the_only_door_is_the_front_door():
    graph = build_graph()
    door = roles.doors(graph)[0]
    assert _width_request(graph, door).title == "Measure how wide the front door opens and send us the number"


def test_two_unlabelled_doors_get_two_different_requests_and_neither_is_called_the_front_door():
    graph = _unlabelled(_with_second_door(build_graph()))
    titles = [_width_request(graph, door).title for door in roles.doors(graph)]
    assert len(set(titles)) == 2
    assert not any("front" in title for title in titles)


def test_a_named_door_is_asked_about_by_its_name():
    graph = _with_second_door(build_graph(), label="Restroom door")
    restroom = graph.by_id(node_id("door_second"))
    assert _width_request(graph, restroom).title == "Measure how wide the restroom door opens and send us the number"


def _request_for(rule_id, facts):
    from standardphysics_agents.copy import request

    return request(load_pack().by_id(rule_id), facts)


def test_a_counter_height_request_asks_for_a_height_and_names_the_counter():
    text = _request_for("service_counter_height", {"counter": "Ordering counter"})
    assert text.title == "Measure how high the ordering counter is where customers pay"
    assert "narrowest" not in text.detail and "36 inches" in text.detail


def test_every_measured_rule_that_can_ask_for_a_number_has_its_own_request():
    from standardphysics_agents.copy import GENERIC_REQUEST

    for rule_id in ("service_counter_height", "point_of_sale_height", "dining_surface_height", "door_clear_width"):
        assert _request_for(rule_id, {}).title != GENERIC_REQUEST.title, rule_id
