"""The owner's explanation: only measured facts, never an invented one."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from standardphysics_agents.training import TrainingChecker
from standardphysics_agents.training.edits import apply_edits
from standardphysics_agents.training.explain import _moved_toward_passing, explain_change
from standardphysics_agents.training.menu import build_menu
from standardphysics_agents.training.wishes import infer_wishes
from standardphysics_contracts import Mat4, Scenario, SceneGraph
from standardphysics_pipeline import PipelineMeasurements

ID_TAG = re.compile(r"\[[0-9a-fA-F]{4}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _no_ids(*texts: str) -> bool:
    return not any(ID_TAG.search(text) or UUID_RE.search(text) for text in texts)


@pytest.fixture(scope="module")
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"]), Scenario.model_validate(data["scenario"])


@pytest.fixture(scope="module")
def checker(room, pack, ledger):
    return TrainingChecker(room[1], rules=pack, ledger=ledger, measure=PipelineMeasurements())


@pytest.fixture(scope="module")
def wishes(room, checker):
    return infer_wishes(room[0], checker.measure)


@pytest.fixture(scope="module")
def option_one_after(room, checker):
    menu = build_menu(room[0], checker)
    return apply_edits(room[0], menu.options[0].edits)


def test_an_unchanged_room_has_nothing_to_report(room, checker, wishes):
    graph = room[0]
    explanation = explain_change(graph, graph.model_copy(), checker, wishes)
    assert explanation.moves == explanation.fixed == explanation.kept == explanation.bent == []
    assert explanation.text() == ""


def test_only_wishes_about_moved_pieces_are_reported_and_each_only_once(room, checker, wishes, option_one_after):
    explanation = explain_change(room[0], option_one_after, checker, wishes)
    moved = {node.id for node in option_one_after.nodes if node.transform != room[0].by_id(node.id).transform}
    touched = [wish for wish in wishes if wish.kind == "clear_view" or set(wish.subjects) & moved]
    assert len(explanation.kept) + len(explanation.bent) <= len(touched)
    assert len(set(explanation.kept)) == len(explanation.kept)


def test_option_one_names_each_moved_piece_with_its_distance_and_a_cleared_problem(room, checker, wishes,
                                                                                    option_one_after):
    graph = room[0]
    explanation = explain_change(graph, option_one_after, checker, wishes)
    assert len(explanation.moves) == 1
    assert re.search(r"\d+ in", explanation.moves[0])
    assert explanation.fixed, "option one clears a fixable problem"
    assert any(re.search(r"ADA \d", sentence) for sentence in explanation.fixed)
    assert any(sentence.startswith("Fixed:") for sentence in explanation.fixed)
    blob = " ".join([*explanation.moves, *explanation.fixed, *explanation.kept, *explanation.bent])
    assert _no_ids(blob)


def test_a_seat_moved_a_metre_from_its_table_breaks_the_with_table_wish(room, checker, wishes):
    graph = room[0]
    with_table = next(wish for wish in wishes if wish.kind == "with_table")
    chair = next(node for node in graph.nodes if node.id == with_table.subjects[0])
    shifted = list(chair.transform.m)
    shifted[3] += 1.0
    shifted[7] += 1.0
    moved_chair = chair.model_copy(update={"transform": Mat4(m=shifted)})
    after = graph.model_copy(update={
        "nodes": [moved_chair if node.id == chair.id else node for node in graph.nodes],
    })
    explanation = explain_change(graph, after, checker, wishes)
    assert any(sentence.startswith(f"{chair.label} stays at the") for sentence in explanation.bent)
    assert _no_ids(*explanation.bent)
    assert "We had to bend" in explanation.text()


def test_why_is_carried_through_and_stripped(room, checker, wishes, option_one_after):
    graph = room[0]
    explanation = explain_change(graph, option_one_after, checker, wishes, why="  it clears the pinch  ")
    assert explanation.why == "it clears the pinch"
    assert "Why this option: it clears the pinch" in explanation.text()
    without_why = explain_change(graph, option_one_after, checker, wishes)
    assert without_why.why == ""
    assert "Why this option" not in without_why.text()


def test_as_dict_round_trips_every_field(room, checker, wishes, option_one_after):
    graph = room[0]
    explanation = explain_change(graph, option_one_after, checker, wishes, why="because")
    payload = explanation.as_dict()
    assert payload == {
        "moves": explanation.moves,
        "fixed": explanation.fixed,
        "kept": explanation.kept,
        "bent": explanation.bent,
        "why": explanation.why,
    }


@pytest.mark.parametrize(("comparison", "before", "after", "better"), [
    ("at_least", 40.4, 52.8, True),
    ("at_least", 40.4, 37.9, False),
    ("at_least", None, 12.0, True),
    ("at_most", 54.0, 48.0, True),
    ("at_most", 48.0, 54.0, False),
])
def test_a_changed_measurement_is_called_better_only_when_it_moves_toward_passing(comparison, before, after, better):
    rule = SimpleNamespace(comparison=comparison)
    assert _moved_toward_passing(rule, before, after) is better
