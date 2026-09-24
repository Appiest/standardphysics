"""A rearrangement may only set a piece down on floor the scan actually saw."""

from __future__ import annotations

import uuid

import pytest
from standardphysics_agents.fix import NO_FLOOR_MAP, apply_moves, floor_map_missing, violations
from standardphysics_agents.fix.constraints import UNSEEN_FLOOR_TOLERANCE
from standardphysics_contracts import LidarMesh, Mat4, NodeMove, SceneGraph, SceneNode, Vec3
from standardphysics_fixtures import node_id
from standardphysics_pipeline.floor_coverage import ObservedFloor, measure_floor_coverage

SEEN_UP_TO_X = 0.5
"""The mesh saw the floor from the west wall to here; east of it the phone never looked."""

MESH_SQUARE = 0.1
CASE_WIDTH = 0.6


def _node(name, kind, label, centre, dims, movable):
    return SceneNode(
        id=node_id(f"unseen_{name}"),
        kind=kind,
        label=label,
        raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]),
        transform=Mat4.translation(*centre),
        movable=movable,
    )


def _floor_mesh(hole: tuple[float, float, float, float] | None = None) -> LidarMesh:
    """Floor squares at z = 0 over x in [-3, SEEN_UP_TO_X], minus an optional hole."""
    vertices, triangles = [], []
    steps_x, steps_y = round((SEEN_UP_TO_X + 3.0) / MESH_SQUARE), round(4.0 / MESH_SQUARE)
    for i in range(steps_x):
        for j in range(steps_y):
            x, y = -3.0 + i * MESH_SQUARE, -2.0 + j * MESH_SQUARE
            if hole and hole[0] <= x < hole[2] and hole[1] <= y < hole[3]:
                continue
            base = len(vertices) // 3
            vertices += [x, y, 0.0, x + MESH_SQUARE, y, 0.0, x + MESH_SQUARE, y + MESH_SQUARE, 0.0, x, y + MESH_SQUARE, 0.0]
            triangles += [base, base + 1, base + 2, base, base + 2, base + 3]
    part = {"id": str(uuid.uuid4()), "transform": Mat4.identity().m, "vertices": vertices, "triangles": triangles}
    return LidarMesh.model_validate({"parts": [part]})


def _room(mesh: LidarMesh | None = None) -> SceneGraph:
    """A 6 x 4 m floor, a display case on seen floor and a bench standing east of what the mesh saw."""
    nodes = [
        _node("floor", "floor", "Floor", (0.0, 0.0, 0.0), (6.0, 4.0, 0.01), False),
        _node("case", "object", "Display case", (-0.2, 1.2, 0.45), (CASE_WIDTH, CASE_WIDTH, 0.9), True),
        _node("bench", "object", "Bench", (1.0, 0.5, 0.25), (0.8, 0.7, 0.5), True),
    ]
    graph = SceneGraph(scan_id=node_id("unseen_room"), nodes=nodes, capture_to_room=Mat4.identity())
    coverage = measure_floor_coverage(graph, mesh or _floor_mesh(), "0" * 64)
    return graph.model_copy(update={"floor_coverage": coverage})


CASE, BENCH = node_id("unseen_case"), node_id("unseen_bench")


def _slide(graph, piece, dx, dy=0.0):
    return NodeMove(node_id=piece, delta_translation=Vec3(x=dx, y=dy, z=0.0))


def _kinds(graph, *moves):
    return [v.kind for v in violations(graph, apply_moves(graph, list(moves)))]


def test_a_piece_slid_onto_floor_the_scan_never_saw_is_refused():
    graph = _room()
    assert _kinds(graph, _slide(graph, CASE, 1.2)) == ["onto_unseen_floor"]


def test_the_same_slide_across_seen_floor_is_allowed():
    graph = _room()
    assert _kinds(graph, _slide(graph, CASE, -1.2)) == []


def test_floor_that_was_under_another_piece_counts_as_seen():
    graph = _room()
    bench_out = _slide(graph, BENCH, -1.4)
    case_in = _slide(graph, CASE, 1.2, -0.7)
    assert _kinds(graph, bench_out, case_in) == []


def test_a_hole_of_a_few_cells_in_the_mesh_does_not_block_a_move():
    graph = _room(_floor_mesh(hole=(-1.5, 1.1, -1.4, 1.2)))
    moved = apply_moves(graph, [_slide(graph, CASE, -1.2)])
    assert 0.0 < ObservedFloor.of(graph).unseen_share(moved.by_id(CASE)) < UNSEEN_FLOOR_TOLERANCE
    assert violations(graph, moved) == []


def test_a_piece_barely_over_the_edge_of_what_was_seen_is_allowed():
    graph = _room()
    east_edge = -0.2 + CASE_WIDTH / 2
    overhang = CASE_WIDTH * UNSEEN_FLOOR_TOLERANCE / 2
    assert _kinds(graph, _slide(graph, CASE, SEEN_UP_TO_X - east_edge + overhang)) == []


def test_a_piece_already_partly_on_unseen_floor_may_move_back_toward_seen_floor():
    graph = _room()
    stranded = graph.model_copy(update={"nodes": [
        node.model_copy(update={"transform": Mat4.translation(0.6, 1.2, 0.45)}) if node.id == CASE else node
        for node in graph.nodes
    ]})
    assert _kinds(stranded, _slide(stranded, CASE, -0.05)) == []
    assert _kinds(stranded, _slide(stranded, CASE, 0.3)) == ["onto_unseen_floor"]


def test_placing_the_room_elsewhere_carries_its_coverage_with_it():
    turned = Mat4(m=[0.0, -1.0, 0.0, 10.0, 1.0, 0.0, 0.0, 5.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    graph = _room()

    def placed(node):
        p = node.transform.position
        return node.model_copy(update={"transform": Mat4(m=[
            0.0, -1.0, 0.0, 10.0 - p.y, 1.0, 0.0, 0.0, 5.0 + p.x, 0.0, 0.0, 1.0, p.z, 0.0, 0.0, 0.0, 1.0,
        ])})

    moved = graph.model_copy(update={"nodes": [placed(node) for node in graph.nodes]})
    assert moved.by_id(node_id("unseen_floor")).transform.m == turned.m
    assert _kinds(moved, _slide(moved, CASE, 0.0, 1.2)) == ["onto_unseen_floor"]
    assert _kinds(moved, _slide(moved, CASE, 0.0, -1.2)) == []


def test_without_a_coverage_map_the_check_does_not_run_and_says_so():
    graph = _room().model_copy(update={"floor_coverage": []})
    assert floor_map_missing(graph)
    assert "no floor coverage map" in NO_FLOOR_MAP
    assert _kinds(graph, _slide(graph, CASE, 1.2)) == []


def test_a_candidate_cannot_vouch_for_its_own_floor():
    graph = _room()
    candidate = apply_moves(graph, [_slide(graph, CASE, 1.2)])
    everything_seen = candidate.model_copy(update={"floor_coverage": []})
    assert [v.kind for v in violations(graph, everything_seen)] == ["onto_unseen_floor"]


@pytest.mark.parametrize("dx", [1.2, -1.2])
def test_the_unseen_share_is_measured_on_the_footprint(dx):
    graph = _room()
    moved = apply_moves(graph, [_slide(graph, CASE, dx)])
    share = ObservedFloor.of(graph).unseen_share(moved.by_id(CASE))
    assert share == (pytest.approx(1.0) if dx > 0 else pytest.approx(0.0))
