"""The floor coverage grid: what counts as seen, and how it is stored."""

from __future__ import annotations

import uuid

import numpy as np
from standardphysics_contracts import LidarMesh, Mat4, SceneGraph, SceneNode, Vec3
from standardphysics_pipeline.floor_coverage import (
    decode_cells,
    encode_cells,
    measure_floor_coverage,
)
from standardphysics_pipeline.occupancy import CELL_SIZE

SHA = "a" * 64


def _node(label, kind, centre, dims, movable=False) -> SceneNode:
    return SceneNode(
        id=uuid.uuid5(uuid.NAMESPACE_URL, label), kind=kind, label=label, raw_category=kind,
        dimensions=Vec3(x=dims[0], y=dims[1], z=dims[2]), transform=Mat4.translation(*centre), movable=movable,
    )


def _square(x0, y0, size, z, upright=False) -> tuple[list[float], list[int]]:
    if upright:
        corners = [x0, y0, z, x0 + size, y0, z, x0 + size, y0, z + size, x0, y0, z + size]
    else:
        corners = [x0, y0, z, x0 + size, y0, z, x0 + size, y0 + size, z, x0, y0 + size, z]
    return corners, [0, 1, 2, 0, 2, 3]


def _mesh(*squares) -> LidarMesh:
    vertices, triangles = [], []
    for corners, faces in squares:
        base = len(vertices) // 3
        vertices += corners
        triangles += [base + index for index in faces]
    return LidarMesh.model_validate({"parts": [
        {"id": str(uuid.uuid4()), "transform": Mat4.identity().m, "vertices": vertices, "triangles": triangles},
    ]})


def _grid(graph: SceneGraph, mesh: LidarMesh):
    (coverage,) = measure_floor_coverage(graph, mesh, SHA)
    return coverage, decode_cells(coverage.observed, coverage.rows, coverage.columns)


def _seen_at(coverage, cells, x, y) -> bool:
    return bool(cells[int((y - coverage.origin_y) / CELL_SIZE), int((x - coverage.origin_x) / CELL_SIZE)])


def _room(*extra) -> SceneGraph:
    floor = _node("floor", "floor", (0.0, 0.0, 0.0), (4.0, 4.0, 0.01))
    return SceneGraph(scan_id=uuid.uuid4(), nodes=[floor, *extra], capture_to_room=Mat4.identity())


def test_floor_faces_count_and_faces_off_the_floor_do_not():
    mesh = _mesh(
        _square(-1.0, -1.0, 0.5, 0.01),
        _square(1.0, 1.0, 0.5, 0.75),
        _square(-1.0, 1.0, 0.5, 0.0, upright=True),
    )
    coverage, cells = _grid(_room(), mesh)
    assert _seen_at(coverage, cells, -0.75, -0.75)
    assert not _seen_at(coverage, cells, 1.25, 1.25)
    assert not _seen_at(coverage, cells, -0.75, 1.0)
    assert not _seen_at(coverage, cells, 0.5, -1.5)


def test_a_face_many_cells_across_is_seen_everywhere_it_covers():
    coverage, cells = _grid(_room(), _mesh(_square(-1.5, -1.5, 3.0, 0.0)))
    inside = cells[(coverage.rows - 120) // 2 : (coverage.rows + 120) // 2, (coverage.columns - 120) // 2 : (coverage.columns + 120) // 2]
    assert inside.all()


def test_floor_under_a_standing_piece_counts_as_seen():
    cabinet = _node("cabinet", "object", (1.0, -1.0, 0.45), (0.6, 0.4, 0.9))
    coverage, cells = _grid(_room(cabinet), _mesh(_square(-1.0, -1.0, 0.5, 0.0)))
    assert _seen_at(coverage, cells, 1.2, -0.9)
    assert not _seen_at(coverage, cells, 1.5, -0.9)


def test_the_grid_names_its_mesh_and_floor_and_matches_the_occupancy_cell():
    graph = _room()
    coverage, _ = _grid(graph, _mesh(_square(0.0, 0.0, 0.5, 0.0)))
    assert coverage.mesh_sha256 == SHA
    assert coverage.floor_id == graph.nodes[0].id
    assert coverage.anchor == graph.nodes[0].transform
    assert coverage.cell_size == CELL_SIZE
    assert (coverage.rows, coverage.columns) == (160, 160)


def test_cells_survive_the_round_trip_through_storage():
    cells = np.random.default_rng(7).random((37, 53)) > 0.5
    assert np.array_equal(decode_cells(encode_cells(cells), 37, 53), cells)


def test_a_graph_without_a_room_frame_gets_no_grid():
    graph = _room().model_copy(update={"capture_to_room": None})
    assert measure_floor_coverage(graph, _mesh(_square(0.0, 0.0, 0.5, 0.0)), SHA) == []
