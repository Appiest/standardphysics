"""Bounding the menu and the search for a person waiting on them: focus and deadlines."""

import json
import time
from pathlib import Path

import pytest
from standardphysics_agents import assess
from standardphysics_agents.fix import pinch_from, propose_fix
from standardphysics_agents.fix.budget import deadline_in, out_of_time
from standardphysics_agents.fix.placement import placements
from standardphysics_agents.training import TrainingChecker
from standardphysics_agents.training.menu import MenuLimits, build_menu
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
def whole(room, checker):
    return build_menu(room[0], checker)


class _CountingChecker(TrainingChecker):
    assessed: int = 0

    def assess(self, graph):
        self.assessed += 1
        return super().assess(graph)


def test_no_deadline_never_runs_out_and_a_past_one_has():
    assert not out_of_time(None) and deadline_in(None) is None
    assert out_of_time(time.monotonic() - 1.0)
    assert not out_of_time(deadline_in(60.0))


def test_a_focused_menu_only_offers_options_for_the_findings_asked_about(room, checker, whole):
    assert len(whole.problems) > 1, "the fixture room has two problems"
    wanted, label = next(iter(whole.problems.items()))
    focused = build_menu(room[0], checker, limits=MenuLimits(focus=frozenset({wanted})))
    assert focused.problems == whole.problems
    assert focused.options
    assert all(option.wording.endswith(f"for {label}") for option in focused.options)


def test_a_passed_deadline_measures_nothing_and_still_labels_the_problems(room, checker, whole):
    counting = _CountingChecker(checker.scenario, rules=checker.rules, ledger=checker.ledger, measure=checker.measure)
    late = build_menu(room[0], counting, limits=MenuLimits(deadline=time.monotonic() - 1.0))
    assert late.problems == whole.problems
    assert late.options == []
    assert counting.assessed == 1, "only the room itself is assessed"


def test_a_deadline_passing_mid_menu_keeps_what_was_measured(room, checker, monkeypatch):
    counting = _CountingChecker(checker.scenario, rules=checker.rules, ledger=checker.ledger, measure=checker.measure)
    clock = iter(range(10_000))
    monkeypatch.setattr("standardphysics_agents.fix.budget.monotonic", lambda: float(next(clock)))
    partial = build_menu(room[0], counting, limits=MenuLimits(deadline=6.0))
    assert 1 < counting.assessed < 6
    assert partial.options and len(partial.options) < counting.assessed


def test_a_passed_deadline_stops_the_search_and_the_placement_beam(room, pack, ledger):
    graph, scenario = room
    measure = PipelineMeasurements()
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    finding = before.problems[0]
    past = time.monotonic() - 1.0
    assert placements(graph, pinch_from(finding, graph), finding, pack, 24, deadline=past) == []
    outcome = propose_fix(graph, scenario, measure, before.problems, rules=pack, ledger=ledger, baseline=before,
                          deadline=past)
    assert not outcome.found and outcome.measured == 0 and outcome.relaxation is None
