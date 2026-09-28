"""A counter's lowered section is moved together with the counter it belongs to, never pulled off it."""

import json
import math
import uuid

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import relocation_violations, violations
from standardphysics_agents.fix.built_ins import built_in_set_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training import menu as menu_module
from standardphysics_agents.training.construction import build, fixture_ids
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.menu import FIXTURE_TRIES, _fixture_guesses, build_menu
from standardphysics_contracts import Mat4, SceneGraph
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario


def _crowded_counter() -> SceneGraph:
    """The lawsuit shop with a display case parked in front of the lowered section.

    The 48 in approach space may slide along the counter but must overlap 36 in
    of the lowered section, so a case in front of that section blocks it everywhere.
    """
    graph = build_lawsuit_graph()
    section = next(node for node in graph.nodes if node.label == "Lowered counter section")
    case = next(node for node in graph.nodes if node.label == "Display case")
    parked = case.model_copy(update={
        "id": uuid.uuid5(uuid.NAMESPACE_URL, "test-built-ins/parked-case"),
        "dimensions": case.dimensions.model_copy(update={"x": 0.5, "y": 0.5}),
        "transform": Mat4.translation(section.transform.position.x, 2.87, case.transform.position.z),
    })
    return graph.model_copy(update={"nodes": [*graph.nodes, parked]})


@pytest.fixture(scope="module")
def shop(pipeline, ledger):
    graph, scenario = _crowded_counter(), build_lawsuit_scenario()
    finding = next(f for f in assess(graph, scenario, pipeline, ledger=ledger).problems
                   if f.check_id == "service_counter_approach")
    return graph, scenario, finding


def _labels(graph, candidate):
    return {graph.by_id(move.node_id).label for move in candidate.moves}


def test_the_lowered_section_slides_with_the_counter_it_touches(shop):
    graph, _, finding = shop
    found = built_in_set_moves(graph, finding, fixture_ids(graph))
    assert found
    for candidate in found:
        assert _labels(graph, candidate) == {"Lowered counter section", "Ordering counter"}
        deltas = {(move.delta_translation.x, move.delta_translation.y) for move in candidate.moves}
        assert len(deltas) == 1


def test_no_run_is_slid_toward_the_space_it_crowds(shop):
    graph, _, finding = shop
    spot = finding.locus.point
    section = next(node for node in graph.nodes if node.label == "Lowered counter section")
    here = (section.transform.position.x, section.transform.position.y)
    for candidate in built_in_set_moves(graph, finding, fixture_ids(graph)):
        delta = candidate.moves[0].delta_translation
        there = (here[0] + delta.x, here[1] + delta.y)
        slid = math.hypot(delta.x, delta.y)
        assert math.dist(there, (spot.x, spot.y)) >= math.dist(here, (spot.x, spot.y)) - 0.2 * slid - 1e-9


def test_a_built_in_the_caller_does_not_count_is_left_where_it_is(shop):
    graph, _, finding = shop
    counter = next(node.id for node in graph.nodes if node.label == "Ordering counter")
    for candidate in built_in_set_moves(graph, finding, fixture_ids(graph) - {counter}):
        assert _labels(graph, candidate) == {"Lowered counter section"}


def test_every_fixture_option_the_menu_offers_is_legal_and_accepted(shop, pipeline, pack, ledger, monkeypatch):
    graph, scenario, _ = shop
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline)
    monkeypatch.setattr(menu_module, "TIERS", ((_fixture_guesses, FIXTURE_TRIES),))
    menu = build_menu(graph, checker)
    assert any("it touches" in option.wording for option in menu.options)
    for option in menu.options:
        built = build(graph, option.edits.wall_shifts, option.edits.fixture_moves)
        candidate = apply_edits(graph, option.edits)
        relocated = {move.node_id for move in option.edits.fixture_moves}
        assert not violations(built, candidate)
        assert not relocation_violations(graph, candidate, relocated)
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts
