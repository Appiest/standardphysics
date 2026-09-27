"""A card reader on the high counter is set down on the lowered section."""

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.fix.surfaces import lower_surface_moves
from standardphysics_agents.training import TrainingChecker
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.menu import build_menu
from standardphysics_fixtures import build_lawsuit_graph, build_lawsuit_scenario


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


def test_the_menu_offers_the_lowered_counter_and_every_option_is_legal(shop, pipeline, pack, ledger):
    graph, scenario, _ = shop
    checker = TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline)
    menu = build_menu(graph, checker)
    assert any(option.wording.startswith("set Card reader") and "Lowered counter section" in option.wording
               for option in menu.options)
    for option in menu.options:
        assert not violations(graph, apply_edits(graph, option.edits))
