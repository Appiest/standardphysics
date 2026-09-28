"""A card reader on the high counter is set down on the lowered section; the shop's directives hold only built-ins still."""

import json

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.surfaces import lower_surface_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.menu import build_menu
from standardphysics_contracts.precedents import SpaceTypology
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario

PREVIEW_UNVERIFIED = "SP_PREVIEW_UNVERIFIED_PRECEDENTS"


@pytest.fixture(scope="module")
def shop(pipeline, ledger):
    graph, scenario = build_lawsuit_graph(), build_lawsuit_scenario()
    finding = next(f for f in assess(graph, scenario, pipeline, ledger=ledger).problems
                   if f.check_id == "point_of_sale_height")
    return graph, scenario, finding


def test_the_reader_is_offered_spots_on_surfaces_no_higher_than_the_limit(shop):
    graph, _, finding = shop
    found = lower_surface_moves(graph, finding)
    assert found and {move.item.label for move in found} == {"Card reader"}
    assert "Lowered counter section" in {move.surface.label for move in found}
    assert "Ordering counter" not in {move.surface.label for move in found}
    disruptions = [move.candidate.disruption for move in found]
    assert disruptions == sorted(disruptions)


def test_some_spot_is_legal_and_clears_the_height_problem(shop, pipeline, ledger):
    graph, scenario, finding = shop
    for move in lower_surface_moves(graph, finding):
        moved = apply_moves(graph, move.candidate.moves)
        if violations(graph, moved):
            continue
        after = assess(moved, scenario, pipeline, ledger=ledger)
        assert finding.id not in {f.id for f in after.problems}
        return
    pytest.fail("no legal spot on any lower surface")


def test_a_problem_that_is_not_too_high_gets_no_surface_moves(shop):
    graph, _, finding = shop
    lowered = finding.model_copy(update={"measured_inches": finding.required_inches - 1})
    assert lower_surface_moves(graph, lowered) == []


@pytest.fixture(scope="module")
def plain_menu(shop, pipeline, pack, ledger):
    graph, scenario, _ = shop
    return build_menu(graph, TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline))


def _boba_checker(shop, pipeline, pack, ledger, monkeypatch) -> TrainingChecker:
    monkeypatch.setenv(PREVIEW_UNVERIFIED, "1")
    return TrainingChecker(shop[1], rules=pack, ledger=ledger, measure=pipeline,
                           space_typology=SpaceTypology.QSR_BEVERAGE)


def _card_reader_option(menu):
    return next(option for option in menu.options if option.wording.startswith("set Card reader"))


def test_the_menu_offers_the_lowered_counter_and_every_option_is_legal(shop, plain_menu):
    graph, _, _ = shop
    assert any(option.wording.startswith("set Card reader") and "Lowered counter section" in option.wording
               for option in plain_menu.options)
    for option in plain_menu.options:
        assert not violations(graph, apply_edits(graph, option.edits))


def test_a_boba_shop_still_lets_its_card_reader_move_to_the_lowered_counter(shop, plain_menu, pipeline, pack,
                                                                            ledger, monkeypatch):
    graph, _, _ = shop
    completion = json.dumps(_card_reader_option(plain_menu).edits.model_dump(mode="json"))
    verdict = score_completion(completion, graph, _boba_checker(shop, pipeline, pack, ledger, monkeypatch))
    assert verdict.gate_accepts, verdict.reason


def test_a_boba_shop_refuses_relocating_its_counter_even_as_construction(shop, pipeline, pack, ledger, monkeypatch):
    graph, scenario, _ = shop
    counter = next(node for node in graph.nodes if node.label == "Ordering counter")
    completion = json.dumps({"moves": [], "fixture_moves": [
        {"node_id": str(counter.id), "dx_inches": 0.0, "dy_inches": -3.0}]})
    plain = score_completion(completion, graph, TrainingChecker(scenario, rules=pack, ledger=ledger,
                                                                measure=pipeline))
    boba = score_completion(completion, graph, _boba_checker(shop, pipeline, pack, ledger, monkeypatch))
    assert "precedent_violation" not in plain.reason
    assert boba.reward == 0.0 and boba.reason == "precedent_violation:moved_fixed_role"


def test_a_boba_shops_menu_offers_only_options_its_directives_allow(shop, pipeline, pack, ledger, monkeypatch):
    graph, _, _ = shop
    checker = _boba_checker(shop, pipeline, pack, ledger, monkeypatch)
    menu = build_menu(graph, checker)
    assert menu.veto is not None and menu.options
    assert any(option.wording.startswith("set Card reader") for option in menu.options)
    for option in menu.options:
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.gate_accepts, verdict.reason


def test_no_space_type_puts_no_veto_on_the_room(shop, pipeline, pack, ledger, monkeypatch):
    graph, scenario, _ = shop
    monkeypatch.setenv(PREVIEW_UNVERIFIED, "1")
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline)
    assert checker.space_typology is None and checker.directive_veto(graph) is None
