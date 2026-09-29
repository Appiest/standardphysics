"""The menu's second tier: a turning circle or a counter's clear floor emptied at once, a table carried with its
seats, and short nudges."""

import json
import math
from dataclasses import replace

import pytest
from standardphysics_agents.checks.rectangles import EDGE_TOLERANCE, rectangle
from standardphysics_agents.fix import apply_moves, relocation_violations, violations
from standardphysics_agents.fix.clearing import _centres, _reaches_inside, circle_clearing_moves, space_clearing_moves
from standardphysics_agents.fix.groups import group_moves
from standardphysics_agents.fix.nudges import NUDGE_INCHES, nudge_moves
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training import menu as menu_module
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.menu import CLEARING_TRIES, _clearing_guesses, _groups, build_menu
from standardphysics_agents.training.owner import WishBook
from standardphysics_agents.training.quality import seat_table_pairs
from standardphysics_agents.training.wishes import infer_wishes, kept
from standardphysics_contracts import to_meters
from standardphysics_pipeline import PipelineMeasurements, footprint
from standardphysics_pipeline.footprints import rotation_about_z, touching
from standardphysics_pipeline.measure import COUNTER_CLEAR_DEPTH, COUNTER_CLEAR_WIDTH

from conftest import captured_room


@pytest.fixture(scope="module")
def room():
    return captured_room()


@pytest.fixture(scope="module")
def checker(room, pack, ledger):
    return TrainingChecker(room[1], rules=pack, ledger=ledger, measure=PipelineMeasurements())


@pytest.fixture(scope="module")
def problems(room, checker):
    return {finding.check_id: finding for finding in checker.fixable_problems(checker.assess(room[0]))}


def _seat_at_a_table(graph):
    nodes = {node.id: node for node in graph.nodes}
    return next(seat for seat, table in seat_table_pairs(graph) if nodes[table].movable)


def _naming(finding, node_id):
    return finding.model_copy(update={"locus": finding.locus.model_copy(update={"node_ids": [node_id]})})


def test_a_circle_push_moves_every_piece_inside_it_out_together(room, problems):
    graph, _ = room
    finding = problems["turning_space"]
    found = circle_clearing_moves(graph, finding)
    assert found
    assert [move.disruption for move in found] == sorted(move.disruption for move in found)
    radius = to_meters(finding.required_inches) / 2
    for candidate in found:
        moved = apply_moves(graph, candidate.moves)
        assert all(graph.by_id(move.node_id).movable for move in candidate.moves)
        assert any(not any(_reaches_inside(moved.by_id(move.node_id), centre, radius) for move in candidate.moves)
                   for centre in _centres(finding))


def test_a_circle_a_pinned_piece_reaches_into_is_left_alone(room, problems):
    graph, _ = room
    finding = problems["turning_space"]
    pinned = frozenset(move.node_id for candidate in circle_clearing_moves(graph, finding) for move in candidate.moves)
    assert circle_clearing_moves(graph, finding, pinned) == []


def test_only_a_turning_space_is_pushed_clear(room, problems):
    assert circle_clearing_moves(room[0], problems["service_counter_approach"]) == []


def _clear_floor(graph, finding):
    against = graph.by_id(finding.locus.node_ids[0])
    return rectangle(finding.locus.point, COUNTER_CLEAR_WIDTH - 2 * EDGE_TOLERANCE,
                     COUNTER_CLEAR_DEPTH - 2 * EDGE_TOLERANCE, rotation_about_z(against))


def test_a_counters_clear_floor_is_emptied_by_pushing_every_piece_out_at_once(room, problems):
    graph, _ = room
    finding = problems["service_counter_approach"]
    found = space_clearing_moves(graph, finding)
    assert found
    assert [move.disruption for move in found] == sorted(move.disruption for move in found)
    space = _clear_floor(graph, finding)
    for candidate in found:
        moved = apply_moves(graph, candidate.moves)
        assert not violations(graph, moved)
        assert not any(touching(footprint(moved.by_id(move.node_id)), space) for move in candidate.moves)


def test_only_a_counters_clear_floor_is_pushed_out_as_a_rectangle(room, problems):
    assert space_clearing_moves(room[0], problems["turning_space"]) == []


def test_nudges_are_short_slides_of_a_named_piece_that_never_head_into_the_problem(room, problems):
    graph, _ = room
    finding = problems["turning_space"]
    found = nudge_moves(graph, finding)
    assert found and {move.node_id for candidate in found for move in candidate.moves} <= set(finding.locus.node_ids)
    spot = finding.locus.point
    for candidate in found:
        (move,) = candidate.moves
        node = graph.by_id(move.node_id)
        delta = move.delta_translation
        assert math.hypot(delta.x, delta.y) <= to_meters(max(NUDGE_INCHES)) + 1e-9
        before = math.dist((node.transform.position.x, node.transform.position.y), (spot.x, spot.y))
        after = math.dist((node.transform.position.x + delta.x, node.transform.position.y + delta.y), (spot.x, spot.y))
        assert after >= before - math.hypot(delta.x, delta.y) * 0.2 - 1e-9


def test_a_table_moves_with_its_seats_so_every_seat_stays_at_it(room, problems, pipeline):
    graph, _ = room
    seat = _seat_at_a_table(graph)
    found = group_moves(graph, _naming(problems["turning_space"], seat), _groups(graph))
    assert found
    at_tables = [wish for wish in infer_wishes(graph, pipeline) if wish.kind == "with_table"]
    for candidate in found:
        deltas = {(round(move.delta_translation.x, 9), round(move.delta_translation.y, 9)) for move in candidate.moves}
        assert len(candidate.moves) > 1 and len(deltas) == 1
        moved = apply_moves(graph, candidate.moves)
        assert all(kept(wish, graph, moved, pipeline) for wish in at_tables)


def test_a_set_holding_a_piece_that_cannot_move_is_not_carried(room, problems):
    graph, _ = room
    seat = _seat_at_a_table(graph)
    table = next(table for chair, table in seat_table_pairs(graph) if chair == seat)
    assert group_moves(graph, _naming(problems["turning_space"], seat), _groups(graph), frozenset({table})) == []


@pytest.fixture
def second_tier_menu(room, checker, monkeypatch):
    """The menu built from the second tier alone, so every option comes from the new families."""
    monkeypatch.setattr(menu_module, "TIERS", ((_clearing_guesses, CLEARING_TRIES),))
    return build_menu(room[0], checker)


def test_every_second_tier_option_is_legal_and_accepted_by_the_gate(room, checker, second_tier_menu):
    graph = room[0]
    assert second_tier_menu.options
    for option in second_tier_menu.options:
        candidate = apply_edits(graph, option.edits)
        assert not violations(graph, candidate)
        assert not relocation_violations(graph, candidate, set())
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts


def test_a_stated_wish_holds_every_second_tier_option(room, checker, pipeline, monkeypatch):
    graph = room[0]
    monkeypatch.setattr(menu_module, "TIERS", ((_clearing_guesses, CLEARING_TRIES),))
    seat_wish = next(wish for wish in infer_wishes(graph, pipeline) if wish.kind == "with_table")
    stated = WishBook()
    stated.add(replace(seat_wish, source="stated"), graph)
    menu = build_menu(graph, checker, stated)
    for option in menu.options:
        assert kept(seat_wish, graph, apply_edits(graph, option.edits), pipeline)


def test_the_second_tier_is_only_measured_for_a_problem_the_first_leaves(room, checker, monkeypatch):
    asked = []

    def spy(graph, finding, checker, label):
        asked.append(finding.check_id)
        return []

    tiers = menu_module.TIERS
    monkeypatch.setattr(menu_module, "TIERS", (tiers[0], (spy, CLEARING_TRIES), tiers[2]))
    menu = build_menu(room[0], checker)
    cleared = {label for option in menu.options for label in option.effect["clears"]}
    left = [view["check"] for view in menu.problem_view if view["label"] not in cleared]
    assert sorted(asked) == sorted(left)
