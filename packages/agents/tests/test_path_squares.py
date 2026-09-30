"""A passing space opened along the route: every piece standing in one 60 inch square pushed out of it at once."""

import json
from dataclasses import replace

import pytest
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.path_squares import route_paths, square_clearing_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training import menu as menu_module
from standardphysics_agents.training.menu import CLEARING_TRIES, _clearing_guesses, build_menu
from standardphysics_contracts import Mat4, SceneNode, Vec3
from standardphysics_fixtures import build_graph, build_scenario, node_id
from standardphysics_pipeline import PipelineMeasurements

EAST_STOOLS = (-3.2, -2.6, -2.0, -1.4, -0.8, 0.8, 1.4, 2.0, 2.6)
WEST_STOOLS = (-3.2, -1.6, -0.8, 0.8, 1.4, 2.0, 2.6)
"""Stools lining both sides of the shop's centre aisle, leaving it 41 inches wide end to end."""


def _stool(name: str, x: float, y: float) -> SceneNode:
    return SceneNode(id=node_id(name), kind="object", label="Stool", raw_category="chair",
                     dimensions=Vec3(x=0.45, y=0.45, z=0.75), transform=Mat4.translation(x, y, 0.375), movable=True)


@pytest.fixture(scope="module")
def lined_aisle(pack, ledger):
    stools = [*(_stool(f"stool_east_{index}", 0.75, y) for index, y in enumerate(EAST_STOOLS)),
              *(_stool(f"stool_west_{index}", -0.75, y) for index, y in enumerate(WEST_STOOLS))]
    graph = build_graph()
    graph = graph.model_copy(update={"nodes": [*graph.nodes, *stools]})
    checker = TrainingChecker(build_scenario(), rules=pack, ledger=ledger, measure=PipelineMeasurements())
    finding = next(f for f in checker.fixable_problems(checker.assess(graph)) if f.check_id == "passing_space")
    return graph, checker, finding


def _routes(graph, checker):
    return route_paths(graph, checker.scenario, checker.measure)


def test_the_aisle_has_no_passing_space_anywhere_along_the_route(lined_aisle):
    _, _, finding = lined_aisle
    assert finding.measured_inches == 0.0 and finding.locus.annotation.kind == "path"


def test_each_square_push_empties_a_60_inch_square_on_the_route(lined_aisle):
    graph, checker, finding = lined_aisle
    found = square_clearing_moves(graph, finding, _routes(graph, checker))
    assert found
    for candidate in found:
        moved = apply_moves(graph, candidate.moves)
        assert not violations(graph, moved)
        assert {move.node_id for move in candidate.moves} <= {node.id for node in graph.nodes if node.movable}


def test_the_menu_offers_opening_a_passing_space_when_no_single_stool_can(lined_aisle, monkeypatch):
    graph, checker, finding = lined_aisle
    monkeypatch.setattr(menu_module, "TIERS",
                        (replace(menu_module.TIERS[1], guesses=_clearing_guesses, tries=CLEARING_TRIES),))
    menu = build_menu(graph, checker)
    label = menu.problems[finding.id]
    clearing = [option for option in menu.options if label in option.effect["clears"]]
    assert clearing, [option.wording for option in menu.options]
    for option in clearing:
        assert len(option.edits.moves) >= 2
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts


def test_a_square_a_pinned_stool_stands_in_is_left_alone(lined_aisle):
    graph, checker, finding = lined_aisle
    pinned = frozenset(node_id(f"stool_east_{index}") for index in range(len(EAST_STOOLS)))
    for candidate in square_clearing_moves(graph, finding, _routes(graph, checker), pinned):
        assert not {move.node_id for move in candidate.moves} & pinned
