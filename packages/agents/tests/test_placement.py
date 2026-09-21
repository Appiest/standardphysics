"""A captured room with the counter and turning-space failures in the UI."""

import json
from pathlib import Path

import pytest
from standardphysics_agents import assess
from standardphysics_agents.evaluation.gate import accepts
from standardphysics_agents.fix import apply_moves, pinch_from, propose_fix, violations
from standardphysics_agents.fix.placement import placements
from standardphysics_agents.router import state_for
from standardphysics_contracts import Scenario, SceneGraph
from standardphysics_pipeline import PipelineMeasurements


@pytest.fixture
def room():
    data = json.loads((Path(__file__).parent / "fixtures/placement-room.json").read_text())
    return SceneGraph.model_validate(data["graph"]), Scenario.model_validate(data["scenario"])


def test_the_captured_room_gets_two_actual_moves_and_no_measured_failures(room, pack, ledger):
    graph, scenario = room
    original = graph.model_dump()
    measure = PipelineMeasurements()
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    assert {f.check_id for f in before.problems} == {"turning_space", "service_counter_approach"}
    current = graph
    for _ in range(2):
        baseline = assess(current, scenario, measure, rules=pack, ledger=ledger)
        state = state_for(baseline.findings, current, pack)
        assert {f.id for f in baseline.problems} <= set(state.fixable_finding_ids)
        outcome = propose_fix(current, scenario, measure, baseline.problems,
                              baseline=baseline, rules=pack, ledger=ledger, offer_relaxation=False)
        assert outcome.found and outcome.proposal.moves
        assert outcome.proposal.preserves_inventory
        assert not violations(current, outcome.graph)
        after = assess(outcome.graph, scenario, measure, rules=pack, ledger=ledger)
        assert accepts(baseline, after)
        current = outcome.graph
    assert not after.problems
    assert len([n for n in current.nodes if n.transform != graph.by_id(n.id).transform]) == 2
    assert all(n.dimensions == graph.by_id(n.id).dimensions for n in current.nodes)
    assert graph.model_dump() == original


def test_placements_combine_rotation_and_translation_and_keep_external_veto(room, pack, ledger):
    graph, scenario = room
    measure = PipelineMeasurements()
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    finding = next(f for f in before.problems if f.check_id == "service_counter_approach")
    guesses = placements(graph, pinch_from(finding, graph), finding, pack, 96)
    assert any(m.delta_rotation_z_degrees and (m.delta_translation.x or m.delta_translation.y)
               for guess in guesses for m in guess.moves)
    assert all(not violations(graph, apply_moves(graph, guess.moves)) for guess in guesses)
    outcome = propose_fix(graph, scenario, measure, [finding], rules=pack, ledger=ledger,
                          offer_relaxation=False, candidate_rejection=lambda *_: "mesh_collision")
    assert not outcome.found
    assert "mesh_collision" in outcome.rejected


def test_two_chairs_blocking_one_region_are_relocated_together(room, pack, ledger):
    graph, scenario = room
    measure = PipelineMeasurements()
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    finding = next(f for f in before.problems if f.check_id == "turning_space")
    chair = graph.by_id(finding.locus.node_ids[0])
    from uuid import uuid4
    other = chair.model_copy(update={"id": uuid4()})
    graph = graph.model_copy(update={"nodes": [*graph.nodes, other]})
    before = assess(graph, scenario, measure, rules=pack, ledger=ledger)
    finding = next(f for f in before.problems if f.check_id == "turning_space")
    guesses = placements(graph, pinch_from(finding, graph), finding, pack, 96)
    # Neither chair alone can clear the space occupied by its twin.
    clearing = []
    for guess in guesses:
        after = assess(apply_moves(graph, guess.moves), scenario, measure, rules=pack, ledger=ledger)
        if finding.id not in {f.id for f in after.problems} and accepts(before, after):
            clearing.append(guess)
            break
    assert clearing
    assert all({m.node_id for m in guess.moves} == {chair.id, other.id} for guess in clearing)
