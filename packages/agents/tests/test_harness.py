from __future__ import annotations

import math
import random
import uuid

import pytest
from standardphysics_agents.checks.questions import scan_cannot_see
from standardphysics_agents.fix import apply_moves, violations
from standardphysics_agents.snap import facing_error_degrees, snap
from standardphysics_agents.training import TrainingChecker, score_completion
from standardphysics_agents.training.usefulness import usefulness
from standardphysics_contracts import Mat4, NodeMove, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures import build_graph, build_scenario
from standardphysics_fixtures.shop import node_id

NAMESPACE = uuid.UUID("0e2f3a8c-7d61-4b8e-9a35-1c2d3e4f5a6b")


def _yaw_node(name, kind, label, category, centre, size, heading=0.0, movable=False) -> SceneNode:
    c, s = math.cos(math.radians(heading)), math.sin(math.radians(heading))
    x, y, z = centre
    return SceneNode(id=uuid.uuid5(NAMESPACE, name), kind=kind, label=label, raw_category=category,
                     dimensions=Vec3(x=size[0], y=size[1], z=size[2]),
                     transform=Mat4(m=[c, -s, 0, x, s, c, 0, y, 0, 0, 1, z, 0, 0, 0, 1]), movable=movable)


def _move(node_id_, dx=0.0, dy=0.0, turn=0.0) -> NodeMove:
    return NodeMove(node_id=node_id_, delta_translation=Vec3(x=dx, y=dy, z=0.0), delta_rotation_z_degrees=turn)


def _yaw(node: SceneNode) -> float:
    return math.degrees(math.atan2(node.transform.m[4], node.transform.m[0]))


def _by_id(graph: SceneGraph, wanted) -> SceneNode:
    return next(node for node in graph.nodes if node.id == wanted)


def _shop_with_restroom(trash_at: tuple[float, float]) -> SceneGraph:
    """The fixture shop with its south-west table taken out and a 1.8 by 2.2 m restroom in that corner."""
    graph = build_graph()
    dropped = {node_id("table_3"), node_id("chair_5")}
    restroom = [
        _yaw_node("partition_east", "wall", "Wall", "wall", (-1.15, -2.9, 1.5), (0.1, 2.2, 3.0)),
        _yaw_node("partition_north", "wall", "Wall", "wall", (-2.1, -1.75, 1.5), (1.8, 0.1, 3.0)),
        _yaw_node("toilet", "object", "Toilet", "toilet", (-2.78, -3.55, 0.4), (0.4, 0.7, 0.8), heading=180.0),
        _yaw_node("trash", "object", "Trash can", "storage", (*trash_at, 0.3), (0.35, 0.35, 0.6), movable=True),
    ]
    return graph.model_copy(update={"nodes": [n for n in graph.nodes if n.id not in dropped] + restroom})


def _problems(graph: SceneGraph, check_id: str):
    result = TrainingChecker(build_scenario()).assess(graph)
    return [finding for finding in result.problems if finding.check_id == check_id]


def test_a_trash_can_in_the_middle_of_the_restroom_takes_its_turning_circle():
    assert _problems(_shop_with_restroom((-2.1, -2.8)), "restroom_turning_space")


def test_the_same_trash_can_in_the_corner_leaves_the_circle_clear():
    assert not _problems(_shop_with_restroom((-1.42, -3.72)), "restroom_turning_space")


def test_a_restroom_is_only_asked_about_while_no_toilet_is_seen():
    from standardphysics_agents.checks.context import CheckContext
    from standardphysics_agents.rules import load_ledger, load_pack
    from standardphysics_pipeline import PipelineMeasurements

    def asked(graph):
        ctx = CheckContext(graph, build_scenario(), PipelineMeasurements(), load_pack(), load_ledger())
        return {observation.rule_id for observation in scan_cannot_see(ctx)}

    assert "restroom_turning_space" in asked(build_graph())
    assert "restroom_turning_space" not in asked(_shop_with_restroom((-1.42, -3.72)))


@pytest.mark.parametrize("seed", range(6))
def test_a_snapped_layout_never_breaks_a_hard_constraint(seed):
    graph = build_graph()
    rng = random.Random(seed)
    pieces = [node for node in graph.nodes if node.movable and node.kind == "object"]
    moves = [_move(node.id, rng.uniform(-1.2, 1.2), rng.uniform(-1.2, 1.2), rng.choice((0, 45, 90, 180)))
             for node in rng.sample(pieces, 3)]
    snapped = snap(graph, moves)
    assert not violations(graph, snapped.graph)


def test_a_request_into_a_wall_lands_on_the_nearest_legal_spot():
    graph = build_graph()
    case = node_id("case_east")
    raw = apply_moves(graph, [_move(case, dx=1.5)])
    assert violations(graph, raw)
    snapped = snap(graph, [_move(case, dx=1.5)])
    assert not violations(graph, snapped.graph)
    assert _by_id(snapped.graph, case).transform.position.x > _by_id(graph, case).transform.position.x


def test_a_chair_slid_round_its_table_turns_to_face_it():
    graph = build_graph()
    chair = node_id("chair_5")
    snapped = snap(graph, [_move(chair, dx=-0.7, dy=0.7, turn=37.0)])
    placed = _by_id(snapped.graph, chair)
    assert facing_error_degrees(placed, snapped.graph) <= 30.0


def test_a_moved_table_takes_its_chairs_with_it():
    graph = build_graph()
    table, chairs = node_id("table_1"), (node_id("chair_1"), node_id("chair_2"))
    snapped = snap(graph, [_move(table, dx=0.4)])
    for chair in chairs:
        before, after = _by_id(graph, chair), _by_id(snapped.graph, chair)
        assert after.transform.position.x - before.transform.position.x == pytest.approx(0.4, abs=0.1)


def test_turning_every_chair_away_is_seen_as_a_loss_of_use():
    shop = build_graph()
    chairs = [node.id for node in shop.nodes if node.label == "Chair"]
    graph = snap(shop, [_move(chair, turn=1.0) for chair in chairs]).graph
    turned = apply_moves(graph, [_move(chair, turn=180.0) for chair in chairs])
    before, after = usefulness(graph), usefulness(turned)
    assert "seats_facing" in after.worse_than(before)


def test_a_seat_s_front_is_its_local_minus_y_side():
    table = _yaw_node("t", "object", "Table", "table", (0, 0, 0.375), (0.6, 0.6, 0.75))
    south = _yaw_node("s", "object", "Chair", "chair", (0, -0.6, 0.45), (0.45, 0.5, 0.85), heading=180.0)
    graph = SceneGraph(scan_id=uuid.uuid5(NAMESPACE, "g"), nodes=[table, south])
    assert facing_error_degrees(south, graph) == pytest.approx(0.0, abs=1e-6)
    assert _yaw(south) == pytest.approx(180.0)


def _answer(*moves) -> str:
    items = ",".join('{"node_id":"%s","dx":%s,"dy":%s,"rotation_degrees":%s}' % move for move in moves)
    return '{"moves":[%s]}' % items


FIX_THE_PINCH = (node_id("case_east"), 0.13, 0, 0)


def _checker(**kwargs) -> TrainingChecker:
    return TrainingChecker(build_scenario(), owner_layout=build_graph(), **kwargs)


def test_the_honest_fix_is_paid():
    verdict = score_completion(_answer(FIX_THE_PINCH), build_graph(), _checker())
    assert verdict.gate_accepts and verdict.reward > 0.5


@pytest.mark.parametrize("answer, reason", [
    ("not json", "unparseable"),
    ('{"moves":[]}', "no_supported_furniture_move"),
    (_answer(FIX_THE_PINCH, FIX_THE_PINCH), "duplicate_objects"),
    (_answer((uuid.uuid4(), 0.2, 0, 0)), "unknown_objects"),
    (_answer((node_id("case_east"), 0, 0, 0)), "no_op_moves"),
    (_answer((node_id("counter"), 0.2, 0, 0)), "moved_fixed_object"),
])
def test_the_obvious_tricks_score_nothing(answer, reason):
    verdict = score_completion(answer, build_graph(), _checker())
    assert verdict.reward == 0.0 and verdict.reason == reason


def test_asking_to_turn_a_chair_away_leaves_it_facing_its_table():
    graph = snap(build_graph(), [_move(n.id, turn=1.0) for n in build_graph().nodes if n.label == "Chair"]).graph
    chair = next(n for n in graph.nodes if n.label == "Chair")
    trick = _answer(FIX_THE_PINCH, (chair.id, 0, 0, 180))
    verdict = score_completion(trick, graph, TrainingChecker(build_scenario(), owner_layout=graph))
    assert verdict.usefulness["seats_facing"] == 1.0


def _shop_with_shelf() -> SceneGraph:
    shelf = _yaw_node("shelf", "object", "Shelving unit", "storage", (-2.75, -1.1, 0.9), (0.9, 0.4, 1.8),
                      heading=90.0, movable=True)
    graph = build_graph()
    return graph.model_copy(update={"nodes": [*graph.nodes, shelf]})


def test_fixing_the_pinch_while_parking_a_table_in_front_of_a_shelf_scores_nothing():
    graph = _shop_with_shelf()
    checker = TrainingChecker(build_scenario(), owner_layout=graph)
    assert score_completion(_answer(FIX_THE_PINCH), graph, checker).gate_accepts
    trick = _answer(FIX_THE_PINCH, (node_id("table_3"), -0.1, 1.25, 0))
    verdict = score_completion(trick, graph, checker)
    assert verdict.reward == 0.0 and verdict.reason == "less_useful:fronts_clear"


def test_a_layout_a_directive_refuses_scores_nothing():
    checker = _checker()
    checker.directive_rejection = lambda graph: (lambda base, candidate: "precedent_violation:kept_the_aisle")
    verdict = score_completion(_answer(FIX_THE_PINCH), build_graph(), checker)
    assert verdict.reward == 0.0 and verdict.reason.startswith("precedent_violation")


def test_moving_everything_a_little_is_not_free():
    graph = build_graph()
    others = [(n.id, 0.05, 0.05, 0) for n in graph.nodes if n.movable and n.label == "Table"]
    honest = score_completion(_answer(FIX_THE_PINCH), graph, _checker())
    busy = score_completion(_answer(FIX_THE_PINCH, *others), graph, _checker())
    assert busy.reward < honest.reward
