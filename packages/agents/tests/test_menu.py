"""The menu of legal moves: every option is legal, and every answer resolves to a legal room."""

import json
import math
from pathlib import Path

import pytest
from standardphysics_agents.fix import relocation_violations, violations
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training.construction import build
from standardphysics_agents.training.edits import TrainingEdits, apply_edits, parse_edits
from standardphysics_agents.training.menu import (
    MENU_SYSTEM_PROMPT,
    Menu,
    MenuChoice,
    Option,
    _drop_covered_diagonals,
    _Guess,
    _square_to_room,
    build_menu,
    menu_messages,
    parse_choice,
    resolve,
    resolve_choice,
)
from standardphysics_contracts import Scenario, SceneGraph
from standardphysics_pipeline import PipelineMeasurements


@pytest.fixture(scope="module")
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"]), Scenario.model_validate(data["scenario"])


@pytest.fixture(scope="module")
def checker(room, pack, ledger):
    return TrainingChecker(room[1], rules=pack, ledger=ledger, measure=PipelineMeasurements())


@pytest.fixture(scope="module")
def menu(room, checker):
    return build_menu(room[0], checker)


def _legal(graph, completion) -> bool:
    edits = parse_edits(completion)
    built = build(graph, edits.wall_shifts, edits.fixture_moves)
    candidate = apply_edits(graph, edits)
    relocated = {move.node_id for move in edits.fixture_moves}
    return not violations(built, candidate) and not relocation_violations(graph, candidate, relocated)


def test_every_option_passes_the_hard_constraints_and_the_gate(room, checker, menu):
    graph = room[0]
    assert menu.options, "the fixture room has problems furniture can clear"
    for option in menu.options:
        candidate = apply_edits(graph, option.edits)
        assert not violations(build(graph, option.edits.wall_shifts, option.edits.fixture_moves), candidate)
        relocated = {move.node_id for move in option.edits.fixture_moves}
        assert not relocation_violations(graph, candidate, relocated)
        verdict = score_completion(json.dumps(option.edits.model_dump(mode="json")), graph, checker)
        assert verdict.hard_constraints_pass and verdict.gate_accepts


def test_options_are_worded_as_relations_and_labelled_with_measured_effects(menu):
    for option in menu.options:
        assert option.wording.startswith(("slide ", "turn ", "move built-in "))
        assert " for P" in option.wording
        assert set(option.effect) >= {"clears", "improves", "new_problems", "usable", "inches_moved", "clashes_with"}
        assert option.effect["clears"] or option.effect["improves"] or option.effect["fixable_left"] < len(
            menu.problems)
    assert len({option.wording for option in menu.options}) == len(menu.options)
    assert [option.number for option in menu.options] == list(range(1, len(menu.options) + 1))


def test_picks_apply_in_order_and_a_clashing_pick_is_dropped_and_reported(room, menu):
    graph = room[0]
    numbers = [option.number for option in menu.options][:7]
    resolution = resolve_choice(graph, menu, MenuChoice(choose=[*numbers, 99], why="all of them"))
    assert resolution.applied and resolution.applied[0] == numbers[0]
    assert {"option": 99, "reason": "no such option"} in resolution.dropped
    assert len(resolution.applied) + len(resolution.dropped) == len(numbers) + 1
    assert _legal(graph, resolution.completion)


def test_clashes_with_names_exactly_the_picks_that_would_be_dropped(room, menu):
    graph = room[0]
    first = menu.options[0]
    for other in menu.options[1:]:
        resolution = resolve_choice(graph, menu, MenuChoice(choose=[first.number, other.number]))
        assert (other.number in first.effect["clashes_with"]) == bool(resolution.dropped)


def test_a_free_form_move_onto_another_piece_is_snapped_to_legal_floor(room, checker, menu):
    graph = room[0]
    pieces = [node for node in graph.nodes if node.movable]
    chair, other = min(((a, b) for a in pieces for b in pieces if a.id != b.id), key=lambda pair: math.dist(
        (pair[0].transform.position.x, pair[0].transform.position.y),
        (pair[1].transform.position.x, pair[1].transform.position.y)))
    dx = other.transform.position.x - chair.transform.position.x
    dy = other.transform.position.y - chair.transform.position.y
    reply = json.dumps({"moves": [{"node_id": str(chair.id), "dx": dx, "dy": dy, "rotation_degrees": 0}]})
    assert "collided" in score_completion(reply, graph, checker).reason
    resolution = resolve(reply, graph, menu)
    assert resolution.interface == "free_moves" and resolution.snapped["kept"] == 1
    assert _legal(graph, resolution.completion)
    assert score_completion(resolution.completion, graph, checker).hard_constraints_pass


def test_unparseable_answers_become_no_moves_rather_than_a_collision(room, checker, menu):
    resolution = resolve("I would move the chair", room[0], menu)
    assert resolution.interface == "unparseable"
    assert score_completion(resolution.completion, room[0], checker).reason == "no_supported_furniture_move"


def test_choices_are_read_through_fences_and_thinking():
    for text in ('{"choose":[3,1],"why":"x"}', '```json\n{"choose":[3,1]}\n```', '<think>hm</think>{"choose":[3,1]}'):
        assert parse_choice(text).choose == [3, 1]
    assert parse_choice('{"moves":[]}') is None


def test_each_turn_is_stateless_and_carries_the_last_result(room, checker, menu):
    last = {"applied": [1], "accepted": True}
    messages = menu_messages(room[0], checker, menu, last)
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == MENU_SYSTEM_PROMPT
    content = json.loads(messages[1]["content"])
    assert content["last_result"] == last
    assert [option["option"] for option in content["options"]] == [o.number for o in menu.options]
    assert {problem["label"] for problem in content["problems"]} == set(menu.problems.values())


def test_no_option_moves_a_piece_the_owner_said_to_keep_where_it_is(room, checker, menu):
    from standardphysics_agents.training.owner import WishBook
    from standardphysics_agents.training.wishes import stays_put

    graph = room[0]
    held = graph.by_id(menu.options[0].edits.moves[0].node_id)
    book = WishBook()
    book.add(stays_put(held), graph)
    held_menu = build_menu(graph, checker, stated=book)
    assert not any(move.node_id == held.id for option in held_menu.options for move in option.edits.moves)
    assert {"wish": stays_put(held).text, "source": "stated"} in held_menu.wish_view
    resolution = resolve_choice(graph, held_menu, MenuChoice(choose=[1]))
    assert not resolution.dropped


def test_options_name_the_inferred_wishes_they_would_break_and_the_model_is_told_them(room, checker, menu):
    assert all("breaks_wishes" in option.effect for option in menu.options)
    labels = {wish["label"] for wish in menu.wish_view if wish["source"] == "inferred"}
    assert labels and all(set(option.effect["breaks_wishes"]) <= labels for option in menu.options)
    content = json.loads(menu_messages(room[0], checker, menu, None)[1]["content"])
    assert content["owner_wishes"] == menu.wish_view


def test_a_shuffled_menu_with_wishes_hidden_offers_the_same_moves_without_the_hints(room, checker, menu):
    from standardphysics_agents.training.menu import MenuView

    blind = build_menu(room[0], checker, view=MenuView(order="shuffled", wishes_shown=False, seed=3))
    assert sorted(o.wording for o in blind.options) == sorted(o.wording for o in menu.options)
    assert not any("breaks_wishes" in option.effect for option in blind.options)
    assert blind.wish_view == []
    assert [o.number for o in blind.options] == list(range(1, len(blind.options) + 1))


def test_every_option_says_whether_it_ends_square(menu):
    assert menu.options
    for option in menu.options:
        assert isinstance(option.effect["ends_square"], bool)


ROOM_HEADING_DEGREES = 20.0


@pytest.mark.parametrize("heading_degrees", [0, 90, 180, 30, 45])
def test_world_aligned_headings_are_diagonal_in_a_room_turned_20_degrees(heading_degrees):
    assert not _square_to_room(heading_degrees, ROOM_HEADING_DEGREES)


@pytest.mark.parametrize("heading_degrees", [20, 110, 200, -70])
def test_headings_square_to_a_turned_room_read_as_square(heading_degrees):
    assert _square_to_room(heading_degrees, ROOM_HEADING_DEGREES)


def _measured(clears: list[str], ends_square: bool) -> tuple:
    guess = _Guess(edits=TrainingEdits(), wording=f"wording {clears} {ends_square}")
    effect = {"clears": clears, "ends_square": ends_square, "fixable_left": 0, "construction_inches": 0.0,
              "usable": 1.0, "inches_moved": 0.0}
    return guess, effect


def test_a_diagonal_option_is_dropped_when_a_square_option_clears_the_same_problem():
    square = _measured(["P1"], True)
    diagonal = _measured(["P1"], False)
    assert _drop_covered_diagonals([square, diagonal]) == [square]


def test_a_diagonal_option_stays_when_it_alone_clears_a_problem():
    square_elsewhere = _measured(["P2"], True)
    only_diagonal = _measured(["P1"], False)
    kept = _drop_covered_diagonals([square_elsewhere, only_diagonal])
    assert kept == [square_elsewhere, only_diagonal]


def test_owner_words_drop_piece_ids_and_name_problems_by_title():
    menu = Menu(problems={}, options=[Option(1, "slide Table [68d0] 3 in further from the Counter, for P1",
                                             TrainingEdits(), {})],
                problem_view=[{"label": "P1", "title": "The path to the counter is too narrow"}])
    assert menu.picked_in_owner_words(1) == "slide Table 3 in further from the Counter"
    assert menu.in_owner_words("It clears P1 and P7 by moving Chair [3f2a].") == (
        'It clears "The path to the counter is too narrow" and P7 by moving Chair.')
