from types import SimpleNamespace

import pytest
import room_solver
from shop_generator import generate
from standardphysics_agents.fix import apply_moves
from standardphysics_agents.training.edits import node_moves
from standardphysics_contracts import Vec3, to_meters
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
