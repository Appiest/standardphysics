from uuid import uuid4

from standardphysics_agents.models import ModelAnswer
from standardphysics_agents.redesign import propose_redesign, validate_redesign
from standardphysics_agents.router import Rejected
from standardphysics_agents.rules import VerificationLedger
from standardphysics_agents.workflows import WHEELCHAIR_PROFILE, Workflow
from standardphysics_fixtures import node_id


def answer(moves):
    return ModelAnswer({'moves': moves}, 'OpenAI', 'openai/gpt-6-astra')


def move(node, dx=0, dy=0):
    return {'node_id': str(node), 'dx': dx, 'dy': dy, 'rotation_degrees': 0}


def validate(graph, scenario, pipeline, pack, ledger, moves):
    return validate_redesign(graph, answer(moves), [Workflow(id='room', title='Room', scenario=scenario)], [WHEELCHAIR_PROFILE], pipeline, rules=pack, ledger=ledger)


def test_model_cannot_move_a_fixed_fixture(graph, scenario, pipeline, pack, ledger):
    result = validate(graph, scenario, pipeline, pack, ledger, [move(node_id('counter'), dx=1)])
    assert not result.accepted
    assert 'moved_something_fixed' in result.reasons
    assert result.graph is None


def test_model_cannot_resize_inject_unknown_ids_or_nonfinite_moves(graph, scenario, pipeline, pack, ledger):
    for moves in ([{**move(node_id('table_1')), 'dimensions': [0,0,0]}], [move(uuid4())], [move(node_id('table_1'), dx=float('nan'))], [move(node_id('table_1'))]*2):
        result = validate(graph, scenario, pipeline, pack, ledger, moves)
        assert not result.accepted
        assert result.graph is None


def test_empty_duplicate_unknown_and_no_op_moves_have_distinct_reasons(
    graph, scenario, pipeline, pack, ledger
):
    cases = [
        ([], "no_supported_furniture_move"),
        ([move(node_id("table_1"), dx=0.1)] * 2, "duplicate_objects"),
        ([move(uuid4(), dx=0.1)], "unknown_objects"),
        ([move(node_id("table_1"))], "no_op_moves"),
    ]
    for moves, reason in cases:
        result = validate(graph, scenario, pipeline, pack, ledger, moves)
        assert result.reasons == (reason,)


def test_model_cannot_get_two_moved_objects_to_overlap(graph, scenario, pipeline, pack, ledger):
    a, b = graph.by_id(node_id('table_1')), graph.by_id(node_id('table_2'))
    result = validate(graph, scenario, pipeline, pack, ledger, [
        move(a.id, dx=-a.transform.position.x, dy=-a.transform.position.y),
        move(b.id, dx=-b.transform.position.x, dy=-b.transform.position.y),
    ])
    assert not result.accepted
    assert 'collided' in result.reasons


def test_model_edits_need_verified_rules(graph, scenario, pipeline, pack):
    result = validate(
        graph,
        scenario,
        pipeline,
        pack,
        VerificationLedger(),
        [move(node_id('case_east'), dx=0.1)],
    )
    assert not result.accepted
    assert 'no_verified_rules' in result.reasons


def test_unavailable_openrouter_is_reported_without_inventing_a_redesign(graph, scenario, pipeline, pack, ledger):
    class Unavailable:
        model = 'requested-astra'
        def structured(self, *args):
            return Rejected('openrouter_not_configured')
    result = propose_redesign(graph, [], [], [], pipeline, rules=pack, ledger=ledger, model=Unavailable())
    assert not result.accepted
    assert result.reasons == ('openrouter_not_configured',)
    assert result.graph is None


def test_astra_receives_focused_movable_objects_and_actionable_failures(
    graph, pipeline, pack, ledger
):
    class Capture:
        model = "astra-test"

        def structured(self, instruction, state, schema, name):
            self.instruction = instruction
            self.state = state
            return answer([])

    client = Capture()
    failure = {
        "origin": "Entrance",
        "destination": "Counter",
        "measured_width_inches": 31.0,
        "required_width_inches": 36.0,
        "movable_blocker_ids": [str(node_id("case_east"))],
    }
    result = propose_redesign(
        graph,
        [],
        [],
        [{"actionable_failures": [failure], "evidence_gaps": []}],
        pipeline,
        rules=pack,
        ledger=ledger,
        model=client,
    )
    assert not result.accepted
    assert client.state["actionable_failures"] == [failure]
    assert {item["id"] for item in client.state["movable_objects"]} == {
        str(node.id) for node in graph.nodes if node.kind == "object" and node.movable
    }


def test_validated_aisle_improvement_preserves_all_objects_and_dimensions(graph, scenario, pipeline, pack, ledger):
    from standardphysics_contracts import to_meters
    result = validate(graph, scenario, pipeline, pack, ledger, [move(node_id('case_east'), dx=to_meters(5))])
    assert result.accepted, result.reasons
    assert result.graph is not None
    assert {node.id: node.dimensions for node in result.graph.nodes} == {node.id: node.dimensions for node in graph.nodes}
    assert result.graph.by_id(node_id('counter')) == graph.by_id(node_id('counter'))


def test_astra_receives_rule_problems_and_route_trials(graph, pipeline, pack, ledger):
    class Capture:
        model = "astra-test"

        def structured(self, instruction, state, schema, name):
            self.instruction = instruction
            self.state = state
            return answer([])

    client = Capture()
    problem = {"check_id": "protruding_objects", "movable_node_ids": [str(node_id("case_east"))]}
    trials = {"decided_by": "typesafe", "kept_moves": []}
    propose_redesign(
        graph, [], [], [{"actionable_failures": [], "actionable_rule_problems": [problem], "route_trials": trials}],
        pipeline, rules=pack, ledger=ledger, model=client,
    )
    assert client.state["actionable_rule_problems"] == [problem]
    assert client.state["route_trials"] == trials
    assert "route_trials" in client.instruction
