"""A counter's lowered section is moved together with the counter it belongs to, never pulled off it."""

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import relocation_violations, violations
from standardphysics_agents.fix.built_ins import built_in_set_moves, built_ins_apart_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training import menu as menu_module
from standardphysics_agents.training.construction import build, fixture_ids
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.menu import FIXTURE_TRIES, _fixture_guesses, build_menu
from standardphysics_contracts import Scenario, SceneGraph, to_meters
from standardphysics_fixtures import build_crowded_counter_graph, build_lawsuit_scenario
from standardphysics_pipeline import footprint
from standardphysics_pipeline.footprints import closest_point


@pytest.fixture(scope="module")
def shop(pipeline, ledger):
    graph, scenario = build_crowded_counter_graph(), build_lawsuit_scenario()
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
    monkeypatch.setattr(menu_module, "TIERS", (replace(menu_module.TIERS[2], guesses=_fixture_guesses, tries=FIXTURE_TRIES),))
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


@pytest.fixture(scope="module")
def partitioned_restroom(pipeline, pack, ledger):
    """A synthetic validation shop whose restroom turning circle is 53 of 60 inches between two partition panels."""
    data = json.loads((Path(__file__).parent / "fixtures/partitioned-restroom-shop.json").read_text())
    graph, scenario = SceneGraph.model_validate(data["graph"]), Scenario.model_validate(data["scenario"])
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline, scope="fittings",
                              promoted=frozenset())
    finding = next(f for f in checker.fixable_problems(checker.assess(graph)) if f.check_id == "restroom_turning_space")
    return graph, checker, finding


def test_every_named_built_in_is_pushed_straight_away_from_the_problem_at_once(partitioned_restroom):
    graph, _, finding = partitioned_restroom
    spot = (finding.locus.point.x, finding.locus.point.y)
    named = set(finding.locus.node_ids) & fixture_ids(graph)
    found = built_ins_apart_moves(graph, finding, fixture_ids(graph), to_meters(24.0))
    assert len(named) >= 2 and found
    for candidate in found:
        assert {move.node_id for move in candidate.moves} == named
        for move in candidate.moves:
            edge = closest_point(footprint(graph.by_id(move.node_id)), spot)
            delta = (move.delta_translation.x, move.delta_translation.y)
            outward = (edge[0] - spot[0]) * delta[0] + (edge[1] - spot[1]) * delta[1]
            assert outward > 0 and math.hypot(*delta) <= to_meters(24.0) + 1e-9


def test_the_menu_offers_pushing_a_restrooms_partitions_apart_when_no_single_slide_clears_it(partitioned_restroom):
    graph, checker, finding = partitioned_restroom
    menu = build_menu(graph, checker)
    label = menu.problems[finding.id]
    clearing = [option for option in menu.options if label in option.effect["clears"]]
    assert clearing, [option.wording for option in menu.options]
    assert all(option.edits.fixture_moves and "(construction)" in option.wording for option in clearing)
    for option in clearing:
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts
