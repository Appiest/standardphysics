from types import SimpleNamespace

import pytest
import room_solver
import standardphysics_fixtures.shop as shop_fixture
from fitting_candidates import fitting_candidates
from shop_generator import generate
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.redesign import FurnitureMove
from standardphysics_agents.training import TrainingChecker
from standardphysics_agents.training.edits import TrainingEdits, apply_edits, node_moves
from standardphysics_agents.training.fittings import HeightChange
from standardphysics_contracts import Mat4, SceneNode, Vec3, to_meters
from standardphysics_fixtures import build_graph, build_scenario, node_id
from standardphysics_pipeline.footprints import footprint


class _Checker:
    def __init__(self, finding, pinned=frozenset()):
        self.finding, self.pinned = finding, pinned

    def assess(self, graph):
        return graph

    def fixable_problems(self, _):
        return [self.finding]


def _circle_over(node, inside_inches: float):
    """A 60 in turning-circle finding whose edge reaches `inside_inches` into the node."""
    corners = list(footprint(node))
    edge_mid = ((corners[0][0] + corners[1][0]) / 2, (corners[0][1] + corners[1][1]) / 2)
    centre_dir = (edge_mid[0] - node.transform.position.x, edge_mid[1] - node.transform.position.y)
    length = (centre_dir[0] ** 2 + centre_dir[1] ** 2) ** 0.5
    offset = to_meters(30 - inside_inches)
    centre = (edge_mid[0] + centre_dir[0] / length * offset, edge_mid[1] + centre_dir[1] / length * offset)
    return SimpleNamespace(check_id="turning_space", required_inches=60.0,
                           locus=SimpleNamespace(point=Vec3(x=centre[0], y=centre[1], z=0.0)))


def test_every_piece_inside_a_turning_circle_ends_just_clear_of_it():
    graph = generate(0)[0]
    chair = next(node for node in graph.nodes if node.movable and node.label == "Chair")
    finding = _circle_over(chair, inside_inches=6)
    edits = room_solver._circle_push(graph, _Checker(finding))
    assert edits is not None and chair.id in {move.node_id for move in edits.moves}
    moved = apply_moves(graph, node_moves(edits))
    centre = (finding.locus.point.x, finding.locus.point.y)
    for move in edits.moves:
        distance, _ = room_solver._nearest(centre, list(footprint(moved.by_id(move.node_id))))
        assert distance == pytest.approx(to_meters(30 + room_solver.PUSH_MARGIN_INCHES), abs=0.01)


def test_a_pinned_piece_inside_the_circle_means_no_push():
    graph = generate(0)[0]
    chair = next(node for node in graph.nodes if node.movable and node.label == "Chair")
    finding = _circle_over(chair, inside_inches=6)
    assert room_solver._circle_push(graph, _Checker(finding, pinned=frozenset({chair.id}))) is None


def _demo_room():
    """The boba shop: a 47 in ordering counter with the register on it, and a 31 in pinch on the way in."""
    graph = build_graph()
    register = SceneNode(id=node_id("register"), kind="object", label="Cash register", raw_category="electronics",
                         dimensions=Vec3(x=0.35, y=0.28, z=0.25),
                         transform=Mat4.translation(0.3, 3.42, to_meters(47.0) + 0.125), movable=False)
    return graph.model_copy(update={"nodes": [*graph.nodes, register]})


def test_a_counter_too_high_is_cleared_by_a_lowered_section_with_the_register_carried():
    checker = TrainingChecker(build_scenario(), scope="fittings")
    solution, _ = room_solver.solve(_demo_room(), checker)
    edits = TrainingEdits.model_validate_json(solution.completion)
    assert solution.clears
    assert [section.carry for section in edits.add_lowered_section] == [[node_id("register")]]
    assert not (edits.wall_shifts or edits.fixture_moves or edits.height_changes or edits.replacements)


def test_a_room_with_nothing_to_fix_gets_no_construction():
    checker = TrainingChecker(build_scenario(), scope="fittings")
    fixed = shop_fixture.build_graph()
    fixed = apply_edits(fixed, TrainingEdits(
        moves=[FurnitureMove(node_id=node_id("case_east"), dx=to_meters(shop_fixture.FIX_SHIFT_INCHES), dy=0.0,
                             rotation_degrees=0.0)],
        height_changes=[HeightChange(node_id=node_id("counter"), top_inches=36.0)]))
    assert not checker.fixable_problems(checker.assess(fixed))
    assert fitting_candidates(fixed, checker) == []
    solution, _ = room_solver.solve(fixed, checker)
    assert solution is None


def test_the_demo_counter_is_cleared_by_setting_the_card_reader_on_its_lowered_section():
    """Happy Lemon: a 47 in counter that already has a 36 in section, with the card reader on the high part."""
    graph, checker = shop_fixture.build_lawsuit_graph(), TrainingChecker(shop_fixture.build_lawsuit_scenario(),
                                                                          scope="fittings")
    solution, _ = room_solver.solve(graph, checker)
    edits = TrainingEdits.model_validate_json(solution.completion)
    reader = next(node for node in graph.nodes if node.label == "Card reader")
    assert solution.clears and solution.verdict["construction_cost"] == 0
    assert reader.id in {move.node_id for move in edits.moves}
    assert not (edits.wall_shifts or edits.fixture_moves or edits.height_changes or edits.add_lowered_section)


def _hung_over_a_lavatory():
    graph = shop_fixture.build_lawsuit_graph()
    lavatory = SceneNode(id=node_id("lavatory"), kind="object", label="Lavatory", raw_category="sink",
                         dimensions=Vec3(x=0.5, y=0.45, z=0.85), transform=Mat4.translation(2.0, 0.0, 0.425),
                         movable=False)
    towels = SceneNode(id=node_id("towels"), kind="object", label="Paper towel dispenser", raw_category="dispenser",
                       dimensions=Vec3(x=0.3, y=0.12, z=0.4), transform=Mat4.translation(2.0, 0.0, to_meters(50.0)),
                       movable=False)
    return graph.model_copy(update={"nodes": [*graph.nodes, lavatory, towels]})


def test_a_dispenser_over_a_lavatory_is_rehung_not_refused_as_resting_on_it():
    graph = _hung_over_a_lavatory()
    checker = TrainingChecker(shop_fixture.build_lawsuit_scenario(), scope="fittings")
    lowered = TrainingEdits(height_changes=[HeightChange(node_id=node_id("towels"), top_inches=47.5)])
    groups = [candidate for group in fitting_candidates(graph, checker) for candidate in group]
    slid = [edits for edits in groups if edits.fixture_moves and edits.height_changes]
    assert apply_edits(graph, lowered).by_id(node_id("towels")) is not None
    assert slid, "a control over a basin gets candidates that slide it along its wall before lowering it"


def test_an_exhausted_budget_returns_the_best_answer_already_scored():
    """With no time for any furniture or construction search, what comes back is still a checker verdict."""
    checker = TrainingChecker(build_scenario(), scope="fittings")
    graph = _demo_room()
    solution, _ = room_solver.solve(graph, checker, budget_seconds=0.0)
    edits = TrainingEdits.model_validate_json(solution.completion)
    assert not (edits.moves or edits.wall_shifts or edits.fixture_moves)
    assert solution.verdict == room_solver.score_completion(solution.completion, graph, checker).as_dict()

