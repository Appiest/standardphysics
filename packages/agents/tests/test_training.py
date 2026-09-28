import json

from standardphysics_agents.fix import violations
from standardphysics_agents.training import (
    SYSTEM_PROMPT,
    TrainingChecker,
    edits_between,
    edits_json,
    parse_edits,
    prompt_messages,
    score_completion,
    scramble,
    shaped_reward,
    snapped_reward,
    trusted_geometry,
)
from standardphysics_agents.training.prompt import room_view
from standardphysics_agents.training.reward import summarize
from standardphysics_agents.training.scramble import floor_furniture
from standardphysics_fixtures import node_id


def checker_for(scenario, pipeline, pack, ledger):
    return TrainingChecker(scenario, rules=pack, ledger=ledger, measure=pipeline)


def unsure(graph):
    return graph.model_copy(update={"nodes": [n.model_copy(update={"quality": "needs_another_look"}) for n in graph.nodes]})


def test_trusted_geometry_only_touches_unsure_nodes(graph):
    trusted = trusted_geometry(unsure(graph))
    assert {node.quality for node in trusted.nodes} == {"measured"}
    assert [n.transform for n in trusted.nodes] == [n.transform for n in graph.nodes]


def test_training_checker_answers_where_production_asks(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    assert checker.assess(unsure(graph)).verdicts == checker.assess(graph).verdicts


def test_parse_edits_reads_fenced_or_thinking_answers():
    answer = '{"moves":[{"node_id":"%s","dx":0.1,"dy":0,"rotation_degrees":0}]}' % node_id("table_1")
    for text in (answer, f"```json\n{answer}\n```", f"<think>hmm</think>{answer}", f"Here you go: {answer}"):
        assert parse_edits(text).moves[0].dx == 0.1
    assert parse_edits("move the table") is None
    assert parse_edits('{"moves":[{"node_id":"x","dx":1,"dy":0,"rotation_degrees":0}]}') is None


def test_unparseable_and_fixed_and_noop_answers_score_zero(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    counter = '{"moves":[{"node_id":"%s","dx":1,"dy":0,"rotation_degrees":0}]}' % node_id("counter")
    noop = '{"moves":[{"node_id":"%s","dx":0,"dy":0,"rotation_degrees":0}]}' % node_id("table_1")
    for completion in ("nonsense", counter, noop):
        verdict = score_completion(completion, graph, checker)
        assert verdict.reward == 0.0 and not verdict.gate_accepts


def test_a_request_into_the_counter_is_snapped_to_legal_and_never_scored_as_a_collision(
    graph, scenario, pipeline, pack, ledger
):
    checker = checker_for(scenario, pipeline, pack, ledger)
    table = graph.by_id(node_id("table_1")).transform.position
    counter = graph.by_id(node_id("counter")).transform.position
    into = '{"moves":[{"node_id":"%s","dx":%f,"dy":%f,"rotation_degrees":0}]}' % (
        node_id("table_1"), counter.x - table.x, counter.y - table.y)
    verdict = snapped_reward.score_completion(into, graph, checker)
    assert verdict.parsed and "collided" not in verdict.reason
    assert verdict.hard_constraints_pass or verdict.reason == "no_legal_spot_for_any_move"


def test_moving_a_fixed_piece_scores_nothing(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    answer = '{"moves":[{"node_id":"%s","dx":0.3,"dy":0,"rotation_degrees":0}]}' % node_id("counter")
    verdict = score_completion(answer, graph, checker)
    assert verdict.reward == 0.0 and verdict.reason == "moved_something_fixed"


def test_moving_more_always_costs_more():
    assert snapped_reward.shaped_reward(0.5, False, 6.0) > snapped_reward.shaped_reward(0.5, False, 9.0)


def test_leaning_on_the_solver_costs_something():
    assert snapped_reward.shaped_reward(0.5, False, 1.0, snapped=0.0) > snapped_reward.shaped_reward(0.5, False, 1.0, snapped=0.5)


def _slide_counter(dx_inches, dy_inches):
    return '{"moves":[],"fixture_moves":[{"node_id":"%s","dx_inches":%s,"dy_inches":%s}]}' % (
        node_id("counter"), dx_inches, dy_inches)


def test_a_relocated_fixture_cannot_land_on_furniture(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    verdict = score_completion(_slide_counter(20, -20), graph, checker)
    assert not verdict.hard_constraints_pass and "collided: Ordering counter into Chair" in verdict.reason


def test_a_relocated_fixture_may_slide_onto_open_floor(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    assert "collided" not in score_completion(_slide_counter(0, -6), graph, checker).reason


def test_shaped_reward_rewards_recovery_and_charges_disruption():
    assert shaped_reward(1.0, True, 0.0) == 1.0
    assert shaped_reward(0.5, False, 0.0) > shaped_reward(0.1, False, 0.0)
    assert shaped_reward(0.5, False, 0.0) > shaped_reward(0.5, False, 3.0)
    assert shaped_reward(0.0, False, 100.0, 0.0) == 0.05
    assert shaped_reward(0.0, True, 100.0, 0.0) > shaped_reward(1.0, False, 0.0, 1.0)
    # A perfect complete fix (all clear, full usability, wishes and wall placement, no penalties) pays 1.0.
    assert shaped_reward(1.0, True, 0.0, 1.0, 0.0, 1.0, wall=1.0) == 1.0
    # Turning a moved piece off its wall costs reward even when the fix clears every finding.
    assert shaped_reward(1.0, True, 0.0, wall=0.0) < shaped_reward(1.0, True, 0.0, wall=1.0)
    # Even the worst wall placement on a complete fix still outranks the best possible partial fix.
    assert shaped_reward(0.0, True, 100.0, 0.0, wall=0.0) > shaped_reward(1.0, False, 0.0, 1.0, wall=1.0)


def test_scrambled_variants_are_buildable_and_have_something_to_fix(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    variants = scramble(graph, checker, 3, seed=1)
    assert variants
    for variant in variants:
        assert not violations(graph, variant.graph)
        assert variant.fixable
        assert checker.fixable_problems(checker.assess(variant.graph))
    assert [v.graph for v in scramble(graph, checker, 3, seed=1)] == [v.graph for v in variants]


def test_edits_between_round_trip_to_the_same_layout(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    variant = scramble(graph, checker, 1, seed=2)[0].graph
    back = parse_edits(edits_json(edits_between(variant, graph)))
    moved = {m.node_id for m in back.moves}
    assert moved <= {node.id for node in floor_furniture(graph)}


def test_prompt_is_compact_json_with_only_movable_ids(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    system, user = prompt_messages(graph, checker)
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    view = json.loads(user["content"])
    ids = {item["id"] for item in view["movable_objects"]}
    assert ids == {str(node.id) for node in graph.nodes if node.movable and node.kind != "floor"}
    assert str(node_id("counter")) not in ids
    assert all(problem["check"] for problem in view["problems"])
    assert "ADA compliant" not in user["content"] + SYSTEM_PROMPT


def test_summary_reports_rates(graph, scenario, pipeline, pack, ledger):
    checker = checker_for(scenario, pipeline, pack, ledger)
    report = summarize([score_completion("nonsense", graph, checker)])
    assert report["parse_rate"] == 0.0 and report["mean_reward"] == 0.0 and report["samples"] == 1


def test_the_model_is_told_the_outline_of_every_piece_and_what_a_door_keeps_clear(graph, scenario):
    view = room_view(graph, scenario, [])
    pieces = [*view["doors"], *view["fixed_objects"], *view["movable_objects"]]
    assert all(len(piece["corners"]) == 4 for piece in pieces)
    assert all(len(door["keep_clear"]) == 4 for door in view["doors"])
